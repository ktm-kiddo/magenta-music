#!/usr/bin/env bash
# Provision a Vast.ai instance to run the music server on its GPU.
#
# Written for Vast's on-start hook, which runs on *every* boot of the instance,
# not just the first -- so every step here is guarded and re-running it on a
# provisioned box is a no-op that finishes in a second. That is also what makes
# it safe to paste by hand if a step failed and you want to resume.
#
#   MUSIC_REPO=https://github.com/you/your-fork.git ./vast-setup.sh
#
# Everything lands under /workspace so it survives a stop/start; the container
# filesystem outside it is not somewhere to keep 2.4 GB of weights.
set -euo pipefail

MAGENTA_HOME="${MAGENTA_HOME:-/workspace/magenta}"
MUSIC_ROOT="${MUSIC_ROOT:-/workspace/magenta-music}"
MUSIC_REPO="${MUSIC_REPO:-https://github.com/ktm-kiddo/magenta-music.git}"
MUSIC_MODEL="${MUSIC_MODEL:-mrt2_small}"
export MAGENTA_HOME

say() { echo "[setup] $*"; }

# --- System packages -------------------------------------------------------
# A CUDA runtime image is Ubuntu plus CUDA libraries and nothing else -- no
# python3, no git. python3-venv is also packaged separately from python3, and
# venv creation fails with a confusing message when it is missing.
if ! command -v git >/dev/null || ! python3 -c 'import venv' 2>/dev/null; then
  say "installing system packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq git curl ca-certificates python3 python3-venv >/dev/null
fi

python3 - <<'EOF'
import sys
if sys.version_info < (3, 12):
    sys.exit(
        f"Python {sys.version.split()[0]} is too old -- jax 0.11 needs 3.12. "
        "Pick an Ubuntu 24.04 based template."
    )
EOF

# --- cloudflared -----------------------------------------------------------
# Vast containers sit behind NAT and serve no TLS, and a hosted Foundry is
# HTTPS -- the tunnel is the answer to both, so it is not optional here.
if ! command -v cloudflared >/dev/null; then
  say "installing cloudflared"
  arch=$(uname -m)
  case "$arch" in
    x86_64) cf_arch=amd64 ;;
    aarch64) cf_arch=arm64 ;;
    *) echo "unsupported architecture $arch" >&2; exit 1 ;;
  esac
  curl -fsSL -o /usr/local/bin/cloudflared \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cf_arch}"
  chmod +x /usr/local/bin/cloudflared
fi

# --- The repo --------------------------------------------------------------
if [[ -d "$MUSIC_ROOT/.git" ]]; then
  say "updating $MUSIC_ROOT"
  git -C "$MUSIC_ROOT" pull --ff-only || say "pull skipped (local changes)"
else
  say "cloning into $MUSIC_ROOT"
  git clone --depth 1 "$MUSIC_REPO" "$MUSIC_ROOT"
fi
cd "$MUSIC_ROOT"

# --- Python environment ----------------------------------------------------
# Several GB of CUDA wheels, so this is the slow step: stamped rather than
# re-resolved on every boot.
if [[ ! -f .venv/.provisioned ]]; then
  say "creating venv and installing (this is the slow part, several minutes)"
  [[ -d .venv ]] || python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q \
    "magenta-rt[jax]" "jax[cuda12]" soundfile lameenc requests numpy
  touch .venv/.provisioned
else
  say "venv already provisioned"
fi

# --- Model assets ----------------------------------------------------------
# The JAX backend reads raw safetensors from checkpoints/, which is a different
# file from the .mlxfn bundle the Mac uses -- and neither is fetched on demand.
mkdir -p "$MAGENTA_HOME"
if [[ ! -d "$MAGENTA_HOME/magenta-rt-v2/resources/musiccoca" ]]; then
  say "downloading shared resources (~1.3 GB)"
  .venv/bin/mrt models init
else
  say "shared resources present"
fi

ckpt="$MAGENTA_HOME/magenta-rt-v2/checkpoints/${MUSIC_MODEL}.safetensors"
if [[ ! -f "$ckpt" ]]; then
  say "downloading $MUSIC_MODEL checkpoint"
  .venv/bin/mrt checkpoints download "$MUSIC_MODEL"
else
  say "$MUSIC_MODEL checkpoint present"
fi

# --- Shell environment -----------------------------------------------------
# Every later `mrt` command and every manual run has to resolve the same
# MAGENTA_HOME, or it silently re-downloads into /root and you pay for it twice.
if ! grep -q 'MAGENTA_HOME' ~/.bashrc 2>/dev/null; then
  say "persisting MAGENTA_HOME to ~/.bashrc"
  echo "export MAGENTA_HOME=$MAGENTA_HOME" >> ~/.bashrc
fi

cat <<EOF

[setup] Ready. To start a session:

    cd $MUSIC_ROOT
    MUSIC_TOKEN=\$(openssl rand -hex 16) ./start.sh --backend jax --model $MUSIC_MODEL

Paste the printed URL and token into Foundry's module settings.
Put your Groq key in $MUSIC_ROOT/.env to enable prompt rewriting.
EOF
