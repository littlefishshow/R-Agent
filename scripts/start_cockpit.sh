#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/app_gui_frontend"
BACKEND_HOST="${R_AGENT_COCKPIT_HOST:-127.0.0.1}"
BACKEND_PORT="${R_AGENT_COCKPIT_PORT:-8765}"
FRONTEND_PORT="${R_AGENT_COCKPIT_FRONTEND_PORT:-5173}"
BACKEND_READY_TIMEOUT="${R_AGENT_COCKPIT_READY_TIMEOUT:-30}"

cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3，请先安装 Python。" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "❌ 未找到 npm，请先安装 Node.js/npm。" >&2
  exit 1
fi

ensure_frontend_dependencies() {
  local missing=0
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    missing=1
  else
    for pkg in katex markdown-it @types/katex @types/markdown-it; do
      if ! (cd "$FRONTEND_DIR" && npm ls "$pkg" --depth=0 >/dev/null 2>&1); then
        missing=1
        break
      fi
    done
  fi

  if [ "$missing" -eq 1 ]; then
    echo "📦 安装/补齐前端依赖（含 markdown-it、katex 及类型声明）..."
    (cd "$FRONTEND_DIR" && npm install)
  fi
}

ensure_frontend_dependencies

cleanup() {
  if [ -n "${BACKEND_PID:-}" ] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${FRONTEND_PID:-}" ] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

wait_for_backend() {
  echo "⏳ 等待后端就绪 http://$BACKEND_HOST:$BACKEND_PORT/health（最多 ${BACKEND_READY_TIMEOUT}s）..."
  BACKEND_HOST="$BACKEND_HOST" \
  BACKEND_PORT="$BACKEND_PORT" \
  BACKEND_READY_TIMEOUT="$BACKEND_READY_TIMEOUT" \
  BACKEND_PID="$BACKEND_PID" \
  python3 - <<'PY'
import os
import sys
import time
import urllib.error
import urllib.request

host = os.environ["BACKEND_HOST"]
port = os.environ["BACKEND_PORT"]
timeout = float(os.environ.get("BACKEND_READY_TIMEOUT", "30"))
backend_pid = int(os.environ["BACKEND_PID"])
url = f"http://{host}:{port}/health"
deadline = time.monotonic() + timeout
last_error = None

while time.monotonic() < deadline:
    try:
        # Detect early backend crash so the user sees the original server error above
        # instead of waiting for the full timeout.
        os.kill(backend_pid, 0)
    except OSError:
        print("❌ 后端进程已退出，无法启动前端。", file=sys.stderr)
        sys.exit(1)

    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if 200 <= response.status < 500:
                sys.exit(0)
    except (OSError, urllib.error.URLError) as exc:
        last_error = exc
    time.sleep(0.2)

print(f"❌ 后端未在 {timeout:g}s 内就绪：{url}", file=sys.stderr)
if last_error is not None:
    print(f"   最后错误：{last_error}", file=sys.stderr)
sys.exit(1)
PY
}

echo "🚀 启动 R-Agent Cockpit 后端 http://$BACKEND_HOST:$BACKEND_PORT"
PYTHONPATH="$ROOT_DIR" R_AGENT_COCKPIT_HOST="$BACKEND_HOST" R_AGENT_COCKPIT_PORT="$BACKEND_PORT" python3 -m app_gui.server &
BACKEND_PID=$!

wait_for_backend

echo "🧭 启动 R-Agent Cockpit 前端 http://127.0.0.1:$FRONTEND_PORT"
(
  cd "$FRONTEND_DIR"
  npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

URL="http://127.0.0.1:$FRONTEND_PORT"
echo ""
echo "✅ R-Agent Cockpit 已启动：$URL"
echo "   后端 API：http://$BACKEND_HOST:$BACKEND_PORT"
echo "   按 Ctrl+C 可同时停止前端和后端。"
echo ""

if command -v open >/dev/null 2>&1; then
  open "$URL" >/dev/null 2>&1 || true
fi

wait "$FRONTEND_PID"
