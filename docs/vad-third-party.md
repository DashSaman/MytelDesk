# VAD dependency provenance and notices

The model is redistributed with the source and release bundle. Python wheels are downloaded by the installer from the immutable file URLs recorded in `backend/vad/runtime.json`; their complete SHA-256 pins also appear in `backend/vad/requirements.txt`. No source distribution, arbitrary package index, torch or GPU package is used. The pinned set below was selected on 2026-11-15 for CPython 3.11 and Rocky's x86_64/glibc compatibility.

| Component | Pinned version | Primary source | License/notice location |
| --- | --- | --- | --- |
| Silero VAD model | 6.2.3 | [Release](https://github.com/snakers4/silero-vad/releases/tag/v6.2.3) | `backend/vad/models/LICENSE` (MIT) |
| NumPy | 2.4.6 | [PyPI](https://pypi.org/project/numpy/2.4.6/) | `docs/vad-licenses/numpy/` (BSD-3-Clause and bundled component notices) |
| ONNX Runtime CPU | 1.32.0 | [PyPI](https://pypi.org/project/onnxruntime/1.32.0/) | `docs/vad-licenses/onnxruntime/` (MIT and third-party notices) |
| webrtcvad-wheels | 2.0.14.post1 | [PyPI](https://pypi.org/project/webrtcvad-wheels/2.0.14.post1/) | `docs/vad-licenses/webrtcvad-wheels/` (MIT and WebRTC notices) |
| FlatBuffers | 25.12.19 | [PyPI](https://pypi.org/project/flatbuffers/25.12.19/) | `docs/vad-licenses/flatbuffers/LICENSE` (Apache-2.0) |
| Packaging | 26.3 | [PyPI](https://pypi.org/project/packaging/26.3/) | `docs/vad-licenses/packaging/` (Apache-2.0 or BSD-2-Clause) |
| Protobuf | 7.36.2 | [PyPI](https://pypi.org/project/protobuf/7.36.2/) | `docs/vad-licenses/protobuf/` (BSD-3-Clause) |

The NumPy selection deliberately uses its Python 3.11-compatible release line; a newer release requiring Python 3.12 is not substituted. ONNX Runtime's default dependency closure consists of NumPy, FlatBuffers, Packaging and Protobuf; optional symbolic/quantization extras are not requested. All wheel versions and artifacts are frozen, including transitive dependencies.

The Silero binary was retrieved from the [immutable upstream commit](https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx). It is 2,327,524 bytes, with SHA-256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`. Its Git blob ID is `80c5592ef1f4c9ede3e357bbd02eb863358a6a9d`; both identity checks were performed before vendoring. The model license is copied from the same commit. The small NumPy inference adapter follows the upstream context/state contract; the upstream license is retained beside the model.

Wheel license/notice files were copied without editing from the verified wheel bytes. FlatBuffers 25.12.19's wheel does not include its license text, so its license was obtained from [the matching upstream release commit](https://github.com/google/flatbuffers/blob/7e163021e59cca4f8e1e35a7c828b5c6b7915953/LICENSE); the license Git blob is `d645695673349e3947e8e5ae42332d0ac3164cd7`. NumPy and ONNX Runtime include notices for their bundled native components; retain the complete notice directories when redistributing this runtime.

Updating any dependency or model requires choosing an explicit compatible artifact, checking its digest and license notices, updating the manifest and requirements together, and repeating both isolated regression tests and actual CPU inference on the supported operating systems. Runtime downloads never follow an unpinned `latest` URL.
