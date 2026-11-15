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
        inputs, states = [], []
        def infer(_names, values):
            inputs.append(values['input'].copy())
            states.append(values['state'].copy())
            return np.array([[0.0]], dtype=np.float32), values['state'] + 1
        session = SimpleNamespace(run=infer)
        samples = np.arange(1024, dtype=np.float32) / 2048.0
        with mock.patch.object(analysis, '_silero_session', return_value=session):
            analysis.silero_segments(samples, 16000)
            analysis.silero_segments(samples[:512], 16000)
        self.assertEqual([a.shape for a in inputs], [(1, 576), (1, 576), (1, 576)])
        np.testing.assert_array_equal(inputs[0][0, :64], np.zeros(64))
        np.testing.assert_array_equal(inputs[1][0, :64], samples[448:512])
        np.testing.assert_array_equal(inputs[2][0, :64], np.zeros(64))
        np.testing.assert_array_equal(states[0], np.zeros((2, 1, 128)))
        np.testing.assert_array_equal(states[1], np.ones((2, 1, 128)))
        np.testing.assert_array_equal(states[2], np.zeros((2, 1, 128)))

    def test_tampered_model_is_rejected_before_onnx_load(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.onnx'; model.write_bytes(b'tampered model')
            with mock.patch.dict(os.environ, {'VAD_MODEL_PATH': str(model), 'VAD_MODEL_SHA256': hashlib.sha256(b'expected').hexdigest()}), \
                    mock.patch.object(analysis, '_runtime_spec', return_value={'model': {'sha256': hashlib.sha256(b'expected').hexdigest()}}):
                with self.assertRaises(analysis.VadRuntimeError) as failure: analysis._ensure_silero_model()
                self.assertEqual(failure.exception.code, 'model_hash_mismatch')

    def test_missing_model_never_triggers_a_first_call_download(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / 'model.onnx'
            def fake_download(_url, destination): Path(destination).write_bytes(b'unverified download')
            with mock.patch.dict(os.environ, {'VAD_MODEL_PATH': str(model), 'VAD_MODEL_SHA256': 'a' * 64}), \
                    mock.patch.object(analysis, '_runtime_spec', return_value={'model': {'sha256': 'a' * 64}}), \
                    mock.patch('urllib.request.urlretrieve', side_effect=fake_download) as download:
                with self.assertRaises(analysis.VadRuntimeError) as failure: analysis._ensure_silero_model()
                self.assertEqual(failure.exception.code, 'model_missing')
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

    def test_bundled_model_is_verified_offline_and_override_requires_both_exact_pins(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            model = analysis._ensure_silero_model()
            self.assertEqual(hashlib.sha256(model.read_bytes()).hexdigest(), analysis._runtime_spec()['model']['sha256'])
        pinned = analysis._runtime_spec()['model']['sha256']
        for environment in ({'VAD_MODEL_PATH': 'relative.onnx', 'VAD_MODEL_SHA256': pinned},
                            {'VAD_MODEL_PATH': str(model.parent / '..' / 'model.onnx'), 'VAD_MODEL_SHA256': pinned},
                            {'VAD_MODEL_PATH': str(model)}, {'VAD_MODEL_SHA256': 'a' * 64}):
            with self.subTest(environment=environment), mock.patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(analysis.VadRuntimeError) as failure: analysis._ensure_silero_model()
                self.assertEqual(failure.exception.code, 'invalid_configuration')

    def test_invalid_runtime_metadata_never_falls_back_to_unpinned_model(self):
        for payload in (b'not-json', b'[]', b'{}', b' ' * (128 * 1024 + 1)):
            with self.subTest(size=len(payload)), mock.patch.object(Path, 'read_bytes', return_value=payload):
                with self.assertRaises(analysis.VadRuntimeError) as failure: analysis._runtime_spec()
                self.assertEqual(failure.exception.code, 'invalid_configuration')

    def test_model_integrity_failure_never_silently_reports_webrtc_success(self):
        with mock.patch.object(analysis, 'vad_with_silero', side_effect=analysis.VadRuntimeError('model_hash_mismatch')), \
                mock.patch.object(analysis, 'vad_with_webrtc', return_value=[]) as fallback:
            with self.assertRaises(analysis.VadRuntimeError): analysis._vad_one('unused.wav')
        fallback.assert_not_called()

    def test_hysteresis_and_final_padded_frame_preserve_sample_timestamps(self):
        probabilities = iter((0.9, 0.2, 0.05, 0.9, 0.9))
        def infer(_names, values):
            self.assertEqual(values['input'].shape, (1, 576))
            return np.array([[next(probabilities)]], dtype=np.float32), values['state']
        with mock.patch.object(analysis, '_silero_session', return_value=SimpleNamespace(run=infer)):
            segments = analysis.silero_segments(np.zeros(4 * 512 + 111, dtype=np.float32), 16000)
        self.assertEqual(segments, [(0.0, 0.064), (0.096, 0.135)])


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
                   mock.patch.dict(sys.modules, {'webrtcvad': SimpleNamespace(Vad=lambda _mode: self.vad),
                                                 'onnxruntime': SimpleNamespace()})]
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

    def test_repeated_doctor_probe_resets_recurrent_state_with_cached_session(self):
        states = []
        def infer(_names, values):
            states.append(values['state'].copy())
            return np.array([[0.0]], dtype=np.float32), values['state'] + 1
        self.session.run = infer
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(len(states), 4)
        for index, expected in enumerate((0, 1, 0, 1)):
            np.testing.assert_array_equal(states[index], np.full((2, 1, 128), expected))

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

    def test_doctor_reports_missing_numpy_and_preserves_only_safe_model_codes(self):
        with mock.patch.object(analysis, 'np', None):
            code, result = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(result['error_code'], 'dependency_unavailable')
        with mock.patch.object(analysis, '_ensure_silero_model', side_effect=analysis.VadRuntimeError('model_hash_mismatch')):
            code, result = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertEqual(result['error_code'], 'model_hash_mismatch')


if __name__ == '__main__':
    unittest.main()
