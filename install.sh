#!/usr/bin/env bash
# MytelDesk GPL-3.0 installer bootstrap. Host provisioning lives in installer/install.py.
set -Eeuo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Run as root on the existing PBX server.' >&2
  exit 1
fi
PYTHON=''
for candidate in /usr/bin/python3 /usr/libexec/platform-python; do
  if [[ -x "$candidate" ]]; then PYTHON="$candidate"; break; fi
done
if [[ -z "$PYTHON" ]]; then echo 'Python 3.6+ is required (Rocky platform-python is supported).' >&2; exit 1; fi
for command in curl tar xz; do
  if ! command -v "$command" >/dev/null 2>&1; then
    dnf -y install curl tar xz
    break
  fi
done
umask 077
work=$(mktemp -d /tmp/myteldesk-install.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
base=https://github.com/DashSaman/MytelDesk/releases
version=${MYTELDESK_VERSION:-}
if [[ -z "$version" ]]; then
  curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 --max-time 120 "$base/latest/download/VERSION" -o "$work/VERSION"
  version=$(tr -d '\r\n' < "$work/VERSION")
fi
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then echo 'Invalid release version.' >&2; exit 1; fi
artifact="myteldesk-$version.tar.gz"
echo "Downloading MytelDesk $version..."
curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 --max-time 900 "$base/download/v$version/$artifact" -o "$work/$artifact"
curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 --max-time 120 "$base/download/v$version/SHA256SUMS" -o "$work/SHA256SUMS"
"$PYTHON" -B - "$work" "$artifact" <<'PY'
import hashlib, os, pathlib, re, sys, tarfile
work=pathlib.Path(sys.argv[1]); artifact=sys.argv[2]
rows=[line.split() for line in (work/'SHA256SUMS').read_text().splitlines()]
checks=[row[0] for row in rows if len(row)==2 and row[1].lstrip('*')==artifact]
if len(checks)!=1 or not re.match(r'^[0-9a-f]{64}$',checks[0]): raise SystemExit('Invalid release checksum list')
h=hashlib.sha256()
with open(str(work/artifact),'rb') as source:
    for block in iter(lambda: source.read(1048576),b''): h.update(block)
if h.hexdigest()!=checks[0]: raise SystemExit('Release checksum mismatch')
with tarfile.open(str(work/artifact),'r:gz') as source:
    members=source.getmembers(); names=set()
    if len(members)>100000 or sum(max(0,m.size) for m in members)>2*1024**3: raise SystemExit('Release too large')
    for item in members:
        name=item.name.rstrip('/'); p=pathlib.PurePosixPath(name)
        if not name or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name or name in names or not (item.isfile() or item.isdir()): raise SystemExit('Unsafe release archive')
        names.add(name)
    for item in members:
        path=work/'bundle'/item.name
        if item.isdir(): path.mkdir(parents=True,exist_ok=True)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            with source.extractfile(item) as inp,open(str(path),'xb') as out:
                import shutil
                shutil.copyfileobj(inp,out)
            path.chmod(0o755 if item.mode&0o111 else 0o644)
PY
bundle="$work/bundle/myteldesk-$version"
"$PYTHON" -B "$bundle/installer/install.py" install --bundle "$bundle" "$@"
