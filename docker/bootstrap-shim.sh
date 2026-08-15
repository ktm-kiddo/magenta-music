#!/usr/bin/env bash
# The only bootstrap logic baked into the image, kept deliberately small.
#
# It does two things the repo's own bootstrap cannot: repair SSH permissions
# (which have to be fixed before anyone can log in to fix anything), and fetch
# the repo (which has to exist before its scripts can run). Then it hands off.
#
# Everything else lives in docker/magenta-bootstrap.sh in the repo, so boot
# logic ships with a git pull instead of a 12 GB image rebuild. That matters
# because the first version of this image shipped with a broken SSH setup and
# no way to correct it short of building again.
set -euo pipefail

MUSIC_ROOT="${MUSIC_ROOT:-/workspace/magenta-music}"
MUSIC_REPO="${MUSIC_REPO:-https://github.com/ktm-kiddo/magenta-music.git}"

say() { echo "[shim] $*"; }

# --- SSH permissions -------------------------------------------------------
# sshd's StrictModes refuses a key file that others could have written, and
# refuses it silently from the client's point of view -- the key is offered,
# matched, and rejected, which reads exactly like the key not being installed.
#
# The loop is not superstition: Vast writes authorized_keys around the same time
# this runs, and the ordering is not guaranteed, so a single chmod can land
# before the file exists. A minute of repair covers either ordering.
(
  for _ in $(seq 1 30); do
    [[ -d /root/.ssh ]] && {
      chown -R root:root /root/.ssh
      chmod 700 /root /root/.ssh
      [[ -f /root/.ssh/authorized_keys ]] && chmod 600 /root/.ssh/authorized_keys
    }
    sleep 2
  done
) >/dev/null 2>&1 &

# --- The repo --------------------------------------------------------------
if [[ -d "$MUSIC_ROOT/.git" ]]; then
  say "updating $MUSIC_ROOT"
  git -C "$MUSIC_ROOT" pull --ff-only || say "pull skipped (local changes)"
else
  say "cloning into $MUSIC_ROOT"
  mkdir -p "$(dirname "$MUSIC_ROOT")"
  git clone --depth 1 "$MUSIC_REPO" "$MUSIC_ROOT"
fi

next="$MUSIC_ROOT/docker/magenta-bootstrap.sh"
if [[ ! -f "$next" ]]; then
  echo "[shim] $next is missing -- the clone succeeded but the repo has no" >&2
  echo "[shim] bootstrap script. Check MUSIC_REPO points at the right fork." >&2
  exit 1
fi

say "handing off to the repo's bootstrap"
exec bash "$next"
