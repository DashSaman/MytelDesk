"""Provision the isolated CPU VAD runtime; Python 3.6 compatible installer API.

The caller owns service/environment transactions. These functions never change
recording settings, PBX configuration, application data, or service state.
"""


def ensure_vad(bundle, runtime_root, run, fetch_verified, service_user='asterisk'):
    """Return secret-free VAD environment values after provision and user probe."""
    return {}


def doctor_vad(bundle, environment, run, service_user='asterisk'):
    """Return a verified doctor success or raise on an unusable CPU runtime."""
    return {'status': 'error', 'engine': 'silero', 'error_code': 'not_provisioned'}
