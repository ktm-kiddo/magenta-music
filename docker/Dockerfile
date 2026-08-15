# A Vast.ai template image with the slow half of vast-setup.sh already done:
# the CUDA wheels and the model weights, which are ~10 GB between them and the
# only reason provisioning takes minutes rather than seconds.
#
# What is deliberately NOT baked in is the repo itself. Code changes land with a
# git pull at boot; baking them would mean a multi-gigabyte rebuild and push for
# every one-line fix, which is the failure mode that makes custom images not
# worth having.
#
#   docker buildx build --platform linux/amd64 -t youruser/magenta-music:latest .
#
# See README.md in this directory -- where you build it matters more than how.

# Matches the stock Vast template this was developed against. Ubuntu 24.04 is
# not incidental: its python3 is 3.12, and jax 0.11 requires 3.12.
FROM nvidia/cuda:12.6.3-runtime-ubuntu24.04

ARG MUSIC_MODEL=mrt2_small
ENV MAGENTA_HOME=/opt/magenta \
    MUSIC_VENV=/opt/venv \
    DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

# openssh-server is here because Vast's SSH access expects the image to provide
# it; the key itself is injected at launch, so nothing secret is baked in.
RUN apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends \
      git curl ca-certificates python3 python3-venv openssh-server tmux \
 && rm -rf /var/lib/apt/lists/*

# Vast containers sit behind NAT and serve no TLS, and a hosted Foundry is
# HTTPS -- the tunnel answers both, so it belongs in the image.
RUN arch=$(uname -m) \
 && case "$arch" in \
      x86_64) cf_arch=amd64 ;; \
      aarch64) cf_arch=arm64 ;; \
      *) echo "unsupported architecture $arch" >&2; exit 1 ;; \
    esac \
 && curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${cf_arch}" \
 && chmod +x /usr/local/bin/cloudflared

# --- The expensive layer ---------------------------------------------------
# jax[cuda12] is installed as its own step rather than folded into the line
# above. Resolved together, pip can satisfy magenta-rt's jax pin while leaving
# the CUDA plugin out, and the result is a jax that imports fine and silently
# runs on the CPU -- which looks like a working server generating at a third of
# realtime, not like an error.
RUN python3 -m venv "$MUSIC_VENV" \
 && "$MUSIC_VENV/bin/pip" install -q --upgrade pip \
 && "$MUSIC_VENV/bin/pip" install -q \
      "magenta-rt[jax]" soundfile lameenc requests numpy \
 && "$MUSIC_VENV/bin/pip" install -q "jax[cuda12]"

# Fail the build rather than ship an image that runs on the CPU. jax.devices()
# cannot be the test -- there is no GPU during a build -- but the plugin's
# presence can be, and its absence is exactly what the silent fallback is.
RUN "$MUSIC_VENV/bin/python" - <<'PY'
from importlib.metadata import distributions
names = sorted(d.metadata["Name"].lower() for d in distributions())
cuda = [n for n in names if "cuda" in n]
if not cuda:
    raise SystemExit(
        "no jax CUDA plugin in the venv -- this image would run on the CPU. "
        "Check the jax[cuda12] install step."
    )
print("jax CUDA packages:", ", ".join(cuda))
PY

# --- The other expensive layer ---------------------------------------------
# The JAX backend reads raw safetensors from checkpoints/, which is a different
# file from the .mlxfn bundle the Mac uses -- and neither is fetched on demand.
RUN "$MUSIC_VENV/bin/mrt" models init \
 && "$MUSIC_VENV/bin/mrt" checkpoints download "$MUSIC_MODEL"

# Marks the venv as complete so vast-setup.sh, if it is ever run inside this
# image, skips its install step instead of redoing all of the above.
RUN touch "$MUSIC_VENV/.provisioned"

# sshd will not read a key file that others could have written, and Vast creates
# this directory itself if the image has not. Creating it here with the right
# mode is what stops "Authentication refused: bad ownership or modes" -- an
# error the client never sees, since from its side the key is simply refused.
RUN mkdir -p /root/.ssh \
 && chmod 700 /root /root/.ssh

# Only the shim is baked in. It repairs SSH permissions, fetches the repo, and
# hands off to the repo's own bootstrap -- so boot logic ships with a git pull
# rather than a rebuild.
COPY bootstrap-shim.sh /usr/local/bin/magenta-bootstrap
RUN chmod +x /usr/local/bin/magenta-bootstrap

# Every later `mrt` call has to resolve the same MAGENTA_HOME, or it silently
# re-downloads into /root and you pay for the weights twice.
RUN echo "export MAGENTA_HOME=$MAGENTA_HOME" >> /root/.bashrc

# No ENTRYPOINT on purpose: Vast supplies its own startup, and an entrypoint
# that fights it costs you SSH. Run magenta-bootstrap from the template's
# on-start field instead.
