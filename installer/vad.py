"""Provision the isolated CPU VAD runtime; Python 3.6 compatible installer API.

The caller owns service/environment transactions. These functions never change
recording settings, PBX configuration, application data, or service state.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import urlsplit

PYTHON = '/usr/bin/python3.11'
PACKAGES = frozenset(('numpy', 'onnxruntime', 'webrtcvad-wheels', 'flatbuffers', 'packaging', 'protobuf'))
MARKER = '.myteldesk-vad.json'


def _digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _absolute(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or path == Path(path.anchor):
        raise ValueError('VAD requires a dedicated absolute path')
    return path


def _no_parent_links(path):
    path = _absolute(path)
    if any(item.is_symlink() for item in [path] + list(path.parents)):
        raise ValueError('VAD path contains a symlink')
    return path


def _verify_file(path, expected, label):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise RuntimeError('VAD ' + label + ' is missing or not a regular file')
    if _digest(path) != expected:
        raise RuntimeError('VAD ' + label + ' checksum mismatch')


def _service_user(user):
    if not isinstance(user, str) or user == 'root' or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', user):
        raise ValueError('VAD probe requires a non-root service account')
    return user


def _trusted_owner(path):
    info = Path(path).lstat()
    if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
        raise ValueError('VAD runtime must be root-owned and not group/world writable')


def _load_spec(bundle):
    source = _no_parent_links(Path(bundle) / 'backend/vad')
    manifest = source / 'runtime.json'
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 128 * 1024:
        raise ValueError('VAD dependency manifest is missing or invalid')
    raw = manifest.read_bytes()
    try:
        spec = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeError):
        raise ValueError('VAD dependency manifest is invalid')
    if (not isinstance(spec, dict) or spec.get('schema') != 1
            or spec.get('python_version') != '3.11' or spec.get('architecture') != 'x86_64'):
        raise ValueError('Unsupported VAD runtime platform')
    model = spec.get('model', {})
    if (not isinstance(model, dict) or model.get('filename') != 'silero_vad.onnx'
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', str(model.get('version', '')))
            or not re.fullmatch(r'[0-9a-f]{64}', str(model.get('sha256', '')))):
        raise ValueError('Invalid VAD model pin')
    packages = spec.get('packages')
    if not isinstance(packages, list) or not 3 <= len(packages) <= len(PACKAGES):
        raise ValueError('VAD dependency closure is invalid')
    names = set()
    for item in packages:
        if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
                or item['name'] not in PACKAGES or item['name'] in names):
            raise ValueError('VAD package is unsupported or duplicated')
        names.add(item['name'])
        if (not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*(?:\.post[0-9]+)?', str(item.get('version', '')))
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*\.whl', str(item.get('filename', '')))
                or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', '')))
                or type(item.get('bytes')) is not int or not 0 < item['bytes'] <= 100 * 1024 * 1024):
            raise ValueError('VAD wheel pin is invalid')
        url = urlsplit(str(item.get('url', '')))
        if (url.scheme != 'https' or url.netloc != 'files.pythonhosted.org'
                or not url.path.startswith('/packages/') or url.path.rsplit('/', 1)[-1] != item['filename']
                or url.query or url.fragment or any(c.isspace() for c in item['url'])):
            raise ValueError('VAD wheel URL is not an immutable PyPI file')
    if not {'numpy', 'onnxruntime', 'webrtcvad-wheels'}.issubset(names):
        raise ValueError('VAD primary dependencies are missing')
    expected = ''.join('{name}=={version} --hash=sha256:{sha256}\n'.format(**item)
                       for item in sorted(packages, key=lambda item: item['name']))
    requirements = source / 'requirements.txt'
    if requirements.is_symlink() or not requirements.is_file():
        raise ValueError('VAD locked requirements are missing')
    actual = ''.join(line.strip() + '\n' for line in requirements.read_text(encoding='utf-8').splitlines()
                     if line.strip() and not line.lstrip().startswith('#'))
    if actual != expected:
        raise ValueError('VAD requirements do not match the pinned manifest')
    model_file = source / 'models' / model['filename']
    _no_parent_links(model_file)
    _verify_file(model_file, model['sha256'], 'bundled model')
    script = source / 'call_vad_analysis.py'
    if script.is_symlink() or not script.is_file():
        raise ValueError('VAD worker script is missing or invalid')
    return source, spec, expected, hashlib.sha256(raw).hexdigest()


def _run(run, argv, **kwargs):
    result = run([str(value) for value in argv], **kwargs)
    if result.returncode:
        raise RuntimeError('VAD provisioning command failed: ' + Path(str(argv[0])).name)
    return result


def _check_tree(root, trusted=False):
    if trusted: _trusted_owner(root)
    for parent, directories, files in os.walk(str(root), followlinks=False):
        for name in directories + files:
            item = Path(parent) / name
            if trusted: _trusted_owner(item)
            if item.is_symlink():
                try:
                    item.resolve().relative_to(root.resolve())
                except ValueError:
                    raise ValueError('VAD runtime symlink escapes its generation')
            elif not (item.is_dir() or item.is_file()) or not (stat.S_ISDIR(item.stat().st_mode) or stat.S_ISREG(item.stat().st_mode)):
                raise ValueError('VAD runtime contains a special file')


def _permissions(root):
    _check_tree(root)
    for parent, directories, files in os.walk(str(root), followlinks=False):
        for item in [Path(parent)] + [Path(parent) / name for name in directories + files]:
            if item.is_symlink():
                continue
            mode = 0o755 if item.is_dir() or item.stat().st_mode & 0o111 else 0o644
            os.chown(str(item), 0, 0)
            item.chmod(mode)


def _write_marker(directory, fingerprint, status):
    value = {'owner': 'MytelDesk', 'schema': 1, 'lock_sha256': fingerprint, 'status': status}
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(directory),
                                     prefix='.vad-marker-', delete=False) as output:
        temporary = Path(output.name)
        json.dump(value, output, sort_keys=True)
        output.write('\n'); output.flush(); os.fsync(output.fileno())
    try:
        os.replace(str(temporary), str(directory / MARKER))
        (directory / MARKER).chmod(0o644)
    finally:
        if temporary.exists(): temporary.unlink()


def ensure_vad(bundle, runtime_root, run, fetch_verified, service_user='asterisk', offline=False):
    """Return secret-free VAD environment values after provision and user probe."""
    _service_user(service_user)
    runtime_root = _no_parent_links(runtime_root)
    source, spec, requirements, fingerprint = _load_spec(bundle)
    if runtime_root.exists():
        if not runtime_root.is_dir(): raise ValueError('VAD runtime root is not a directory')
        _trusted_owner(runtime_root)
    generation = runtime_root / ('py311-' + fingerprint[:24])
    python = generation / 'venv/bin/python'
    model = generation / 'models' / spec['model']['filename']
    environment = {'VAD_PYTHON': str(python), 'VAD_MODEL_PATH': str(model),
                   'VAD_MODEL_SHA256': spec['model']['sha256']}
    if generation.exists():
        _no_parent_links(generation)
        _check_tree(generation, trusted=True)
        marker_path = generation / MARKER
        if marker_path.is_symlink():
            raise ValueError('VAD ownership record must not be a symlink')
        try:
            marker = json.loads(marker_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise RuntimeError('Existing VAD generation has no valid ownership record')
        if (not isinstance(marker, dict) or marker.get('owner') != 'MytelDesk' or marker.get('schema') != 1
                or marker.get('lock_sha256') != fingerprint or marker.get('status') not in ('ready', 'building')):
            raise RuntimeError('Existing VAD generation belongs to another installation')
        if marker['status'] == 'ready':
            _verify_file(model, spec['model']['sha256'], 'installed model')
            doctor_vad(bundle, environment, run, service_user)
            return environment
    else:
        generation.mkdir(parents=True, mode=0o755)
        _write_marker(generation, fingerprint, 'building')
    try:
        available = run([PYTHON, '--version'], check=False, timeout=30)
    except OSError:
        available = None
    if available is None or available.returncode:
        if offline:
            raise RuntimeError('Offline VAD requires Python3.11 and python3.11-pip installed from the signed local RPM repository')
        try:
            _run(run, ['dnf', '-y', '--disablerepo=*', '--enablerepo=baseos', '--enablerepo=appstream',
                       '--setopt=timeout=30', '--setopt=retries=2',
                       'install', 'python3.11', 'python3.11-pip'], timeout=900)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            raise RuntimeError('VAD could not install parallel Python 3.11 from Rocky BaseOS/AppStream. '
                               'Check that these repositories are reachable and contain python3.11 and python3.11-pip.') from None
        available = _run(run, [PYTHON, '--version'], timeout=30)
    if not re.fullmatch(r'Python 3\.11\.[0-9]+', available.stdout.strip()):
        raise RuntimeError('VAD requires the parallel Python 3.11 interpreter')
    wheels = generation / 'wheels'; wheels.mkdir(exist_ok=True)
    for item in spec['packages']:
        target = wheels / item['filename']
        if target.is_symlink(): raise ValueError('VAD wheel symlink refused')
        if target.exists() and not target.is_file():
            raise ValueError('VAD wheel target is not a regular file')
        if not target.is_file() or _digest(target) != item['sha256']:
            if target.is_file():
                target.unlink()  # only a corrupt wheel inside this owned generation
            fetch_verified(item['url'], target, item['sha256'])
        _verify_file(target, item['sha256'], 'downloaded wheel')
        if target.stat().st_size != item['bytes']:
            raise RuntimeError('VAD downloaded wheel size mismatch')
    model.parent.mkdir(exist_ok=True)
    shutil.copyfile(str(source / 'models' / spec['model']['filename']), str(model))
    _verify_file(model, spec['model']['sha256'], 'installed model')
    locked = generation / 'requirements.txt'; locked.write_text(requirements, encoding='utf-8')
    clean_env = {'PATH': '/usr/bin:/bin', 'HOME': str(generation), 'PIP_CONFIG_FILE': os.devnull,
                 'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PYTHONNOUSERSITE': '1'}
    _run(run, [PYTHON, '-I', '-m', 'venv', '--copies', str(generation / 'venv')], env=clean_env, timeout=180)
    _run(run, [python, '-I', '-m', 'pip', 'install', '--no-index', '--find-links', wheels,
               '--require-hashes', '--only-binary=:all:', '--no-deps', '--no-cache-dir', '--no-compile',
               '--force-reinstall', '-r', locked], env=clean_env, timeout=600)
    _run(run, [python, '-I', '-m', 'pip', 'check'], env=clean_env, timeout=60)
    os.chown(str(runtime_root), 0, 0); runtime_root.chmod(0o755)
    _permissions(generation)
    doctor_vad(bundle, environment, run, service_user)
    _write_marker(generation, fingerprint, 'ready')
    return environment


def doctor_vad(bundle, environment, run, service_user='asterisk'):
    """Return a verified doctor success or raise on an unusable CPU runtime."""
    _service_user(service_user)
    source, spec, _requirements, _fingerprint = _load_spec(bundle)
    try:
        python = _absolute(environment['VAD_PYTHON'])
        model = _absolute(environment['VAD_MODEL_PATH'])
        expected = environment['VAD_MODEL_SHA256']
    except (KeyError, TypeError):
        raise ValueError('VAD environment is incomplete')
    if expected != spec['model']['sha256']:
        raise ValueError('VAD environment does not match the pinned model')
    _verify_file(model, expected, 'configured model')
    command = ['runuser', '-u', service_user, '--', 'env', '-i', 'PATH=/usr/bin:/bin',
               'HOME=/nonexistent', 'PYTHONDONTWRITEBYTECODE=1', 'OMP_NUM_THREADS=1',
               'OPENBLAS_NUM_THREADS=1', 'MKL_NUM_THREADS=1', 'VAD_MODEL_PATH=' + str(model),
               'VAD_MODEL_SHA256=' + expected, str(python), '-I', str(source / 'call_vad_analysis.py'), '--doctor']
    result = run(command, check=False, timeout=60)
    if result.returncode or len(result.stdout) > 32768:
        raise RuntimeError('VAD doctor failed for the service account')
    try:
        health = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise RuntimeError('VAD doctor returned invalid JSON')
    if (not isinstance(health, dict) or health.get('status') != 'ok' or health.get('engine') != 'silero'
            or health.get('model_sha256') != expected or health.get('model_version') != spec['model']['version']
            or health.get('providers') != ['CPUExecutionProvider'] or health.get('threads') != 1
            or health.get('context_samples') != 64 or type(health.get('inference_frames')) is not int
            or health['inference_frames'] < 2 or not str(health.get('python_version', '')).startswith('3.11.')
            or not isinstance(health.get('packages'), dict)
            or any(health['packages'].get(item['name']) != item['version'] for item in spec['packages'])):
        raise RuntimeError('VAD doctor did not prove the pinned CPU runtime is usable')
    return health
