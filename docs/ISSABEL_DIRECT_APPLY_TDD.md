# Standalone Issabel Apply completion (unpublished 1.1.1 R3)

The diagnostic installation proved that `retrieve_conf` followed by `core reload`
left native Issabel `need_reload=true` after a successful mapped-user save.
Standalone Apply now calls native `do_reload()` once through a fixed PHP CLI
adapter. Success requires native boolean status, zero integer errors and an
independent read of the cleared native pending flag. The adapter does not write
that flag itself or invoke root-only `amportal`.

| Evidence | Result |
| --- | --- |
| RED checkpoint `cdfd8ac`, Jest `direct-pbx.spec.ts` + `direct-pbx-apply.spec.ts` | 22 tests executed: 17 expected failures, 5 controls passed |
| Same focused Jest suite after repair | 22/22 passed |
| Focused suite plus `users-routes.spec.ts` and `pbx-settings-routes.spec.ts` | 79/79 passed |
| `npm run typecheck` and `npm run build` | Passed |
| Native PHP7.4 inert `issabel-reload-contract.php` fixture, executed by the isolated VM operator | 31/31 passed; adapter and fixture lint passed |
| Independent source review | Confirmed native bootstrap, strict result/readback, fixed script path and included release payload; independently reran 22 Jest tests and typecheck |

The focused Jest coverage run exercised all nine instrumented statements in the
new direct-native branch (lines 323–332 at this checkpoint). This is a changed
branch statement observation, not overall application or PHP coverage: the
focused run covers only 26.42% of the larger dialplan module's lines. The PHP
fixture imports the adapter inertly and tests its result/DB-error boundary with
stubs; it does not load native PBX configuration or perform a reload. Actual
mapped-user Apply and full R3 artifact/native acceptance remain release gates.

Old R2 source/artifact evidence stays intact. This source repair does not certify
real calls, audio, recording, VAD, or a final pristine installation.
