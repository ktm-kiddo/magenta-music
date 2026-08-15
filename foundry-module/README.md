# Magenta Live Music — Foundry VTT setup

Live AI-generated soundtrack for your table. Anyone types `/music they enter the
dungeon` in Foundry chat; the Mac running `stream_player.py` rewrites that into a
style prompt, morphs the music into it, and every player hears the change.

## How the pieces fit

```
  Foundry browser (any player)                 your Mac
  ┌────────────────────────────┐               ┌──────────────────────────┐
  │ /music they enter the...   │──POST /prompt→│ stream_player.py --serve │
  │ <audio> ←──── MP3 stream ──┼───────────────│  ├ Magenta RT (MLX)      │
  └────────────────────────────┘  :30001       │  └ prompt_enhancer (LLM) │
                                               └──────────────────────────┘
```

The audio is generated on the Mac and leaves it as an endless 128 kbit/s MP3,
which browsers play natively. That costs about **16 KB/s per listener**, so a
table of five needs roughly 0.6 Mbit/s of upstream.

## 1. Install the module

**By manifest URL (easiest, and the only option on hosted Foundry).** In
*Add-on Modules → Install Module → Manifest URL*, paste:

```
https://raw.githubusercontent.com/ktm-kiddo/magenta-music/main/foundry-module/magenta-music/module.json
```

**Or copy the folder in,** if Foundry runs on a machine you control:

```bash
cp -r foundry-module/magenta-music "<FOUNDRY_DATA>/Data/modules/"
```

`<FOUNDRY_DATA>` is the path shown in Foundry under **Configuration → Data
Path**. Either way, enable **Magenta Live Music** in *Manage Modules*.

## 2. Start the music

From your checkout of this repo:

```bash
.venv/bin/python stream_player.py --serve
```

Add `--no-local-audio` if you listen through Foundry yourself — otherwise you
hear the music twice, offset by the stream delay.

Open <http://localhost:30001/> in a browser to confirm it works before involving
Foundry. That page has a player and a prompt box.

## 3. Point the module at the Mac

In *Module Settings → Music server URL*, set the address **players' browsers**
will use. The default guesses `<same host as Foundry>:30001`, which is right
when Foundry runs on this Mac and you forward the port.

Because your players are remote, port `30001` has to reach the Mac the same way
port `30000` does — forward it on your router, or include it in your tunnel.

### If Foundry is served over HTTPS

A page loaded over `https://` **cannot** play an `http://` stream; the browser
blocks it as mixed content and players get silence. Pick one:

- **Reverse proxy (best).** If nginx/Caddy already fronts Foundry, add a route
  to the stream and set the URL to `https://your-domain/music`:

  ```nginx
  location /music/ {
      proxy_pass http://127.0.0.1:30001/;
      proxy_buffering off;          # required: buffering stalls a live stream
      proxy_read_timeout 24h;
  }
  ```

  ```caddy
  handle_path /music/* {
      reverse_proxy 127.0.0.1:30001 {
          flush_interval -1         # required: stream, don't buffer
      }
  }
  ```

- **Second tunnel hostname.** `cloudflared tunnel --url http://localhost:30001`
  gives you an `https://…trycloudflare.com` address; paste that in the setting.

## If Foundry is hosted somewhere else (Forge, VPS, a friend's box)

**The audio path does not change.** The stream goes browser → your Mac directly
and never touches the Foundry server, so Foundry's host is irrelevant to it.
Only two things differ: how the module gets installed, and the fact that your
Mac now has to be reachable from the open internet.

### Expose the Mac with a tunnel

Your Mac is behind NAT, and a hosted Foundry is almost always HTTPS — which
means an `http://` stream would be blocked as mixed content anyway. A tunnel
solves both at once by giving you an HTTPS address:

```bash
brew install cloudflared
cloudflared tunnel --url http://localhost:30001
#  -> https://something-random.trycloudflare.com
```

Paste that URL into *Module Settings → Music server URL*. Quick tunnels need no
account; the address changes each restart, so for a regular game use a named
tunnel or ngrok with a reserved domain.

### Set a token — this is not optional here

On a public tunnel, anyone who learns the URL can hijack your soundtrack. Start
the player with a shared secret:

```bash
.venv/bin/python stream_player.py --serve --serve-token "$(openssl rand -hex 16)"
```

Put the same value in *Module Settings → Music server token*. Without a match,
listening and prompting both return 403. The player warns at startup when no
token is set.

### Installing the module on a host you don't control

- **VPS / self-hosted:** copy the folder into `Data/modules/` as above, over
  scp or the host's file manager.
- **The Forge:** you cannot drop files into `Data/modules`, so use the manifest
  URL from step 1 — that is exactly what it is there for.

To cut a new release after changing the module, edit `module.json` to bump
**both** `version` and the tag inside `download` — Foundry compares the manifest
version against the installed one, then fetches whatever `download` points at,
so a stale tag there quietly reinstalls the old code. Then:

```bash
./foundry-module/build-release.sh
gh release create v1.0.1 dist/magenta-music.zip
```

The zip must have `module.json` at its root, not nested in a folder; the script
handles that.

### Bandwidth moves to your uplink

Every listener streams from your Mac, not from the Foundry host: 16 KB/s each,
so five players is ~0.6 Mbit/s of sustained upstream from your home connection.
Drop `--serve-bitrate` to 96 or 64 if that is tight.

## 4. Play

### From the Music tab

The module adds a **Live Music** panel to the top of Foundry's Playlists
("Music") sidebar tab, above the volume controls. It is the stream's transport:

- **Play / pause.** For a GM this starts and stops the music for the *whole
  table*; for a player it is their own mute. GMs get a second headphones button
  to silence themselves without stopping everyone.
- **Jump to live** skips whatever delay your browser has accumulated.
- **Volume**, on top of Foundry's own global Music slider.
- **A prompt box and preset buttons** for anyone allowed to steer.
- **Generation settings** — cross-fade, temperature, style strength — under a
  fold, applied to the server for everyone.
- **Errors, in place**, with a *Diagnose* button when something is wrong.

Because the stream is routed through Foundry's Music audio channel, the core
Music volume slider governs it exactly as it governs a playlist track. If the
browser cannot route it that way — a proxy that strips CORS headers is the
usual reason — the module falls back to direct playback and applies the slider
itself, which is a fallback you should never notice.

### From chat

| Command | Effect |
|---|---|
| `/music the floor gives way` | Rewrites the scene into a style and morphs to it |
| `/music raw dark ambient, low strings` | Skips the AI rewrite, uses your words |
| `/music status` | Current style, listener count, generation rate |
| `/music diagnose` | Checks every reason it might not be working |
| `/music play` / `pause` | The table if you are the GM, otherwise just you |
| `/music on` / `off` | Mutes the stream for **you** only |
| `/music sync` | Jump to the live edge |
| `/music volume 0.4` | Your own volume |
| `/music morph 3` | Cross-fade seconds |
| `/music temp 1.2` / `cfg 3` / `topk 40` | Generation knobs |
| `/music llm on` / `off` | Prompt rewriting |
| `/music presets` | List the configured presets |
| `/music` | Help |

Who may steer is set under *Module Settings → Who can change the music*:
everyone, trusted players and up, or GMs only.

## When it does not work

Run **`/music diagnose`**, or press *Diagnose* in the Music tab. It walks every
failure mode in order and reports what each one says: whether the URL is set and
scheme-compatible, whether the server answers, whether a failure is the server
being down or CORS being stripped, whether the token is accepted for both
`/status` and `/stream.mp3`, whether the generator is starving, and whether your
own volume or the GM's stop is the reason for the silence.

That exists because silence has half a dozen indistinguishable causes. The
module also names them as they happen rather than logging "unreachable": a 403
says whether the token is wrong or missing, a 404 says the address is not the
music server, an `http://` URL on an `https://` page is refused as mixed content
*before* the request, and a `localhost` URL is called out as unreachable from
anywhere but the GM's own machine.

## Things that will bite you

- **Browsers block autoplay.** Each player must click once in the Foundry window;
  the module posts a "click anywhere to enable" notice and starts on that click.
- **Expect 2–6 s of delay** between the command and hearing the change: LLM
  rewrite (~0.4 s), cross-fade (1.6 s), plus browser buffering.
- **The stream is live, not a playlist.** Latecomers join wherever the music
  currently is; there is no seeking or rewinding.
- **Volume** is the global Music slider times the module's own slider.
- **Killing the Python process** ends the stream; clients retry with backoff and
  pick it up again when you restart.
