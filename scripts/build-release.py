#!/usr/bin/env python3
"""Build a source-complete, host-independent MytelDesk release from a clean Git tree.

Requires Python 3.9+, Git, Node and npm, with development dependencies already
installed in backend/ and frontend/. No host configuration is read or imported.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile


ROOT_FILES = frozenset({
    '.dockerignore', '.env.example', '.gitattributes', '.gitignore',
    'Dockerfile', 'LICENSE', 'UPSTREAM.md', 'README.md', 'README.en.md',
    'VERSION', 'install.sh', 'start.sh', 'docker-compose.yml',
    'THIRD_PARTY_NOTICES.md',
})
SOURCE_DIRS = frozenset({'docs', 'installer', 'configs', 'scripts', 'tests', '.github'})
BACKEND_DIRS = frozenset({'src', 'test', 'vad', 'scripts', 'pbx-modules'})
BACKEND_FILES = frozenset({
    'package.json', 'package-lock.json', 'schema.sql', 'tsconfig.json',
    'tsconfig.build.json', 'jest.config.js', '.env.example',
})
FRONTEND_FILES = frozenset({
    'package.json', 'package-lock.json', 'index.html', 'tsconfig.json',
    'tsconfig.node.json', 'vite.config.ts', 'eslint.config.js', '.stylelintrc.json',
})
PRIVATE_DIRS = frozenset({
    '.git', '.venv', '__pycache__', 'secrets', 'secret', 'cert', 'certs',
    'certificates', 'backups', 'backup', 'recordings', 'uploads', 'logs',
})
PRIVATE_SUFFIXES = ('.pem', '.key', '.p8', '.p12', '.pfx', '.sqlite', '.sqlite3',
                    '.db', '.db-shm', '.db-wal', '.dump', '.log', '.pyc', '.node')
RUNTIME_EXCLUDE = frozenset({'.bin', 'test', 'tests', '__tests__', 'example',
                              'examples', 'fixture', 'fixtures', '.github'})
TOKEN = re.compile(rb'(?:github_pat_[A-Za-z0-9_]{40,}|gh[pousr]_[A-Za-z0-9]{30,})')
PRIVATE_PEM = re.compile(
    rb'^-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{40,}', re.M)


def validate_relative(relative):
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or '\x00' in relative or ':' in relative or relative.startswith('/')):
        raise ValueError('Invalid package-relative path: '+repr(relative))
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ValueError('Non-canonical package-relative path: '+repr(relative))
    return relative


def validate_version(version):
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?', version):
        raise ValueError('Version must be an unprefixed semantic version, e.g. 1.0.0')
    return version


def _beneath(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_output(output, repo_root):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output must be a new directory: '+str(output))
    if _beneath(output.resolve(), Path(repo_root).resolve()):
        raise ValueError('Output must be outside the source repository')
    return output


def _private_path(relative):
    parts = PurePosixPath(relative).parts
    lowered = [part.lower() for part in parts]
    name = lowered[-1]
    return (any(part in PRIVATE_DIRS for part in lowered)
            or (name.startswith('.env') and name != '.env.example')
            or name == '.admin_init_hash' or name.startswith('credentials')
            or name.startswith('installer-state') or '.bak' in name
            or name.endswith(PRIVATE_SUFFIXES)
            or (name.endswith('.sql') and relative != 'backend/schema.sql'))


def select_source_paths(root, tracked):
    """Intentional source allowlist; a tracked private file is a hard error."""
    selected = []
    for relative in tracked:
        validate_relative(relative)
        if _private_path(relative) or 'node_modules' in PurePosixPath(relative).parts:
            raise ValueError('Private or generated file is tracked: '+relative)
        parts = relative.split('/')
        accepted = relative in ROOT_FILES
        if len(parts) > 1 and parts[0] in SOURCE_DIRS:
            accepted = True
        if len(parts) > 1 and parts[0] == 'backend':
            accepted = ((len(parts) == 2 and parts[1] in BACKEND_FILES)
                        or (len(parts) > 2 and parts[1] in BACKEND_DIRS))
        if len(parts) > 1 and parts[0] == 'frontend':
            accepted = ((len(parts) == 2 and parts[1] in FRONTEND_FILES)
                        or (len(parts) > 2 and parts[1] in ('src', 'public')))
        if relative == 'nginx/opdesk.conf.example':
            accepted = True
        if accepted:
            selected.append(relative)
    return sorted(set(selected))


def _regular_file(root, relative):
    validate_relative(relative)
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Symlink root is not permitted: '+str(root))
    current = root
    for part in relative.split('/'):
        current = current/part
        if current.is_symlink():
            raise ValueError('Symlinks are not permitted in release input: '+relative)
    if not stat.S_ISREG(current.stat().st_mode):
        raise ValueError('Only regular files can be released: '+relative)
    return current


def _check_content(relative, content):
    if TOKEN.search(content) or PRIVATE_PEM.search(content):
        raise ValueError('Secret material detected; refusing release: '+relative)


def _write(stage, relative, content):
    validate_relative(relative)
    _check_content(relative, content)
    destination = Path(stage)/relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)


def stage_sources(source_root, stage_root, tracked_paths, blobs=None):
    for relative in select_source_paths(source_root, tracked_paths):
        source = _regular_file(source_root, relative)
        _write(stage_root, relative, blobs[relative] if blobs is not None else source.read_bytes())


def _walk_regular(root, excluded=frozenset()):
    """Walk without following symlinks and fail on unsupported input types."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Expected a real directory: '+str(root))
    for parent, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in excluded)
        for name in directories:
            if (Path(parent)/name).is_symlink():
                raise ValueError('Symlink directory in release input: '+str(Path(parent)/name))
        for name in sorted(names):
            if name in excluded:
                continue
            path = Path(parent)/name
            relative = path.relative_to(root).as_posix()
            yield relative, _regular_file(root, relative)


def stage_runtime_modules(source, destination):
    for relative, path in _walk_regular(source, RUNTIME_EXCLUDE):
        if relative == '.package-lock.json':
            continue
        if _private_path(relative):
            raise ValueError('Unexpected private/native file in runtime dependency: '+relative)
        _write(destination, relative, path.read_bytes())


def _package_directories(modules):
    modules = Path(modules)
    if modules.is_symlink():
        raise ValueError('Symlink node_modules directory: '+str(modules))
    for entry in sorted(modules.iterdir()):
        if entry.name.startswith('.'):
            continue
        if entry.is_symlink():
            raise ValueError('Symlink package directory: '+str(entry))
        if entry.is_dir() and entry.name.startswith('@'):
            packages = sorted(entry.iterdir())
        else:
            packages = [entry]
        for package in packages:
            if package.is_symlink():
                raise ValueError('Symlink package directory: '+str(package))
            if not package.is_dir() or not (package/'package.json').is_file():
                continue
            yield package
            if (package/'node_modules').is_dir():
                yield from _package_directories(package/'node_modules')


def collect_notices(module_roots, destination):
    """Retain license/notice text as supplied, with README fallback and an index."""
    destination = Path(destination)
    records = []
    license_name = re.compile(r'^(?:licen[sc]e|copying|notice|copyright|thirdpartynoticetext)(?:[._-].*)?$', re.I)
    for section, root in sorted(module_roots.items()):
        validate_relative(section)
        root = Path(root)
        for package in _package_directories(root):
            relative = package.relative_to(root).as_posix()
            metadata_path = _regular_file(root, relative+'/package.json')
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            licenses = []
            for entry in sorted(package.iterdir()):
                if entry.is_file() and license_name.fullmatch(entry.name):
                    licenses.append(entry)
                elif entry.name.lower() in ('licenses', 'license') and entry.is_dir():
                    licenses.extend(path for _, path in _walk_regular(entry))
            fallback = False
            if not licenses:
                licenses = [entry for entry in sorted(package.iterdir())
                            if entry.is_file() and re.fullmatch(r'readme(?:\..*)?', entry.name, re.I)]
                fallback = True
            copied = []
            for path in licenses:
                notice_relative = section+'/'+relative+'/'+path.relative_to(package).as_posix()
                _regular_file(root, relative+'/'+path.relative_to(package).as_posix())
                _write(destination, notice_relative, path.read_bytes())
                copied.append(notice_relative)
            # Keep the original author/repository/license declaration, including
            # older packages whose permission statement lives only in metadata.
            metadata_relative = section+'/'+relative+'/package.json'
            _write(destination, metadata_relative, metadata_path.read_bytes())
            records.append({'scope':section, 'path':relative, 'name':metadata.get('name', relative),
                            'version':metadata.get('version', ''), 'license':metadata.get('license'),
                            'files':copied, 'readme_fallback':fallback})
    _write(destination, 'index.json', (json.dumps({'schema':1,'packages':records}, ensure_ascii=False,
                                                indent=2, sort_keys=True)+'\n').encode())
    return records


def make_manifest(stage_root, version, source_tree):
    validate_version(version)
    if not re.fullmatch('[0-9a-f]{40}', source_tree):
        raise ValueError('SOURCE_TREE must be a SHA-1 Git tree object')
    files = {}
    for relative, path in _walk_regular(stage_root):
        validate_relative(relative)
        if relative == 'manifest.json':
            continue
        if _private_path(relative):
            raise ValueError('Private/generated material in package: '+relative)
        content = path.read_bytes()
        _check_content(relative, content)
        files[relative] = {'sha256':hashlib.sha256(content).hexdigest(), 'bytes':len(content)}
    return {'schema':1, 'version':version, 'source_tree':source_tree, 'files':files}


def write_archive(stage_root, archive_path, version, epoch):
    validate_version(version)
    if not isinstance(epoch, int) or not 0 <= epoch <= 4294967295:
        raise ValueError('Archive epoch is outside the gzip timestamp range')
    with Path(archive_path).open('xb') as raw:
        with gzip.GzipFile(filename='', mode='wb', compresslevel=9, fileobj=raw, mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for relative, path in _walk_regular(stage_root):
                    validate_relative(relative)
                    if _private_path(relative):
                        raise ValueError('Private/generated material in archive: '+relative)
                    content = path.read_bytes()
                    _check_content(relative, content)
                    info = tarfile.TarInfo('myteldesk-'+version+'/'+relative)
                    info.size = len(content)
                    info.mtime = epoch
                    info.uid = info.gid = 0
                    info.uname = info.gname = ''
                    info.mode = 0o755 if relative.endswith(('.sh', '.py')) else 0o644
                    archive.addfile(info, io.BytesIO(content))


def _git(repo, *arguments):
    return subprocess.run(['git', '-C', str(repo), *arguments], check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout


def _clean_tree(repo):
    status = _git(repo, 'status', '--porcelain=v1', '--untracked-files=all')
    if status:
        raise ValueError('Commit or remove source changes before releasing; Git tree is not clean')
    return _git(repo, 'rev-parse', 'HEAD^{tree}').decode().strip()


def _git_sources(repo):
    records = {}
    for item in _git(repo, 'ls-tree', '-rz', 'HEAD').split(b'\x00'):
        if not item:
            continue
        metadata, raw_path = item.split(b'\t', 1)
        mode, kind, oid = metadata.decode('ascii').split()
        path = raw_path.decode('utf-8')
        validate_relative(path)
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('Non-regular tracked Git object: '+path)
        records[path] = oid
    selected = select_source_paths(repo, records)
    result = subprocess.run(['git', '-C', str(repo), 'cat-file', '--batch'], check=True,
                            input=''.join(records[path]+'\n' for path in selected).encode('ascii'),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    stream = io.BytesIO(result)
    blobs = {}
    for path in selected:
        header = stream.readline().split()
        if len(header) != 3 or header[1] != b'blob':
            raise ValueError('Cannot read Git source blob: '+path)
        size = int(header[2])
        blobs[path] = stream.read(size)
        if len(blobs[path]) != size or stream.read(1) != b'\n':
            raise ValueError('Truncated Git source blob: '+path)
    return selected, blobs


def _run(command, cwd):
    print('Running '+ ' '.join(str(item) for item in command), flush=True)
    environment = {name:value for name, value in os.environ.items()
                   if not name.upper().startswith('VITE_')
                   and name.upper() not in ('NODE_OPTIONS', 'NODE_PATH')}
    environment['NODE_ENV'] = 'production'
    subprocess.run(command, cwd=cwd, check=True, env=environment)


def _stage_dist(repo, stage, relative):
    for name, path in _walk_regular(repo/relative):
        if _private_path(name):
            raise ValueError('Private material in compiled output: '+relative+'/'+name)
        _write(stage, relative+'/'+name, path.read_bytes())


def prepare_build_tree(repo, build, selected, blobs):
    """Compile Git bytes in isolation; never load local env/certs or stale output."""
    stage_sources(repo, build, selected, blobs)
    for section in ('backend', 'frontend'):
        modules = Path(repo)/section/'node_modules'
        if modules.is_symlink() or not modules.is_dir():
            raise ValueError('Development dependencies must be a real directory: '+str(modules))
        # Copies are intentionally independent: a build tool cannot mutate the
        # developer install via a hardlink. .bin links remain local build tooling.
        shutil.copytree(modules, Path(build)/section/'node_modules', symlinks=True)


def build_release(repo, output, version):
    repo = Path(repo).resolve()
    version = validate_version(version)
    output = validate_output(output, repo)
    source_tree = _clean_tree(repo)
    commit = _git(repo, 'rev-parse', 'HEAD').decode().strip()
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', _git(repo, 'show', '-s', '--format=%ct', 'HEAD').decode().strip()))
    selected, blobs = _git_sources(repo)
    if blobs.get('VERSION', b'').decode().strip() != version:
        raise ValueError('Tracked VERSION must match --version')
    required = {'LICENSE', 'UPSTREAM.md', 'README.md', 'install.sh', 'installer/install.py',
                'installer/host.py', 'installer/runtime.json', 'frontend/public/fonts/OFL.txt',
                'backend/vad/call_vad_analysis.py', 'backend/vad/requirements.txt'}
    missing = required-set(selected)
    if missing:
        raise ValueError('Required corresponding source is absent: '+', '.join(sorted(missing)))
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise ValueError('npm is required to build the release')
    for section in ('backend', 'frontend'):
        if not (repo/section/'node_modules').is_dir():
            raise ValueError('Run npm ci in '+section+' before building')
    # A clean temporary runtime install avoids importing the developer dependency tree.
    with tempfile.TemporaryDirectory(prefix='myteldesk-release-') as temporary:
        temp = Path(temporary)
        build = temp/'build'
        build.mkdir()
        prepare_build_tree(repo, build, selected, blobs)
        for section in ('backend', 'frontend'):
            _run([npm, 'run', 'build'], build/section)
        stage = temp/'package'
        stage.mkdir()
        stage_sources(repo, stage, selected, blobs)
        runtime = temp/'runtime'
        runtime.mkdir()
        for name in ('package.json', 'package-lock.json'):
            (runtime/name).write_bytes(blobs['backend/'+name])
        _run([npm, 'ci', '--omit=dev', '--omit=optional', '--ignore-scripts', '--no-audit', '--no-fund'], runtime)
        stage_runtime_modules(runtime/'node_modules', stage/'backend/node_modules')
        _stage_dist(build, stage, 'backend/dist')
        _stage_dist(build, stage, 'frontend/dist')
        records = collect_notices({'backend':runtime/'node_modules',
                                   'frontend':build/'frontend/node_modules'}, stage/'THIRD_PARTY_LICENSES')
        font_license = blobs['frontend/public/fonts/OFL.txt']
        _write(stage, 'THIRD_PARTY_LICENSES/fonts/Vazirmatn/OFL.txt', font_license)
        _write(stage, 'THIRD_PARTY_NOTICES.md', (
            '# Third-party notices\n\n'
            'MytelDesk is a GPLv3 derivative of OpDesk; see LICENSE and UPSTREAM.md.\n\n'
            'Dependencies retain their own licenses. Full installed package license/notice texts '
            '(or a README fallback where no standalone text was supplied) and metadata are in '
            'THIRD_PARTY_LICENSES/. Backend runtime dependencies include web-push under MPL-2.0; '
            'its original JavaScript source is included in backend/node_modules/web-push/. '
            'Vazirmatn fonts retain the SIL Open Font License 1.1 in '
            'frontend/public/fonts/OFL.txt and THIRD_PARTY_LICENSES/fonts/Vazirmatn/OFL.txt.\n\n'
            'Supplemental upstream license texts and preserved permission evidence for '
            'older packages with incomplete npm notices are retained in docs/licenses/.\n\n'
            'The dependency inventory is generated from the exact backend runtime installation '
            'and frontend build installation used for this artifact.\n').encode())
        _write(stage, 'SOURCE_TREE', (source_tree+'\n').encode())
        _write(stage, 'VERSION', (version+'\n').encode())
        # npm/build tools must not alter a source input, even if ignored by Git status.
        if _clean_tree(repo) != source_tree or _git(repo, 'rev-parse', 'HEAD').decode().strip() != commit:
            raise ValueError('Source tree changed while the release was being built')
        _, final_blobs = _git_sources(repo)
        if final_blobs != blobs:
            raise ValueError('Source blobs changed while the release was being built')
        manifest = make_manifest(stage, version, source_tree)
        _write(stage, 'manifest.json', (json.dumps(manifest, indent=2, sort_keys=True)+'\n').encode())
        output.mkdir(parents=True, exist_ok=False)
        archive = output/('myteldesk-'+version+'.tar.gz')
        write_archive(stage, archive, version, epoch)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (output/'SHA256SUMS').write_bytes((digest+'  '+archive.name+'\n').encode('ascii'))
        (output/'VERSION').write_bytes((version+'\n').encode('ascii'))
        (output/'release.json').write_bytes((json.dumps({
            'schema':1, 'version':version, 'source_tree':source_tree, 'source_commit':commit,
            'source_date_epoch':epoch, 'archive':archive.name, 'sha256':digest,
            'files':len(manifest['files']), 'dependency_packages':len(records),
        }, indent=2, sort_keys=True)+'\n').encode('utf-8'))
    print('Built '+str(archive)+'\nSHA256 '+digest, flush=True)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory outside this repository')
    arguments = parser.parse_args()
    try:
        build_release(Path(__file__).resolve().parents[1], arguments.output, arguments.version)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print('Release build failed: '+str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
