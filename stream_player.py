"""Interactive real-time streaming player for Magenta RT 2.

Generates audio continuously in a worker thread, plays it through the default
output device, and lets you retype the style prompt at any time -- the model
keeps its state, so the music morphs into the new prompt instead of restarting.
Type nothing and it just keeps going on the current prompt.

    python stream_player.py --prompt "slow ominous dungeon drone, low strings"

Runs on an Apple Silicon GPU through MLX by default; --backend jax runs the
same model on CUDA, for hosting it on a rented GPU box (see the README).

Defaults to mrt2_small, which generates ~2.9x faster than realtime on this
machine and so keeps latency low; pass --model mrt2_base for higher quality at
~0.93x realtime (playback then slows slightly to keep up -- see VarispeedBuffer).

Typed text is rewritten into a music style prompt by a small LLM (see
prompt_enhancer.py for endpoint/model/key), so you can type "they enter the dungeon" instead of
"slow ominous dungeon drone, low strings". Without a key it degrades to sending
your text straight to the music model.

Commands while running:
    <any text>      steer toward a new style (rewritten by the LLM)
    /raw <text>     steer using your exact words, no rewriting
    /llm on|off     toggle prompt rewriting
    /guidance ...   standing direction for the rewriter ("keep it ambient");
                    /guidance alone shows it, /guidance clear removes it
    /morph 4        seconds to cross-fade between styles (default 1.6)
    /temp 1.3       sampling temperature
    /topk 40        top-k
    /cfg 3.0        classifier-free guidance on the style prompt
    /status         buffer depth, playback speed, realtime factor
    /save out.wav   write everything generated so far
    /quit           stop (Ctrl-C works too)
"""

import argparse
import logging
import sys
import threading
import time

import numpy as np

from magenta_rt.config import MUSICCOCA

import prompt_enhancer

SAMPLE_RATE = 48_000
FRAMES_PER_SECOND = 25  # one model frame is 40 ms of audio
DEFAULT_PROMPT = 'slow ominous dungeon drone, low strings'


def _sounddevice():
  """Import sounddevice on demand.

  A headless server (--no-local-audio) has no output device and often no
  PortAudio library at all, so importing at module scope would make the box
  unusable for the thing it is actually there to do.
  """
  import sounddevice as sd
  return sd


def _backend(name: str):
  """Return the MagentaRT2 class for `name`.

  Imported here rather than at module scope because each backend pulls in a
  framework the other machine does not have: MLX is Apple-only, and the JAX
  build wants CUDA.
  """
  import magenta_rt
  if name == 'mlx':
    return magenta_rt.MagentaRT2StdMlxfn
  if name == 'jax':
    return magenta_rt.MagentaRT2Jax
  raise ValueError(f'unknown backend {name!r}')


class VarispeedBuffer:
  """Sample buffer read through a fractional pointer.

  This box generates at roughly 0.93x realtime, so a fixed-rate player would
  drain the buffer and stutter forever. Instead the reader tracks a playback
  speed that drifts down until consumption matches production, which trades a
  slight, steady pitch drop for gapless audio. The controller settles a little
  below `target_s` of buffered audio and stays there.
  """

  def __init__(self, target_s: float = 2.0, min_speed: float = 0.85,
               varispeed: bool = True):
    self._lock = threading.Lock()
    self._buf = np.zeros((0, 2), dtype=np.float32)
    self._pos = 0.0  # fractional read position into _buf
    self._target_s = target_s
    self._min_speed = min_speed if varispeed else 1.0
    self.speed = 1.0
    self.starved_samples = 0

  def push(self, block: np.ndarray) -> None:
    with self._lock:
      self._buf = np.concatenate([self._buf, block.astype(np.float32)])

  def available_seconds(self) -> float:
    with self._lock:
      return (len(self._buf) - self._pos) / SAMPLE_RATE

  def _update_speed(self, available_s: float) -> None:
    # Slow down when the buffer runs shallow; never play faster than realtime
    # (speeding up would raise the pitch and we can never get ahead anyway).
    desired = np.clip(1.0 + 0.15 * (available_s - self._target_s),
                      self._min_speed, 1.0)
    self.speed += 0.05 * (desired - self.speed)  # ~0.4 s time constant

  def read(self, n: int) -> np.ndarray:
    """Pull `n` output samples, resampling by the current playback speed."""
    out = np.zeros((n, 2), dtype=np.float32)
    with self._lock:
      self._update_speed((len(self._buf) - self._pos) / SAMPLE_RATE)
      idx = self._pos + self.speed * np.arange(n, dtype=np.float64)

      # Linear interpolation needs one sample past the last index we touch.
      usable = int(np.searchsorted(idx, len(self._buf) - 2.0, side='right'))
      if usable > 0:
        i0 = idx[:usable].astype(np.int64)
        frac = (idx[:usable] - i0)[:, None]
        out[:usable] = self._buf[i0] * (1.0 - frac) + self._buf[i0 + 1] * frac
        self._pos = idx[usable - 1] + self.speed
      if usable < n:
        self.starved_samples += n - usable

      # Drop the consumed head once it grows past a second.
      if self._pos > SAMPLE_RATE:
        drop = int(self._pos)
        self._buf = self._buf[drop:]
        self._pos -= drop
    return out


class StreamingPlayer:

  def __init__(self, args):
    self.args = args
    self.buffer = VarispeedBuffer(target_s=args.target_buffer,
                                  min_speed=args.min_speed,
                                  varispeed=not args.no_varispeed)
    self._stop = threading.Event()
    self._ready = threading.Event()
    self._load_error: BaseException | None = None
    self._cond_lock = threading.Lock()
    self._recorded: list[np.ndarray] = []
    self._rtf = 0.0  # realtime factor, exponentially smoothed
    self._generated_s = 0.0

    # MLX streams are thread-local and the TFLite style model is not
    # thread-safe, so the model is built and used only by the generator
    # thread. Other threads hand it plain text through _pending_prompt.
    self.mrt = None
    self.prompt = args.prompt
    self._pending_prompt: str | None = None
    self._emb = None
    self._morph_from = None
    self._morph_to = None
    self._morph_left = 0
    self._morph_total = 0

    self.temperature = args.temp
    self.top_k = args.topk
    self.cfg = args.cfg

    self.server = None
    self._steer_lock = threading.Lock()  # /music and the console can collide

    # House rules the rewriter follows on every line: how this table's music
    # should sound, and what recurring names mean. Kept here rather than only
    # on the enhancer so it survives the enhancer being absent (no API key)
    # and can still be reported to Foundry.
    self.guidance = prompt_enhancer.clean_guidance(args.guidance)

    key = prompt_enhancer.find_api_key(args.api_key)
    self.enhancer = (None if (key is None or args.no_llm) else
                     prompt_enhancer.PromptEnhancer(
                         key, model=args.llm_model, timeout=args.llm_timeout,
                         max_tokens=args.llm_max_tokens,
                         reasoning_effort=args.llm_effort,
                         guidance=self.guidance))
    self.use_llm = self.enhancer is not None

  def set_guidance(self, text: str | None) -> str:
    """Replace the standing direction; returns what was actually kept."""
    self.guidance = prompt_enhancer.clean_guidance(text)
    if self.enhancer is not None:
      self.enhancer.guidance = self.guidance
    return self.guidance

  def _embed(self, text: str) -> np.ndarray:
    return np.asarray(self.mrt.embed_style(text, use_mapper=True),
                      dtype=np.float32)

  def set_prompt(self, text: str) -> None:
    """Queue a new style; the generator cross-fades toward it."""
    with self._cond_lock:
      self.prompt = text
      self._pending_prompt = text

  def _steer(self, text: str, enhance: bool = True) -> tuple[str, str | None]:
    """Handle a typed line: optionally rewrite it, then morph toward it."""
    with self._steer_lock:
      style, elapsed, error = text, 0.0, None
      if enhance and self.use_llm and self.enhancer is not None:
        t0 = time.time()
        style, error = self.enhancer.enhance(text, current_style=self.prompt)
        elapsed = time.time() - t0
        if error:
          print(f'  ! rewrite failed ({error}); using your text as-is')

      self.set_prompt(style)
      if style != text:
        print(f'  llm ({elapsed:.1f}s): "{style}"')
        print(f'  -> morphing over {self.args.morph:.1f}s')
      else:
        print(f'  -> morphing to "{style}" over {self.args.morph:.1f}s')
      return style, error

  def _apply_tuning(self, tuning: dict) -> list[str]:
    """Apply generation knobs sent by a remote client; return what changed.

    Values are clamped rather than rejected: these arrive from a chat command
    or a slider in someone else's browser, and a wild number should not be
    able to make the generator unstable mid-session.
    """
    applied = []
    previous_guidance = self.guidance
    for key, value in tuning.items():
      try:
        if key == 'morph':
          self.args.morph = min(30.0, max(0.0, float(value)))
          applied.append(f'morph={self.args.morph:.1f}s')
        elif key == 'temp':
          with self._cond_lock:
            self.temperature = min(4.0, max(0.05, float(value)))
          applied.append(f'temp={self.temperature:.2f}')
        elif key == 'topk':
          with self._cond_lock:
            self.top_k = min(1024, max(1, int(value)))
          applied.append(f'topk={self.top_k}')
        elif key == 'cfg':
          with self._cond_lock:
            self.cfg = min(10.0, max(0.0, float(value)))
          applied.append(f'cfg={self.cfg:.1f}')
        elif key == 'guidance':
          # Foundry sends this with every prompt so a restart here cannot
          # leave the table on a stale direction, so the common case is that
          # it has not changed -- and saying so on every line would bury the
          # prompts it is meant to sit next to.
          if self.set_guidance(value) != previous_guidance:
            applied.append(f'direction={self.guidance!r}' if self.guidance
                           else 'direction cleared')
        elif key == 'llm':
          want = bool(value)
          if want and self.enhancer is None:
            applied.append('llm=off (no API key)')
          else:
            self.use_llm = want
            applied.append(f'llm={"on" if want else "off"}')
      except (TypeError, ValueError):
        applied.append(f'{key}=? (ignored)')
    return applied

  def _handle_remote_prompt(self, text: str, raw: bool,
                            tuning: dict | None = None) -> tuple[str, str | None]:
    """Called from an HTTP thread when someone types /music in Foundry."""
    if tuning:
      applied = self._apply_tuning(tuning)
      if applied:
        print(f'\n[foundry] {", ".join(applied)}')
      if not text:
        print('> ', end='', flush=True)
        return self.prompt, None

    print(f'\n[foundry] {"raw " if raw else ""}{text!r}')
    style, error = self._steer(text, enhance=not raw)
    print('> ', end='', flush=True)  # redraw the console prompt
    return style, error

  def _drain_loop(self) -> None:
    """Consume the buffer at realtime when the speakers are off.

    Generation is throttled by buffer depth, so with nothing reading the buffer
    the generator would stall and the network stream would starve.
    """
    block = 1024
    next_t = time.time()
    while not self._stop.is_set():
      self.buffer.read(block)
      next_t += block / SAMPLE_RATE
      time.sleep(max(0.0, next_t - time.time()))

  def _next_embedding(self) -> np.ndarray:
    """Current conditioning, stepping one chunk along any active cross-fade."""
    with self._cond_lock:
      pending, self._pending_prompt = self._pending_prompt, None

    if pending is not None:
      chunk_s = self.args.chunk / FRAMES_PER_SECOND
      self._morph_from = self._emb.copy()
      self._morph_to = self._embed(pending)
      self._morph_total = max(1, round(self.args.morph / chunk_s))
      self._morph_left = self._morph_total

    if self._morph_left <= 0:
      return self._emb
    self._morph_left -= 1
    if self._morph_left == 0:
      self._emb = self._morph_to
    else:
      alpha = 1.0 - self._morph_left / self._morph_total
      blend = (1.0 - alpha) * self._morph_from + alpha * self._morph_to
      self._emb = (blend / np.linalg.norm(blend)).astype(np.float32)
    return self._emb

  def _generate_loop(self) -> None:
    try:
      t0 = time.time()
      self.mrt = _backend(self.args.backend)(size=self.args.model)
      self._emb = self._embed(self.args.prompt)
      print(f'Loaded in {time.time() - t0:.1f}s', flush=True)
    except BaseException as e:  # surfaced by run()
      self._load_error = e
      self._ready.set()
      return
    self._ready.set()

    state = None
    # Cap how far ahead we generate: everything already in the buffer has to
    # play out before a new prompt is heard, so the cap *is* the reaction
    # latency. mrt2_small runs ~2.9x realtime and would otherwise race ahead.
    max_buffer_s = self.args.target_buffer + self.args.chunk / FRAMES_PER_SECOND
    while not self._stop.is_set():
      if self.buffer.available_seconds() > max_buffer_s:
        time.sleep(0.01)
        continue

      emb = self._next_embedding()
      with self._cond_lock:
        cfg_scales = {'musiccoca': self.cfg, 'notes': 1.0, 'drums': 1.0}
        temperature, top_k = self.temperature, self.top_k

      t0 = time.time()
      wav, state = self.mrt.generate(
          conditioning={MUSICCOCA.key: emb},
          cfg_scales=cfg_scales,
          temperature=temperature,
          top_k=top_k,
          frames=self.args.chunk,
          state=state,
      )
      elapsed = time.time() - t0

      samples = wav.samples
      audio_s = len(samples) / SAMPLE_RATE
      self._generated_s += audio_s
      rtf = audio_s / elapsed
      self._rtf = rtf if self._rtf == 0.0 else 0.8 * self._rtf + 0.2 * rtf

      self.buffer.push(samples)
      if self.server is not None:
        # Fed from the generator, which the buffer cap paces to realtime on
        # average -- so listeners receive audio at roughly the rate they play
        # it, in bursts bounded by the buffer depth.
        self.server.push_pcm(samples)
      if not self.args.no_record:
        self._recorded.append(samples.copy())

  def _audio_callback(self, outdata, frames, time_info, status) -> None:
    outdata[:] = self.buffer.read(frames)

  def save(self, path: str) -> None:
    if not self._recorded:
      print('Nothing recorded.')
      return
    import soundfile as sf
    sf.write(path, np.concatenate(self._recorded), SAMPLE_RATE)
    total = sum(len(c) for c in self._recorded) / SAMPLE_RATE
    print(f'Wrote {total:.1f}s to {path} (48kHz stereo, unstretched)')

  def _remote_status(self) -> dict:
    """What GET /status returns: enough for a client to draw a full UI."""
    return {
        'prompt': self.prompt,
        'model': self.args.model,
        'backend': self.args.backend,
        'llm': self.enhancer.model if self.use_llm else None,
        # Echoed back so the module can tell "the GM has not written one" from
        # "this server never received the one they wrote".
        'guidance': self.guidance,
        # Clients show this so a silent stream can be told apart from a stream
        # that is playing something the listener simply cannot hear.
        'starved': round(self.buffer.starved_samples / SAMPLE_RATE, 2),
        'morph': round(self.args.morph, 2),
        'temp': round(self.temperature, 3),
        'topk': self.top_k,
        'cfg': round(self.cfg, 2),
        'buffer': round(self.buffer.available_seconds(), 2),
        'speed': round(self.buffer.speed, 3),
        'gen': round(self._rtf, 2),
        'generated': round(self._generated_s, 1),
    }

  def status(self) -> str:
    llm = 'off'
    if self.use_llm:
      left = self.enhancer.remaining_requests
      llm = self.enhancer.model + (f' ({left} rewrites left in quota)'
                                   if left is not None else '')
    direction = (f'direction="{self.guidance}"\n  ' if self.guidance else '')
    return (f'llm={llm}\n  ' + direction +
            f'prompt="{self.prompt}"  buffer={self.buffer.available_seconds():.1f}s  '
            f'speed={self.buffer.speed:.3f}x  gen={self._rtf:.2f}x realtime  '
            f'generated={self._generated_s:.0f}s  '
            f'starved={self.buffer.starved_samples / SAMPLE_RATE:.2f}s')

  def run(self) -> None:
    if self.args.enhance_initial and self.use_llm:
      style, error = self.enhancer.enhance(self.args.prompt)
      if error:
        print(f'! rewrite failed ({error}); using your text as-is')
      elif style != self.args.prompt:
        print(f'llm: "{self.args.prompt}" -> "{style}"')
      self.args.prompt = self.prompt = style

    print(f'Loading {self.args.model}...', flush=True)
    gen = threading.Thread(target=self._generate_loop, daemon=True)
    gen.start()

    if self.args.serve:
      import music_server
      self.server = music_server.MusicServer(
          on_prompt=self._handle_remote_prompt,
          status_fn=self._remote_status,
          port=self.args.serve_port, bitrate=self.args.serve_bitrate,
          token=self.args.serve_token)
      self.server.start()
      suffix = f'?token={self.args.serve_token}' if self.args.serve_token else ''
      print(f'Streaming on http://0.0.0.0:{self.args.serve_port}/{suffix}  '
            f'(open it in a browser to test; /stream.mp3 is the audio)')
      if not self.args.serve_token:
        print('  no --serve-token set: anyone who can reach this port can '
              'change the music. Fine on a LAN, not on a public tunnel.')

    self._ready.wait()
    if self._load_error is not None:
      raise self._load_error

    # Never wait for more than the generator is allowed to buffer ahead.
    preroll = min(self.args.preroll,
                  self.args.target_buffer + self.args.chunk / FRAMES_PER_SECOND)
    print(f'Buffering {preroll:.1f}s...', end='', flush=True)
    while self.buffer.available_seconds() < preroll and gen.is_alive():
      time.sleep(0.1)
    if not gen.is_alive():
      print('\nGenerator thread died before playback could start.')
      return
    print(' playing.\n')
    print(f'Prompt: {self.prompt}')
    if self.use_llm:
      print(f'Describe what is happening and {self.enhancer.model} turns it '
            'into a style prompt (/raw to bypass, /llm off to disable).')
      if self.guidance:
        print(f'Standing direction: {self.guidance}')
    elif not self.args.no_llm:
      print('No LLM API key found, so text is sent to the music model as-is. '
            'Put GROQ_API_KEY=... in .env to enable prompt rewriting.')
    print('Type to steer it, /status, /save out.wav, /quit.\n', flush=True)

    if self.args.no_local_audio:
      threading.Thread(target=self._drain_loop, daemon=True).start()
      try:
        self._input_loop()
      except (KeyboardInterrupt, EOFError):
        print()
    else:
      stream = _sounddevice().OutputStream(
          samplerate=SAMPLE_RATE, channels=2, dtype='float32',
          blocksize=1024, device=self.args.device,
          callback=self._audio_callback)
      with stream:
        try:
          self._input_loop()
        except (KeyboardInterrupt, EOFError):
          print()

    self._stop.set()
    if self.server is not None:
      self.server.stop()
    gen.join(timeout=5)

  def _input_loop(self) -> None:
    while True:
      line = input('> ').strip()
      if not line:
        continue  # keep going on the current prompt

      if not line.startswith('/'):
        self._steer(line)
        continue

      parts = line.split(maxsplit=1)
      cmd, arg = parts[0], (parts[1].strip() if len(parts) > 1 else '')
      try:
        if cmd in ('/quit', '/q', '/exit'):
          return
        elif cmd == '/raw':
          if arg:
            self._steer(arg, enhance=False)
        elif cmd == '/llm':
          if arg == 'off':
            self.use_llm = False
            print('  prompt rewriting off')
          elif arg == 'on':
            if self.enhancer is None:
              print('  no API key; put GROQ_API_KEY=... in .env')
            else:
              self.use_llm = True
              print(f'  prompt rewriting on ({self.enhancer.model})')
          else:
            print(f'  prompt rewriting is '
                  f'{"on" if self.use_llm else "off"}; use /llm on|off')
        elif cmd == '/guidance':
          if not arg:
            print(f'  direction: {self.guidance or "(none)"}')
          elif arg.lower() in ('clear', 'none', 'off'):
            self.set_guidance('')
            print('  direction cleared')
          else:
            print(f'  direction: {self.set_guidance(arg)}')
        elif cmd == '/status':
          print('  ' + self.status())
        elif cmd == '/save':
          self.save(arg or 'out.wav')
        elif cmd == '/morph':
          self.args.morph = float(arg)
          print(f'  morph = {self.args.morph:.1f}s')
        elif cmd == '/temp':
          with self._cond_lock:
            self.temperature = float(arg)
          print(f'  temperature = {self.temperature}')
        elif cmd == '/topk':
          with self._cond_lock:
            self.top_k = int(arg)
          print(f'  top_k = {self.top_k}')
        elif cmd == '/cfg':
          with self._cond_lock:
            self.cfg = float(arg)
          print(f'  cfg(musiccoca) = {self.cfg}')
        else:
          print(f'  unknown command {cmd}')
      except ValueError:
        print(f'  bad argument for {cmd}: {arg!r}')


def main() -> None:
  p = argparse.ArgumentParser(description=__doc__,
                              formatter_class=argparse.RawDescriptionHelpFormatter)
  p.add_argument('--prompt', default=None,
                 help=f'starting style, or a scene to rewrite into one '
                      f'(default: "{DEFAULT_PROMPT}")')
  p.add_argument('--model', default='mrt2_small',
                 help='mrt2_small (~2.9x realtime, low latency) or mrt2_base '
                      '(~0.93x realtime, better quality but needs varispeed)')
  p.add_argument('--backend', default='mlx', choices=['mlx', 'jax'],
                 help='mlx runs on an Apple Silicon GPU (the default); jax '
                      'runs on CUDA, for hosting this on a rented GPU box. '
                      'The two load different weights -- see the README.')
  p.add_argument('--chunk', type=int, default=5,
                 help='model frames per generate call (5 = 200ms). Smaller '
                      'reacts faster to prompts, larger is slightly cheaper.')
  p.add_argument('--preroll', type=float, default=None,
                 help='seconds to buffer before playback starts '
                      '(default 1.0, or 2.0 for mrt2_base)')
  p.add_argument('--target-buffer', type=float, default=None,
                 help='buffer depth the playback-speed controller aims for; '
                      'also bounds how long a new prompt takes to be heard '
                      '(default 0.8, or 2.0 for mrt2_base)')
  p.add_argument('--min-speed', type=float, default=0.85,
                 help='slowest playback speed the controller may use')
  p.add_argument('--no-varispeed', action='store_true',
                 help='disable adaptive playback speed (gaps if generation '
                      'cannot keep up)')
  p.add_argument('--morph', type=float, default=1.6,
                 help='seconds to cross-fade between style prompts')
  p.add_argument('--temp', type=float, default=1.3)
  p.add_argument('--topk', type=int, default=40)
  p.add_argument('--cfg', type=float, default=3.0)
  p.add_argument('--no-llm', action='store_true',
                 help='send typed text straight to the music model without '
                      'rewriting it into a style prompt')
  p.add_argument('--llm-model', default=prompt_enhancer.DEFAULT_MODEL,
                 help='model used to rewrite prompts')
  p.add_argument('--llm-timeout', type=float, default=5.0)
  p.add_argument('--llm-max-tokens', type=int,
                 default=prompt_enhancer.DEFAULT_MAX_TOKENS,
                 help='output budget; reasoning models need room to think '
                      'before they answer')
  p.add_argument('--llm-effort', default='low',
                 choices=['low', 'medium', 'high'],
                 help='reasoning effort, for models that support it')
  p.add_argument('--guidance', default=None,
                 help='standing direction added to the rewriter\'s system '
                      'prompt, e.g. "keep the music ambient, never '
                      'overpowering". The Foundry module sets this itself and '
                      'will overwrite whatever is passed here.')
  p.add_argument('--api-key', default=None,
                 help='API key (default: GROQ_API_KEY/CEREBRAS_API_KEY from '
                      'the environment, then from .env)')
  p.add_argument('--serve', action='store_true',
                 help='stream the music over HTTP so Foundry players can '
                      'listen, and accept prompts from the /music command')
  p.add_argument('--serve-port', type=int, default=30001)
  p.add_argument('--serve-bitrate', type=int, default=128,
                 help='MP3 bitrate per listener (kbit/s)')
  p.add_argument('--serve-token', default=None,
                 help='shared secret required to listen or send prompts. Use '
                      'this whenever the port is reachable from the internet '
                      '(e.g. a tunnel for a remotely-hosted Foundry).')
  p.add_argument('--no-local-audio', action='store_true',
                 help='do not play through this machine\'s speakers; use with '
                      '--serve if you listen through Foundry (avoids hearing '
                      'both at once, offset by the stream delay)')
  p.add_argument('--device', default=None, help='output device name or index')
  p.add_argument('--list-devices', action='store_true')
  p.add_argument('--no-record', action='store_true',
                 help='do not keep generated audio in memory for /save')
  args = p.parse_args()

  if args.list_devices:
    print(_sounddevice().query_devices())
    return

  # mrt2_base generates slower than realtime, so it needs a deeper buffer for
  # the playback-speed controller to work with; mrt2_small can stay shallow
  # and therefore reacts to prompts much sooner.
  # A prompt the user typed may be a scene rather than a style, so it goes
  # through the rewriter; the built-in default is already a style.
  args.enhance_initial = args.prompt is not None
  if args.prompt is None:
    args.prompt = DEFAULT_PROMPT

  slow_model = args.model == 'mrt2_base'
  if args.target_buffer is None:
    args.target_buffer = 2.0 if slow_model else 0.8
  if args.preroll is None:
    args.preroll = 2.0 if slow_model else 1.0

  logging.basicConfig(level=logging.WARNING)
  StreamingPlayer(args).run()


if __name__ == '__main__':
  main()
