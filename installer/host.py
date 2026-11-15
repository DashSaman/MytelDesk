"""Pure installer helpers, compatible with Python 3.6 and the standard library.

Nothing executes commands, changes files or reads configuration at import time.
The caller owns preflight, filesystem permissions, backups and service activation.
"""
import ipaddress
import os
import re
from urllib.parse import urlsplit


BASE_PATH = "/MytellDesk/"


def _literal_value(value, line_number):
    value = value.strip()
    if not value or value[0] not in "\"'":
        return re.sub(r"\s+#.*$", "", value).rstrip()
    quote, escaped = value[0], False
    for index in range(1, len(value)):
        char = value[index]
        if char == quote and not escaped:
            tail = value[index + 1:].strip()
            if not tail or tail.startswith("#"):
                return value[1:index]
            break
        escaped = char == "\\" and not escaped
    # Never include the value: it may be a database password.
    raise ValueError("Malformed quoted configuration at line {}".format(line_number))


def parse_key_values(text):
    """Read literal KEY=value assignments; the last assignment wins.

    Optional outer quotes/export and whitespace-prefixed # comments are accepted.
    Values are never evaluated, expanded or backslash-unescaped. Other lines are
    ignored. An unfinished quoted assignment fails instead of guessing a secret.
    """
    if not isinstance(text, str) or "\x00" in text:
        raise ValueError("Configuration must be text without NUL characters")
    result = {}
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if match:
            result[match.group(1)] = _literal_value(match.group(2), number)
    return result


def read_key_values(path):
    """Read a config such as /etc/issabel.conf without sourcing or logging it."""
    with open(path, "r", encoding="utf-8-sig") as source:
        return parse_key_values(source.read())


def detect_platform(os_release_text, asterisk_version_output, architecture="x86_64"):
    """Validate fixture/command outputs, without executing probes or changing PBX."""
    release = parse_key_values(os_release_text)
    version = release.get("VERSION_ID", "")
    if release.get("ID", "").lower() != "rocky" or not re.match(r"^[0-9]+(?:\.[0-9]+)*$", version):
        raise ValueError("A recognized Rocky Linux version is required")
    major = int(version.split(".")[0])
    if major not in (8, 9):
        raise ValueError("Only Rocky Linux 8 and 9 are supported")
    if architecture != "x86_64":
        raise ValueError("Only x86_64 is supported by this runtime bundle")
    match = re.search(r"^\s*Asterisk\s+(?:certified/)?([0-9]+\.[0-9]+(?:\.[0-9]+)*(?:[-+][A-Za-z0-9._-]+)?)(?:\s|$)",
                      asterisk_version_output, re.M)
    if not match or int(match.group(1).split(".")[0]) not in (16, 18, 20):
        raise ValueError("A recognized Asterisk 16, 18 or 20 version is required")
    return {"os": "rocky", "os_major": major, "os_version": version,
            "asterisk_major": int(match.group(1).split(".")[0]),
            "asterisk_version": match.group(1), "arch": architecture}


def normalize_base_path(base="/MytellDesk/"):
    """Keep the deployed product URL fixed; never turn it into a root proxy."""
    if base not in (BASE_PATH, BASE_PATH.rstrip("/")):
        raise ValueError("The product base must be /MytellDesk/")
    return BASE_PATH


def _public_host(hostname):
    if ":" in hostname:
        return "[{}]".format(ipaddress.IPv6Address(hostname).compressed)
    hostname = hostname.encode("idna").decode("ascii").lower()
    if hostname.endswith("."):
        hostname = hostname[:-1]
    labels = hostname.split(".")
    if len(hostname) > 253 or any(not re.match(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", label) for label in labels):
        raise ValueError("Invalid hostname")
    if len(labels) == 4 and all(label.isdigit() for label in labels):
        hostname = str(ipaddress.IPv4Address(hostname))
    return hostname


def validate_public_url(url, base="/MytellDesk/"):
    """Canonical HTTPS origin + product base, without userinfo/query/fragment."""
    base = normalize_base_path(base)
    try:
        if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 for char in url) or any(char in url for char in "\\?#"):
            raise ValueError("Invalid URL characters")
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError("Invalid URL origin")
        if parts.path not in ("", "/", base, base.rstrip("/")) or parts.netloc.endswith(":"):
            raise ValueError("Invalid URL path or port")
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Invalid URL port")
        authority = _public_host(parts.hostname)
        if port is not None:
            authority += ":{}".format(port)
        return "https://" + authority + base
    except (ValueError, UnicodeError):
        # A rejected URL might contain userinfo or a fragment token. Do not echo it.
        raise ValueError("Public URL must be an HTTPS origin or /MytellDesk/ URL without credentials, query or fragment") from None


def _port(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("Port must be an integer from 1 to 65535")
    return value


def render_apache(base="/MytellDesk/", backend_port=8765, asterisk_http_port=8088):
    """Render only product-prefixed routes, compatible with httpd 2.4.37."""
    base = normalize_base_path(base)
    backend = "127.0.0.1:{}".format(_port(backend_port))
    asterisk = "127.0.0.1:{}".format(_port(asterisk_http_port))
    routes = ((base + "api/", "http://" + backend + "/api/"),
              (base + "ws", "ws://" + backend + "/ws"),
              (base + "sip-ws", "ws://" + asterisk + "/ws"),
              (base, "http://" + backend + "/"))
    lines = ["# MytelDesk: owned URL prefix only; keep websocket routes before the catch-all."]
    for source, destination in routes:
        lines.append('ProxyPass "{}" "{}"'.format(source, destination))
        lines.append('ProxyPassReverse "{}" "{}"'.format(source, destination))
    lines.extend(['RedirectMatch 301 "^/MytellDesk$" "/MytellDesk/"',
                  '<Location "/MytellDesk/">', "    ProxyPreserveHost On", "</Location>"])
    return "\n".join(lines) + "\n"


def _absolute_path(value):
    value = os.fspath(value)
    if not isinstance(value, str) or not re.match(r"^/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$", value) or any(part in (".", "..") for part in value.split("/")):
        raise ValueError("An absolute path without whitespace, traversal or service specifiers is required")
    return value


def _service_identity(value):
    if value == "root" or not re.match(r"^[a-z_][a-z0-9_-]*$", value):
        raise ValueError("An unprivileged service account and group are required")
    return value


def render_systemd(node_path, release_dir, env_file, data_dir="/var/lib/myteldesk",
                   service_user="asterisk", service_group="asterisk", protect_system=True, private_tmp=False):
    """Render a prebuilt backend service; the env file owns runtime secrets.

    PrivateTmp defaults off: PBX helpers can share temporary files with Apache or
    Asterisk. Enable it only after validating those integration paths. ProtectSystem
    full makes /etc read-only; only existing Asterisk paths and app data are excepted.
    """
    node_path, release_dir, env_file, data_dir = [_absolute_path(value) for value in (node_path, release_dir, env_file, data_dir)]
    service_user, service_group = _service_identity(service_user), _service_identity(service_group)
    lines = ["[Unit]", "Description=MytelDesk operator workspace",
             "After=network-online.target mariadb.service asterisk.service httpd.service", "Wants=network-online.target", "",
             "[Service]", "Type=simple", "User=" + service_user, "Group=" + service_group,
             "WorkingDirectory=" + release_dir + "/backend", "EnvironmentFile=" + env_file,
             "Environment=NODE_ENV=production", "ExecStart=" + node_path + " " + release_dir + "/backend/dist/main.js",
             "NoNewPrivileges=yes", "UMask=0007", "ProtectSystem=" + ("full" if protect_system else "no"),
             "PrivateTmp=" + ("yes" if private_tmp else "no")]
    if protect_system:
        lines.append("ReadWritePaths=-/etc/asterisk -/var/lib/asterisk -/var/spool/asterisk -/var/log/asterisk " + data_dir)
    lines.extend(["Restart=on-failure", "RestartSec=5", "KillMode=mixed", "TimeoutStopSec=30", "",
                  "[Install]", "WantedBy=multi-user.target"])
    return "\n".join(lines) + "\n"
