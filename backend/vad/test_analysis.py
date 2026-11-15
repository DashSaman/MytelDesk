"""VAD runtime regressions. NumPy is real; ONNX/network calls are isolated fakes."""
import hashlib
import importlib.util
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


if __name__ == '__main__':
    unittest.main()
