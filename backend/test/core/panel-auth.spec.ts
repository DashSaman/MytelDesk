/**
 * Panel-source authentication tests (MyTellPBX v7.2.0).
 * The panel ACL fixture is a real sqlite file opened read-only, mirroring
 * /var/www/db/acl.db (acl_user + acl_membership, group 1 = admin).
 */
import { createHash } from 'node:crypto';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';

const adminPw = 'panel-admin-pw-1';
const otherPw = 'panel-other-pw-2';

function makeAclFixture(): string {
  const dir = mkdtempSync(join(tmpdir(), 'mt-panel-acl-'));
  const path = join(dir, 'acl.db');
  const db = new DatabaseSync(path);
  db.exec('CREATE TABLE acl_user (id INTEGER PRIMARY KEY, name TEXT, md5_password TEXT)');
  db.exec('CREATE TABLE acl_membership (id INTEGER PRIMARY KEY, id_user INTEGER, id_group INTEGER)');
  const md5 = (s: string) => createHash('md5').update(s).digest('hex');
  db.prepare('INSERT INTO acl_user (id, name, md5_password) VALUES (?, ?, ?)').run(7, 'admin', md5(adminPw));
  db.prepare('INSERT INTO acl_user (id, name, md5_password) VALUES (?, ?, ?)').run(8, 'operator', md5(otherPw));
  db.prepare('INSERT INTO acl_membership (id, id_user, id_group) VALUES (1, 7, 1)').run();
  db.prepare('INSERT INTO acl_membership (id, id_user, id_group) VALUES (2, 8, 3)').run();
  db.close();
  return path;
}

describe('panel-auth credential verification', () => {
  let fixture: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let panelAuth: any;

  beforeAll(() => {
    fixture = makeAclFixture();
    jest.isolateModules(() => {
      process.env.MYTELLPBX_PANEL_ACL_PATH = fixture;
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      panelAuth = require('../../src/core/panel-auth');
    });
  });

  test('group-1 member with the correct password verifies', () => {
    expect(panelAuth.verifyPanelCredential('admin', adminPw)).toEqual({ panelUserId: 7, name: 'admin' });
  });

  test('wrong password, unknown user and non-admin are indistinguishable (null)', () => {
    expect(panelAuth.verifyPanelCredential('admin', 'nope')).toBeNull();
    expect(panelAuth.verifyPanelCredential('ghost', adminPw)).toBeNull();
    expect(panelAuth.verifyPanelCredential('operator', otherPw)).toBeNull();
  });

  test('membership guard follows group 1', () => {
    panelAuth.resetPanelAuthCache();
    expect(panelAuth.panelMembershipValid(7)).toBe(true);
    expect(panelAuth.panelMembershipValid(8)).toBe(false);
  });
});
