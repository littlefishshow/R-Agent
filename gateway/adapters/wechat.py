import hashlib
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional


@dataclass
class WeChatMessage:
    from_user: str
    to_user: str
    msg_type: str
    content: str
    msg_id: str = ""


def verify_wechat_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    if not token or not signature or not timestamp or not nonce:
        return False
    raw = "".join(sorted([token, timestamp, nonce]))
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return digest == signature


def parse_wechat_xml(xml_body: str) -> Optional[WeChatMessage]:
    root = ET.fromstring(xml_body)

    def text(name: str) -> str:
        node = root.find(name)
        return node.text if node is not None and node.text is not None else ""

    msg_type = text("MsgType")
    if msg_type not in {"text", "event"}:
        return None

    content = text("Content") if msg_type == "text" else text("Event")
    return WeChatMessage(
        from_user=text("FromUserName"),
        to_user=text("ToUserName"),
        msg_type=msg_type,
        content=content,
        msg_id=text("MsgId"),
    )


def build_wechat_text_reply(to_user: str, from_user: str, content: str) -> str:
    # 微信被动回复 XML 中 ToUserName 是原发送者，FromUserName 是公众号/企业号。
    escaped = _xml_cdata_safe(content)
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{escaped}]]></Content>"
        "</xml>"
    )


def _xml_cdata_safe(value: str) -> str:
    # CDATA 不能直接包含 ]]>，这里做最小切分转义。
    return (value or "").replace("]]>", "]]]]><![CDATA[>")
