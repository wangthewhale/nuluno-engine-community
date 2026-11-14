import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import UncertainWrite, Waiting, WorkflowError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GraphClient:
    def __init__(self, base_url, token_provider, version, mode="live", timeout=20):
        parsed = urlparse(base_url)
        if mode == "fixture":
            if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
                raise ValueError("Fixture transport is restricted to loopback")
        elif parsed.scheme != "https" or parsed.hostname not in ("graph.facebook.com", "graph.instagram.com"):
            raise ValueError("Live transport must use the official HTTPS Graph host")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Graph base URL cannot contain credentials, query or fragment")
        self.base = base_url.rstrip("/")
        self.tokens, self.version, self.timeout = token_provider, version, timeout
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, params=None, authenticated=True):
        if not self.version:
            raise Waiting("尚未選定及驗證Graph API版本。", "waiting_capability")
        if path.startswith(("http:", "https:")) or ".." in path.split("/"):
            raise ValueError("Only relative Graph paths are accepted")
        values = {}
        for key, value in (params or {}).items():
            if value is None:
                continue
            values[key] = json.dumps(value, separators=(",", ":")) if isinstance(value, (dict, list, bool)) else str(value)
        query = urlencode(values)
        url = self.base + "/" + self.version + "/" + path.lstrip("/")
        headers = {"Accept": "application/json", "User-Agent": "meta-ig-growth-skills/0.2.0"}
        token = self.tokens.get() if authenticated else None
        secret_values = [token] if token else []
        secret_values.extend(str(v) for k, v in values.items() if k in ("client_secret", "app_secret", "code", "fb_exchange_token", "access_token"))
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None
        if method == "GET":
            url += ("?" + query) if query else ""
        else:
            body = query.encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            with self.opener.open(Request(url, body, headers, method=method), timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024)
                result = json.loads(raw.decode("utf-8"))
            if isinstance(result, dict) and result.get("error"):
                self._error(result, 400, method, {}, secret_values)
            return result
        except HTTPError as exc:
            try:
                result = json.loads(exc.read(1024 * 1024).decode("utf-8"))
            except (ValueError, UnicodeError):
                result = {"error": {"message": "Graph returned HTTP %s" % exc.code}}
            self._error(result, exc.code, method, exc.headers, secret_values)
        except (TimeoutError, socket.timeout, URLError, ConnectionError, OSError) as exc:
            message = "Graph network outcome unknown" if method != "GET" else "Graph read temporarily unavailable"
            if method != "GET":
                raise UncertainWrite(message) from exc
            raise WorkflowError(message, "transient") from exc
        except (ValueError, UnicodeError) as exc:
            if method != "GET":
                raise UncertainWrite("Write response could not be decoded") from exc
            raise WorkflowError("Graph response is not usable JSON", "transient") from exc

    def _error(self, result, status, method, headers, secret_values):
        error = result.get("error", {}) if isinstance(result, dict) else {}
        code = error.get("code")
        message = str(error.get("message", "Graph request failed"))
        for secret in secret_values:
            if secret:
                message = message.replace(secret, "[REDACTED]")
        details = {"http_status": status, "code": code, "subcode": error.get("error_subcode")}
        if code == 190 or status == 401:
            raise WorkflowError(message, "auth_expired", details=details)
        if status == 429 or code in (4, 17, 32, 613) or error.get("is_transient"):
            retry = headers.get("Retry-After") if headers else None
            try:
                retry = min(float(retry), 86400) if retry is not None else None
            except (TypeError, ValueError):
                retry = None
            raise WorkflowError(message, "transient", retry_after=retry, details=details)
        if status >= 500:
            if method != "GET":
                raise UncertainWrite(message, details=details)
            raise WorkflowError(message, "transient", details=details)
        if code in (10, 200, 294) or status == 403:
            raise Waiting(message, "waiting_owner", details=details)
        if code == 100:
            raise WorkflowError(message, "invalid_parameter", details=details)
        raise WorkflowError(message, "permanent", details=details)

    def all(self, path, params=None, max_pages=20):
        params = dict(params or {})
        records = []
        for _ in range(max_pages):
            result = self.request("GET", path, params)
            records.extend(result.get("data", []))
            paging = result.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not paging.get("next"):
                return records
            if not after or after == params.get("after"):
                raise WorkflowError("Pagination cannot be reconciled safely", "incomplete_read")
            params["after"] = after
        raise WorkflowError("Result exceeded the configured pagination limit", "incomplete_read")
