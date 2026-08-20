"""Turn narrative scene descriptions into Magenta RT style prompts via an LLM.

MusicCoCa responds to musical descriptors -- genre, instrumentation, mood,
tempo -- not to narration. "they enter the dungeon" is a story beat, not a
style, and embeds poorly. This module runs the line through a small LLM to get
"slow ominous dungeon drone, low strings, dark ambient, sparse percussion".

Talks to any OpenAI-compatible chat endpoint; Groq and Cerebras both work and
are fast enough (well under a second for a line this short) to sit in an
interactive loop. The model, the endpoint and the key all come from .env next
to this file (`LLM_MODEL=`, `LLM_ENDPOINT=`, `GROQ_API_KEY=`) or from the
environment, so switching provider is a config change rather than an edit here;
--llm-model / --llm-endpoint / --api-key override for one run. The key is
deliberately not a constant in this file so it cannot be committed by accident.

The table can bend the rewriter without touching this file: `guidance` is free
text appended to the system prompt (see build_system_prompt), written by the GM
in Foundry or passed as --guidance to stream_player.py.
"""

import os
import pathlib
import re

import requests

DEFAULT_ENDPOINT = 'https://api.groq.com/openai/v1/chat/completions'
DEFAULT_MODEL = 'openai/gpt-oss-20b'
API_KEY = ''  # leave blank; the key belongs in .env, which is not committed

# Both are overridable without editing this file: --llm-model / --llm-endpoint,
# or these names in the environment or .env. The model and the endpoint are a
# pair -- a Cerebras model name sent to Groq's URL is a 401 -- so they are
# settable the same way rather than one in a flag and one in the source.
_MODEL_VARS = ('LLM_MODEL', 'MUSIC_LLM_MODEL')
_ENDPOINT_VARS = ('LLM_ENDPOINT', 'MUSIC_LLM_ENDPOINT')
_EFFORT_VARS = ('LLM_EFFORT', 'MUSIC_LLM_EFFORT')

# Accepted values for reasoning_effort are the provider's business, not ours:
# gpt-oss wants low/medium/high, qwen3.6 rejects those and wants none/default.
# So the value is passed through as typed, and these words mean "send no
# reasoning_effort field at all" -- the only setting no provider can refuse.
_EFFORT_OFF = ('off', 'omit', 'unset', '')

# Reasoning models spend most of their output budget on a `reasoning` field and
# only then emit `content`, so they need generous max_tokens or they hit
# finish_reason=length with no answer at all. `reasoning_effort: low` keeps that
# short (~14 vs ~284 reasoning tokens) and the round trip near 0.4s.
DEFAULT_MAX_TOKENS = 1000
DEFAULT_EFFORT = 'low'  # what gpt-oss wants; LLM_EFFORT for anything else
_REASONING_MODELS = re.compile(r'gpt-oss|qwen-?3|deepseek-r1|glm', re.IGNORECASE)

# Upper bound on descriptors kept from a reply, matching the 4-8 the system
# prompt asks for. Guards against models that emit several answers at once.
MAX_DESCRIPTORS = 8

_SYSTEM = """You convert scene descriptions into music style prompts for a \
real-time music generation model.

Reply with ONE line of comma-separated musical descriptors covering genre, \
instrumentation, mood, and tempo. Describe only how the music SOUNDS.

Rules:
- No sentences, no explanation, no quotes, no preamble.
- Never mention the story, characters, or sound effects (no footsteps, doors).
- 4 to 8 descriptors, under 15 words total.
- If the input is already a music style description, refine it, don't replace it.
- Always answer with exactly one style line, even if the input is vague, a \
greeting, or not a scene at all. Never ask a question.
 - ALWAYS make it instumental - no lyrics please."""

# Extra standing direction, written by the GM in Foundry (Module Settings ->
# Music direction) and sent along with every prompt. Two things it is for: how
# this table's music should sound in general ("keep it ambient, never
# overpowering"), and what recurring names mean ("The Town is a small Spanish
# coastal village") -- neither of which a per-scene line should have to repeat.
#
# Capped because it rides on every request, and because past a certain length
# it stops colouring the rules above and starts drowning them.
MAX_GUIDANCE_CHARS = 1500

# Appended after the rules, so it sits as close as possible to the few-shot
# turns it may have to beat: "keep it ambient" has to survive an example whose
# answer is "aggressive orchestral metal", hence saying so outright.
_GUIDANCE_BLOCK = """

Standing direction from the game master. It overrides the examples and \
anything above that contradicts it:
<direction>
{guidance}
</direction>
It says how this table's music should sound and what recurring names mean. It \
never changes the output format: still ONE line of comma-separated musical \
descriptors, nothing else."""


def clean_guidance(text: str | None) -> str:
  """Trim GM-written direction to what is safe to paste into the prompt.

  Mirrored by cleanGuidance() in the Foundry module so that what the GM sees
  in the editor is what the model is actually given.
  """
  if not text:
    return ''
  # The closing tag is the one string that could end the block early and let
  # the rest be read as instructions in their own right, so it cannot survive.
  text = re.sub(r'</?direction>', '', str(text), flags=re.IGNORECASE)
  lines = [line.strip() for line in text.splitlines()]
  return '\n'.join(line for line in lines if line)[:MAX_GUIDANCE_CHARS].strip()


def build_system_prompt(guidance: str | None = None) -> str:
  """The system prompt, with the table's standing direction folded in."""
  guidance = clean_guidance(guidance)
  if not guidance:
    return _SYSTEM
  return _SYSTEM + _GUIDANCE_BLOCK.format(guidance=guidance)


# Few-shot turns: small models follow the format far more reliably with these.
_EXAMPLES = [
    ('they enter the dungeon',
     'slow ominous dungeon drone, low strings, dark ambient, sparse percussion'),
    ('the party wins the battle',
     'triumphant orchestral fanfare, brass, timpani, major key, uplifting'),
    ('sneaking through the night market',
     'sparse hand percussion, oud, tense minor melody, quiet, mid-tempo'),
    ('boss fight begins',
     'aggressive orchestral metal, double kick drums, dissonant brass, fast'),
]


def _env_value(names: tuple[str, ...],
               env_file: pathlib.Path | None = None) -> str | None:
  """First non-empty value for `names`, from the environment then a .env file.

  The environment wins so that a one-off `LLM_MODEL=... ./start.sh` overrides
  the file, which is the order start.sh uses when it reads .env itself.

  `env_file` exists so tests can point at a throwaway path instead of the real
  .env -- a test that writes to the default location will destroy a real key.
  """
  for name in names:
    if os.environ.get(name):
      return os.environ[name]
  env_file = env_file or pathlib.Path(__file__).parent / '.env'
  if not env_file.exists():
    return None
  # Parsed into a dict first, then looked up in preference order: reading in
  # file order would let `GROQ_API_KEY=` (present but blank, as .env.example
  # ships it) shadow a CEREBRAS_API_KEY filled in below it.
  values = {}
  for line in env_file.read_text().splitlines():
    line = line.strip().removeprefix('export ').strip()
    if not line or line.startswith('#'):
      continue
    key, separator, value = line.partition('=')
    if separator:
      values[key.strip()] = value.strip().strip('"').strip("'")
  for name in names:
    if values.get(name):
      return values[name]
  return None


def find_api_key(explicit: str | None = None,
                 env_file: pathlib.Path | None = None) -> str | None:
  """Key from the flag, the API_KEY constant, the environment, or a .env file."""
  if explicit:
    return explicit
  if API_KEY:
    return API_KEY
  return _env_value(('GROQ_API_KEY', 'CEREBRAS_API_KEY'), env_file)


def find_model(explicit: str | None = None,
               env_file: pathlib.Path | None = None) -> str:
  """Rewriter model from the flag, the environment, .env, or the default."""
  return explicit or _env_value(_MODEL_VARS, env_file) or DEFAULT_MODEL


def find_endpoint(explicit: str | None = None,
                  env_file: pathlib.Path | None = None) -> str:
  """Chat-completions URL from the flag, the environment, .env, or the default."""
  return explicit or _env_value(_ENDPOINT_VARS, env_file) or DEFAULT_ENDPOINT


def find_effort(explicit: str | None = None,
                env_file: pathlib.Path | None = None) -> str | None:
  """Reasoning effort to send, or None to send none.

  Returns None for the _EFFORT_OFF words so that pinning an awkward model in
  .env is a config change too: a model that rejects `low` must be escapable
  without reaching for a command-line flag, or LLM_MODEL alone is not enough
  to switch to it.
  """
  value = explicit or _env_value(_EFFORT_VARS, env_file) or DEFAULT_EFFORT
  return None if value.strip().lower() in _EFFORT_OFF else value.strip()


def _clean(text: str) -> str:
  """Strip reasoning blocks, quotes, and stray prose from the model output."""
  text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
  if '</think>' in text:
    text = text.split('</think>')[-1]
  # An unterminated <think> is a model that ran out of budget mid-thought and
  # never reached its answer. There is no tail to keep, and the last line of
  # the reasoning is emphatically not a style -- qwen3.6 ends one on
  # '- Wait, "dark ambient" is 2 words', which would go straight to the music
  # model. Returning nothing makes the caller report it and pass the original
  # text through instead.
  if '<think>' in text.lower():
    return ''
  lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
  if not lines:
    return ''
  # Prefer the last non-empty line: any stray preamble comes first.
  line = lines[-1]
  line = re.sub(r'^(style|prompt|output|answer)\s*:\s*', '', line,
                flags=re.IGNORECASE)
  line = line.strip().strip('"\'').rstrip('.').strip()
  # These models sometimes emit several complete answers run together with no
  # separator at all ("...slow tempobrooding synth pads, rumbling bass..."),
  # which would hand the music model a 40-word soup. Collapse an exact
  # doubling, then cap the descriptor count so any other repetition is bounded.
  half, odd = divmod(len(line), 2)
  if half and not odd and line[:half] == line[half:]:
    line = line[:half]
  parts = [p.strip() for p in line.split(',') if p.strip()]
  return ', '.join(parts[:MAX_DESCRIPTORS])


class PromptEnhancer:
  """Rewrites a line of scene text into a music style prompt."""

  def __init__(self, api_key: str, model: str = DEFAULT_MODEL,
               timeout: float = 5.0, endpoint: str = DEFAULT_ENDPOINT,
               max_tokens: int = DEFAULT_MAX_TOKENS,
               reasoning_effort: str | None = 'low',
               guidance: str = ''):
    self.api_key = api_key
    self.model = model
    self.timeout = timeout
    self.endpoint = endpoint
    self.max_tokens = max_tokens
    self.reasoning_effort = reasoning_effort
    # Rebound while a rewrite may be in flight (an HTTP thread sets it, this
    # object is used from another). A plain string assignment is atomic, so
    # the worst case is one request that used the direction it started with.
    self.guidance = clean_guidance(guidance)
    self.remaining_requests: str | None = None  # from last response headers

  def enhance(self, text: str, current_style: str | None = None
              ) -> tuple[str, str | None]:
    """Return (style_prompt, error). On any failure the input is passed through
    unchanged, so a network problem degrades to the old behaviour."""
    messages = [{'role': 'system',
                 'content': build_system_prompt(self.guidance)}]
    for user, assistant in _EXAMPLES:
      messages.append({'role': 'user', 'content': user})
      messages.append({'role': 'assistant', 'content': assistant})
    if current_style:
      messages.append({
          'role': 'user',
          'content': (f'(currently playing: {current_style})\n{text}')})
    else:
      messages.append({'role': 'user', 'content': text})

    body = {'model': self.model, 'messages': messages, 'temperature': 0.4,
            'max_tokens': self.max_tokens, 'stream': False}
    # Only reasoning models accept this; sending it elsewhere risks a 400.
    if self.reasoning_effort and _REASONING_MODELS.search(self.model):
      body['reasoning_effort'] = self.reasoning_effort

    try:
      r = requests.post(
          self.endpoint,
          headers={'Authorization': f'Bearer {self.api_key}',
                   'Content-Type': 'application/json'},
          json=body,
          timeout=self.timeout)
    except requests.RequestException as e:
      return text, f'{type(e).__name__}: {e}'

    # Free-tier keys can be limited to a handful of requests per minute, which
    # is reachable just by typing quickly, so say so in plain words. Cerebras
    # and Groq spell these headers differently, hence the two lookups.
    if r.status_code == 429:
      limit = (r.headers.get('x-ratelimit-limit-requests-minute')
               or r.headers.get('x-ratelimit-limit-requests') or '?')
      retry = r.headers.get('retry-after')
      return text, (f'rate limited ({limit} requests allowed)'
                    + (f', retry in {retry}s' if retry else ''))

    if r.status_code != 200:
      detail = r.text[:200].replace('\n', ' ')
      return text, f'HTTP {r.status_code}: {detail}'

    self.remaining_requests = (
        r.headers.get('x-ratelimit-remaining-requests-minute')
        or r.headers.get('x-ratelimit-remaining-requests'))

    try:
      choice = r.json()['choices'][0]
    except (ValueError, KeyError, IndexError) as e:
      return text, f'unexpected response shape: {e}'

    # A reasoning model that runs out of budget mid-thought returns a message
    # with a `reasoning` field and no `content` at all, so don't index blindly.
    content = choice.get('message', {}).get('content')
    if not content:
      finish = choice.get('finish_reason')
      if finish == 'length':
        return text, (f'spent all {self.max_tokens} tokens reasoning without '
                      'answering; raise --llm-max-tokens or lower --llm-effort')
      return text, f'no content in response (finish_reason={finish})'

    cleaned = _clean(content)
    if not cleaned:
      # Same cause as the no-content case above, but this model spent the
      # budget inside `content` rather than a separate `reasoning` field.
      if choice.get('finish_reason') == 'length':
        return text, (f'spent all {self.max_tokens} tokens reasoning without '
                      'answering; raise --llm-max-tokens or lower --llm-effort')
      return text, 'model returned an empty style'
    return cleaned, None
