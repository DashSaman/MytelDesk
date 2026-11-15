"""Pure host configuration helpers. Python 3.6+, standard library only."""


def parse_key_values(text):
    raise NotImplementedError("literal config parser not implemented")


def read_key_values(path):
    raise NotImplementedError("read-only config reader not implemented")


def detect_platform(os_release_text, asterisk_version_output, architecture="x86_64"):
    raise NotImplementedError("supported platform detector not implemented")


def normalize_base_path(base="/MytellDesk/"):
    raise NotImplementedError("canonical product base not implemented")


def validate_public_url(url, base="/MytellDesk/"):
    raise NotImplementedError("HTTPS public URL validation not implemented")


def render_apache(base="/MytellDesk/", backend_port=8765, asterisk_http_port=8088):
    raise NotImplementedError("scoped Apache configuration not implemented")


def render_systemd(node_path, release_dir, env_file, data_dir="/var/lib/myteldesk", service_user="asterisk", service_group="asterisk", protect_system=True, private_tmp=False):
    raise NotImplementedError("unprivileged systemd configuration not implemented")
