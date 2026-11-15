# Automatic CPU voice activity analysis

The installer provisions VAD before publishing its application environment. It uses a parallel Python 3.11 interpreter, a private virtual environment, verified binary dependencies and a bundled Silero model. Provisioning does not enable recording, alter existing recording/VAD settings, change PBX configuration, or reload Asterisk. Existing recording controls still determine whether audio is recorded and analysed.

## Runtime and platform

The pinned dependency set targets CPython 3.11 on Linux x86_64 with glibc 2.28 or newer. Rocky 8.8+ provides parallel Python 3.11 packages; Rocky 9.2 introduced Python 3.11 as well. The helper installs only `python3.11` and `python3.11-pip` when the versioned interpreter is absent. It does not replace platform Python, change alternatives, install packages into system Python, or install GPU libraries. See the [RHEL 8.8 Python release notes](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/8/pdf/8.8_release_notes/Red_Hat_Enterprise_Linux-8-8.8_Release_Notes-en-US.pdf) and [Rocky 9.2 release notes](https://docs.rockylinux.org/releases/release_notes/9_2/).

The interpreter lives in `/opt/myteldesk/runtime/vad/py311-<manifest digest>/venv`. Each generation is created at its final path because [Python virtual environments are not movable](https://docs.python.org/3.11/library/venv.html). Runtime files are owned by root, readable by the service account and not writable by that account. Old generations are retained for application rollback. No global Python environment, PBX identity, SELinux policy or firewall setting is changed by this helper.

If Python 3.11 is absent, its single DNF install command enables only the existing Rocky repository IDs `baseos` and `appstream`, using `--disablerepo=* --enablerepo=baseos --enablerepo=appstream`. This avoids making an unrelated third-party repository's availability a prerequisite for installing Rocky's Python packages. It does not edit repository files, persist enable/disable settings, bypass package signatures or perform a global upgrade. DNF network timeout is 30 seconds with two retries, and the whole command has a 900-second limit. If the required Rocky repositories are unreachable or lack these packages, installation stops with a generic actionable error; existing application settings remain unpublished/unchanged. The existing interpreter path skips DNF entirely.

`backend/vad/runtime.json` records the exact wheel URLs, sizes and SHA-256 digests. The installer downloads those files, independently checks their digests and then runs pip against that local wheel directory with no index, no dependency resolution, binary-only packages and required hashes. `pip check` verifies the resulting dependency closure. This follows pip's [hash-checking installation guidance](https://pip.pypa.io/en/stable/topics/secure-installs/). Package installation may require network access; call analysis and health probes never download anything.

Only ONNX Runtime's CPU execution provider is selected. Its intra-op and inter-op thread pools are each limited to one, and execution is sequential; NumPy/BLAS thread environment variables are also bounded inside the worker process. These settings follow [ONNX Runtime's threading controls](https://onnxruntime.ai/docs/performance/tune-performance/threading.html) and do not change the PBX process or system-wide settings.

## Model and inference

The bundled model is Silero VAD v6.2.3 from commit `5cd7945676eb32225748052e2e6a0580e4686a08`, licensed under MIT. Its SHA-256 is `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`. The model and license are under `backend/vad/models`; provenance and dependency notices are listed in [vad-third-party.md](vad-third-party.md).

The adapter uses 512 samples per frame at 16 kHz and prepends the preceding 64 samples, as required by the [upstream Silero wrapper](https://github.com/snakers4/silero-vad/blob/v6.2.3/src/silero_vad/utils_vad.py). Both recurrent state and context start at zero for every speaker leg; neither is shared between calls. The model session can be reused by the existing worker. Existing resampling, hysteresis, merging and JSON recording-result formats remain in place.

The worker accepts only the bundled pin or an absolute `VAD_MODEL_PATH` accompanied by the matching `VAD_MODEL_SHA256`. A missing or mismatched model produces a clear failure and cannot silently report fallback analysis as healthy. Explicit WebRTC analysis remains available; the existing automatic fallback is retained for non-integrity Silero execution/dependency errors.

## Installer API

```python
environment = ensure_vad(bundle, runtime_root, run, fetch_verified,
                         service_user='asterisk')
health = doctor_vad(bundle, environment, run, service_user='asterisk')
```

`ensure_vad` returns exactly `VAD_PYTHON`, `VAD_MODEL_PATH` and `VAD_MODEL_SHA256`, after successful installation and an inference probe as the service user. The caller owns publication of these values, service management and application rollback. Existing `opdesk` deployments must pass that service identity instead of changing ownership to `asterisk`.

The `run(argv, **options)` callback returns a subprocess-like object with `returncode`, `stdout` and `stderr`, and must support `timeout`, `check` and `env`. `fetch_verified(url, destination, sha256)` downloads a wheel to the given path; the helper rechecks size and digest before pip. Neither callback receives user-supplied shell fragments. Invalid metadata, unmanaged runtime directories and corrupt ready generations are rejected without adopting or overwriting them.

A healthy identical generation is a read-only probe, without downloads or package installation. An interrupted generation with a matching installer ownership record can be resumed. Existing runtime directories and every generation entry must be root-owned and not writable by group or others; a marker alone cannot authorize adopting untrusted contents. Only a corrupt regular wheel inside such a trusted generation is removed before retrying its verified download. It is marked ready only after its service-user probe succeeds. Failed preparation leaves the caller's published environment untouched; the helper itself never writes that environment.

## Health contract

`<VAD_PYTHON> -I <release>/backend/vad/call_vad_analysis.py --doctor` emits one JSON object and exits. It checks the pinned model and package versions, CPU provider and thread limits, performs two real synthetic ONNX inference frames using the same context adapter, and invokes WebRTC on a silent frame. It reads no call recordings. A successful example is:

```json
{
  "status": "ok",
  "engine": "silero",
  "model_version": "6.2.3",
  "model_sha256": "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
  "providers": ["CPUExecutionProvider"],
  "threads": 1,
  "context_samples": 64,
  "inference_frames": 2,
  "python_version": "3.11.x",
  "packages": {"numpy": "2.4.6", "onnxruntime": "1.32.0", "webrtcvad-wheels": "2.0.14.post1", "flatbuffers": "25.12.19", "packaging": "26.3", "protobuf": "7.36.2"}
}
```

Success exits 0. Failure exits nonzero with `status: "error"`, `engine: "silero"` and one `error_code`: `model_missing`, `model_hash_mismatch`, `dependency_unavailable`, `invalid_configuration` or `inference_failed`. The JSON contains no filesystem paths, credentials or raw exception text. The installer invokes the probe with a clean, secret-free environment as the configured non-root service account, with a 60-second limit.

The application status endpoint should cache and deduplicate this probe rather than starting an ONNX process for every UI request. It should expose capability/error information only. A passing synthetic probe demonstrates that the pinned engines execute; it does not measure speech accuracy, prove recording is enabled, or replace real two-way audio and recording tests.

## Regression tests

`python3 -m unittest tests.test_vad_install` exercises provisioning using fake subprocess/download callbacks; it never changes a PBX. `python3.11 backend/vad/test_analysis.py` tests real NumPy arrays with isolated ONNX/WebRTC fakes, including context/state separation, padding/timestamps, model integrity, CPU bounds and the sanitized doctor protocol. The native symlink test requires POSIX support and is skipped when the Windows account lacks symlink permission. Actual inference must additionally be exercised with the installed runtime on supported Linux hosts.
