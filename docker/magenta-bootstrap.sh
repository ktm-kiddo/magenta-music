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

# --- Session identity ------------------------------------------------------
# A fixed token and a named tunnel are what let Foundry's two settings be filled
# in once instead of every session. They arrive as Vast template environment
# variables, which reach this script but not necessarily an interactive SSH
# shell -- so persist them, and source them from .bashrc, or starting the player
# by hand would silently fall back to a quick tunnel and a fresh token.
ENV_FILE="${ENV_FILE:-/workspace/magenta.env}"
if [[ -n "${MUSIC_TOKEN:-}${CF_TUNNEL_TOKEN:-}${MUSIC_HOSTNAME:-}" ]]; then
  say "persisting session identity to $ENV_FILE"
  # Written before the content, and only readable by root: this file holds the
  # tunnel credential, which is worth more than the music.
  install -m 600 /dev/null "$ENV_FILE"
  {
    [[ -n "${MUSIC_TOKEN:-}" ]]     && echo "MUSIC_TOKEN=$MUSIC_TOKEN"
    [[ -n "${CF_TUNNEL_TOKEN:-}" ]] && echo "CF_TUNNEL_TOKEN=$CF_TUNNEL_TOKEN"
    [[ -n "${MUSIC_HOSTNAME:-}" ]]  && echo "MUSIC_HOSTNAME=$MUSIC_HOSTNAME"
    :
  } >> "$ENV_FILE"

  if ! grep -q "$ENV_FILE" ~/.bashrc 2>/dev/null; then
    echo "set -a; . $ENV_FILE; set +a" >> ~/.bashrc
  fi
fi

# --- Rewriter settings -----------------------------------------------------
# .env is gitignored, so it never arrives with the clone -- which on a fresh
# instance means the one remaining manual step. Taking these from the template
# environment removes it. prompt_enhancer checks the environment before it
# checks .env, so the file is belt and braces: it is what makes them survive
# into a shell or tmux session that did not inherit the template's variables.
#
# LLM_MODEL is here for the same reason the token is: a box pinned to a
# different rewriter model would otherwise fall back to the default the moment
# you started the player over SSH instead of from the on-start hook, and the
# only symptom would be the music being steered by a model you did not choose.
for name in GROQ_API_KEY CEREBRAS_API_KEY LLM_MODEL LLM_ENDPOINT LLM_EFFORT; do
  value="${!name:-}"
  [[ -n "$value" ]] || continue
  if [[ ! -f "$MUSIC_ROOT/.env" ]]; then
    # Created empty and root-only before anything is written into it: this
    # file holds the API key.
    install -m 600 /dev/null "$MUSIC_ROOT/.env"
  elif grep -q "^$name=" "$MUSIC_ROOT/.env"; then
    continue  # already set in the file; leave what is there alone
  fi
  say "writing $name to $MUSIC_ROOT/.env"
  echo "$name=$value" >> "$MUSIC_ROOT/.env"
done

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

A new shell sources $ENV_FILE, so if the template supplies MUSIC_TOKEN,
CF_TUNNEL_TOKEN, or MUSIC_HOSTNAME you can drop the MUSIC_TOKEN= prefix above
and just run ./start.sh -- the fixed address and token are already in scope.

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
# The token goes through a file rather than the command line: a tmux command is
# visible in ps to every process on the box, and CF_TUNNEL_TOKEN in particular
# is a credential for your Cloudflare account, not just for this music server.
# Only when it was generated here -- a supplied MUSIC_TOKEN is already in the
# file, and appending it again would leave two lines to keep in agreement.
if [[ -z "${MUSIC_TOKEN:-}" ]]; then
  printf 'MUSIC_TOKEN=%s\n' "$token" >> "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"
tmux new-session -d -s music -c "$MUSIC_ROOT" \
  "set -a; . $ENV_FILE; set +a; ./start.sh --backend jax --model $MUSIC_MODEL \
     --preroll ${MUSIC_PREROLL:-6} --target-buffer ${MUSIC_TARGET_BUFFER:-4}"

# The address is printed inside tmux, where nothing reading this log can see it.
# Lift it back out so it is available without attaching. A named tunnel has a
# hostname known in advance, so there is nothing to wait for in that case.
url=""
if [[ -n "${MUSIC_HOSTNAME:-}" ]]; then
  url="https://${MUSIC_HOSTNAME#https://}"
  url="${url%/}"
else
  say "waiting for the tunnel address"
  for _ in $(seq 1 60); do
    url=$(tmux capture-pane -p -t music 2>/dev/null \
          | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1) || true
    [[ -n "$url" ]] && break
    tmux has-session -t music 2>/dev/null || { say "the player exited early:"; tmux capture-pane -p -t music 2>/dev/null || true; exit 1; }
    sleep 1
  done
fi

{
  echo "Music server URL:   ${url:-<not reported -- run: tmux attach -t music>}"
  echo "Music server token: $token"
} | tee "$SESSION_FILE"

cat <<EOF

[bootstrap] Both values are in $SESSION_FILE and go into Foundry's module
settings. Attach to the console with:  tmux attach -t music
EOF
