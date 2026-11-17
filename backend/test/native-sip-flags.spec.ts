/**
 * Native Issabel sip has flags INT NOT NULL with no default (unlike the older
 * lab schema). Model that constraint before duplicate-key handling, as MariaDB
 * does, so an existing credential does not hide an incomplete INSERT candidate.
 * These are pool-boundary tests; no database or PBX command is executed.
 */
jest.mock('../src/db/pool', () => ({
  ...jest.requireActual('../src/db/pool'),
  execute: jest.fn(),
  queryOne: jest.fn(),
}));

import type { PoolConnection } from 'mysql2/promise';
import * as pool from '../src/db/pool';
import * as pbx from '../src/db/manager/pbx';
import { setLogLevel } from '../src/core/logger';

type SipRow = { id: string; keyword: string; data: string; flags: number };

/** Only the two INSERT forms and the UPDATE issued by this module are needed. */
class NativeSipTable {
  readonly rows = new Map<string, SipRow>();

  constructor(rows: SipRow[] = [], private readonly flagsDefault?: number) {
    for (const row of rows) this.rows.set(this.key(row.id, row.keyword), { ...row });
  }

  key(id: unknown, keyword: unknown): string { return JSON.stringify([id, keyword]); }

  execute(sql: string, params: unknown[]): { affectedRows: number } {
    const insert = /^INSERT INTO sip \(([^)]+)\) VALUES \(([^)]+)\)(.*)$/i.exec(sql);
    if (insert) {
      let parameter = 0;
      const columns = insert[1].split(',').map((s) => s.trim());
      const values = insert[2].split(',').map((s) => s.trim());
      const row = Object.fromEntries(columns.map((column, i) => {
        const token = values[i];
        if (token === '?') return [column, params[parameter++]];
        if (/^-?\d+$/.test(token)) return [column, Number(token)];
        throw new Error('Unexpected test INSERT value');
      })) as SipRow;
      if (row.flags === undefined && this.flagsDefault !== undefined) row.flags = this.flagsDefault;
      if (row.flags === undefined) {
        throw Object.assign(new Error("Field 'flags' doesn't have a default value"), { errno: 1364 });
      }
      const key = this.key(row.id, row.keyword), existing = this.rows.get(key);
      if (existing) {
        const update = /^ ON DUPLICATE KEY UPDATE (.+)$/i.exec(insert[3]);
        if (!update) throw Object.assign(new Error('Duplicate sip keyword'), { errno: 1062 });
        const before = { ...existing };
        for (const assignment of update[1].split(',')) {
          const field = /^\s*(\w+)\s*=\s*VALUES\((\w+)\)\s*$/i.exec(assignment);
          if (!field) throw new Error('Unexpected test duplicate-key assignment');
          Object.assign(existing, { [field[1]]: row[field[2] as keyof SipRow] });
        }
        return { affectedRows: JSON.stringify(existing) === JSON.stringify(before) ? 0 : 2 };
      }
      this.rows.set(key, row);
      return { affectedRows: 1 };
    }
    if (sql === 'UPDATE sip SET data = ? WHERE id = ? AND keyword = ?') {
      const existing = this.rows.get(this.key(params[1], params[2]));
      if (!existing || existing.data === params[0]) return { affectedRows: 0 };
      existing.data = String(params[0]);
      return { affectedRows: 1 };
    }
    throw new Error('Unexpected test SIP statement');
  }
}

const execute = pool.execute as jest.Mock, queryOne = pool.queryOne as jest.Mock;
const connection = {} as PoolConnection;

function use(table: NativeSipTable): void {
  execute.mockImplementation(async (sql: string, params: unknown[]) => table.execute(sql, params));
  queryOne.mockImplementation(async (sql: string, params: unknown[]) => {
    if (!sql.includes('FROM sip')) throw new Error('Unexpected test read');
    return table.rows.has(table.key(params[0], params[1])) ? { found: 1 } : null;
  });
}

beforeAll(() => setLogLevel('CRITICAL'));
beforeEach(() => jest.resetAllMocks());

test('native no-default schema permits retiring an existing secret and preserves its flags', async () => {
  const table = new NativeSipTable([{ id: '9916101', keyword: 'secret', data: 'old-fixture', flags: 27 }]);
  use(table);
  expect(await pbx.setExtensionSecretInPbx('9916101', 'new-fixture')).toBe(true);
  expect(table.rows.get(table.key('9916101', 'secret'))).toEqual({
    id: '9916101', keyword: 'secret', data: 'new-fixture', flags: 27,
  });
});

test('new username and max_contacts rows have explicit flags and repeated writes remain successful', async () => {
  const table = new NativeSipTable();
  use(table);
  expect(await pbx.setExtensionUsernameInPbx('9916101', 'fixture-user')).toBe(true);
  expect(await pbx.setExtensionUsernameInPbx('9916101', 'fixture-user')).toBe(true);
  expect(await pbx.setExtensionMaxContacts('9916101', 2, { reload: false })).toBe(true);
  expect([...table.rows.values()]).toEqual([
    { id: '9916101', keyword: 'username', data: 'fixture-user', flags: 0 },
    { id: '9916101', keyword: 'max_contacts', data: '2', flags: 0 },
  ]);
});

test('the equivalent Issabel missing-WebRTC-keyword insert handles the same native constraint', async () => {
  const table = new NativeSipTable();
  use(table);
  expect(await pbx.setSipKeywordRow(connection, '9916101', 'ice_support', 'yes', true)).toBe(1);
  expect(table.rows.get(table.key('9916101', 'ice_support'))).toEqual({
    id: '9916101', keyword: 'ice_support', data: 'yes', flags: 0,
  });
  expect(await pbx.setSipKeywordRow(connection, '9916101', 'ice_support', 'yes', true)).toBe(0);
});

test('updating existing WebRTC metadata preserves flags and never inserts with insertMissing=false', async () => {
  const table = new NativeSipTable([{ id: '9916101', keyword: 'transport', data: 'transport-udp', flags: 42 }]);
  use(table);
  expect(await pbx.setSipKeywordRow(connection, '9916101', 'transport', 'transport-wss', true)).toBe(1);
  expect(table.rows.get(table.key('9916101', 'transport'))?.flags).toBe(42);
  expect(await pbx.setSipKeywordRow(connection, '9916101', 'absent', 'yes', false)).toBe(0);
  expect(table.rows.size).toBe(1);
});

test('a schema with the legacy flags default keeps the same new-row and duplicate behavior', async () => {
  const table = new NativeSipTable([{ id: '100', keyword: 'secret', data: 'old', flags: 9 }], 0);
  use(table);
  expect(await pbx.setExtensionSecretInPbx('100', 'new')).toBe(true);
  expect(await pbx.setExtensionUsernameInPbx('101', 'fixture')).toBe(true);
  expect(table.rows.get(table.key('100', 'secret'))?.flags).toBe(9);
  expect(table.rows.get(table.key('101', 'username'))?.flags).toBe(0);
});

test('real database failures still propagate as false or a rejected missing-row write', async () => {
  execute.mockRejectedValue(new Error('database unavailable'));
  queryOne.mockResolvedValue(null);
  expect(await pbx.setExtensionSecretInPbx('9916101', 'fixture')).toBe(false);
  await expect(pbx.setSipKeywordRow(connection, '9916101', 'ice_support', 'yes', true)).rejects.toThrow('database unavailable');
});
