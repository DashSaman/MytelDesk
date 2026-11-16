"""Real installer orchestration against an isolated filesystem and fake commands."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

spec=importlib.util.spec_from_file_location('install_vad_lifecycle',Path(__file__).resolve().parents[1]/'installer/install.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
REAL_WAIT_HEALTH = mod.wait_health
REAL_VERIFY_PROXY = mod.verify_proxy


class VadInstallLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='myteldesk-install-flow-');self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name);self.host=self.home/'host';self.root=self.home/'app';self.config=self.home/'config';self.data=self.home/'data'
        self.bundle=self.home/'bundle';(self.bundle/'backend').mkdir(parents=True)
        (self.bundle/'backend/schema.sql').write_text('CREATE TABLE IF NOT EXISTS users (id INT);')
        self.manifest={'schema':1,'version':'1.1.0','source_tree':'a'*40}
        self.values={'DB_PASSWORD':'d'*48,'JWT_SECRET':'j'*48,'AMI_SECRET':'m'*48,'CUSTOM_SETTING':'preserve'}
        self.vad={'VAD_PYTHON':'/opt/myteldesk/runtime/vad/ready/bin/python','VAD_MODEL_PATH':'/opt/myteldesk/runtime/vad/model.onnx','VAD_MODEL_SHA256':'b'*64}
        real_path=Path
        def translated(value):
            if isinstance(value,real_path):return value
            text=os.fspath(value)
            if text.startswith(('/etc/','/opt/opdesk','/opt/OpDesk','/usr/local/sbin/','/run/')):
                return self.host/text.lstrip('/')
            return real_path(value)
        self.path=translated
        for name,text in [('/etc/os-release','ID=rocky\nVERSION_ID=8.10\n'),('/etc/issabel.conf','mysqlrootpwd=fake\n'),('/etc/asterisk/manager.conf','[general]\nenabled=yes\n#include manager_custom.conf\n'),('/etc/asterisk/manager_custom.conf','; keep native manager accounts\n')]:
            p=translated(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text)
        translated('/etc/httpd/conf.d').mkdir(parents=True)
        self.unit=translated('/etc/systemd/system/myteldesk.service');self.proxy=translated('/etc/httpd/conf.d/myteldesk.conf');self.ami=translated('/etc/asterisk/manager_myteldesk.conf')
        self.env=self.config/'myteldesk.env';self.state_file=self.config/'installation.json'
        self.calls=[]
        def command(argv,*args,**kwargs):
            self.calls.append(argv)
            text=''
            if argv[:2]==['asterisk','-V']:text='Asterisk 20.12.0'
            elif argv[:2]==['httpd','-M']:text='proxy_module\nproxy_http_module\nproxy_wstunnel_module'
            elif argv[:2]==['asterisk','-rx']:text='Manager (AMI): Yes'
            elif '-e' in argv:text='$2b$12$'+'x'*53
            return SimpleNamespace(stdout=text,returncode=0,stderr='')
        def node(_bundle):
            p=self.root/'runtime/node/bin/node';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fake-node');return p
        patches=[mock.patch.object(mod,'Path',side_effect=translated),mock.patch.object(mod,'ROOT',self.root),mock.patch.object(mod,'CONFIG',self.config),mock.patch.object(mod,'DATA',self.data),mock.patch.object(mod,'UNIT',self.unit),mock.patch.object(mod,'PROXY',self.proxy),mock.patch.object(mod,'AMI',self.ami),mock.patch.object(mod,'ENV',self.env),mock.patch.object(mod,'STATE',self.state_file),mock.patch.object(mod,'verify_bundle',return_value=self.manifest),mock.patch.object(mod,'root_owned'),mock.patch.object(mod,'ensure_runtime',side_effect=node),mock.patch.object(mod,'run',side_effect=command),mock.patch.object(mod,'mysql',return_value=''),mock.patch.object(mod,'root_database_password',return_value='fake'),mock.patch.object(mod,'wait_health',return_value={'status':'ok','ami_connected':True}),mock.patch.object(mod,'verify_app'),mock.patch.object(mod,'verify_proxy'),mock.patch.object(mod,'backup_database'),mock.patch.object(mod,'render_systemd',return_value='[Service]\nUser=asterisk\n'),mock.patch.object(mod,'render_apache',return_value='# own prefix only\n'),mock.patch.object(mod,'render_env',side_effect=lambda d:''.join(k+'='+str(v)+'\n' for k,v in sorted(d.items()))),mock.patch.object(mod,'read_state',side_effect=lambda:json.loads(self.state_file.read_text()) if self.state_file.exists() else None),mock.patch.object(mod.os,'chown',create=True),mock.patch.object(mod.os,'uname',return_value=SimpleNamespace(machine='x86_64'),create=True),mock.patch.dict(sys.modules,{'pwd':SimpleNamespace(getpwnam=lambda _:SimpleNamespace(pw_uid=1000)),'grp':SimpleNamespace(getgrnam=lambda _:SimpleNamespace(gr_gid=1000))})]
        if os.name=='nt':patches.append(mock.patch.object(Path,'symlink_to',lambda p,target,*a,**k:p.write_text(str(target))))
        for patch in patches:patch.start();self.addCleanup(patch.stop)
        provision_patch=mock.patch.object(mod,'ensure_vad',return_value=self.vad,create=True)
        self.provision=provision_patch.start();self.addCleanup(provision_patch.stop)
        doctor_patch=mock.patch.object(mod,'doctor_vad',return_value={'status':'ok','engine':'silero'},create=True)
        self.doctor=doctor_patch.start();self.addCleanup(doctor_patch.stop)
        self.recording = SimpleNamespace(record={'schema':1,'fixture':'owned-module'},intent={'fixture':'module-intent'},
            started=False,activate=mock.Mock(),doctor=mock.Mock(),before_restore=mock.Mock(),after_restore=mock.Mock(),remove=mock.Mock(),untouched=mock.Mock())
        def activate(_transaction, on_begin=None):
            self.recording.started=True
            self.recording.intent['started']=True
            if on_begin: on_begin()
        self.recording.activate.side_effect=activate
        for name, value in [('prepare_recording_module',mock.Mock(return_value=self.recording)),
                            ('recording_from_intent',mock.Mock(return_value=self.recording)),
                            ('recording_for_restore',mock.Mock(return_value=self.recording)),
                            ('apply_native_recording',mock.Mock())]:
            patch=mock.patch.object(mod,name,value,create=True); patch.start(); self.addCleanup(patch.stop)

    def existing(self,version='1.0.0',tree='c'*40):
        self.config.mkdir();self.root.mkdir();self.data.mkdir()
        self.env.write_text(''.join(k+'='+v+'\n' for k,v in self.values.items()))
        hashes={}
        for p in [self.unit,self.proxy,self.ami,self.path('/usr/local/sbin/myteldesk')]:
            p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original '+p.name);hashes[str(p)]=mod.digest(p)
        release=self.root/'releases/previous';release.mkdir(parents=True)
        state={'owner':'MytelDesk','schema':1,'status':'ready','version':version,'tree':tree,'release':str(release),'node':'/private/node','public_url':'https://pbx.example/MytellDesk/','owned_hashes':hashes}
        self.state_file.write_text(json.dumps(state));return state

    def test_fresh_install_automatically_provisions_vad(self):
        mod.install(self.bundle,'https://pbx.example')
        self.provision.assert_called_once()
        values=mod.read_key_values(self.env)
        for key,value in self.vad.items():self.assertEqual(values[key],value)
        self.assertNotIn('RECORDING_ENABLED',values)

    def test_recording_generation_module_is_activated_applied_and_live_checked(self):
        mod.install(self.bundle,'https://pbx.example')
        self.recording.activate.assert_called_once()
        mod.apply_native_recording.assert_called_once()
        self.recording.doctor.assert_called_once()
        self.assertEqual(json.loads(self.state_file.read_text())['recording_module'],self.recording.record)

    def test_recording_module_rollback_runs_on_failed_activation(self):
        mod.wait_health.side_effect=RuntimeError('synthetic activation failure')
        with self.assertRaisesRegex(RuntimeError,'synthetic activation failure'):mod.install(self.bundle,'https://pbx.example')
        self.recording.before_restore.assert_called_once()
        self.recording.after_restore.assert_called_once()

    def test_recording_prewrite_refusal_never_stops_service_or_applies_pending_changes(self):
        self.recording.activate.side_effect=RuntimeError('operator pending changes appeared')
        with self.assertRaisesRegex(RuntimeError,'operator pending changes'):mod.install(self.bundle,'https://pbx.example')
        mod.apply_native_recording.assert_not_called()
        self.recording.before_restore.assert_not_called()
        self.assertNotIn(['systemctl','stop','myteldesk'],self.calls)

    def test_untouched_recording_crash_recovery_does_not_apply_or_stop(self):
        previous=self.existing(); directory=self.config/'backups/untouched'; directory.mkdir(parents=True)
        (directory/'previous-state.json').write_text(json.dumps(previous))
        state=dict(previous,pending_journal=str(directory/'journal.json'),pending_recording=self.recording.intent)
        self.recording.started=False
        self.assertEqual(mod.recover_pending(state),previous)
        mod.apply_native_recording.assert_not_called(); self.recording.before_restore.assert_not_called()
        self.recording.untouched.assert_called_once(); self.assertEqual(self.calls,[])

    def install_with_real_health(self, ready_after):
        clock = [0]
        self.activation_values = None
        def response(*_args, **_kwargs):
            if self.activation_values is None:
                self.activation_values = mod.read_key_values(self.env)
            if ready_after is None or clock[0] < ready_after:
                raise OSError('synthetic cold storage startup')
            return io.BytesIO(b'{"status":"ok","ami_connected":true}')
        mod.wait_health.side_effect = REAL_WAIT_HEALTH
        with mock.patch.object(mod.time, 'time', side_effect=lambda: clock[0]), \
             mock.patch.object(mod.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
             mock.patch.object(mod, 'urlopen', side_effect=response), mock.patch('sys.stdout', io.StringIO()) as output:
            try:
                mod.install(self.bundle, 'https://pbx.example')
            finally:
                self.elapsed = clock[0]
                self.readiness_output = output.getvalue()

    def test_cold_activation_can_finish_after_the_old_300_second_deadline(self):
        failure = None
        try:
            self.install_with_real_health(360)
        except RuntimeError as error:
            failure = error
        self.assertIsNone(failure, 'Cold activation stopped before simulated readiness: ' + str(failure))
        self.assertEqual(self.elapsed, 360)
        self.assertEqual(json.loads(self.state_file.read_text())['status'], 'ready')
        mod.wait_health.assert_called_once_with(timeout=900)
        self.assertIn('elapsed (limit 900s)', self.readiness_output)

    def test_early_ready_activation_does_not_wait_for_the_new_budget(self):
        self.install_with_real_health(0)
        self.assertEqual(self.elapsed, 0)
        self.assertEqual(json.loads(self.state_file.read_text())['status'], 'ready')
        mod.wait_health.assert_called_once_with(timeout=900)
        self.assertNotIn('Waiting for backend', self.readiness_output)

    def test_failed_cold_activation_is_bounded_and_preserves_prepared_secrets(self):
        with self.assertRaisesRegex(RuntimeError, 'Backend/AMI did not become ready'):
            self.install_with_real_health(None)
        self.assertEqual(self.elapsed, 900)
        self.assertEqual(json.loads(self.state_file.read_text())['status'], 'prepared')
        after = mod.read_key_values(self.env)
        for key in ('DB_PASSWORD', 'JWT_SECRET', 'AMI_SECRET', 'OPDESK_BRIDGE_PW'):
            self.assertEqual(after[key], self.activation_values[key])
        self.assertFalse(self.unit.exists()); self.assertFalse(self.proxy.exists()); self.assertFalse(self.ami.exists())
        self.assertIn(['systemctl', 'stop', 'myteldesk'], self.calls)
        self.assertNotIn('Installation verified:', self.readiness_output)

    def test_upgrade_keeps_300_second_budget_and_rolls_back_on_late_readiness(self):
        self.existing(); before = self.env.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Backend/AMI did not become ready'):
            self.install_with_real_health(360)
        self.assertEqual(self.elapsed, 300)
        self.assertEqual(self.env.read_bytes(), before)
        self.assertEqual(json.loads(self.state_file.read_text())['status'], 'ready')
        mod.wait_health.assert_called_once_with()

    def install_with_real_proxy(self, mode):
        clock = [0]; attempts = [0]; self.proxy_requests = []; self.activation_values = None
        owner = self
        class Connection:
            def __init__(self, host, port, **_kwargs):
                owner.assertEqual((host, port), ('127.0.0.1', 443))
            def request(self, method, route, headers=None):
                owner.assertEqual(method, 'GET'); owner.assertEqual(headers['Host'], 'pbx.example')
                self.route = route; owner.proxy_requests.append(route)
            def getresponse(self):
                status = 200
                if self.route == '/MytellDesk/health':
                    attempts[0] += 1
                    if owner.activation_values is None: owner.activation_values = mod.read_key_values(owner.env)
                    if mode == 'always-404' or mode == 'first-404' and attempts[0] == 1: status = 404
                    body = b'{"status":"ok","ami_connected":false}' if mode == 'bad-ami' else b'{"status":"ok","ami_connected":true}'
                elif self.route == '/MytellDesk/':
                    body = b'<html><script src="/MytellDesk/assets/fixture.js"></script></html>'
                else:
                    owner.assertEqual(self.route, '/MytellDesk/assets/fixture.js')
                    status = 404 if mode == 'bad-asset' else 200
                    body = b'window.fixture=true;'
                return SimpleNamespace(status=status, read=lambda size=None: body if size is None else body[:size])
            def close(self): pass
        mod.verify_proxy.side_effect = REAL_VERIFY_PROXY
        with mock.patch.object(mod.http.client, 'HTTPSConnection', Connection), \
             mock.patch.object(mod.time, 'monotonic', side_effect=lambda: clock[0]), \
             mock.patch.object(mod.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
             mock.patch('sys.stdout', io.StringIO()) as output:
            try:
                mod.install(self.bundle, 'https://pbx.example')
            finally:
                self.proxy_elapsed = clock[0]; self.proxy_progress = output.getvalue(); self.proxy_attempts = attempts[0]

    def test_activation_waits_for_apache_graceful_after_an_initial_404(self):
        failure = None
        try:
            self.install_with_real_proxy('first-404')
        except RuntimeError as error:
            failure = error
        self.assertIsNone(failure, 'Transient pre-graceful404 caused rollback: ' + str(failure))
        self.assertEqual(json.loads(self.state_file.read_text())['status'], 'ready')
        self.assertEqual(self.proxy_attempts, 2)
        self.assertEqual(self.proxy_elapsed, 2)
        self.assertIn('/MytellDesk/assets/fixture.js', self.proxy_requests)

    def test_persistent_proxy_route_ami_or_asset_failure_is_bounded_and_rolls_back(self):
        for mode in ['always-404', 'bad-ami', 'bad-asset']:
            with self.subTest(mode=mode):
                with self.assertRaises(RuntimeError): self.install_with_real_proxy(mode)
                self.assertEqual(self.proxy_elapsed, 60)
                self.assertGreater(self.proxy_attempts, 1)
                self.assertIn('elapsed (limit 60s)', self.proxy_progress)
                self.assertEqual(json.loads(self.state_file.read_text())['status'], 'prepared')
                values = mod.read_key_values(self.env)
                for key in ('DB_PASSWORD', 'JWT_SECRET', 'AMI_SECRET', 'OPDESK_BRIDGE_PW'):
                    self.assertEqual(values[key], self.activation_values[key])
                self.assertFalse(self.proxy.exists()); self.assertFalse(self.unit.exists()); self.assertFalse(self.ami.exists())
                self.assertNotIn('Installation verified:', self.proxy_progress)

    def test_doctor_proxy_failure_remains_immediate(self):
        self.existing(); mod.verify_proxy.side_effect = RuntimeError('bad existing proxy')
        with mock.patch.object(mod.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'bad existing proxy'): mod.doctor()
        mod.verify_proxy.assert_called_once_with('https://pbx.example/MytellDesk/')
        sleep.assert_not_called()

    def test_same_ready_proxy_failure_remains_immediate(self):
        self.existing(version='1.1.0', tree='a'*40)
        with self.env.open('a') as stream:
            stream.write(''.join(key + '=' + value + '\n' for key, value in self.vad.items()))
        mod.verify_proxy.side_effect = RuntimeError('bad existing proxy')
        with mock.patch.object(mod.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'bad existing proxy'): mod.install(self.bundle, 'https://pbx.example')
        mod.wait_health.assert_called_once_with(20)
        mod.verify_proxy.assert_called_once_with('https://pbx.example/MytellDesk/')
        sleep.assert_not_called()

    def test_fresh_download_failure_can_be_retried_without_manual_directory_deletion(self):
        self.provision.side_effect=[RuntimeError('temporary download failure'),self.vad]
        with self.assertRaisesRegex(RuntimeError,'temporary download failure'):mod.install(self.bundle,'https://pbx.example')
        self.assertFalse(self.env.exists())
        mod.install(self.bundle,'https://pbx.example')
        self.assertEqual(json.loads(self.state_file.read_text())['status'],'ready')

    def test_bootstrap_failure_preserves_already_generated_connection_secrets_on_retry(self):
        original_run=mod.run.side_effect
        attempts=[0]
        def fail_hash_once(argv,*args,**kwargs):
            if '-e' in argv and attempts[0]==0:
                attempts[0]+=1;raise RuntimeError('interrupted password hashing')
            return original_run(argv,*args,**kwargs)
        mod.run.side_effect=fail_hash_once
        with self.assertRaisesRegex(RuntimeError,'interrupted password hashing'):mod.install(self.bundle,'https://pbx.example')
        before=mod.read_key_values(self.env)
        mod.install(self.bundle,'https://pbx.example')
        after=mod.read_key_values(self.env)
        for key in ('DB_PASSWORD','JWT_SECRET','AMI_SECRET','OPDESK_BRIDGE_PW'):
            self.assertEqual(before[key],after[key])

    def test_upgrade_preserves_credentials_and_switches_vad_environment(self):
        self.existing();mod.install(self.bundle,'https://pbx.example')
        values=mod.read_key_values(self.env)
        for key,value in self.values.items():self.assertEqual(values[key],value)
        self.assertEqual(values['VAD_MODEL_SHA256'],self.vad['VAD_MODEL_SHA256'])
        state=json.loads(self.state_file.read_text());journal=json.loads(Path(state['last_journal']).read_text())
        self.assertIn(str(self.env),[entry['path'] for entry in journal])

    def test_operator_environment_changes_are_preserved_on_later_upgrade(self):
        state=self.existing();state['owned_hashes'][str(self.env)]=mod.digest(self.env)
        self.state_file.write_text(json.dumps(state))
        self.env.write_text(self.env.read_text()+'OPERATOR_CHOICE=keep_this\n')
        mod.install(self.bundle,'https://pbx.example')
        self.assertEqual(mod.read_key_values(self.env)['OPERATOR_CHOICE'],'keep_this')

    def test_unusable_vad_does_not_replace_the_active_environment(self):
        self.existing();before=self.env.read_bytes();self.provision.side_effect=RuntimeError('VAD probe failed')
        with self.assertRaisesRegex(RuntimeError,'VAD probe failed'):mod.install(self.bundle,'https://pbx.example')
        self.assertEqual(self.env.read_bytes(),before)
        self.assertFalse(any(argv[:2]==['systemctl','restart'] for argv in self.calls))

    def test_same_release_checks_vad_instead_of_hiding_missing_runtime(self):
        self.existing(version='1.1.0',tree='a'*40);self.doctor.side_effect=RuntimeError('VAD not ready')
        with self.assertRaisesRegex(RuntimeError,'VAD not ready'):mod.install(self.bundle,'https://pbx.example')

    def test_failed_backend_activation_restores_the_previous_environment(self):
        self.existing();before=self.env.read_bytes();mod.wait_health.side_effect=RuntimeError('backend failed')
        with self.assertRaisesRegex(RuntimeError,'backend failed'):mod.install(self.bundle,'https://pbx.example')
        self.assertEqual(self.env.read_bytes(),before)
        self.provision.assert_called_once()

    def test_doctor_requires_working_vad(self):
        self.existing();self.doctor.side_effect=RuntimeError('VAD not ready')
        with self.assertRaisesRegex(RuntimeError,'VAD not ready'):mod.doctor()


if __name__=='__main__':unittest.main()
