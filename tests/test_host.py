"""Pure installer boundary tests; no PBX, package manager or network access."""
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from installer import host


ROCKY_8 = 'ID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="8.10"\n'
SYSTEMD_ARGS = {
    "node_path": "/opt/myteldesk/runtime/node-v24.22.3-linux-x64/bin/node",
    "release_dir": "/opt/myteldesk/releases/v1.0.0",
    "env_file": "/etc/myteldesk/myteldesk.env",
    "data_dir": "/var/lib/myteldesk",
}


class ConfigTests(unittest.TestCase):
    def test_first_equals_only_and_literal_metacharacters(self):
        text = "mysqlrootpwd=abc=def==\nOTHER=$(printf example); `printf text`\nDOLLAR=$HOME\n"
        with mock.patch("os.system", side_effect=AssertionError("execution forbidden")):
            self.assertEqual(host.parse_key_values(text), {
                "mysqlrootpwd": "abc=def==", "OTHER": "$(printf example); `printf text`", "DOLLAR": "$HOME"})

    def test_quotes_comments_whitespace_and_export(self):
        text = "# comment\r\n ; another comment\r\n export TOKEN = 'a # b=c' # note\r\nEMPTY=\r\nNAME= desk # comment\r\nHASH=a#b\r\n"
        self.assertEqual(host.parse_key_values(text), {"TOKEN": "a # b=c", "EMPTY": "", "NAME": "desk", "HASH": "a#b"})

    def test_duplicate_last_wins_and_unrelated_lines_ignored(self):
        self.assertEqual(host.parse_key_values("A=first\n[section]\nnot a statement\nBAD KEY=x\nA=last\n"), {"A": "last"})

    def test_quoted_password_is_literal_not_shell_unescaped(self):
        self.assertEqual(host.parse_key_values('KEY="literal\\n$VALUE=still"\n'), {"KEY": "literal\\n$VALUE=still"})

    def test_malformed_quotes_fail_without_secret_in_exception(self):
        for text in ("KEY='private-example", 'KEY="private-example" junk'):
            with self.subTest(text=text):
                with self.assertRaises(ValueError) as raised:
                    host.parse_key_values(text)
                self.assertNotIn("private-example", str(raised.exception))

    def test_read_config_is_only_a_mapping_and_does_not_modify_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "issabel.conf"
            value = b"mysqlrootpwd=fixture-only==\n"
            path.write_bytes(value)
            self.assertEqual(host.read_key_values(str(path)), {"mysqlrootpwd": "fixture-only=="})
            self.assertEqual(path.read_bytes(), value)

    def test_missing_file_is_not_an_empty_success(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(OSError):
                host.read_key_values(os.path.join(temp, "missing.conf"))


class PlatformTests(unittest.TestCase):
    def test_authorized_version_matrix_without_private_host_details(self):
        for os_version, ast_version in (("8.10", "20.12.0"), ("8.8", "18.19.0"), ("8.8", "16.30.1"), ("9.4", "20.7.0")):
            with self.subTest(os_version=os_version, ast_version=ast_version):
                actual = host.detect_platform('ID=rocky\nVERSION_ID=' + os_version, "Asterisk " + ast_version)
                self.assertEqual(actual, {"os": "rocky", "os_major": int(os_version[0]), "os_version": os_version,
                                          "asterisk_major": int(ast_version.split(".")[0]), "asterisk_version": ast_version,
                                          "arch": "x86_64"})

    def test_certified_version_preserves_suffix(self):
        result = host.detect_platform(ROCKY_8, "Asterisk certified/18.9-cert12\n")
        self.assertEqual(result["asterisk_major"], 18)
        self.assertEqual(result["asterisk_version"], "18.9-cert12")

    def test_unsupported_distribution_or_os_major_is_rejected(self):
        for text in ("ID=centos\nVERSION_ID=8", "ID=rocky\nVERSION_ID=7.9", "ID=rocky\nVERSION_ID=10", "ID=rocky", "ID=rocky\nVERSION_ID=8.evil"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                host.detect_platform(text, "Asterisk 20.12.0")

    def test_wrong_or_unknown_asterisk_version_is_rejected(self):
        for version in ("Asterisk 22.0.0", "Asterisk 13.38.0", "not installed", "Asterisk GIT-unknown", "20.12.0"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                host.detect_platform(ROCKY_8, version)

    def test_unsupported_architecture_is_rejected(self):
        with self.assertRaises(ValueError):
            host.detect_platform(ROCKY_8, "Asterisk 20.12.0", "aarch64")


class PublicUrlTests(unittest.TestCase):
    def test_base_has_one_canonical_form(self):
        self.assertEqual(host.normalize_base_path(), "/MytellDesk/")
        self.assertEqual(host.normalize_base_path("/MytellDesk"), "/MytellDesk/")
        for base in ("/", "MytellDesk", "/mytelldesk/", "/MytellDesk//", "/MytellDesk/../", "/MytellDesk/\nProxyPass /api/ bad"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                host.normalize_base_path(base)

    def test_https_origin_or_product_base_normalizes(self):
        for value in ("https://PBX.example.test:8443", "https://PBX.example.test:8443/", "https://PBX.example.test:8443/MytellDesk", "https://PBX.example.test:8443/MytellDesk/"):
            with self.subTest(value=value):
                self.assertEqual(host.validate_public_url(value), "https://pbx.example.test:8443/MytellDesk/")

    def test_documentation_ip_and_ipv6_are_accepted(self):
        self.assertEqual(host.validate_public_url("https://192.0.2.10"), "https://192.0.2.10/MytellDesk/")
        self.assertEqual(host.validate_public_url("https://[2001:db8::1]:8443"), "https://[2001:db8::1]:8443/MytellDesk/")

    def test_unsafe_or_ambiguous_urls_are_rejected_without_echo(self):
        values = ("http://pbx.example.test", "https:///missing", "https://bad host.test", "https://pbx.example.test:0", "https://pbx.example.test:65536",
                  "https://admin:private-example@pbx.example.test", "https://pbx.example.test/?x=1", "https://pbx.example.test/#t=private-example",
                  "https://pbx.example.test/other", "https://pbx.example.test/%4dytellDesk/", "https://pbx.example.test\n.evil", "https://pbx.example.test\\evil")
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as raised:
                    host.validate_public_url(value)
                self.assertNotIn("private-example", str(raised.exception))


class ApacheTests(unittest.TestCase):
    def test_routes_preserve_other_products_and_order_websockets_first(self):
        config = host.render_apache()
        routes = re.findall(r'^ProxyPass\s+"([^"]+)"\s+"([^"]+)"', config, re.M)
        self.assertEqual(routes, [
            ("/MytellDesk/api/", "http://127.0.0.1:8765/api/"),
            ("/MytellDesk/ws", "ws://127.0.0.1:8765/ws"),
            ("/MytellDesk/sip-ws", "ws://127.0.0.1:8088/ws"),
            ("/MytellDesk/", "http://127.0.0.1:8765/"),
        ])
        reverse = re.findall(r'^ProxyPassReverse\s+"([^"]+)"\s+"([^"]+)"', config, re.M)
        self.assertEqual(reverse, routes)
        self.assertNotRegex(config, r'(?m)^ProxyPass\s+"?/(api|ws|sip-ws)(/|\s|\")')
        self.assertNotIn("ProxyWebsocketFallbackToProxyHttp", config)

    def test_nondefault_ports_are_validated_then_rendered(self):
        value = host.render_apache(backend_port=18765, asterisk_http_port=18088)
        self.assertIn("127.0.0.1:18765/api/", value)
        self.assertIn("127.0.0.1:18088/ws", value)
        for port in (0, 65536, True, "8765\nRequire all granted"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                host.render_apache(backend_port=port)

    def test_no_global_httpd_firewall_or_selinux_configuration(self):
        config = host.render_apache()
        for forbidden in ("Listen ", "<VirtualHost", "DocumentRoot", "setsebool", "SELINUX=", "firewall-cmd", "ProxyRequests"):
            self.assertNotIn(forbidden, config)


class SystemdTests(unittest.TestCase):
    def test_pinned_runtime_backend_cwd_and_external_secrets(self):
        unit = host.render_systemd(**SYSTEMD_ARGS)
        self.assertIn("WorkingDirectory=/opt/myteldesk/releases/v1.0.0/backend\n", unit)
        self.assertIn("ExecStart=/opt/myteldesk/runtime/node-v24.22.3-linux-x64/bin/node /opt/myteldesk/releases/v1.0.0/backend/dist/main.js\n", unit)
        self.assertIn("EnvironmentFile=/etc/myteldesk/myteldesk.env\n", unit)
        self.assertNotIn("/bin/bash", unit)
        self.assertNotIn("sudo", unit)
        self.assertNotIn("npm", unit)

    def test_default_is_pbx_user_and_no_privilege_escalation(self):
        unit = host.render_systemd(**SYSTEMD_ARGS)
        for required in ("User=asterisk\n", "Group=asterisk\n", "NoNewPrivileges=yes\n", "UMask=0007\n", "ProtectSystem=full\n", "PrivateTmp=no\n"):
            self.assertIn(required, unit)

    def test_write_exceptions_are_only_pbx_paths_and_data_directory(self):
        unit = host.render_systemd(**SYSTEMD_ARGS)
        paths = re.search(r'^ReadWritePaths=(.*)$', unit, re.M).group(1).split()
        self.assertEqual(set(paths), {"-/etc/asterisk", "-/var/lib/asterisk", "-/var/spool/asterisk", "-/var/log/asterisk", "/var/lib/myteldesk"})
        self.assertNotIn("ReadWritePaths=/", unit)

    def test_private_tmp_and_protection_are_explicit_options(self):
        unit = host.render_systemd(private_tmp=True, protect_system=False, **SYSTEMD_ARGS)
        self.assertIn("PrivateTmp=yes\n", unit)
        self.assertIn("ProtectSystem=no\n", unit)
        self.assertNotIn("ReadWritePaths=", unit)

    def test_cannot_render_root_service(self):
        for key in ("service_user", "service_group"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                host.render_systemd(**dict(SYSTEMD_ARGS, **{key: "root"}))

    def test_path_and_identity_directive_injection_are_rejected(self):
        for key in ("node_path", "release_dir", "env_file", "data_dir"):
            for bad in ("relative", "/", "/opt/../etc", "/opt/path with space", "/opt/%n", "/opt/path\nUser=root"):
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                    host.render_systemd(**dict(SYSTEMD_ARGS, **{key: bad}))
        with self.assertRaises(ValueError):
            host.render_systemd(service_user="asterisk\nUser=root", **SYSTEMD_ARGS)


if __name__ == "__main__":
    unittest.main()
