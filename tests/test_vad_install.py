"""Isolated provisioning contracts; commands, downloads and ownership are fakes."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('vad_install_fixture', Path(__file__).resolve().parents[1] / 'installer/vad.py')
vad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vad)


class VadInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='myteldesk-vad-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bundle = self.base / 'bundle'
        self.sources = self.bundle / 'backend/vad'
        (self.sources / 'models').mkdir(parents=True)
        self.runtime = self.base / 'runtime'
        self.model = b'fixed model fixture'
        self.payloads = {}
        self.manifest = {'schema': 1, 'python_version': '3.11', 'architecture': 'x86_64',
                         'model': {'version': '6.2.3', 'filename': 'silero_vad.onnx',
                                   'sha256': hashlib.sha256(self.model).hexdigest()}, 'packages': []}
        for name in ('numpy', 'onnxruntime', 'webrtcvad-wheels'):
            filename = name.replace('-', '_') + '-1.0-cp311-cp311-manylinux_2_28_x86_64.whl'
            payload = ('wheel fixture ' + name).encode()
            self.payloads[filename] = payload
            self.manifest['packages'].append({'name': name, 'version': '1.0', 'filename': filename,
                'url': 'https://files.pythonhosted.org/packages/fixture/' + filename,
                'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)})
        (self.sources / 'models/silero_vad.onnx').write_bytes(self.model)
        (self.sources / 'call_vad_analysis.py').write_text('# doctor fixture, never executed\n')
        self.write_manifest()
        self.calls = []
        self.downloads = []
        self.python_present = True
        self.python_version = 'Python 3.11.18\n'
        self.pip_fail = False
        self.bad_download = False
        self.doctor_override = None
        self.doctor_returncode = 0
        patcher = mock.patch.object(os, 'chown', create=True)
        self.chown = patcher.start()
        self.addCleanup(patcher.stop)

    def write_manifest(self):
        (self.sources / 'runtime.json').write_text(json.dumps(self.manifest, sort_keys=True) + '\n')
        (self.sources / 'requirements.txt').write_text(''.join(
            '{name}=={version} --hash=sha256:{sha256}\n'.format(**item)
            for item in sorted(self.manifest['packages'], key=lambda p: p['name'])))

    def health(self):
        return {'status': 'ok', 'engine': 'silero', 'model_version': self.manifest['model']['version'],
                'model_sha256': self.manifest['model']['sha256'], 'providers': ['CPUExecutionProvider'],
                'threads': 1, 'context_samples': 64, 'inference_frames': 2, 'python_version': '3.11.18',
                'packages': {p['name']: p['version'] for p in self.manifest['packages']}}

    def run_command(self, argv, **kwargs):
        argv = [str(a) for a in argv]
        self.calls.append((argv, kwargs))
        if argv[0] == '/usr/bin/python3.11' and '--version' in argv:
            if not self.python_present:
                raise FileNotFoundError('fixture interpreter absent')
            return SimpleNamespace(returncode=0, stdout=self.python_version, stderr='')
        if argv[0] == 'dnf':
            self.python_present = True
        if 'venv' in argv:
            target = Path(argv[-1])
            (target / 'bin').mkdir(parents=True, exist_ok=True)
            (target / 'bin/python').write_bytes(b'private interpreter fixture')
            (target / 'bin/python').chmod(0o700)
            (target / 'pyvenv.cfg').write_text('include-system-site-packages = false\n')
        if 'pip' in argv and 'install' in argv and self.pip_fail:
            return SimpleNamespace(returncode=1, stdout='', stderr='fixture pip failure')
        if '--doctor' in argv:
            response = self.health() if self.doctor_override is None else self.doctor_override
            return SimpleNamespace(returncode=self.doctor_returncode,
                stdout=response if isinstance(response, str) else json.dumps(response), stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def fetch(self, url, destination, digest):
        self.downloads.append((url, str(destination), digest))
        payload = self.payloads[Path(destination).name]
        Path(destination).write_bytes(b'wrong bytes' if self.bad_download else payload)

    def provision(self, user='asterisk'):
        return vad.ensure_vad(self.bundle, self.runtime, self.run_command, self.fetch, service_user=user)

    def test_provisions_a_private_runtime_and_probes_as_the_service_user(self):
        environment = self.provision()
        self.assertEqual(set(environment), {'VAD_PYTHON', 'VAD_MODEL_PATH', 'VAD_MODEL_SHA256'})
        self.assertTrue(Path(environment['VAD_PYTHON']).is_file())
        self.assertEqual(Path(environment['VAD_MODEL_PATH']).read_bytes(), self.model)
        self.assertEqual(environment['VAD_MODEL_SHA256'], hashlib.sha256(self.model).hexdigest())
        command = next(argv for argv, _ in self.calls if '--doctor' in argv)
        self.assertEqual(command[:4], ['runuser', '-u', 'asterisk', '--'])
        self.assertNotIn('sudo', command)

    def test_install_uses_only_verified_binary_wheels_in_the_private_venv(self):
        environment = self.provision()
        command, options = next((a, k) for a, k in self.calls if 'pip' in a and 'install' in a)
        self.assertEqual(command[0], environment['VAD_PYTHON'])
        for option in ('--no-index', '--no-deps', '--require-hashes', '--only-binary=:all:', '--no-cache-dir'):
            self.assertIn(option, command)
        self.assertEqual(options['env']['PIP_CONFIG_FILE'], os.devnull)
        self.assertEqual(len(self.downloads), len(self.manifest['packages']))
        self.assertTrue(any('pip' in a and 'check' in a for a, _ in self.calls))

    def test_missing_python_installs_only_the_parallel_versioned_packages(self):
        self.python_present = False
        self.provision()
        commands = [a for a, _ in self.calls if a[0] == 'dnf']
        self.assertEqual(commands, [['dnf', '-y', 'install', 'python3.11', 'python3.11-pip']])
        self.assertFalse(any('alternatives' in a or 'python' in a[1:2] for a, _ in self.calls))

    def test_wrong_interpreter_version_is_not_reported_as_ready(self):
        self.python_version = 'Python 3.12.0\n'
        with self.assertRaises(RuntimeError):
            self.provision()
        self.assertFalse(any('pip' in a for a, _ in self.calls))

    def test_reinstall_is_a_read_only_probe_when_the_same_runtime_is_healthy(self):
        first = self.provision()
        files = {str(p): p.read_bytes() for p in self.runtime.rglob('*') if p.is_file()}
        self.calls[:] = []; self.downloads[:] = []
        self.assertEqual(self.provision(), first)
        self.assertEqual(self.downloads, [])
        self.assertTrue(self.calls)
        self.assertTrue(all('--doctor' in a for a, _ in self.calls))
        self.assertEqual({str(p): p.read_bytes() for p in self.runtime.rglob('*') if p.is_file()}, files)

    def test_changed_lock_creates_another_generation_and_preserves_the_previous_one(self):
        first = self.provision()
        old = Path(first['VAD_PYTHON']).read_bytes()
        self.manifest['model']['version'] = '6.2.4'
        self.write_manifest()
        second = self.provision()
        self.assertNotEqual(first['VAD_PYTHON'], second['VAD_PYTHON'])
        self.assertEqual(Path(first['VAD_PYTHON']).read_bytes(), old)

    def test_package_failure_never_marks_the_partial_runtime_ready(self):
        self.pip_fail = True
        with self.assertRaises(RuntimeError):
            self.provision()
        markers = list(self.runtime.rglob('.myteldesk-vad.json'))
        self.assertEqual(len(markers), 1)
        self.assertEqual(json.loads(markers[0].read_text())['status'], 'building')

    def test_an_owned_interrupted_generation_can_resume_without_deleting_other_data(self):
        self.runtime.mkdir()
        sentinel = self.runtime / 'operator-note'; sentinel.write_text('preserve')
        self.pip_fail = True
        with self.assertRaises(RuntimeError): self.provision()
        self.pip_fail = False
        environment = self.provision()
        self.assertTrue(Path(environment['VAD_PYTHON']).is_file())
        self.assertEqual(sentinel.read_text(), 'preserve')

    def test_missing_or_changed_bundled_model_fails_before_host_commands(self):
        model = self.sources / 'models/silero_vad.onnx'
        for value in (None, b'changed'):
            with self.subTest(value=value):
                if model.exists(): model.unlink()
                if value is not None: model.write_bytes(value)
                with self.assertRaises((ValueError, RuntimeError)): self.provision()
                self.assertEqual(self.calls, [])
                self.assertEqual(self.downloads, [])

    def test_download_hash_is_rechecked_before_pip_can_execute(self):
        self.bad_download = True
        with self.assertRaises(RuntimeError): self.provision()
        self.assertFalse(any('pip' in a for a, _ in self.calls))

    def test_invalid_manifest_paths_hashes_and_urls_fail_before_any_command(self):
        baseline = copy.deepcopy(self.manifest)
        for field, value in (('filename', '../escape.whl'), ('url', 'https://example.com/wheel.whl'),
                             ('sha256', 'not-a-hash'), ('version', '1.0 --index-url=bad')):
            with self.subTest(field=field):
                self.manifest = copy.deepcopy(baseline)
                self.manifest['packages'][0][field] = value
                self.write_manifest()
                with self.assertRaises(ValueError): self.provision()
                self.assertEqual(self.calls, [])

    def test_requirements_cannot_add_an_unverified_package_or_pip_option(self):
        with (self.sources / 'requirements.txt').open('a') as stream:
            stream.write('unverified-package\n--extra-index-url=https://example.com\n')
        with self.assertRaises(ValueError): self.provision()
        self.assertEqual(self.calls, [])

    def test_existing_unmanaged_generation_is_not_modified(self):
        self.provision()
        marker = next(self.runtime.rglob('.myteldesk-vad.json'))
        marker.unlink()
        before = {str(p): p.read_bytes() for p in self.runtime.rglob('*') if p.is_file()}
        self.calls[:] = []
        with self.assertRaises(RuntimeError): self.provision()
        self.assertEqual(self.calls, [])
        self.assertEqual({str(p): p.read_bytes() for p in self.runtime.rglob('*') if p.is_file()}, before)

    def test_corrupted_ready_model_is_not_silently_replaced_or_accepted(self):
        environment = self.provision()
        model = Path(environment['VAD_MODEL_PATH']); model.write_bytes(b'tampered')
        self.calls[:] = []; self.downloads[:] = []
        with self.assertRaises(RuntimeError): self.provision()
        self.assertEqual(model.read_bytes(), b'tampered')
        self.assertEqual(self.downloads, [])

    def test_service_identity_is_validated_and_cannot_probe_as_root(self):
        for user in ('root', '', 'asterisk\nroot', 'asterisk;command', '-u root'):
            with self.subTest(user=user), self.assertRaises(ValueError): self.provision(user)
        self.assertEqual(self.calls, [])

    def test_existing_opdesk_identity_is_honored_and_other_environment_secrets_are_ignored(self):
        environment = self.provision('opdesk')
        environment['DB_PASSWORD'] = 'never-pass-this-fixture-secret'
        self.calls[:] = []
        result = vad.doctor_vad(self.bundle, environment, self.run_command, service_user='opdesk')
        self.assertEqual(result['status'], 'ok')
        command = self.calls[-1][0]
        self.assertEqual(command[:4], ['runuser', '-u', 'opdesk', '--'])
        self.assertNotIn(environment['DB_PASSWORD'], ' '.join(command))
        self.assertLessEqual(self.calls[-1][1]['timeout'], 60)

    def test_doctor_rejects_failed_malformed_or_incomplete_success(self):
        environment = self.provision()
        bad = ["not JSON", ' ' * 40000 + '{}', {'status': 'ok'},
               dict(self.health(), engine='webrtc'), dict(self.health(), threads=16),
               dict(self.health(), context_samples=0), dict(self.health(), inference_frames=0),
               dict(self.health(), model_sha256='b' * 64), dict(self.health(), packages={})]
        for response in bad:
            self.doctor_override = response
            with self.subTest(response_type=type(response).__name__), self.assertRaises(RuntimeError):
                vad.doctor_vad(self.bundle, environment, self.run_command)
        self.doctor_override = None; self.doctor_returncode = 1
        with self.assertRaises(RuntimeError): vad.doctor_vad(self.bundle, environment, self.run_command)

    def test_root_owned_modes_keep_service_files_readable_but_not_writable(self):
        environment = self.provision()
        self.assertTrue(self.chown.called)
        self.assertTrue(all(call[0][1:3] == (0, 0) for call in self.chown.call_args_list))
        if os.name == 'posix':
            self.assertEqual(Path(environment['VAD_PYTHON']).stat().st_mode & 0o777, 0o755)
            self.assertEqual(Path(environment['VAD_MODEL_PATH']).stat().st_mode & 0o777, 0o644)

    def test_runtime_parent_symlink_is_rejected_before_any_command(self):
        outside = self.base / 'outside'; outside.mkdir()
        try: self.runtime.symlink_to(outside, target_is_directory=True)
        except OSError as error: self.skipTest('Native symlink privilege unavailable: ' + str(error))
        with self.assertRaises(ValueError): self.provision()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
