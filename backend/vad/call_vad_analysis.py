#!/usr/bin/env python3
"""
VAD analysis for OpDesk call recordings.

Each call produces two mono WAV files written by MixMonitor:
  <base>-sp1.<fmt>  — caller leg  (r() receive)
  <base>-sp2.<fmt>  — callee leg  (t() transmit)

Primary: Silero VAD (ONNX runtime) — neural model, accurate on noisy/telephony audio.
Fallback: WebRTC VAD with sliding-window smoothing + RMS normalisation.

Usage:
  vad/.venv/bin/python vad/call_vad_analysis.py /var/spool/asterisk/single/2024/06/11/callbase
  vad/.venv/bin/python vad/call_vad_analysis.py --sp1 call-sp1.wav --sp2 call-sp2.wav
  vad/.venv/bin/python vad/call_vad_analysis.py --all --pretty
  vad/.venv/bin/python vad/call_vad_analysis.py --backend webrtc   # force fallback
  vad/.venv/bin/python vad/call_vad_analysis.py --serve            # backend worker (see serve())
  vad/.venv/bin/python vad/call_vad_analysis.py --doctor           # offline CPU inference check

Requirements (vad/requirements.txt, provisioned by the installer in a private Python 3.11 environment):
  numpy
  onnxruntime          # for Silero (recommended)
  webrtcvad-wheels     # for fallback (prebuilt wheels of webrtcvad; same `import webrtcvad`)

The backend (src/services/vad-runner.ts) runs this file under the interpreter named by
VAD_PYTHON, else vad/.venv/bin/python, else python3.
"""

# Keep annotations unevaluated; the supported, pinned runtime is Python 3.11.
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import re
import struct
import sys
import wave
from pathlib import Path

# These limits affect this analysis process only, never the PBX or system runtime.
for _thread_variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_thread_variable] = '1'
try:
    import numpy as np
except (ImportError, OSError):
    np = None  # --doctor must still return its bounded, sanitized JSON contract.

log = logging.getLogger(__name__)

RECORD_SINGLE_DIR = "/var/spool/asterisk/single"
EXTS = (".wav", ".WAV")

# ---------------------------------------------------------------------------
# Silero VAD settings
# ---------------------------------------------------------------------------
SILERO_SAMPLE_RATE    = 16000
SILERO_CHUNK_SAMPLES  = 512           # 32 ms at 16 kHz
SILERO_CONTEXT_SAMPLES = 64          # preceding samples required by the upstream wrapper
SILERO_ONSET          = 0.40          # prob must reach this to open a speech region
SILERO_OFFSET         = 0.10          # prob must drop below this to close it

# ---------------------------------------------------------------------------
# Shared post-processing settings
# ---------------------------------------------------------------------------
MERGE_GAP_S    = 0.5   # merge bursts separated by silence shorter than this
MIN_SEGMENT_S  = 0.15  # drop segments shorter than this

# ---------------------------------------------------------------------------
# WebRTC VAD fallback settings
# ---------------------------------------------------------------------------
WEBRTC_FRAME_MS      = 10   # 10 ms — highest supported resolution
WEBRTC_AGGRESSIVENESS = 2
WEBRTC_SMOOTH_WINDOW = 5    # sliding window width for vote smoothing
WEBRTC_SMOOTH_VOTES  = 3    # frames-in-window that must be speech to count as speech

SUPPORTED_RATES = {8000, 16000, 32000, 48000}


# ---------------------------------------------------------------------------
# Audio loading
# ---------------------------------------------------------------------------

def load_mono_f32(filepath) -> tuple[int, np.ndarray]:
    """
    Load a WAV file as float32 mono, returning (sample_rate, samples).
    Mixes multi-channel down to mono.  Does NOT resample here.
    """
    with wave.open(filepath, "rb") as wf:
        n_ch = wf.getnchannels()
        sw   = wf.getsampwidth()
        sr   = wf.getframerate()
        nf   = wf.getnframes()
        raw  = wf.readframes(nf)

    if sw == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    else:
        raise ValueError(f"Unsupported sample width {sw} in {filepath}")

    if n_ch > 1:
        samples = samples.reshape(-1, n_ch).mean(axis=1)

    return sr, samples


def resample_f32(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear-interpolation resample (cheap, good enough for speech VAD)."""
    if src == dst:
        return samples
    n_out = int(len(samples) * dst / src)
    x_old = np.linspace(0, len(samples) - 1, len(samples))
    x_new = np.linspace(0, len(samples) - 1, n_out)
    return np.interp(x_new, x_old, samples).astype(np.float32)


def rms_normalize(samples: np.ndarray, target_rms: float = 0.05) -> np.ndarray:
    """Normalise audio volume so quiet legs are detected properly."""
    rms = np.sqrt(np.mean(samples ** 2))
    if rms < 1e-9:
        return samples
    return (samples * (target_rms / rms)).clip(-1.0, 1.0)


# ---------------------------------------------------------------------------
# Segment post-processing (shared)
# ---------------------------------------------------------------------------

def merge_segments(raw_segs: list[tuple[float, float]],
                   gap: float = MERGE_GAP_S,
                   min_len: float = MIN_SEGMENT_S) -> list[dict]:
    """Merge close speech bursts, drop very short segments."""
    if not raw_segs:
        return []
    merged = [list(raw_segs[0])]
    for s, e in raw_segs[1:]:
        if s - merged[-1][1] <= gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [{"start": round(s, 3), "end": round(e, 3)}
            for s, e in merged if (e - s) >= min_len]


# ---------------------------------------------------------------------------
# Silero VAD (primary)
# ---------------------------------------------------------------------------

class VadRuntimeError(RuntimeError):
    """A public, path-free failure code; integrity failures never trigger fallback."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _runtime_spec() -> dict:
    try:
        raw = Path(__file__).with_name('runtime.json').read_bytes()
        if len(raw) > 128 * 1024:
            raise ValueError('manifest too large')
        spec = json.loads(raw)
        model = spec['model']
        if (spec['schema'] != 1 or spec['python_version'] != '3.11'
                or model['filename'] != 'silero_vad.onnx'
                or not re.fullmatch(r'[0-9a-f]{64}', model['sha256'])
                or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', model['version'])
                or not isinstance(spec['packages'], list)):
            raise ValueError('invalid manifest')
        return spec
    except (OSError, ValueError, KeyError, TypeError):
        raise VadRuntimeError('invalid_configuration') from None


def _ensure_silero_model() -> Path:
    """Verify the installed/bundled model; never download during a call or probe."""
    model = _runtime_spec()['model']
    configured_path = os.getenv('VAD_MODEL_PATH')
    configured_hash = os.getenv('VAD_MODEL_SHA256')
    if configured_path is not None or configured_hash is not None:
        if not configured_path or configured_hash != model['sha256']:
            raise VadRuntimeError('invalid_configuration')
        path = Path(configured_path)
        if not path.is_absolute() or '..' in path.parts:
            raise VadRuntimeError('invalid_configuration')
    else:
        path = Path(__file__).resolve().parent / 'models' / model['filename']
    try:
        if not path.is_file() or path.stat().st_size > 10 * 1024 * 1024:
            raise VadRuntimeError('model_missing')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise VadRuntimeError('model_missing') from None
    if digest != model['sha256']:
        raise VadRuntimeError('model_hash_mismatch')
    return path


_SILERO_SESSION = None


def _silero_session():
    """One ONNX session per process: the --serve worker loads the model once, not per leg."""
    global _SILERO_SESSION
    if _SILERO_SESSION is None:
        model_path = str(_ensure_silero_model())
        import onnxruntime as ort  # imported here so explicit WebRTC use stays independent
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        _SILERO_SESSION = ort.InferenceSession(model_path, sess_options=options, providers=["CPUExecutionProvider"])
    return _SILERO_SESSION


def _silero_step(session, frame, state, context):
    """The v6 wrapper prepends 64 samples to each 512-sample frame at 16 kHz."""
    inp = np.concatenate((context, frame.reshape(1, -1)), axis=1).astype(np.float32, copy=False)
    output, state = session.run(['output', 'stateN'],
        {'input': inp, 'state': state, 'sr': np.array(SILERO_SAMPLE_RATE, dtype=np.int64)})
    probability = float(output[0][0])
    if (not np.isfinite(probability) or not 0.0 <= probability <= 1.0
            or state.shape != (2, 1, 128) or not np.isfinite(state).all()):
        raise VadRuntimeError('inference_failed')
    return probability, state, inp[:, -SILERO_CONTEXT_SAMPLES:].copy()


def silero_segments(samples: np.ndarray, sr: int,
                    onset: float = SILERO_ONSET,
                    offset: float = SILERO_OFFSET) -> list[tuple[float, float]]:
    """
    Run the pinned Silero model on float32 mono samples at `sr` Hz.
    Model: inputs=(input, state, sr), outputs=(output, stateN).

    Uses hysteresis (onset > offset) so momentary dips inside an utterance
    don't fragment a single word into multiple tiny segments.
    Returns raw (start_s, end_s) tuples before merge/filter.
    """
    sess = _silero_session()

    target_sr = SILERO_SAMPLE_RATE
    if sr != target_sr:
        samples = resample_f32(samples, sr, target_sr)

    state     = np.zeros((2, 1, 128), dtype=np.float32)
    context   = np.zeros((1, SILERO_CONTEXT_SAMPLES), dtype=np.float32)

    segs: list[tuple[float, float]] = []
    in_speech = False
    t_start   = 0.0
    chunk     = SILERO_CHUNK_SAMPLES
    n         = len(samples)

    for off in range(0, n, chunk):
        frame = samples[off: off + chunk]
        if len(frame) < chunk:
            frame = np.pad(frame, (0, chunk - len(frame)))
        ts = off / target_sr

        prob, state, context = _silero_step(sess, frame, state, context)

        if not in_speech and prob >= onset:
            t_start, in_speech = ts, True
        elif in_speech and prob < offset:
            segs.append((round(t_start, 3), round(ts, 3)))
            in_speech = False

    if in_speech:
        segs.append((round(t_start, 3), round(n / target_sr, 3)))

    return segs


def vad_with_silero(filepath: str) -> list[dict]:
    sr, samples = load_mono_f32(filepath)
    samples = rms_normalize(samples)
    raw = silero_segments(samples, sr)
    return merge_segments(raw)


# ---------------------------------------------------------------------------
# WebRTC VAD (fallback)
# ---------------------------------------------------------------------------

def _to_pcm16(samples: np.ndarray, target_sr: int, src_sr: int) -> bytes:
    """Resample + convert float32 → int16 PCM bytes."""
    if src_sr != target_sr:
        samples = resample_f32(samples, src_sr, target_sr)
    return (samples * 32767).clip(-32768, 32767).astype("<i2").tobytes()


def _webrtc_raw_frames(pcm_bytes: bytes, sr: int,
                       aggressiveness: int = WEBRTC_AGGRESSIVENESS,
                       frame_ms: int = WEBRTC_FRAME_MS) -> list[bool]:
    """Return a boolean list: True = speech for each frame."""
    import webrtcvad
    vad = webrtcvad.Vad(aggressiveness)
    frame_bytes = int(sr * frame_ms / 1000) * 2  # 16-bit samples
    flags = []
    for off in range(0, len(pcm_bytes) - frame_bytes + 1, frame_bytes):
        frame = pcm_bytes[off: off + frame_bytes]
        try:
            flags.append(vad.is_speech(frame, sr))
        except Exception:
            flags.append(False)
    return flags


def _smooth(flags: list[bool],
            window: int = WEBRTC_SMOOTH_WINDOW,
            votes: int = WEBRTC_SMOOTH_VOTES) -> list[bool]:
    """Sliding-window majority vote to remove single-frame noise spikes."""
    n = len(flags)
    out = []
    half = window // 2
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(sum(flags[lo:hi]) >= votes)
    return out


def vad_with_webrtc(filepath: str,
                    aggressiveness: int = WEBRTC_AGGRESSIVENESS,
                    frame_ms: int = WEBRTC_FRAME_MS) -> list[dict]:
    """WebRTC VAD with smoothing and RMS normalisation."""
    sr, samples = load_mono_f32(filepath)
    samples = rms_normalize(samples)

    target_sr = min(SUPPORTED_RATES, key=lambda r: abs(r - sr))
    pcm = _to_pcm16(samples, target_sr, sr)

    raw_flags = _webrtc_raw_frames(pcm, target_sr, aggressiveness, frame_ms)
    smooth_flags = _smooth(raw_flags)

    segs: list[tuple[float, float]] = []
    in_speech = False
    t_start = 0.0
    frame_s = frame_ms / 1000

    for i, is_speech in enumerate(smooth_flags):
        ts = i * frame_s
        if is_speech and not in_speech:
            t_start, in_speech = ts, True
        elif not is_speech and in_speech:
            segs.append((round(t_start, 3), round(ts, 3)))
            in_speech = False

    if in_speech:
        total_s = len(pcm) / 2 / target_sr
        segs.append((round(t_start, 3), round(total_s, 3)))

    return merge_segments(segs)


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------

def _vad_one(filepath: str, backend: str = "auto") -> list[dict]:
    """Run VAD on a single file, returning merged segments."""
    use_silero = backend in ("silero", "auto")
    if use_silero:
        try:
            return vad_with_silero(filepath)
        except VadRuntimeError:
            raise  # missing/tampered pins must not look like healthy fallback analysis
        except Exception as e:
            if backend == "silero":
                raise
            log.warning("Silero VAD failed (%s), falling back to webrtcvad: %s", filepath, e)
    return vad_with_webrtc(filepath)


# ---------------------------------------------------------------------------
# Pairing helpers
# ---------------------------------------------------------------------------

def pair_from_base(base: str) -> tuple[str | None, str | None]:
    for sp in ("sp1", "sp2"):
        candidates = {}
        for ext in EXTS:
            p = f"{base}-{sp}{ext}"
            if os.path.isfile(p):
                candidates[sp] = p
                break
    sp1 = next((f"{base}-sp1{ext}" for ext in EXTS if os.path.isfile(f"{base}-sp1{ext}")), None)
    sp2 = next((f"{base}-sp2{ext}" for ext in EXTS if os.path.isfile(f"{base}-sp2{ext}")), None)
    return sp1, sp2


def find_pairs(root: str) -> list[tuple[str, str]]:
    sp1_pattern = re.compile(r"(.+)-sp1(\.[^.]+)$")
    pairs = []
    for dirpath, _, files in os.walk(root):
        for f in sorted(files):
            m = sp1_pattern.match(f)
            if not m:
                continue
            sp1 = os.path.join(dirpath, f)
            sp2 = os.path.join(dirpath, f"{m.group(1)}-sp2{m.group(2)}")
            if os.path.isfile(sp2):
                pairs.append((sp1, sp2))
    return pairs


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_pair(sp1_path: str | None, sp2_path: str | None,
                 aggressiveness: int = WEBRTC_AGGRESSIVENESS,
                 gap: float = MERGE_GAP_S,
                 backend: str = "auto") -> dict:
    results = {}
    for sp, path in (("sp1", sp1_path), ("sp2", sp2_path)):
        if path is None:
            results[sp] = {"error": "file not found"}
            continue
        try:
            segs = _vad_one(path, backend=backend)
            # honour caller-supplied gap (CLI override)
            if gap != MERGE_GAP_S:
                raw_pairs = [(s["start"], s["end"]) for s in segs]
                segs = merge_segments(raw_pairs, gap=gap)
            results[sp] = segs
        except Exception as e:
            results[sp] = {"error": str(e)}

    base = os.path.basename(sp1_path or sp2_path or "")
    base = re.sub(r"-sp[12](\.[^.]+)?$", "", base)

    # Duration: use the longer of the two files
    duration = None
    for path in (sp1_path, sp2_path):
        if path and os.path.isfile(path):
            try:
                with wave.open(path, "rb") as wf:
                    d = round(wf.getnframes() / wf.getframerate(), 3)
                if duration is None or d > duration:
                    duration = d
            except Exception:
                pass

    return {
        "base": base,
        "sp1_file": os.path.basename(sp1_path) if sp1_path else None,
        "sp2_file": os.path.basename(sp2_path) if sp2_path else None,
        "duration": duration,
        "speakers": results,
    }


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def serve():
    """
    Long-running worker for the backend (src/services/vad-runner.ts): one JSON request per
    line on stdin, {"id", "sp1", "sp2"}, answered by one line on stdout, {"id", "result"} or
    {"id", "error"}. stdout carries nothing else (logging goes to stderr). Exits on EOF,
    so the worker never outlives the backend that spawned it.
    """
    import sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            reply = {"id": req_id, "result": analyze_pair(req.get("sp1"), req.get("sp2"))}
        except Exception as e:
            reply = {"id": req_id, "error": str(e)}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def doctor() -> dict:
    """Prove the exact CPU runtime can infer; no audio files, network or writes."""
    spec = _runtime_spec()
    _ensure_silero_model()
    if np is None:
        raise VadRuntimeError('dependency_unavailable')
    try:
        packages = {item['name']: importlib.metadata.version(item['name']) for item in spec['packages']}
        import webrtcvad
        import onnxruntime  # dependency failure has a stable public code
    except (ImportError, OSError, importlib.metadata.PackageNotFoundError):
        raise VadRuntimeError('dependency_unavailable') from None
    if (not platform.python_version().startswith('3.11.')
            or any(packages[item['name']] != item['version'] for item in spec['packages'])):
        raise VadRuntimeError('invalid_configuration')
    session = _silero_session()
    options = session.get_session_options()
    if (session.get_providers() != ['CPUExecutionProvider']
            or options.intra_op_num_threads != 1 or options.inter_op_num_threads != 1):
        raise VadRuntimeError('invalid_configuration')
    state = np.zeros((2, 1, 128), dtype=np.float32)
    context = np.zeros((1, SILERO_CONTEXT_SAMPLES), dtype=np.float32)
    for _ in range(2):
        probability, state, context = _silero_step(session, np.zeros(SILERO_CHUNK_SAMPLES, dtype=np.float32), state, context)
        if probability >= SILERO_ONSET:
            raise VadRuntimeError('inference_failed')
    if webrtcvad.Vad(WEBRTC_AGGRESSIVENESS).is_speech(bytes(320), SILERO_SAMPLE_RATE):
        raise VadRuntimeError('inference_failed')
    return {'status': 'ok', 'engine': 'silero', 'model_version': spec['model']['version'],
            'model_sha256': spec['model']['sha256'], 'providers': ['CPUExecutionProvider'],
            'threads': 1, 'context_samples': SILERO_CONTEXT_SAMPLES, 'inference_frames': 2,
            'python_version': platform.python_version(), 'packages': packages}


def main():
    p = argparse.ArgumentParser(
        description="OpDesk call VAD analysis — pairs sp1/sp2 legs per call",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument("base", nargs="?",
                       help="Base path of a call (auto-appends -sp1.wav / -sp2.wav)")
    group.add_argument("--all", action="store_true",
                       help="Find and analyse all pairs under --dir")
    p.add_argument("--sp1", help="Explicit sp1 WAV file")
    p.add_argument("--sp2", help="Explicit sp2 WAV file")
    p.add_argument("--dir", default=RECORD_SINGLE_DIR,
                   help="Root directory for --all scan")
    p.add_argument("--backend", choices=["auto", "silero", "webrtc"], default="auto",
                   help="VAD backend: auto tries Silero then falls back to webrtcvad")
    p.add_argument("--aggressiveness", type=int, default=WEBRTC_AGGRESSIVENESS,
                   choices=[0, 1, 2, 3],
                   help="WebRTC VAD aggressiveness (0=lenient, 3=strict); ignored for Silero")
    p.add_argument("--gap", type=float, default=MERGE_GAP_S,
                   help="Max silence (s) to merge into one segment")
    p.add_argument("--pretty", action="store_true", help="Indent JSON output")
    p.add_argument("--serve", action="store_true",
                   help="Run as the backend's worker: JSON requests on stdin, results on stdout")
    p.add_argument('--doctor', action='store_true', help='Verify the pinned CPU model and both inference engines')
    args = p.parse_args()

    indent = 2 if args.pretty else None

    if args.doctor:
        try:
            result = doctor()
        except VadRuntimeError as error:
            result = {'status': 'error', 'engine': 'silero', 'error_code': error.code}
        except Exception:
            result = {'status': 'error', 'engine': 'silero', 'error_code': 'inference_failed'}
        print(json.dumps(result))
        return 0 if result['status'] == 'ok' else 1

    if args.serve:
        serve()

    elif args.sp1 or args.sp2:
        result = analyze_pair(args.sp1, args.sp2,
                              aggressiveness=args.aggressiveness,
                              gap=args.gap, backend=args.backend)
        print(json.dumps(result, indent=indent))

    elif args.all:
        pairs = find_pairs(args.dir)
        if not pairs:
            print(json.dumps({"error": f"No sp1/sp2 pairs found under {args.dir}"}))
            return
        out = [analyze_pair(s1, s2, aggressiveness=args.aggressiveness,
                            gap=args.gap, backend=args.backend)
               for s1, s2 in pairs]
        print(json.dumps(out, indent=indent))

    elif args.base:
        sp1, sp2 = pair_from_base(args.base)
        if not sp1 and not sp2:
            print(json.dumps({"error": f"No sp1/sp2 files found for base: {args.base}"}))
            return
        result = analyze_pair(sp1, sp2, aggressiveness=args.aggressiveness,
                              gap=args.gap, backend=args.backend)
        print(json.dumps(result, indent=indent))

    else:
        p.print_help()


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO))
    sys.exit(main())
