#!/usr/bin/env bash
set -euo pipefail

BACKEND_PORT="${R_AGENT_COCKPIT_PORT:-8765}"
FRONTEND_PORT="${R_AGENT_COCKPIT_FRONTEND_PORT:-5173}"
FORCE=0
if [ "${1:-}" = "--force" ] || [ "${1:-}" = "-f" ]; then
  FORCE=1
fi

if ! command -v ss >/dev/null 2>&1; then
  echo "ss not found; cannot inspect listening ports." >&2
  exit 1
fi

list_pids_for_port() {
  local port="$1"
  ss -ltnp "sport = :$port" 2>/dev/null \
    | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
    | sort -u
}

stop_pid_group() {
  local pid="$1"
  local signal="$2"
  local pgid
  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
  if [ -z "$pgid" ]; then
    return 0
  fi
  echo "Sending $signal to process group $pgid for pid $pid"
  kill "-$signal" -- "-$pgid" 2>/dev/null || kill "-$signal" "$pid" 2>/dev/null || true
}

collect_pids() {
  {
    list_pids_for_port "$BACKEND_PORT"
    list_pids_for_port "$FRONTEND_PORT"
  } | sort -u
}

pids="$(collect_pids)"
if [ -z "$pids" ]; then
  echo "No Cockpit listener found on ports $BACKEND_PORT/$FRONTEND_PORT."
  exit 0
fi

echo "Found Cockpit-related listener pids:"
for pid in $pids; do
  ps -o pid,ppid,pgid,stat,etime,command -p "$pid" || true
done

for pid in $pids; do
  stop_pid_group "$pid" TERM
done

sleep 2

remaining="$(collect_pids)"
if [ -n "$remaining" ] && [ "$FORCE" -eq 1 ]; then
  echo "Ports still occupied; forcing shutdown."
  for pid in $remaining; do
    stop_pid_group "$pid" KILL
  done
  sleep 1
  remaining="$(collect_pids)"
fi

if [ -n "$remaining" ]; then
  echo "Ports are still occupied. Re-run with --force if these are stale Cockpit processes:" >&2
  for pid in $remaining; do
    ps -o pid,ppid,pgid,stat,etime,command -p "$pid" >&2 || true
  done
  exit 2
fi

echo "Cockpit ports $BACKEND_PORT/$FRONTEND_PORT are free."
