import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, List
from urllib.parse import parse_qs, urlparse

from gateway.adapters.feishu import FeishuClient, parse_feishu_event
from gateway.adapters.qq import QQOfficialClient, parse_qq_event, sign_qq_validation
from gateway.adapters.wechat import (
    build_wechat_text_reply,
    parse_wechat_xml,
    verify_wechat_signature,
)
from gateway.config import GatewayConfig, get_gateway_config
from gateway.queue import AsyncJobQueue, EventDeduplicator
from gateway.service import AgentSessionManager


class GatewayState:
    def __init__(self, config: GatewayConfig):
        self.config = config
        self.manager = AgentSessionManager(
            max_sessions=config.max_sessions,
            system_prompt=config.system_prompt or None,
        )
        self.feishu = FeishuClient(config.feishu_app_id, config.feishu_app_secret)
        self.qq = QQOfficialClient(config.qq_app_id, config.qq_app_secret, sandbox=config.qq_sandbox)
        self.dedupe = EventDeduplicator(ttl_seconds=config.event_dedupe_ttl_seconds)
        self.jobs = AsyncJobQueue(maxsize=config.async_queue_size, workers=config.async_workers)


_STATE: Optional[GatewayState] = None


def get_state() -> GatewayState:
    if _STATE is None:
        raise RuntimeError("gateway state is not initialized")
    return _STATE


def create_handler(state: GatewayState):
    class RAgentGatewayHandler(BaseHTTPRequestHandler):
        server_version = "RAgentGateway/0.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._send_json({"ok": True, "service": "r-agent-gateway"})
                return

            if parsed.path == "/v1/sessions":
                if not self._authorized():
                    return
                self._send_json({"sessions": state.manager.list_sessions()})
                return

            if parsed.path == "/webhook/wechat":
                query = parse_qs(parsed.query)
                signature = _first(query, "signature")
                timestamp = _first(query, "timestamp")
                nonce = _first(query, "nonce")
                echostr = _first(query, "echostr")
                if verify_wechat_signature(state.config.wechat_token, signature, timestamp, nonce):
                    self._send_text(echostr, content_type="text/plain")
                else:
                    self._send_json({"error": "invalid wechat signature"}, HTTPStatus.FORBIDDEN)
                return

            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if parsed.path == "/v1/chat":
                if not self._authorized():
                    return
                body = self._read_json()
                message = str(body.get("message") or "")
                if not message.strip():
                    self._send_json({"error": "message is required"}, HTTPStatus.BAD_REQUEST)
                    return
                session_id = str(body.get("session_id") or state.config.default_session_id)
                exclude_tools = body.get("exclude_tools") or None
                try:
                    answer = state.manager.chat(session_id, message, exclude_tools=exclude_tools)
                except Exception as exc:  # noqa: BLE001 - gateway boundary
                    self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._send_json({"session_id": session_id, "answer": answer})
                return

            if parsed.path.startswith("/v1/sessions/") and parsed.path.endswith("/reset"):
                if not self._authorized():
                    return
                session_id = parsed.path.split("/")[3]
                existed = state.manager.reset(session_id)
                self._send_json({"session_id": session_id, "reset": existed})
                return

            if parsed.path == "/webhook/wechat":
                query = parse_qs(parsed.query)
                if not verify_wechat_signature(
                    state.config.wechat_token,
                    _first(query, "signature"),
                    _first(query, "timestamp"),
                    _first(query, "nonce"),
                ):
                    self._send_json({"error": "invalid wechat signature"}, HTTPStatus.FORBIDDEN)
                    return
                raw = self._read_body().decode("utf-8")
                try:
                    msg = parse_wechat_xml(raw)
                    if msg is None or not msg.content.strip():
                        self._send_text("success", content_type="text/plain")
                        return
                    answer = state.manager.chat(f"wechat:{msg.from_user}", msg.content)
                    reply = build_wechat_text_reply(msg.from_user, msg.to_user, answer)
                    self._send_text(reply, content_type="application/xml; charset=utf-8")
                except Exception as exc:  # noqa: BLE001 - webhook boundary
                    reply = build_wechat_text_reply(
                        msg.from_user if "msg" in locals() and msg else "",
                        msg.to_user if "msg" in locals() and msg else "",
                        f"R-Agent 处理失败：{exc}",
                    )
                    self._send_text(reply, content_type="application/xml; charset=utf-8")
                return

            if parsed.path == "/webhook/qq":
                payload = self._read_json()
                parsed_event = parse_qq_event(payload)
                event_type = parsed_event.get("type")
                if event_type == "validation":
                    try:
                        signature = sign_qq_validation(
                            parsed_event.get("event_ts", ""),
                            parsed_event.get("plain_token", ""),
                            state.config.qq_app_secret,
                        )
                    except Exception as exc:  # noqa: BLE001 - webhook boundary
                        self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                        return
                    self._send_json({"plain_token": parsed_event.get("plain_token", ""), "signature": signature})
                    return
                if event_type == "ignored":
                    self._send_json({"ok": True, "ignored": parsed_event.get("event_type"), "reason": parsed_event.get("reason", "")})
                    return
                if event_type != "message":
                    self._send_json({"ok": True, "ignored": event_type})
                    return

                msg = parsed_event["message"]
                if state.dedupe.seen_or_mark(msg.event_id):
                    self._send_json({"ok": True, "duplicate": True})
                    return

                def process_qq_message() -> Optional[Dict[str, Any]]:
                    answer = state.manager.chat(msg.session_id, msg.text)
                    if state.qq.is_configured():
                        return state.qq.send_text(msg, answer)
                    return {"sent": False, "answer": answer}

                if state.config.async_webhooks:
                    accepted = state.jobs.submit(process_qq_message)
                    status = HTTPStatus.ACCEPTED if accepted else HTTPStatus.SERVICE_UNAVAILABLE
                    self._send_json({"ok": accepted, "queued": accepted}, status)
                    return

                try:
                    sent = process_qq_message()
                    self._send_json({"ok": True, "sent": bool(state.qq.is_configured()), "qq_response": sent})
                except Exception as exc:  # noqa: BLE001 - webhook boundary
                    self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            if parsed.path == "/webhook/feishu":
                payload = self._read_json()
                parsed_event = parse_feishu_event(payload, state.config.feishu_verification_token)
                event_type = parsed_event.get("type")
                if event_type == "challenge":
                    self._send_json({"challenge": parsed_event.get("challenge", "")})
                    return
                if event_type == "verification_failed":
                    self._send_json({"error": "invalid feishu verification token"}, HTTPStatus.FORBIDDEN)
                    return
                if event_type == "encrypted":
                    self._send_json({"error": "encrypted callbacks are not supported by this minimal gateway"}, HTTPStatus.BAD_REQUEST)
                    return
                if event_type == "ignored":
                    self._send_json({"ok": True, "ignored": parsed_event.get("event_type")})
                    return
                if event_type != "message":
                    self._send_json({"ok": True, "ignored": event_type})
                    return

                msg = parsed_event["message"]
                if not msg.text.strip():
                    self._send_json({"ok": True, "ignored": "empty text"})
                    return
                if state.dedupe.seen_or_mark(msg.event_id):
                    self._send_json({"ok": True, "duplicate": True})
                    return

                def process_feishu_message() -> Optional[Dict[str, Any]]:
                    answer = state.manager.chat(f"feishu:{msg.chat_id or msg.sender_id}", msg.text)
                    if state.feishu.is_configured() and msg.chat_id:
                        return state.feishu.send_text(msg.chat_id, answer, receive_id_type="chat_id")
                    return None

                if state.config.async_webhooks:
                    accepted = state.jobs.submit(process_feishu_message)
                    status = HTTPStatus.ACCEPTED if accepted else HTTPStatus.SERVICE_UNAVAILABLE
                    self._send_json({"ok": accepted, "queued": accepted}, status)
                    return

                try:
                    sent = process_feishu_message()
                    self._send_json({"ok": True, "sent": sent is not None, "feishu_response": sent})
                except Exception as exc:  # noqa: BLE001 - webhook boundary
                    self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("[gateway] " + fmt % args + "\n")

        def _authorized(self) -> bool:
            token = state.config.auth_token
            if not token:
                return True
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {token}":
                return True
            self._send_json({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return False

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or "0")
            return self.rfile.read(length) if length else b""

        def _read_json(self) -> Dict[str, Any]:
            raw = self._read_body()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK, content_type: str = "text/plain; charset=utf-8") -> None:
            data = (text or "").encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return RAgentGatewayHandler


def _first(query: Dict[str, List[str]], key: str) -> str:
    value = query.get(key) or [""]
    return value[0]


def run_server(config: Optional[GatewayConfig] = None) -> None:
    global _STATE
    config = config or get_gateway_config()
    _STATE = GatewayState(config)
    handler = create_handler(_STATE)
    httpd = ThreadingHTTPServer((config.host, config.port), handler)
    print(f"R-Agent gateway listening on http://{config.host}:{config.port}")
    httpd.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run R-Agent HTTP gateway")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    cfg = get_gateway_config()
    if args.host is not None or args.port is not None:
        cfg = GatewayConfig(
            host=args.host or cfg.host,
            port=args.port or cfg.port,
            default_session_id=cfg.default_session_id,
            max_sessions=cfg.max_sessions,
            system_prompt=cfg.system_prompt,
            auth_token=cfg.auth_token,
            wechat_token=cfg.wechat_token,
            feishu_app_id=cfg.feishu_app_id,
            feishu_app_secret=cfg.feishu_app_secret,
            feishu_verification_token=cfg.feishu_verification_token,
            feishu_encrypt_key=cfg.feishu_encrypt_key,
            qq_app_id=cfg.qq_app_id,
            qq_app_secret=cfg.qq_app_secret,
            qq_sandbox=cfg.qq_sandbox,
            async_webhooks=cfg.async_webhooks,
            event_dedupe_ttl_seconds=cfg.event_dedupe_ttl_seconds,
            async_queue_size=cfg.async_queue_size,
            async_workers=cfg.async_workers,
        )
    run_server(cfg)


if __name__ == "__main__":
    main()
