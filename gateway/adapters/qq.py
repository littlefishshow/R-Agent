import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import request


@dataclass
class QQMessage:
    event_id: str
    message_id: str
    event_type: str
    session_id: str
    text: str
    group_openid: str = ""
    user_openid: str = ""


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def clean_qq_content(content: str) -> str:
    """Remove common bot mention markup from QQ message content."""
    content = content or ""
    content = re.sub(r"<@!?\d+>", "", content)
    content = re.sub(r"@\S+", "", content, count=1) if content.lstrip().startswith("@") else content
    return content.strip()


def parse_qq_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Parse QQ official webhook event payloads.

    Supports the common QQ Bot Gateway/Webhook envelope shape:
    {"op": 13, "d": {"plain_token": "...", "event_ts": "..."}}
    for URL validation, and message events with {"t": "...", "d": {...}}.
    """
    data = payload.get("d") if isinstance(payload.get("d"), dict) else payload

    if payload.get("op") == 13 or "plain_token" in data:
        return {
            "type": "validation",
            "plain_token": _text(data.get("plain_token")),
            "event_ts": _text(data.get("event_ts")),
        }

    event_type = _text(payload.get("t") or payload.get("event_type") or data.get("event_type"))
    event_id = _text(payload.get("id") or data.get("event_id") or data.get("id"))
    message_id = _text(data.get("id") or data.get("msg_id") or event_id)
    content = clean_qq_content(_text(data.get("content") or data.get("text")))

    group_openid = _text(data.get("group_openid") or data.get("group_id") or data.get("guild_id"))
    author = data.get("author") if isinstance(data.get("author"), dict) else {}
    user_openid = _text(
        data.get("user_openid")
        or data.get("openid")
        or author.get("user_openid")
        or author.get("openid")
        or author.get("id")
    )

    if group_openid:
        session_id = f"qq:group:{group_openid}"
    elif user_openid:
        session_id = f"qq:user:{user_openid}"
    else:
        session_id = "qq:unknown"

    message_events = {
        "GROUP_AT_MESSAGE_CREATE",
        "C2C_MESSAGE_CREATE",
        "AT_MESSAGE_CREATE",
        "DIRECT_MESSAGE_CREATE",
        "MESSAGE_CREATE",
    }
    if event_type and event_type not in message_events:
        return {"type": "ignored", "event_type": event_type}
    if not content:
        return {"type": "ignored", "event_type": event_type, "reason": "empty content"}

    return {
        "type": "message",
        "message": QQMessage(
            event_id=event_id or message_id,
            message_id=message_id,
            event_type=event_type,
            session_id=session_id,
            text=content,
            group_openid=group_openid,
            user_openid=user_openid,
        ),
    }


def sign_qq_validation(event_ts: str, plain_token: str, app_secret: str) -> str:
    """Sign QQ validation token using Ed25519.

    QQ official webhook validation expects a signature over event_ts + plain_token.
    PyNaCl is used when available. The secret is accepted as hex, raw 32-byte
    text, or hashed to 32 bytes as a pragmatic fallback for local experiments.
    """
    if not app_secret:
        raise ValueError("QQ_APP_SECRET is required for QQ webhook validation")
    try:
        from nacl.signing import SigningKey  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on optional runtime dep
        raise RuntimeError("PyNaCl is required for QQ webhook validation: pip install pynacl") from exc

    secret = app_secret.strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", secret):
        seed = bytes.fromhex(secret)
    else:
        raw = secret.encode("utf-8")
        seed = raw if len(raw) == 32 else hashlib.sha256(raw).digest()
    signed = SigningKey(seed).sign((event_ts + plain_token).encode("utf-8"))
    return signed.signature.hex()


class QQOfficialClient:
    def __init__(self, app_id: str, app_secret: str, sandbox: bool = False):
        self.app_id = app_id
        self.app_secret = app_secret
        self.sandbox = sandbox
        self._access_token = ""
        self._token_expire_at = 0.0

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    @property
    def api_base(self) -> str:
        # QQ official bot OpenAPI host. Sandbox currently shares the same host
        # for many endpoints; keep the flag for future host/path divergence.
        return "https://api.sgroup.qq.com"

    def get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expire_at - 60:
            return self._access_token
        data = json.dumps({"appId": self.app_id, "clientSecret": self.app_secret}).encode("utf-8")
        req = request.Request(
            f"{self.api_base}/app/getAppAccessToken",
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with request.urlopen(req, timeout=10) as resp:  # noqa: S310 - official QQ API URL
            body = json.loads(resp.read().decode("utf-8"))
        token = body.get("access_token") or body.get("accessToken")
        if not token:
            raise RuntimeError(f"QQ token error: {body}")
        self._access_token = token
        self._token_expire_at = now + int(body.get("expires_in") or body.get("expiresIn") or 7200)
        return self._access_token

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        token = self.get_access_token()
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.api_base}{path}",
            data=data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"QQBot {token}",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=10) as resp:  # noqa: S310 - official QQ API URL
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def send_text(self, msg: QQMessage, text: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"content": text}
        if msg.message_id:
            payload["msg_id"] = msg.message_id
        if msg.group_openid:
            return self._post(f"/v2/groups/{msg.group_openid}/messages", payload)
        if msg.user_openid:
            return self._post(f"/v2/users/{msg.user_openid}/messages", payload)
        raise ValueError("QQ message has neither group_openid nor user_openid")
