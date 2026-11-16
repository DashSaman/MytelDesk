"""Publication boundary contracts; fixtures never execute npm or touch a PBX."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]


def load_builder():
    path = REPO / 'scripts/build-release.py'
    if not path.is_file():
        raise AssertionError('Standalone release builder is absent; publication boundary is not implemented')
    spec = importlib.util.spec_from_file_location('myteldesk_release_test', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, data='fixture\n'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'repo'
        self.stage = self.root / 'package'
        self.source.mkdir()
        self.stage.mkdir()

    def test_relative_paths_reject_traversal_and_platform_escapes(self):
        builder = load_builder()
        for path in ('', '.', '..', '../secret', '/etc/passwd', 'a/../secret',
                     'a//secret', 'a\\secret', 'C:/secret', 'a\x00secret'):
            with self.subTest(path=repr(path)), self.assertRaises(ValueError):
                builder.validate_relative(path)
        self.assertEqual(builder.validate_relative('backend/src/main.ts'), 'backend/src/main.ts')

    def test_untracked_runtime_files_never_enter_corresponding_source(self):
        builder = load_builder()
        files = ('backend/src/main.ts', 'backend/schema.sql', 'frontend/src/App.tsx',
                 'frontend/public/fonts/OFL.txt', 'backend/vad/call_vad_analysis.py',
                 'installer/install.py', 'installer/host.py', 'README.md', '.env.example')
        for path in files:
            write(self.source/path)
        write(self.source/'backend/.env', 'production secret')
        write(self.source/'frontend/dist/old.js', 'untracked stale build')
        write(self.source/'docs/customer-backup.txt', 'untracked customer data')
        builder.stage_sources(self.source, self.stage, files)
        self.assertEqual(sorted(p.relative_to(self.stage).as_posix() for p in self.stage.rglob('*') if p.is_file()), sorted(files))

    def test_tracked_private_material_fails_instead_of_silently_shipping(self):
        builder = load_builder()
        for path in ('backend/.env', '.env.production', 'backend/.admin_init_hash',
                     'cert/server.key', 'secrets/client.pem', 'backend/data.sqlite',
                     'backups/host.sql', 'recordings/customer.wav', 'logs/runtime.log'):
            with self.subTest(path=path):
                write(self.source/path)
                with self.assertRaises(ValueError):
                    builder.select_source_paths(self.source, [path])

    def test_native_recording_module_payload_is_staged_from_git(self):
        builder = load_builder()
        files = {
            'backend/pbx-modules/myteldeskrecording/functions.inc.php': b'<?php // fixed native module fixture\n',
            'backend/pbx-modules/myteldeskrecording/module.xml': b'<module><rawname>myteldeskrecording</rawname></module>\n',
        }
        for name in files:
            write(self.source/name, b'local bytes are not authoritative')
        write(self.source/'backend/pbx-modules/foreign-untracked/module.xml', b'untracked')
        builder.stage_sources(self.source, self.stage, files, blobs=files)
        actual = {p.relative_to(self.stage).as_posix(): p.read_bytes()
                  for p in self.stage.rglob('*') if p.is_file()}
        self.assertEqual(actual, files)

    def test_unapproved_root_content_is_not_part_of_package(self):
        builder = load_builder()
        for name in ('README.md', 'random-host-export.txt'):
            write(self.source/name)
        self.assertEqual(builder.select_source_paths(self.source, ['README.md', 'random-host-export.txt']), ['README.md'])

    def test_source_symlinks_cannot_escape_into_release(self):
        builder = load_builder()
        write(self.root/'outside.ts', 'secret')
        (self.source/'backend/src').mkdir(parents=True)
        try:
            (self.source/'backend/src/main.ts').symlink_to(self.root/'outside.ts')
        except OSError as error:
            self.skipTest('Symlink privilege unavailable: '+str(error))
        with self.assertRaises(ValueError):
            builder.stage_sources(self.source, self.stage, ['backend/src/main.ts'])

    def test_parent_symlink_is_also_rejected(self):
        builder = load_builder()
        write(self.root/'outside/main.ts')
        (self.source/'backend').mkdir()
        try:
            (self.source/'backend/src').symlink_to(self.root/'outside', target_is_directory=True)
        except OSError as error:
            self.skipTest('Symlink privilege unavailable: '+str(error))
        with self.assertRaises(ValueError):
            builder.stage_sources(self.source, self.stage, ['backend/src/main.ts'])

    def test_runtime_bin_and_test_fixtures_are_excluded_but_licenses_remain(self):
        builder = load_builder()
        modules = self.root/'node_modules'
        for name in ('pkg/index.js', 'pkg/package.json', 'pkg/LICENSE', '.bin/helper',
                     'pkg/test/client.key', 'pkg/examples/.env'):
            write(modules/name)
        builder.stage_runtime_modules(modules, self.stage/'backend/node_modules')
        copied = sorted(p.relative_to(self.stage).as_posix() for p in self.stage.rglob('*') if p.is_file())
        self.assertEqual(copied, ['backend/node_modules/pkg/LICENSE', 'backend/node_modules/pkg/index.js', 'backend/node_modules/pkg/package.json'])

    def test_unexpected_runtime_private_key_fails(self):
        builder = load_builder()
        modules = self.root/'node_modules'
        write(modules/'pkg/index.js')
        write(modules/'pkg/client.key', 'private material')
        with self.assertRaises(ValueError):
            builder.stage_runtime_modules(modules, self.stage/'backend/node_modules')

    def test_manifest_hashes_source_tree_and_excludes_its_own_file(self):
        builder = load_builder()
        write(self.stage/'SOURCE_TREE', 'a'*40+'\n')
        write(self.stage/'backend/dist/main.js', b'compiled\x00bytes')
        write(self.stage/'manifest.json', 'old manifest')
        result = builder.make_manifest(self.stage, '1.0.0', 'a'*40)
        self.assertEqual(result['schema'], 1)
        self.assertEqual(result['source_tree'], 'a'*40)
        self.assertNotIn('manifest.json', result['files'])
        self.assertEqual(result['files']['SOURCE_TREE'], {'sha256': hashlib.sha256(('a'*40+'\n').encode()).hexdigest(), 'bytes':41})
        self.assertEqual(result['files']['backend/dist/main.js']['bytes'],14)

    def test_archive_is_deterministic_and_every_member_is_beneath_version_root(self):
        builder = load_builder()
        write(self.stage/'SOURCE_TREE', 'a'*40+'\n')
        write(self.stage/'backend/dist/main.js', 'compiled')
        first, second = self.root/'one.tar.gz', self.root/'two.tar.gz'
        builder.write_archive(self.stage, first, '1.0.0', 1700000000)
        for p in self.stage.rglob('*'):
            if p.is_file(): os.utime(p, (1800000000,1800000000))
        builder.write_archive(self.stage, second, '1.0.0', 1700000000)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first, 'r:gz') as archive:
            members = archive.getmembers()
            self.assertTrue(all(m.name.startswith('myteldesk-1.0.0/') and m.isfile() for m in members))
            self.assertTrue(all(m.mtime == 1700000000 and m.uid == 0 and m.gid == 0 for m in members))

    def test_archive_rechecks_symlinks_after_staging(self):
        builder = load_builder()
        write(self.root/'secret', 'secret')
        try:
            (self.stage/'copied').symlink_to(self.root/'secret')
        except OSError as error:
            self.skipTest('Symlink privilege unavailable: '+str(error))
        with self.assertRaises(ValueError):
            builder.write_archive(self.stage, self.root/'bad.tar.gz', '1.0.0', 1700000000)

    def test_version_cannot_be_used_as_an_archive_path(self):
        builder = load_builder()
        for version in ('../secret', '/secret', '1.0.0/../../secret', 'v1.0.0', '1.0'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                builder.validate_version(version)
        self.assertEqual(builder.validate_version('1.0.0'), '1.0.0')

    def test_output_must_be_new_and_outside_the_repository(self):
        builder = load_builder()
        with self.assertRaises(ValueError): builder.validate_output(self.source/'dist', self.source)
        existing = self.root/'existing'; existing.mkdir()
        with self.assertRaises(ValueError): builder.validate_output(existing, self.source)
        builder.validate_output(self.root/'new-artifact', self.source)

    def test_notice_collection_preserves_full_license_and_missing_metadata_readme(self):
        builder = load_builder()
        modules = self.root/'modules'
        write(modules/'pkg/package.json', json.dumps({'name':'pkg','version':'1.2.3','license':'MPL-2.0'}))
        write(modules/'pkg/LICENSE', 'Full license text\nCopyright fixture author\n')
        write(modules/'old/package.json', json.dumps({'name':'old','version':'0.1.0'}))
        write(modules/'old/README.md', 'Copyright old author\nMIT permission text fixture\n')
        builder.collect_notices({'backend':modules}, self.stage/'THIRD_PARTY_LICENSES')
        self.assertEqual((self.stage/'THIRD_PARTY_LICENSES/backend/pkg/LICENSE').read_text(), 'Full license text\nCopyright fixture author\n')
        self.assertTrue((self.stage/'THIRD_PARTY_LICENSES/backend/old/README.md').is_file())
        self.assertEqual(json.loads((self.stage/'THIRD_PARTY_LICENSES/backend/old/package.json').read_text())['name'], 'old')
        index = json.loads((self.stage/'THIRD_PARTY_LICENSES/index.json').read_text())
        self.assertEqual(len(index['packages']), 2)

    def test_content_boundary_rejects_tokens_and_real_pem_body(self):
        builder = load_builder()
        # Deliberately fabricated strings, split so this source is not a credential.
        for content in (('github_'+'pat_'+'x'*60).encode(),
                        ('-----BEGIN '+'PRIVATE KEY-----\n'+'A'*64+'\n').encode()):
            with self.subTest(content_length=len(content)), self.assertRaises(ValueError):
                builder._write(self.stage, 'README.md', content)
        builder._write(self.stage, 'README.md', b"Validation checks 'BEGIN PRIVATE KEY'; no key is present.\n")

    def test_clean_build_snapshot_has_only_git_source_and_development_dependencies(self):
        builder = load_builder()
        files = ['backend/src/main.ts', 'frontend/src/App.tsx', 'frontend/public/fonts/OFL.txt']
        for name in files: write(self.source/name, 'tracked')
        write(self.source/'frontend/.env', 'VITE_PRIVATE=hostsecret')
        write(self.source/'backend/src/ignored-local.ts', 'ignored host code')
        write(self.source/'frontend/dist/stale.js', 'stale')
        write(self.source/'cert/server.pem', 'host key')
        for section in ('backend', 'frontend'):
            write(self.source/section/'node_modules/compiler/index.js', 'installed compiler')
        builder.prepare_build_tree(self.source, self.stage, files, {name:b'git blob source' for name in files})
        for name in files:
            self.assertEqual((self.stage/name).read_bytes(), b'git blob source')
        for name in ('frontend/.env', 'backend/src/ignored-local.ts', 'frontend/dist/stale.js', 'cert/server.pem'):
            self.assertFalse((self.stage/name).exists(), name)
        self.assertTrue((self.stage/'frontend/node_modules/compiler/index.js').is_file())

    def test_build_environment_cannot_inject_host_vite_values_or_node_preloads(self):
        builder = load_builder()
        with patch.dict(os.environ, {'VITE_HOST_SECRET':'private', 'NODE_OPTIONS':'--require local.js',
                                     'NODE_PATH':'/host/modules'}), patch.object(builder.subprocess, 'run') as run:
            builder._run(['npm', 'run', 'build'], self.source)
        environment = run.call_args[1]['env']
        for name in ('VITE_HOST_SECRET', 'NODE_OPTIONS', 'NODE_PATH'):
            self.assertNotIn(name, environment)
        self.assertEqual(environment['NODE_ENV'], 'production')


if __name__ == '__main__':
    unittest.main()
