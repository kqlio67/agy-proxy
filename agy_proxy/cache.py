"""
Prompt Caching and Session Affinity Manager for Antigravity Proxy.
Optimizes token usage, maintains stable multi-turn sessions, and interfaces
with Google AI Studio Native Context Caching.
"""

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import httpx

logger = logging.getLogger("agy_proxy.cache")

# Minimum token estimate to trigger Google Context Caching (32k tokens roughly ~100k chars)
DEFAULT_MIN_CACHE_CHARS = 30_000
DEFAULT_CACHE_TTL_SECONDS = 3600  # 1 hour


class SessionAffinityManager:
    """
    Maintains sticky sessions per client conversation.
    Keeps subsequent turns on the same account for maximum backend KV-cache reuse,
    and seamlessly migrates to another account when quota is exhausted.
    """

    def __init__(self, session_ttl: float = 3600.0):
        self.session_ttl = session_ttl
        # session_key -> {"account_id": str, "backend_session_id": str, "last_active": float, "req_count": int}
        self._sessions: Dict[str, Dict[str, Any]] = {}

    def get_session_key(self, raw_messages: List[Dict[str, Any]], system_prompt: str = "") -> str:
        """Derives a deterministic session key from the conversation start."""
        if not raw_messages:
            return "default_session"

        # Hash the first user message + system prompt to identify the conversation thread
        first_msg_str = ""
        for m in raw_messages:
            if m.get("role") in ("user", "human"):
                content = m.get("content", "")
                if isinstance(content, str):
                    first_msg_str = content[:250]
                elif isinstance(content, list):
                    first_msg_str = json.dumps(content[:2])[:250]
                break

        key_source = f"{system_prompt[:200]}|{first_msg_str}"
        return hashlib.sha256(key_source.encode("utf-8", "ignore")).hexdigest()[:16]

    def get_pinned_account(self, session_key: str) -> Optional[Tuple[str, str]]:
        """Returns (account_id, backend_session_id) if valid session exists."""
        self._cleanup_expired()
        entry = self._sessions.get(session_key)
        if entry:
            entry["last_active"] = time.time()
            entry["req_count"] += 1
            return entry["account_id"], entry["backend_session_id"]
        return None

    def pin_session(self, session_key: str, account_id: str, backend_session_id: str):
        """Pins a conversation session to a specific account and backend session ID."""
        self._sessions[session_key] = {
            "account_id": account_id,
            "backend_session_id": backend_session_id,
            "last_active": time.time(),
            "req_count": 1,
        }

    def unpin_session(self, session_key: str):
        """Removes pinning when an account hits rate-limits or fails."""
        self._sessions.pop(session_key, None)

    def _cleanup_expired(self):
        now = time.time()
        expired = [k for k, v in self._sessions.items() if now - v["last_active"] > self.session_ttl]
        for k in expired:
            del self._sessions[k]


class GoogleContextCacheManager:
    """
    Manages Google AI Studio Context Caching (cachedContents API).
    Creates remote cached content for large system prompts, tools, and prefixes,
    providing up to 75% token discount and instant first-token response times.
    """

    def __init__(self, min_chars: int = DEFAULT_MIN_CACHE_CHARS, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS):
        self.min_chars = min_chars
        self.ttl_seconds = ttl_seconds
        # cache_key -> {"cached_content_name": str, "created_at": float, "expires_at": float, "tokens_saved": int}
        self._cache_map: Dict[str, Dict[str, Any]] = {}
        self._unsupported_keys: set = set()
        self.total_tokens_saved: int = 0
        self.cache_hits: int = 0
        self.cache_misses: int = 0

    def compute_prefix_hash(self, system_instruction: Dict[str, Any], tools: List[Dict[str, Any]], contents: List[Dict[str, Any]]) -> str:
        """Generates a SHA256 hash of the static context (system instruction + tools + prefix)."""
        data = {
            "sys": system_instruction,
            "tools": tools,
            # Cache the prefix if contents > 2 turns
            "prefix": contents[:-1] if len(contents) > 1 else [],
        }
        raw_str = json.dumps(data, sort_keys=True)
        return hashlib.sha256(raw_str.encode("utf-8", "ignore")).hexdigest()

    async def get_or_create_cache(
        self,
        api_key: str,
        model_name: str,
        system_instruction: Optional[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        contents: List[Dict[str, Any]],
    ) -> Optional[str]:
        """
        Looks up or creates a Google AI Studio cachedContents resource.
        Returns the resource name (e.g. 'cachedContents/12345678') if available.
        """
        if api_key in self._unsupported_keys:
            return None

        # Estimate size in characters
        sys_str = json.dumps(system_instruction or {})
        tools_str = json.dumps(tools or [])
        prefix_str = json.dumps(contents[:-1] if len(contents) > 1 else [])
        total_chars = len(sys_str) + len(tools_str) + len(prefix_str)

        if total_chars < self.min_chars:
            return None

        prefix_hash = self.compute_prefix_hash(system_instruction or {}, tools or [], contents)
        cache_key = f"{api_key[:10]}:{model_name}:{prefix_hash}"

        now = time.time()
        # 1. Check existing active cache
        if cache_key in self._cache_map:
            entry = self._cache_map[cache_key]
            if now < entry["expires_at"] - 60:
                self.cache_hits += 1
                estimated_tokens = total_chars // 4
                self.total_tokens_saved += estimated_tokens
                logger.debug("Cache HIT for %s (Saved ~%d tokens)", model_name, estimated_tokens)
                return entry["cached_content_name"]
            else:
                del self._cache_map[cache_key]

        # 2. Create new cached content via Google REST API
        self.cache_misses += 1
        clean_model = model_name.replace("models/", "")
        if "gemini" not in clean_model:
            clean_model = "gemini-1.5-flash"

        create_url = f"https://generativelanguage.googleapis.com/v1beta/cachedContents?key={api_key}"
        payload: Dict[str, Any] = {
            "model": f"models/{clean_model}",
            "ttl": f"{self.ttl_seconds}s",
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if tools:
            payload["tools"] = tools
        if len(contents) > 1:
            payload["contents"] = contents[:-1]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(create_url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    res_name = data.get("name")
                    if res_name:
                        self._cache_map[cache_key] = {
                            "cached_content_name": res_name,
                            "created_at": now,
                            "expires_at": now + self.ttl_seconds,
                        }
                        logger.info("Created Google Context Cache: %s (TTL: %ds)", res_name, self.ttl_seconds)
                        return res_name
                else:
                    if "TotalCachedContentStorageTokensPerModelFreeTier" in resp.text:
                        self._unsupported_keys.add(api_key)
                    logger.debug("Google cachedContents creation returned %d: %s", resp.status_code, resp.text)
        except Exception as e:
            logger.debug("Failed to create Google cachedContent: %s", e)

        return None


# Global Cache Singletons
session_affinity = SessionAffinityManager()
google_context_cache = GoogleContextCacheManager()
