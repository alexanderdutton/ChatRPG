"""Provider abstraction for ChatRPG LLM backends.

Two backends:
- GeminiProvider: the original Google Gemini path (google-genai SDK), lazily
  initialized so GEMINI_API_KEY is not required at import time.
- LocalOpenAIProvider: any OpenAI-compatible HTTP API (Ollama, LM Studio,
  llama.cpp server, vLLM) via httpx.

Config (env):
- CHATRPG_PROVIDER=local|gemini|auto  (default: auto = local if reachable, else Gemini)
- CHATRPG_LOCAL_BASE_URL (default: http://127.0.0.1:11434/v1)
- CHATRPG_LOCAL_MODEL    (default: first model advertised by the local server)
- CHATRPG_GEMINI_MODEL   (default: gemini-flash-latest)
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"


class ProviderError(Exception):
    """Raised when no provider can serve a request."""


class LLMProvider:
    name = "base"

    async def generate(self,
                       conversation_history: List[Dict],
                       system_instruction: Optional[str] = None,
                       json_mode: bool = False) -> str:
        """Returns raw text for the conversation."""
        raise NotImplementedError

    async def list_models(self) -> List[str]:
        raise NotImplementedError

    async def is_reachable(self) -> bool:
        raise NotImplementedError

    def is_configured(self) -> bool:
        raise NotImplementedError


class LocalOpenAIProvider(LLMProvider):
    """OpenAI-compatible chat-completions backend (Ollama/LM Studio/llama.cpp/vLLM)."""

    name = "local"

    def __init__(self,
                 base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None,
                 timeout: float = 120.0):
        self.base_url = (base_url or os.getenv(
            "CHATRPG_LOCAL_BASE_URL", DEFAULT_LOCAL_BASE_URL)).rstrip("/")
        self.model = model or os.getenv("CHATRPG_LOCAL_MODEL") or None
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=httpx.Timeout(timeout),
        )

    def is_configured(self) -> bool:
        return True  # A local server needs no credentials.

    async def is_reachable(self) -> bool:
        try:
            await self.list_models()
            return True
        except Exception as e:
            logger.info(f"Local LLM server not reachable at {self.base_url}: {e}")
            return False

    async def list_models(self) -> List[str]:
        resp = await self._client.get("/models")
        resp.raise_for_status()
        data = resp.json()
        return [m.get("id") for m in data.get("data", []) if m.get("id")]

    async def resolve_model(self) -> str:
        if self.model:
            return self.model
        models = await self.list_models()
        if not models:
            raise ProviderError(
                f"Local LLM server at {self.base_url} advertises no models. "
                "Pull one (e.g. `ollama pull llama3.1:8b`) or set CHATRPG_LOCAL_MODEL.")
        self.model = models[0]
        return self.model

    async def generate(self,
                       conversation_history: List[Dict],
                       system_instruction: Optional[str] = None,
                       json_mode: bool = False) -> str:
        model = await self.resolve_model()
        messages: List[Dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        for entry in conversation_history:
            messages.append({"role": entry["role"],
                             "content": "\n".join(entry.get("parts", []))})

        payload: Dict[str, Any] = {"model": model, "messages": messages}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = await self._client.post("/chat/completions", json=payload)

        # Some OpenAI-compatible servers reject response_format; retry without it.
        if resp.status_code == 400 and json_mode:
            logger.info("Local server rejected response_format=json_object; retrying without it.")
            payload.pop("response_format")
            resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"Malformed chat-completions response: {e}: {data!r}")


class GeminiProvider(LLMProvider):
    """Original Gemini backend; client/key initialized lazily."""

    name = "gemini"

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.getenv("CHATRPG_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        self._client = None

    def is_configured(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY"))

    def _get_client(self):
        if self._client is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise ProviderError(
                    "GEMINI_API_KEY not set. Set it, or run with "
                    "CHATRPG_PROVIDER=local against a local LLM server.")
            from google import genai
            self._client = genai.Client(api_key=api_key)
        return self._client

    async def is_reachable(self) -> bool:
        return self.is_configured()

    async def list_models(self) -> List[str]:
        client = self._get_client()
        names = []
        for m in client.models.list():
            names.append(m.name)
        return names

    async def generate(self,
                       conversation_history: List[Dict],
                       system_instruction: Optional[str] = None,
                       json_mode: bool = False) -> str:
        from google.genai import types
        client = self._get_client()

        formatted_history = [
            types.Content(role=entry["role"],
                          parts=[types.Part(text=p) for p in entry["parts"]])
            for entry in conversation_history
        ]
        config = None
        if system_instruction:
            config = types.GenerateContentConfig(system_instruction=system_instruction)

        # google-genai's client is sync; run in a thread so the event loop stays free.
        import asyncio
        response = await asyncio.to_thread(
            lambda: client.models.generate_content(
                model=self.model,
                contents=formatted_history,
                config=config,
            )
        )
        return response.text


# ---------------------------------------------------------------------------
# Provider resolution / singleton
# ---------------------------------------------------------------------------

_provider: Optional[LLMProvider] = None


def get_provider_mode() -> str:
    mode = (os.getenv("CHATRPG_PROVIDER") or "auto").strip().lower()
    if mode not in ("local", "gemini", "auto"):
        raise ProviderError(
            f"Invalid CHATRPG_PROVIDER={mode!r}. Use local, gemini, or auto.")
    return mode


async def resolve_provider(mode: Optional[str] = None,
                           local: Optional[LocalOpenAIProvider] = None,
                           gemini: Optional[GeminiProvider] = None) -> LLMProvider:
    """Pick a backend. auto = local if reachable, else Gemini."""
    mode = mode or get_provider_mode()
    if mode == "local":
        return local or LocalOpenAIProvider()
    if mode == "gemini":
        return gemini or GeminiProvider()
    # auto
    local = local or LocalOpenAIProvider()
    gemini = gemini or GeminiProvider()
    if await local.is_reachable():
        return local
    if gemini.is_configured():
        return gemini
    raise ProviderError(
        "No LLM provider available: local server unreachable at "
        f"{local.base_url} and GEMINI_API_KEY is not set.")


async def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = await resolve_provider()
    return _provider


def override_provider(provider_name: Optional[str] = None,
                      model: Optional[str] = None) -> Dict[str, Any]:
    """Runtime override (used by the /api/provider endpoint). Resets the singleton."""
    global _provider
    if provider_name == "local":
        _provider = LocalOpenAIProvider(model=model)
    elif provider_name == "gemini":
        _provider = GeminiProvider(model=model)
    elif provider_name is None and model is not None:
        if _provider is None or provider_name is None:
            # Apply model to whichever backend is active; resolved lazily.
            pass
    if provider_name not in ("local", "gemini", None):
        raise ProviderError(f"Unknown provider {provider_name!r}")
    if provider_name is None:
        _provider = None  # force re-resolve from env
    return {"provider": provider_name or "auto", "model": model}


def reset_provider() -> None:
    global _provider
    _provider = None


async def generate_llm_response(conversation_history: List[Dict],
                                system_instruction: Optional[str] = None,
                                json_mode: bool = False) -> str:
    """High-level entry used by gemini_service functions."""
    provider = await get_provider()
    try:
        return await provider.generate(conversation_history,
                                       system_instruction=system_instruction,
                                       json_mode=json_mode)
    except ProviderError:
        raise
    except Exception as e:
        logger.error(f"LLM generate failed via {provider.name}: "
                     f"{type(e).__name__}: {e}")
        raise
