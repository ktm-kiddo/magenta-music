# Magenta Live Music — Foundry VTT setup

Live AI-generated soundtrack for your table. Anyone types `/music they enter the
dungeon` in Foundry chat; the Mac running `stream_player.py` rewrites that into a
style prompt, morphs the music into it, and every player hears the change.

Module **1.2.0** — requires Foundry v12 or newer, verified against v14.

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

If your players are remote, `./start.sh` in the repo root does this *and* opens
a tunnel and prints both settings below — see the main
[README](../README.md#running-a-session-with-foundry).

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
so a stale tag there quietly reinstalls the old code. The current release is
`v1.2.0`, so the next is:

```bash
./foundry-module/build-release.sh          # writes dist/magenta-music.zip
gh release create v1.2.1 dist/magenta-music.zip
```

The zip must have `module.json` at its root, not nested in a folder; the script
zips the *contents* of `magenta-music/` for exactly that reason, and prints the
version it read from the manifest so a forgotten bump is visible before the tag
is cut.

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
- **A prompt box and preset buttons** for anyone allowed to steer. Next to the
  presets a GM gets three more: **＋** keeps what is playing right now as a new
  preset, the **sliders** open the preset editor, and the **quill** opens the
  music direction (it is tinted when one is set).
- **Generation settings** — cross-fade, temperature, style strength — under a
  fold, applied to the server for everyone.
- **Errors, in place**, with a *Diagnose* button when something is wrong.
- **A window button** in the header, which puts the status widget below on
  screen — or takes it away again.

### Music direction

Standing rules the AI follows *every* time it turns a scene into music, written
once and applied to every prompt afterwards. Two kinds of thing belong here:

- **How this table should sound.** "Always keep the music ambient and in the
  background — never overpowering." "Prefer acoustic instruments; no synths."
- **What recurring names mean.** "When I say The Town, I mean a small Spanish
  coastal village." From then on `/music we arrive in The Town at dusk` gets
  flamenco guitar without anyone having to say so again.

Open it from the quill button in the Music tab, from *Configure Settings →
Module Settings → Music direction*, or with `/music direction edit`. One-line
rules can skip the editor entirely: `/music direction keep it ambient` sets it,
`/music direction` shows it, `/music direction clear` removes it.

The direction is part of the **AI rewrite**, so it has no effect on
`/music raw ...`, on presets marked "exact words", or while `/music llm off` —
in each of those the words go to the music model untouched, by design. The
editor's footer says which of those is true right now, and `/music diagnose`
reports it too.

It is one shared setting for the whole world, so only a GM can write it, and it
is capped at 1500 characters: it rides along with every prompt, and past that
length it stops colouring the rules the rewriter already has and starts
drowning them. Leave it empty for the stock behaviour.

> The direction is stored in the world but applied on the music machine, so the
> module resends it with every prompt. Restarting `stream_player.py` therefore
> cannot leave the table on a direction nobody wrote any more — but a music
> server older than module 1.3 ignores it, which the editor and *Diagnose* both
> say outright rather than letting it fail quietly.

### Presets

A preset is one button: a label, the music it plays, whether the AI rewrites it
first, and optionally the generation knobs it pins. Open the editor from the
sliders button in the Music tab, from *Configure Settings → Module Settings →
Preset buttons*, or with `/music presets edit`.

In it, every row has:

- **Test**, which plays that row *as currently typed* — before it is saved — so
  you can hear a preset while writing it. Testing changes what the table hears,
  because there is only one stream; **Undo testing** in the footer puts the
  music back on whatever was playing before the first test.
- **Keep the rewrite**, offered after testing a row that is not "exact words".
  The LLM's version of your description becomes the preset's stored words, so
  the button sounds the same every time instead of being re-improvised.
- **A broadcast button**, which overwrites the row with what is playing now.
- **Generation settings**, blank by default. A number pins that knob whenever
  the preset is used — a Combat preset can carry a short cross-fade and a high
  style strength, and a Travel preset can leave both alone.
- Duplicate, delete, and arrows to order them.

Nothing is written to the world until **Save presets**, so testing and tweaking
cost nothing. The usual way to build a good preset is backwards: steer the music
by hand until it sounds right, then press **＋** in the Music tab (or type
`/music preset save Ambush`) to keep exactly that — style prompt, cross-fade,
temperature and all.

Presets are shared by the table, so only a GM can edit them; anyone allowed to
steer can press the buttons.

Worlds upgrading from 1.1 keep their presets: the old
"`Label | style`"-per-line setting is converted to the new editor once, on
first load.

Because the stream is routed through Foundry's Music audio channel, the core
Music volume slider governs it exactly as it governs a playlist track. If the
browser cannot route it that way — a proxy that strips CORS headers is the
usual reason — the module falls back to direct playback and applies the slider
itself, which is a fallback you should never notice.

### The on-screen widget

The Music tab and `/music status` both answer "what is the music doing" — but
both are something you have to go and ask, and a GM running a fight does not
want to open a sidebar tab to find out whether the stream is still alive. So
there is an optional widget: a small panel floating over the canvas showing the
same thing, keeping itself up to date.

```
┌──────────────────────────────────┐
│ ● live                  ⧉  ⌃  ✕ │
│ urgent orchestral strings, driv… │
│ 3 listeners · 1.4× realtime · 0… │
└──────────────────────────────────┘
```

- **The state**, with the same dot as the sidebar panel: green live, amber
  connecting or stopped, red offline. When something is wrong the border turns
  red and the line below carries the reason.
- **The style** currently playing, which flashes green for a second whenever it
  changes — so a scene you just set is visibly acknowledged.
- **Listeners, generation rate and your own delay**, refreshed every second
  from what the browser knows and every six seconds from the server. Hover for
  the rest: model, backend, rewriter, cross-fade and buffer.
- **Drag it anywhere.** It stays where you leave it, across reloads, and cannot
  be dragged off screen or lost behind a resize.
- **The three buttons**, which appear on hover: ⧉ opens the Music tab for the
  actual controls, ⌃ folds it down to the status line alone, ✕ hides it.

It is **off by default and per-player**: turning it on puts it on *your* screen
only, and every person gets their own position for it. Turn it on with the
window button in the Music tab header, with `/music widget`, or under *Configure
Settings → Module Settings → Show the on-screen status widget*.

Because the widget polls on its own, leaving it on keeps the module in touch
with the music server even when the Music tab is closed.

### From chat

| Command | Effect |
|---|---|
| `/music the floor gives way` | Rewrites the scene into a style and morphs to it |
| `/music raw dark ambient, low strings` | Skips the AI rewrite, uses your words |
| `/music status` | Current style, listener count, generation rate |
| `/music widget` | Toggles the on-screen status readout (`on` / `off` to be explicit) |
| `/music diagnose` | Checks every reason it might not be working (`debug` too) |
| `/music play` / `pause` | The table if you are the GM, otherwise just you (`resume` and `stop` also work) |
| `/music on` / `off` | Mutes the stream for **you** only |
| `/music sync` | Jump to the live edge |
| `/music volume 0.4` | Your own volume |
| `/music morph 3` | Cross-fade seconds |
| `/music temp 1.2` / `cfg 3` / `topk 40` | Generation knobs |
| `/music llm on` / `off` | Prompt rewriting |
| `/music preset tavern` | Play a saved preset (matches on a prefix or a typo) |
| `/music presets` | List them |
| `/music presets edit` | Open the preset editor (GM) |
| `/music preset save Ambush` | Keep what is playing now as a preset (GM) |
| `/music direction` | Show the standing rules the AI follows |
| `/music direction keep it ambient` | Set them (GM) |
| `/music direction edit` | Open the direction editor (GM) |
| `/music direction clear` | Remove them (GM) |
| `/music` | Help |

Who may steer is set under *Module Settings → Who can change the music*:
everyone, trusted players and up, or GMs only.

## Settings reference

*Configure Settings → Module Settings → Magenta Live Music.* The first group is
world-scoped, so only a GM sets it and the whole table gets it:

| Setting | Default | What it does |
|---|---|---|
| Music server URL | `<Foundry's own scheme and host>:30001` | Where players' browsers reach `stream_player.py --serve`. Must match Foundry's scheme — an https page cannot load an http stream. |
| Music server token | empty | Must equal `--serve-token` on the music machine. Required whenever that machine is reachable from the internet. |
| Who can change the music | Everyone | Everyone, trusted players and up, or GMs only. |
| Announce music changes in chat | on | Posts the scene and the style it became. Off is quieter; the music still changes. |
| Preset buttons | — | Opens the preset editor (GM only). |
| Music direction | empty | Opens the direction editor (GM only): standing rules the AI rewriter follows on every prompt. |

The rest are per-player and affect nobody else:

| Setting | Default | What it does |
|---|---|---|
| Play the live music | on | Off silences the stream for you alone — the same as `/music off`. |
| Music volume | 0.5 | Your level, multiplied by Foundry's global Music slider. |
| Route through Foundry's Music channel | on | Makes the core Music slider control the stream. Needs CORS headers, which the server sends by default; falls back to direct playback on its own if it cannot. |
| Show the on-screen status widget | off | A small draggable readout of what the music is doing, floating over the canvas — the same information as `/music status`, without opening the Music tab. Yours alone. |
| Keep up with the live edge | on | Periodically skips buffered audio so you hear changes when everyone else does. Turn off if the skips are audible on your connection. |

## When it does not work

Run **`/music diagnose`**, or press *Diagnose* in the Music tab. It walks every
failure mode in order and reports what each one says: whether the URL is set and
scheme-compatible, whether the server answers, whether a failure is the server
being down or CORS being stripped, whether the token is accepted for both
`/status` and `/stream.mp3`, whether the generator is starving, whether the
music direction has actually reached the server, and whether your own volume or
the GM's stop is the reason for the silence.

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
