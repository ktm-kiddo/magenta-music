"""HTTP server that streams the generated music to browsers and takes prompts.

The music is generated on this machine, but Foundry players listen in their own
browsers, so the audio has to leave the box as something a browser can play
progressively. That means a shoutcast-style endless MP3: `<audio src=...>` picks
it up natively, and 128 kbit/s costs ~16 KB/s per listener where raw PCM would
cost 1.5 Mbit/s.

Endpoints:
    GET  /             a test page with a player, for checking without Foundry
    GET  /stream.mp3   the endless stream; one connection per listener
    GET  /status       JSON: current prompt, listener count
    POST /prompt       JSON {"text": ..., "raw": false} -> steers the music

Everything is CORS-open because the Foundry page is served from a different
origin (and usually a different port) than this server.
"""

import json
import queue
import threading
import http.server
import socketserver
import urllib.parse

import numpy as np

import lameenc

# A listener that reads slower than realtime (network hiccup, backgrounded tab)
# builds a queue, and every queued chunk is permanent added delay for them. Keep
# the ceiling to a couple of seconds: past it we throw the backlog away and
# resync to live, because a short glitch beats a listener drifting minutes
# behind the table. Each chunk is one generate-chunk of audio (~200 ms).
_MAX_CLIENT_BACKLOG = 10


def _first_frame_sync(data: bytes) -> int | None:
  """Offset of the first MPEG frame header (11 set sync bits), if any.

  Listeners join an already-running stream, and the chunk they land on may
  begin mid-frame or on LAME's info tag. Browsers resync by scanning, but
  stricter decoders just refuse the stream, so we trim to a frame boundary
  before sending a listener their first bytes.
  """
  for i in range(len(data) - 1):
    if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
      return i
  return None


class MP3Broadcaster:
  """Encodes PCM once and fans the frames out to every connected listener.

  push_pcm() is called only from the generator thread, which matters: the LAME
  encoder is stateful and not thread-safe.
  """

  def __init__(self, sample_rate: int = 48_000, bitrate: int = 128,
               channels: int = 2):
    self._encoder = lameenc.Encoder()
    self._encoder.set_bit_rate(bitrate)
    self._encoder.set_in_sample_rate(sample_rate)
    self._encoder.set_channels(channels)
    self._encoder.set_quality(5)  # 2=best/slowest, 7=fastest; 5 is transparent
    self._clients: set[queue.Queue] = set()
    self._lock = threading.Lock()
    self.total_bytes = 0

  def listener_count(self) -> int:
    with self._lock:
      return len(self._clients)

  def add_client(self) -> queue.Queue:
    q = queue.Queue(maxsize=_MAX_CLIENT_BACKLOG)
    with self._lock:
      self._clients.add(q)
    return q

  def remove_client(self, q: queue.Queue) -> None:
    with self._lock:
      self._clients.discard(q)

  def push_pcm(self, samples: np.ndarray) -> None:
    """Encode one block of float32 (n, 2) audio and hand it to every listener."""
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    frames = self._encoder.encode(pcm16.tobytes())
    if not frames:
      return
    data = bytes(frames)
    self.total_bytes += len(data)
    with self._lock:
      clients = list(self._clients)
    for q in clients:
      try:
        q.put_nowait(data)
      except queue.Full:
        # Behind by more than the ceiling: discard everything queued and jump
        # them to live, rather than play them an ever-growing delay.
        try:
          while True:
            q.get_nowait()
        except queue.Empty:
          pass
        try:
          q.put_nowait(data)
        except queue.Full:
          pass


_TEST_PAGE = """<!doctype html>
<meta charset="utf-8"><title>Magenta live stream</title>
<style>body{font-family:system-ui;margin:3rem auto;max-width:34rem;
background:#16161a;color:#e6e6e6}input,button{font:inherit;padding:.5rem}
input{width:70%}#s{opacity:.7;font-size:.9rem;white-space:pre-wrap}</style>
<h2>Magenta live stream</h2>
<audio controls autoplay src="/stream.mp3?token=__TOKEN__"></audio>
<p><input id="t" placeholder="they enter the dungeon">
<button onclick="send()">send</button></p>
<p id="s">loading...</p>
<script>
const TOKEN='__TOKEN__';
const q = TOKEN ? ('?token='+TOKEN) : '';
async function send(){
  const text = document.getElementById('t').value;
  if(!text) return;
  const r = await fetch('/prompt'+q,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({text})});
  document.getElementById('s').textContent = JSON.stringify(await r.json(),null,1);
}
document.getElementById('t').addEventListener('keydown',e=>{if(e.key==='Enter')send()});
setInterval(async()=>{
  try{const r=await fetch('/status'+q);const d=await r.json();
  document.getElementById('s').textContent =
    'prompt: '+d.prompt+'\\nlisteners: '+d.listeners;}catch(e){}
},3000);
</script>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
  # Close-delimited responses (shoutcast style) keep the endless stream simple:
  # no Content-Length, no chunked framing, the body just continues forever.
  protocol_version = 'HTTP/1.0'

  broadcaster: MP3Broadcaster = None
  on_prompt = None       # callable(text, raw) -> (style, error)
  status_fn = None       # callable() -> dict
  token = None           # shared secret, or None to allow anyone

  def log_message(self, fmt, *args):
    pass  # the player owns stdout; HTTP noise would bury the prompt line

  def _authorised(self) -> bool:
    """Check the shared token, if one is configured.

    When Foundry is hosted elsewhere this server has to be exposed to the
    internet, and without a token anyone who learns the URL can drive the
    music. The token is accepted as a query parameter as well as a header,
    because an <audio> element cannot send custom headers.
    """
    if not self.token:
      return True
    query = urllib.parse.urlparse(self.path).query
    supplied = (self.headers.get('X-Music-Token')
                or urllib.parse.parse_qs(query).get('token', [None])[0])
    return supplied == self.token

  def _cors(self):
    self.send_header('Access-Control-Allow-Origin', '*')
    self.send_header('Access-Control-Allow-Headers', 'Content-Type')
    self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

  def _send_json(self, payload: dict, status: int = 200):
    body = json.dumps(payload).encode()
    self.send_response(status)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', str(len(body)))
    self._cors()
    self.end_headers()
    self.wfile.write(body)

  def do_OPTIONS(self):
    self.send_response(204)
    self._cors()
    self.end_headers()

  def do_GET(self):
    path = self.path.split('?')[0]
    if not self._authorised():
      return self._send_json({'error': 'bad or missing token'}, 403)
    if path == '/stream.mp3':
      return self._serve_stream()
    if path == '/status':
      return self._send_json(self.status_fn())
    if path in ('/', '/index.html'):
      # The page must pass the token on to its own fetches and <audio>.
      token = urllib.parse.parse_qs(
          urllib.parse.urlparse(self.path).query).get('token', [''])[0]
      body = _TEST_PAGE.replace('__TOKEN__', urllib.parse.quote(token)).encode()
      self.send_response(200)
      self.send_header('Content-Type', 'text/html; charset=utf-8')
      self.send_header('Content-Length', str(len(body)))
      self._cors()
      self.end_headers()
      return self.wfile.write(body)
    self._send_json({'error': 'not found'}, 404)

  def _serve_stream(self):
    self.send_response(200)
    self.send_header('Content-Type', 'audio/mpeg')
    self.send_header('Cache-Control', 'no-cache, no-store')
    self.send_header('Pragma', 'no-cache')
    self._cors()
    self.end_headers()

    q = self.broadcaster.add_client()
    aligned = False
    try:
      while True:
        try:
          chunk = q.get(timeout=30)
        except queue.Empty:
          break  # generator died or stalled; let the browser reconnect
        if not aligned:
          offset = _first_frame_sync(chunk)
          if offset is None:
            continue  # no frame header in this chunk; wait for the next
          chunk, aligned = chunk[offset:], True
        self.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
      pass  # listener closed the tab
    finally:
      self.broadcaster.remove_client(q)

  def do_POST(self):
    if not self._authorised():
      return self._send_json({'ok': False, 'error': 'bad or missing token'}, 403)
    if self.path.split('?')[0] != '/prompt':
      return self._send_json({'error': 'not found'}, 404)
    try:
      length = int(self.headers.get('Content-Length', 0))
      payload = json.loads(self.rfile.read(length) or b'{}')
    except (ValueError, TypeError) as e:
      return self._send_json({'ok': False, 'error': f'bad JSON: {e}'}, 400)

    text = (payload.get('text') or '').strip()
    morph = payload.get('morph')
    if not text and morph is None:
      return self._send_json({'ok': False, 'error': 'empty prompt'}, 400)

    style, error = self.on_prompt(text, bool(payload.get('raw')), morph)
    self._send_json({'ok': True, 'style': style, 'text': text,
                     'warning': error})


class MusicServer:
  """Threaded HTTP server wrapping the broadcaster and the prompt callback."""

  def __init__(self, on_prompt, status_fn, port: int = 30001,
               host: str = '0.0.0.0', bitrate: int = 128,
               sample_rate: int = 48_000, token: str | None = None):
    self.broadcaster = MP3Broadcaster(sample_rate=sample_rate, bitrate=bitrate)
    self.port = port

    def status():
      base = status_fn()
      base['listeners'] = self.broadcaster.listener_count()
      return base

    handler = type('_BoundHandler', (_Handler,), {
        'broadcaster': self.broadcaster,
        'on_prompt': staticmethod(on_prompt),
        'status_fn': staticmethod(status),
        'token': token,
    })
    self._httpd = socketserver.ThreadingTCPServer((host, port), handler)
    self._httpd.daemon_threads = True
    self._httpd.allow_reuse_address = True

  def start(self) -> None:
    threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

  def stop(self) -> None:
    self._httpd.shutdown()

  def push_pcm(self, samples: np.ndarray) -> None:
    self.broadcaster.push_pcm(samples)

  def listener_count(self) -> int:
    return self.broadcaster.listener_count()
