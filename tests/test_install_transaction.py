"""Real temporary-file transaction checks; host commands, ownership and network are mocked."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock


spec = importlib.util.spec_from_file_location(
    'install_transaction_fixture', Path(__file__).resolve().parents[1] / 'installer/install.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='myteldesk-transaction-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = self.base / 'config'
        self.root = self.base / 'code'
        self.data = self.base / 'data'
        for directory in (self.config, self.root, self.data):
            directory.mkdir()
        for name, value in {
            'CONFIG': self.config, 'ROOT': self.root, 'DATA': self.data,
            'STATE': self.config / 'installation.json', 'ENV': self.config / 'myteldesk.env',
        }.items():
            patcher = mock.patch.object(mod, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.chown = self.start_patch(mod.os, 'chown', create=True)
        self.run = self.start_patch(mod, 'run', return_value=SimpleNamespace(returncode=0, stdout='', stderr=''))
        self.health = self.start_patch(mod, 'wait_health', return_value={'status': 'ok', 'ami_connected': True})
        self.start_patch(mod.subprocess, 'run', side_effect=AssertionError('host commands forbidden'))
        self.start_patch(mod, 'urlopen', side_effect=AssertionError('network forbidden'))
        self.start_patch(mod, 'mysql', side_effect=AssertionError('database access forbidden'))

    def start_patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def file(self, name, content=b'original\x00bytes\n', mode=0o640):
        path = self.base / name
        path.write_bytes(content)
        path.chmod(mode)
        # Windows read-only flags must not prevent cleanup after a failed assertion.
        self.addCleanup(lambda: path.chmod(0o600) if path.exists() and not path.is_symlink() else None)
        return path

    def symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            self.skipTest('native symlink creation unavailable: ' + str(error))

    def journal(self, transaction):
        return json.loads((transaction.directory / 'journal.json').read_text())

    def test_journal_and_backup_exist_before_target_atomic_write(self):
        target = self.file('managed.conf')
        old = target.read_bytes()
        transaction = mod.Transaction()
        actual_atomic = mod.atomic
        observed = []

        def inspect_before_write(path, content, *args, **kwargs):
            if Path(path) == target:
                entries = self.journal(transaction)
                self.assertEqual(target.read_bytes(), old)
                self.assertEqual(entries[0]['path'], str(target))
                self.assertEqual(Path(entries[0]['before']).read_bytes(), old)
                self.assertEqual(entries[0]['after'], hashlib.sha256(b'replacement').hexdigest())
                observed.append(True)
            return actual_atomic(path, content, *args, **kwargs)

        with mock.patch.object(mod, 'atomic', side_effect=inspect_before_write):
            transaction.write(target, b'replacement')
        self.assertEqual(observed, [True])
        self.assertEqual(target.read_bytes(), b'replacement')

    def test_rollback_restores_existing_bytes_mode_and_owner_and_removes_new_file(self):
        target = self.file('managed.conf')
        before = target.read_bytes()
        info = target.stat()
        added = self.base / 'added.conf'
        transaction = mod.Transaction()
        transaction.write(target, 'new config', 0o600)
        transaction.write(added, 'new include')
        transaction.rollback()
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), stat.S_IMODE(info.st_mode))
        self.assertFalse(added.exists())
        self.assertIn(mock.call(mock.ANY, info.st_uid, info.st_gid), self.chown.mock_calls)

    def test_restore_is_idempotent_after_success_and_uses_persisted_journal(self):
        target = self.file('existing.conf')
        added = self.base / 'added.conf'
        transaction = mod.Transaction()
        transaction.write(target, 'new value')
        transaction.write(added, 'new file')
        entries = self.journal(transaction)
        mod.restore_entries(entries)
        first = target.read_bytes()
        mod.restore_entries(entries)
        self.assertEqual(target.read_bytes(), first)
        self.assertFalse(added.exists())

    def test_external_edit_is_refused_and_never_overwritten(self):
        target = self.file('managed.conf')
        transaction = mod.Transaction()
        transaction.write(target, 'installer value')
        target.write_bytes(b'operator edit')
        with self.assertRaises(RuntimeError):
            transaction.rollback()
        self.assertEqual(target.read_bytes(), b'operator edit')

    def test_conflict_is_checked_before_restoring_other_journal_entries(self):
        target = self.file('first.conf')
        added = self.base / 'second.conf'
        transaction = mod.Transaction()
        transaction.write(target, 'installer value')
        transaction.write(added, 'owned new file')
        target.write_bytes(b'operator edit')
        with self.assertRaises(RuntimeError):
            transaction.rollback()
        self.assertEqual(target.read_bytes(), b'operator edit')
        self.assertTrue(added.exists(), 'conflict must not leave a partially restored configuration')

    def test_rollback_restores_mode_even_when_installer_kept_identical_bytes(self):
        target = self.file('unchanged-content.conf', mode=0o600)
        previous_mode = stat.S_IMODE(target.stat().st_mode)
        transaction = mod.Transaction()
        transaction.write(target, target.read_bytes(), 0o400)
        self.assertNotEqual(stat.S_IMODE(target.stat().st_mode), previous_mode)
        transaction.rollback()
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), previous_mode)

    def test_symlink_is_refused_even_if_referent_matches_backup(self):
        target = self.file('managed.conf')
        before = target.read_bytes()
        transaction = mod.Transaction()
        transaction.write(target, 'installer value')
        outside = self.file('unrelated.conf', before)
        target.unlink()
        self.symlink(target, outside)
        with self.assertRaises(RuntimeError):
            transaction.rollback()
        self.assertTrue(target.is_symlink())
        self.assertEqual(outside.read_bytes(), before)

    def test_target_atomic_failure_before_or_after_replace_remains_recoverable(self):
        for after_replace in (False, True):
            with self.subTest(after_replace=after_replace):
                target = self.file('target-' + str(after_replace) + '.conf')
                before = target.read_bytes()
                transaction = mod.Transaction()
                actual_atomic = mod.atomic

                def fail_target(path, content, *args, **kwargs):
                    if Path(path) == target:
                        if after_replace:
                            actual_atomic(path, content, *args, **kwargs)
                        raise OSError('fixture atomic interruption')
                    return actual_atomic(path, content, *args, **kwargs)

                with mock.patch.object(mod, 'atomic', side_effect=fail_target):
                    with self.assertRaises(OSError):
                        transaction.write(target, 'new value')
                mod.restore_entries(self.journal(transaction))
                self.assertEqual(target.read_bytes(), before)

    def test_journal_write_failure_never_replaces_target(self):
        target = self.file('managed.conf')
        before = target.read_bytes()
        transaction = mod.Transaction()
        with mock.patch.object(mod, 'atomic', side_effect=OSError('fixture journal failure')):
            with self.assertRaises(OSError):
                transaction.write(target, 'new value')
        self.assertEqual(target.read_bytes(), before)
        transaction.rollback()
        self.assertEqual(target.read_bytes(), before)

    def test_transaction_directories_are_unique_within_same_second(self):
        with mock.patch.object(mod.time, 'strftime', return_value='fixed-second'):
            first, second = mod.Transaction(), mod.Transaction()
        self.assertNotEqual(first.directory, second.directory)
        self.assertTrue(first.directory.is_dir())
        self.assertTrue(second.directory.is_dir())

    def pending(self, previous, transaction, current=None):
        (transaction.directory / 'previous-state.json').write_text(json.dumps(previous))
        pending = dict(previous, pending_journal=str(transaction.directory / 'journal.json'), pending_current=current)
        mod.STATE.write_text(json.dumps(pending))
        return pending

    def sentinels(self):
        values = {mod.ENV: b'JWT_SECRET=fixture-preserved\n',
                  self.data / 'app-data': b'customer-data-fixture',
                  self.data / 'bootstrap-admin.hash': b'bootstrap-fixture',
                  self.config / 'initial-admin.txt': b'private-login-fixture'}
        for path, value in values.items():
            path.write_bytes(value)
        return values

    def test_recover_prepared_transaction_without_journal_retains_data_and_secrets(self):
        values = self.sentinels()
        transaction = mod.Transaction()
        previous = {'owner': 'MytelDesk', 'schema': 1, 'status': 'prepared'}
        pending = self.pending(previous, transaction)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(mod.recover_pending(pending), previous)
        self.assertEqual(json.loads(mod.STATE.read_text()), previous)
        for path, value in values.items():
            self.assertEqual(path.read_bytes(), value)
        self.assertFalse((self.root / 'current').exists())
        self.health.assert_not_called()
        self.run.assert_any_call(['systemctl', 'disable', 'myteldesk'], check=False)

    def test_recover_ready_transaction_restores_pointer_config_and_preserves_data(self):
        values = self.sentinels()
        old_release, new_release = self.root / 'old-release', self.root / 'new-release'
        old_release.mkdir(); new_release.mkdir()
        self.symlink(self.root / 'current', new_release, directory=True)
        target = self.file('proxy.conf')
        original = target.read_bytes()
        transaction = mod.Transaction()
        transaction.write(target, 'new proxy')
        previous = {'owner': 'MytelDesk', 'schema': 1, 'status': 'ready', 'release': str(old_release)}
        pending = self.pending(previous, transaction, str(old_release))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(mod.recover_pending(pending), previous)
        self.assertEqual((self.root / 'current').resolve(), old_release.resolve())
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(json.loads(mod.STATE.read_text()), previous)
        for path, value in values.items():
            self.assertEqual(path.read_bytes(), value)
        self.health.assert_called_once_with()
        self.run.assert_any_call(['systemctl', 'start', 'myteldesk'])

    def test_pending_conflict_retains_recovery_record_and_application_data(self):
        values = self.sentinels()
        target = self.file('proxy.conf')
        transaction = mod.Transaction()
        transaction.write(target, 'new proxy')
        target.write_bytes(b'operator change')
        previous = {'owner': 'MytelDesk', 'schema': 1, 'status': 'ready'}
        pending = self.pending(previous, transaction, str(self.root / 'old-release'))
        with mock.patch.object(mod, 'restore_current') as current:
            with self.assertRaises(RuntimeError):
                mod.recover_pending(pending)
            current.assert_not_called()
        self.assertEqual(json.loads(mod.STATE.read_text()), pending)
        self.assertEqual(target.read_bytes(), b'operator change')
        for path, value in values.items():
            self.assertEqual(path.read_bytes(), value)
        self.health.assert_not_called()


if __name__ == '__main__':
    unittest.main()
