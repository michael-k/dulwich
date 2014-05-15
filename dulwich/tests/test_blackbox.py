# test_blackbox.py -- blackbox tests
# Copyright (C) 2010 Jelmer Vernooij <jelmer@samba.org>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; version 2
# of the License or (at your option) a later version.
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

"""Blackbox tests for Dulwich commands."""

import tempfile
import shutil

from dulwich.repo import (
    Repo,
    )
from dulwich.tests import (
    BlackboxTestCase,
    )


class GitReceivePackTests(BlackboxTestCase):
    """Blackbox tests for dul-receive-pack."""

    def setUp(self):
        super(GitReceivePackTests, self).setUp()
        self.path = tempfile.mkdtemp()
        self.repo = Repo.init(self.path)

    def test_basic(self):
        process = self.run_command("dul-receive-pack", [self.path])
        (stdout, stderr) = process.communicate("0000")
        self.assertEqual('', stderr)
        self.assertEqual('0000', stdout[-4:])
        self.assertEqual(0, process.returncode)

    def test_missing_arg(self):
        process = self.run_command("dul-receive-pack", [])
        (stdout, stderr) = process.communicate()
        self.assertEqual('usage: dul-receive-pack <git-dir>\n', stderr)
        self.assertEqual('', stdout)
        self.assertEqual(1, process.returncode)


class GitUploadPackTests(BlackboxTestCase):
    """Blackbox tests for dul-upload-pack."""

    def setUp(self):
        super(GitUploadPackTests, self).setUp()
        self.path = tempfile.mkdtemp()
        self.repo = Repo.init(self.path)

    def test_missing_arg(self):
        process = self.run_command("dul-upload-pack", [])
        (stdout, stderr) = process.communicate()
        self.assertEqual('usage: dul-upload-pack <git-dir>\n', stderr)
        self.assertEqual('', stdout)
        self.assertEqual(1, process.returncode)


class DulwichTestCase(BlackboxTestCase):
    """Blackbox tests for `dulwich`."""

    def setUp(self):
        super(DulwichTestCase, self).setUp()
        repo_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo_dir)
        self.repo = Repo.init(repo_dir)
        self.subcommand = ''

    @property
    def usage_prefix(self):
        return 'usage: dulwich %s' % self.subcommand

    def run_dulwich(self, args, repository=None):
        """Run a Dulwich command.

        This wrapper around run_command makes sure that the command is
        executed on the specified repository.

        :param args: List of arguments to pass to dulwich,
            i.e. bin/dulwich
        :param repository: Repository to operate on
            (defaults to self.repo)
        """
        if repository is None:
            repository = self.repo
        process = self.run_command('dulwich', args, pwd=repository.path)
        stdout, stderr = process.communicate()
        return stdout, stderr, process.returncode

    def missing_arg(self, args=[], usage=''):
        """Checks the output for missing arguments.

        :param args: list of arguments to pass to dulwich
        :param usage: usage string to test for
            (after 'usage: dulwich subcommand')
        """
        stdout, stderr, returncode = self.run_dulwich([self.subcommand] + args)
        self.assertEqual('', stdout)
        self.assertEqual('%s %s\n' % (self.usage_prefix, usage), stderr)
        self.assertEqual(1, returncode)

    def exception(self, exception, args=[], output=''):
        """Checks if an exception is raised.

        :param exception: expected exception
        :param args: list of arguments to pass to dulwich
        :param output: expected output on sys.stdout
        """
        stdout, stderr, returncode = self.run_dulwich([self.subcommand] + args)
        self.assertEqual(output, stdout)
        self.assertIn('raise %s(' % exception.__name__, stderr)
        self.assertEqual(1, returncode)


class DulwichBasicTests(DulwichTestCase):
    """Basic blackbox tests for `dulwich`."""

    def setUp(self):
        super(DulwichBasicTests, self).setUp()

    def test_missing_arg(self):
        stdout, stderr, returncode = self.run_dulwich([])
        self.assertEqual('', stdout)
        self.assertEqual('%s<reset|rev-list|fetch-pack|log|show|symbolic-ref|'
                         'rm|clone|dump-index|tag|init|dump-pack|add|'
                         'commit-tree|diff|update-server-info|commit|fetch|'
                         'archive|diff-tree> [OPTIONS...]\n'
                         % self.usage_prefix,
                         stderr)
        self.assertEqual(1, returncode)


class DulwichAddTests(DulwichTestCase):
    """Basic blackbox tests for `dulwich add`."""

    def setUp(self):
        super(DulwichAddTests, self).setUp()
        self.subcommand = 'add'


class DulwichArchiveTests(DulwichTestCase):
    """Blackbox tests for `dulwich archive`."""

    def setUp(self):
        super(DulwichArchiveTests, self).setUp()
        self.subcommand = 'archive'


class DulwichCloneTests(DulwichTestCase):
    """Blackbox tests for `dulwich clone`."""

    def setUp(self):
        super(DulwichCloneTests, self).setUp()
        self.subcommand = 'clone'

    def test_missing_arg(self):
        self.missing_arg(usage='host:path [PATH]')


class DulwichCommitTests(DulwichTestCase):
    """Blackbox tests for `dulwich commit`."""

    def setUp(self):
        super(DulwichCommitTests, self).setUp()
        self.subcommand = 'commit'


class DulwichCommitTreeTests(DulwichTestCase):
    """Blackbox tests for `dulwich commit-tree`."""

    def setUp(self):
        super(DulwichCommitTreeTests, self).setUp()
        self.subcommand = 'commit-tree'

    def test_missing_arg(self):
        self.missing_arg(usage='tree')


class DulwichDiffTests(DulwichTestCase):
    """Blackbox tests for `dulwich diff`."""

    def setUp(self):
        super(DulwichDiffTests, self).setUp()
        self.subcommand = 'diff'

    def test_missing_arg(self):
        self.missing_arg(usage='COMMITID')


class DulwichDiffTreeTests(DulwichTestCase):
    """Blackbox tests for `dulwich diff-tree`."""

    def setUp(self):
        super(DulwichDiffTreeTests, self).setUp()
        self.subcommand = 'diff-tree'

    def test_missing_arg(self):
        self.missing_arg(usage='OLD-TREE NEW-TREE')
        self.missing_arg(args=['HEAD'], usage='OLD-TREE NEW-TREE')


class DulwichDumpPackTests(DulwichTestCase):
    """Blackbox tests for `dulwich dump-pack`."""

    def setUp(self):
        super(DulwichDumpPackTests, self).setUp()
        self.subcommand = 'dump-pack'

    def test_missing_arg(self):
        self.missing_arg(usage='FILENAME')


class DulwichDumpIndexTests(DulwichTestCase):
    """Blackbox tests for `dulwich dump-index`."""

    def setUp(self):
        super(DulwichDumpIndexTests, self).setUp()
        self.subcommand = 'dump-index'

    def test_missing_arg(self):
        self.missing_arg(usage='FILENAME')


class DulwichFetchPackTests(DulwichTestCase):
    """Blackbox tests for `dulwich fetch-pack`."""

    def setUp(self):
        super(DulwichFetchPackTests, self).setUp()
        self.subcommand = 'fetch-pack'


class DulwichFetchTests(DulwichTestCase):
    """Blackbox tests for `dulwich fetch`."""

    def setUp(self):
        super(DulwichFetchTests, self).setUp()
        self.subcommand = 'fetch'


class DulwichInitTests(DulwichTestCase):
    """Blackbox tests for `dulwich init`."""

    def setUp(self):
        super(DulwichInitTests, self).setUp()
        self.subcommand = 'init'


class DulwichLogTests(DulwichTestCase):
    """Blackbox tests for `dulwich log`."""

    def setUp(self):
        super(DulwichLogTests, self).setUp()
        self.subcommand = 'log'


class DulwichResetTests(DulwichTestCase):
    """Blackbox tests for `dulwich reset`."""

    def setUp(self):
        super(DulwichResetTests, self).setUp()
        self.subcommand = 'reset'

    def test_mixed(self):
        self.exception(exception=ValueError, args=['--mixed'])

    def test_soft(self):
        self.exception(exception=ValueError, args=['--soft'])

    def test_no_mode(self):
        self.exception(exception=ValueError)


class DulwichRevListTests(DulwichTestCase):
    """Blackbox tests for `dulwich rev-list`."""

    def setUp(self):
        super(DulwichRevListTests, self).setUp()
        self.subcommand = 'rev-list'

    def test_missing_arg(self):
        self.missing_arg(usage='COMMITID...')


class DulwichRmTests(DulwichTestCase):
    """Blackbox tests for `dulwich rm`."""

    def setUp(self):
        super(DulwichRmTests, self).setUp()
        self.subcommand = 'rm'


class DulwichShowTests(DulwichTestCase):
    """Blackbox tests for `dulwich show`."""

    def setUp(self):
        super(DulwichShowTests, self).setUp()
        self.subcommand = 'show'


class DulwichSymbolicRefTests(DulwichTestCase):
    """Blackbox tests for `dulwich symbolic-ref`."""

    def setUp(self):
        super(DulwichSymbolicRefTests, self).setUp()
        self.subcommand = 'symbolic-ref'

    def test_missing_arg(self):
        self.missing_arg(usage='REF_NAME [--force]')


class DulwichTagTests(DulwichTestCase):
    """Blackbox tests for `dulwich tag`."""

    def setUp(self):
        super(DulwichTagTests, self).setUp()
        self.subcommand = 'tag'

    def test_missing_arg(self):
        self.missing_arg(usage='NAME')


class DulwichUpdateServerInfoTests(DulwichTestCase):
    """Blackbox tests for `dulwich update-server-info`."""

    def setUp(self):
        super(DulwichUpdateServerInfoTests, self).setUp()
        self.subcommand = 'update-server-info'
