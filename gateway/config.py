import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayConfig:
    host: str = os.environ.get("RAGENT_GATEWAY_HOST", "0.0.0.0")
    port: int = int(os.environ.get("RAGENT_GATEWAY_PORT", "8080"))
    default_session_id: str = os.environ.get("RAGENT_GATEWAY_DEFAULT_SESSION", "default")
    max_sessions: int = int(os.environ.get("RAGENT_GATEWAY_MAX_SESSIONS", "100"))
    system_prompt: str = os.environ.get("RAGENT_GATEWAY_SYSTEM_PROMPT", "")
    auth_token: str = os.environ.get("RAGENT_GATEWAY_AUTH_TOKEN", "")

    wechat_token: str = os.environ.get("WECHAT_TOKEN", "")
    feishu_app_id: str = os.environ.get("FEISHU_APP_ID", "")
    feishu_app_secret: str = os.environ.get("FEISHU_APP_SECRET", "")
    feishu_verification_token: str = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")
    feishu_encrypt_key: str = os.environ.get("FEISHU_ENCRYPT_KEY", "")

    qq_app_id: str = os.environ.get("QQ_APP_ID", "")
    qq_app_secret: str = os.environ.get("QQ_APP_SECRET", "")
    qq_sandbox: bool = os.environ.get("QQ_SANDBOX", "false").lower() in {"1", "true", "yes", "on"}

    async_webhooks: bool = os.environ.get("RAGENT_GATEWAY_ASYNC_WEBHOOKS", "false").lower() in {"1", "true", "yes", "on"}
    event_dedupe_ttl_seconds: int = int(os.environ.get("RAGENT_GATEWAY_EVENT_DEDUPE_TTL_SECONDS", "3600"))
    async_queue_size: int = int(os.environ.get("RAGENT_GATEWAY_ASYNC_QUEUE_SIZE", "100"))
    async_workers: int = int(os.environ.get("RAGENT_GATEWAY_ASYNC_WORKERS", "1"))


def get_gateway_config() -> GatewayConfig:
    return GatewayConfig()
