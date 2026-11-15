# Upstream and license notices

MytelDesk is a modified distribution of **OpDesk**, originally authored by
**Ibrahim Gamal**. The upstream project is
<https://github.com/Ibrahimgamal99/OpDesk>.

The source imported from the installed product includes the upstream GNU General
Public License, version 3, in [LICENSE](LICENSE). That license and the original
copyright and attribution notices remain applicable. MytelDesk modifications are
distributed under GPLv3 as part of this derivative work. This repository does
not claim ownership of the upstream authors' work or replace dependency licenses.

The initial import was taken from the existing MyTel deployment, rather than
represented as an unmodified checkout of an upstream commit. Its original
upstream commit was not recorded in that deployment. Release archives identify
the exact corresponding MytelDesk source tree in `SOURCE_TREE`, with file hashes
in `manifest.json`.

## MyTel changes

**2026-11-15:** prepared the standalone MytelDesk distribution and installer for
existing Issabel systems; retained the Persian/RTL MyTel operator workspace;
corrected application-relative HTTP, WebSocket and service-worker routing;
improved accessible dialogs and controls; included deployment and release
validation, host-independent corresponding source, and third-party notices.
Individual modifications and subsequent dates are recorded in Git history.

## Third-party materials

Dependencies keep their own licenses. `backend/package-lock.json` and
`frontend/package-lock.json` identify their pinned package sources and versions.
The release builder preserves full installed package LICENSE, COPYING, NOTICE
and copyright texts in `THIRD_PARTY_LICENSES/`, with an inventory in
`THIRD_PARTY_LICENSES/index.json`. A package README is retained as a fallback
when the package does not supply a standalone license text. SPDX metadata in
that inventory is descriptive and does not replace those texts.

The backend includes **web-push**, licensed under **MPL-2.0**. Its original
JavaScript source and LICENSE are included with the release runtime. Other
dependencies may use different licenses; they are not relicensed as GPLv3.

The bundled **Vazirmatn** font files, including the variable font, are by the
Vazirmatn Project Authors and retain the **SIL Open Font License 1.1** in
[`frontend/public/fonts/OFL.txt`](frontend/public/fonts/OFL.txt). The upstream
font project is <https://github.com/rastikerdar/vazirmatn>; the license text is
retained from that project's `OFL.txt`.

Existing application icons and audio tones are retained from the installed
derivative. No separate commercial license or ownership claim is introduced for
them by this distribution.
