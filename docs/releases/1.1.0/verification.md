# MytelDesk 1.1.0 verification

This work extends 1.0.0 with automatic CPU VAD provisioning, verified browser
calling and a consistent Persian/RTL interface. This document is an evidence
index. Build checks, laboratory acceptance and external-provider limitations are
reported separately so a simulated integration is never presented as live delivery.

## Acceptance journeys

- A fresh Issabel installation provisions the isolated Python runtime, locked VAD
  dependencies and verified model automatically. Startup and doctor exercise it
  as the service account. Re-running preserves accounts, secrets and PBX policy.
- Two browser endpoints establish real SIP calls through Asterisk 16, 18 and 20.
  Each receiver must decode the other endpoint's distinct generated audio;
  registration, SIP success and RTP byte counters alone do not prove audio.
- Call controls, queue/presence, recording and VAD results are exercised with
  uniquely owned test fixtures. Existing trunks, contexts and user records are
  preserved; cleanup removes only tagged fixtures.
- All application pages and Settings sections are checked with populated, empty,
  loading and error states, appropriate roles, keyboard input, Persian/RTL,
  desktop/mobile layouts and light/dark themes.
- Integration contracts use controlled test services. A simulated CRM/push
  endpoint does not establish delivery to an unconfigured third-party provider.
- The final artifact includes corresponding source, dependency/model notices and
  checksums, passes automated checks, and is tested through the public installer.

## TDD evidence

| Behavior | Executed target | RED | GREEN |
|---|---|---|---|
| Empty or legacy WebRTC server settings retain the installed URL prefix; a configured public port appears once | `npm test -- --runInBand softphone-routes` | 3 failed, 37 passed; runtime assertions reproduce the wrong paths/duplicate ports | 40 passed; stored custom URLs and legacy deployments remain covered |
| CPU VAD provisioning verifies packages/model, isolates the runtime and rejects unsafe ownership | `python -m unittest tests.test_vad_install` and worker test modules | Missing runtime integration, corrupt-cache retry, ownership and unavailable unrelated-repository cases reproduced before their fixes | Pinned wheels, official Rocky repositories scoped to one command, CPU inference and safe resumable generations covered |
| Save/upgrade/retry installs and probes VAD without replacing operator settings or credentials | `python -m unittest tests.test_install_vad_lifecycle` | 6 initial lifecycle failures; additional interrupted-bootstrap and operator-ENV regressions committed separately | 9 lifecycle tests, including pre-activation failure and rollback protections |
| An administrator receives a bounded, sanitized VAD capability result | `npm test -- --runInBand vad-health` | 10 failed, 1 passed before implementation | 11 passed; timeout, malformed output, hash mismatch, deduplication and cache expiry covered |
| Active-call keypad sends only valid DTMF on an established session | `node scripts/test-web-dtmf.cjs` | Missing control and session guards reproduced before implementation | 9 browser assertions; actual Asterisk Read acceptance is verified separately |
| Persian dates use Jalali civil dates while the API keeps ISO dates | `node scripts/test-web-calendar.cjs` | Gregorian-only input and Persian digit/date-boundary defects reproduced before implementation | 15 browser assertions across time zones, leap Esfand, bounds and English fallback |
| Product forms handle rejected requests and repeated submission explicitly | Product and journey browser suites under `scripts/` | Load-error, duplicate-submit, stale-response and permission-state regressions reproduced | Populated, empty, loading, error, keyboard, role and responsive journeys exercised |
| Recording playback/download preserves absolute file identity through Apache while enforcing the existing sandbox and call visibility | Recording HTTP/scope tests, transport/worker suite and native recording browser fixture | 4 transport failures and 13 missing-route failures; actual HTTPS reproduced Apache rejection of encoded path separators | 28 HTTP/scope, 32 transport/worker and 4 native playback/download cases passed; authenticated query transport shares the existing wildcard serving/authentication logic |

The initial reproducer is on the active development branch at `d25f6b2`.
Operational evidence stays private when it contains host identifiers or call data.

## Release checks

- Production dependencies are audited separately from development tools. Neither
  a successful production audit nor a browser fixture establishes that every
  development dependency or third-party integration is free of defects.
- Render coverage includes 35 application views at three widths in both themes
  (210 cases). Browser checks also cover report dialogs, settings subsections,
  RTL navigation, reduced motion and accessibility.
- Real-media acceptance uses generated tones and locally synthesized speech,
  dedicated laboratory accounts and additive dialplan fixtures. Decoded audio,
  call controls, DTMF, queue/supervision, per-leg recording, CPU VAD and the
  authenticated call-history timeline are checked independently of unit tests.
- Existing administrator identities, application secrets, system Python,
  unrelated PBX configuration and the Asterisk process are preservation checks.
  Only the application service is restarted for an application upgrade.

See the published release notes for final artifact checksums, test totals and
the completed Asterisk version matrix. External CRM, push, SMTP and certificate
providers require the operator's configured credentials and reachable service;
controlled endpoint tests do not certify those providers.
