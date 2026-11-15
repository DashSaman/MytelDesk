"""VAD runtime regressions. NumPy is real; ONNX/network calls are isolated fakes."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np

spec = importlib.util.spec_from_file_location('vad_analysis_fixture', Path(__file__).with_name('call_vad_analysis.py'))
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class AnalysisTests(unittest.TestCase):
    def test_onnx_receives_context_and_resets_it_between_separate_legs(self):
        inputs = []
        def infer(_names, values):
            inputs.append(values['input'].copy())
            return np.array([[0.0]], dtype=np.float32), values['state']
        session = SimpleNamespace(run=infer)
        samples = np.arange(1024, dtype=np.float32) / 2048.0
        with mock.patch.object(analysis, '_silero_session', return_value=session):
            analysis.silero_segments(samples, 16000)
            analysis.silero_segments(samples[:512], 16000)
        self.assertEqual([a.shape for a in inputs], [(1, 576), (1, 576), (1, 576)])
        np.testing.assert_array_equal(inputs[0][0, :64], np.zeros(64))
        np.testing.assert_array_equal(inputs[1][0, :64], samples[448:512])
        np.testing.assert_array_equal(inputs[2][0, :64], np.zeros(64))

    def test_tampered_model_is_rejected_before_onnx_load(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.onnx'; model.write_bytes(b'tampered model')
            with mock.patch.dict(os.environ, {'VAD_MODEL_PATH': str(model), 'VAD_MODEL_SHA256': hashlib.sha256(b'expected').hexdigest()}), \
                    mock.patch.object(analysis, 'SILERO_MODEL_CACHE', model, create=True):
                with self.assertRaises(RuntimeError): analysis._ensure_silero_model()

    def test_missing_model_never_triggers_a_first_call_download(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.onnx'
            def fake_download(_url, destination): Path(destination).write_bytes(b'unverified download')
            with mock.patch.dict(os.environ, {'VAD_MODEL_PATH': str(model), 'VAD_MODEL_SHA256': 'a' * 64}), \
                    mock.patch.object(analysis, 'SILERO_MODEL_CACHE', model, create=True), \
                    mock.patch('urllib.request.urlretrieve', side_effect=fake_download) as download:
                with self.assertRaises(RuntimeError): analysis._ensure_silero_model()
                download.assert_not_called()

    def test_session_uses_only_one_cpu_thread_for_each_execution_pool(self):
        fake = SimpleNamespace(SessionOptions=lambda: SimpleNamespace(), InferenceSession=mock.Mock(),
                               ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=0))
        with mock.patch.dict(sys.modules, {'onnxruntime': fake}), \
                mock.patch.object(analysis, '_SILERO_SESSION', None), \
                mock.patch.object(analysis, '_ensure_silero_model', return_value=Path('unused.onnx')):
            analysis._silero_session()
        options = fake.InferenceSession.call_args[1]
        self.assertEqual(options['providers'], ['CPUExecutionProvider'])
        self.assertIn('sess_options', options)
        self.assertEqual(options['sess_options'].intra_op_num_threads, 1)
        self.assertEqual(options['sess_options'].inter_op_num_threads, 1)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.spec = {'model': {'version': '6.2.3', 'sha256': 'a' * 64},
                     'packages': [{'name': 'numpy', 'version': '2.4.6'},
                                  {'name': 'onnxruntime', 'version': '1.32.0'},
                                  {'name': 'webrtcvad-wheels', 'version': '2.0.14.post1'}]}
        self.inputs = []
        def infer(_names, values):
            self.inputs.append(values['input'].copy())
            return np.array([[0.0]], dtype=np.float32), values['state']
        self.session = SimpleNamespace(run=infer, get_providers=lambda: ['CPUExecutionProvider'],
            get_session_options=lambda: SimpleNamespace(intra_op_num_threads=1, inter_op_num_threads=1))
        self.versions = {item['name']: item['version'] for item in self.spec['packages']}
        self.vad = mock.Mock(); self.vad.is_speech.return_value = False
        patches = [mock.patch.object(analysis, '_runtime_spec', return_value=self.spec, create=True),
                   mock.patch.object(analysis, '_ensure_silero_model', return_value=Path('verified.onnx')),
                   mock.patch.object(analysis, '_silero_session', return_value=self.session),
                   mock.patch('importlib.metadata.version', side_effect=self.versions.__getitem__),
                   mock.patch('platform.python_version', return_value='3.11.18'),
                   mock.patch.dict(sys.modules, {'webrtcvad': SimpleNamespace(Vad=lambda _mode: self.vad)})]
        for patch in patches:
            patch.start(); self.addCleanup(patch.stop)

    def invoke(self):
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', ['call_vad_analysis.py', '--doctor']), \
                mock.patch('sys.stdout', output):
            code = analysis.main()
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        return code, json.loads(lines[0])

    def test_doctor_performs_two_real_adapter_frames_and_webrtc_probe(self):
        code, result = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['engine'], 'silero')
        self.assertEqual(result['model_sha256'], 'a' * 64)
        self.assertEqual(result['python_version'], '3.11.18')
        self.assertEqual(result['packages'], self.versions)
        self.assertEqual(result['context_samples'], 64)
        self.assertEqual(result['inference_frames'], 2)
        self.assertEqual([v.shape for v in self.inputs], [(1, 576), (1, 576)])
        self.vad.is_speech.assert_called_once_with(bytes(320), 16000)

    def test_doctor_rejects_nonfinite_or_speech_outputs(self):
        for probability in (float('nan'), float('inf'), -0.1, 1.1, 0.9):
            with self.subTest(probability=probability):
                self.session.run = lambda _names, values: (np.array([[probability]]), values['state'])
                code, result = self.invoke()
                self.assertNotEqual(code, 0)
                self.assertEqual(result, {'status': 'error', 'engine': 'silero', 'error_code': 'inference_failed'})

    def test_doctor_sanitizes_model_and_dependency_errors(self):
        with mock.patch.object(analysis, '_ensure_silero_model', side_effect=RuntimeError('/private/secret-path')):
            code, result = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(result['error_code'], 'inference_failed')
        with mock.patch('importlib.metadata.version', side_effect=ImportError('/private/package')):
            code, result = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(result['error_code'], 'dependency_unavailable')

    def test_doctor_refuses_wrong_versions_gpu_or_unbounded_pools(self):
        changes = [('version', '9.9.9'), ('provider', ['CUDAExecutionProvider']), ('threads', 8)]
        for kind, value in changes:
            with self.subTest(kind=kind):
                if kind == 'version': self.versions['numpy'] = value
                if kind == 'provider': self.session.get_providers = lambda: value
                if kind == 'threads': self.session.get_session_options = lambda: SimpleNamespace(intra_op_num_threads=value, inter_op_num_threads=1)
                code, result = self.invoke()
                self.assertNotEqual(code, 0)
                self.assertEqual(result['status'], 'error')
                self.versions['numpy'] = '2.4.6'
                self.session.get_providers = lambda: ['CPUExecutionProvider']

    def test_doctor_rejects_webrtc_failure_without_reporting_healthy(self):
        self.vad.is_speech.side_effect = RuntimeError('native VAD failed')
        code, result = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(result['error_code'], 'inference_failed')


if __name__ == '__main__':
    unittest.main()
