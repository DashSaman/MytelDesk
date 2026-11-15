"""Installer trust boundaries: no host command is run by this suite."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('install', Path(__file__).resolve().parents[1] / 'installer/install.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class InstallTests(unittest.TestCase):
    def test_fresh_database_on_slow_disk_can_take_over_ninety_seconds(self):
        clock=[0]
        def response(*_args,**_kwargs):
            if clock[0]<120: raise OSError('starting')
            return io.BytesIO(b'{"status":"ok","ami_connected":true}')
        def sleep(_seconds): clock[0]+=30
        with mock.patch.object(mod.time,'time',side_effect=lambda:clock[0]), mock.patch.object(mod.time,'sleep',side_effect=sleep), mock.patch.object(mod,'urlopen',side_effect=response):
            self.assertEqual(mod.wait_health()['status'],'ok')
        self.assertGreaterEqual(clock[0],120)

    def test_private_node_extraction_ignores_archive_owner_and_umask(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'owned'; root.mkdir(mode=0o755)
            bundle=Path(d)/'bundle'; (bundle/'installer').mkdir(parents=True)
            (bundle/'installer/runtime.json').write_text(json.dumps({'version':'v24.22.3','sha256':'a'*64}))
            def fake_run(argv, **_kwargs):
                if argv[0]=='tar':
                    self.assertIn('--no-same-owner',argv)
                    self.assertIn('--no-same-permissions',argv)
                    unpack=Path(argv[argv.index('-C')+1])/'node-v24.22.3-linux-x64'
                    (unpack/'bin').mkdir(parents=True,mode=0o700)
                    (unpack/'bin/node').write_bytes(b'fixture-node')
                    (unpack/'bin/node').chmod(0o700)
                return SimpleNamespace(stdout='v24.22.3\n',returncode=0)
            with mock.patch.object(mod,'ROOT',root), mock.patch.object(mod,'root_owned'), mock.patch.object(mod,'fetch_verified'), mock.patch.object(mod,'run',side_effect=fake_run), mock.patch.object(mod.os,'chown',create=True):
                node=mod.ensure_runtime(bundle)
                self.assertTrue(node.is_file())
                if mod.os.name=='posix':
                    self.assertEqual(node.stat().st_mode&0o777,0o755)
                    self.assertEqual(node.parent.stat().st_mode&0o777,0o755)

    def test_actual_schema_comments_are_not_expected_tables(self):
        release=Path(__file__).resolve().parents[1]
        text=(release/'backend/schema.sql').read_text()
        import re
        tables=re.findall(r'^CREATE TABLE IF NOT EXISTS\s+`?([A-Za-z0-9_]+)', text, re.M)
        with mock.patch.object(mod,'mysql',side_effect=['\n'.join(tables),'1']):
            mod.verify_app('not-a-live-password',release)

    def test_shared_config_edits_are_detected_before_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'manager_custom.conf'; path.write_text('; original\n#include manager_myteldesk.conf\n')
            state={'owned_hashes': {str(path): mod.digest(path)}}
            mod.check_managed_files(state)
            path.write_text(path.read_text()+'[another-account]\nsecret=preserved\n')
            with self.assertRaises(RuntimeError): mod.check_managed_files(state)
            self.assertIn('another-account',path.read_text())

    def test_real_ami_console_settings_format(self):
        self.assertTrue(mod.ami_enabled('Global Settings:\n  Manager (AMI):             Yes    \n  Web Manager (AMI/HTTP):    No\n'))
        self.assertFalse(mod.ami_enabled('Manager (AMI): No\nWeb Manager (AMI/HTTP): Yes\n'))
        self.assertFalse(mod.ami_enabled('garbled output'))

    def test_include_preserves_and_deduplicates(self):
        text = '; preserve\n[other]\nsecret=existing\n'
        added = mod.include_config(text, 'manager_myteldesk.conf')
        self.assertTrue(added.startswith(text))
        self.assertEqual(mod.include_config(added, 'manager_myteldesk.conf'), added)
        existing = '#include "/etc/asterisk/manager_myteldesk.conf"\n'
        self.assertEqual(mod.include_config(existing, 'manager_myteldesk.conf'), existing)

    def test_include_rejects_injection(self):
        for name in ['../manager.conf', 'x\n[admin]', '/tmp/root', 'x.conf;bad']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                mod.include_config('', name)

    def test_env_roundtrip_literal_and_rejects_control(self):
        self.assertEqual(mod.render_env({'JWT_SECRET': 'abc123_-.', 'PBX': 'Issabel'}), 'JWT_SECRET=abc123_-.\nPBX=Issabel\n')
        for value in ['x\nY=z', 'x\x00', '"', 'bad secret']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                mod.render_env({'SECRET': value})

    def test_database_grants_are_scoped(self):
        sql = mod.database_sql('a' * 48)
        self.assertIn('`myteldesk`.*', sql)
        self.assertIn('SELECT ON `asteriskcdrdb`.*', sql)
        self.assertIn('SELECT, INSERT, UPDATE, DELETE ON `asterisk`.*', sql)
        self.assertNotIn('ON *.*', sql)
        self.assertNotIn('DROP ', sql)
        self.assertNotIn('ALTER USER', sql)
        self.assertNotIn('event_scheduler', sql)
        with self.assertRaises(ValueError): mod.database_sql("evil'password")

    def test_bundle_hashes_and_extra_file_detection(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d); (p/'SOURCE_TREE').write_text('a'*40+'\n')
            b = (p/'SOURCE_TREE').read_bytes()
            manifest = {'schema': 1, 'version':'1.0.0', 'source_tree':'a'*40, 'files':{'SOURCE_TREE':{'bytes':len(b), 'sha256':hashlib.sha256(b).hexdigest()}}}
            (p/'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(mod.verify_bundle(p)['version'], '1.0.0')
            (p/'unlisted').write_text('unexpected')
            with self.assertRaises(ValueError): mod.verify_bundle(p)
            (p/'unlisted').unlink(); (p/'SOURCE_TREE').write_text('tampered')
            with self.assertRaises(ValueError): mod.verify_bundle(p)

    def test_tar_rejects_traversal_links_and_devices_before_extract(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE), ('C:/escape', tarfile.REGTYPE), ('ok/link', tarfile.SYMTYPE), ('ok/device', tarfile.CHRTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                archive=Path(d)/'bad.tar.gz'; dest=Path(d)/'out'
                with tarfile.open(str(archive),'w:gz') as tf:
                    item=tarfile.TarInfo(name); item.type=kind; item.linkname='../outside'
                    tf.addfile(item, io.BytesIO(b''))
                with self.assertRaises(ValueError): mod.safe_extract(archive,dest)
                self.assertFalse(dest.exists())

if __name__ == '__main__': unittest.main()
