# Supplemental dependency notices

Some older npm releases omit a standalone license text. The release preserves
their original package metadata and README in `THIRD_PARTY_LICENSES/`; this
directory supplies additional upstream texts and verifiable permission evidence.
It does not change any dependency's license.

- `http_ece@1.2.0`: the MIT license is declared in its published `package.json`.
  The full original license is retained from the author's exact `v1.2.0` tag.
- `saxes@5.0.1`: the ISC license is declared in its published `package.json`.
  The full original notice, including its inherited notices, is retained from
  the exact `v5.0.1` tag.
- `buffers@0.1.1`: its published npm metadata omits the license. The preserved
  original upstream commit `1b745ee35d33eb166e15ef1866073a07c6d7de87`
  explicitly declares **MIT/X11**, version **0.1.1**, and author **James Halliday**.
  That commit survives in the BitPay fork of the original repository. Its
  `index.js` is byte-for-byte identical to the npm runtime: SHA256
  `97801e296ba5f3c32242f647e1daac5917e300367fed7935ab1e62f40adb1b8a`.
  The exact upstream `package.json` is included here as permission evidence.
- `binary@0.3.0` and `chainsaw@0.1.0` declare MIT and MIT/X11 respectively in
  their unmodified npm package metadata, identifying James Halliday as author.
  `substack-MIT-permission.txt` supplies the standard MIT permission text with
  that attribution for these packages and `buffers`; no unrecorded copyright
  year is asserted. The standard text comes from the SPDX MIT license template.

Original download URLs and SHA256 hashes for the retained upstream files are in
`sources.json`. The historical `buffers` license declaration is also documented
by Debian's official package archive:
<https://sources.debian.org/copyright/license/node-buffers/0.1.1-2/>.
