jest.mock('child_process', () => ({ execFile: jest.fn() }));
jest.mock('../src/db/db-manager', () => ({ getSetting: jest.fn(async (_k: string, d: unknown) => d) }));
import * as cp from 'child_process';
import * as path from 'path';
import { reloadAsteriskSip } from '../src/services/dialplan';

const exec = cp.execFile as unknown as jest.Mock;
const adapter = path.resolve(__dirname, '../scripts/issabel-reload.php');
const success = { status: true, num_errors: 0, reload_needed: false };

describe('standalone native Issabel Apply completion', () => {
  const original = process.env.MYTELDESK_DIRECT_PBX;
  beforeEach(() => { process.env.MYTELDESK_DIRECT_PBX = '1'; exec.mockReset(); });
  afterAll(() => {
    if (original === undefined) delete process.env.MYTELDESK_DIRECT_PBX;
    else process.env.MYTELDESK_DIRECT_PBX = original;
  });
  function respond(body: string, code = 0) {
    exec.mockImplementation((_file, _args, _options, callback) => {
      callback(code ? { code } : null, Buffer.from(body), Buffer.from(''));
      return { stdin: { on: jest.fn(), end: jest.fn() } };
    });
  }
  it('does not leave the native pending flag set by retrieve_conf', async () => {
    let pending = false;
    exec.mockImplementation((file, args, _options, callback) => {
      // Captured native behavior: regeneration marks pending. A raw core reload
      // does not clear it; only the complete native apply performs that step.
      if (file === '/var/lib/asterisk/bin/retrieve_conf') pending = true;
      if (file === '/usr/bin/php' && args[0] === adapter) pending = false;
      callback(null, Buffer.from(JSON.stringify(success)), Buffer.from(''));
      return { stdin: { on: jest.fn(), end: jest.fn() } };
    });
    expect(await reloadAsteriskSip('Issabel')).toBe(true);
    expect(pending).toBe(false);
    expect(exec.mock.calls.map(row => row.slice(0, 2))).toEqual([['/usr/bin/php', [adapter]]]);
  });
  it.each([
    { status: false, num_errors: 0, reload_needed: false },
    { status: 'true', num_errors: 0, reload_needed: false },
    { status: true, num_errors: 1, reload_needed: false },
    { status: true, num_errors: '0', reload_needed: false },
    { status: true, num_errors: null, reload_needed: false },
    { status: true, num_errors: false, reload_needed: false },
    { status: true, reload_needed: false },
    { status: true, num_errors: 0, reload_needed: true },
    { status: true, num_errors: 0, reload_needed: 'false' },
    { status: true, num_errors: 0 },
  ])('rejects incomplete or failed native Apply evidence: %j', async result => {
    respond(JSON.stringify(result));
    expect(await reloadAsteriskSip('Issabel')).toBe(false);
  });
  it.each(['', 'Successfully reloaded', '{invalid', 'null', '[]'])('does not treat exit0 plus %j as success', async body => {
    respond(body);
    expect(await reloadAsteriskSip('Issabel')).toBe(false);
  });
  it('rejects a nonzero command even with a success-shaped result', async () => {
    respond(JSON.stringify(success), 1);
    expect(await reloadAsteriskSip('Issabel')).toBe(false);
    expect(exec).toHaveBeenCalledTimes(1);
  });
  it('does not retry a failed or timed-out native Apply', async () => {
    exec.mockImplementation((_file, _args, _options, callback) => {
      callback({ killed: true }, Buffer.from(''), Buffer.from(''));
      return { stdin: { on: jest.fn(), end: jest.fn() } };
    });
    expect(await reloadAsteriskSip('Issabel')).toBe(false);
    expect(exec).toHaveBeenCalledTimes(1);
  });
});
