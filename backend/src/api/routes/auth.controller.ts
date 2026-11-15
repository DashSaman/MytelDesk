/**
 * Sign-in and self-service credentials: POST /api/auth/login, GET /api/auth/me,
 * POST /api/me/password.
 *
 * Login accepts a username OR an extension with the bcrypt password and returns an HS256
 * JWT (24 h). There is no refresh and no logout endpoint: the client drops the token.
 */

import { Controller, Req } from '@nestjs/common';
import type { Request } from 'express';
import { setRedactedAuditDetail } from '../../core/audit';
import { getLogger } from '../../core/logger';
import {
  checkLoginRateLimit,
  clearLoginFailures,
  createAccessToken,
  getUserScope,
  isAgentUser,
  principalMonitorModes,
  recordLoginFailure,
  verifyPassword,
} from '../../core/security';
import {
  PANEL_AUTH_SOURCE,
  PANEL_TOKEN_TTL_SECONDS,
  ensurePanelUser,
  verifyPanelCredential,
} from '../../core/panel-auth';
import type { Principal } from '../../core/security';
import * as dbm from '../../db/db-manager';
import { clientHost } from '../client-host';
import { HttpError } from '../http-error';
import { respond } from '../respond';
import { Auth, Get, Post, principal } from '../routing';
import { parseBody, v } from '../validation';

const log = getLogger('api.routes.auth');

export const LoginBody = v.model({ login: v.str(), password: v.str() });
export const ChangePasswordBody = v.model({ current_password: v.str(), new_password: v.str() });

/** The public account fields of a principal (GET /api/auth/me), plus is_agent. */
export async function meBody(p: Principal): Promise<Record<string, unknown>> {
  return {
    id: p.id,
    username: p.username,
    name: p.name,
    role: p.role,
    extension: p.extension,
    monitor_modes: p.monitor_modes,
    allowed_agent_extensions: p.allowed_agent_extensions,
    allowed_queue_names: p.allowed_queue_names,
    is_agent: await isAgentUser(p),
  };
}

/**
 * _check_current_password: re-authenticate the caller (by the username in their token)
 * before they change a credential. 400 "Current password is incorrect" otherwise.
 */
export async function checkCurrentPassword(p: Principal, password: string): Promise<{ id: number; username: string }> {
  const user = await dbm.getUserByUsername(p.username || '');
  if (!user || !(await verifyPassword(password || '', user.password_hash || ''))) {
    throw new HttpError(400, 'Current password is incorrect');
  }
  return { id: Number(user.id), username: String(user.username) };
}

@Controller()
export class AuthController {
  /**
   * Login with extension or username and password.
   * Body: { "login": "ext_or_username", "password": "..." }
   * Returns: { access_token, token_type: "bearer", user: { id, username, name, role, extension,
   *            monitor_modes, allowed_agent_extensions, allowed_queue_names, is_agent } }
   */
  @Post('/api/auth/login')
  @Auth('public')
  async login(@Req() req: Request) {
    const body = parseBody(LoginBody, req.body);
    const clientIp = clientHost(req) ?? 'unknown';
    checkLoginRateLimit(clientIp);
    const login = (body.login || '').trim();
    const password = body.password || '';
    if (!login || !password) throw new HttpError(400, 'Login and password required');
    const user = await dbm.authenticateUser(login, password);
    if (!user) {
      recordLoginFailure(clientIp);
      throw new HttpError(401, 'Invalid extension/username or password');
    }
    clearLoginFailures(clientIp);
    const token = await createAccessToken({ id: user.id, username: user.username, role: user.role });
    const scope = await getUserScope(user.id);
    log.info('Login: %s (%s) from %s', user.username, user.role, clientIp);
    return {
      access_token: token,
      token_type: 'bearer',
      user: {
        id: user.id,
        username: user.username,
        name: user.name ?? null,
        role: user.role,
        extension: user.extension ?? null,
        monitor_modes: principalMonitorModes(scope),
        allowed_agent_extensions: scope.allowed_agent_extensions,
        allowed_queue_names: scope.allowed_queue_names,
        is_agent: await isAgentUser({ id: user.id, extension: user.extension }),
      },
    };
  }

  /**
   * The current user with role, extension and filter scope. Now includes `name`, so the
   * display name survives a reload (the frontend stores this body as the user).
   */

  /**
   * MyTellPBX v7.2.0 — sign in with Issabel PANEL credentials (admin group
   * only). Same request/response shape as the native login; failures are
   * indistinguishable from it. Tokens carry auth_source='panel' and a
   * 15-minute TTL; no refresh path. Throttling shares the native limiter
   * buckets (per client IP and per client-IP|login pair).
   */
  @Post('/api/auth/login-panel')
  @Auth('public')
  async loginPanel(@Req() req: Request) {
    const body = parseBody(LoginBody, req.body);
    const clientIp = clientHost(req) ?? 'unknown';
    const pair = `${clientIp}|${body.login || ''}`;
    checkLoginRateLimit(clientIp);
    checkLoginRateLimit(pair);
    const login = (body.login || '').trim();
    const password = body.password || '';
    if (!login || !password) throw new HttpError(400, 'Login and password required');
    const cred = verifyPanelCredential(login, password);
    if (!cred) {
      recordLoginFailure(clientIp);
      recordLoginFailure(pair);
      throw new HttpError(401, 'Invalid extension/username or password');
    }
    const shadow = await ensurePanelUser(cred);
    if (!shadow || !shadow.is_active) {
      // desk-side suspension is honored; still indistinguishable
      recordLoginFailure(clientIp);
      recordLoginFailure(pair);
      throw new HttpError(401, 'Invalid extension/username or password');
    }
    clearLoginFailures(clientIp);
    clearLoginFailures(pair);
    const token = await createAccessToken(
      { id: shadow.id, username: shadow.username, role: shadow.role },
      { authSource: PANEL_AUTH_SOURCE, ttlSeconds: PANEL_TOKEN_TTL_SECONDS },
    );
    const scope = await getUserScope(shadow.id);
    log.info('Login(panel): %s (%s) from %s', shadow.username, shadow.role, clientIp);
    return {
      access_token: token,
      token_type: 'bearer',
      auth_source: PANEL_AUTH_SOURCE,
      user: {
        id: shadow.id,
        username: shadow.username,
        name: shadow.name ?? null,
        role: shadow.role,
        extension: null,
        monitor_modes: principalMonitorModes(scope),
        allowed_agent_extensions: scope.allowed_agent_extensions,
        allowed_queue_names: scope.allowed_queue_names,
        is_agent: false,
      },
    };
  }

  @Get('/api/auth/me')
  @Auth('user')
  async me(@Req() req: Request) {
    return meBody(principal(req));
  }

  /** Change the caller's own OpDesk sign-in password (not the SIP secret). Enveloped. */
  @Post('/api/me/password')
  @Auth('user')
  async changeMyPassword(@Req() req: Request) {
    const body = parseBody(ChangePasswordBody, req.body);
    const user = await checkCurrentPassword(principal(req), body.current_password);
    const fresh = body.new_password || '';
    if (Array.from(fresh).length < 8) throw new HttpError(400, 'The new password needs at least 8 characters');
    if (!(await dbm.updateUser(user.id, { password: fresh }))) {
      throw new HttpError(500, 'Could not save the new password');
    }
    setRedactedAuditDetail(req, { user_id: user.id, password: 'changed' });
    return respond({ message: 'Password changed.' });
  }
}
