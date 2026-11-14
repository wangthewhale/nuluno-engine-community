import hmac
import json
import os
import secrets
import time
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

from .errors import Waiting, WorkflowError
from .files import atomic_json, read_json


class TokenProvider:
    def __init__(self, root, env_name="META_ACCESS_TOKEN", refresh_callback=None):
        self.path = Path(root) / "credentials.json"
        self.env_name = env_name
        self.refresh_callback = refresh_callback
        self._fresh = None

    def get(self):
        token = self._fresh
        environment = os.environ.get(self.env_name)
        saved = read_json(self.path) if self.path.exists() else {}
        if not token and environment:
            current_hash = hashlib.sha256(environment.encode()).hexdigest()
            token = saved.get("access_token") if saved.get("source_env_sha256") == current_hash else environment
        if not token:
            token = saved.get("access_token")
        if not token:
            raise Waiting("缺有效登入授權；使用官方登入，憑證不要貼到聊天。", "waiting_owner")
        return token

    def save(self, result):
        if not result.get("access_token"):
            raise WorkflowError("Authorization response has no usable token", "auth_failed")
        result = dict(result)
        environment = os.environ.get(self.env_name)
        result["source_env_sha256"] = hashlib.sha256(environment.encode()).hexdigest() if environment else None
        atomic_json(self.path, result, private=True)
        self._fresh = result["access_token"]

    def refresh(self):
        if not self.refresh_callback:
            raise Waiting("登入失效且沒有已配置的正常更新路徑；保存原工作並重新授權。", "waiting_owner")
        result = self.refresh_callback(self.get())
        self.save(result)
        return True


def begin_oauth(app_id, redirect_uri, scopes, state_path, clock=time.time):
    parsed = urlparse(redirect_uri)
    if not app_id or parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.query:
        raise ValueError("Use a registered loopback callback URI and actual app ID")
    state = secrets.token_urlsafe(32)
    atomic_json(state_path, {"state": state, "redirect_uri": redirect_uri, "app_id": app_id, "expires_at": clock() + 600}, private=True)
    return "https://www.facebook.com/dialog/oauth?" + urlencode({
        "client_id": app_id, "redirect_uri": redirect_uri,
        "state": state, "scope": ",".join(scopes), "response_type": "code",
    })


def validate_callback(state_path, returned_state, clock=time.time):
    saved = read_json(state_path)
    if saved.get("expires_at", 0) <= clock():
        raise WorkflowError("OAuth callback expired; restart normal sign-in", "auth_failed")
    if not hmac.compare_digest(saved["state"], returned_state or ""):
        raise WorkflowError("OAuth state did not match; callback rejected", "auth_failed")
    Path(state_path).unlink()
    return saved


def receive_oauth(profile, workspace, port=8765, emit=print):
    """Normal official consent flow; no passwords/codes are accepted in chat."""
    from .transport import GraphClient
    auth = profile.get("auth", {})
    app_id = os.environ.get(auth.get("app_id_env", "META_APP_ID"))
    secret = os.environ.get(auth.get("app_secret_env", "META_APP_SECRET"))
    version = profile.get("graph", {}).get("version")
    if not app_id or not secret or not version:
        raise Waiting("正常登入需要已配置Meta App、回呼URI與已驗證API版本；不自行建立新權限或猜憑證。", "waiting_capability")
    redirect = "http://127.0.0.1:%s/callback" % port
    registered = auth.get("registered_redirect_uri")
    if registered != redirect:
        raise Waiting("此回呼URI尚未在設定中確認為已註冊；先完成該項設定。", "waiting_capability")
    state_path = Path(workspace) / "oauth-state.json"
    tokens = TokenProvider(workspace, auth.get("token_env", "META_ACCESS_TOKEN"))
    client = GraphClient("https://graph.facebook.com", tokens, version)
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_error(404)
                return
            params = parse_qs(parsed.query)
            try:
                saved = validate_callback(state_path, params.get("state", [None])[0])
                if params.get("error") or not params.get("code"):
                    raise WorkflowError("Official sign-in was cancelled or incomplete", "auth_failed")
                reply = client.request("GET", "oauth/access_token", {
                    "client_id": app_id, "client_secret": secret, "redirect_uri": saved["redirect_uri"],
                    "code": params["code"][0],
                }, authenticated=False)
                tokens.save(reply)
                result.update({"connected": True, "credentials_saved_privately": True})
                message = "登入完成。憑證已私下保存，可以回到原任務接續。"
                status = 200
            except (WorkflowError, ValueError, OSError) as exc:
                result.update({"connected": False, "reason": getattr(exc, "kind", "auth_failed")})
                message, status = "登入未完成，原進度已保留。請重新開始正常授權。", 400
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(message.encode("utf-8"))

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise Waiting("已註冊回呼的本機連接埠被占用；保留進度，待釋出後續接。", "waiting_capability") from exc
    try:
        url = begin_oauth(app_id, redirect, auth.get("scopes", []), state_path)
        emit(json.dumps({"authorization_url": url, "expires_seconds": 600}, ensure_ascii=False))
        server.timeout = 1
        deadline = time.time() + 600
        while not result and time.time() < deadline:
            server.handle_request()
        if not result:
            raise Waiting("等待本人正常登入逾時；原工作與素材保留。")
        return result
    finally:
        server.server_close()
