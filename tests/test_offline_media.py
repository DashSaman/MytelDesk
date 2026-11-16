"""Pinned local Node/wheel reads; no network, services or packages."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
spec = importlib.util.spec_from_file_location('desk_offline_fixture', str(ROOT / 'installer/install.py'))
install = importlib.util.module_from_spec(spec); spec.loader.exec_module(install)
import vad


class OfflineMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'media'; self.root.mkdir()
        self.payload = b'pinned-node-archive'
        self.node = 'runtime/node-v24.22.3-linux-x64.tar.xz'
        file = self.root / self.node; file.parent.mkdir(); file.write_bytes(self.payload)
        self.pin = hashlib.sha256(self.payload).hexdigest()
        self.value = dict(schema=1, owner='MyTellPBX', platform='rocky8.10-x86_64', node=self.node,
                          wheels=[], files={self.node: dict(sha256=self.pin, bytes=len(self.payload))})

    def fetcher(self):
        self.assertTrue(hasattr(install, 'offline_fetcher'), 'Desk has no pinned offline fetch path')
        raw = json.dumps(self.value).encode(); (self.root / 'manifest.json').write_bytes(raw)
        return install.offline_fetcher(self.root, hashlib.sha256(raw).hexdigest(),
                                      expected_uid=os.getuid() if hasattr(os, 'getuid') else 0)

    def test_reads_only_matching_pinned_node_without_network(self):
        fetch = self.fetcher(); out = Path(self.temp.name) / 'node.tar.xz'
        with mock.patch.object(install, 'urlopen', side_effect=AssertionError('network forbidden')):
            fetch('https://nodejs.org/dist/v24.22.3/node-v24.22.3-linux-x64.tar.xz', out, self.pin)
        self.assertEqual(out.read_bytes(), self.payload)

    def test_missing_wrong_hash_and_changed_source_never_fall_back(self):
        fetch = self.fetcher(); out = Path(self.temp.name) / 'copy'
        for url, pin in [('https://remote/missing.whl', self.pin),
                         ('https://remote/node-v24.22.3-linux-x64.tar.xz', '0' * 64)]:
            with self.subTest(url=url), self.assertRaises(ValueError): fetch(url, out, pin)
        (self.root / self.node).write_bytes(b'changed')
        with self.assertRaises(ValueError): fetch('https://remote/node-v24.22.3-linux-x64.tar.xz', out, self.pin)
        self.assertFalse(out.exists())

    def test_manifest_pin_and_traversal_are_rejected(self):
        self.fetcher()
        with self.assertRaises(ValueError): install.offline_fetcher(self.root, '0' * 64)
        self.value['node'] = '../outside'
        with self.assertRaises(ValueError): self.fetcher()

    def test_wheel_is_copied_from_its_exact_manifest_entry(self):
        name = 'wheels/numpy-1.0.whl'; file = self.root / name; file.parent.mkdir(); file.write_bytes(b'wheel')
        pin = hashlib.sha256(b'wheel').hexdigest()
        self.value['wheels'] = [name]; self.value['files'][name] = dict(bytes=5, sha256=pin)
        destination = Path(self.temp.name) / 'wheel.whl'
        self.fetcher()('https://files.pythonhosted.org/packages/fixture/numpy-1.0.whl', destination, pin)
        self.assertEqual(destination.read_bytes(), b'wheel')

    def test_unknown_profile_duplicate_asset_and_invalid_records_are_rejected(self):
        for key, value in [('schema', True), ('owner', 'other'), ('platform', 'other'), ('files', [])]:
            before = self.value[key]; self.value[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.fetcher()
            self.value[key] = before
        self.value['wheels'] = [self.node]
        with self.assertRaises(ValueError): self.fetcher()
        self.value['wheels'] = []
        self.value['files'][self.node]['bytes'] = True
        with self.assertRaises(ValueError): self.fetcher()

    def test_existing_destination_and_missing_manifest_are_not_adopted(self):
        fetch = self.fetcher(); destination = Path(self.temp.name) / 'existing'; destination.write_bytes(b'operator')
        with self.assertRaises(FileExistsError): fetch('https://remote/node-v24.22.3-linux-x64.tar.xz', destination, self.pin)
        self.assertEqual(destination.read_bytes(), b'operator')
        (self.root / 'manifest.json').unlink()
        with self.assertRaises(OSError): install.offline_fetcher(self.root, 'a' * 64)

    def test_incomplete_offline_install_flags_fail_before_any_native_probe(self):
        with mock.patch.object(install, 'verify_bundle', side_effect=AssertionError('native work forbidden')):
            with self.assertRaises(ValueError): install.install(self.root, offline_media=self.root)

    @unittest.skipUnless(os.name == 'posix', 'Native POSIX link check')
    def test_media_asset_symlink_is_rejected(self):
        fetch = self.fetcher(); file = self.root / self.node; file.unlink(); file.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError): fetch('https://remote/node-v24.22.3-linux-x64.tar.xz', Path(self.temp.name) / 'out', self.pin)

    def test_offline_vad_missing_python_refuses_before_dnf(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if argv == [vad.PYTHON, '--version']: return SimpleNamespace(returncode=1, stdout='')
            raise AssertionError('Package/network command must not run')
        with mock.patch.object(vad, '_load_spec', return_value=(self.root, {'model': {'filename':'model.onnx', 'sha256':'a'*64}}, '', 'b'*64)), \
             mock.patch.object(vad, '_write_marker'), mock.patch.object(vad, '_trusted_owner'):
            try: vad.ensure_vad(self.root, self.root / 'vad-runtime', run, lambda *a: self.fail('network'), offline=True)
            except TypeError as error: self.fail('Offline VAD contract absent: ' + str(error))
            except RuntimeError as error: self.assertIn('offline', str(error).lower())
            else: self.fail('Missing Python3.11 accepted')
        self.assertEqual(calls, [[vad.PYTHON, '--version']])


if __name__ == '__main__': unittest.main()
