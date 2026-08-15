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
#
# A quick tunnel gets a new random hostname every run, so Foundry's URL setting
# has to be re-pasted every session. To stop that, set both of these and the
# address becomes permanent:
#
#   CF_TUNNEL_TOKEN   the token for a named tunnel, from the Cloudflare Zero
#                     Trust dashboard (Networks -> Tunnels). Requires a domain.
#   MUSIC_HOSTNAME    the public hostname you routed to http://localhost:30001
#                     in that tunnel's config, e.g. music.example.com
#
# Fixing MUSIC_TOKEN as well means both Foundry settings are filled in once and
# never touched again. No domain? Tailscale Funnel gives a fixed hostname for
# free -- see "Getting an HTTPS address" in the README.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# One source of truth: the tunnel and the player must agree on the port, so
# override it here rather than passing --serve-port through.
PORT="${MUSIC_PORT:-30001}"
# The X's are required by GNU mktemp (Linux) and harmless on BSD (macOS) --
# without them this script only runs on the Mac.
log=$(mktemp -t magenta-tunnel.XXXXXX)
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

url=""

if [[ -n "${CF_TUNNEL_TOKEN:-}" ]]; then
  # A named tunnel carries its own routing, configured once in the dashboard, so
  # nothing here picks a hostname -- which is exactly why the address survives a
  # restart. The flip side is that it never prints one either, so MUSIC_HOSTNAME
  # is the only way this script can tell you where the server ended up.
  if [[ -z "${MUSIC_HOSTNAME:-}" ]]; then
    echo "CF_TUNNEL_TOKEN is set but MUSIC_HOSTNAME is not."
    echo "Set it to the hostname you routed to http://localhost:$PORT in the"
    echo "tunnel's config, e.g. MUSIC_HOSTNAME=music.example.com"
    exit 1
  fi

  cloudflared tunnel run --token "$CF_TUNNEL_TOKEN" > "$log" 2>&1 &
  tunnel_pid=$!

  echo "Starting named tunnel..."
  ready=""
  for _ in $(seq 1 45); do
    grep -q 'Registered tunnel connection' "$log" && { ready=1; break; }
    kill -0 "$tunnel_pid" 2>/dev/null || { echo "cloudflared exited:"; cat "$log"; exit 1; }
    sleep 1
  done

  if [[ -z "$ready" ]]; then
    echo "Tunnel did not register in time. Log:"; cat "$log"; exit 1
  fi

  # Tolerate a pasted URL as well as a bare hostname; both are the obvious thing
  # to put in this variable, and only one of them concatenates correctly.
  url="https://${MUSIC_HOSTNAME#https://}"
  url="${url%/}"
else
  cloudflared tunnel --url "http://localhost:$PORT" > "$log" 2>&1 &
  tunnel_pid=$!

  echo "Starting tunnel..."
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
fi

# A fixed address only needs pasting the first time, so saying "paste this"
# every run trains you to ignore the one run where it actually changed.
if [[ -n "${CF_TUNNEL_TOKEN:-}" ]]; then
  url_note="(fixed -- set once in Foundry module settings)"
else
  url_note="(paste into Foundry module settings)"
fi

echo
echo "  Music server URL $url_note:"
echo "    $url"
echo
if [[ -n "${MUSIC_TOKEN:-}" ]]; then
  # Generated inline (MUSIC_TOKEN=$(openssl rand -hex 16)) the value is never
  # seen otherwise, and it has to be typed into Foundry to match.
  echo "  Music server token (paste into Foundry module settings):"
  echo "    $MUSIC_TOKEN"
  echo
else
  echo "  No MUSIC_TOKEN set: anyone with that URL can hear and change the music."
  echo
fi

args=(--serve --serve-port "$PORT" --no-local-audio)
[[ -n "${MUSIC_TOKEN:-}" ]] && args+=(--serve-token "$MUSIC_TOKEN")

.venv/bin/python stream_player.py "${args[@]}" "$@"
