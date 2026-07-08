import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import request


@dataclass
class FeishuMessage:
    event_id: str
    chat_id: str
    sender_id: str
    text: str


def parse_feishu_event(payload: Dict[str, Any], verification_token: str = "") -> Dict[str, Any]:
    """Parse Feishu/Lark callback payloads.

    Supports URL verification and the common message.receive_v1 event shape.
    Encrypted callbacks are intentionally not decrypted here; configure Feishu
    without Encrypt Key first, or terminate/decrypt before forwarding.
    """
    if "encrypt" in payload:
        return {"type": "encrypted", "supported": False}

    if payload.get("type") == "url_verification":
        if verification_token and payload.get("token") != verification_token:
            return {"type": "verification_failed"}
        return {"type": "challenge", "challenge": payload.get("challenge", "")}

    header = payload.get("header") or {}
    event = payload.get("event") or {}
    event_type = header.get("event_type") or payload.get("type")
    if event_type != "im.message.receive_v1":
        return {"type": "ignored", "event_type": event_type}

    message = event.get("message") or {}
    sender = event.get("sender") or {}
    content = message.get("content") or "{}"
    try:
        content_obj = json.loads(content)
    except json.JSONDecodeError:
        content_obj = {"text": content}

    chat_id = message.get("chat_id") or sender.get("sender_id", {}).get("open_id", "")
    sender_id = sender.get("sender_id", {}).get("open_id", "")
    return {
        "type": "message",
        "message": FeishuMessage(
            event_id=header.get("event_id", ""),
            chat_id=chat_id,
            sender_id=sender_id,
            text=content_obj.get("text", ""),
        ),
    }


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._tenant_access_token = ""
        self._token_expire_at = 0.0

    def is_configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._token_expire_at - 60:
            return self._tenant_access_token
        data = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode("utf-8")
        req = request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with request.urlopen(req, timeout=10) as resp:  # noqa: S310 - official Feishu API URL
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("code") != 0:
            raise RuntimeError(f"Feishu token error: {body}")
        self._tenant_access_token = body["tenant_access_token"]
        self._token_expire_at = now + int(body.get("expire", 7200))
        return self._tenant_access_token

    def send_text(self, receive_id: str, text: str, receive_id_type: str = "chat_id") -> Dict[str, Any]:
        token = self.get_tenant_access_token()
        data = json.dumps(
            {
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            ensure_ascii=False,
        ).encode("utf-8")
        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
        req = request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with request.urlopen(req, timeout=10) as resp:  # noqa: S310 - official Feishu API URL
            return json.loads(resp.read().decode("utf-8"))
