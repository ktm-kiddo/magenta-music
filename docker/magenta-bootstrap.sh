#!/usr/bin/env bash
# Boot-time half of the template image: everything that is fast, changeable, or
# specific to this instance. The slow half -- venv and weights -- is already in
# the image, so this finishes in about a second on a warm boot and a few seconds
# on a cold one.
#
# Goes in the Vast template's on-start field:
#
#     magenta-bootstrap
#
# It runs on every boot, not just the first, so every step is guarded.
set -euo pipefail

MUSIC_ROOT="${MUSIC_ROOT:-/workspace/magenta-music}"
MUSIC_REPO="${MUSIC_REPO:-https://github.com/ktm-kiddo/magenta-music.git}"
MUSIC_VENV="${MUSIC_VENV:-/opt/venv}"
MUSIC_MODEL="${MUSIC_MODEL:-mrt2_small}"
MAGENTA_HOME="${MAGENTA_HOME:-/opt/magenta}"
SESSION_FILE="${SESSION_FILE:-/workspace/music-session.txt}"
export MAGENTA_HOME

say() { echo "[bootstrap] $*"; }

if [[ ! -x "$MUSIC_VENV/bin/python" ]]; then
  echo "[bootstrap] no venv at $MUSIC_VENV -- this is not the template image." >&2
  echo "[bootstrap] Run vast-setup.sh instead, which provisions from scratch." >&2
  exit 1
fi

# --- The repo --------------------------------------------------------------
# The one thing not baked into the image, so that a code change costs a pull
# rather than a rebuild and a 10 GB push.
if [[ -d "$MUSIC_ROOT/.git" ]]; then
  say "updating $MUSIC_ROOT"
  git -C "$MUSIC_ROOT" pull --ff-only || say "pull skipped (local changes)"
else
  say "cloning into $MUSIC_ROOT"
  mkdir -p "$(dirname "$MUSIC_ROOT")"
  git clone --depth 1 "$MUSIC_REPO" "$MUSIC_ROOT"
fi

# --- Wiring the baked venv into the repo -----------------------------------
# start.sh calls .venv/bin/python by relative path, so the venv has to appear
# there. A symlink keeps the image's single copy rather than duplicating several
# GB onto the instance's disk. Never replace a real directory: that would be
# someone's own environment from a manual vast-setup.sh run.
if [[ ! -e "$MUSIC_ROOT/.venv" ]]; then
  say "linking $MUSIC_VENV into the repo"
  ln -s "$MUSIC_VENV" "$MUSIC_ROOT/.venv"
elif [[ -L "$MUSIC_ROOT/.venv" ]]; then
  :  # already linked, nothing to do
else
  say "note: $MUSIC_ROOT/.venv is a real directory, leaving it alone"
fi

if ! grep -q 'MAGENTA_HOME' ~/.bashrc 2>/dev/null; then
  echo "export MAGENTA_HOME=$MAGENTA_HOME" >> ~/.bashrc
fi

# --- Optional autostart ----------------------------------------------------
# Off by default: a box that starts streaming the moment it boots is only
# useful if Foundry already knows its address, which means a fixed token and a
# named tunnel. With a quick tunnel the hostname changes every boot and you
# have to come and read it anyway.
if [[ "${MUSIC_AUTOSTART:-0}" != "1" ]]; then
  cat <<EOF

[bootstrap] Ready. To start a session:

    cd $MUSIC_ROOT
    tmux new -s music
    MUSIC_TOKEN=\$(openssl rand -hex 16) ./start.sh --backend jax --model $MUSIC_MODEL

Set MUSIC_AUTOSTART=1 in the template to have this start on boot instead.
Put your Groq key in $MUSIC_ROOT/.env to enable prompt rewriting.
EOF
  exit 0
fi

if tmux has-session -t music 2>/dev/null; then
  say "session 'music' is already running, leaving it alone"
  exit 0
fi

# Generated per boot unless the template supplies one. A fixed MUSIC_TOKEN is
# what lets Foundry's token setting be filled in once and left alone.
token="${MUSIC_TOKEN:-$(openssl rand -hex 16)}"

say "starting the player in tmux session 'music'"
tmux new-session -d -s music -c "$MUSIC_ROOT" \
  "MUSIC_TOKEN=$token ./start.sh --backend jax --model $MUSIC_MODEL \
     --preroll ${MUSIC_PREROLL:-6} --target-buffer ${MUSIC_TARGET_BUFFER:-4}"

# The tunnel hostname is printed inside tmux, where nothing that reads this log
# can see it. Lift it back out so the address is available without attaching.
say "waiting for the tunnel address"
url=""
for _ in $(seq 1 60); do
  url=$(tmux capture-pane -p -t music 2>/dev/null \
        | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1) || true
  [[ -n "$url" ]] && break
  tmux has-session -t music 2>/dev/null || { say "the player exited early:"; tmux capture-pane -p -t music 2>/dev/null || true; exit 1; }
  sleep 1
done

{
  echo "Music server URL:   ${url:-<not reported -- run: tmux attach -t music>}"
  echo "Music server token: $token"
} | tee "$SESSION_FILE"

cat <<EOF

[bootstrap] Both values are in $SESSION_FILE and go into Foundry's module
settings. Attach to the console with:  tmux attach -t music
EOF
