# test_server.py -- Tests for the git server
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

"""Tests for the smart protocol server."""

from io import BytesIO
import os
import tempfile

from dulwich.errors import (
    GitProtocolError,
    NotGitRepository,
    UnexpectedCommandError,
    HangupException,
    )
from dulwich.objects import (
    Commit,
    Tag,
    )
from dulwich.object_store import (
    MemoryObjectStore,
    )
from dulwich.repo import (
    MemoryRepo,
    Repo,
    )
from dulwich.server import (
    Backend,
    DictBackend,
    FileSystemBackend,
    Handler,
    MultiAckGraphWalkerImpl,
    MultiAckDetailedGraphWalkerImpl,
    _split_proto_line,
    serve_command,
    _find_shallow,
    ProtocolGraphWalker,
    ReceivePackHandler,
    SingleAckGraphWalkerImpl,
    UploadPackHandler,
    update_server_info,
    )
from dulwich.tests import TestCase
from dulwich.tests.utils import (
    make_commit,
    make_object,
    )
from dulwich.protocol import (
    ZERO_SHA,
    )

ONE = b'1' * 40
TWO = b'2' * 40
THREE = b'3' * 40
FOUR = b'4' * 40
FIVE = b'5' * 40
SIX = b'6' * 40


class TestProto(object):

    def __init__(self):
        self._output = []
        self._received = {0: [], 1: [], 2: [], 3: []}

    def set_output(self, output_lines):
        self._output = output_lines

    def read_pkt_line(self):
        if self._output:
            data = self._output.pop(0)
            if data is not None:
                return data.rstrip() + b'\n'
            else:
                # flush-pkt ('0000').
                return None
        else:
            raise HangupException()

    def write_sideband(self, band, data):
        self._received[band].append(data)

    def write_pkt_line(self, data):
        self._received[0].append(data)

    def get_received_line(self, band=0):
        lines = self._received[band]
        return lines.pop(0)


class TestGenericHandler(Handler):

    def __init__(self):
        Handler.__init__(self, Backend(), None)

    @classmethod
    def capabilities(cls):
        return (b'cap1', b'cap2', b'cap3')

    @classmethod
    def required_capabilities(cls):
        return (b'cap2',)


class HandlerTestCase(TestCase):

    def setUp(self):
        super(HandlerTestCase, self).setUp()
        self._handler = TestGenericHandler()

    def assertSucceeds(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except GitProtocolError as e:
            self.fail(e)

    def test_capability_line(self):
        self.assertEqual(b'cap1 cap2 cap3', self._handler.capability_line())

    def test_set_client_capabilities(self):
        set_caps = self._handler.set_client_capabilities
        self.assertSucceeds(set_caps, [b'cap2'])
        self.assertSucceeds(set_caps, [b'cap1', b'cap2'])

        # different order
        self.assertSucceeds(set_caps, [b'cap3', b'cap1', b'cap2'])

        # error cases
        self.assertRaises(GitProtocolError, set_caps, [b'capxxx', b'cap2'])
        self.assertRaises(GitProtocolError, set_caps, [b'cap1', b'cap3'])

        # ignore innocuous but unknown capabilities
        self.assertRaises(GitProtocolError, set_caps, [b'cap2', b'ignoreme'])
        self.assertFalse(b'ignoreme' in self._handler.capabilities())
        self._handler.innocuous_capabilities = lambda: (b'ignoreme',)
        self.assertSucceeds(set_caps, [b'cap2', b'ignoreme'])

    def test_has_capability(self):
        self.assertRaises(GitProtocolError, self._handler.has_capability, 'cap')
        caps = self._handler.capabilities()
        self._handler.set_client_capabilities(caps)
        for cap in caps:
            self.assertTrue(self._handler.has_capability(cap))
        self.assertFalse(self._handler.has_capability('capxxx'))


class UploadPackHandlerTestCase(TestCase):

    def setUp(self):
        super(UploadPackHandlerTestCase, self).setUp()
        self._repo = MemoryRepo.init_bare([], {})
        backend = DictBackend({'/': self._repo})
        self._handler = UploadPackHandler(
          backend, ['/', 'host=lolcathost'], TestProto())

    def test_progress(self):
        caps = self._handler.required_capabilities()
        self._handler.set_client_capabilities(caps)
        self._handler.progress(b'first message')
        self._handler.progress(b'second message')
        self.assertEqual(b'first message',
                         self._handler.proto.get_received_line(2))
        self.assertEqual(b'second message',
                         self._handler.proto.get_received_line(2))
        self.assertRaises(IndexError, self._handler.proto.get_received_line, 2)

    def test_no_progress(self):
        caps = list(self._handler.required_capabilities()) + [b'no-progress']
        self._handler.set_client_capabilities(caps)
        self._handler.progress(b'first message')
        self._handler.progress(b'second message')
        self.assertRaises(IndexError, self._handler.proto.get_received_line, 2)

    def test_get_tagged(self):
        refs = {
            b'refs/tags/tag1': ONE,
            b'refs/tags/tag2': TWO,
            b'refs/heads/master': FOUR,  # not a tag, no peeled value
        }
        # repo needs to peel this object
        self._repo.object_store.add_object(make_commit(id=FOUR))
        self._repo.refs._update(refs)
        peeled = {
            b'refs/tags/tag1': b'1234' * 10,
            b'refs/tags/tag2': b'5678' * 10,
        }
        self._repo.refs._update_peeled(peeled)

        caps = list(self._handler.required_capabilities()) + [b'include-tag']
        self._handler.set_client_capabilities(caps)
        self.assertEqual({b'1234' * 10: ONE, b'5678' * 10: TWO},
                         self._handler.get_tagged(refs, repo=self._repo))

        # non-include-tag case
        caps = self._handler.required_capabilities()
        self._handler.set_client_capabilities(caps)
        self.assertEqual({}, self._handler.get_tagged(refs, repo=self._repo))


class FindShallowTests(TestCase):

    def setUp(self):
        self._store = MemoryObjectStore()

    def make_commit(self, **attrs):
        commit = make_commit(**attrs)
        self._store.add_object(commit)
        return commit

    def make_linear_commits(self, n, message=b''):
        commits = []
        parents = []
        for _ in range(n):
            commits.append(self.make_commit(parents=parents, message=message))
            parents = [commits[-1].id]
        return commits

    def assertSameElements(self, expected, actual):
        self.assertEqual(set(expected), set(actual))

    def test_linear(self):
        c1, c2, c3 = self.make_linear_commits(3)

        self.assertEqual((set([c3.id]), set([])),
                         _find_shallow(self._store, [c3.id], 0))
        self.assertEqual((set([c2.id]), set([c3.id])),
                         _find_shallow(self._store, [c3.id], 1))
        self.assertEqual((set([c1.id]), set([c2.id, c3.id])),
                         _find_shallow(self._store, [c3.id], 2))
        self.assertEqual((set([]), set([c1.id, c2.id, c3.id])),
                         _find_shallow(self._store, [c3.id], 3))

    def test_multiple_independent(self):
        a = self.make_linear_commits(2, message=b'a')
        b = self.make_linear_commits(2, message=b'b')
        c = self.make_linear_commits(2, message=b'c')
        heads = [a[1].id, b[1].id, c[1].id]

        self.assertEqual((set([a[0].id, b[0].id, c[0].id]), set(heads)),
                         _find_shallow(self._store, heads, 1))

    def test_multiple_overlapping(self):
        # Create the following commit tree:
        # 1--2
        #  \
        #   3--4
        c1, c2 = self.make_linear_commits(2)
        c3 = self.make_commit(parents=[c1.id])
        c4 = self.make_commit(parents=[c3.id])

        # 1 is shallow along the path from 4, but not along the path from 2.
        self.assertEqual((set([c1.id]), set([c1.id, c2.id, c3.id, c4.id])),
                         _find_shallow(self._store, [c2.id, c4.id], 2))

    def test_merge(self):
        c1 = self.make_commit()
        c2 = self.make_commit()
        c3 = self.make_commit(parents=[c1.id, c2.id])

        self.assertEqual((set([c1.id, c2.id]), set([c3.id])),
                         _find_shallow(self._store, [c3.id], 1))

    def test_tag(self):
        c1, c2 = self.make_linear_commits(2)
        tag = make_object(Tag, name=b'tag', message=b'',
                          tagger=b'Tagger <test@example.com>',
                          tag_time=12345, tag_timezone=0,
                          object=(Commit, c2.id))
        self._store.add_object(tag)

        self.assertEqual((set([c1.id]), set([c2.id])),
                         _find_shallow(self._store, [tag.id], 1))


class TestUploadPackHandler(UploadPackHandler):
    @classmethod
    def required_capabilities(self):
        return ()

class ReceivePackHandlerTestCase(TestCase):

    def setUp(self):
        super(ReceivePackHandlerTestCase, self).setUp()
        self._repo = MemoryRepo.init_bare([], {})
        backend = DictBackend({'/': self._repo})
        self._handler = ReceivePackHandler(
          backend, ['/', 'host=lolcathost'], TestProto())

    def test_apply_pack_del_ref(self):
        refs = {
            b'refs/heads/master': TWO,
            b'refs/heads/fake-branch': ONE}
        self._repo.refs._update(refs)
        update_refs = [[ONE, ZERO_SHA, b'refs/heads/fake-branch'], ]
        status = self._handler._apply_pack(update_refs)
        self.assertEqual(status[0][0], b'unpack')
        self.assertEqual(status[0][1], b'ok')
        self.assertEqual(status[1][0], b'refs/heads/fake-branch')
        self.assertEqual(status[1][1], b'ok')


class ProtocolGraphWalkerEmptyTestCase(TestCase):
    def setUp(self):
        super(ProtocolGraphWalkerEmptyTestCase, self).setUp()
        self._repo = MemoryRepo.init_bare([], {})
        backend = DictBackend({'/': self._repo})
        self._walker = ProtocolGraphWalker(
            TestUploadPackHandler(backend, ['/', 'host=lolcats'], TestProto()),
            self._repo.object_store, self._repo.get_peeled)

    def test_empty_repository(self):
        # The server should wait for a flush packet.
        self._walker.proto.set_output([])
        self.assertRaises(HangupException, self._walker.determine_wants, {})
        self.assertEqual(None, self._walker.proto.get_received_line())

        self._walker.proto.set_output([None])
        self.assertEqual([], self._walker.determine_wants({}))
        self.assertEqual(None, self._walker.proto.get_received_line())



class ProtocolGraphWalkerTestCase(TestCase):

    def setUp(self):
        super(ProtocolGraphWalkerTestCase, self).setUp()
        # Create the following commit tree:
        #   3---5
        #  /
        # 1---2---4
        commits = [
            make_commit(id=ONE, parents=[], commit_time=111),
            make_commit(id=TWO, parents=[ONE], commit_time=222),
            make_commit(id=THREE, parents=[ONE], commit_time=333),
            make_commit(id=FOUR, parents=[TWO], commit_time=444),
            make_commit(id=FIVE, parents=[THREE], commit_time=555),
        ]
        self._repo = MemoryRepo.init_bare(commits, {})
        backend = DictBackend({'/': self._repo})
        self._walker = ProtocolGraphWalker(
            TestUploadPackHandler(backend, ['/', 'host=lolcats'], TestProto()),
            self._repo.object_store, self._repo.get_peeled)

    def test_is_satisfied_no_haves(self):
        self.assertFalse(self._walker._is_satisfied([], ONE, 0))
        self.assertFalse(self._walker._is_satisfied([], TWO, 0))
        self.assertFalse(self._walker._is_satisfied([], THREE, 0))

    def test_is_satisfied_have_root(self):
        self.assertTrue(self._walker._is_satisfied([ONE], ONE, 0))
        self.assertTrue(self._walker._is_satisfied([ONE], TWO, 0))
        self.assertTrue(self._walker._is_satisfied([ONE], THREE, 0))

    def test_is_satisfied_have_branch(self):
        self.assertTrue(self._walker._is_satisfied([TWO], TWO, 0))
        # wrong branch
        self.assertFalse(self._walker._is_satisfied([TWO], THREE, 0))

    def test_all_wants_satisfied(self):
        self._walker.set_wants([FOUR, FIVE])
        # trivial case: wants == haves
        self.assertTrue(self._walker.all_wants_satisfied([FOUR, FIVE]))
        # cases that require walking the commit tree
        self.assertTrue(self._walker.all_wants_satisfied([ONE]))
        self.assertFalse(self._walker.all_wants_satisfied([TWO]))
        self.assertFalse(self._walker.all_wants_satisfied([THREE]))
        self.assertTrue(self._walker.all_wants_satisfied([TWO, THREE]))

    def test_split_proto_line(self):
        allowed = (b'want', b'done', None)
        fmtline = lambda cmd, sha: cmd + b' ' + sha + b'\n'

        self.assertEqual((b'want', ONE),
                         _split_proto_line(fmtline(b'want', ONE), allowed))
        self.assertEqual((b'want', TWO),
                         _split_proto_line(fmtline(b'want', TWO), allowed))
        self.assertRaises(GitProtocolError, _split_proto_line,
                          b'want xxxx\n', allowed)
        self.assertRaises(UnexpectedCommandError, _split_proto_line,
                          fmtline(b'have', THREE), allowed)
        self.assertRaises(GitProtocolError, _split_proto_line,
                          fmtline(b'foo', FOUR), allowed)
        self.assertRaises(GitProtocolError, _split_proto_line, b'bar', allowed)
        self.assertEqual((b'done', None), _split_proto_line(b'done\n', allowed))
        self.assertEqual((None, None), _split_proto_line(b'', allowed))

    def test_determine_wants(self):
        self._walker.proto.set_output([None])
        self.assertEqual([], self._walker.determine_wants({}))
        self.assertEqual(None, self._walker.proto.get_received_line())

        self._walker.proto.set_output([
            b'want ' + ONE + b' multi_ack',
            b'want ' + TWO,
            None,
        ])
        heads = {
            b'refs/heads/ref1': ONE,
            b'refs/heads/ref2': TWO,
            b'refs/heads/ref3': THREE,
        }
        self._repo.refs._update(heads)
        self.assertEqual([ONE, TWO], self._walker.determine_wants(heads))

        self._walker.advertise_refs = True
        self.assertEqual([], self._walker.determine_wants(heads))
        self._walker.advertise_refs = False

        self._walker.proto.set_output([b'want ' + FOUR + b' multi_ack', None])
        self.assertRaises(GitProtocolError, self._walker.determine_wants, heads)

        self._walker.proto.set_output([None])
        self.assertEqual([], self._walker.determine_wants(heads))

        self._walker.proto.set_output([b'want ' + ONE + b' multi_ack', b'foo', None])
        self.assertRaises(GitProtocolError, self._walker.determine_wants, heads)

        self._walker.proto.set_output([b'want ' + FOUR + b' multi_ack', None])
        self.assertRaises(GitProtocolError, self._walker.determine_wants, heads)

    def test_determine_wants_advertisement(self):
        self._walker.proto.set_output([None])
        # advertise branch tips plus tag
        heads = {
            b'refs/heads/ref4': FOUR,
            b'refs/heads/ref5': FIVE,
            b'refs/heads/tag6': SIX,
        }
        self._repo.refs._update(heads)
        self._repo.refs._update_peeled(heads)
        self._repo.refs._update_peeled({b'refs/heads/tag6': FIVE})
        self._walker.determine_wants(heads)
        lines = []
        while True:
            line = self._walker.proto.get_received_line()
            if line is None:
                break
            # strip capabilities list if present
            if b'\x00' in line:
                line = line[:line.index(b'\x00')]
            lines.append(line.rstrip())

        self.assertEqual(
            [FOUR + b' refs/heads/ref4',
             FIVE + b' refs/heads/ref5',
             FIVE + b' refs/heads/tag6^{}',
             SIX + b' refs/heads/tag6', ],
            sorted(lines))

        # ensure peeled tag was advertised immediately following tag
        for i, line in enumerate(lines):
            if line.endswith(b' refs/heads/tag6'):
                self.assertEqual(FIVE + b' refs/heads/tag6^{}', lines[i+1])

    # TODO: test commit time cutoff

    def _handle_shallow_request(self, lines, heads):
        self._walker.proto.set_output(lines + [None])
        self._walker._handle_shallow_request(heads)

    def assertReceived(self, expected):
        self.assertEquals(
          expected, list(iter(self._walker.proto.get_received_line, None)))

    def test_handle_shallow_request_no_client_shallows(self):
        self._handle_shallow_request([b'deepen 1\n'], [FOUR, FIVE])
        self.assertEquals(set([TWO, THREE]), self._walker.shallow)
        self.assertReceived([
            b'shallow ' + TWO,
            b'shallow ' + THREE,
        ])

    def test_handle_shallow_request_no_new_shallows(self):
        lines = [
            b'shallow ' + TWO + b'\n',
            b'shallow ' + THREE + b'\n',
            b'deepen 1\n',
        ]
        self._handle_shallow_request(lines, [FOUR, FIVE])
        self.assertEquals(set([TWO, THREE]), self._walker.shallow)
        self.assertReceived([])

    def test_handle_shallow_request_unshallows(self):
        lines = [
            b'shallow ' + TWO + b'\n',
            b'deepen 2\n',
        ]
        self._handle_shallow_request(lines, [FOUR, FIVE])
        self.assertEquals(set([ONE]), self._walker.shallow)
        self.assertReceived([
            b'shallow ' + ONE,
            b'unshallow ' + TWO,
            # THREE is unshallow but was is not shallow in the client
        ])


class TestProtocolGraphWalker(object):

    def __init__(self):
        self.acks = []
        self.lines = []
        self.done = False
        self.http_req = None
        self.advertise_refs = False

    def read_proto_line(self, allowed):
        command, sha = self.lines.pop(0)
        if allowed is not None:
            assert command in allowed
        return command, sha

    def send_ack(self, sha, ack_type=''):
        self.acks.append((sha, ack_type))

    def send_nak(self):
        self.acks.append((None, 'nak'))

    def all_wants_satisfied(self, haves):
        return self.done

    def pop_ack(self):
        if not self.acks:
            return None
        return self.acks.pop(0)


class AckGraphWalkerImplTestCase(TestCase):
    """Base setup and asserts for AckGraphWalker tests."""

    def setUp(self):
        super(AckGraphWalkerImplTestCase, self).setUp()
        self._walker = TestProtocolGraphWalker()
        self._walker.lines = [
          ('have', TWO),
          ('have', ONE),
          ('have', THREE),
          ('done', None),
          ]
        self._impl = self.impl_cls(self._walker)

    def assertNoAck(self):
        self.assertEqual(None, self._walker.pop_ack())

    def assertAcks(self, acks):
        for sha, ack_type in acks:
            self.assertEqual((sha, ack_type), self._walker.pop_ack())
        self.assertNoAck()

    def assertAck(self, sha, ack_type=''):
        self.assertAcks([(sha, ack_type)])

    def assertNak(self):
        self.assertAck(None, 'nak')

    def assertNextEquals(self, sha):
        self.assertEqual(sha, next(self._impl))


class SingleAckGraphWalkerImplTestCase(AckGraphWalkerImplTestCase):

    impl_cls = SingleAckGraphWalkerImpl

    def test_single_ack(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAck(ONE)

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNoAck()

    def test_single_ack_flush(self):
        # same as ack test but ends with a flush-pkt instead of done
        self._walker.lines[-1] = (None, None)

        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAck(ONE)

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNoAck()

    def test_single_ack_nak(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()

    def test_single_ack_nak_flush(self):
        # same as nak test but ends with a flush-pkt instead of done
        self._walker.lines[-1] = (None, None)

        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()


class MultiAckGraphWalkerImplTestCase(AckGraphWalkerImplTestCase):

    impl_cls = MultiAckGraphWalkerImpl

    def test_multi_ack(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAck(ONE, 'continue')

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertAck(THREE, 'continue')

        self.assertNextEquals(None)
        self.assertAck(THREE)

    def test_multi_ack_partial(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._impl.ack(ONE)
        self.assertAck(ONE, 'continue')

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        # done, re-send ack of last common
        self.assertAck(ONE)

    def test_multi_ack_flush(self):
        self._walker.lines = [
          ('have', TWO),
          (None, None),
          ('have', ONE),
          ('have', THREE),
          ('done', None),
          ]
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNak()  # nak the flush-pkt

        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAck(ONE, 'continue')

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertAck(THREE, 'continue')

        self.assertNextEquals(None)
        self.assertAck(THREE)

    def test_multi_ack_nak(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()


class MultiAckDetailedGraphWalkerImplTestCase(AckGraphWalkerImplTestCase):

    impl_cls = MultiAckDetailedGraphWalkerImpl

    def test_multi_ack(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAcks([(ONE, 'common'), (ONE, 'ready')])

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertAck(THREE, 'ready')

        self.assertNextEquals(None)
        self.assertAck(THREE)

    def test_multi_ack_partial(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self._impl.ack(ONE)
        self.assertAck(ONE, 'common')

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        # done, re-send ack of last common
        self.assertAck(ONE)

    def test_multi_ack_flush(self):
        # same as ack test but contains a flush-pkt in the middle
        self._walker.lines = [
          ('have', TWO),
          (None, None),
          ('have', ONE),
          ('have', THREE),
          ('done', None),
          ]
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNak()  # nak the flush-pkt

        self._walker.done = True
        self._impl.ack(ONE)
        self.assertAcks([(ONE, 'common'), (ONE, 'ready')])

        self.assertNextEquals(THREE)
        self._impl.ack(THREE)
        self.assertAck(THREE, 'ready')

        self.assertNextEquals(None)
        self.assertAck(THREE)

    def test_multi_ack_nak(self):
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()

    def test_multi_ack_nak_flush(self):
        # same as nak test but contains a flush-pkt in the middle
        self._walker.lines = [
          ('have', TWO),
          (None, None),
          ('have', ONE),
          ('have', THREE),
          ('done', None),
          ]
        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNak()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()

    def test_multi_ack_stateless(self):
        # transmission ends with a flush-pkt
        self._walker.lines[-1] = (None, None)
        self._walker.http_req = True

        self.assertNextEquals(TWO)
        self.assertNoAck()

        self.assertNextEquals(ONE)
        self.assertNoAck()

        self.assertNextEquals(THREE)
        self.assertNoAck()

        self.assertNextEquals(None)
        self.assertNak()


class FileSystemBackendTests(TestCase):
    """Tests for FileSystemBackend."""

    def setUp(self):
        super(FileSystemBackendTests, self).setUp()
        self.path = tempfile.mkdtemp()
        self.repo = Repo.init(self.path)
        self.backend = FileSystemBackend()

    def test_nonexistant(self):
        self.assertRaises(NotGitRepository,
            self.backend.open_repository, "/does/not/exist/unless/foo")

    def test_absolute(self):
        repo = self.backend.open_repository(self.path)
        self.assertEqual(repo.path, self.repo.path)

    def test_child(self):
        self.assertRaises(NotGitRepository,
            self.backend.open_repository, os.path.join(self.path, "foo"))

    def test_bad_repo_path(self):
        repo = MemoryRepo.init_bare([], {})
        backend = DictBackend({'/': repo})

        self.assertRaises(NotGitRepository,
                          lambda: backend.open_repository('/ups'))


class ServeCommandTests(TestCase):
    """Tests for serve_command."""

    def setUp(self):
        super(ServeCommandTests, self).setUp()
        self.backend = DictBackend({})

    def serve_command(self, handler_cls, args, inf, outf):
        return serve_command(handler_cls, ["test"] + args, backend=self.backend,
            inf=inf, outf=outf)

    def test_receive_pack(self):
        commit = make_commit(id=ONE, parents=[], commit_time=111)
        self.backend.repos["/"] = MemoryRepo.init_bare(
            [commit], {b"refs/heads/master": commit.id})
        outf = BytesIO()
        exitcode = self.serve_command(ReceivePackHandler, ["/"], BytesIO(b"0000"), outf)
        outlines = outf.getvalue().splitlines()
        self.assertEqual(2, len(outlines))
        self.assertEqual(
            b"1111111111111111111111111111111111111111 refs/heads/master",
            outlines[0][4:].split(b"\x00")[0])
        self.assertEqual(b"0000", outlines[-1])
        self.assertEqual(0, exitcode)


class UpdateServerInfoTests(TestCase):
    """Tests for update_server_info."""

    def setUp(self):
        super(UpdateServerInfoTests, self).setUp()
        self.path = tempfile.mkdtemp()
        self.repo = Repo.init(self.path)

    def test_empty(self):
        update_server_info(self.repo)
        self.assertEqual(
            b"",
            open(os.path.join(self.path, ".git", "info", "refs"), 'rb').read())
        self.assertEqual(
            b"",
            open(os.path.join(self.path, ".git", "objects", "info", "packs"), 'rb').read())

    def test_simple(self):
        commit_id = self.repo.do_commit(
            message=b"foo",
            committer=b"Joe Example <joe@example.com>",
            ref=b"refs/heads/foo")
        update_server_info(self.repo)
        ref_text = open(os.path.join(self.path, ".git", "info", "refs"), 'rb').read()
        self.assertEqual(ref_text, commit_id + b"\trefs/heads/foo\n")
        packs_text = open(os.path.join(self.path, ".git", "objects", "info", "packs"), 'rb').read()
        self.assertEqual(packs_text, b"")
