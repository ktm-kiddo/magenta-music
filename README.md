# Magenta Live Music

A real-time AI soundtrack for tabletop games. The music never stops and never
loops — it is generated continuously on your own machine. You type what is happening
("they enter the dungeon"), a small LLM rewrites that into a musical style, and
the score **morphs** into it over a couple of seconds without restarting.

Two ways to use it:

- **Solo at the desk** — a terminal player with a prompt box. Nothing else needed.
- **With Foundry VTT** — the same engine streams MP3 to every player's browser,
  and anyone at the table can steer it with `/music` in chat.

---

## Quick start

Requires an Apple Silicon Mac and Python 3.12 — see [Requirements](#requirements).

```bash
git clone https://github.com/ktm-kiddo/magenta-music.git
cd magenta-music

python3.12 -m venv .venv
.venv/bin/pip install magenta-rt mlx mlx-metal sounddevice soundfile lameenc requests

cp .env.example .env       # then put your Groq API key in it
.venv/bin/python stream_player.py
```

That's it. Model loads (~10–30 s the first time), it buffers a second, and music
starts playing through your speakers. Type to steer it, `/quit` to stop.

```
Loading mrt2_small...
Loaded in 12.4s
Buffering 1.0s... playing.

Prompt: slow ominous dungeon drone, low strings
Describe what is happening and openai/gpt-oss-20b turns it into a style prompt.
Type to steer it, /status, /save out.wav, /quit.

> the party is ambushed by bandits
  llm (0.4s): "urgent orchestral strings, driving percussion, tense brass, fast"
  -> morphing over 1.6s
```

---

## Running a session with Foundry

Everything below is per-session, from cold. One command does all of it:

```bash
cd magenta-music
MUSIC_TOKEN=$(openssl rand -hex 16) ./start.sh
```

That starts the music server, opens a Cloudflare tunnel so remote players can
reach it, and prints what you need:

```
  Music server URL (paste into Foundry module settings):
    https://<random-words>.trycloudflare.com

  Music server token (paste into Foundry module settings):
    <32 hex characters>

Loading mrt2_small...
Buffering 1.0s... playing.

>
```

Then in Foundry, under *Module Settings*:

| Setting | Value |
|---|---|
| Music server URL | the printed `https://…trycloudflare.com` address |
| Music server token | the printed token |

Now anyone at the table can type `/music they enter the dungeon` in chat, or use
the **Live Music** panel the module adds to Foundry's Music tab — play/pause for
the whole table, volume, a prompt box, preset buttons, and the generation knobs.
Presets are editable in place: steer the music until it sounds right, press **＋**
to keep it as a button, and test or adjust the lot in the preset editor.
The prompt you're left at is the same player console as solo mode, so you can
also steer it from the terminal. Ctrl-C stops the server and the tunnel together.

If the table hears nothing, `/music diagnose` in Foundry chat checks every cause
in turn and says which one it is.

**Both values change every run.** A quick tunnel gets a new random hostname each
time it starts, and the token above is freshly generated, so you re-paste both
each session. To stop re-pasting, use a named tunnel or [Tailscale
Funnel](#getting-an-https-address) for a fixed hostname, and a fixed token.

**Drop `MUSIC_TOKEN=…` and there is no password** — anyone who learns the URL can
listen and change the music. That is fine on a LAN, but a `trycloudflare.com`
address is on the open internet. The script warns you when it is missing.

### Keeping the settings in `.env`

`start.sh` reads `.env` as well as the environment, so the token and tunnel
settings can live beside the API key and a session becomes plain `./start.sh`:

```bash
GROQ_API_KEY=gsk_...
MUSIC_TOKEN=<a fixed secret>
CF_TUNNEL_TOKEN=<named-tunnel token>    # optional, for a fixed hostname
MUSIC_HOSTNAME=music.example.com        # required alongside CF_TUNNEL_TOKEN
```

The file is parsed rather than sourced — it is a place people paste secrets, not
a script — and anything already set in the environment wins, so a one-off
`MUSIC_TOKEN=… ./start.sh` still overrides what is in the file. `MUSIC_PORT`
changes the port the server and the tunnel agree on, if 30001 is taken.

### Doing it without the wrapper

Two terminals, which is all `start.sh` is doing:

```bash
.venv/bin/python stream_player.py --serve --no-local-audio
cloudflared tunnel --url http://localhost:30001   # second terminal
```

The tunnel prints its hostname on startup. `--serve` is the flag that opens port
30001 — without it there is nothing for the tunnel to point at, and players get
a 502.

If your players are on the same LAN you can skip the tunnel entirely and use
`http://<your-mac>:30001` — but only if Foundry itself is served over plain
HTTP, since an HTTPS page cannot load an HTTP stream.

### Check it before the game

Open <http://localhost:30001/> in a browser. That page has a player and a prompt
box; if you hear music there, the server half is working and anything still
broken is in Foundry's settings or the tunnel.

`--no-local-audio` stops your Mac's speakers. Use it whenever *you* are also
listening through Foundry, otherwise you hear the same music twice, offset by
the stream delay.

Full Foundry setup — installing the module, its settings, the `/music` chat
commands, presets, hosting and tunnels — is in
[foundry-module/README.md](foundry-module/README.md). The module is at **1.3.0**,
needs Foundry v12 or newer, and is verified against v14.

---

## Requirements

- **Apple Silicon Mac**, or a Linux box with an NVIDIA GPU — see [Running the
  model on a remote GPU](#running-the-model-on-a-remote-gpu). On the Mac the
  model runs through MLX; on NVIDIA it runs the same model through JAX. There
  is no CPU path.
- **Python 3.12** (developed against 3.12.14)
- `magenta-rt` 2.0.3, `mlx` + `mlx-metal`, `sounddevice`, `soundfile`,
  `lameenc`, `requests`, `numpy`
- An API key for any OpenAI-compatible chat endpoint, for the prompt rewriter.
  Optional — without one, your text goes to the music model unchanged.

```bash
python3.12 -m venv .venv
.venv/bin/pip install magenta-rt mlx mlx-metal sounddevice soundfile lameenc requests
```

Model weights download automatically on first run and are cached by
`huggingface_hub` — the first launch is slow, later ones are not.

The examples invoke `.venv/bin/python` directly rather than activating the venv.
Activating works too, but if your checkout path contains a space, some
`activate` scripts mishandle it — calling the interpreter by path always works.

---

## Steering the music while it runs

Anything that isn't a command is treated as a scene description and sent to the
LLM to be rewritten into a style.

| Type this | What happens |
|---|---|
| `they enter the dungeon` | LLM rewrites it into a style, music morphs to it |
| `/raw dark ambient, low strings` | Uses your exact words, skips the rewrite |
| `/llm on` / `/llm off` | Toggle prompt rewriting |
| `/guidance keep it ambient` | Standing direction the rewriter follows on every line |
| `/guidance` / `/guidance clear` | Show it / remove it |
| `/morph 4` | Seconds to cross-fade between styles (default 1.6) |
| `/status` | Buffer depth, playback speed, realtime factor, LLM quota |
| `/save out.wav` | Write everything generated so far to a file |
| `/temp 1.3` | Sampling temperature |
| `/topk 40` | Top-k |
| `/cfg 3.0` | How hard the model is pushed toward the style prompt |
| `/quit` | Stop (Ctrl-C also works) |

Pressing Enter on an empty line does nothing — the music just keeps going.

---

## Common flags

| Flag | Why you'd use it |
|---|---|
| `--serve` | Stream over HTTP so Foundry players can listen (port 30001) |
| `--serve-port 30002` | Use a different port (`start.sh` uses `MUSIC_PORT`) |
| `--no-local-audio` | Don't use your Mac's speakers |
| `--serve-token SECRET` | Require a shared secret — **mandatory on a public tunnel** |
| `--prompt "..."` | Set the opening style or scene |
| `--model mrt2_base` | Higher quality, slower (see below) |
| `--backend jax` | Run on an NVIDIA GPU instead of Apple Silicon ([details](#running-the-model-on-a-remote-gpu)) |
| `--serve-bitrate 96` | Lower MP3 bitrate if your upstream is tight |
| `--no-llm` | Send your text straight to the music model, no rewriting |
| `--llm-model ...` | Which model rewrites prompts (or `LLM_MODEL` in `.env`) |
| `--llm-endpoint ...` | Where that model lives (or `LLM_ENDPOINT`) |
| `--guidance "..."` | Opening standing direction for the rewriter (see below) |
| `--list-devices` | List audio outputs, for `--device` |
| `--no-record` | Don't hold generated audio in memory (disables `/save`) |

`.venv/bin/python stream_player.py --help` lists everything, including the
buffer-tuning knobs.

### Choosing a model

- **`mrt2_small`** (default) generates ~2.9× faster than realtime. Because it
  runs ahead easily, the buffer stays shallow and prompts take effect quickly.
  This is the right choice for live play.
- **`mrt2_base`** sounds better but generates at ~0.93× realtime — slightly
  slower than it plays. The player compensates by gradually slowing playback
  (a small, steady pitch drop) rather than stuttering, and it needs a deeper
  buffer, so the music reacts to your prompts noticeably later.

### Latency, and why

Expect **2–6 seconds** between typing and hearing the change: LLM rewrite
(~0.4 s) + cross-fade (1.6 s) + buffered audio that has to play out first +
browser buffering for remote listeners. Shortening `--target-buffer` reduces it
at the cost of stability.

---

## The LLM prompt rewriter

The music model responds to *musical* descriptors — genre, instrumentation,
mood, tempo — not to narration. "they enter the dungeon" is a story beat and
embeds poorly. So typed text goes through a small, fast LLM first, which turns
it into "slow ominous dungeon drone, low strings, dark ambient, sparse
percussion".

It talks to any OpenAI-compatible chat endpoint. It defaults to Groq
(`openai/gpt-oss-20b`); Cerebras also works. Both are fast enough (well under a
second) to sit in a live loop. Which model, and where, is
[configuration rather than an edit](#choosing-the-rewriter-model).

Everything degrades gracefully: no key, a network error, or a rate limit all
fall back to sending your text to the music model unchanged. You'll see a
warning line, and the music keeps playing.

### Standing direction

One line of scene text should not have to repeat how your table's music always
sounds, or what a recurring name means. `--guidance`, or `/guidance` while it
runs, adds that to the rewriter's system prompt for every line after it:

```
> /guidance keep it ambient and never overpowering; The Town is a small Spanish
  coastal village
  direction: keep it ambient and never overpowering; The Town is a small ...
```

It is folded in after the rules and marked as overriding the examples, so
"keep it ambient" beats the few-shot turn whose answer is "aggressive
orchestral metal". Blank lines are dropped, `<direction>` tags are stripped so
the block cannot be closed early, and the whole thing is capped at 1500
characters — past that it stops colouring the rules and starts drowning them.
It never changes the output format, and it does nothing at all with `--no-llm`
or without an API key, since there is no rewriter to direct.

`/status` shows the current direction, and `GET /status` returns it as
`guidance`.

The GM normally writes it in Foundry instead — *Module Settings → Music
direction*, or `/music direction ...` — which is the same field reached from
the other end. The module sends it with **every** prompt, so restarting this
process cannot leave the table on a direction nobody wrote any more, and
`--guidance` is only the value in force until the first prompt arrives from
Foundry. Set it here for a solo session at the console; set it there for a
table.

### Choosing the rewriter model

`LLM_MODEL` and `LLM_ENDPOINT`, in the environment or in `.env` beside the key:

```bash
LLM_MODEL=openai/gpt-oss-120b
# switching provider means switching both
# LLM_ENDPOINT=https://api.cerebras.ai/v1/chat/completions
```

Resolution is the same order as the key: `--llm-model` → environment → `.env` →
the built-in default. So a slower box can be pinned to a smaller model in its
own `.env` without every command line having to say so, and
`LLM_MODEL=... ./start.sh` still overrides for one run.

`LLM_EFFORT` goes with them, because providers disagree about reasoning effort:
`gpt-oss` wants `low`/`medium`/`high`, `qwen3` rejects those and wants
`none`/`default`. The value is passed through as typed, and `off` sends no such
field at all — which nothing can refuse, and is the escape hatch when a new
model returns `400` on the parameter.

Two things to expect when you swap models:

- **Reasoning models need output budget.** They spend it in a `<think>` block
  and only then answer; run out and you get no style at all. The failure is
  reported in plain words ("spent all 1000 tokens reasoning without answering")
  rather than becoming music — raise `--llm-max-tokens` or lower `LLM_EFFORT`.
- **Bigger is not obviously better.** This is a one-line formatting task with
  four worked examples. `gpt-oss-120b` answers it about as well as `20b` and
  takes longer, and latency is felt directly at the table.

### Setting the API key

Copy `.env.example` to `.env` and put your key in it:

```bash
cp .env.example .env
# GROQ_API_KEY=gsk_...
```

`.env` is gitignored, so the key stays out of the repo. The `API_KEY` constant
in `prompt_enhancer.py` is deliberately left blank — filling it in would put a
live credential one `git add .` away from being committed.

Resolution order is: `--api-key` flag → `API_KEY` constant → `GROQ_API_KEY` or
`CEREBRAS_API_KEY` in the environment → the same two names in `.env`.

A Cerebras key sent to Groq's endpoint just returns 401, so set
`LLM_ENDPOINT` (and `LLM_MODEL`) alongside it — see
[Choosing the rewriter model](#choosing-the-rewriter-model).

---

## Exposing it to remote players

Only needed when Foundry is hosted somewhere else, or your players aren't on
your LAN. Two things force this: your Mac is behind NAT, and a hosted Foundry
is almost always HTTPS — an `https://` page **cannot** play an `http://`
stream, so you need a real HTTPS address regardless.

**Set a token whenever the port is reachable from the internet.** Without one,
anyone who learns the URL can hijack your soundtrack:

```bash
.venv/bin/python stream_player.py --serve --no-local-audio \
  --serve-token "$(openssl rand -hex 16)"
```

Put the same value in *Module Settings → Music server token*. Mismatches return
403 for both listening and prompting. The player prints a warning at startup if
no token is set.

### Getting an HTTPS address

**Quickest — a cloudflared quick tunnel:**

```bash
cloudflared tunnel --url http://localhost:30001
#  -> https://something-random.trycloudflare.com
```

Paste that into *Module Settings → Music server URL*. The catch: the hostname is
random and **changes every restart**, so you re-paste it before every session.

**Permanent — a named Cloudflare tunnel.** Requires a domain on Cloudflare.
Create the tunnel under Zero Trust → Networks → Tunnels, route your chosen
hostname to `http://localhost:30001`, and copy the tunnel token. Then:

```bash
CF_TUNNEL_TOKEN=<token> MUSIC_HOSTNAME=music.example.com \
  MUSIC_TOKEN=<a fixed secret> ./start.sh
```

`start.sh` runs the named tunnel instead of a quick one, and the hostname is
yours permanently. Fix `MUSIC_TOKEN` as well and **both** Foundry settings are
filled in once and never touched again — which is the difference between a
server you reconfigure every session and one you just start.

Keep the tunnel token out of the repo and out of any image: it is a credential
for your Cloudflare account, not just for this music server. On Vast, pass it as
a template environment variable.

**Permanent — Tailscale Funnel.** Gives a fixed
`https://<machine>.<tailnet>.ts.net` address for free, with no domain required,
surviving restarts and reboots:

```bash
brew install tailscale
sudo brew services start tailscale   # daemon, and on every reboot
tailscale up                         # browser login
```

Then the funnel puts Foundry at `/` and the music server at `/music` on one
hostname — which also makes the stream same-origin with Foundry, so the CORS and
mixed-content caveats stop applying. Funnel has to be enabled once in the
tailnet ACL policy; the CLI prints the admin link.

### Bandwidth

Every listener streams **from your Mac**, not from the Foundry host — the audio
path is browser → your Mac directly, and never touches Foundry's server. At the
default 128 kbit/s that's ~16 KB/s each, so five players is roughly 0.6 Mbit/s
of sustained upstream from your home connection. Drop `--serve-bitrate` to 96 or
64 if that's tight.

---

## Running the model on a remote GPU

Nothing here has to run on your laptop. With `--serve` the player is already a
headless server — it streams MP3 over HTTP and takes prompts on `POST /prompt`
— so putting it on a rented GPU box (RunPod, Lambda, Vast.ai, a cloud VM) means
no Apple Silicon requirement, no music generation competing with Foundry for
your laptop, and listener bandwidth coming out of a datacenter instead of your
home upstream.

The one Mac-specific piece is the model backend. `magenta-rt` ships a JAX
implementation of the same model that runs on CUDA, and `--backend jax` selects
it. Everything else — the buffer, the cross-fade, the MP3 fan-out, the LLM
rewriter, the Foundry module — is unchanged.

### Setting up the box

Linux with an NVIDIA GPU and Python 3.12:

```bash
git clone https://github.com/ktm-kiddo/magenta-music.git
cd magenta-music

python3.12 -m venv .venv
.venv/bin/pip install "magenta-rt[jax]" "jax[cuda12]" soundfile lameenc requests numpy
```

No `mlx`, and no `sounddevice` — a headless box has no output device, and the
import is deferred so a missing PortAudio never comes up.

**The weights are different files, and nothing fetches them for you.** MLX
loads exported `.mlxfn` bundles; the JAX backend loads raw safetensors out of
`~/Documents/Magenta/magenta-rt-v2/checkpoints/`. Pull those, plus the shared
resources every backend needs (the MusicCoCa style encoder and SpectroStream):

```bash
.venv/bin/mrt models init                         # shared resources, ~1.3 GB
.venv/bin/mrt checkpoints download mrt2_small     # ~1.1 GB (mrt2_base is 9.8 GB)
```

Then run it the way `start.sh` does, plus the backend flag:

```bash
.venv/bin/python stream_player.py --backend jax \
  --serve --no-local-audio --serve-token "$(openssl rand -hex 16)"
```

Point *Module Settings → Music server URL* at the box and you're done.

### What changes about the setup

**Reaching it.** A cloud box with a public IP doesn't need cloudflared for
NAT — but a hosted Foundry is HTTPS, and an HTTPS page still cannot load an
HTTP stream, so you need TLS either way. Either terminate it in front of port
30001 (Caddy will do this in one line with a domain), or keep using a tunnel
exactly as on the Mac. `start.sh` works unmodified if you add `--backend jax`
to the end: `MUSIC_TOKEN=… ./start.sh --backend jax`.

**Set a token.** Non-negotiable here — the port is on the public internet by
construction, not by accident.

**Steering it.** Under `nohup` or systemd there is no console to type into. The
`/music` chat command and the web page at `/` both work regardless; run it under
`tmux` if you want the terminal prompt back.

**Buffer defaults assume Mac speeds.** They're chosen from the model name —
`mrt2_base` gets a deep 2 s buffer because it generates at ~0.93× realtime on
an M-series GPU. A datacenter GPU should push `mrt2_base` past realtime, at
which point that buffer is pure added latency. Watch `gen=` in `/status` and, if
it's comfortably above 1.0×, pass `--target-buffer 0.8` to get the reaction time
back.

**Cost shape.** This is a process that holds the GPU for the whole session, not
a burst of inference — you pay for wall-clock hours of play, and a small
instance sized for `mrt2_small` is the economical pick.

### Renting one per session

Setup is the slow part, not the model: `jax[cuda12]` pulls in a couple of GB of
CUDA wheels and the assets above are another ~2.4 GB, so a from-scratch box is
several minutes of waiting before any music. Model load itself is seconds.

So do that once, on storage that survives — a provider with a persistent volume
(RunPod, Vast), or a cloud VM you *stop* rather than destroy. Then a session is:

```bash
# start the box, then:
cd magenta-music
MUSIC_TOKEN=$(openssl rand -hex 16) ./start.sh --backend jax
# paste the printed URL + token into Foundry, play, Ctrl-C, stop the box
```

Stop the instance when you're done — an idle GPU bills exactly like a busy one.

### On Vast.ai specifically

Vast is a good fit — this is a small model, so its cheap consumer cards (a 3090
or 4090 is plenty for `mrt2_small`) are exactly the right tier. Four things are
Vast-specific:

**Rent on-demand, not interruptible.** A spot bid that gets outbid mid-session
takes the music down in the middle of the game. The saving is not worth it.

**Keep the tunnel — don't chase an open port.** Vast containers are usually
behind NAT, and direct ports mean filtering for machines that support them and
declaring `-p 30001:30001` at creation time. Even when you get one it's plain
HTTP, which a hosted HTTPS Foundry refuses to load. `cloudflared` solves NAT and
TLS together, so the Vast workflow is identical to the Mac one. Install it in
the container:

```bash
curl -fsSL -o /usr/local/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x /usr/local/bin/cloudflared
```

**Pick a template with Python 3.12.** `magenta-rt` itself only wants 3.11, but
`jax` 0.11 requires 3.12, and jax is what makes the GPU go. Ubuntu 24.04 is the
first release whose `python3` is 3.12, which is what pins the image choice.

You do *not* need a CUDA devel image — `jax[cuda12]` brings the CUDA runtime
along as pip wheels, so the base image's CUDA version barely matters and all the
host must supply is a driver new enough for CUDA 12 (535+). A `runtime` tag is
the smaller, correct pick. Note it ships no `python3` and no `git` at all;
[vast-setup.sh](vast-setup.sh) installs both.

**Point the assets at your persistent volume.** They default to
`~/Documents/Magenta`, which on a container is `/root/Documents` — easy to lose
and not where Vast's disk conventionally lives. Set `MAGENTA_HOME` once, in
`~/.bashrc` so every session and every `mrt` command agrees:

```bash
echo 'export MAGENTA_HOME=/workspace/magenta' >> ~/.bashrc && source ~/.bashrc
```

Stop (don't destroy) the instance between sessions: you keep paying a few cents
a month for the disk instead of re-downloading ~2.4 GB of assets and several GB
of CUDA wheels every time you play.

### Making a Vast template

[vast-setup.sh](vast-setup.sh) does all of the above in one idempotent pass —
packages, cloudflared, the repo, the venv, the model assets, `MAGENTA_HOME`.
Every step is guarded, so re-running it on a provisioned box is a no-op. That
matters because Vast runs a template's on-start script on *every* boot, not just
the first.

Vast's UI moves around, but a template is the same handful of fields whatever
they're called this month:

| Field | Value |
|---|---|
| Docker image | `nvidia/cuda:12.6.3-runtime-ubuntu24.04` — 24.04 for Python 3.12, `runtime` not `devel`. Ubuntu 24.04 builds start at CUDA 12.5.1; older tags like `12.4.1-runtime-ubuntu24.04` do not exist and fail with `manifest unknown`. |
| Launch mode | SSH — you want a shell for `tmux` and the player console |
| Disk space | 40 GB (≈6 GB CUDA wheels + 2.4 GB assets, plus room for `mrt2_base` later) |
| Environment | `MAGENTA_HOME=/workspace/magenta`, and `GROQ_API_KEY=…` if you want prompt rewriting |
| On-start script | the two lines below |
| Ports | none — the tunnel is outbound, so nothing needs opening |

On-start script:

```bash
curl -fsSL -o /tmp/vast-setup.sh \
  https://raw.githubusercontent.com/ktm-kiddo/magenta-music/main/vast-setup.sh
bash /tmp/vast-setup.sh
```

Provisioning takes several minutes on a fresh instance and its output goes to
`/var/log/onstart.log`, so the first boot looks idle for a while — `tail -f` it
rather than guessing. If you'd rather watch it directly, leave the on-start
field empty and run the same two lines by hand over SSH the first time; the
template is still worth having for the image, disk, and env vars.

Then every session is:

```bash
cd /workspace/magenta-music
MUSIC_TOKEN=$(openssl rand -hex 16) ./start.sh --backend jax
```

### Skipping provisioning with a prebuilt image

Provisioning from the script is several minutes of downloads on every fresh box.
[`docker/`](docker/) builds a template image with the slow half — the CUDA
wheels and the model weights — already inside, so a fresh instance costs an
image pull instead of an install. The repo itself is *not* baked in: the image
carries a small shim that clones or pulls it at boot and hands off to
[docker/magenta-bootstrap.sh](docker/magenta-bootstrap.sh), so a code or
boot-behaviour change costs a `git pull` rather than a multi-gigabyte rebuild.

[`.github/workflows/image.yml`](.github/workflows/image.yml) builds it on a
GitHub runner and pushes to GHCR (Actions → **template image** → Run workflow),
which is worth doing rather than building at home: the image is 10–15 GB and the
upload is the long pole. The template then points at
`ghcr.io/<owner>/magenta-music:latest` with `magenta-bootstrap` as the on-start
script, and with `MUSIC_AUTOSTART=1` the instance comes up already streaming,
writing its URL and token to `/workspace/music-session.txt`. Full details, and
the environment variables the boot script reads, are in
[docker/README.md](docker/README.md).

You do not need the image if you stop and start one instance rather than renting
fresh boxes — `/workspace` persists across a stop, so provisioning is already a
one-time cost there.

Not tested on CUDA hardware from here — the JAX backend is the one the upstream
package ships for it, and the player's calls into it are the same two methods
the MLX backend exposes, but the first run on a real GPU is still the first run.

---

## Troubleshooting

**Stutters or gaps in the audio.** Generation isn't keeping up. Check `/status`
— `gen=` below 1.0× realtime with a rising `starved=` confirms it. Switch to
`mrt2_small`, or raise `--target-buffer` to trade reaction time for stability.

**"No LLM API key found."** Your text is going to the music model unrewritten.
See the API key section above.

**"rate limited" after typing quickly.** Free tiers allow only a handful of
requests per minute and you can hit that by hand. `/status` shows your remaining
quota. It falls back to raw text, so the music keeps working.

**Rewrites time out.** Raise `--llm-timeout` (default 5 s), or `--no-llm` to skip
the LLM entirely.

**"spent all N tokens reasoning without answering."** A reasoning model burned
its budget thinking. Raise `--llm-max-tokens` or lower `--llm-effort`.

**Players hear nothing, but localhost:30001 works.** Almost always the URL or
scheme in module settings — an HTTPS Foundry page silently blocks an HTTP
stream as mixed content. Check the browser console for a mixed-content error.

**Players get 403.** Token mismatch between `--serve-token` and *Module
Settings → Music server token*.

**Browsers block autoplay.** Each player must click once inside the Foundry
window; the module posts a "click anywhere to enable" notice and starts on that
click.

**A player has drifted seconds behind.** The stream is live, not a playlist —
latecomers join wherever the music currently is, and there's no seeking. `/music
sync` skips their buffered delay back to the live edge.

**Port 30001 already in use.** A previous run didn't exit cleanly:
`lsof -ti:30001 | xargs kill`.

**Killing the Python process ends the stream.** Clients retry with backoff and
pick it up again when you restart.

---

## How it works

```
  you type / a player types /music        your Mac
  ┌──────────────────────────┐            ┌────────────────────────────────┐
  │ "they enter the dungeon" │──POST────► │ prompt_enhancer.py             │
  │                          │  /prompt   │   └─ LLM → style descriptors   │
  │                          │            │ stream_player.py               │
  │  <audio> ◄──── MP3 ──────┼────────────┤   ├─ Magenta RT 2 (MLX)        │
  └──────────────────────────┘  :30001    │   ├─ cross-fade between styles │
                                          │   └─ VarispeedBuffer           │
                                          │ music_server.py → MP3 fan-out  │
                                          └────────────────────────────────┘
```

A generator thread produces 200 ms chunks of audio continuously and pushes them
into a buffer. A new prompt is embedded and **cross-faded** against the current
embedding over `--morph` seconds, which is why the music bends into a new style
instead of cutting to it. The model keeps its state throughout.

Two details worth knowing, because they explain most of the tuning knobs:

- **The buffer cap *is* the reaction latency.** Everything already buffered has
  to play out before a new prompt is audible, so the generator is deliberately
  throttled rather than allowed to race ahead.
- **Playback speed is adaptive.** When generation runs slower than realtime, the
  reader gradually slows playback instead of stuttering — a slight, steady pitch
  drop in exchange for gapless audio.

For streaming, PCM is encoded to MP3 **once** and fanned out to all listeners
(128 kbit/s ≈ 16 KB/s each, versus 1.5 Mbit/s for raw PCM). Listeners who fall
more than ~2 s behind have their backlog dropped and are resynced to live — a
brief glitch beats drifting minutes behind the table.

## Files

| File | What it is |
|---|---|
| [start.sh](start.sh) | Server + Cloudflare tunnel in one command, prints the URL |
| [vast-setup.sh](vast-setup.sh) | Provisions a Vast.ai GPU box; idempotent, for the on-start hook |
| [stream_player.py](stream_player.py) | Main entry point — generation loop, buffer, console |
| [music_server.py](music_server.py) | HTTP server: MP3 broadcast, `/prompt`, `/status` |
| [prompt_enhancer.py](prompt_enhancer.py) | Scene text → music style, via an LLM |
| [foundry-module/](foundry-module/) | The Foundry VTT module and its setup guide |
| [foundry-module/build-release.sh](foundry-module/build-release.sh) | Builds the module zip for a GitHub release |
| [docker/](docker/) | Prebuilt Vast.ai template image: Dockerfile and boot scripts |
| [.github/workflows/image.yml](.github/workflows/image.yml) | Builds and pushes that image from a GitHub runner |
| [.env.example](.env.example) | Template for `.env` — API key, token, tunnel settings |
| `.env` | Those values, filled in — gitignored, never committed |

### HTTP endpoints (`--serve`)

| Endpoint | Purpose |
|---|---|
| `GET /` | Test page with a player and prompt box |
| `GET /stream.mp3` | The endless stream, one connection per listener |
| `GET /status` | JSON: prompt, model, backend, listeners, and every knob below |
| `POST /prompt` | JSON `{"text": ..., "raw": false}` plus any knob to retune |

All accept the token as `?token=...` or an `X-Music-Token` header — the query
parameter exists because an `<audio>` element cannot send custom headers.

`POST /prompt` takes `morph`, `temp`, `topk`, `cfg`, `llm` and `guidance`
alongside (or instead of) `text`, so a client can retune, or reset the standing
direction, without changing the style. Values
are clamped rather than rejected — they arrive from a slider in someone else's
browser, and a wild number should not be able to destabilise the generator
mid-session. The response echoes the full `/status` payload, so a client never
needs a follow-up request to redraw itself.
