import hashlib
import json

from gateway.adapters.feishu import parse_feishu_event
from gateway.adapters.wechat import build_wechat_text_reply, parse_wechat_xml, verify_wechat_signature
from gateway.config import GatewayConfig
from gateway.server import GatewayState, create_handler


def test_wechat_signature_and_xml_roundtrip():
    token = "abc"
    timestamp = "123"
    nonce = "xyz"
    signature = hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()
    assert verify_wechat_signature(token, signature, timestamp, nonce)

    xml = """<xml><ToUserName><![CDATA[to]]></ToUserName><FromUserName><![CDATA[from]]></FromUserName><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[hello]]></Content><MsgId>1</MsgId></xml>"""
    msg = parse_wechat_xml(xml)
    assert msg.from_user == "from"
    assert msg.to_user == "to"
    assert msg.content == "hello"
    reply = build_wechat_text_reply("from", "to", "hi")
    assert "<Content><![CDATA[hi]]></Content>" in reply


def test_feishu_url_verification_and_message_parse():
    assert parse_feishu_event({"type": "url_verification", "token": "t", "challenge": "c"}, "t") == {
        "type": "challenge",
        "challenge": "c",
    }
    payload = {
        "header": {"event_type": "im.message.receive_v1", "event_id": "e1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {"chat_id": "oc_1", "content": json.dumps({"text": "hello"})},
        },
    }
    parsed = parse_feishu_event(payload)
    assert parsed["type"] == "message"
    assert parsed["message"].chat_id == "oc_1"
    assert parsed["message"].text == "hello"


def test_gateway_handler_factory_smoke():
    state = GatewayState(GatewayConfig(host="127.0.0.1", port=0, system_prompt="test"))
    handler = create_handler(state)
    assert handler.server_version.startswith("RAgentGateway")


def test_event_deduplicator_marks_duplicates():
    from gateway.queue import EventDeduplicator

    dedupe = EventDeduplicator(ttl_seconds=3600)
    assert dedupe.seen_or_mark("evt-1") is False
    assert dedupe.seen_or_mark("evt-1") is True
    assert dedupe.seen_or_mark("") is False



def test_qq_validation_and_message_parse():
    from gateway.adapters.qq import parse_qq_event, sign_qq_validation

    parsed = parse_qq_event({"op": 13, "d": {"plain_token": "plain", "event_ts": "123"}})
    assert parsed == {"type": "validation", "plain_token": "plain", "event_ts": "123"}
    try:
        signature = sign_qq_validation("123", "plain", "0" * 64)
    except RuntimeError as exc:
        assert "PyNaCl" in str(exc)
    else:
        assert isinstance(signature, str)
        assert len(signature) == 128

    payload = {
        "id": "evt-1",
        "t": "GROUP_AT_MESSAGE_CREATE",
        "d": {
            "id": "msg-1",
            "group_openid": "group-1",
            "author": {"user_openid": "user-1"},
            "content": "<@123456> 你好",
        },
    }
    msg_event = parse_qq_event(payload)
    assert msg_event["type"] == "message"
    msg = msg_event["message"]
    assert msg.event_id == "evt-1"
    assert msg.message_id == "msg-1"
    assert msg.session_id == "qq:group:group-1"
    assert msg.text == "你好"


def test_qq_ignored_event_parse():
    from gateway.adapters.qq import parse_qq_event

    parsed = parse_qq_event({"t": "READY", "d": {}})
    assert parsed["type"] == "ignored"
    assert parsed["event_type"] == "READY"
