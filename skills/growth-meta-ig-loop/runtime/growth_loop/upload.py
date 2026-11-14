import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener

from .errors import UncertainWrite, Waiting, WorkflowError
from .files import file_digest
from .transport import NoRedirect


class AssetUploader:
    """Adapter for an explicitly configured existing media endpoint, not a new vendor."""
    def __init__(self, url, token_env=None, mode="live"):
        parsed = urlparse(url)
        if mode == "fixture":
            if parsed.hostname not in ("127.0.0.1", "localhost"):
                raise ValueError("Fixture upload must stay on loopback")
        elif parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Media upload requires an explicitly configured HTTPS endpoint")
        if parsed.username or parsed.password:
            raise ValueError("Do not put credentials in media URLs")
        self.url, self.token_env, self.mode = url, token_env, mode
        self.opener = build_opener(NoRedirect())

    def upload(self, path):
        headers = {"Content-Type": "image/jpeg", "X-Content-SHA256": file_digest(path)}
        if self.token_env:
            token = os.environ.get(self.token_env)
            if not token:
                raise Waiting("媒體儲存的授權未配置；原素材已保存。", "waiting_capability")
            headers["Authorization"] = "Bearer " + token
        try:
            with self.opener.open(Request(self.url, Path(path).read_bytes(), headers, method="POST"), timeout=20) as response:
                result = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise Waiting("媒體上傳缺授權。", "waiting_owner") from exc
            if exc.code == 429:
                raise WorkflowError("媒體上傳暫時限流。", "transient") from exc
            raise WorkflowError("媒體上傳被拒絕。", "invalid_asset") from exc
        except (OSError, URLError, ValueError) as exc:
            raise UncertainWrite("媒體上傳結果不明；以內容SHA在儲存端核對後再重試。") from exc
        url = urlparse(result.get("url", ""))
        if not url.hostname or (url.scheme != "https" and self.mode != "fixture"):
            raise WorkflowError("Media endpoint did not return a usable URL", "invalid_response")
        return {"url": result["url"], "sha256": file_digest(path)}
