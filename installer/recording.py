"""Installer-owned native Issabel generation module; no vendor source changes."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

NAME = 'myteldeskrecording'
VERSION = '1.0.0'
FILES = ('functions.inc.php', 'module.xml')


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _real_path(path, missing=False):
    path = Path(path)
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)):
        raise RuntimeError('Non-canonical native module path')
    for parent in list(reversed(path.parents)) + [path]:
        if parent.is_symlink(): raise RuntimeError('Native module symlink refused')
        if not parent.exists():
            if missing and parent == path: break
            raise RuntimeError('Native module parent is missing')
    return path


def _result(run, argv):
    value = run(argv)
    try:
        if value.returncode != 0 or len(value.stdout) > 16384: raise ValueError()
        data = json.loads(value.stdout)
        if not isinstance(data, dict) or data.get('status') is not True: raise ValueError()
        return data
    except (TypeError, ValueError):
        raise RuntimeError('Native recording module operation did not provide strict completion evidence')


def _native(release, run, action):
    data = _result(run, ['/usr/bin/php', str(Path(release)/'installer/recording-module.php'), action])
    if set(data) != {'status','admin','module','reload_needed'} or type(data['reload_needed']) is not bool:
        raise RuntimeError('Native recording module metadata is incomplete')
    admin = _real_path(data['admin'])
    if not admin.is_dir(): raise RuntimeError('Native administration directory is unavailable')
    row = data['module']
    if row is not None and (not isinstance(row,dict) or set(row) != {'version','enabled'} or
            not isinstance(row['version'],str) or not re.match(r'^[0-9]+\.[0-9]+\.[0-9]+$',row['version']) or type(row['enabled']) is not bool):
        raise RuntimeError('Native module registration is malformed')
    return data


def apply_native(release, run):
    data = _result(run, ['/usr/bin/php', str(Path(release)/'backend/scripts/issabel-reload.php')])
    if (set(data) != {'status','num_errors','reload_needed'} or type(data['num_errors']) is not int or
            data['num_errors'] != 0 or data['reload_needed'] is not False):
        raise RuntimeError('Native Apply did not complete after recording module operation')


def _validate_record(record, directory):
    if (not isinstance(record,dict) or set(record) != {'schema','name','version','directory','files'} or
            type(record['schema']) is not int or record['schema'] != 1 or record['name'] != NAME or
            record['version'] != VERSION or record['directory'] != str(directory) or
            not isinstance(record['files'],dict) or set(record['files']) != set(FILES) or
            any(not isinstance(value,str) or not re.match(r'^[a-f0-9]{64}$',value) for value in record['files'].values())):
        raise RuntimeError('Recording module ownership receipt is invalid')


class ModulePlan:
    def __init__(self, release, run, previous=None, intent=None):
        self.release = Path(release); self.run = run; self.previous = previous
        info = _native(self.release,run,'inspect')
        self.admin = Path(info['admin'])
        self.directory = _real_path(self.admin/'modules'/NAME, missing=True)
        self.parents = {str(path):[path.stat().st_dev,path.stat().st_ino] for path in (self.admin,self.directory.parent)}
        self.started = False
        source = self.release/'backend/pbx-modules'/NAME
        contents = {}
        for name in FILES:
            path = _real_path(source/name)
            if not path.is_file(): raise RuntimeError('Recording module release payload is missing')
            contents[name] = path.read_bytes()
        self.contents = contents
        self.record = dict(schema=1,name=NAME,version=VERSION,directory=str(self.directory),
                           files={name:hashlib.sha256(value).hexdigest() for name,value in contents.items()})
        if previous is not None: _validate_record(previous,self.directory)
        if intent is not None:
            if (set(intent) != {'release','before','after','parents','started'} or intent['release'] != str(self.release) or
                    intent['after'] != self.record or intent['parents'] != self.parents or type(intent['started']) is not bool):
                raise RuntimeError('Recording module recovery intent does not match the immutable release')
            self.started = intent['started']
            self._check_files(partial=True)
            if info['module'] not in (None,dict(version=VERSION,enabled=True)):
                raise RuntimeError('Recording module registration changed during recovery')
        else:
            if info['reload_needed']: raise RuntimeError('Apply existing PBX changes before installing the recording module')
            if previous is None:
                if self.directory.exists() or info['module'] is not None:
                    raise RuntimeError('Existing native recording module has no installer ownership; nothing changed')
            else:
                self._check_files(expected=previous)
                if info['module'] != dict(version=VERSION,enabled=True):
                    raise RuntimeError('Owned recording module registration changed outside the installer')
        self.intent = dict(release=str(self.release),before=previous,after=self.record,parents=self.parents,started=self.started)

    def _guard_parents(self):
        for name, expected in self.parents.items():
            path = _real_path(name)
            info = path.stat()
            if not path.is_dir() or [info.st_dev,info.st_ino] != expected:
                raise RuntimeError('Native module parent identity changed after preflight')

    def _inspect(self, action='inspect'):
        self._guard_parents()
        value = _native(self.release,self.run,action)
        if value['admin'] != str(self.admin): raise RuntimeError('Native administration root changed after preflight')
        self._guard_parents()
        return value

    def _check_files(self, expected=None, partial=False):
        self._guard_parents()
        expected = expected or self.record
        path = _real_path(self.directory,missing=partial)
        if not path.exists():
            if partial and self.previous is None: return
            raise RuntimeError('Owned recording module directory disappeared')
        info = path.stat()
        if not path.is_dir() or (os.name == 'posix' and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022)):
            raise RuntimeError('Owned recording module directory permissions changed')
        names = {item.name for item in path.iterdir()}
        if (names - set(FILES)) or (not partial and names != set(FILES)):
            raise RuntimeError('Native recording module contains unexpected files')
        for name in names:
            leaf = _real_path(path/name)
            info = leaf.stat()
            allowed = {expected['files'][name]}
            if partial and self.previous: allowed.add(self.previous['files'][name])
            if (not leaf.is_file() or (os.name == 'posix' and (info.st_uid != os.geteuid() or info.st_mode & 0o022)) or _hash(leaf) not in allowed):
                raise RuntimeError('Owned recording module changed outside the installer')

    def activate(self, transaction, on_begin=None):
        # Revalidate the read-only plan immediately before its first write.
        info = self._inspect()
        expected = None if self.previous is None else dict(version=VERSION,enabled=True)
        if info['reload_needed'] or info['module'] != expected:
            raise RuntimeError('Native module registration changed after preflight')
        if self.previous is None:
            if self.directory.exists(): raise RuntimeError('Native module directory appeared after preflight')
        else:
            self._check_files(expected=self.previous)
        self.started = True; self.intent['started'] = True
        if on_begin: on_begin()
        self._guard_parents()
        if self.previous is None:
            self.directory.mkdir(mode=0o755); self.directory.chmod(0o755)
        for name in FILES:
            self._guard_parents()
            _real_path(self.directory)
            transaction.write(self.directory/name,self.contents[name],0o644)
        self._check_files()
        info = self._inspect()
        if info['module'] != dict(version=VERSION,enabled=True):
            info = self._inspect('install')
        if info['module'] != dict(version=VERSION,enabled=True):
            raise RuntimeError('Native recording module registration did not complete')

    def before_restore(self):
        if not self.started: raise RuntimeError('Untouched module preflight cannot trigger native rollback')
        self._check_files(partial=True)
        info = self._inspect()
        if info['module'] not in (None,dict(version=VERSION,enabled=True)):
            raise RuntimeError('Native recording module state changed; automatic restore stopped')
        if self.previous is None and info['module'] is not None:
            if self._inspect('uninstall')['module'] is not None:
                raise RuntimeError('Owned recording module was not unregistered')

    def after_restore(self):
        if self.previous is None:
            if self.directory.exists(): self.directory.rmdir()
        else:
            self._check_files(expected=self.previous)
            if self._inspect('install')['module'] != dict(version=VERSION,enabled=True):
                raise RuntimeError('Previous owned recording module was not restored')

    def remove(self):
        self._check_files()
        info = self._inspect()
        if info['reload_needed'] or info['module'] != dict(version=VERSION,enabled=True):
            raise RuntimeError('Native module state changed before removal')
        if self._inspect('uninstall')['module'] is not None:
            raise RuntimeError('Owned native module was not unregistered')
        for name in FILES: (self.directory/name).unlink()
        self.directory.rmdir()

    def doctor(self):
        self._check_files()
        if self._inspect()['module'] != dict(version=VERSION,enabled=True):
            raise RuntimeError('Native recording module is not installed and enabled')
        loaded = self.run(['asterisk','-rx','dialplan show sub-record-check'])
        needle = 'opdesk-record,s,1(${ARG1},${ARG2})'
        if loaded.returncode != 0 or loaded.stdout.count(needle) != 5:
            raise RuntimeError('Loaded native recording call paths are incomplete; old hook bodies alone are insufficient')

    def untouched(self):
        if self.started: raise RuntimeError('Recording module transaction already started')
        expected = None if self.previous is None else dict(version=VERSION,enabled=True)
        if self._inspect()['module'] != expected: raise RuntimeError('Native module changed before an untouched recovery')
        if self.previous is None:
            if self.directory.exists(): raise RuntimeError('Native module directory appeared before untouched recovery')
        else: self._check_files(expected=self.previous)


def prepare(release, run, previous=None):
    return ModulePlan(release,run,previous)


def from_intent(intent, run):
    if not isinstance(intent,dict) or set(intent) != {'release','before','after','parents','started'} or not isinstance(intent['release'],str):
        raise RuntimeError('Recording module recovery intent is invalid')
    return ModulePlan(intent['release'],run,intent['before'],intent=intent)


def for_restore(release,run,current,before):
    plan = prepare(release,run,current)
    if before is not None: _validate_record(before,plan.directory)
    plan.previous = before; plan.started = True
    plan.intent.update(before=before,started=True)
    return plan
