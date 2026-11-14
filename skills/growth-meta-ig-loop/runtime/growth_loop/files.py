import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


SECRET_KEYS = re.compile(r"^(access_token|refresh_token|id_token|session_token|api_key|secret_key|token|client_secret|app_secret|authorization|password|code|fb_exchange_token)$", re.I)
TOKEN_TEXT = re.compile(r"(?i)(access_token|client_secret|app_secret|fb_exchange_token)([=:\s]+)([^\s&\"']+)")


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_KEYS.match(str(k)) and not (str(k).lower() == "code" and isinstance(v, int)) else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        value = TOKEN_TEXT.sub(r"\1\2[REDACTED]", value)
        for secret in secrets:
            if isinstance(secret, str) and len(secret) >= 4:
                value = value.replace(secret, "[REDACTED]")
        return value
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    raw = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_digest(path):
    return digest(Path(path).read_bytes())


def atomic_json(path, value, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600 if private else 0o644)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def safe_path(root, relative):
    root = Path(root).resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path must remain inside the workspace")
    return candidate
