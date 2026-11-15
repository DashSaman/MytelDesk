/** Recording query transport over real Nest/Express/auth/Range handling. No PBX or DB.
 * Filesystem resolution is mocked here; recording.spec.ts owns the real sandbox tests.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

jest.mock('../src/db/pool', () => ({
  ...jest.requireActual('../src/db/pool'), query: jest.fn(), queryOne: jest.fn(), execute: jest.fn(),
}));
jest.mock('../src/db/db-manager', () => ({
  getSetting: jest.fn(async (_key: string, fallback: unknown = null) => fallback),
  getCallLegs: jest.fn(),
}));
jest.mock('../src/services/call-log', () => ({}));
jest.mock('../src/services/recording', () => ({
  resolveRecordingRequest: jest.fn(),
  pyBasename: (p: string) => p.replace(/\\/g, '/').split('/').pop(),
  recordingContentType: () => 'audio/x-wav',
}));

import type { INestApplication } from '@nestjs/common';
import request from 'supertest';
import { CallHistoryController } from '../src/api/routes/call-history.controller';
import { createAccessToken, generateApiKey, hashApiKey, resetJwtSecretCache } from '../src/core/security';
import * as pool from '../src/db/pool';
import * as dbm from '../src/db/db-manager';
import { resolveRecordingRequest } from '../src/services/recording';
import { nestApp } from './helpers/nest-app';

const USERS: Record<number, Record<string, unknown>> = {
  1: { id: 1, username: 'fixture-admin', role: 'admin', extension: null, is_active: 1 },
  2: { id: 2, username: 'fixture-agent', role: 'agent', extension: '201', is_active: 1 },
};
const KEYS: Record<string, string[]> = {};
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'myteldesk-recording-route-'));
const file = path.join(tmp, 'fixture.wav');
const audio = Buffer.from('RIFFfixture-audio-data');
let app: INestApplication;
let adminToken: string;
let agentToken: string;
const http = () => request(app.getHttpServer());
const endpoint = (p: string) => '/api/recording-file?path=' + encodeURIComponent(p);
const keyWith = (...scopes: string[]) => {
  const key = generateApiKey();KEYS[hashApiKey(key)] = scopes;return key;
};

beforeAll(async () => {
  fs.writeFileSync(file, audio);
  process.env.JWT_SECRET = 'recording-route-fixture-only';resetJwtSecretCache();
  (pool.queryOne as jest.Mock).mockImplementation(async (sql: string, params: unknown[] = []) => {
    if (sql.includes('FROM users')) return USERS[params[0] as number] ?? null;
    if (sql.includes('FROM api_keys')) {
      const scopes = KEYS[params[0] as string];
      return scopes ? { id: 5, name: 'fixture', scopes: JSON.stringify(scopes), enabled: 1 } : null;
    }
    return null;
  });
  (pool.query as jest.Mock).mockResolvedValue([]);
  (pool.execute as jest.Mock).mockResolvedValue({ affectedRows: 1, insertId: 0 });
  adminToken = await createAccessToken({ id: 1, username: 'fixture-admin', role: 'admin' });
  agentToken = await createAccessToken({ id: 2, username: 'fixture-agent', role: 'agent' });
  app = await nestApp([CallHistoryController]);
});
afterAll(async () => {
  await app?.close();fs.rmSync(tmp, { recursive: true, force: true });
});
beforeEach(() => {
  jest.clearAllMocks();
  (resolveRecordingRequest as jest.Mock).mockResolvedValue({ path: file });
  (dbm.getCallLegs as jest.Mock).mockResolvedValue([{ src: '900', dst: '201', lastapp: 'Dial' }]);
});

test.each([
  '/var/spool/asterisk/monitor/2026/11/15/call.wav',
  '2026/11/15/call.wav',
  '/var/spool/asterisk/monitor/تماس + #?%&= 100.wav',
  'literal%2Fname.wav',
])('query preserves exact recording identity %s', async (recordingPath) => {
  const res = await http().get(endpoint(recordingPath)).set('Authorization', `Bearer ${adminToken}`).expect(200);
  expect(resolveRecordingRequest).toHaveBeenCalledWith(recordingPath);
  expect(res.body).toEqual(audio);
  expect(res.headers['content-type']).toMatch(/^audio\/x-wav/);
});

test('query preserves the existing Range/206 response for playback', async () => {
  const res = await http().get(endpoint('call.wav')).set('Authorization', `Bearer ${adminToken}`)
    .set('Range', 'bytes=0-3').expect(206);
  expect(res.body).toEqual(audio.subarray(0, 4));
  expect(res.headers['content-range']).toBe(`bytes 0-3/${audio.length}`);
});

test('the legacy wildcard still serves decoded segments through the same policy', async () => {
  await http().get('/api/recordings/2026/11/call%20one.wav').set('Authorization', `Bearer ${adminToken}`).expect(200);
  expect(resolveRecordingRequest).toHaveBeenCalledWith('2026/11/call one.wav');
});

test('missing path is a validation error before filesystem resolution', async () => {
  await http().get('/api/recording-file').set('Authorization', `Bearer ${adminToken}`).expect(400);
  expect(resolveRecordingRequest).not.toHaveBeenCalled();
});

test('anonymous and query-token access are denied before filesystem resolution', async () => {
  await http().get(endpoint('call.wav')).expect(401);
  await http().get(endpoint('call.wav') + '&token=' + adminToken).expect(401);
  expect(resolveRecordingRequest).not.toHaveBeenCalled();
});

test('only an API key with cdr:read may use the query endpoint', async () => {
  await http().get(endpoint('call.wav')).set('X-API-Key', keyWith('calls:read')).expect(403);
  expect(resolveRecordingRequest).not.toHaveBeenCalled();
  await http().get(endpoint('call.wav')).set('X-API-Key', keyWith('cdr:read')).expect(200);
});

test.each(['../secret.wav', '/etc/passwd', 'link-outside.wav'])('sandbox denial remains 403 for %s', async (recordingPath) => {
  (resolveRecordingRequest as jest.Mock).mockResolvedValue({ status: 403 });
  await http().get(endpoint(recordingPath)).set('Authorization', `Bearer ${adminToken}`).expect(403);
  expect(resolveRecordingRequest).toHaveBeenCalledWith(recordingPath);
});

test('missing recording remains 404', async () => {
  (resolveRecordingRequest as jest.Mock).mockResolvedValue({ status: 404 });
  await http().get(endpoint('absent.wav')).set('Authorization', `Bearer ${adminToken}`).expect(404);
});

test('an agent can play their own call and authorization uses the resolved basename', async () => {
  await http().get(endpoint('/alias/original.wav')).set('Authorization', `Bearer ${agentToken}`).expect(200);
  expect(dbm.getCallLegs).toHaveBeenCalledWith({ recordingfile: 'fixture.wav' });
});

test.each(['query', 'wildcard'])('%s denies another user call, missing call history, and ChanSpy', async (transport) => {
  const url = transport === 'query' ? endpoint('call.wav') : '/api/recordings/call.wav';
  for (const legs of [
    [{ src: '900', dst: '202', lastapp: 'Dial' }],
    [],
    [{ src: '201', dst: 's', lastapp: 'ChanSpy', linkedid: 'spy-300-201-1' }],
  ]) {
    (dbm.getCallLegs as jest.Mock).mockResolvedValue(legs);
    await http().get(url).set('Authorization', `Bearer ${agentToken}`).expect(403);
  }
});
