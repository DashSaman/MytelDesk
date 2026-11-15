"""Installer trust boundaries: no host command is run by this suite."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('install', Path(__file__).resolve().parents[1] / 'installer/install.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class InstallTests(unittest.TestCase):
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
