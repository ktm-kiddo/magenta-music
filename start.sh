#!/usr/bin/env bash
# Start the music server and a Cloudflare tunnel, and print the URL to paste
# into Foundry's "Music server URL" setting.
#
# The player runs in the foreground so you keep its console (type a scene, or
# /status, /quit). The tunnel runs behind it and is torn down on exit.
#
# Set MUSIC_TOKEN to require a shared secret -- do this whenever the tunnel is
# public, which it always is with trycloudflare.com:
#
#   MUSIC_TOKEN=$(openssl rand -hex 16) ./start.sh
#
# and put the same value in Module Settings -> Music server token.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# One source of truth: the tunnel and the player must agree on the port, so
# override it here rather than passing --serve-port through.
PORT="${MUSIC_PORT:-30001}"
log=$(mktemp -t magenta-tunnel)
tunnel_pid=""

cleanup() {
  [[ -n "$tunnel_pid" ]] && kill "$tunnel_pid" 2>/dev/null || true
  rm -f "$log"
}
trap cleanup EXIT

if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is already in use -- a player is probably still running."
  echo "Stop it first:  lsof -ti:$PORT | xargs kill"
  exit 1
fi

cloudflared tunnel --url "http://localhost:$PORT" > "$log" 2>&1 &
tunnel_pid=$!

echo "Starting tunnel..."
url=""
for _ in $(seq 1 45); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log" | head -1) || true
  [[ -n "$url" ]] && break
  # The tunnel dying early (no cloudflared, no network) should not cost 45s.
  kill -0 "$tunnel_pid" 2>/dev/null || { echo "cloudflared exited:"; cat "$log"; exit 1; }
  sleep 1
done

if [[ -z "$url" ]]; then
  echo "Tunnel did not report a URL in time. Log:"; cat "$log"; exit 1
fi

echo
echo "  Music server URL (paste into Foundry module settings):"
echo "    $url"
echo
if [[ -z "${MUSIC_TOKEN:-}" ]]; then
  echo "  No MUSIC_TOKEN set: anyone with that URL can hear and change the music."
  echo
fi

args=(--serve --serve-port "$PORT" --no-local-audio)
[[ -n "${MUSIC_TOKEN:-}" ]] && args+=(--serve-token "$MUSIC_TOKEN")

.venv/bin/python stream_player.py "${args[@]}" "$@"
