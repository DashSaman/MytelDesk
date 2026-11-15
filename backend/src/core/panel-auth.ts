/**
 * Panel-source authentication (MyTellPBX v7.2.0).
 *
 * Verifies Issabel panel credentials against the panel's own sqlite ACL
 * database READ-ONLY (readOnly open + PRAGMA query_only; the file is never
 * written). The md5 comparison is a read-only compatibility check with the
 * format the panel itself stores — the desk keeps bcrypt for its own
 * accounts. Maps the immutable panel identity (provider='issabel-panel',
 * externalId=acl_user.id) to a desk shadow user and exposes the membership
 * guard used to revalidate panel-sourced tokens and sockets.
 *
 * Policy: only current members of panel group 1 (administrators) may sign
 * in via this source; they hold role 'admin' exactly while the membership
 * stays valid. Shadow users get an unusable random bcrypt password, keep
 * the panel display name, and honor desk-side suspension (is_active).
 */
import { createHash } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { getLogger } from './logger';
import { execute, queryOne, withTransaction } from '../db/pool';
import { hashUserPassword } from '../db/manager/users';

const log = getLogger('core.panel_auth');

export const PANEL_AUTH_SOURCE = 'panel';
export const PANEL_PROVIDER = 'issabel-panel';
export const PANEL_TOKEN_TTL_SECONDS = 15 * 60;
const MEMBERSHIP_CACHE_MS = 15_000;

/** Resolve the panel ACL path at CALL time so tests (and env overrides)
 *  apply regardless of module load order. */
function panelAclPath(): string {
  return process.env.MYTELLPBX_PANEL_ACL_PATH || '/var/www/db/acl.db';
}

let membershipCache: { key: string; at: number; ok: boolean } | null = null;

/** Open the panel ACL read-only (query_only as an extra safeguard). */
function openPanelDb(): DatabaseSync | null {
  try {
    const db = new DatabaseSync(panelAclPath(), { readOnly: true });
    db.prepare('PRAGMA query_only').run();
    return db;
  } catch (e) {
    log.warning('Panel ACL unavailable: %s', e);
    return null;
  }
}

/** The panel stores md5(password) hex; this mirrors that check read-only. */
function panelMd5(input: string): string {
  return createHash('md5').update(input, 'utf8').digest('hex');
}

function sameHexConstantTime(a: string, b: string): boolean {
  const ab = Buffer.from(String(a), 'utf8');
  const bb = Buffer.from(String(b), 'utf8');
  if (ab.length !== bb.length) {
    // burn a comparison anyway so the length branch does not leak timing
    ab.equals(ab);
    return false;
  }
  return ab.equals(bb);
}

export interface PanelCredential {
  panelUserId: number;
  name: string | null;
}

/**
 * Verify a panel username + password. Returns the panel identity on
 * success, null otherwise (unknown user, wrong password, non-admin
 * membership, or an unreadable ACL — all fail closed and are
 * indistinguishable to the caller).
 */
export function verifyPanelCredential(loginName: string, password: string): PanelCredential | null {
  if (!loginName || !password) return null;
  const db = openPanelDb();
  if (!db) return null;
  try {
    const row = db
      .prepare('SELECT id, name, md5_password FROM acl_user WHERE name = ?')
      .get(loginName) as { id: number; name: string | null; md5_password: string } | undefined;
    if (!row) {
      sameHexConstantTime('0'.repeat(32), '1'.repeat(32)); // dummy work
      return null;
    }
    if (!sameHexConstantTime(String(row.md5_password || '').toLowerCase(), panelMd5(password))) return null;
    const grp = db
      .prepare('SELECT COUNT(*) AS n FROM acl_membership WHERE id_user = ? AND id_group = 1')
      .get(row.id) as { n: number } | undefined;
    if (!grp || Number(grp.n) <= 0) return null;
    return { panelUserId: Number(row.id), name: row.name ?? null };
  } catch (e) {
    log.warning('Panel credential check failed: %s', e);
    return null;
  } finally {
    db.close();
  }
}

interface DeskShadowUser {
  id: number;
  username: string;
  name: string | null;
  role: string;
  is_active: number;
}

async function shadowByExternalId(externalId: number): Promise<DeskShadowUser | null> {
  const row = await queryOne(
    'SELECT u.id, u.username, u.name, u.role, u.is_active FROM users u '
      + 'JOIN user_identities i ON i.user_id = u.id '
      + 'WHERE i.provider = ? AND i.external_id = ?',
    [PANEL_PROVIDER, String(externalId)],
  );
  return (row as unknown as DeskShadowUser) ?? null;
}

/**
 * Find-or-create the desk shadow user for a panel identity and refresh its
 * display metadata (rename never changes identity). Creation is
 * transactional (user + identity + default monitor mode) under the
 * UNIQUE(provider, external_id) constraint; a concurrent create collapses
 * to the existing row.
 */
export async function ensurePanelUser(panel: PanelCredential): Promise<DeskShadowUser | null> {
  const internalLogin = `panel:${panel.panelUserId}`;
  let shadow = await shadowByExternalId(panel.panelUserId);
  if (!shadow) {
    const { randomBytes } = await import('node:crypto');
    const unusableHash = await hashUserPassword(randomBytes(48).toString('hex'));
    try {
      await withTransaction(async (conn) => {
        const res = await execute(
          'INSERT INTO users (username, password_hash, name, role) VALUES (?, ?, ?, ?)',
          [internalLogin, unusableHash, panel.name ?? internalLogin, 'admin'],
          conn,
        );
        const userId = Number(res.insertId);
        if (!userId) throw new Error('shadow insert produced no id');
        await execute(
          'INSERT INTO user_identities (provider, external_id, user_id) VALUES (?, ?, ?)',
          [PANEL_PROVIDER, String(panel.panelUserId), String(userId)],
          conn,
        );
        await execute('INSERT INTO user_monitor_modes (user_id, mode) VALUES (?, ?)', [String(userId), 'listen'], conn);
      });
    } catch (e) {
      // UNIQUE race or transient error: fall through to the re-select.
      log.warning('Panel shadow create raced: %s', e);
    }
    shadow = await shadowByExternalId(panel.panelUserId);
  }
  if (!shadow) return null;
  if ((shadow.name ?? null) !== (panel.name ?? null)) {
    await execute('UPDATE users SET name = ? WHERE id = ?', [panel.name ?? internalLogin, String(shadow.id)]);
    shadow.name = panel.name ?? internalLogin;
  }
  return shadow;
}

/** Group-1 membership of a panel user (cached <=15s, fail-closed). */
export function panelMembershipValid(externalId: number): boolean {
  const key = String(externalId);
  const now = Date.now();
  if (membershipCache && membershipCache.key === key && now - membershipCache.at < MEMBERSHIP_CACHE_MS) {
    return membershipCache.ok;
  }
  const db = openPanelDb();
  if (!db) {
    membershipCache = { key, at: now, ok: false };
    return false;
  }
  try {
    const grp = db
      .prepare('SELECT COUNT(*) AS n FROM acl_membership WHERE id_user = ? AND id_group = 1')
      .get(externalId) as { n: number } | undefined;
    const ok = !!grp && Number(grp.n) > 0;
    membershipCache = { key, at: now, ok };
    return ok;
  } catch (e) {
    log.warning('Panel membership check failed: %s', e);
    membershipCache = { key, at: now, ok: false };
    return false;
  } finally {
    db.close();
  }
}

/** The panel external id behind a desk user (null when not panel-sourced). */
export async function panelExternalIdOfDeskUser(deskUserId: number): Promise<number | null> {
  const row = await queryOne(
    'SELECT external_id FROM user_identities WHERE provider = ? AND user_id = ?',
    [PANEL_PROVIDER, String(deskUserId)],
  );
  return row ? Number((row as unknown as { external_id: string }).external_id) : null;
}

/**
 * Guard for token resolution: a panel-sourced principal stays valid only
 * while its mapped panel identity still holds group-1 membership. A
 * panel marker without a mapping fails closed. Async (identity lookup).
 */
export async function panelSourceAllowed(deskUserId: number): Promise<boolean> {
  const ext = await panelExternalIdOfDeskUser(deskUserId);
  if (ext === null) return false;
  return panelMembershipValid(ext);
}

/** Tests only. */
export function resetPanelAuthCache(): void {
  membershipCache = null;
}
