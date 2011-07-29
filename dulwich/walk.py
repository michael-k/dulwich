# walk.py -- General implementation of walking commits and their contents.
# Copyright (C) 2010 Google, Inc.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# or (at your option) any later version of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.

"""General implementation of walking commits and their contents."""

import heapq
import itertools
import os

from dulwich.diff_tree import (
    tree_changes,
    tree_changes_for_merge,
    )

ORDER_DATE = 'date'


class WalkEntry(object):
    """Object encapsulating a single result from a walk."""

    def __init__(self, store, commit):
        self.commit = commit
        self._store = store
        self._changes = None

    def changes(self):
        """Get the tree changes for this entry.

        :return: For commits with up to one parent, a list of TreeChange
            objects; if the commit has no parents, these will be relative to the
            empty tree. For merge commits, a list of lists of TreeChange
            objects; see dulwich.diff.tree_changes_for_merge.
        """
        if self._changes is None:
            commit = self.commit
            if not commit.parents:
                changes = list(tree_changes(self._store, None, commit.tree))
            elif len(commit.parents) == 1:
                ptree = self._store[commit.parents[0]].tree
                changes = list(tree_changes(self._store, ptree, commit.tree))
            else:
                ptrees = [self._store[p].tree for p in commit.parents]
                changes = list(tree_changes_for_merge(self._store, ptrees,
                                                      commit.tree))
            self._changes = changes
        return self._changes

    def __repr__(self):
        return '<WalkEntry commit=%s, changes=%r>' % (
          self.commit.id, self.changes())


class Walker(object):
    """Object for performing a walk of commits in a store.

    Walker objects are initialized with a store and other options and can then
    be treated as iterators of Commit objects.
    """

    def __init__(self, store, include, exclude=None, order=ORDER_DATE,
                 reverse=False, max_entries=None, paths=None):
        """Constructor.

        :param store: ObjectStore instance for looking up objects.
        :param include: Iterable of SHAs of commits to include along with their
            ancestors.
        :param exclude: Iterable of SHAs of commits to exclude along with their
            ancestors, overriding includes.
        :param order: ORDER_* constant specifying the order of results. Anything
            other than ORDER_DATE may result in O(n) memory usage.
        :param reverse: If True, reverse the order of output, requiring O(n)
            memory.
        :param max_entries: The maximum number of entries to yield, or None for
            no limit.
        :param paths: Iterable of file or subtree paths to show entries for.
        """
        self._store = store

        if order not in (ORDER_DATE,):
            raise ValueError('Unknown walk order %s' % order)
        self._order = order
        self._reverse = reverse
        self._max_entries = max_entries
        self._num_entries = 0

        exclude = exclude or []
        self._excluded = set(exclude)
        self._pq = []
        self._pq_set = set()
        self._done = set()
        self._paths = paths and list(paths) or None

        for commit_id in itertools.chain(include, exclude):
            self._push(store[commit_id])

    def _push(self, commit):
        sha = commit.id
        if sha not in self._pq_set and sha not in self._done:
            heapq.heappush(self._pq, (-commit.commit_time, commit))
            self._pq_set.add(sha)

    def _pop(self):
        while self._pq:
            _, commit = heapq.heappop(self._pq)
            sha = commit.id
            self._pq_set.remove(sha)
            if sha in self._done:
                continue

            is_excluded = sha in self._excluded
            if is_excluded:
                self._excluded.update(commit.parents)

            self._done.add(commit.id)
            for parent_id in commit.parents:
                self._push(self._store[parent_id])

            if not is_excluded:
                return commit
        return None

    def _path_matches(self, changed_path):
        if changed_path is None:
            return False
        for followed_path in self._paths:
            if changed_path == followed_path:
                return True
            if (changed_path.startswith(followed_path) and
                changed_path[len(followed_path)] == '/'):
                return True
        return False

    def _change_matches(self, change):
        return (self._path_matches(change.old.path) or
                self._path_matches(change.new.path))

    def _make_entry(self, commit):
        """Make a WalkEntry from a commit.

        :param commit: The commit for the WalkEntry.
        :return: A WalkEntry object, or None if no entry should be returned for
            this commit (e.g. if it doesn't match any requested  paths).
        """
        entry = WalkEntry(self._store, commit)
        if self._paths is None:
            return entry

        if len(commit.parents) > 1:
            for path_changes in entry.changes():
                # For merge commits, only include changes with conflicts for
                # this path. Since a rename conflict may include different
                # old.paths, we have to check all of them.
                for change in path_changes:
                    if self._change_matches(change):
                        return entry
        else:
            for change in entry.changes():
                if self._change_matches(change):
                    return entry
        return None

    def _next(self):
        max_entries = self._max_entries
        while True:
            if max_entries is not None and self._num_entries >= max_entries:
                return None
            commit = self._pop()
            if commit is None:
                return None
            entry = self._make_entry(commit)
            if entry:
                self._num_entries += 1
                return entry

    def __iter__(self):
        results = iter(self._next, None)
        if self._reverse:
            results = reversed(list(results))
        return iter(results)