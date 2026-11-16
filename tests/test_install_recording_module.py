"""Owned native-module lifecycle against real temporary files and fake commands."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('recording_under_test', Path(__file__).resolve().parents[1]/'installer/recording.py')
recording = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recording)


class Transaction:
    def __init__(self): self.entries = []
    def write(self, path, content, *_args):
        self.entries.append((path, path.read_bytes() if path.exists() else None))
        path.write_bytes(content)
    def rollback(self):
        for path, before in reversed(self.entries):
            if before is None: path.unlink()
            else: path.write_bytes(before)


class RecordingModuleLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='desk-record-module-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.admin = self.root/'web/admin'; (self.admin/'modules').mkdir(parents=True)
        self.release = self.root/'release'
        source = self.release/'backend/pbx-modules/myteldeskrecording'; source.mkdir(parents=True)
        (source/'module.xml').write_bytes(b'<module><rawname>myteldeskrecording</rawname><version>1.0.0</version></module>')
        (source/'functions.inc.php').write_bytes(b'<?php function myteldeskrecording_hookGet_config($engine) {}')
        self.target = self.admin/'modules/myteldeskrecording'
        self.row = None; self.pending = False; self.calls = []; self.bad_action = None
        self.tx = Transaction()
    def command(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:2] == ['asterisk','-rx']:
            return SimpleNamespace(returncode=0,stdout=('GosubIf(guard?opdesk-record,s,1(${ARG1},${ARG2}))\n'*5),stderr='')
        if str(argv[1]).endswith('issabel-reload.php'):
            self.pending = False
            value = dict(status=True,num_errors=0,reload_needed=False)
        else:
            self.assertEqual(argv[:2], ['/usr/bin/php',str(self.release/'installer/recording-module.php')])
            action = argv[2]
            if action == self.bad_action: raise RuntimeError('synthetic native failure')
            if action in ('install','enable'):
                self.row = dict(version='1.0.0',enabled=True); self.pending = True
            if action == 'uninstall': self.row = None; self.pending = True
            value = dict(status=True,admin=str(self.admin),module=self.row,reload_needed=self.pending)
        return SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')
    def plan(self, previous=None): return recording.prepare(self.release,self.command,previous)
    def installed(self):
        plan = self.plan(); plan.activate(self.tx); recording.apply_native(self.release,self.command)
        return plan
    def test_fresh_registration_has_exact_files_and_one_native_install(self):
        plan = self.installed()
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ['functions.inc.php','module.xml'])
        self.assertEqual(sum(call[-1]=='install' for call in self.calls),1)
        plan.doctor()
        self.assertEqual(plan.record['version'],'1.0.0')
    def test_unknown_directory_or_registration_is_never_adopted(self):
        self.target.mkdir()
        with self.assertRaises(RuntimeError): self.plan()
        self.target.rmdir(); self.row=dict(version='1.0.0',enabled=True)
        with self.assertRaises(RuntimeError): self.plan()
        self.assertFalse(any(call[-1]=='install' for call in self.calls))
    def test_pending_operator_changes_refuse_before_files_or_api_mutation(self):
        self.pending=True
        with self.assertRaises(RuntimeError): self.plan()
        self.assertFalse(self.target.exists())
    def test_plan_then_directory_collision_cannot_overwrite_foreign_files(self):
        plan=self.plan(); self.target.mkdir(); foreign=self.target/'functions.inc.php'; foreign.write_bytes(b'foreign')
        with self.assertRaises(RuntimeError):plan.activate(self.tx)
        self.assertEqual(foreign.read_bytes(),b'foreign'); self.assertEqual(self.tx.entries,[])
    def test_plan_then_registration_collision_cannot_install(self):
        plan=self.plan(); self.row=dict(version='2.0.0',enabled=True)
        with self.assertRaises(RuntimeError):plan.activate(self.tx)
        self.assertFalse(self.target.exists()); self.assertEqual(self.row['version'],'2.0.0')
        self.assertFalse(plan.started)
    def test_same_owned_version_preserves_native_registration(self):
        first=self.installed(); self.calls=[]
        second=self.plan(first.record); second.activate(Transaction())
        self.assertFalse(any(call[-1] in ('install','enable','uninstall') for call in self.calls))
    def test_external_edit_or_extra_file_refuses_before_api_mutation(self):
        first=self.installed(); self.calls=[]
        (self.target/'functions.inc.php').write_bytes(b'operator change')
        with self.assertRaises(RuntimeError): self.plan(first.record)
        self.assertFalse(any(call[-1]=='install' for call in self.calls))
    def test_new_module_rollback_unregisters_before_restoring_files(self):
        plan=self.installed(); plan.before_restore(); self.assertIsNone(self.row)
        self.tx.rollback(); plan.after_restore()
        self.assertFalse(self.target.exists())
    def test_failed_native_install_retains_a_recoverable_intent(self):
        plan=self.plan(); self.bad_action='install'
        with self.assertRaises(RuntimeError): plan.activate(self.tx)
        recovered=recording.from_intent(plan.intent,self.command)
        recovered.before_restore(); self.tx.rollback(); recovered.after_restore()
        self.assertFalse(self.target.exists())
    def test_untouched_intent_can_recover_without_applying_new_operator_changes(self):
        plan=self.plan(); self.pending=True
        recovered=recording.from_intent(plan.intent,self.command); recovered.untouched()
        self.assertTrue(self.pending); self.assertFalse(self.target.exists())
        self.assertFalse(any(call[-1] in ('install','uninstall') for call in self.calls))
    def test_owned_upgrade_can_restore_previous_bytes_and_native_registration(self):
        first=self.installed(); before=(self.target/'functions.inc.php').read_bytes()
        (self.release/'backend/pbx-modules/myteldeskrecording/functions.inc.php').write_bytes(before+b'\n// reviewed upgrade')
        upgrade=self.plan(first.record); transaction=Transaction(); upgrade.activate(transaction)
        recording.apply_native(self.release,self.command)
        restore=recording.for_restore(self.release,self.command,upgrade.record,first.record)
        restore.before_restore(); transaction.rollback(); restore.after_restore()
        self.assertEqual((self.target/'functions.inc.php').read_bytes(),before)
        self.assertEqual(self.row,dict(version='1.0.0',enabled=True))
    def test_drift_during_recovery_does_not_unregister_or_remove(self):
        plan=self.installed(); (self.target/'foreign').write_text('keep')
        with self.assertRaises(RuntimeError): plan.before_restore()
        self.assertIsNotNone(self.row); self.assertTrue((self.target/'foreign').exists())
    def test_remove_only_owned_module_and_regenerate_without_restart(self):
        plan=self.installed(); plan.remove(); recording.apply_native(self.release,self.command)
        self.assertFalse(self.target.exists()); self.assertIsNone(self.row); self.assertFalse(self.pending)
        self.assertFalse(any('restart' in call for call in self.calls))
    def test_remove_from_ready_owned_receipt_unregisters_module(self):
        first=self.installed(); owned=self.plan(first.record); owned.remove()
        self.assertIsNone(self.row); self.assertFalse(self.target.exists())
    def test_doctor_rejects_dead_body_only_hooks(self):
        plan=self.installed(); original=self.command
        def dead(argv, **kwargs):
            if argv[:2]==['asterisk','-rx']: return SimpleNamespace(returncode=0,stdout='macro-dialout-one-predial-hook opdesk-record',stderr='')
            return original(argv,**kwargs)
        plan.run=dead
        with self.assertRaises(RuntimeError):plan.doctor()
    def test_ambiguous_native_output_is_not_success(self):
        def invalid(_argv, **_kwargs):return SimpleNamespace(returncode=0,stdout='{"status":true,"module":null}',stderr='')
        with self.assertRaises(RuntimeError):recording.prepare(self.release,invalid)
    @unittest.skipIf(os.name!='posix','real symlink fixture needs POSIX')
    def test_symlinked_native_module_ancestor_is_refused(self):
        original=self.admin/'modules'; original.rmdir(); elsewhere=self.root/'elsewhere'; elsewhere.mkdir(); original.symlink_to(elsewhere)
        with self.assertRaises(RuntimeError):self.plan()
        self.assertEqual(list(elsewhere.iterdir()),[])
    @unittest.skipIf(os.name!='posix','real symlink fixture needs POSIX')
    def test_native_ancestor_symlink_after_plan_is_refused_before_writing(self):
        plan=self.plan(); original=self.admin/'modules'; original.rmdir()
        elsewhere=self.root/'elsewhere'; elsewhere.mkdir(); original.symlink_to(elsewhere)
        with self.assertRaises(RuntimeError):plan.activate(self.tx)
        self.assertEqual(list(elsewhere.iterdir()),[]); self.assertEqual(self.tx.entries,[])
    def test_changed_native_administration_root_after_plan_is_refused(self):
        plan=self.plan(); old=self.admin; new=self.root/'other/admin'; (new/'modules').mkdir(parents=True)
        self.admin=new
        with self.assertRaises(RuntimeError):plan.activate(self.tx)
        self.assertFalse((old/'modules/myteldeskrecording').exists()); self.assertEqual(self.tx.entries,[])


if __name__ == '__main__': unittest.main()
