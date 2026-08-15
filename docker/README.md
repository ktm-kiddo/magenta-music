# Template image

A prebuilt image for Vast.ai that skips provisioning. A fresh instance costs a
Vast image pull instead of an install: the CUDA wheels and the model weights —
the two things that make `vast-setup.sh` take minutes — are already inside.

The repo itself is **not** baked in. `magenta-bootstrap` clones or pulls it at
boot, so a code change costs a `git pull`, not a rebuild and a 10 GB push. Only
a dependency change or a different baked model needs a new image.

## Build it in CI (recommended)

[`.github/workflows/image.yml`](../.github/workflows/image.yml) builds it on a
GitHub runner and pushes to GHCR. Runners are x86, so nothing is emulated, and
the 10–15 GB upload happens on a datacenter connection instead of yours.

Actions tab → **template image** → **Run workflow**, choosing the model and tag.
Or push a tag: `git tag image-v1 && git push origin image-v1`.

Then make the package public under the repo's Packages tab — a Vast instance
cannot pull a private image without registry credentials.

## Build it locally

The image must be `linux/amd64`, whatever you build it on.

```bash
cd docker
docker buildx build --platform linux/amd64 -t youruser/magenta-music:latest .
docker push youruser/magenta-music:latest
```

**Where you build matters more than how.** The image is roughly 10–15 GB, and
pushing it is the long pole — from a home connection that is a slow afternoon,
not a coffee break. If you have a cheap x86 Linux VM in a datacenter, build and
push from there and it is minutes. On Apple Silicon the build also runs under
QEMU emulation; that is tolerable here, since the heavy steps are downloads and
wheel unpacking rather than compilation, but it is not fast.

The build fails deliberately if the jax CUDA plugin is missing, rather than
producing an image that imports jax fine and silently runs on the CPU. That
failure is worth catching at build time: at runtime it looks like a working
server that happens to generate at a third of realtime.

To bake the larger model instead — 9.8 GB more image, so know you want it:

```bash
docker buildx build --platform linux/amd64 \
  --build-arg MUSIC_MODEL=mrt2_base -t youruser/magenta-music:base .
```

## Use it on Vast

Create a template with:

| Field | Value |
|---|---|
| Image | `youruser/magenta-music:latest` |
| Launch mode | SSH (`openssh-server` is in the image; Vast injects the key) |
| On-start | `magenta-bootstrap` |
| Disk | 40 GB — the image counts against it |

Then rent on-demand, not interruptible. An outbid spot instance takes the music
down mid-game.

### Environment variables

Set these in the template, not in the image — a token baked into a public image
is a published token.

| Variable | Default | Effect |
|---|---|---|
| `MUSIC_AUTOSTART` | `0` | `1` starts the player in tmux at boot |
| `MUSIC_TOKEN` | generated per boot | Fix it and Foundry's token setting stops changing |
| `CF_TUNNEL_TOKEN` | — | Named-tunnel token; fixes the hostname too |
| `MUSIC_HOSTNAME` | — | The hostname routed to `localhost:30001`, e.g. `music.example.com` |
| `MUSIC_MODEL` | `mrt2_small` | Must be a model baked into the image |
| `MUSIC_REPO` | this repo | Point it at a fork |
| `MUSIC_PREROLL` | `6` | Seconds buffered before playback starts |
| `MUSIC_TARGET_BUFFER` | `4` | Buffer depth; also bounds prompt latency |

Whichever of these are set get written to `/workspace/magenta.env` (mode 600)
and sourced by new shells, so starting the player by hand picks up the same
fixed address and token rather than silently falling back to a quick tunnel.

With `MUSIC_AUTOSTART=1` the instance comes up already streaming, and the URL
and token are written to `/workspace/music-session.txt`. Attach to the console
with `tmux attach -t music`.

Autostart is only fully hands-off with a fixed address — `CF_TUNNEL_TOKEN` plus
`MUSIC_HOSTNAME`, or a Tailscale Funnel hostname. With a quick tunnel the
address changes every boot, so you still have to read the file and re-paste it
into Foundry, and a box that boots streaming to an address Foundry does not know
is not actually saving you anything.

## When to rebuild

- Dependencies change → rebuild
- You want a different model baked in → rebuild
- Any change to the Python or Foundry code → **no rebuild**, just restart the
  instance or `git pull` in `/workspace/magenta-music`

If you are stopping and starting one instance rather than renting fresh boxes,
you do not need this image at all — `/workspace` already persists, so a stopped
instance skips provisioning anyway.
