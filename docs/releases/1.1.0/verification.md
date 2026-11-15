# MytelDesk 1.1.0 verification plan

This work extends 1.0.0 with automatic CPU VAD provisioning, verified browser
calling and a consistent Persian/RTL interface. This document is an evidence
index; unfinished checks below are not release approval.

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

The initial reproducer is on the active development branch at `d25f6b2`.
Operational evidence stays private when it contains host identifiers or call data.
