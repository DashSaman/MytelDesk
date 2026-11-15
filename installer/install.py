"""MytelDesk standalone installer. Python 3.6+, standard library only.

Code is immutable and root-owned; the service runs as Issabel's asterisk user.
The installer owns one database/account, AMI include, Apache prefix and unit.
It does not restart Asterisk, rewrite trunks, or replace Issabel's runtime.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.request import urlopen
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host import detect_platform, read_key_values, render_apache, render_systemd, validate_public_url
from vad import ensure_vad, doctor_vad

ROOT = Path('/opt/myteldesk')
CONFIG = Path('/etc/myteldesk')
DATA = Path('/var/lib/myteldesk')
UNIT = Path('/etc/systemd/system/myteldesk.service')
PROXY = Path('/etc/httpd/conf.d/myteldesk.conf')
AMI = Path('/etc/asterisk/manager_myteldesk.conf')
ENV = CONFIG / 'myteldesk.env'
STATE = CONFIG / 'installation.json'


def digest(path):
    h = hashlib.sha256()
    with open(str(path), 'rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def relative_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('Invalid archive path')
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or name.startswith('./'):
        raise ValueError('Unsafe archive path')
    return p


def verify_bundle(bundle):
    bundle = Path(bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 1 or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', manifest.get('version', '')):
        raise ValueError('Unsupported release manifest')
    if not re.fullmatch(r'[0-9a-f]{40}', manifest.get('source_tree', '')):
        raise ValueError('Invalid source tree')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('Empty release manifest')
    actual = set()
    for item in bundle.rglob('*'):
        if item.is_symlink():
            raise ValueError('Release symlink refused')
        if item.is_file():
            actual.add(item.relative_to(bundle).as_posix())
    if actual != set(files) | {'manifest.json'}:
        raise ValueError('Release contains missing or unlisted files')
    for name, info in files.items():
        relative_name(name)
        item = bundle / name
        if not isinstance(info, dict) or item.stat().st_size != info.get('bytes') or digest(item) != info.get('sha256'):
            raise ValueError('Release checksum mismatch')
    if (bundle / 'SOURCE_TREE').read_text(encoding='ascii').strip() != manifest['source_tree']:
        raise ValueError('Source tree mismatch')
    return manifest


def safe_extract(archive, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Extraction destination already exists')
    with tarfile.open(str(archive), 'r:*') as source:
        members = source.getmembers()
        if len(members) > 100000 or sum(max(0, m.size) for m in members) > 2 * 1024 ** 3:
            raise ValueError('Archive exceeds release limits')
        names = set()
        for member in members:
            name = str(relative_name(member.name.rstrip('/')))
            if name in names or not (member.isdir() or member.isfile()):
                raise ValueError('Archive duplicate, link or special file refused')
            names.add(name)
        destination.mkdir(mode=0o700)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as inp, open(str(target), 'xb') as out:
                    shutil.copyfileobj(inp, out)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)


def include_config(text, filename):
    if not re.fullmatch(r'[A-Za-z0-9_-]+\.conf', filename):
        raise ValueError('Invalid include filename')
    pattern = r'^\s*#(?:try)?include\s+["\']?(?:/etc/asterisk/)?' + re.escape(filename) + r'["\']?\s*(?:;.*)?$'
    if re.search(pattern, text, re.M):
        return text
    return text + ('' if not text or text.endswith('\n') else '\n') + '#include ' + filename + '\n'


def render_env(values):
    lines = []
    for key, value in sorted(values.items()):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key) or not re.fullmatch(r'[A-Za-z0-9_./:@,+%=?\[\]-]*', str(value)):
            raise ValueError('Environment value requires unsupported quoting')
        lines.append(key + '=' + str(value))
    return '\n'.join(lines) + '\n'


def database_sql(password):
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', password):
        raise ValueError('Invalid generated database secret')
    lines = ['CREATE DATABASE IF NOT EXISTS `myteldesk` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;']
    for host in ('localhost', '127.0.0.1'):
        who = "'myteldesk'@'{}'".format(host)
        lines.extend(["CREATE USER IF NOT EXISTS {} IDENTIFIED BY '{}';".format(who, password),
                      'GRANT ALL PRIVILEGES ON `myteldesk`.* TO ' + who + ';',
                      'GRANT SELECT, INSERT, UPDATE, DELETE ON `asterisk`.* TO ' + who + ';',
                      'GRANT SELECT ON `asteriskcdrdb`.* TO ' + who + ';'])
    return '\n'.join(lines) + '\n'


def run(argv, input_text=None, env=None, timeout=180, check=True):
    # Capture output because upstream commands can echo SQL or credentials on error.
    result = subprocess.run(argv, input=input_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, env=env, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('{} failed (exit {}); no credentials printed'.format(Path(argv[0]).name, result.returncode))
    return result


def atomic(path, data, mode=0o600, uid=0, gid=0):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('Refusing to replace a configuration symlink')
    fd, tmp = tempfile.mkstemp(prefix='.myteldesk-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data.encode('utf-8') if isinstance(data, str) else data)
            out.flush(); os.fsync(out.fileno())
        os.chmod(tmp, mode); os.chown(tmp, uid, gid)
        os.replace(tmp, str(path))
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def read_state():
    if not STATE.exists(): return None
    if STATE.is_symlink() or STATE.stat().st_uid != 0 or STATE.stat().st_mode & 0o077:
        raise RuntimeError('Installation ownership file must be root-private')
    state = json.loads(STATE.read_text(encoding='utf-8'))
    if state.get('owner') != 'MytelDesk' or state.get('schema') != 1:
        raise RuntimeError('Unrecognized installation state')
    return state


def save_state(state):
    atomic(STATE, json.dumps(state, indent=2) + '\n')


class Transaction:
    def __init__(self):
        parent = CONFIG / 'backups'; parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d-%H%M%S-'), dir=str(parent)))
        self.entries = []

    def write(self, path, data, mode=0o644, uid=0, gid=0):
        path = Path(path)
        if path.is_symlink(): raise ValueError('Configuration symlink refused')
        entry = {'path': str(path), 'before': None}
        if path.exists():
            info = path.stat(); backup = self.directory / str(len(self.entries))
            shutil.copyfile(str(path), str(backup)); backup.chmod(0o600)
            entry.update(before=str(backup), mode=stat.S_IMODE(info.st_mode), uid=info.st_uid, gid=info.st_gid)
        content = data.encode('utf-8') if isinstance(data, str) else data
        entry['after'] = hashlib.sha256(content).hexdigest()
        self.entries.append(entry)
        # Journal before replacing the target, so termination can be recovered.
        atomic(self.directory / 'journal.json', json.dumps(self.entries, indent=2))
        atomic(path, content, mode, uid, gid)

    def rollback(self):
        restore_entries(self.entries)


def restore_entries(entries):
    planned = []
    for item in reversed(entries):
        path = Path(item['path'])
        if path.is_symlink():
            raise RuntimeError('Configuration changed since install; automatic restore stopped')
        if not path.exists() and not item['before']: continue
        if not path.is_file():
            raise RuntimeError('Configuration changed since install; automatic restore stopped')
        current = digest(path)
        if current != item['after'] and (not item['before'] or current != digest(Path(item['before']))):
            raise RuntimeError('Configuration changed since install; automatic restore stopped')
        planned.append((path, item))
    # Validate the complete transaction before changing any entry. A shared-file
    # conflict must not leave a half-restored service or remove unrelated files.
    for path, item in planned:
        if item['before']:
            if digest(path) == digest(Path(item['before'])):
                os.chmod(str(path), item['mode']); os.chown(str(path), item['uid'], item['gid'])
            else:
                atomic(path, Path(item['before']).read_bytes(), item['mode'], item['uid'], item['gid'])
        else:
            path.unlink()


def restore_current(target):
    link = ROOT / 'current'
    if link.exists() and not link.is_symlink(): raise RuntimeError('Current release pointer is not installer-owned')
    temp = ROOT / '.current-restore'
    if temp.is_symlink(): temp.unlink()
    if target:
        temp.symlink_to(target); os.replace(str(temp), str(link))
    elif link.is_symlink(): link.unlink()


def recover_pending(state):
    directory = Path(state['pending_journal']).parent
    journal = directory / 'journal.json'
    previous = json.loads((directory / 'previous-state.json').read_text())
    run(['systemctl', 'stop', 'myteldesk'], check=False)
    restore_entries(json.loads(journal.read_text()) if journal.exists() else [])
    restore_current(state.get('pending_current'))
    save_state(previous)
    run(['systemctl', 'daemon-reload'])
    run(['asterisk', '-rx', 'manager reload'], check=False)
    run(['httpd', '-t']); run(['systemctl', 'reload', 'httpd'])
    if previous.get('status') == 'ready': run(['systemctl', 'start', 'myteldesk']); wait_health()
    else: run(['systemctl', 'disable', 'myteldesk'], check=False)
    print('Recovered the interrupted configuration transaction; application data and secrets retained.')
    return previous


def root_owned(path):
    path = Path(path)
    if path.is_symlink() or (path.exists() and (path.stat().st_uid != 0 or path.stat().st_mode & 0o022)):
        raise RuntimeError('Installer code/config path must be root-owned and not group/world writable')


def check_managed_files(state):
    if not state.get('owned_hashes'):
        raise RuntimeError('Managed configuration inventory is missing')
    for name, expected in state['owned_hashes'].items():
        path = Path(name)
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise RuntimeError('Managed configuration changed; reconcile shared include/service/proxy changes before upgrading')


def mysql(sql, password, database=None):
    environ = dict(os.environ); environ['MYSQL_PWD'] = password
    argv = ['mysql', '--batch', '--skip-column-names', '--user=root']
    if database: argv.append('--database=' + database)
    return run(argv, sql, env=environ, timeout=120).stdout.strip()


def root_database_password():
    for file in ('/etc/issabel.conf', '/etc/amportal.conf'):
        if Path(file).is_file():
            values = read_key_values(file)
            for key in ('mysqlrootpwd', 'MYSQL_ROOT_PASSWORD', 'AMPDBPASS'):
                if values.get(key):
                    try:
                        mysql('SELECT 1;', values[key])
                        return values[key]
                    except RuntimeError: pass
    try:
        mysql('SELECT 1;', '')
        return ''
    except RuntimeError:
        raise RuntimeError('Cannot authenticate local MariaDB root using Issabel configuration')


def fetch_verified(url, path, expected):
    if not url.startswith('https://') or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Invalid runtime download')
    h = hashlib.sha256()
    with urlopen(url, timeout=60) as response, open(str(path), 'xb') as out:
        while True:
            block = response.read(1024 * 1024)
            if not block: break
            h.update(block); out.write(block)
    if h.hexdigest() != expected:
        path.unlink()
        raise RuntimeError('Runtime checksum mismatch')


def ensure_runtime(bundle):
    pin = json.loads((bundle / 'installer/runtime.json').read_text())
    name = 'node-' + pin['version'] + '-linux-x64'
    runtime = ROOT / 'runtime' / name
    for path in (ROOT, runtime.parent, runtime, runtime / 'bin', runtime / 'bin/node'):
        root_owned(path)
    if not (runtime / 'bin/node').is_file():
        runtime.parent.mkdir(parents=True, exist_ok=True)
        runtime.parent.chmod(0o755)
        with tempfile.TemporaryDirectory(prefix='myteldesk-node-') as tmp:
            archive = Path(tmp) / 'node.tar.xz'
            fetch_verified('https://nodejs.org/dist/' + pin['version'] + '/' + name + '.tar.xz', archive, pin['sha256'])
            # Official Node includes symlinks. Use tar after checksum verification;
            # extract into private temp then move only the expected top-level dir.
            run(['tar', '--no-same-owner', '--no-same-permissions', '-xJf', str(archive), '-C', tmp])
            unpacked = Path(tmp) / name
            for item in [unpacked] + list(unpacked.rglob('*')):
                if item.is_symlink(): continue
                os.chown(str(item), 0, 0)
                item.chmod(0o755 if item.is_dir() or item.stat().st_mode & 0o111 else 0o644)
            shutil.move(str(Path(tmp) / name), str(runtime))
    version = run([str(runtime / 'bin/node'), '--version']).stdout.strip()
    if version != pin['version']:
        raise RuntimeError('Installed private Node version differs from release pin')
    return runtime / 'bin/node'


def progress(number, message):
    print('[{}/9] {}'.format(number, message), flush=True)


def ami_enabled(output):
    return bool(re.search(r'^\s*(?:Manager \(AMI\)|Enabled):\s*Yes\s*$', output, re.I | re.M))


def default_url():
    # No outbound network probe: prefer the address used by the SSH session.
    candidate = os.environ.get('SSH_CONNECTION', '').split()
    if len(candidate) == 4: address = candidate[2]
    else:
        addresses = run(['hostname', '-I']).stdout.split()
        address = next((ip for ip in addresses if ':' not in ip), socket.getfqdn())
    return validate_public_url('https://' + address)


def verify_app(password, release):
    tables = re.findall(r'^\s*CREATE TABLE IF NOT EXISTS\s+`?([A-Za-z0-9_]+)', (release / 'backend/schema.sql').read_text(), re.M)
    if not tables: raise RuntimeError('Application schema has no recognized table definitions')
    present = set(mysql('SHOW TABLES;', password, 'myteldesk').splitlines())
    if set(tables) - present:
        raise RuntimeError('Application database migration incomplete')
    if int(mysql('SELECT COUNT(*) FROM users WHERE is_active=1 AND role=\'admin\';', password, 'myteldesk') or 0) < 1:
        raise RuntimeError('Application has no active administrator')


def backup_database(password, directory):
    environ = dict(os.environ); environ['MYSQL_PWD'] = password
    # Only the owned application schema. Call history/PBX databases are untouched.
    result = run(['mysqldump', '--user=root', '--single-transaction', '--routines', '--events', 'myteldesk'], env=environ)
    atomic(directory / 'myteldesk.sql', result.stdout)


def wait_health(timeout=300):
    started = time.time()
    deadline = started + timeout
    last_progress = started
    while time.time() < deadline:
        try:
            with urlopen('http://127.0.0.1:8765/health', timeout=3) as response:
                result = json.loads(response.read().decode('utf-8'))
            if result.get('status') == 'ok' and result.get('ami_connected') is True:
                return result
        except Exception: pass
        now = time.time()
        if now - last_progress >= 15:
            print('Waiting for backend/database startup and local AMI: {}s elapsed (limit {}s)'.format(int(now-started), timeout), flush=True)
            last_progress = now
        time.sleep(2)
    raise RuntimeError('Backend/AMI did not become ready; inspect journalctl -u myteldesk')


def verify_proxy(public_url):
    # This exception to certificate validation is restricted to local Issabel's
    # existing self-signed HTTPS listener. Downloads always use verified HTTPS.
    authority = urlsplit(public_url).netloc
    for route, expected in (('/MytellDesk/health', 200), ('/MytellDesk/', 200)):
        connection = http.client.HTTPSConnection('127.0.0.1', 443, timeout=10, context=ssl._create_unverified_context())
        try:
            connection.request('GET', route, headers={'Host': authority})
            response = connection.getresponse(); body = response.read()
            if response.status != expected:
                raise RuntimeError('Issabel HTTPS product proxy returned an unexpected status')
            if route.endswith('/health'):
                if json.loads(body.decode('utf-8')).get('ami_connected') is not True:
                    raise RuntimeError('HTTPS product health response was not the backend')
            else:
                if b'/MytellDesk/assets/' not in body:
                    raise RuntimeError('HTTPS product page does not reference release assets')
                assets = re.findall(rb'(?:src|href)="(/MytellDesk/assets/[^"<>]+)"', body)
                if not assets: raise RuntimeError('Product assets missing')
        finally: connection.close()
    connection = http.client.HTTPSConnection('127.0.0.1', 443, timeout=10, context=ssl._create_unverified_context())
    try:
        connection.request('GET', assets[0].decode('ascii'), headers={'Host': authority})
        response = connection.getresponse()
        if response.status != 200 or not response.read(64): raise RuntimeError('HTTPS product asset unavailable')
    finally: connection.close()


def install(bundle, public_url=None):
    progress(1, 'Verifying release and Issabel prerequisites')
    manifest = verify_bundle(bundle)
    platform = detect_platform(Path('/etc/os-release').read_text(), run(['asterisk', '-V']).stdout, os.uname().machine)
    if not Path('/etc/issabel.conf').exists() or not Path('/etc/asterisk').is_dir():
        raise RuntimeError('This installer requires an existing Issabel PBX')
    import pwd, grp
    account = pwd.getpwnam('asterisk'); group = grp.getgrnam('asterisk')
    for name in ('httpd', 'mariadb', 'asterisk'):
        run(['systemctl', 'is-active', '--quiet', name])
    run(['httpd', '-t'])
    modules = run(['httpd', '-M']).stdout
    for name in ('proxy_module', 'proxy_http_module', 'proxy_wstunnel_module'):
        if name not in modules: raise RuntimeError('Apache module missing: ' + name)
    ami_settings = run(['asterisk', '-rx', 'manager show settings']).stdout
    if not ami_enabled(ami_settings):
        raise RuntimeError('Enable local Asterisk AMI first in Issabel; its global listener is not changed automatically')
    state = read_state()
    if state and state.get('pending_journal'):
        state = recover_pending(state)
    if state and state.get('status') == 'ready':
        check_managed_files(state)
    if not state:
        for path in (Path('/opt/opdesk'), Path('/opt/OpDesk'), ROOT, CONFIG, DATA, UNIT, PROXY, AMI, ENV, Path('/usr/local/sbin/myteldesk')):
            if path.exists(): raise RuntimeError('Existing Desk installation/configuration detected; no settings changed. See migration documentation.')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', 8765)) == 0: raise RuntimeError('Backend port 8765 is already occupied')
        for conf in Path('/etc/httpd/conf.d').glob('*.conf'):
            if '/MytellDesk' in conf.read_text(errors='replace'): raise RuntimeError('Product URL already owned by another Apache configuration')
    password = root_database_password()
    if not state:
        collisions = mysql("SELECT User FROM mysql.user WHERE User='myteldesk'; SHOW DATABASES LIKE 'myteldesk';", password)
        if collisions: raise RuntimeError('Database/account myteldesk already exists without installer ownership; refusing to reset it')
    for directory, mode in ((ROOT, 0o755), (CONFIG, 0o700), (DATA, 0o750)):
        if directory != DATA: root_owned(directory)
        directory.mkdir(parents=True, exist_ok=True); directory.chmod(mode)
    os.chown(str(DATA), account.pw_uid, group.gr_gid)
    public_url = validate_public_url(public_url) if public_url else (state.get('public_url') if state else default_url())
    if state and public_url != state.get('public_url'):
        raise RuntimeError('Changing the public URL requires an explicit configuration migration; existing CORS/SIP settings were preserved')
    if state and state.get('status') not in ('prepared', 'ready'):
        raise RuntimeError('Installation state requires manual recovery; retained identities will not be reset')
    progress(2, 'Installing checksum-pinned private Node runtime')
    node = ensure_runtime(bundle)
    release = ROOT / 'releases' / (manifest['version'] + '-' + manifest['source_tree'][:12])
    release.parent.mkdir(exist_ok=True)
    release.parent.chmod(0o755)
    root_owned(release.parent); root_owned(release)
    if release.exists(): verify_bundle(release)
    else:
        shutil.copytree(str(bundle), str(release))
        for item in release.rglob('*'):
            if item.is_file(): item.chmod(0o755 if item.stat().st_mode & 0o111 else 0o644)
            elif item.is_dir(): item.chmod(0o755)
        release.chmod(0o755)
    progress(3, 'Provisioning and verifying automatic CPU voice analysis')
    vad_environment = ensure_vad(release, ROOT / 'runtime/vad', run, fetch_verified, service_user='asterisk')
    if set(vad_environment) != {'VAD_PYTHON', 'VAD_MODEL_PATH', 'VAD_MODEL_SHA256'}:
        raise RuntimeError('VAD provisioner returned incomplete environment configuration')
    if state and state.get('version') == manifest['version'] and state.get('tree') == manifest['source_tree'] and state.get('status') == 'ready':
        existing_values = read_key_values(ENV)
        if all(existing_values.get(key) == value for key, value in vad_environment.items()):
            doctor_vad(release, existing_values, run, service_user='asterisk')
            wait_health(20); verify_app(password, release); verify_proxy(public_url)
            print('Already installed and healthy. URL: ' + public_url)
            return
    progress(4, 'Preparing dedicated secrets and database grants')
    admin_password = None
    if state:
        values = read_key_values(ENV)
    else:
        random_secret = lambda: base64.urlsafe_b64encode(os.urandom(36)).decode('ascii').rstrip('=')
        admin_password = random_secret()
        values = dict(OS='Linux', PBX='Issabel', DB_HOST='127.0.0.1', DB_PORT='3306', DB_USER='myteldesk',
                      DB_PASSWORD=random_secret(), DB_NAME='asterisk', DB_CDR='asteriskcdrdb', DB_OpDesk='myteldesk',
                      AMI_HOST='127.0.0.1', AMI_PORT='5038', AMI_USERNAME='myteldesk', AMI_SECRET=random_secret(), AMI_CONTEXT='ext-local',
                      PORT='8765', OPDESK_BIND_HOST='127.0.0.1', OPDESK_BASE_PATH='/MytellDesk/',
                      OPDESK_DOMAIN=public_url.split('/')[2], CORS_ALLOWED_ORIGINS=public_url.split('/MytellDesk/')[0],
                      JWT_SECRET=random_secret(), OPDESK_BRIDGE_PW=random_secret(), MYTELDESK_DIRECT_PBX='1',
                      OPDESK_ADMIN_INIT_HASH_FILE=str(DATA / 'bootstrap-admin.hash'),
                      SETTINGS_ENCRYPTION_KEY_FILE=str(DATA / 'secrets/settings.key'),
                      ASTERISK_RECORDING_ROOT_DIR='/var/spool/asterisk/monitor')
        values.update(vad_environment)
        atomic(ENV, render_env(values), 0o640, 0, group.gr_gid)
        # EnvironmentFile is read by systemd, directory remains root-private.
        state = dict(owner='MytelDesk', schema=1, status='prepared', public_url=public_url, platform=platform)
        hash_script = "const fs=require('fs'),b=require('bcryptjs');process.stdout.write(b.hashSync(fs.readFileSync(0,'utf8'),12));"
        admin_hash = run([str(node), '-e', hash_script], admin_password, env=dict(os.environ, NODE_PATH=str(release / 'backend/node_modules'))).stdout
        atomic(DATA / 'bootstrap-admin.hash', admin_hash + '\n', 0o600, account.pw_uid, group.gr_gid)
        secret_dir = DATA / 'secrets'; secret_dir.mkdir(mode=0o750, exist_ok=True)
        os.chown(str(secret_dir), account.pw_uid, group.gr_gid)
        atomic(secret_dir / 'settings.key', base64.urlsafe_b64encode(os.urandom(32)) + b'\n', 0o600, account.pw_uid, group.gr_gid)
        atomic(CONFIG / 'initial-admin.txt', 'URL: ' + public_url + '\nUsername: admin\nPassword: ' + admin_password + '\n')
        save_state(state)
    mysql(database_sql(values['DB_PASSWORD']), password)
    transaction = Transaction()
    previous = dict(state)
    if previous.get('status') == 'ready': backup_database(password, transaction.directory)
    was_active = run(['systemctl', 'is-active', '--quiet', 'myteldesk'], check=False).returncode == 0
    previous_current = os.readlink(str(ROOT / 'current')) if (ROOT / 'current').is_symlink() else None
    atomic(transaction.directory / 'previous-state.json', json.dumps(previous))
    state.update(pending_journal=str(transaction.directory / 'journal.json'), pending_current=previous_current)
    save_state(state)
    try:
        values.update(vad_environment)
        if read_key_values(ENV) != values:
            transaction.write(ENV, render_env(values), 0o640, 0, group.gr_gid)
        progress(5, 'Adding isolated loopback-only AMI account')
        manager = Path('/etc/asterisk/manager.conf')
        main = manager.read_text()
        custom = Path('/etc/asterisk/manager_custom.conf')
        include_parent = custom if re.search(r'^\s*#(?:try)?include\s+["\']?(?:/etc/asterisk/)?manager_custom\.conf["\']?\s*$', main, re.M) else manager
        parent_text = include_parent.read_text() if include_parent.exists() else ''
        # An unrelated account with our name is never overwritten.
        for conf in Path('/etc/asterisk').glob('manager*.conf'):
            if conf != AMI and re.search(r'^\s*\[myteldesk\]\s*$', conf.read_text(errors='replace'), re.M | re.I):
                raise RuntimeError('AMI account name collides with existing PBX configuration')
        ami = '[myteldesk]\nsecret=' + values['AMI_SECRET'] + '\ndeny=0.0.0.0/0.0.0.0\npermit=127.0.0.1/255.255.255.255\n'
        ami += 'read=system,call,log,verbose,command,agent,user,config,dtmf,reporting,cdr,dialplan\nwrite=system,call,agent,user,config,command,originate\nwritetimeout=5000\n'
        transaction.write(AMI, ami, 0o640, account.pw_uid, group.gr_gid)
        oldinfo = include_parent.stat() if include_parent.exists() else None
        transaction.write(include_parent, include_config(parent_text, AMI.name), stat.S_IMODE(oldinfo.st_mode) if oldinfo else 0o644,
                          oldinfo.st_uid if oldinfo else account.pw_uid, oldinfo.st_gid if oldinfo else group.gr_gid)
        run(['asterisk', '-rx', 'manager reload'])
        progress(6, 'Configuring unprivileged service and scoped Apache proxy')
        # Keep system tools available without changing system-wide Node or shell profiles.
        unit = render_systemd(str(node), str(release), str(ENV))
        unit = unit.replace('Environment=NODE_ENV=production\n', 'Environment=NODE_ENV=production\nEnvironment=PATH=' + str(node.parent) + ':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin\n')
        transaction.write(UNIT, unit)
        transaction.write(PROXY, render_apache())
        wrapper = '#!/bin/sh\nexec ' + sys.executable + ' -B /opt/myteldesk/current/installer/install.py "$@"\n'
        transaction.write('/usr/local/sbin/myteldesk', wrapper, 0o755)
        run(['httpd', '-t'])
        # SELinux state and firewall policy stay owned by the PBX administrator.
        # Default Issabel policy permits proxying; enforcing hosts must authorize it.
        run(['systemctl', 'daemon-reload'])
        progress(7, 'Starting MytelDesk and checking database/AMI readiness')
        run(['systemctl', 'enable', 'myteldesk'])
        run(['systemctl', 'restart', 'myteldesk'])
        wait_health(); verify_app(password, release)
        doctor_vad(release, values, run, service_user='asterisk')
        progress(8, 'Activating Apache configuration')
        run(['systemctl', 'reload', 'httpd'])
        verify_proxy(public_url)
        link = ROOT / '.current-next'
        if link.is_symlink(): link.unlink()
        link.symlink_to(release); os.replace(str(link), str(ROOT / 'current'))
        if previous.get('status') == 'ready':
            state['previous'] = {k: previous[k] for k in ('version', 'tree', 'release', 'node') if k in previous}
        state.update(status='ready', version=manifest['version'], tree=manifest['source_tree'], release=str(release), node=str(node), public_url=public_url,
                     last_journal=str(transaction.directory / 'journal.json'))
        if not state.get('uninstall_journal'):
            state['uninstall_journal'] = state['last_journal']
        state['owned_hashes'] = {entry['path']: entry['after'] for entry in transaction.entries}
        state.pop('pending_journal', None); state.pop('pending_current', None)
        save_state(state)
    except BaseException:
        run(['systemctl', 'stop', 'myteldesk'], check=False)
        transaction.rollback()
        restore_current(previous_current)
        save_state(previous)
        run(['systemctl', 'daemon-reload'], check=False)
        run(['asterisk', '-rx', 'manager reload'], check=False)
        run(['systemctl', 'reload', 'httpd'], check=False)
        if was_active: run(['systemctl', 'start', 'myteldesk'], check=False)
        else: run(['systemctl', 'disable', 'myteldesk'], check=False)
        raise
    progress(9, 'Installation verified: backend, database, Asterisk AMI and CPU voice analysis')
    print('URL: ' + public_url)
    if admin_password:
        print('Administrator credentials: /etc/myteldesk/initial-admin.txt (root only). Read once and store securely.')
    print('No Asterisk restart or test call was performed. Use myteldesk doctor for diagnostics.')


def doctor():
    state = read_state()
    if not state or state.get('status') != 'ready': raise RuntimeError('No completed MytelDesk installation found')
    run(['systemctl', 'is-active', '--quiet', 'myteldesk'])
    wait_health(15)
    verify_app(root_database_password(), Path(state['release']))
    run(['httpd', '-t'])
    verify_proxy(state['public_url'])
    doctor_vad(Path(state['release']), read_key_values(ENV), run, service_user='asterisk')
    print('OK: service, local health, AMI, application schema, administrator and CPU voice analysis. ' + state['public_url'])


def uninstall():
    state = read_state()
    if not state: raise RuntimeError('No installer ownership record found')
    if state.get('status') != 'ready': raise RuntimeError('Installation is incomplete; inspect the saved journal first')
    # Only generated own files are removed. Shared include edits are restored from
    # the first journal only if nobody has edited them since installation.
    entries = json.loads(Path(state['uninstall_journal']).read_text())
    # Updates replace generated files. Validate against the latest successful
    # install, then restore their original pre-install versions.
    for entry in entries:
        path = Path(entry['path'])
        if not path.is_file() or path.is_symlink() or digest(path) != state['owned_hashes'].get(str(path)):
            raise RuntimeError('Installed configuration edited outside installer; automatic removal stopped')
        entry['after'] = digest(path)
    run(['systemctl', 'stop', 'myteldesk'], check=False)
    restore_entries(entries)
    run(['systemctl', 'disable', 'myteldesk'], check=False)
    run(['systemctl', 'daemon-reload'])
    run(['httpd', '-t']); run(['systemctl', 'reload', 'httpd'])
    run(['asterisk', '-rx', 'manager reload'])
    state['status'] = 'removed'; save_state(state)
    print('Service/proxy/AMI include removed. Database, accounts, credentials, data and backups retained.')


def rollback():
    state = read_state()
    if not state or state.get('status') != 'ready' or not state.get('previous') or not state.get('last_journal'):
        raise RuntimeError('No previous installed release is available')
    entries = json.loads(Path(state['last_journal']).read_text())
    # Validate everything before stopping the running service.
    for item in entries:
        path = Path(item['path'])
        if path.is_symlink() or not path.is_file() or digest(path) != item['after']:
            raise RuntimeError('Configuration edited since upgrade; automatic rollback stopped')
    run(['systemctl', 'stop', 'myteldesk'])
    restore_entries(entries)
    run(['systemctl', 'daemon-reload']); run(['httpd', '-t'])
    run(['systemctl', 'start', 'myteldesk']); wait_health()
    run(['systemctl', 'reload', 'httpd'])
    state.update(state.pop('previous')); state['last_journal'] = None
    state['owned_hashes'] = {item['path']: digest(Path(item['path'])) for item in entries if Path(item['path']).exists()}
    link = ROOT / '.current-next'
    if link.is_symlink(): link.unlink()
    link.symlink_to(state['release']); os.replace(str(link), str(ROOT / 'current'))
    save_state(state)
    print('Previous code/config restored and healthy. Database data retained; private pre-upgrade SQL backup is available in /etc/myteldesk/backups.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('install', 'doctor', 'rollback', 'uninstall'), default='install')
    parser.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--public-url', help='HTTPS address of this Issabel server')
    args = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0: raise RuntimeError('Run this installer as root on the Issabel server')
    def terminated(_signum, _frame): raise KeyboardInterrupt('Installer interrupted; configuration transaction will be restored')
    signal.signal(signal.SIGTERM, terminated)
    import fcntl
    with open('/run/myteldesk-install.lock', 'w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: raise RuntimeError('Another MytelDesk installer is running')
        if args.action == 'doctor': doctor()
        elif args.action == 'rollback': rollback()
        elif args.action == 'uninstall': uninstall()
        else: install(args.bundle.resolve(), args.public_url)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('MytelDesk installation stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
