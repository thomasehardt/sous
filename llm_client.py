"""Provider-agnostic LLM interface backed by litellm - see PLAN.md Phase 17.

Every LLM-backed feature (flavor tagging, pantry shelf-life, craving's
query planner, recipe adaptation/invention, the easter-egg riff) used to
hand-roll its own `urllib.request` call straight at OLLAMA_HOST, hardcoded
- so the 2026-07-16 outage (Ollama unreachable) took every one of them
down at once with no way to point at a different provider without editing
container env vars and restarting. This module is the one place that
decision gets made now.

Provider/model is resolved in priority order:
1. The household's saved preference (preferences.py - UI-configurable on
   /preferences, takes effect immediately, no restart). This is what
   actually fixes the outage pattern above - an env var still needs a
   container restart to change.
2. SOUS_LLM_PROVIDER / SOUS_LLM_MODEL env vars.
3. The Ollama default this app has always used, so nothing changes in
   behavior until someone opts in.

Ollama's host (the equivalent "connection detail" for that provider,
since it has no API key) follows the same three-tier priority: the saved
`ollama_host` preference, then the OLLAMA_HOST env var, then a hardcoded
LAN default - also UI-configurable on /preferences, not just env-var
only, for the same reason provider/model and API keys are.

API keys: entered on /preferences (llm_credentials.py - a separate,
gitignored SQLite file, never recipes.db, which is git-tracked in this
project and would leak a stored secret into history), falling back to
provider-standard environment variables (ANTHROPIC_API_KEY,
GEMINI_API_KEY, ...) for anyone who prefers file-based config over the
UI. The UI-stored key wins when both are set.
"""
import json
import os

import litellm

import preferences as prefs_module
import llm_credentials

# (provider id, display name, default chat model, required API key env var or None)
PROVIDERS = [
    ('ollama', 'Ollama (local/LAN)', 'qwen3:8b', None),
    ('anthropic', 'Anthropic (Claude)', 'claude-opus-4-8', 'ANTHROPIC_API_KEY'),
    ('gemini', 'Google (Gemini)', 'gemini-2.5-flash', 'GEMINI_API_KEY'),
]
PROVIDER_IDS = [p[0] for p in PROVIDERS]
_PROVIDER_INFO = {p[0]: p for p in PROVIDERS}

DEFAULT_PROVIDER = 'ollama'
DEFAULT_EMBED_MODEL = 'nomic-embed-text'
DEFAULT_OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://192.168.68.105:11434')


def get_ollama_host(db_path: str = 'recipes.db') -> str:
    """Ollama API base URL, same three-tier priority as provider/model:
    saved preference -> OLLAMA_HOST env var -> hardcoded LAN default."""
    prefs = prefs_module.get_preferences(db_path)
    return prefs.get('ollama_host') or DEFAULT_OLLAMA_HOST


class LLMUnavailableError(RuntimeError):
    """Raised for any LLM failure - unreachable host, missing API key,
    timeout, empty/invalid response. One error type so callers' existing
    per-feature fallback logic (see query_planner.py etc.) only needs one
    except clause, same as when they caught bare urllib exceptions."""


def api_key_configured(provider: str) -> bool:
    """Whether this provider is actually usable - a key saved on
    /preferences, or (fallback) its env var is set. Used by the
    Preferences page to warn before someone selects a provider that will
    just fail on first use."""
    info = _PROVIDER_INFO.get(provider)
    if not info or info[3] is None:
        return True
    return llm_credentials.has_api_key(provider) or bool(os.environ.get(info[3]))


def _resolve_api_key(provider: str) -> str:
    """The actual key value to send: UI-stored wins over the env var."""
    info = _PROVIDER_INFO.get(provider)
    stored = llm_credentials.get_api_key(provider)
    if stored:
        return stored
    return os.environ.get(info[3], '') if info and info[3] else ''


def get_active_provider_and_model(db_path: str = 'recipes.db') -> tuple:
    """(provider, model) per the priority order documented above. Always
    returns a valid provider id (falls back to the Ollama default if the
    stored/env value is unrecognized, e.g. after a typo'd env var)."""
    prefs = prefs_module.get_preferences(db_path)
    provider = prefs.get('llm_provider') or os.environ.get('SOUS_LLM_PROVIDER') or DEFAULT_PROVIDER
    if provider not in _PROVIDER_INFO:
        provider = DEFAULT_PROVIDER
    model = prefs.get('llm_model') or os.environ.get('SOUS_LLM_MODEL') or _PROVIDER_INFO[provider][2]
    return provider, model


def _model_string_and_kwargs(provider: str, model: str, db_path: str = 'recipes.db') -> tuple:
    """litellm model string (the "<provider>/<model>" convention that
    picks the backend) plus any extra completion() kwargs that provider
    needs - including api_key/api_base, passed explicitly rather than
    relying on litellm's own env var auto-read, since a UI-stored value
    (if any) needs to take priority over whatever's in the environment."""
    kwargs = {}
    if provider == 'anthropic':
        model_string = f'anthropic/{model}'
        kwargs['api_key'] = _resolve_api_key(provider)
    elif provider == 'gemini':
        model_string = f'gemini/{model}'
        kwargs['api_key'] = _resolve_api_key(provider)
    else:
        model_string = f'ollama/{model}'
        kwargs['api_base'] = get_ollama_host(db_path)
        # Qwen3's "thinking" mode otherwise burns the whole token budget on
        # hidden <think> reasoning before it ever writes the actual answer,
        # returning empty content once max_tokens is hit.
        kwargs['think'] = False
    return model_string, kwargs


def chat(messages: list, json_mode: bool = False, timeout: int = 60, max_tokens: int = 1024,
         db_path: str = 'recipes.db') -> str:
    """One chat completion against the active provider/model. Returns the
    raw text content. json_mode requests the provider's native JSON mode
    (litellm's response_format={"type": "json_object"} - translated per-
    provider, e.g. Ollama's own `format: "json"` underneath) rather than
    just asking nicely in the prompt."""
    provider, model = get_active_provider_and_model(db_path)
    if not api_key_configured(provider):
        raise LLMUnavailableError(
            f"{provider} selected but {_PROVIDER_INFO[provider][3]} is not set"
        )
    model_string, kwargs = _model_string_and_kwargs(provider, model, db_path)
    if json_mode:
        kwargs['response_format'] = {'type': 'json_object'}

    try:
        response = litellm.completion(
            model=model_string,
            messages=messages,
            timeout=timeout,
            max_tokens=max_tokens,
            **kwargs,
        )
    except Exception as e:
        raise LLMUnavailableError(f"{provider}/{model}: {e}")

    content = (response.choices[0].message.content or '').strip()
    if not content:
        raise LLMUnavailableError(f"{provider}/{model}: empty response")
    return content


def chat_json(messages: list, timeout: int = 60, max_tokens: int = 1024,
              db_path: str = 'recipes.db') -> dict:
    """chat() with json_mode=True, then json.loads() the result. Raises
    LLMUnavailableError on a request failure OR invalid JSON in the
    response - one exception type either way for callers to catch."""
    content = chat(messages, json_mode=True, timeout=timeout, max_tokens=max_tokens, db_path=db_path)
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise LLMUnavailableError(f"model response was not valid JSON: {e}")


def test_connection(provider: str, model: str = '', api_key: str = '', ollama_host: str = '') -> tuple:
    """Minimal live connectivity check against explicit values, not the
    saved/env-resolved config - backs the Preferences page's "Test
    Connection" button, which needs to validate whatever's currently
    typed in the form (saved or not) rather than requiring a save first.
    A blank api_key/ollama_host falls back to whatever's already
    configured (stored/env), matching the rest of this page's "blank
    means don't change / use existing" convention. Returns (ok, message)
    - never raises, since this is meant to report failure as a result,
    not propagate one."""
    if provider not in _PROVIDER_INFO:
        return False, f"Unknown provider: {provider!r}"
    info = _PROVIDER_INFO[provider]
    model = model.strip() or info[2]

    kwargs = {}
    if provider == 'ollama':
        model_string = f'ollama/{model}'
        kwargs['api_base'] = ollama_host.strip() or get_ollama_host()
        kwargs['think'] = False
    else:
        model_string = f'{provider}/{model}'
        kwargs['api_key'] = api_key.strip() or _resolve_api_key(provider)

    try:
        response = litellm.completion(
            model=model_string,
            messages=[{'role': 'user', 'content': 'Reply with exactly one word: OK'}],
            timeout=15,
            max_tokens=10,
            **kwargs,
        )
    except Exception as e:
        return False, f"{model_string}: {e}"

    content = (response.choices[0].message.content or '').strip()
    return True, f'Connected to {model_string} successfully.' + (f' Response: "{content}"' if content else '')


def embed(text: str, db_path: str = 'recipes.db') -> list:
    """Embedding vector for one string. Ollama-only regardless of the
    active chat provider - ingredient_embeddings.vector's dimensionality
    is tied to the specific embedding model already used across the
    corpus, so switching embedding providers would need a full re-embed
    of ~7,200 ingredients, deliberately out of scope here. Returns []
    on failure (matches embeddings.py's pre-existing behavior - callers
    already treat an empty vector as "no embedding available"), not an
    exception - unlike chat()/chat_json(), which are new call sites with
    no established caller convention to preserve."""
    try:
        response = litellm.embedding(
            model=f'ollama/{DEFAULT_EMBED_MODEL}',
            input=[text],
            api_base=get_ollama_host(db_path),
        )
        return response.data[0]['embedding']
    except Exception as e:
        print(f"Error getting embedding: {e}")
        return []
