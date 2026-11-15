jest.mock('child_process', () => ({ execFile: jest.fn() }));
jest.mock('../src/services/vad-runner', () => ({ VAD_SCRIPT: '/private/vad/script.py', vadPython: () => '/private/vad/python' }));

import 'reflect-metadata';
import * as cp from 'child_process';
import { VadHealthMonitor, vadHealth } from '../src/services/vad-health';
import { VadController } from '../src/api/routes/vad.controller';
import { AUTH_POLICY } from '../src/api/routing';

const exec = cp.execFile as unknown as jest.Mock;
const hash = 'a'.repeat(64);
const report = () => ({ status: 'ok', engine: 'silero', model_version:'6.2.3', model_sha256: hash,
  providers:['CPUExecutionProvider'], threads:1, context_samples:64, inference_frames:2,
  packages:{ numpy:'2.4.6', onnxruntime:'1.32.0', 'webrtcvad-wheels':'2.0.14.post1' } });
const ready = { available:true, engine:'silero', error_code:null };
const unavailable = { available:false, engine:null, error_code:'vad.unavailable' };
type Callback = (error: unknown, stdout: string, stderr: string) => void;

describe('VAD readiness uses real doctor output', () => {
  let clock = 0;
  let monitor: VadHealthMonitor;
  const originalHash = process.env.VAD_MODEL_SHA256;
  beforeEach(() => {
    clock = 0; monitor = new VadHealthMonitor(() => clock);
    process.env.VAD_MODEL_SHA256 = hash;
    exec.mockReset().mockImplementation((_file, _args, _options, callback: Callback) => {
      callback(null, JSON.stringify(report()), '');
      return {};
    });
  });
  afterAll(() => {
    if(originalHash===undefined)delete process.env.VAD_MODEL_SHA256;else process.env.VAD_MODEL_SHA256=originalHash;
  });
  test('accepts verified CPU inference, not file existence alone', async () => {
    expect(await monitor.check()).toEqual(ready);
    expect(exec).toHaveBeenCalledTimes(1);
    expect(exec.mock.calls[0].slice(0,2)).toEqual(['/private/vad/python',['/private/vad/script.py','--doctor']]);
    expect(exec.mock.calls[0][2]).toMatchObject({ timeout:15000,maxBuffer:16384,shell:false });
  });
  test.each([
    '{bad json',
    JSON.stringify({ ...report(), inference_frames:0 }),
    JSON.stringify({ ...report(), providers:['CUDAExecutionProvider','CPUExecutionProvider'] }),
    JSON.stringify({ ...report(), model_sha256:'b'.repeat(64) }),
    JSON.stringify({ ...report(), context_samples:0 }),
    JSON.stringify({ status:'error',error_code:'model_missing',path:'/private/model' }),
  ])('rejects incomplete/unverified doctor response %s', async stdout => {
    exec.mockImplementation((_f,_a,_o,callback:Callback) => callback(null,stdout,''));
    expect(await monitor.check()).toEqual(unavailable);
    expect(exec).toHaveBeenCalledTimes(1);
  });
  test('deduplicates simultaneous requests and caches success for a minute', async () => {
    let finish!: Callback;
    exec.mockImplementation((_f,_a,_o,callback:Callback) => {finish=callback;});
    const a=monitor.check(),b=monitor.check();
    expect(exec).toHaveBeenCalledTimes(1);
    finish(null,JSON.stringify(report()),'');
    expect(await a).toEqual(ready);expect(await b).toEqual(ready);
    clock=59999;expect(await monitor.check()).toEqual(ready);expect(exec).toHaveBeenCalledTimes(1);
    clock=60001;exec.mockImplementation((_f,_a,_o,callback:Callback) => callback(null,JSON.stringify(report()),''));
    expect(await monitor.check()).toEqual(ready);expect(exec).toHaveBeenCalledTimes(2);
  });
  test('retries an unavailable dependency after five seconds', async () => {
    exec.mockImplementationOnce((_f,_a,_o,callback:Callback) => callback({code:'ENOENT'},'','private path'));
    expect(await monitor.check()).toEqual(unavailable);
    clock=4000;expect(await monitor.check()).toEqual(unavailable);expect(exec).toHaveBeenCalledTimes(1);
    clock=5001;expect(await monitor.check()).toEqual(ready);expect(exec).toHaveBeenCalledTimes(2);
  });
  test('returns bounded timeout feedback without leaking diagnostic text', async () => {
    exec.mockImplementation((_f,_a,_o,callback:Callback) => callback({killed:true,code:'ETIMEDOUT'},'','secret /root/private'));
    expect(await monitor.check()).toEqual({available:false,engine:null,error_code:'vad.timeout'});
  });
  test('controller is admin-only and returns the capability without internal paths', async () => {
    expect(Reflect.getMetadata(AUTH_POLICY,VadController.prototype.status)).toBe('admin');
    const spy=jest.spyOn(vadHealth,'check').mockResolvedValueOnce(ready as never);
    expect(await new VadController().status()).toEqual(ready);
    spy.mockRestore();
  });
});
