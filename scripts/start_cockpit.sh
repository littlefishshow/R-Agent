#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/app_gui_frontend"
BACKEND_HOST="${R_AGENT_COCKPIT_HOST:-127.0.0.1}"
BACKEND_PORT="${R_AGENT_COCKPIT_PORT:-8765}"
FRONTEND_PORT="${R_AGENT_COCKPIT_FRONTEND_PORT:-5173}"

cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3，请先安装 Python。" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "❌ 未找到 npm，请先安装 Node.js/npm。" >&2
  exit 1
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "📦 首次启动：安装前端依赖..."
  (cd "$FRONTEND_DIR" && npm install)
fi

cleanup() {
  if [ -n "${BACKEND_PID:-}" ] && kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    kill "$BACKEND_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "${FRONTEND_PID:-}" ] && kill -0 "$FRONTEND_PID" >/dev/null 2>&1; then
    kill "$FRONTEND_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

echo "🚀 启动 R-Agent Cockpit 后端 http://$BACKEND_HOST:$BACKEND_PORT"
PYTHONPATH="$ROOT_DIR" python3 -m app_gui.server &
BACKEND_PID=$!

sleep 1

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
