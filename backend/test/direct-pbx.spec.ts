jest.mock('child_process', () => ({ execFile: jest.fn() }));
jest.mock('../src/db/db-manager', () => ({ getSetting: jest.fn(async (_k: string, d: unknown) => d) }));
import * as cp from 'child_process';
import * as path from 'path';
import { reloadAsteriskSip, subprocessRun } from '../src/services/dialplan';

const exec = cp.execFile as unknown as jest.Mock;
describe('standalone PBX account execution', () => {
  const old = process.env.MYTELDESK_DIRECT_PBX;
  beforeEach(() => {
    process.env.MYTELDESK_DIRECT_PBX = '1';
    exec.mockReset().mockImplementation((_f, _a, _o, cb) => {
      cb(null, Buffer.from('{"status":true,"num_errors":0,"reload_needed":false}'), Buffer.from(''));
      return { stdin: { on: jest.fn(), end: jest.fn() } };
    });
  });
  afterAll(() => { if (old === undefined) delete process.env.MYTELDESK_DIRECT_PBX; else process.env.MYTELDESK_DIRECT_PBX = old; });
  it('uses the existing PBX identity without sudo or shell interpolation', async () => {
    await subprocessRun(['sudo', 'cp', '/tmp/a b', '/etc/asterisk/test.conf']);
    expect(exec.mock.calls[0].slice(0, 2)).toEqual(['cp', ['/tmp/a b', '/etc/asterisk/test.conf']]);
  });
  it('keeps the legacy behavior unless explicitly enabled', async () => {
    delete process.env.MYTELDESK_DIRECT_PBX;
    await subprocessRun(['sudo', 'asterisk', '-rx', 'core show uptime']);
    expect(exec.mock.calls[0].slice(0, 2)).toEqual(['sudo', ['asterisk', '-rx', 'core show uptime']]);
  });
  it('uses one complete native apply without invoking root amportal', async () => {
    expect(await reloadAsteriskSip('Issabel')).toBe(true);
    expect(exec.mock.calls.map(c => c.slice(0, 2))).toEqual([
      ['/usr/bin/php', [path.resolve(__dirname, '../scripts/issabel-reload.php')]],
    ]);
  });
  it('does not report success when the native apply command fails', async () => {
    exec.mockImplementationOnce((_f, _a, _o, cb) => { cb({ code: 1 }, Buffer.from(''), Buffer.from('failed')); });
    expect(await reloadAsteriskSip('Issabel')).toBe(false);
    expect(exec).toHaveBeenCalledTimes(1);
  });
});
