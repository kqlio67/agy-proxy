"""
Data models and schemas for OpenAI, Anthropic, and Gemini API requests and responses.
"""

from __future__ import annotations

from enum import Enum
import time
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


# ==============================================================================
# AGY-PROXY MODEL ARCHITECTURE & CLASSIFICATION
# ==============================================================================
#
# To avoid any ambiguity or confusion, all models supported by agy-proxy
# are categorized into 4 distinct groups:
#
# 1. NATIVE ANTIGRAVITY MODELS (CLOUDCODE API / GOOGLE API):
#    - Authentic Google CloudCode and Antigravity Language Server models:
#      * gemini-3.8-flash-* (High / Medium / Low / Tiered) - official primary flagship
#      * gemini-4-argon-* (High / Medium / Low) - available in Argon-supported subscription tiers
#      * gemini-3.7-flash-*, gemini-3.6-flash-*, gemini-3.5-flash-*, gemini-3.1-pro-*
#      * claude-sonnet-4-6, claude-opus-4-6-thinking (official 3P Anthropic models in Antigravity)
#      * gpt-oss-120b-medium (open-weights Google CloudCode model)
#      * gemini-3.8-flash-deepagent (autonomous agent swarm preset with 64k thinking budget)
#
# 2. GEMINI WEB MODELS (BROWSER AUTOMATION):
#    - All models with `-web-*` suffix (e.g., `gemini-4-argon-web-*`, `gemini-3.8-flash-web-*`).
#    - These requests are NOT sent to CloudCode API; they execute via browser session
#      automation against gemini.google.com.
#    - Task / Agent autonomous execution mode is a Gemini Web feature (web-task / web-agent).
#
# 3. COMPATIBILITY ALIASES:
#    - Provided for external client tools (Cursor, Cline, Aider, Claude Code, OpenAI SDK)
#      that expect industry-standard model identifiers:
#      * OpenAI: `gpt-4o`, `gpt-4`, `o1`, `o3-mini`, `gpt-6-astra` -> mapped to `gemini-3.8-flash-high`
#      * DeepSeek: `deepseek-r1`, `deepseek-v3` -> mapped to `gemini-3.8-flash-high`
#      * Claude: `claude-3-5-sonnet`, `claude-3-7-sonnet` -> mapped to `claude-sonnet-4-6`
#    - NOTE: These are transparent compatibility aliases routing to the strongest available
#      native Antigravity model.
#
# 4. SUBSCRIPTION TIERS & AVAILABILITY:
#    - Gemini 4 Argon CloudCode API (`gemini-4-argon-*`):
#      Supported in Google CloudCode API for accounts with Argon-enabled subscription tiers.
#      Requests to Argon are routed directly to CloudCode if the account tier supports it.
#    - Claude 5.5 (`claude-sonnet-5-5-*`, `claude-opus-5-5-*`):
#      Emulated / mapped to `claude-sonnet-4-6` / `claude-opus-4-6-thinking`.
# ==============================================================================

DEFAULT_MODEL = "gemini-3.8-flash-high"

MODEL_ALIASES: dict[str, str] = {
    # --------------------------------------------------------------------------
    # 1. Gemini 4 Argon & Hydrogen (API models - requires supported subscription tier)
    # --------------------------------------------------------------------------
    "gemini-4-argon": "gemini-4-argon",
    "gemini-4-argon-high": "gemini-4-argon-high",
    "gemini-4-argon-medium": "gemini-4-argon-medium",
    "gemini-4-argon-low": "gemini-4-argon-low",
    "gemini-4-argon-disabled": "gemini-4-argon",

    # Gemini 4 Hydrogen (CloudCode API & Web models - 256k output tokens)
    "gemini-4-hydrogen": "gemini-4-hydrogen",
    "gemini-4-hydrogen-high": "gemini-4-hydrogen-high",
    "gemini-4-hydrogen-medium": "gemini-4-hydrogen-medium",
    "gemini-4-hydrogen-low": "gemini-4-hydrogen-low",
    "gemini-4-hydrogen-xhigh": "gemini-4-hydrogen-xhigh",
    "gemini-4-hydrogen-disabled": "gemini-4-hydrogen",
    "hydrogen": "gemini-4-hydrogen",
    "gemini-4-hydrogen-spark": "gemini-4-hydrogen-web-spark",
    "gemini-4-hydrogen-task": "gemini-4-hydrogen-web-spark",
    "spark-hydrogen": "gemini-4-hydrogen-web-spark",

    # Gemini 4 Carbon (Next-Gen v2.31.0 CloudCode model)
    "gemini-4-carbon": "gemini-4-carbon",
    "gemini-4-carbon-high": "gemini-4-carbon-high",
    "gemini-4-carbon-medium": "gemini-4-carbon-medium",
    "gemini-4-carbon-low": "gemini-4-carbon-low",
    "gemini-4-carbon-disabled": "gemini-4-carbon-disabled",
    "carbon": "gemini-4-carbon",

    # Antigravity Orchestrator & Worker (v2.31.0 CloudCode models)
    "gemini-pro-orchestrator-g1-pro": "gemini-pro-orchestrator-g1-pro",
    "orchestrator": "gemini-pro-orchestrator-g1-pro",
    "orchestrator-argon": "gemini-pro-orchestrator-g1-pro",
    "gemini-orchestrator": "gemini-pro-orchestrator-g1-pro",
    "gemini-flash-worker-agy": "gemini-flash-worker-agy",
    "flash-worker": "gemini-flash-worker-agy",
    "gemini-worker": "gemini-flash-worker-agy",

    # Gemini 3.8 Aliases (Latest default)
    "gemini-3.8-flash": "gemini-3.8-flash-high",
    "gemini-3.8-flash-high": "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low": "gemini-3.8-flash-low",
    "gemini-3.8-flash-tiered": "gemini-3.8-flash-tiered",
    "gemini-3.8-flash-n": "gemini-3.8-flash-high",
    "gemini-3.8-flash-control": "gemini-3.8-flash-high",
    "gemini-3.8": "gemini-3.8-flash-high",
    "gemini-3-8": "gemini-3.8-flash-high",
    "gemini-3.8-flash-preview": "gemini-3.8-flash-high",
    # Antigravity Tab & Jump Completion models
    "chat_20706": "chat_20706",
    "chat_23310": "chat_23310",
    "tab_flash_lite_preview": "tab_flash_lite_preview",
    "tab_jump_flash_lite_preview": "tab_jump_flash_lite_preview",

    # Antigravity Codenames & Next-Gen Experimental Models (v2.29.0)
    "gemini-riftrunner": "gemini-riftrunner",
    "riftrunner": "gemini-riftrunner",
    "gemini-riftrunner-thinking-low": "gemini-riftrunner-thinking-low",
    "gemini-riftrunner-thinking-high": "gemini-riftrunner-thinking-high",
    "gemini-infinityjet": "gemini-infinityjet",
    "infinityjet": "gemini-infinityjet",
    "gemini-infinitybloom": "gemini-infinitybloom",
    "infinitybloom": "gemini-infinitybloom",
    "gemini-orionfire": "gemini-orionfire",
    "orionfire": "gemini-orionfire",
    "gemini-cosmicforge": "gemini-cosmicforge",
    "cosmicforge": "gemini-cosmicforge",
    "gemini-horizondawn": "gemini-horizondawn",
    "horizondawn": "gemini-horizondawn",
    "gemini-pureprism": "gemini-pureprism",
    "pureprism": "gemini-pureprism",
    "gemini-gentleisland": "gemini-gentleisland",
    "gentleisland": "gemini-gentleisland",
    "gemini-rainsong": "gemini-rainsong",
    "rainsong": "gemini-rainsong",
    "jarvis-v4s": "jarvis-v4s",
    "jarvis": "jarvis-v4s",
    "jarvis-proxy": "jarvis-proxy",

    # Multimodal Vision & Image Generation Models
    "gemini-3.1-flash-image": "gemini-3.1-flash-image",
    "gemini-image": "gemini-3.1-flash-image",
    "image": "gemini-3.1-flash-image",
    "image-generation": "gemini-3.1-flash-image",
    "imagen": "gemini-3.1-flash-image",
    "imagen-3": "gemini-3.1-flash-image",
    "dall-e": "gemini-3.1-flash-image",
    "dall-e-3": "gemini-3.1-flash-image",
    "dall-e-2": "gemini-3.1-flash-image",

    # Audio Transcription Models
    "proactive-observer-v10": "models/proactive-observer-v10",
    "proactive-observer": "models/proactive-observer-v10",
    "whisper": "models/proactive-observer-v10",
    "whisper-1": "models/proactive-observer-v10",
    "whisper-large-v3": "models/proactive-observer-v10",


    # Legacy & Specific Gemini Models (Mapped to authentic CloudCode endpoints)
    "gemini-3.7-flash": "gemini-3.8-flash-high",
    "gemini-3.7-flash-high": "gemini-3.8-flash-high",
    "gemini-3.7-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.7-flash-low": "gemini-3.8-flash-low",
    "gemini-3.7-flash-tiered": "gemini-3.8-flash-tiered",
    "gemini-3.6-flash": "gemini-3.8-flash-tiered",
    "gemini-3.6-flash-high": "gemini-3.8-flash-high",
    "gemini-3.6-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.6-flash-low": "gemini-3.8-flash-low",
    "gemini-3.6-flash-tiered": "gemini-3.8-flash-tiered",
    "gemini-3.5-flash": "gemini-3.8-flash-low",
    "gemini-3.5-flash-high": "gemini-3.8-flash-high",
    "gemini-3.5-flash-medium": "gemini-3.8-flash-low",
    "gemini-3.5-flash-low": "gemini-3.8-flash-low",
    "gemini-3.5-flash-extra-low": "gemini-3.8-flash-low",
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3-5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.5-lite": "gemini-3.5-flash-lite",
    "gemini-3.5-flash-lite-high": "gemini-3.5-flash-lite",
    "gemini-3.5-flash-lite-medium": "gemini-3.5-flash-lite",
    "gemini-3.5-flash-lite-low": "gemini-3.5-flash-lite",
    "gemini-3-flash": "gemini-3-flash",
    "gemini-3-flash-agent": "gemini-3-flash-agent",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "gemini-3.1-pro": "gemini-2.5-pro",
    "gemini-3.1-pro-high": "gemini-2.5-pro",
    "gemini-3.1-pro-medium": "gemini-2.5-pro",
    "gemini-3.1-pro-low": "gemini-2.5-pro",
    "gemini-pro-agent": "gemini-2.5-pro",
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite": "gemini-2.0-flash-lite",
    "gemini-flash-lite": "gemini-2.0-flash-lite",
    "flash-lite": "gemini-2.0-flash-lite",
    "gemini-2.5-flash-thinking": "gemini-3.8-flash-high",

    # --------------------------------------------------------------------------
    # 2. GEMINI WEB MODELS (Routed EXCLUSIVELY via gemini.google.com web session)
    #    Clean canonical suffix *-web models
    # --------------------------------------------------------------------------
    "gemini-4-argon-web": "gemini-4-argon-web-high",
    "gemini-4-argon-web-high": "gemini-4-argon-web-high",
    "gemini-4-argon-web-medium": "gemini-4-argon-web-medium",
    "gemini-4-argon-web-low": "gemini-4-argon-web-low",
    "gemini-4-argon-web-spark": "gemini-4-argon-web-spark",

    "gemini-4-hydrogen-web": "gemini-4-hydrogen-web-medium",
    "gemini-4-hydrogen-web-high": "gemini-4-hydrogen-web-high",
    "gemini-4-hydrogen-web-medium": "gemini-4-hydrogen-web-medium",
    "gemini-4-hydrogen-web-low": "gemini-4-hydrogen-web-low",
    "gemini-4-hydrogen-web-xhigh": "gemini-4-hydrogen-web-xhigh",
    "gemini-4-hydrogen-web-spark": "gemini-4-hydrogen-web-spark",
    "gemini-4-hydrogen-web-task": "gemini-4-hydrogen-web-spark",

    "gemini-3.8-flash-web": "gemini-3.8-flash-web-high",
    "gemini-3.8-flash-web-high": "gemini-3.8-flash-web-high",
    "gemini-3.8-flash-web-medium": "gemini-3.8-flash-web-medium",
    "gemini-3.8-flash-web-low": "gemini-3.8-flash-web-low",
    "gemini-3.8-flash-web-spark": "gemini-3.8-flash-web-spark",

    "gemini-3.1-pro-web": "gemini-3.1-pro-web-high",
    "gemini-3.1-pro-web-high": "gemini-3.1-pro-web-high",
    "gemini-3.1-pro-web-medium": "gemini-3.1-pro-web-medium",
    "gemini-3.1-pro-web-low": "gemini-3.1-pro-web-low",

    "gemini-3.5-flash-lite-web": "gemini-3.5-flash-lite-web-high",
    "gemini-3.5-flash-lite-web-high": "gemini-3.5-flash-lite-web-high",
    "gemini-3.5-flash-lite-web-medium": "gemini-3.5-flash-lite-web-medium",
    "gemini-3.5-flash-lite-web-low": "gemini-3.5-flash-lite-web-low",

    # --------------------------------------------------------------------------
    # 3. OFFICIAL 3P CLAUDE MODELS IN ANTIGRAVITY (Supported natively in API)
    # --------------------------------------------------------------------------
    "claude": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-sonnet-4-6-thinking": "claude-sonnet-4-6",
    "claude-sonnet-4.6-thinking": "claude-sonnet-4-6",
    "claude-4-6-sonnet": "claude-sonnet-4-6",
    "claude-4.6-sonnet": "claude-sonnet-4-6",
    "claude-sonnet-4-5": "claude-sonnet-4-6",
    "claude-sonnet-4.5": "claude-sonnet-4-6",
    "claude-4-5-sonnet": "claude-sonnet-4-6",
    "claude-4.5-sonnet": "claude-sonnet-4-6",
    "claude-sonnet-4-0": "claude-sonnet-4-6",
    "claude-sonnet-4.0": "claude-sonnet-4-6",
    "claude-4-0-sonnet": "claude-sonnet-4-6",
    "claude-4-sonnet": "claude-sonnet-4-6",
    "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
    "claude-opus-4-6": "claude-opus-4-6-thinking",
    "claude-opus-4.6": "claude-opus-4-6-thinking",
    "claude-4-6-opus": "claude-opus-4-6-thinking",
    "claude-4.6-opus": "claude-opus-4-6-thinking",
    "claude-opus-4-8": "claude-opus-4-6-thinking",
    "claude-opus-4.8": "claude-opus-4-6-thinking",
    "claude-opus-4-7": "claude-opus-4-6-thinking",
    "claude-opus-4.7": "claude-opus-4-6-thinking",
    "claude-opus-4-5": "claude-opus-4-6-thinking",
    "claude-opus-4.5": "claude-opus-4-6-thinking",
    "claude-opus-4-1": "claude-opus-4-6-thinking",
    "claude-opus-4-0": "claude-opus-4-6-thinking",
    "claude-3-7-sonnet": "claude-sonnet-4-6",
    "claude-3.7-sonnet": "claude-sonnet-4-6",
    "claude-3-7-sonnet-20250219": "claude-sonnet-4-6",
    "claude-3.7-sonnet-20250219": "claude-sonnet-4-6",
    "claude-3-7-sonnet-latest": "claude-sonnet-4-6",
    "claude-3.7-sonnet-latest": "claude-sonnet-4-6",
    "claude-3-7-sonnet-thinking": "claude-sonnet-4-6",
    "claude-3.7-sonnet-thinking": "claude-sonnet-4-6",
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "claude-3.5-sonnet": "claude-sonnet-4-6",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-3.5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-3-5-sonnet-20240620": "claude-sonnet-4-6",
    "claude-3.5-sonnet-20240620": "claude-sonnet-4-6",
    "claude-3-5-sonnet-latest": "claude-sonnet-4-6",
    "claude-3.5-sonnet-latest": "claude-sonnet-4-6",
    "claude-3-opus": "claude-opus-4-6-thinking",
    "claude-3.5-opus": "claude-opus-4-6-thinking",
    "claude-3-opus-20240229": "claude-opus-4-6-thinking",
    "claude-3-opus-latest": "claude-opus-4-6-thinking",
    "claude-3.5-opus-latest": "claude-opus-4-6-thinking",
    # Claude 4.5 & 5.5 Haiku
    "claude-haiku-4-5": "claude-haiku-4-5",
    "claude-haiku-4.5": "claude-haiku-4-5",
    "claude-4-5-haiku": "claude-haiku-4-5",
    "claude-4.5-haiku": "claude-haiku-4-5",
    "claude-haiku-4-5-thinking": "claude-haiku-4-5-thinking",
    "claude-haiku-4.5-thinking": "claude-haiku-4-5-thinking",
    "claude-4-5-haiku-thinking": "claude-haiku-4-5-thinking",
    "claude-4.5-haiku-thinking": "claude-haiku-4-5-thinking",
    "claude-haiku-5-5": "claude-haiku-4-5",
    "claude-haiku-5.5": "claude-haiku-4-5",
    "claude-5-5-haiku": "claude-haiku-4-5",
    "claude-haiku-5-5-thinking": "claude-haiku-4-5-thinking",
    "claude-3-5-haiku": "claude-haiku-4-5",
    "claude-3.5-haiku": "claude-haiku-4-5",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5",
    "claude-3.5-haiku-20241022": "claude-haiku-4-5",
    "claude-3-5-haiku-latest": "claude-haiku-4-5",
    "claude-3.5-haiku-latest": "claude-haiku-4-5",
    "claude-3-haiku": "claude-haiku-4-5",
    "claude-3-haiku-20240307": "claude-haiku-4-5",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-opus": "claude-opus-4-6-thinking",
    "claude-haiku": "claude-haiku-4-5",

    # Claude 5.0 & 5.5 Official Next-Gen Models
    "claude-sonnet-5": "claude-sonnet-5-5-medium",
    "claude-sonnet-5.0": "claude-sonnet-5-5-medium",
    "claude-sonnet-5-high": "claude-sonnet-5-5-high",
    "claude-sonnet-5-medium": "claude-sonnet-5-5-medium",
    "claude-sonnet-5-low": "claude-sonnet-5-5-low",
    "claude-5-sonnet": "claude-sonnet-5-5-medium",
    "claude-opus-5": "claude-opus-5-5-medium",
    "claude-opus-5.0": "claude-opus-5-5-medium",
    "claude-opus-5-high": "claude-opus-5-5-high",
    "claude-opus-5-medium": "claude-opus-5-5-medium",
    "claude-opus-5-low": "claude-opus-5-5-low",
    "claude-5-opus": "claude-opus-5-5-medium",
    "claude-opus-5-5": "claude-opus-5-5-medium",
    "claude-opus-5.5": "claude-opus-5-5-medium",
    "claude-opus-5-5-high": "claude-opus-5-5-high",
    "claude-opus-5.5-high": "claude-opus-5-5-high",
    "claude-opus-5-5-medium": "claude-opus-5-5-medium",
    "claude-opus-5.5-medium": "claude-opus-5-5-medium",
    "claude-opus-5-5-low": "claude-opus-5-5-low",
    "claude-opus-5.5-low": "claude-opus-5-5-low",
    "claude-opus-5-5-max": "claude-opus-5-5-max",
    "claude-opus-5.5-max": "claude-opus-5-5-max",
    "claude-sonnet-5-5": "claude-sonnet-5-5-medium",
    "claude-sonnet-5.5": "claude-sonnet-5-5-medium",
    "claude-sonnet-5-5-high": "claude-sonnet-5-5-high",
    "claude-sonnet-5.5-high": "claude-sonnet-5-5-high",
    "claude-sonnet-5-5-medium": "claude-sonnet-5-5-medium",
    "claude-sonnet-5.5-medium": "claude-sonnet-5-5-medium",
    "claude-sonnet-5-5-low": "claude-sonnet-5-5-low",
    "claude-sonnet-5.5-low": "claude-sonnet-5-5-low",
    "claude-sonnet-5-5-max": "claude-sonnet-5-5-max",
    "claude-sonnet-5.5-max": "claude-sonnet-5-5-max",
    "claude-5-5": "claude-sonnet-5-5-medium",
    "claude-5.5": "claude-sonnet-5-5-medium",
    "claude-5-5-sonnet": "claude-sonnet-5-5-medium",
    "claude-5.5-sonnet": "claude-sonnet-5-5-medium",
    "claude-5-5-opus": "claude-opus-5-5-medium",
    "claude-5.5-opus": "claude-opus-5-5-medium",

    # Claude Fable & Mythos Family (Autonomous Planning & Frontier Intelligence)
    "claude-fable-5": "claude-opus-5-5-high",
    "claude-fable-5.0": "claude-opus-5-5-high",
    "claude-fable-5-1": "claude-opus-5-5-high",
    "claude-fable-5.1": "claude-opus-5-5-high",
    "claude-fable-5-2": "claude-opus-5-5-high",
    "claude-fable-5.2": "claude-opus-5-5-high",
    "fable": "claude-opus-5-5-high",
    "claude-mythos-5": "gemini-4-argon-high",
    "claude-mythos-5.0": "gemini-4-argon-high",
    "claude-mythos-5-1": "gemini-4-argon-high",
    "claude-mythos-5.1": "gemini-4-argon-high",
    "claude-mythos-5-2": "gemini-4-argon-high",
    "claude-mythos-5.2": "gemini-4-argon-high",
    "claude-mythos-preview": "gemini-4-argon-high",
    "mythos": "gemini-4-argon-high",

    # Hybrid Plan & Context Presets
    "opusplan": "claude-opus-5-5-high",

    # --------------------------------------------------------------------------
    # 4. OPENAI CODEX CLI AND STANDARD OPENAI MODEL COMPATIBILITY
    #    (Compatibility aliases mapping standard IDs to Gemini 3.8 Flash)
    # --------------------------------------------------------------------------
    "codex-1": "gemini-3.8-flash-high",
    "codex-mini": "gemini-3.1-flash-lite",
    "codex": "gemini-3.8-flash-high",
    "codex-auto-review": "gemini-3.8-flash-high",
    "gpt-6-astra": "gemini-3.8-flash-high",
    "gpt-6.1-sol": "gemini-3.8-flash-high",
    "gpt-6.1-terra": "gemini-3.1-pro-high",
    "gpt-6-sol": "gemini-3.8-flash-high",
    "gpt-6-luna": "gemini-3.5-flash-lite",
    "gpt-6": "gemini-3.8-flash-high",
    "gpt-5.6-sol": "gemini-3.8-flash-high",
    "gpt-5.6-terra": "gemini-3.1-pro-high",
    "gpt-5.6-luna": "gemini-3.5-flash-lite",
    "gpt-daybreak-blue-latest": "gemini-3.8-flash-high",
    "gpt-daybreak-red-latest": "gemini-3.1-pro-high",
    "gpt-4.5": "gemini-3.8-flash-high",
    "gpt-4.5-preview": "gemini-3.8-flash-high",
    "gpt-4.1": "gemini-3.8-flash-high",
    "gpt-4.1-mini": "gemini-3.1-flash-lite",
    "gpt-4.1-nano": "gemini-3.1-flash-lite",
    "gpt-4o": "gemini-3.8-flash-high",
    "gpt-4o-mini": "gemini-3.1-flash-lite",
    "gpt-4-turbo": "gemini-3.8-flash-high",
    "gpt-4": "gemini-3.8-flash-high",
    "gpt-3.5-turbo": "gemini-3.1-flash-lite",
    "o1": "gemini-3.8-flash-high",
    "o1-mini": "gemini-3.8-flash-high",
    "o1-preview": "gemini-3.8-flash-high",
    "o3": "gemini-3.8-flash-high",
    "o3-high": "gemini-3.8-flash-high",
    "o3-medium": "gemini-3.8-flash-medium",
    "o3-low": "gemini-3.8-flash-low",
    "o3-mini": "gemini-3.8-flash-high",
    "o3-mini-high": "gemini-3.8-flash-high",
    "o3-mini-medium": "gemini-3.8-flash-medium",
    "o3-mini-low": "gemini-3.8-flash-low",
    "o4": "gemini-3.8-flash-high",
    "o4-mini": "gemini-3.1-flash-lite",
    "gpt-oss-120b": "gpt-oss-120b-medium",
    "gpt-oss-120b-medium": "gpt-oss-120b-medium",

    # --------------------------------------------------------------------------
    # 6. DEEPSEEK COMPATIBILITY (Aliases mapped to Gemini 3.8 Flash High)
    # --------------------------------------------------------------------------
    "deepseek-r1": "gemini-3.8-flash-high",
    "deepseek-v3": "gemini-3.8-flash-high",

    # --------------------------------------------------------------------------
    # 7. DEEPAGENT SWARM PRESET (Autonomous agent preset with 64k thinking)
    # --------------------------------------------------------------------------
    "gemini-deepagent": "gemini-3.8-flash-deepagent",
    "gemini-3.8-flash-deepagent": "gemini-3.8-flash-deepagent",
    "claude-deepagent": "claude-sonnet-4-6-deepagent",
    "claude-sonnet-deepagent": "claude-sonnet-4-6-deepagent",
    "claude-sonnet-4-6-deepagent": "claude-sonnet-4-6-deepagent",
    "claude-opus-deepagent": "claude-opus-4-6-deepagent",
    "claude-opus-4-6-deepagent": "claude-opus-4-6-deepagent",
}


VALID_CLOUDCODE_MODELS = {
    # Gemini 4 Argon & Hydrogen (CloudCode API - requires supported subscription tier)
    "gemini-4-argon",
    "gemini-4-argon-high",
    "gemini-4-argon-medium",
    "gemini-4-argon-low",
    "gemini-4-hydrogen",
    "gemini-4-hydrogen-high",
    "gemini-4-hydrogen-medium",
    "gemini-4-hydrogen-low",
    "gemini-4-hydrogen-xhigh",
    "gemini-4-carbon",
    "gemini-4-carbon-high",
    "gemini-4-carbon-medium",
    "gemini-4-carbon-low",
    "gemini-4-carbon-disabled",
    # Antigravity Orchestrator & Execution Worker (v2.31.0)
    "gemini-pro-orchestrator-g1-pro",
    "gemini-flash-worker-agy",
    # Gemini 3.8
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "gemini-3.8-flash-tiered",
    "gemini-3.8-flash-deepagent",
    # Gemini 3.5 & earlier
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-image",
    "gemini-3-flash",
    "gemini-3-flash-agent",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    # 3rd Party models
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6-deepagent",
    "claude-opus-4-6-deepagent",
    "claude-sonnet-5-5-max",
    "claude-sonnet-5-5-high",
    "claude-sonnet-5-5-medium",
    "claude-sonnet-5-5-low",
    "claude-opus-5-5-max",
    "claude-opus-5-5-high",
    "claude-opus-5-5-medium",
    "claude-opus-5-5-low",
    "claude-haiku-4-5",
    "claude-haiku-4-5-thinking",
    # Antigravity Codenames & Next-Gen Experimental Models (v2.29.0)
    "gemini-riftrunner",
    "gemini-riftrunner-thinking-low",
    "gemini-riftrunner-thinking-high",
    "gemini-infinityjet",
    "gemini-infinitybloom",
    "gemini-orionfire",
    "gemini-cosmicforge",
    "gemini-horizondawn",
    "gemini-pureprism",
    "gemini-gentleisland",
    "gemini-rainsong",
    "jarvis-v4s",
    "jarvis-proxy",
    "gpt-oss-120b-medium",
    # Antigravity Tab & Jump Completion models
    "chat_20706",
    "chat_23310",
    "tab_flash_lite_preview",
    "tab_jump_flash_lite_preview",
    # Audio transcription & proactive observer
    "models/proactive-observer-v10",
}

VALID_WEB_MODELS = {
    # Gemini 4 Argon Web models
    "gemini-4-argon-web-high",
    "gemini-4-argon-web-medium",
    "gemini-4-argon-web-low",
    "gemini-4-argon-web-spark",
    # Gemini 4 Hydrogen Web models
    "gemini-4-hydrogen-web-high",
    "gemini-4-hydrogen-web-medium",
    "gemini-4-hydrogen-web-low",
    "gemini-4-hydrogen-web-xhigh",
    "gemini-4-hydrogen-web-spark",
    # Gemini 3.8 Flash Web models
    "gemini-3.8-flash-web-high",
    "gemini-3.8-flash-web-medium",
    "gemini-3.8-flash-web-low",
    "gemini-3.8-flash-web-spark",
    # Gemini 3.1 Pro Web models
    "gemini-3.1-pro-web-high",
    "gemini-3.1-pro-web-medium",
    "gemini-3.1-pro-web-low",
    # Gemini 3.5 Flash Lite Web models
    "gemini-3.5-flash-lite-web-high",
    "gemini-3.5-flash-lite-web-medium",
    "gemini-3.5-flash-lite-web-low",
}

CLOUDCODE_PLACEHOLDER_MAP: dict[str, str] = {
    "claude-opus-4-6-thinking": "MODEL_PLACEHOLDER_M26",
    "claude-sonnet-4-6": "MODEL_PLACEHOLDER_M35",
    "claude-haiku-4-5": "MODEL_CLAUDE_4_5_HAIKU",
    "claude-haiku-4-5-thinking": "MODEL_CLAUDE_4_5_HAIKU_THINKING",
    "claude-4-5-haiku": "MODEL_CLAUDE_4_5_HAIKU",
    "claude-4-5-haiku-thinking": "MODEL_CLAUDE_4_5_HAIKU_THINKING",
    "gemini-riftrunner": "MODEL_GOOGLE_GEMINI_RIFTRUNNER",
    "gemini-riftrunner-thinking-low": "MODEL_GOOGLE_GEMINI_RIFTRUNNER_THINKING_LOW",
    "gemini-riftrunner-thinking-high": "MODEL_GOOGLE_GEMINI_RIFTRUNNER_THINKING_HIGH",
    "gemini-infinityjet": "MODEL_GOOGLE_GEMINI_INFINITYJET",
    "gemini-infinitybloom": "MODEL_GOOGLE_GEMINI_INFINITYBLOOM",
    "gemini-orionfire": "MODEL_GOOGLE_GEMINI_ORIONFIRE",
    "gemini-cosmicforge": "MODEL_GOOGLE_GEMINI_COSMICFORGE",
    "gemini-horizondawn": "MODEL_GOOGLE_GEMINI_HORIZONDAWN",
    "gemini-pureprism": "MODEL_GOOGLE_GEMINI_PUREPRISM",
    "gemini-gentleisland": "MODEL_GOOGLE_GEMINI_GENTLEISLAND",
    "gemini-rainsong": "MODEL_GOOGLE_GEMINI_RAINSONG",
    "jarvis-v4s": "MODEL_GOOGLE_JARVIS_V4S",
    "jarvis-proxy": "MODEL_GOOGLE_JARVIS_PROXY",
    "gemini-3.7-flash-high": "MODEL_PLACEHOLDER_M315",
    "gemini-3.7-flash-medium": "MODEL_PLACEHOLDER_M316",
    "gemini-3.7-flash-low": "MODEL_PLACEHOLDER_M317",
    "gemini-3.7-flash": "MODEL_PLACEHOLDER_M315",
    "gemini-3.8-flash-high": "MODEL_PLACEHOLDER_M318",
    "gemini-3.8-flash-medium": "MODEL_PLACEHOLDER_M319",
    "gemini-3.8-flash-low": "MODEL_PLACEHOLDER_M320",
    "gemini-4-argon": "MODEL_PLACEHOLDER_M417",
    "gemini-4-argon-high": "MODEL_PLACEHOLDER_M417",
    "gemini-4-argon-medium": "MODEL_PLACEHOLDER_M418",
    "gemini-4-argon-low": "MODEL_PLACEHOLDER_M419",
    "gemini-4-argon-disabled": "MODEL_PLACEHOLDER_M421",
    "gemini-4-hydrogen-disabled": "MODEL_PLACEHOLDER_M526",
    "gemini-4-hydrogen": "MODEL_PLACEHOLDER_M526",
    "gemini-4-hydrogen-high": "MODEL_PLACEHOLDER_M525",
    "gemini-4-hydrogen-low": "MODEL_PLACEHOLDER_M527",
    "gemini-4-hydrogen-xhigh": "MODEL_PLACEHOLDER_M528",
    "gemini-4-hydrogen-medium": "MODEL_PLACEHOLDER_M526",
    "gemini-4-carbon": "MODEL_PLACEHOLDER_M670",
    "gemini-4-carbon-high": "MODEL_PLACEHOLDER_M670",
    "gemini-4-carbon-medium": "MODEL_PLACEHOLDER_M670",
    "gemini-4-carbon-low": "MODEL_PLACEHOLDER_M670",
    "gemini-4-carbon-disabled": "MODEL_PLACEHOLDER_M670",
    "gemini-pro-orchestrator-g1-pro": "MODEL_PLACEHOLDER_M668",
    "gemini-flash-worker-agy": "MODEL_PLACEHOLDER_M669",
    "gpt-oss-120b-medium": "MODEL_PLACEHOLDER_M410",
    "MODEL_OPENAI_GPT_OSS_120B_MEDIUM": "MODEL_OPENAI_GPT_OSS_120B_MEDIUM",
    # Official Authentic CloudCode Enums from Antigravity Hub v2.28.2 / cl=1009437749
    "claude-opus-5-5-low": "MODEL_PLACEHOLDER_M400",
    "claude-opus-5-5-medium": "MODEL_PLACEHOLDER_M401",
    "claude-opus-5-5-high": "MODEL_PLACEHOLDER_M402",
    "claude-opus-5-5-max": "MODEL_PLACEHOLDER_M406",
    "claude-sonnet-5-5-low": "MODEL_PLACEHOLDER_M403",
    "claude-sonnet-5-5-medium": "MODEL_PLACEHOLDER_M404",
    "claude-sonnet-5-5-high": "MODEL_PLACEHOLDER_M405",
    "claude-sonnet-5-5-max": "MODEL_PLACEHOLDER_M407",
    "gemini-3.1-flash-image": "MODEL_PLACEHOLDER_M21",
    "gemini-3.1-flash-lite": "MODEL_PLACEHOLDER_M50",
    "gemini-3.5-flash-lite": "MODEL_PLACEHOLDER_M198",
    "gemini-3.5-flash-low": "MODEL_PLACEHOLDER_M20",
    "gemini-3.5-flash-extra-low": "MODEL_PLACEHOLDER_M187",
    "gemini-3-flash": "MODEL_PLACEHOLDER_M18",
    "gemini-3-flash-agent": "MODEL_PLACEHOLDER_M84",
    "gemini-3.8-flash-tiered": "MODEL_PLACEHOLDER_M322",
    "tab_flash_lite_preview": "MODEL_PLACEHOLDER_M19",
    "tab_jump_flash_lite_preview": "MODEL_PLACEHOLDER_M28",
    "chat_20706": "MODEL_CHAT_20706",
    "chat_23310": "MODEL_CHAT_23310",
    "gemini-2.5-pro": "MODEL_GOOGLE_GEMINI_2_5_PRO",
    "gemini-2.5-flash": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    "gemini-2.5-flash-lite": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE",
    "gemini-2.5-flash-thinking": "MODEL_GOOGLE_GEMINI_2_5_FLASH_THINKING",
    "models/proactive-observer-v10": "models/proactive-observer-v10",
    # Injected web & cloud models
    "gemini-4-argon-web-high": "MODEL_PLACEHOLDER_M501",
    "gemini-4-argon-web-medium": "MODEL_PLACEHOLDER_M502",
    "gemini-4-argon-web-low": "MODEL_PLACEHOLDER_M503",
    "gemini-4-argon-web-spark": "MODEL_PLACEHOLDER_M504",
    "gemini-4-argon-web-task": "MODEL_PLACEHOLDER_M504",
    "gemini-4-hydrogen-web-high": "MODEL_PLACEHOLDER_M551",
    "gemini-4-hydrogen-web-medium": "MODEL_PLACEHOLDER_M552",
    "gemini-4-hydrogen-web-low": "MODEL_PLACEHOLDER_M553",
    "gemini-4-hydrogen-web-xhigh": "MODEL_PLACEHOLDER_M554",
    "gemini-4-hydrogen-web-spark": "MODEL_PLACEHOLDER_M555",
    "gemini-4-hydrogen-web-task": "MODEL_PLACEHOLDER_M555",
    "gemini-3.8-flash-web-high": "MODEL_PLACEHOLDER_M505",
    "gemini-3.8-flash-web-medium": "MODEL_PLACEHOLDER_M506",
    "gemini-3.8-flash-web-low": "MODEL_PLACEHOLDER_M507",
    "gemini-3.8-flash-web-spark": "MODEL_PLACEHOLDER_M508",
    "gemini-3.8-flash-web-task": "MODEL_PLACEHOLDER_M508",
    "gemini-3.1-pro-web-high": "MODEL_PLACEHOLDER_M509",
    "gemini-3.1-pro-web-medium": "MODEL_PLACEHOLDER_M510",
    "gemini-3.1-pro-web-low": "MODEL_PLACEHOLDER_M511",
    "gemini-3.5-flash-lite-web-high": "MODEL_PLACEHOLDER_M512",
    "gemini-3.5-flash-lite-web-medium": "MODEL_PLACEHOLDER_M513",
    "gemini-3.5-flash-lite-web-low": "MODEL_PLACEHOLDER_M514",
    "gemini-3.8-flash-deepagent": "MODEL_PLACEHOLDER_M522",
    "claude-sonnet-4-6-deepagent": "MODEL_PLACEHOLDER_M556",
    "claude-opus-4-6-deepagent": "MODEL_PLACEHOLDER_M557",
    "gemini-3.7-flash-tiered": "MODEL_PLACEHOLDER_M523",
    "gemini-3.6-flash-high": "MODEL_PLACEHOLDER_M524",
    "gemini-3.6-flash-medium": "MODEL_PLACEHOLDER_M541",
    "gemini-3.6-flash-low": "MODEL_PLACEHOLDER_M542",
    "gemini-3.1-pro-high": "MODEL_PLACEHOLDER_M531",
    "gemini-3.1-pro-medium": "MODEL_PLACEHOLDER_M532",
    "gemini-3.1-pro-low": "MODEL_PLACEHOLDER_M533",
    "gemini-3.6-flash-tiered": "MODEL_PLACEHOLDER_M543",
    "gemini-3.5-flash-lite-high": "MODEL_PLACEHOLDER_M544",
    "gemini-3.5-flash-lite-medium": "MODEL_PLACEHOLDER_M545",
    "gemini-3.5-flash-lite-low": "MODEL_PLACEHOLDER_M546",
    "gemini-pro-agent": "MODEL_PLACEHOLDER_M547",
    # Gemini 4 Hydrogen Web models
    "gemini-4-hydrogen-web-high": "MODEL_PLACEHOLDER_M551",
    "gemini-4-hydrogen-web-medium": "MODEL_PLACEHOLDER_M552",
    "gemini-4-hydrogen-web-low": "MODEL_PLACEHOLDER_M553",
    "gemini-4-hydrogen-web-xhigh": "MODEL_PLACEHOLDER_M554",
    "gemini-4-hydrogen-web-spark": "MODEL_PLACEHOLDER_M555",
    "gemini-4-hydrogen-web-task": "MODEL_PLACEHOLDER_M555",
    # External Web & API models (Language Server protobuf placeholder range M1000..M1650 -> enums 2000..2650)
    "chatgpt-web/gpt-4o": "MODEL_PLACEHOLDER_M1000",
    "chatgpt-web/gpt-4o-mini": "MODEL_PLACEHOLDER_M1001",
    "chatgpt-web/o3-mini": "MODEL_PLACEHOLDER_M1002",
    "chatgpt-web/chatgpt-4o-latest": "MODEL_PLACEHOLDER_M1003",
    "deepseek-web/deepseek-chat": "MODEL_PLACEHOLDER_M1004",
    "deepseek-web/deepseek-reasoner": "MODEL_PLACEHOLDER_M1005",
    "gemini-web-guest/gemini-2.0-flash-lite": "MODEL_PLACEHOLDER_M1006",
    "gemini-web-guest/gemini-flash-lite": "MODEL_PLACEHOLDER_M1007",
    "chatgpt-web-guest/gpt-4o": "MODEL_PLACEHOLDER_M1008",
    "chatgpt-web-guest/gpt-4o-mini": "MODEL_PLACEHOLDER_M1009",
    "chatgpt-web-guest/o3-mini": "MODEL_PLACEHOLDER_M1010",
    "chatgpt-web-guest/chatgpt-4o-latest": "MODEL_PLACEHOLDER_M1011",
    "deepseek-chat": "MODEL_PLACEHOLDER_M1004",
    "deepseek-reasoner": "MODEL_PLACEHOLDER_M1005",
    "DeepSeek V3 (Web Free)": "MODEL_PLACEHOLDER_M1004",
    "DeepSeek R1 (Web Free Thinking)": "MODEL_PLACEHOLDER_M1005",
}

DYNAMIC_MODEL_START_ENUM: int = 2000
DYNAMIC_MODEL_MAX_ENUM: int = 2650


def get_or_assign_model_placeholder(model_id: str, display_name: str | None = None) -> tuple[str, int]:
    """
    Returns (placeholder_name, proto_enum_value) for any model ID.
    If the model already has an assigned placeholder in CLOUDCODE_PLACEHOLDER_MAP,
    returns it. Otherwise, assigns a free dynamic placeholder from the 2000..2650 range
    (MODEL_PLACEHOLDER_M1000..M1650) and updates CLOUDCODE_PLACEHOLDER_MAP and REVERSE_PLACEHOLDER_MAP.
    """
    # Check model_id
    if model_id in CLOUDCODE_PLACEHOLDER_MAP:
        ph = CLOUDCODE_PLACEHOLDER_MAP[model_id]
        if ph.startswith("MODEL_PLACEHOLDER_M"):
            try:
                num = int(ph.split("MODEL_PLACEHOLDER_M")[1])
                return ph, 1000 + num
            except Exception:
                pass
    # Check display_name
    if display_name and display_name in CLOUDCODE_PLACEHOLDER_MAP:
        ph = CLOUDCODE_PLACEHOLDER_MAP[display_name]
        if ph.startswith("MODEL_PLACEHOLDER_M"):
            try:
                num = int(ph.split("MODEL_PLACEHOLDER_M")[1])
                return ph, 1000 + num
            except Exception:
                pass

    # Find next available enum in 2000..2650
    used_enums: set[int] = set()
    for m, p in CLOUDCODE_PLACEHOLDER_MAP.items():
        if p.startswith("MODEL_PLACEHOLDER_M"):
            try:
                used_enums.add(1000 + int(p.split("MODEL_PLACEHOLDER_M")[1]))
            except Exception:
                pass
    enum_val = DYNAMIC_MODEL_START_ENUM
    while enum_val in used_enums and enum_val < DYNAMIC_MODEL_MAX_ENUM:
        enum_val += 1

    ph = f"MODEL_PLACEHOLDER_M{enum_val - 1000}"
    CLOUDCODE_PLACEHOLDER_MAP[model_id] = ph
    REVERSE_PLACEHOLDER_MAP[ph.lower()] = model_id
    REVERSE_PLACEHOLDER_MAP[str(enum_val)] = model_id
    if display_name:
        CLOUDCODE_PLACEHOLDER_MAP[display_name] = ph
        REVERSE_PLACEHOLDER_MAP[display_name.lower()] = model_id
    return ph, enum_val


def sync_all_external_model_placeholders() -> dict[str, int]:
    """
    Ensures all currently active external and web models have registered placeholders
    and returns a mapping of model_id/display_name -> proto_enum_value for Web UI sync.
    """
    mapping: dict[str, int] = {}
    try:
        from agy_proxy.providers import external_provider_manager
        ext_models = external_provider_manager.get_all_active_models()
        for em in ext_models:
            m_id = em["id"]
            d_name = em.get("display_name")
            ph, enum_val = get_or_assign_model_placeholder(m_id, d_name)
            mapping[m_id] = enum_val
            if d_name:
                mapping[d_name] = enum_val
    except Exception:
        pass
    return mapping

REVERSE_PLACEHOLDER_MAP: dict[str, str] = {}
for k, v in CLOUDCODE_PLACEHOLDER_MAP.items():
    v_low = v.lower()
    if v_low not in REVERSE_PLACEHOLDER_MAP or "/" in k:
        REVERSE_PLACEHOLDER_MAP[v_low] = k
for _m, _p in CLOUDCODE_PLACEHOLDER_MAP.items():
    if _p.startswith("MODEL_PLACEHOLDER_M"):
        try:
            _num = int(_p.split("MODEL_PLACEHOLDER_M")[1])
            _enum_val = 1000 + _num
            _enum_str = str(_enum_val)
            if _enum_str not in REVERSE_PLACEHOLDER_MAP or "/" in _m:
                REVERSE_PLACEHOLDER_MAP[_enum_str] = _m
        except Exception:
            pass

# Direct Protobuf enum numbers and Model enum strings used by Language Server
REVERSE_PLACEHOLDER_MAP.update({
    # Proto enum values as strings
    "246": "gemini-2.5-pro",
    "312": "gemini-2.5-flash",
    "313": "gemini-2.5-flash-thinking",
    "329": "gemini-2.5-flash-thinking",
    "330": "gemini-2.5-flash-lite",
    "342": "gpt-oss-120b-medium",
    "281": "claude-sonnet-4-6",
    "282": "claude-sonnet-4-6",
    "290": "claude-opus-4-6-thinking",
    "291": "claude-opus-4-6-thinking",
    "333": "claude-sonnet-4-6",
    "334": "claude-sonnet-4-6",
    "340": "claude-haiku-4-5",
    "341": "claude-haiku-4-5-thinking",
    # Antigravity Next-Gen & Codenames Enums (cortex.proto v2.29.0)
    "336": "gemini-horizondawn",
    "337": "gemini-pureprism",
    "338": "gemini-gentleisland",
    "339": "gemini-rainsong",
    "343": "gemini-orionfire",
    "346": "jarvis-proxy",
    "347": "gemini-cosmicforge",
    "348": "gemini-riftrunner",
    "349": "jarvis-v4s",
    "350": "gemini-infinityjet",
    "351": "gemini-infinitybloom",
    "352": "gemini-riftrunner-thinking-low",
    "353": "gemini-riftrunner-thinking-high",
    "1018": "gemini-3-flash",
    "1019": "tab_flash_lite_preview",
    "1020": "gemini-3.5-flash-low",
    "1021": "gemini-3.1-flash-image",
    "1026": "claude-opus-4-6-thinking",
    "1028": "tab_jump_flash_lite_preview",
    "1035": "claude-sonnet-4-6",
    "1050": "gemini-3.1-flash-lite",
    "1084": "gemini-3-flash-agent",
    "1187": "gemini-3.5-flash-extra-low",
    "1198": "gemini-3.5-flash-lite",
    "1315": "gemini-3.7-flash-high",
    "1316": "gemini-3.7-flash-medium",
    "1317": "gemini-3.7-flash-low",
    "1318": "gemini-3.8-flash-high",
    "1319": "gemini-3.8-flash-medium",
    "1320": "gemini-3.8-flash-low",
    "1322": "gemini-3.8-flash-tiered",
    "1400": "claude-opus-5-5-low",
    "1401": "claude-opus-5-5-medium",
    "1402": "claude-opus-5-5-high",
    "1403": "claude-sonnet-5-5-low",
    "1404": "claude-sonnet-5-5-medium",
    "1405": "claude-sonnet-5-5-high",
    "1406": "claude-opus-5-5-max",
    "1407": "claude-sonnet-5-5-max",
    "1408": "gemini-4-argon",
    "1410": "gpt-oss-120b-medium",
    "1417": "gemini-4-argon-high",
    "1418": "gemini-4-argon-medium",
    "1419": "gemini-4-argon-low",
    "1421": "gemini-4-argon",
    "1422": "gemini-4-argon-medium",
    "1423": "gemini-4-argon-low",
    "1432": "gemini-4-hydrogen",
    "model_placeholder_m400": "claude-opus-5-5-low",
    "model_placeholder_m401": "claude-opus-5-5-medium",
    "model_placeholder_m402": "claude-opus-5-5-high",
    "model_placeholder_m403": "claude-sonnet-5-5-low",
    "model_placeholder_m404": "claude-sonnet-5-5-medium",
    "model_placeholder_m405": "claude-sonnet-5-5-high",
    "model_placeholder_m406": "claude-opus-5-5-max",
    "model_placeholder_m407": "claude-sonnet-5-5-max",
    "model_placeholder_m408": "gemini-4-argon",
    "model_placeholder_m410": "gpt-oss-120b-medium",
    "model_placeholder_m417": "gemini-4-argon-high",
    "model_placeholder_m418": "gemini-4-argon-medium",
    "model_placeholder_m419": "gemini-4-argon-low",
    "model_placeholder_m421": "gemini-4-argon",
    "model_placeholder_m422": "gemini-4-argon-medium",
    "model_placeholder_m423": "gemini-4-argon-low",
    "model_placeholder_m432": "gemini-4-hydrogen",
    "model_placeholder_m667": "gemini-pro-orchestrator-g1-pro",
    "1667": "gemini-pro-orchestrator-g1-pro",
    "model_placeholder_m668": "gemini-pro-orchestrator-g1-pro",
    "1668": "gemini-pro-orchestrator-g1-pro",
    "model_placeholder_m669": "gemini-flash-worker-agy",
    "1669": "gemini-flash-worker-agy",
    "model_placeholder_m670": "gemini-4-carbon",
    "1670": "gemini-4-carbon",
    # Legacy synthetic mappings for backward compatibility
    "model_placeholder_m515": "claude-sonnet-5-5-high",
    "model_placeholder_m516": "claude-sonnet-5-5-medium",
    "model_placeholder_m517": "claude-sonnet-5-5-low",
    "model_placeholder_m518": "claude-opus-5-5-high",
    "model_placeholder_m519": "claude-opus-5-5-medium",
    "model_placeholder_m520": "claude-opus-5-5-low",
    "1515": "claude-sonnet-5-5-high",
    "1516": "claude-sonnet-5-5-medium",
    "1517": "claude-sonnet-5-5-low",
    "1518": "claude-opus-5-5-high",
    "1519": "claude-opus-5-5-medium",
    "1520": "claude-opus-5-5-low",
    "model_placeholder_m521": "gemini-3.8-flash-tiered",
    "model_placeholder_m525": "gemini-4-hydrogen-high",
    "model_placeholder_m526": "gemini-4-hydrogen-medium",
    "model_placeholder_m527": "gemini-4-hydrogen-low",
    "model_placeholder_m528": "gemini-4-hydrogen-xhigh",
    "model_placeholder_m529": "gemini-3.5-flash-extra-low",
    "model_placeholder_m530": "gemini-3.5-flash-lite",
    "model_placeholder_m524": "gemini-3.6-flash-high",
    "model_placeholder_m531": "gemini-3.1-pro-high",
    "model_placeholder_m532": "gemini-3.1-pro-medium",
    "model_placeholder_m533": "gemini-3.1-pro-low",
    "model_placeholder_m534": "gemini-3.1-flash-lite",
    "model_placeholder_m535": "gemini-3-flash",
    "model_placeholder_m536": "gemini-2.5-pro",
    "model_placeholder_m537": "gemini-2.5-flash",
    "model_placeholder_m538": "gemini-2.5-flash-lite",
    "model_placeholder_m539": "gemini-2.5-flash-thinking",
    "model_placeholder_m541": "gemini-3.6-flash-medium",
    "model_placeholder_m542": "gemini-3.6-flash-low",
    "model_placeholder_m543": "gemini-3.6-flash-tiered",
    "model_placeholder_m544": "gemini-3.5-flash-lite-high",
    "model_placeholder_m545": "gemini-3.5-flash-lite-medium",
    "model_placeholder_m546": "gemini-3.5-flash-lite-low",
    "model_placeholder_m547": "gemini-pro-agent",
    "model_placeholder_m548": "gemini-3.1-flash-image",
    "model_placeholder_m549": "gemini-3.1-flash-image",
    "model_placeholder_m556": "claude-sonnet-4-6-deepagent",
    "model_placeholder_m557": "claude-opus-4-6-deepagent",
    "model_placeholder_m551": "gemini-4-hydrogen-web-high",
    "model_placeholder_m552": "gemini-4-hydrogen-web-medium",
    "model_placeholder_m553": "gemini-4-hydrogen-web-low",
    "model_placeholder_m554": "gemini-4-hydrogen-web-xhigh",
    "model_placeholder_m555": "gemini-4-hydrogen-web-spark",
    "1521": "gemini-3.8-flash-tiered",
    "1522": "gemini-3.8-flash-deepagent",
    "1523": "gemini-3.7-flash-tiered",
    "1524": "gemini-3.6-flash-high",
    "1525": "gemini-4-hydrogen-high",
    "1526": "gemini-4-hydrogen-medium",
    "1527": "gemini-4-hydrogen-low",
    "1528": "gemini-4-hydrogen-xhigh",
    "1529": "gemini-3.5-flash-extra-low",
    "1530": "gemini-3.5-flash-lite",
    "1531": "gemini-3.1-pro-high",
    "1532": "gemini-3.1-pro-medium",
    "1533": "gemini-3.1-pro-low",
    "1534": "gemini-3.1-flash-lite",
    "1535": "gemini-3-flash",
    "1536": "gemini-2.5-pro",
    "1537": "gemini-2.5-flash",
    "1538": "gemini-2.5-flash-lite",
    "1539": "gemini-2.5-flash-thinking",
    "1541": "gemini-3.6-flash-medium",
    "1542": "gemini-3.6-flash-low",
    "1543": "gemini-3.6-flash-tiered",
    "1544": "gemini-3.5-flash-lite-high",
    "1545": "gemini-3.5-flash-lite-medium",
    "1546": "gemini-3.5-flash-lite-low",
    "1547": "gemini-pro-agent",
    "1548": "gemini-3.1-flash-image",
    "1556": "claude-sonnet-4-6-deepagent",
    "1557": "claude-opus-4-6-deepagent",
    "1551": "gemini-4-hydrogen-web-high",
    "1552": "gemini-4-hydrogen-web-medium",
    "1553": "gemini-4-hydrogen-web-low",
    "1554": "gemini-4-hydrogen-web-xhigh",
    "1555": "gemini-4-hydrogen-web-spark",
    # Protobuf enum string representations from language_server
    "model_google_gemini_2_5_pro": "gemini-2.5-pro",
    "model_google_gemini_2_5_flash": "gemini-2.5-flash",
    "model_google_gemini_2_5_flash_lite": "gemini-2.5-flash-lite",
    "model_google_gemini_2_5_flash_thinking": "gemini-2.5-flash-thinking",
    "model_openai_gpt_oss_120b_medium": "gpt-oss-120b-medium",
    "model_chat_20706": "chat_20706",
    "model_chat_23310": "chat_23310",
    "chat_20706": "chat_20706",
    "chat_23310": "chat_23310",
    "tab_flash_lite_preview": "tab_flash_lite_preview",
    "tab_jump_flash_lite_preview": "tab_jump_flash_lite_preview",

    # Antigravity Codenames & Next-Gen Experimental Models (v2.29.0)
    "gemini-riftrunner": "gemini-riftrunner",
    "riftrunner": "gemini-riftrunner",
    "gemini-riftrunner-thinking-low": "gemini-riftrunner-thinking-low",
    "gemini-riftrunner-thinking-high": "gemini-riftrunner-thinking-high",
    "gemini-infinityjet": "gemini-infinityjet",
    "infinityjet": "gemini-infinityjet",
    "gemini-infinitybloom": "gemini-infinitybloom",
    "infinitybloom": "gemini-infinitybloom",
    "gemini-orionfire": "gemini-orionfire",
    "orionfire": "gemini-orionfire",
    "gemini-cosmicforge": "gemini-cosmicforge",
    "cosmicforge": "gemini-cosmicforge",
    "gemini-horizondawn": "gemini-horizondawn",
    "horizondawn": "gemini-horizondawn",
    "gemini-pureprism": "gemini-pureprism",
    "pureprism": "gemini-pureprism",
    "gemini-gentleisland": "gemini-gentleisland",
    "gentleisland": "gemini-gentleisland",
    "gemini-rainsong": "gemini-rainsong",
    "rainsong": "gemini-rainsong",
    "jarvis-v4s": "jarvis-v4s",
    "jarvis": "jarvis-v4s",
    "jarvis-proxy": "jarvis-proxy",
    "model_claude_4_sonnet": "claude-sonnet-4-6",
    "model_claude_4_sonnet_thinking": "claude-sonnet-4-6",
    "model_claude_4_opus": "claude-opus-4-6-thinking",
    "model_claude_4_opus_thinking": "claude-opus-4-6-thinking",
    "model_claude_4_5_sonnet": "claude-sonnet-4-6",
    "model_claude_4_5_sonnet_thinking": "claude-sonnet-4-6",
    "model_claude_4_5_haiku": "claude-haiku-4-5",
    "model_claude_4_5_haiku_thinking": "claude-haiku-4-5-thinking",
})

EXACT_MODEL_METADATA: dict[str, dict[str, Any]] = {
    # Gemini 4 Hydrogen (CloudCode API - 256k output tokens, 1M context)
    "gemini-4-hydrogen": {"displayName": "Gemini 4 Hydrogen", "maxTokens": 1048576, "maxOutputTokens": 262144, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 2},
    "gemini-4-hydrogen-high": {"displayName": "Gemini 4 Hydrogen (High)", "maxTokens": 1048576, "maxOutputTokens": 262144, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    "gemini-4-hydrogen-medium": {"displayName": "Gemini 4 Hydrogen (Medium)", "maxTokens": 1048576, "maxOutputTokens": 262144, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 2},
    "gemini-4-hydrogen-low": {"displayName": "Gemini 4 Hydrogen (Low)", "maxTokens": 1048576, "maxOutputTokens": 262144, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 1},
    "gemini-4-hydrogen-xhigh": {"displayName": "Gemini 4 Hydrogen (XHigh)", "maxTokens": 1048576, "maxOutputTokens": 262144, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 4},
    "gemini-4-hydrogen-disabled": {"displayName": "Gemini 4 Hydrogen", "maxTokens": 1048576, "maxOutputTokens": 262144, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 2},
    # Gemini 4 Argon (CloudCode API - requires Argon-supported subscription tier)
    "gemini-4-argon": {"displayName": "Gemini 4 Argon", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    "gemini-4-argon-high": {"displayName": "Gemini 4 Argon (High)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    "gemini-4-argon-medium": {"displayName": "Gemini 4 Argon (Medium)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 2},
    "gemini-4-argon-low": {"displayName": "Gemini 4 Argon (Low)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 1},
    # Gemini 4 Carbon (Next-Gen v2.31.0 CloudCode model)
    "gemini-4-carbon": {"displayName": "Gemini 4 Carbon", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    "gemini-4-carbon-high": {"displayName": "Gemini 4 Carbon (High)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    "gemini-4-carbon-medium": {"displayName": "Gemini 4 Carbon (Medium)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 2},
    "gemini-4-carbon-low": {"displayName": "Gemini 4 Carbon (Low)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 1},
    "gemini-4-carbon-disabled": {"displayName": "Gemini 4 Carbon", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    # Antigravity Orchestrator & Execution Worker (v2.31.0 Plan-and-Execute)
    "gemini-pro-orchestrator-g1-pro": {"displayName": "Orchestrator (Argon)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    "gemini-flash-worker-agy": {"displayName": "Gemini Flash (Execution Worker)", "maxTokens": 1048576, "maxOutputTokens": 262144, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 100},
    # Gemini 3.8 Flagship
    "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash (High)", "maxTokens": 1048576},
    "gemini-3.8-flash-medium": {"displayName": "Gemini 3.8 Flash (Medium)", "maxTokens": 1048576},
    "gemini-3.8-flash-low": {"displayName": "Gemini 3.8 Flash (Low)", "maxTokens": 1048576},
    "gemini-3.8-flash-tiered": {"displayName": "Gemini 3.8 Flash (Tiered)", "maxTokens": 1048576},
    "gemini-3.8-flash-n": {"displayName": "Gemini 3.8 Flash (High)", "maxTokens": 1048576},
    "gemini-3.8-flash-control": {"displayName": "Gemini 3.8 Flash (Control Serving Version)", "maxTokens": 1048576},
    "gemini-3.8-flash-deepagent": {"displayName": "Gemini 3.8 Flash DeepAgent (Autonomous Swarm, 64k Reasoning)", "maxTokens": 1048576},
    # Gemini 3.7
    "gemini-3.7-flash": {"displayName": "Gemini 3.7 Flash", "maxTokens": 1048576},
    "gemini-3.7-flash-high": {"displayName": "Gemini 3.7 Flash (High)", "maxTokens": 1048576},
    "gemini-3.7-flash-medium": {"displayName": "Gemini 3.7 Flash (Medium)", "maxTokens": 1048576},
    "gemini-3.7-flash-low": {"displayName": "Gemini 3.7 Flash (Low)", "maxTokens": 1048576},
    "gemini-3.7-flash-tiered": {"displayName": "Gemini 3.7 Flash (Tiered)", "maxTokens": 1048576},
    # Gemini 3.6
    "gemini-3.6-flash-high": {"displayName": "Gemini 3.6 Flash (High)", "maxTokens": 1048576},
    "gemini-3.6-flash-medium": {"displayName": "Gemini 3.6 Flash (Medium)", "maxTokens": 1048576},
    "gemini-3.6-flash-low": {"displayName": "Gemini 3.6 Flash (Low)", "maxTokens": 1048576},
    # Gemini 3.5 & earlier
    "gemini-3.5-flash-low": {"displayName": "Gemini 3.5 Flash (Medium)", "maxTokens": 1048576},
    "gemini-3.5-flash-extra-low": {"displayName": "Gemini 3.5 Flash (Low)", "maxTokens": 1048576},
    "gemini-3.5-flash-lite": {"displayName": "Gemini 3.5 Flash Lite", "maxTokens": 1048576},
    "gemini-3.1-flash-lite": {"displayName": "Gemini 3.1 Flash Lite", "maxTokens": 1048576},
    "gemini-3.1-flash-image": {"displayName": "Gemini 3.1 Flash Image", "maxTokens": 65536, "maxOutputTokens": 65536, "supportsImages": True},
    "gemini-3.1-pro": {"displayName": "Gemini 3.1 Pro", "maxTokens": 1048576},
    "gemini-3.1-pro-high": {"displayName": "Gemini 3.1 Pro (High)", "maxTokens": 1048576},
    "gemini-3.1-pro-medium": {"displayName": "Gemini 3.1 Pro (Medium)", "maxTokens": 1048576},
    "gemini-3.1-pro-low": {"displayName": "Gemini 3.1 Pro (Low)", "maxTokens": 1048576},
    "gemini-4-argon-disabled": {"displayName": "Gemini 4 Argon", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 2},
    "models/proactive-observer-v10": {"displayName": "Proactive Observer v10", "maxTokens": 65536},
    "gemini-3-flash": {"displayName": "Gemini 3 Flash", "maxTokens": 1048576, "minThinkingBudget": 128},
    "gemini-3-flash-agent": {"displayName": "Gemini 3 Flash Agent", "maxTokens": 1048576},
    "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro", "maxTokens": 1048576},
    "gemini-2.5-flash": {"displayName": "Gemini 2.5 Flash", "maxTokens": 1048576},
    "gemini-2.5-flash-lite": {"displayName": "Gemini 2.5 Flash Lite", "maxTokens": 1048576},
    "gemini-2.5-flash-thinking": {"displayName": "Gemini 2.5 Flash (Thinking)", "maxTokens": 1048576},
    # 3rd Party models
    "claude-sonnet-4-6": {"displayName": "Claude Sonnet 4.6 (Thinking)", "maxTokens": 200000},
    "claude-opus-4-6-thinking": {"displayName": "Claude Opus 4.6 (Thinking)", "maxTokens": 200000},
    "claude-sonnet-4-6-deepagent": {"displayName": "Claude Sonnet 4.6 (DeepAgent)", "maxTokens": 200000},
    "claude-opus-4-6-deepagent": {"displayName": "Claude Opus 4.6 (DeepAgent)", "maxTokens": 200000},
    "claude-sonnet-5-5-max": {"displayName": "Claude Sonnet 5.5 (Max)", "maxTokens": 200000, "supportsThinking": True, "thinkingLevel": 4},
    "claude-sonnet-5-5-high": {"displayName": "Claude Sonnet 5.5 (High)", "maxTokens": 200000, "supportsThinking": True, "thinkingLevel": 3},
    "claude-sonnet-5-5-medium": {"displayName": "Claude Sonnet 5.5 (Medium)", "maxTokens": 200000, "supportsThinking": True, "thinkingLevel": 2},
    "claude-sonnet-5-5-low": {"displayName": "Claude Sonnet 5.5 (Low)", "maxTokens": 200000, "supportsThinking": True, "thinkingLevel": 1},
    "claude-opus-5-5-max": {"displayName": "Claude Opus 5.5 (Max)", "maxTokens": 200000, "supportsThinking": True, "thinkingLevel": 4},
    "claude-opus-5-5-high": {"displayName": "Claude Opus 5.5 (High)", "maxTokens": 200000, "supportsThinking": True, "thinkingLevel": 3},
    "claude-opus-5-5-medium": {"displayName": "Claude Opus 5.5 (Medium)", "maxTokens": 200000, "supportsThinking": True, "thinkingLevel": 2},
    "claude-opus-5-5-low": {"displayName": "Claude Opus 5.5 (Low)", "maxTokens": 200000, "supportsThinking": True, "thinkingLevel": 1},
    "claude-haiku-4-5": {"displayName": "Claude 4.5 Haiku", "maxTokens": 200000},
    "claude-haiku-4-5-thinking": {"displayName": "Claude 4.5 Haiku (Thinking)", "maxTokens": 200000, "supportsThinking": True},
    "claude-4-5-haiku": {"displayName": "Claude 4.5 Haiku", "maxTokens": 200000},
    "claude-4-5-haiku-thinking": {"displayName": "Claude 4.5 Haiku (Thinking)", "maxTokens": 200000, "supportsThinking": True},
    "gemini-riftrunner": {"displayName": "Gemini Riftrunner", "maxTokens": 1048576, "supportsThinking": True},
    "gemini-riftrunner-thinking-low": {"displayName": "Gemini Riftrunner (Thinking Low)", "maxTokens": 1048576, "supportsThinking": True, "thinkingBudget": 2048},
    "gemini-riftrunner-thinking-high": {"displayName": "Gemini Riftrunner (Thinking High)", "maxTokens": 1048576, "supportsThinking": True, "thinkingBudget": 16384},
    "gemini-infinityjet": {"displayName": "Gemini InfinityJet", "maxTokens": 1048576, "supportsThinking": True},
    "gemini-infinitybloom": {"displayName": "Gemini InfinityBloom", "maxTokens": 1048576, "supportsThinking": True},
    "gemini-orionfire": {"displayName": "Gemini OrionFire", "maxTokens": 1048576},
    "gemini-cosmicforge": {"displayName": "Gemini CosmicForge", "maxTokens": 1048576},
    "gemini-horizondawn": {"displayName": "Gemini HorizonDawn", "maxTokens": 1048576},
    "gemini-pureprism": {"displayName": "Gemini PurePrism", "maxTokens": 1048576},
    "gemini-gentleisland": {"displayName": "Gemini GentleIsland", "maxTokens": 1048576},
    "gemini-rainsong": {"displayName": "Gemini RainSong", "maxTokens": 1048576},
    "jarvis-v4s": {"displayName": "Jarvis v4s", "maxTokens": 1048576},
    "jarvis-proxy": {"displayName": "Jarvis Proxy", "maxTokens": 1048576},
    "gpt-oss-120b-medium": {"displayName": "GPT-OSS 120B (Medium)", "maxTokens": 131072, "maxOutputTokens": 32768, "supportsThinking": True, "thinkingBudget": 8192},
    "gpt-oss-120b": {"displayName": "GPT-OSS 120B", "maxTokens": 131072, "maxOutputTokens": 32768, "supportsThinking": True, "thinkingBudget": 8192},
    # Tab & Jump Completion models
    "chat_20706": {"displayName": "Tab Code Completion 16k", "maxTokens": 16384},
    "chat_23310": {"displayName": "Tab Code Completion 32k", "maxTokens": 32768},
    "tab_flash_lite_preview": {"displayName": "Tab Flash Lite Preview", "maxTokens": 16384, "maxOutputTokens": 4096},
    "tab_jump_flash_lite_preview": {"displayName": "Tab Jump Flash Lite Preview", "maxTokens": 16384, "maxOutputTokens": 4096},
    # OpenAI & Codex models available in agy-proxy
    "gpt-6-astra": {"displayName": "GPT-6 Astra (Flagship)", "maxTokens": 272000},
    "gpt-6.1-sol": {"displayName": "GPT-6.1 Sol", "maxTokens": 272000},
    "gpt-6.1-terra": {"displayName": "GPT-6.1 Terra", "maxTokens": 272000},
    "gpt-6-sol": {"displayName": "GPT-6 Sol", "maxTokens": 272000},
    "gpt-6-luna": {"displayName": "GPT-6 Luna", "maxTokens": 272000},
    "gpt-daybreak-blue-latest": {"displayName": "GPT Daybreak Blue (Latest)", "maxTokens": 272000},
    "gpt-daybreak-red-latest": {"displayName": "GPT Daybreak Red (Latest)", "maxTokens": 272000},
    "codex-auto-review": {"displayName": "Codex Auto Review", "maxTokens": 272000},
    "gpt-6": {"displayName": "GPT-6", "maxTokens": 272000},
    "gpt-5.6-sol": {"displayName": "GPT-5.6 Sol", "maxTokens": 272000},
    "gpt-5.6-terra": {"displayName": "GPT-5.6 Terra", "maxTokens": 272000},
    "gpt-5.6-luna": {"displayName": "GPT-5.6 Luna", "maxTokens": 272000},
    "gpt-5.5": {"displayName": "GPT-5.5", "maxTokens": 272000},
    "gpt-5.4": {"displayName": "GPT-5.4", "maxTokens": 272000},
    "gpt-5.2": {"displayName": "GPT-5.2", "maxTokens": 272000},
    "gpt-5": {"displayName": "GPT-5", "maxTokens": 272000},
    "gpt-5-mini": {"displayName": "GPT-5-Mini", "maxTokens": 128000},
    "gpt-5-turbo": {"displayName": "GPT-5 Turbo", "maxTokens": 272000},
    "gpt-5-codex": {"displayName": "GPT-5 Codex", "maxTokens": 272000},
    "codex-1": {"displayName": "Codex-1", "maxTokens": 272000},
    "gpt-4.5": {"displayName": "GPT-4.5 (Preview)", "maxTokens": 128000},
    "gpt-4.1": {"displayName": "GPT-4.1", "maxTokens": 128000},
    "gpt-4o": {"displayName": "GPT-4o (Omni)", "maxTokens": 128000},
    "gpt-4o-mini": {"displayName": "GPT-4o Mini", "maxTokens": 128000},
    "gpt-4-turbo": {"displayName": "GPT-4 Turbo", "maxTokens": 128000},
    "o1": {"displayName": "OpenAI o1", "maxTokens": 200000},
    "o1-mini": {"displayName": "OpenAI o1 Mini", "maxTokens": 128000},
    "o3": {"displayName": "OpenAI o3", "maxTokens": 200000},
    "o3-mini": {"displayName": "OpenAI o3 Mini", "maxTokens": 200000},
    "o4": {"displayName": "OpenAI o4", "maxTokens": 200000},
    "o4-mini": {"displayName": "OpenAI o4-mini", "maxTokens": 128000},
    "deepseek-r1": {"displayName": "DeepSeek R1", "maxTokens": 128000},
    "deepseek-v3": {"displayName": "DeepSeek V3", "maxTokens": 128000},
    "claude-sonnet-4.6": {"displayName": "Claude Sonnet 4.6 (Thinking)", "maxTokens": 200000},
    "claude-opus-4.6": {"displayName": "Claude Opus 4.6 (Thinking)", "maxTokens": 200000},
    "claude-3-7-sonnet": {"displayName": "Claude 3.7 Sonnet", "maxTokens": 200000},
    "claude-3-7-sonnet-latest": {"displayName": "Claude 3.7 Sonnet (Latest)", "maxTokens": 200000},
    "claude-3-5-sonnet": {"displayName": "Claude 3.5 Sonnet", "maxTokens": 200000},
    "claude-3-5-sonnet-latest": {"displayName": "Claude 3.5 Sonnet (Latest)", "maxTokens": 200000},
    "claude-3-opus": {"displayName": "Claude 3 Opus", "maxTokens": 200000},
    "claude-3-opus-latest": {"displayName": "Claude 3 Opus (Latest)", "maxTokens": 200000},
    "claude-3-5-haiku": {"displayName": "Claude 3.5 Haiku", "maxTokens": 200000},
    "claude-3-5-haiku-latest": {"displayName": "Claude 3.5 Haiku (Latest)", "maxTokens": 200000},
    # Gemini Web browser models (Clean canonical *-web)
    "gemini-4-hydrogen-web-high": {"displayName": "Gemini 4 Hydrogen (Web) (High Reasoning)", "maxTokens": 1048576, "maxOutputTokens": 262144},
    "gemini-4-hydrogen-web-medium": {"displayName": "Gemini 4 Hydrogen (Web) (Medium Reasoning)", "maxTokens": 1048576, "maxOutputTokens": 262144},
    "gemini-4-hydrogen-web-low": {"displayName": "Gemini 4 Hydrogen (Web) (Low / Fast)", "maxTokens": 1048576, "maxOutputTokens": 262144},
    "gemini-4-hydrogen-web-xhigh": {"displayName": "Gemini 4 Hydrogen (Web) (Extra-High Reasoning)", "maxTokens": 1048576, "maxOutputTokens": 262144},
    "gemini-4-hydrogen-web-spark": {"displayName": "Gemini 4 Hydrogen (Web) (Autonomous Spark Agent)", "maxTokens": 1048576, "maxOutputTokens": 262144},
    "gemini-4-argon-web-high": {"displayName": "Gemini 4 Argon (Web) (High Reasoning)", "maxTokens": 1048576},
    "gemini-4-argon-web-medium": {"displayName": "Gemini 4 Argon (Web) (Medium Reasoning)", "maxTokens": 1048576},
    "gemini-4-argon-web-low": {"displayName": "Gemini 4 Argon (Web) (Low / Fast)", "maxTokens": 1048576},
    "gemini-4-argon-web-spark": {"displayName": "Gemini 4 Argon (Web) (Autonomous Spark Agent)", "maxTokens": 1048576},
    "gemini-3.8-flash-web-high": {"displayName": "Gemini 3.8 Flash (Web) (High Reasoning)", "maxTokens": 1048576},
    "gemini-3.8-flash-web-medium": {"displayName": "Gemini 3.8 Flash (Web) (Medium Reasoning)", "maxTokens": 1048576},
    "gemini-3.8-flash-web-low": {"displayName": "Gemini 3.8 Flash (Web) (Low / Fast)", "maxTokens": 1048576},
    "gemini-3.8-flash-web-spark": {"displayName": "Gemini 3.8 Flash (Web) (Autonomous Spark Agent)", "maxTokens": 1048576},
    "gemini-3.1-pro-web-high": {"displayName": "Gemini 3.1 Pro (Web) (High Reasoning)", "maxTokens": 1048576},
    "gemini-3.1-pro-web-medium": {"displayName": "Gemini 3.1 Pro (Web) (Medium Reasoning)", "maxTokens": 1048576},
    "gemini-3.1-pro-web-low": {"displayName": "Gemini 3.1 Pro (Web) (Low / Fast)", "maxTokens": 1048576},
    "gemini-3.5-flash-lite-web-high": {"displayName": "Gemini 3.5 Flash-Lite (Web) (High Reasoning)", "maxTokens": 1048576},
    "gemini-3.5-flash-lite-web-medium": {"displayName": "Gemini 3.5 Flash-Lite (Web) (Medium Reasoning)", "maxTokens": 1048576},
    "gemini-3.5-flash-lite-web-low": {"displayName": "Gemini 3.5 Flash-Lite (Web) (Low / Ultra Fast)", "maxTokens": 1048576},
}

PROXY_CATALOG_MODELS: dict[str, dict[str, Any]] = {
    # OpenAI Codex Models
    "gpt-6-astra": {"displayName": "GPT-6-Astra", "maxTokens": 272000, "description": "OpenAI flagship Codex agent model with deep reasoning (Default)", "category": "OpenAI Codex"},
    "gpt-6.1-sol": {"displayName": "GPT-6.1 Sol", "maxTokens": 272000, "description": "High performance Codex reasoning model", "category": "OpenAI Codex"},
    "gpt-6.1-terra": {"displayName": "GPT-6.1 Terra", "maxTokens": 272000, "description": "Maximum capability Codex flagship reasoning model", "category": "OpenAI Codex"},
    "gpt-6": {"displayName": "GPT-6", "maxTokens": 272000, "description": "OpenAI GPT-6 advanced reasoning model", "category": "OpenAI Codex"},
    "gpt-5.6-sol": {"displayName": "GPT-5.6-Sol", "maxTokens": 272000, "description": "High-performance coding model", "category": "OpenAI Codex"},
    "gpt-5.6-terra": {"displayName": "GPT-5.6-Terra", "maxTokens": 272000, "description": "Balanced speed and depth model", "category": "OpenAI Codex"},
    "gpt-5.6-luna": {"displayName": "GPT-5.6-Luna", "maxTokens": 272000, "description": "Fast lightweight model", "category": "OpenAI Codex"},
    "gpt-5.5": {"displayName": "GPT-5.5", "maxTokens": 272000, "description": "Advanced coding agent model", "category": "OpenAI Codex"},
    "gpt-5.4": {"displayName": "GPT-5.4", "maxTokens": 272000, "description": "High reasoning coding model", "category": "OpenAI Codex"},
    "gpt-5.2": {"displayName": "GPT-5.2", "maxTokens": 272000, "description": "Stable previous generation model", "category": "OpenAI Codex"},
    "gpt-5": {"displayName": "GPT-5", "maxTokens": 272000, "description": "Next-generation OpenAI frontier model with deep reasoning", "category": "OpenAI Codex"},
    "gpt-5-mini": {"displayName": "GPT-5-Mini", "maxTokens": 128000, "description": "Fast lightweight GPT-5 tier model", "category": "OpenAI Codex"},
    "codex-1": {"displayName": "Codex-1", "maxTokens": 272000, "description": "Specialized OpenAI Codex autonomous coding agent model", "category": "OpenAI Codex"},

    # Standard OpenAI Models
    "gpt-4.5": {"displayName": "GPT-4.5 (Preview)", "maxTokens": 128000, "description": "OpenAI massive knowledge & deep reasoning model", "category": "OpenAI"},
    "gpt-4.1": {"displayName": "GPT-4.1", "maxTokens": 128000, "description": "High-efficiency general purpose coding model", "category": "OpenAI"},
    "gpt-4o": {"displayName": "GPT-4o (Omni)", "maxTokens": 128000, "description": "OpenAI GPT-4o multimodal flagship model", "category": "OpenAI"},
    "gpt-4o-mini": {"displayName": "GPT-4o Mini", "maxTokens": 128000, "description": "Fast, cost-efficient small model", "category": "OpenAI"},
    "gpt-4-turbo": {"displayName": "GPT-4 Turbo", "maxTokens": 128000, "description": "GPT-4 Turbo with high context window", "category": "OpenAI"},
    "o1": {"displayName": "OpenAI o1", "maxTokens": 200000, "description": "Deep reasoning model for math and coding", "category": "OpenAI"},
    "o1-mini": {"displayName": "OpenAI o1 Mini", "maxTokens": 128000, "description": "Fast reasoning model for coding", "category": "OpenAI"},
    "o3": {"displayName": "OpenAI o3", "maxTokens": 200000, "description": "OpenAI flagship deep reasoning model for complex coding and math", "category": "OpenAI"},
    "o3-mini": {"displayName": "OpenAI o3 Mini", "maxTokens": 200000, "description": "Latest reasoning model with high performance", "category": "OpenAI"},
    "o4-mini": {"displayName": "OpenAI o4-mini", "maxTokens": 128000, "description": "Fast, highly efficient next-generation reasoning model", "category": "OpenAI"},
    "dall-e-3": {"displayName": "DALL-E 3 (Gemini 3.1 Flash Image)", "maxTokens": 65536, "description": "High-fidelity image generation alias mapped to Gemini 3.1 Flash Image", "category": "OpenAI"},
    "whisper-1": {"displayName": "Whisper 1 (Audio Transcription)", "maxTokens": 65536, "description": "Speech audio transcription model alias mapped to Proactive Observer v10", "category": "OpenAI"},

    # Anthropic Claude Models
    "claude-mythos-preview": {"displayName": "Claude Mythos Preview", "maxTokens": 200000, "description": "Anthropic Claude Mythos next-generation preview model", "category": "Anthropic"},
    "claude-mythos-5": {"displayName": "Claude Mythos 5 (Frontier Intelligence)", "maxTokens": 200000, "description": "Anthropic frontier flagship intelligence model (Tier 3 Reasoning)", "category": "Anthropic"},
    "claude-mythos-5-2": {"displayName": "Claude Mythos 5.2", "maxTokens": 1000000, "description": "Anthropic highest intelligence frontier model with 1M context native", "category": "Anthropic"},
    "claude-fable-5-2": {"displayName": "Claude Fable 5.2 (Deep Reasoning)", "maxTokens": 1000000, "description": "Anthropic specialized planning and autonomous execution model (Tier 3 Reasoning)", "category": "Anthropic"},
    "claude-fable-5": {"displayName": "Claude Fable 5", "maxTokens": 200000, "description": "Anthropic next-generation autonomous planning model", "category": "Anthropic"},
    "opusplan": {"displayName": "OpusPlan (Opus 5.5 Plan + Sonnet 4.6 Exec)", "maxTokens": 200000, "description": "Anthropic hybrid preset: Opus 5.5 for architectural planning, Sonnet 4.6 for code execution", "category": "Anthropic"},
    "claude-sonnet-5-5": {"displayName": "Claude Sonnet 5.5", "maxTokens": 1000000, "description": "Anthropic Claude Sonnet 5.5 flagship hybrid model with 1M context native", "category": "Anthropic"},
    "claude-sonnet-5": {"displayName": "Claude Sonnet 5", "maxTokens": 200000, "description": "Anthropic Claude Sonnet 5 flagship hybrid model ($2/M in, $10/M out)", "category": "Anthropic"},
    "claude-opus-5-5": {"displayName": "Claude Opus 5.5 (Thinking)", "maxTokens": 1000000, "description": "Anthropic Claude Opus 5.5 frontier reasoning model with 1M context native", "category": "Anthropic"},
    "claude-opus-5": {"displayName": "Claude Opus 5 (Thinking)", "maxTokens": 200000, "description": "Anthropic Claude Opus 5 high-capability reasoning model ($5/M in, $25/M out)", "category": "Anthropic"},
    "claude-haiku-5-5": {"displayName": "Claude Haiku 5.5", "maxTokens": 1000000, "description": "Anthropic ultra-low cost high-efficiency model with native 1M context ($0.10/M in, $0.50/M out)", "category": "Anthropic"},
    "claude-haiku-5-5-thinking": {"displayName": "Claude Haiku 5.5 (Thinking)", "maxTokens": 1000000, "description": "Anthropic Claude Haiku 5.5 with adaptive reasoning effort", "category": "Anthropic"},
    "claude-sonnet-4-6": {"displayName": "Claude Sonnet 4.6 (Thinking)", "maxTokens": 200000, "description": "Anthropic flagship hybrid model with extended thinking (Antigravity 3P)", "category": "Anthropic"},
    "claude-opus-4-6-thinking": {"displayName": "Claude Opus 4.6 (Thinking)", "maxTokens": 200000, "description": "Anthropic high-capability reasoning model (Antigravity 3P)", "category": "Anthropic"},
    "claude-sonnet-4-6-deepagent": {"displayName": "Claude Sonnet 4.6 (DeepAgent)", "maxTokens": 200000, "description": "Anthropic Claude Sonnet 4.6 autonomous agent preset with 64k thinking budget", "category": "Anthropic"},
    "claude-opus-4-6-deepagent": {"displayName": "Claude Opus 4.6 (DeepAgent)", "maxTokens": 200000, "description": "Anthropic Claude Opus 4.6 autonomous agent preset with 64k thinking budget", "category": "Anthropic"},
    "claude-3-7-sonnet": {"displayName": "Claude 3.7 Sonnet", "maxTokens": 200000, "description": "Claude 3.7 Sonnet standard alias", "category": "Anthropic"},
    "claude-3-7-sonnet-latest": {"displayName": "Claude 3.7 Sonnet (Latest)", "maxTokens": 200000, "description": "Anthropic Claude 3.7 Sonnet latest release with hybrid reasoning", "category": "Anthropic"},
    "claude-3-5-sonnet": {"displayName": "Claude 3.5 Sonnet", "maxTokens": 200000, "description": "Claude 3.5 Sonnet standard alias", "category": "Anthropic"},
    "claude-3-5-sonnet-latest": {"displayName": "Claude 3.5 Sonnet (Latest)", "maxTokens": 200000, "description": "Anthropic Claude 3.5 Sonnet latest release standard alias", "category": "Anthropic"},
    "claude-3-opus": {"displayName": "Claude 3 Opus", "maxTokens": 200000, "description": "Claude 3 Opus standard alias", "category": "Anthropic"},
    "claude-3-opus-latest": {"displayName": "Claude 3 Opus (Latest)", "maxTokens": 200000, "description": "Anthropic Claude 3 Opus latest release standard alias", "category": "Anthropic"},
    "claude-haiku-4-5": {"displayName": "Claude 4.5 Haiku", "maxTokens": 200000, "description": "Anthropic Claude 4.5 Haiku ultra-fast lightweight model (Antigravity 3P)", "category": "Anthropic"},
    "claude-haiku-4-5-thinking": {"displayName": "Claude 4.5 Haiku (Thinking)", "maxTokens": 200000, "description": "Anthropic Claude 4.5 Haiku with hybrid reasoning (Antigravity 3P)", "category": "Anthropic"},
    "claude-4-5-haiku": {"displayName": "Claude 4.5 Haiku", "maxTokens": 200000, "description": "Anthropic Claude 4.5 Haiku model alias", "category": "Anthropic"},
    "claude-4-5-haiku-thinking": {"displayName": "Claude 4.5 Haiku (Thinking)", "maxTokens": 200000, "description": "Anthropic Claude 4.5 Haiku thinking model alias", "category": "Anthropic"},
    "claude-3-5-haiku": {"displayName": "Claude 3.5 Haiku", "maxTokens": 200000, "description": "Claude 3.5 Haiku alias mapping to native Claude 4.5 Haiku", "category": "Anthropic"},
    "claude-3-5-haiku-latest": {"displayName": "Claude 3.5 Haiku (Latest)", "maxTokens": 200000, "description": "Anthropic Claude 3.5 Haiku alias mapping to native Claude 4.5 Haiku", "category": "Anthropic"},
    "claude-sonnet-5-5-high": {"displayName": "Claude Sonnet 5.5 (High)", "maxTokens": 200000, "description": "Anthropic Claude Sonnet 5.5 with high reasoning (Antigravity 3P)", "category": "Anthropic"},
    "claude-sonnet-5-5-medium": {"displayName": "Claude Sonnet 5.5 (Medium)", "maxTokens": 200000, "description": "Anthropic Claude Sonnet 5.5 with balanced reasoning (Antigravity 3P)", "category": "Anthropic"},
    "claude-sonnet-5-5-low": {"displayName": "Claude Sonnet 5.5 (Low)", "maxTokens": 200000, "description": "Anthropic Claude Sonnet 5.5 with fast reasoning (Antigravity 3P)", "category": "Anthropic"},
    "claude-sonnet-5-5-max": {"displayName": "Claude Sonnet 5.5 (Max)", "maxTokens": 200000, "description": "Anthropic Claude Sonnet 5.5 with maximum reasoning (Antigravity 3P)", "category": "Anthropic"},
    "claude-opus-5-5-high": {"displayName": "Claude Opus 5.5 (High)", "maxTokens": 200000, "description": "Anthropic Claude Opus 5.5 with deep reasoning (Antigravity 3P)", "category": "Anthropic"},
    "claude-opus-5-5-medium": {"displayName": "Claude Opus 5.5 (Medium)", "maxTokens": 200000, "description": "Anthropic Claude Opus 5.5 with balanced reasoning (Antigravity 3P)", "category": "Anthropic"},
    "claude-opus-5-5-low": {"displayName": "Claude Opus 5.5 (Low)", "maxTokens": 200000, "description": "Anthropic Claude Opus 5.5 with fast reasoning (Antigravity 3P)", "category": "Anthropic"},
    "claude-opus-5-5-max": {"displayName": "Claude Opus 5.5 (Max)", "maxTokens": 200000, "description": "Anthropic Claude Opus 5.5 with maximum reasoning (Antigravity 3P)", "category": "Anthropic"},

    # Antigravity Codenames & Next-Gen Experimental Models (v2.29.0)
    "gemini-riftrunner": {"displayName": "Gemini Riftrunner", "maxTokens": 1048576, "description": "Next-generation Gemini Riftrunner agentic model", "category": "Google Gemini"},
    "gemini-infinityjet": {"displayName": "Gemini InfinityJet", "maxTokens": 1048576, "description": "High-throughput Gemini InfinityJet reasoning model", "category": "Google Gemini"},
    "gemini-infinitybloom": {"displayName": "Gemini InfinityBloom", "maxTokens": 1048576, "description": "Creative and generative Gemini InfinityBloom model", "category": "Google Gemini"},
    "jarvis-v4s": {"displayName": "Jarvis v4s", "maxTokens": 1048576, "description": "Autonomous developer agent model Jarvis v4s", "category": "Google Gemini"},

    # Google Gemini Models (CloudCode API)
    "gemini-4-hydrogen": {"displayName": "Gemini 4 Hydrogen", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Google Next-Gen Gemini 4 Hydrogen flagship with 256k generation tokens", "category": "Google Gemini"},
    "gemini-4-hydrogen-high": {"displayName": "Gemini 4 Hydrogen (High)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen deep reasoning with 256k generation tokens", "category": "Google Gemini"},
    "gemini-4-hydrogen-medium": {"displayName": "Gemini 4 Hydrogen (Medium)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen balanced reasoning with 256k generation tokens", "category": "Google Gemini"},
    "gemini-4-hydrogen-low": {"displayName": "Gemini 4 Hydrogen (Low)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen fast reasoning with 256k generation tokens", "category": "Google Gemini"},
    "gemini-4-hydrogen-xhigh": {"displayName": "Gemini 4 Hydrogen (XHigh)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen extra-high reasoning with 256k generation tokens", "category": "Google Gemini"},
    "gemini-4-carbon": {"displayName": "Gemini 4 Carbon", "maxTokens": 1048576, "description": "Google Next-Gen Gemini 4 Carbon CloudCode model", "category": "Google Gemini"},
    "gemini-4-carbon-high": {"displayName": "Gemini 4 Carbon (High)", "maxTokens": 1048576, "description": "Google Next-Gen Gemini 4 Carbon deep reasoning CloudCode model", "category": "Google Gemini"},
    "gemini-4-carbon-medium": {"displayName": "Gemini 4 Carbon (Medium)", "maxTokens": 1048576, "description": "Google Next-Gen Gemini 4 Carbon balanced reasoning CloudCode model", "category": "Google Gemini"},
    "gemini-4-carbon-low": {"displayName": "Gemini 4 Carbon (Low)", "maxTokens": 1048576, "description": "Google Next-Gen Gemini 4 Carbon fast reasoning CloudCode model", "category": "Google Gemini"},
    "gemini-4-carbon-disabled": {"displayName": "Gemini 4 Carbon (Disabled)", "maxTokens": 1048576, "description": "Google Next-Gen Gemini 4 Carbon CloudCode model", "category": "Google Gemini"},
    "gemini-pro-orchestrator-g1-pro": {"displayName": "Orchestrator (Argon)", "maxTokens": 1048576, "description": "Antigravity Plan-and-Execute Argon Orchestrator", "category": "Google Gemini"},
    "gemini-flash-worker-agy": {"displayName": "Gemini Flash (Execution Worker)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Antigravity 256k Execution Worker", "category": "Google Gemini"},
    "gemini-4-argon": {"displayName": "Gemini 4 Argon", "maxTokens": 1048576, "description": "Google Next-Gen Gemini 4 Argon flagship CloudCode model (Argon-supported tier)", "category": "Google Gemini"},
    "gemini-4-argon-high": {"displayName": "Gemini 4 Argon (High)", "maxTokens": 1048576, "description": "Gemini 4 Argon with deep reasoning (Argon-supported tier)", "category": "Google Gemini"},
    "gemini-4-argon-medium": {"displayName": "Gemini 4 Argon (Medium)", "maxTokens": 1048576, "description": "Gemini 4 Argon with balanced reasoning (Argon-supported tier)", "category": "Google Gemini"},
    "gemini-4-argon-low": {"displayName": "Gemini 4 Argon (Low)", "maxTokens": 1048576, "description": "Gemini 4 Argon with fast reasoning (Argon-supported tier)", "category": "Google Gemini"},
    "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash (High Reasoning)", "maxTokens": 1048576, "description": "Google Flagship model with deep thinking & reasoning (Default)", "category": "Google Gemini"},
    "gemini-3.8-flash-medium": {"displayName": "Gemini 3.8 Flash (Medium Reasoning)", "maxTokens": 1048576, "description": "Balances speed and reasoning depth", "category": "Google Gemini"},
    "gemini-3.8-flash-low": {"displayName": "Gemini 3.8 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Fast responses with light reasoning", "category": "Google Gemini"},
    "gemini-3.8-flash-tiered": {"displayName": "Gemini 3.8 Flash (Tiered)", "maxTokens": 1048576, "description": "Dynamic tiered reasoning allocation", "category": "Google Gemini"},
    "gemini-3.8-flash-deepagent": {"displayName": "Gemini 3.8 Flash (DeepAgent)", "maxTokens": 1048576, "description": "Autonomous multi-agent swarm preset with 64k thinking budget", "category": "Google Gemini"},
    "gemini-3.5-flash-lite": {"displayName": "Gemini 3.5 Flash Lite", "maxTokens": 1048576, "description": "Gemini 3.5 Flash Lite low-latency", "category": "Google Gemini"},
    "gemini-pro-agent": {"displayName": "Gemini Pro Agent", "maxTokens": 1048576, "description": "Gemini Pro autonomous agent model", "category": "Google Gemini"},
    "gemini-3.1-flash-lite": {"displayName": "Gemini 3.1 Flash Lite (Ultra Fast)", "maxTokens": 1048576, "description": "Ultra fast low-latency model", "category": "Google Gemini"},
    "gemini-3-flash": {"displayName": "Gemini 3 Flash", "maxTokens": 1048576, "description": "Gemini 3 Flash stable", "category": "Google Gemini"},
    "gemini-3-flash-agent": {"displayName": "Gemini 3 Flash Agent", "maxTokens": 1048576, "description": "Gemini 3 Flash autonomous agent model", "category": "Google Gemini"},
    "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro", "maxTokens": 1048576, "description": "Gemini 2.5 Pro stable architecture", "category": "Google Gemini"},
    "gemini-2.5-flash": {"displayName": "Gemini 2.5 Flash", "maxTokens": 1048576, "description": "Gemini 2.5 Flash stable", "category": "Google Gemini"},
    "gemini-2.5-flash-lite": {"displayName": "Gemini 2.5 Flash Lite", "maxTokens": 1048576, "description": "Gemini 2.5 Flash Lite fast architecture", "category": "Google Gemini"},
    "gemini-3.1-flash-image": {"displayName": "Gemini 3.1 Flash Image", "maxTokens": 65536, "description": "Google CloudCode specialized image generation and multimodal vision model", "category": "Google Gemini"},
    "chat_20706": {"displayName": "Chat 20706 (Tab Code Completion 16k)", "maxTokens": 16384, "description": "Google Antigravity tab completion model", "category": "Google Gemini"},
    "chat_23310": {"displayName": "Chat 23310 (Tab Code Completion 32k)", "maxTokens": 32768, "description": "Google Antigravity tab completion model", "category": "Google Gemini"},
    "tab_flash_lite_preview": {"displayName": "Tab Flash Lite Preview", "maxTokens": 16384, "description": "Google Antigravity tab completion preview model", "category": "Google Gemini"},
    "tab_jump_flash_lite_preview": {"displayName": "Tab Jump Flash Lite Preview", "maxTokens": 16384, "description": "Google Antigravity tab jump navigation model", "category": "Google Gemini"},
    "models/proactive-observer-v10": {"displayName": "Proactive Observer v10", "maxTokens": 65536, "description": "Google CloudCode audio transcription and proactive observer model", "category": "Google Gemini"},

    # DeepSeek Models
    "deepseek-r1": {"displayName": "DeepSeek R1", "maxTokens": 128000, "description": "DeepSeek R1 open reasoning model", "category": "DeepSeek"},
    "deepseek-v3": {"displayName": "DeepSeek V3", "maxTokens": 128000, "description": "DeepSeek V3 high-throughput general purpose model", "category": "DeepSeek"},

    # Open Source Models
    "gpt-oss-120b-medium": {"displayName": "GPT-OSS 120B (Medium)", "maxTokens": 131072, "description": "Google CloudCode authentic open-source GPT model", "category": "Open Source"},
    "gpt-oss-120b": {"displayName": "GPT-OSS 120B", "maxTokens": 131072, "description": "Standard alias for GPT-OSS 120B", "category": "Open Source"},

    # Gemini Web Browser Models (Clean canonical suffix *-web)
    "gemini-4-hydrogen-web-high": {"displayName": "Gemini 4 Hydrogen (Web) (High)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen Web flagship model with deep reasoning", "category": "Gemini Web"},
    "gemini-4-hydrogen-web-medium": {"displayName": "Gemini 4 Hydrogen (Web) (Medium)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen Web model with balanced reasoning", "category": "Gemini Web"},
    "gemini-4-hydrogen-web-low": {"displayName": "Gemini 4 Hydrogen (Web) (Low)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen Web fast low-latency model", "category": "Gemini Web"},
    "gemini-4-hydrogen-web-xhigh": {"displayName": "Gemini 4 Hydrogen (Web) (XHigh)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen Web extra-high reasoning model", "category": "Gemini Web"},
    "gemini-4-hydrogen-web-spark": {"displayName": "Gemini 4 Hydrogen (Web) (Autonomous Spark Agent)", "maxTokens": 1048576, "maxOutputTokens": 262144, "description": "Gemini 4 Hydrogen Web autonomous Spark agent mode", "category": "Gemini Web"},
    "gemini-4-argon-web-high": {"displayName": "Gemini 4 Argon (Web) (High)", "maxTokens": 1048576, "description": "Gemini 4 Argon Web flagship model with deep reasoning", "category": "Gemini Web"},
    "gemini-4-argon-web-medium": {"displayName": "Gemini 4 Argon (Web) (Medium)", "maxTokens": 1048576, "description": "Gemini 4 Argon Web model with balanced reasoning", "category": "Gemini Web"},
    "gemini-4-argon-web-low": {"displayName": "Gemini 4 Argon (Web) (Low)", "maxTokens": 1048576, "description": "Gemini 4 Argon Web fast low-latency model", "category": "Gemini Web"},
    "gemini-4-argon-web-spark": {"displayName": "Gemini 4 Argon (Web) (Autonomous Spark Agent)", "maxTokens": 1048576, "description": "Gemini 4 Argon Web autonomous Spark agent mode", "category": "Gemini Web"},
    "gemini-3.8-flash-web-high": {"displayName": "Gemini 3.8 Flash (Web) (High)", "maxTokens": 1048576, "description": "Gemini 3.8 Flash Web model with deep reasoning", "category": "Gemini Web"},
    "gemini-3.8-flash-web-medium": {"displayName": "Gemini 3.8 Flash (Web) (Medium)", "maxTokens": 1048576, "description": "Gemini 3.8 Flash Web model with medium reasoning", "category": "Gemini Web"},
    "gemini-3.8-flash-web-low": {"displayName": "Gemini 3.8 Flash (Web) (Low)", "maxTokens": 1048576, "description": "Gemini 3.8 Flash Web fast low-latency model", "category": "Gemini Web"},
    "gemini-3.8-flash-web-spark": {"displayName": "Gemini 3.8 Flash (Web) (Autonomous Spark Agent)", "maxTokens": 1048576, "description": "Gemini 3.8 Flash Web autonomous Spark agent mode", "category": "Gemini Web"},
    "gemini-3.1-pro-web-high": {"displayName": "Gemini 3.1 Pro (Web) (High)", "maxTokens": 1048576, "description": "Gemini 3.1 Pro Web model with deep reasoning", "category": "Gemini Web"},
    "gemini-3.1-pro-web-medium": {"displayName": "Gemini 3.1 Pro (Web) (Medium)", "maxTokens": 1048576, "description": "Gemini 3.1 Pro Web model with medium reasoning", "category": "Gemini Web"},
    "gemini-3.1-pro-web-low": {"displayName": "Gemini 3.1 Pro (Web) (Low)", "maxTokens": 1048576, "description": "Gemini 3.1 Pro Web fast model", "category": "Gemini Web"},
    "gemini-3.5-flash-lite-web-high": {"displayName": "Gemini 3.5 Flash-Lite (Web) (High)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash-Lite Web model with deep reasoning", "category": "Gemini Web"},
    "gemini-3.5-flash-lite-web-medium": {"displayName": "Gemini 3.5 Flash-Lite (Web) (Medium)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash-Lite Web model with balanced reasoning", "category": "Gemini Web"},
    "gemini-3.5-flash-lite-web-low": {"displayName": "Gemini 3.5 Flash-Lite (Web) (Low)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash-Lite Web ultra-fast model", "category": "Gemini Web"},
}


def is_external_model(model_name: Any) -> bool:
    """Checks whether the requested model is routed to an external LLM provider."""
    if not model_name or not isinstance(model_name, str):
        return False
    try:
        from agy_proxy.providers import external_provider_manager
        return external_provider_manager.is_external_model(model_name)
    except Exception:
        return False


def normalize_model_name(model_name: Any) -> str:
    """Normalizes any requested model name, placeholder, or enum to an Antigravity backend model identifier."""
    if model_name is None:
        return DEFAULT_MODEL

    import re
    raw = str(model_name).strip().lower()
    cleaned = raw
    if not cleaned:
        return DEFAULT_MODEL

    # Retain external models with provider prefix without coercing to Google Antigravity endpoints
    if "/" in raw and is_external_model(raw):
        return raw

    if cleaned in REVERSE_PLACEHOLDER_MAP:
        return REVERSE_PLACEHOLDER_MAP[cleaned]

    # Detect explicit web targeting before stripping bracketed annotations
    is_web_annotated = "(web)" in raw or "[web]" in raw or "gemini-web" in raw

    # Remove provider prefixes (e.g. anthropic/gemini-3.7-flash-high or anthropic.gemini-3.7-flash-high)
    cleaned = re.sub(r"^(anthropic|openai|google|models)[./]", "", cleaned).strip()

    # Remove context annotations like [1m], (1m context), [thinking], etc.
    cleaned = re.sub(r"\[.*?\]", "", cleaned).strip()
    cleaned = re.sub(r"\(.*?\)", "", cleaned).strip()

    if cleaned in REVERSE_PLACEHOLDER_MAP:
        return REVERSE_PLACEHOLDER_MAP[cleaned]

    if cleaned in MODEL_ALIASES:
        return MODEL_ALIASES[cleaned]

    if is_external_model(cleaned):
        return cleaned

    if cleaned in VALID_WEB_MODELS:
        return cleaned

    if cleaned in VALID_CLOUDCODE_MODELS:
        return cleaned

    # Check if this is an explicit web request
    is_web_query = (
        is_web_annotated
        or cleaned.startswith("web-")
        or cleaned.startswith("web_")
        or cleaned.startswith("web/")
        or "-web" in cleaned
        or "_web" in cleaned
    )

    if is_web_query:
        if "hydrogen" in cleaned:
            if "spark" in cleaned:
                return "gemini-4-hydrogen-web-spark"
            if "task" in cleaned or "agent" in cleaned:
                return "gemini-4-hydrogen-web-task"
            if "xhigh" in cleaned or "extra" in cleaned:
                return "gemini-4-hydrogen-web-xhigh"
            if "high" in cleaned:
                return "gemini-4-hydrogen-web-high"
            if "low" in cleaned:
                return "gemini-4-hydrogen-web-low"
            if "med" in cleaned:
                return "gemini-4-hydrogen-web-medium"
            return "gemini-4-hydrogen-web-medium"
        if "spark" in cleaned:
            if "3.8" in cleaned or ("flash" in cleaned and "lite" not in cleaned):
                return "gemini-3.8-flash-web-spark"
            return "gemini-4-argon-web-spark"
        if "task" in cleaned or "agent" in cleaned:
            if "3.8" in cleaned or ("flash" in cleaned and "lite" not in cleaned):
                return "gemini-3.8-flash-web-task"
            return "gemini-4-argon-web-task"
        if "carbon" in cleaned:
            if "high" in cleaned:
                return "gemini-4-carbon-high"
            if "low" in cleaned:
                return "gemini-4-carbon-low"
            if "med" in cleaned:
                return "gemini-4-carbon-medium"
            if "disabled" in cleaned:
                return "gemini-4-carbon-disabled"
            return "gemini-4-carbon"
        if "argon" in cleaned:
            if "low" in cleaned:
                return "gemini-4-argon-web-low"
            if "med" in cleaned:
                return "gemini-4-argon-web-medium"
            return "gemini-4-argon-web-high"
        if "3.5" in cleaned or "lite" in cleaned:
            if "low" in cleaned:
                return "gemini-3.5-flash-lite-web-low"
            if "med" in cleaned:
                return "gemini-3.5-flash-lite-web-medium"
            return "gemini-3.5-flash-lite-web-high"
        if "3.1-pro" in cleaned or "3_1-pro" in cleaned or "3-1-pro" in cleaned or ("pro" in cleaned and "2.5" not in cleaned):
            if "low" in cleaned:
                return "gemini-3.1-pro-web-low"
            if "med" in cleaned:
                return "gemini-3.1-pro-web-medium"
            return "gemini-3.1-pro-web-high"
        if "3.8" in cleaned or "flash" in cleaned:
            if "low" in cleaned:
                return "gemini-3.8-flash-web-low"
            if "med" in cleaned:
                return "gemini-3.8-flash-web-medium"
            return "gemini-3.8-flash-web-high"
        return "gemini-4-argon-web-high"

    # Non-web Keyword checks
    # Claude query check
    is_claude_query = any(k in cleaned for k in ("claude", "sonnet", "opus", "haiku"))

    # Version specific checks (Gemini CloudCode API only)
    if not is_claude_query:
        if "hydrogen" in cleaned:
            if "spark" in cleaned or "task" in cleaned or "agent" in cleaned:
                return "gemini-4-hydrogen-web-spark"
            if "xhigh" in cleaned or "extra" in cleaned:
                return "gemini-4-hydrogen-xhigh"
            if "high" in cleaned:
                return "gemini-4-hydrogen-high"
            if "low" in cleaned:
                return "gemini-4-hydrogen-low"
            if "med" in cleaned:
                return "gemini-4-hydrogen-medium"
            return "gemini-4-hydrogen"
        if "spark" in cleaned or "task" in cleaned or "agent" in cleaned:
            if "flash-agent" in cleaned or "3-flash-agent" in cleaned:
                return "gemini-3-flash-agent"
            if "pro-agent" in cleaned:
                return "gemini-pro-agent"
            if "deepagent" in cleaned:
                return "gemini-3.8-flash-deepagent"
            if "3.8" in cleaned or ("flash" in cleaned and "lite" not in cleaned):
                return "gemini-3.8-flash-high"
            return "gemini-4-argon-high"
        if "carbon" in cleaned:
            if "high" in cleaned:
                return "gemini-4-carbon-high"
            if "low" in cleaned:
                return "gemini-4-carbon-low"
            if "med" in cleaned:
                return "gemini-4-carbon-medium"
            if "disabled" in cleaned:
                return "gemini-4-carbon-disabled"
            return "gemini-4-carbon"
        if "argon" in cleaned:
            if "low" in cleaned:
                return "gemini-4-argon-low"
            if "med" in cleaned:
                return "gemini-4-argon-medium"
            if "high" in cleaned:
                return "gemini-4-argon-high"
            return "gemini-4-argon"
        if "3.8" in cleaned or "3-8" in cleaned:
            if "low" in cleaned:
                return "gemini-3.8-flash-low"
            if "med" in cleaned:
                return "gemini-3.8-flash-medium"
            return "gemini-3.8-flash-high"
        if "3.7" in cleaned or "3-7" in cleaned:
            if "low" in cleaned:
                return "gemini-3.8-flash-low"
            if "med" in cleaned:
                return "gemini-3.8-flash-medium"
            if "tiered" in cleaned:
                return "gemini-3.8-flash-tiered"
            return "gemini-3.8-flash-high"
        if "3.6" in cleaned or "3-6" in cleaned:
            if "low" in cleaned:
                return "gemini-3.8-flash-low"
            if "med" in cleaned:
                return "gemini-3.8-flash-medium"
            if "tiered" in cleaned:
                return "gemini-3.8-flash-tiered"
            return "gemini-3.8-flash-high"
        if "3.5" in cleaned or "3-5" in cleaned:
            if "lite" in cleaned:
                if "high" in cleaned:
                    return "gemini-3.5-flash-lite-high"
                if "med" in cleaned:
                    return "gemini-3.5-flash-lite-medium"
                if "low" in cleaned:
                    return "gemini-3.5-flash-lite-low"
                return "gemini-3.5-flash-lite"
            return "gemini-3.8-flash-low"
        if "3.1-pro" in cleaned or "3_1-pro" in cleaned or "3-1-pro" in cleaned:
            # Standard API CloudCode has no 3.1 Pro: map to CloudCode native Pro architecture
            return "gemini-2.5-pro"
        if "2.5-pro" in cleaned or "2_5-pro" in cleaned:
            return "gemini-2.5-pro"

    # Claude 5.5 models
    if ("5.5" in cleaned or "5-5" in cleaned) and "gpt" not in cleaned:
        is_opus = "opus" in cleaned
        if "max" in cleaned:
            return "claude-opus-5-5-max" if is_opus else "claude-sonnet-5-5-max"
        if "high" in cleaned:
            return "claude-opus-5-5-high" if is_opus else "claude-sonnet-5-5-high"
        if "low" in cleaned:
            return "claude-opus-5-5-low" if is_opus else "claude-sonnet-5-5-low"
        if "med" in cleaned:
            return "claude-opus-5-5-medium" if is_opus else "claude-sonnet-5-5-medium"
        return "claude-opus-5-5-medium" if is_opus else "claude-sonnet-5-5-medium"

    # Family specific checks
    if "opus" in cleaned:
        return "claude-opus-4-6-thinking"
    if "sonnet" in cleaned:
        return "claude-sonnet-4-6"
    if "haiku" in cleaned or "flash-lite" in cleaned or "lite" in cleaned or "-mini" in cleaned or "mini-" in cleaned or cleaned == "mini" or "small" in cleaned or "micro" in cleaned:
        return "gemini-3.1-flash-lite"
    if "flash" in cleaned:
        return "gemini-3.8-flash-high"
    if "claude" in cleaned:
        return "claude-sonnet-4-6"

    # Prefix match
    for k, v in MODEL_ALIASES.items():
        if cleaned.startswith(k):
            return v

    # If it's already a valid model, keep it; otherwise fallback to DEFAULT_MODEL
    if model_name.strip() in VALID_CLOUDCODE_MODELS:
        return model_name.strip()

    return DEFAULT_MODEL


def is_3p_model(model: str) -> bool:
    """
    Returns True if the requested model belongs to the 3P (third-party) category
    (Claude, Sonnet, Opus, Haiku, GPT-OSS, Fable), and False if it is a native Gemini model
    (even if prefixed with 'anthropic.' by clients like Claude Code).
    """
    if not model:
        return False
    m = model.strip().lower()
    if "gemini" in m or "argon" in m or "spark" in m or "task" in m:
        return False
    if m == "anthropic":
        return True
    return any(k in m for k in ("claude", "sonnet", "opus", "haiku", "gpt-oss", "fable", "3p"))


CLAUDE_5_5_MODELS: set[str] = {
    "claude-sonnet-5-5-max",
    "claude-sonnet-5.5-max",
    "claude-sonnet-5-5-high",
    "claude-sonnet-5.5-high",
    "claude-sonnet-5-5-medium",
    "claude-sonnet-5.5-medium",
    "claude-sonnet-5-5-low",
    "claude-sonnet-5.5-low",
    "claude-opus-5-5-max",
    "claude-opus-5.5-max",
    "claude-opus-5-5-high",
    "claude-opus-5.5-high",
    "claude-opus-5-5-medium",
    "claude-opus-5.5-medium",
    "claude-opus-5-5-low",
    "claude-opus-5.5-low",
    "claude-5-5",
    "claude-5.5",
    "claude-sonnet-5-5",
    "claude-sonnet-5.5",
    "claude-opus-5-5",
    "claude-opus-5.5",
    "claude-5-5-sonnet",
    "claude-5.5-sonnet",
    "claude-5-5-opus",
    "claude-5.5-opus",
}

CLAUDE_5_5_TO_4_6_FALLBACK: dict[str, str] = {
    # Sonnet 5.5 variants -> claude-sonnet-4-6
    "claude-sonnet-5-5-max": "claude-sonnet-4-6",
    "claude-sonnet-5.5-max": "claude-sonnet-4-6",
    "claude-sonnet-5-5-high": "claude-sonnet-4-6",
    "claude-sonnet-5.5-high": "claude-sonnet-4-6",
    "claude-sonnet-5-5-medium": "claude-sonnet-4-6",
    "claude-sonnet-5.5-medium": "claude-sonnet-4-6",
    "claude-sonnet-5-5-low": "claude-sonnet-4-6",
    "claude-sonnet-5.5-low": "claude-sonnet-4-6",
    "claude-sonnet-5-5": "claude-sonnet-4-6",
    "claude-sonnet-5.5": "claude-sonnet-4-6",
    "claude-5-5": "claude-sonnet-4-6",
    "claude-5.5": "claude-sonnet-4-6",
    "claude-5-5-sonnet": "claude-sonnet-4-6",
    "claude-5.5-sonnet": "claude-sonnet-4-6",
    # Opus 5.5 variants -> claude-opus-4-6-thinking
    "claude-opus-5-5-max": "claude-opus-4-6-thinking",
    "claude-opus-5.5-max": "claude-opus-4-6-thinking",
    "claude-opus-5-5-high": "claude-opus-4-6-thinking",
    "claude-opus-5.5-high": "claude-opus-4-6-thinking",
    "claude-opus-5-5-medium": "claude-opus-4-6-thinking",
    "claude-opus-5.5-medium": "claude-opus-4-6-thinking",
    "claude-opus-5-5-low": "claude-opus-4-6-thinking",
    "claude-opus-5.5-low": "claude-opus-4-6-thinking",
    "claude-opus-5-5": "claude-opus-4-6-thinking",
    "claude-opus-5.5": "claude-opus-4-6-thinking",
    "claude-5-5-opus": "claude-opus-4-6-thinking",
    "claude-5.5-opus": "claude-opus-4-6-thinking",
}


def is_claude_5_5_model(model_name: str | None) -> bool:
    """Checks whether the requested model is a Claude 5.5 variant."""
    if not model_name:
        return False
    norm = normalize_model_name(model_name)
    return norm in CLAUDE_5_5_MODELS or (norm.startswith("claude-") and ("5-5" in norm or "5.5" in norm))


def get_claude_fallback_model(model_name: str | None) -> str:
    """
    Returns the corresponding Claude 4.6 fallback model for a Claude 5.5 variant.
    Preserves model tier (Opus -> Opus 4.6 Thinking, Sonnet -> Sonnet 4.6).
    """
    if not model_name:
        return "claude-sonnet-4-6"
    norm = normalize_model_name(model_name)
    if "opus" in norm:
        return "claude-opus-4-6-thinking"
    return "claude-sonnet-4-6"


CLAUDE_4_5_MODELS: set[str] = {
    "claude-haiku-4-5",
    "claude-haiku-4-5-thinking",
}


def is_claude_4_5_model(model_name: str | None) -> bool:
    """Checks whether the requested model is a Claude 4.5 variant (e.g. Haiku 4.5)."""
    if not model_name:
        return False
    norm = normalize_model_name(model_name)
    return norm in CLAUDE_4_5_MODELS or (norm.startswith("claude-") and "haiku" in norm)


def is_claude_4_6_model(model_name: str | None) -> bool:
    """Checks whether the requested model is a Claude 4.6 variant (e.g. Sonnet 4.6, Opus 4.6)."""
    if not model_name:
        return False
    norm = normalize_model_name(model_name)
    return (norm.startswith("claude-") or "claude" in norm) and not is_claude_5_5_model(norm) and not is_claude_4_5_model(norm)


def get_claude_5_5_upgrade_model(model_name: str | None) -> str:
    """
    Returns the corresponding Claude 5.5 upgrade model for a Claude 4.6 variant.
    Preserves model tier (Opus -> Opus 5.5 High, Sonnet -> Sonnet 5.5 High).
    """
    if not model_name:
        return "claude-sonnet-5-5-high"
    norm = normalize_model_name(model_name)
    if "opus" in norm:
        return "claude-opus-5-5-high"
    return "claude-sonnet-5-5-high"


ARGON_MODELS: set[str] = {
    "gemini-4-argon",
    "gemini-4-argon-high",
    "gemini-4-argon-medium",
    "gemini-4-argon-low",
    "gemini-4-hydrogen",
    "gemini-4-hydrogen-high",
    "gemini-4-hydrogen-medium",
    "gemini-4-hydrogen-low",
    "gemini-4-hydrogen-xhigh",
    "gemini-4-carbon",
    "gemini-4-carbon-high",
    "gemini-4-carbon-medium",
    "gemini-4-carbon-low",
    "gemini-4-carbon-disabled",
    "gemini-4-argon-web-high",
    "gemini-4-argon-web-medium",
    "gemini-4-argon-web-low",
    "gemini-4-argon-web-spark",
    "gemini-4-hydrogen-web-high",
    "gemini-4-hydrogen-web-medium",
    "gemini-4-hydrogen-web-low",
    "gemini-4-hydrogen-web-xhigh",
    "gemini-4-hydrogen-web-spark",
}


def is_argon_model(model_name: str | None) -> bool:
    """Checks whether the requested model is a Gemini 4 Argon, Carbon, or Hydrogen variant."""
    if not model_name:
        return False
    norm = normalize_model_name(model_name)
    return "argon" in norm or "carbon" in norm or "hydrogen" in norm


def is_hydrogen_model(model_name: str | None) -> bool:
    """Checks whether the requested model is a Gemini 4 Hydrogen variant."""
    if not model_name:
        return False
    norm = normalize_model_name(model_name)
    return "hydrogen" in norm


def resolve_cloudcode_argon_model(model_name: str) -> str:
    """Maps bare 'gemini-4-argon' (or '-disabled') to 'gemini-4-argon-high', carbon to 'gemini-4-carbon-disabled', and hydrogen to 'gemini-4-hydrogen-medium'."""
    if model_name in ("gemini-4-argon", "gemini-4-argon-disabled"):
        return "gemini-4-argon-high"
    if model_name in ("gemini-4-carbon", "gemini-4-carbon-disabled"):
        return "gemini-4-carbon-disabled"
    if model_name in ("gemini-4-hydrogen", "gemini-4-hydrogen-disabled"):
        return "gemini-4-hydrogen-medium"
    return model_name


def is_gemini_web_model(model_name: str | None) -> bool:
    """Checks whether the requested model explicitly targets the Gemini Web browser backend."""
    if not model_name:
        return False
    raw = str(model_name).strip().lower()
    if raw.startswith("web-") or raw.startswith("web_") or raw.startswith("web/") or raw.startswith("web:"):
        return True
    if "-web" in raw or "_web" in raw or "(web)" in raw or "[web]" in raw or "gemini-web" in raw:
        return True
    if raw in VALID_WEB_MODELS:
        return True
    aliased = MODEL_ALIASES.get(raw)
    if aliased and (aliased.startswith("web-") or "-web" in aliased or aliased in VALID_WEB_MODELS):
        return True
    norm = normalize_model_name(model_name).lower()
    return norm.startswith("web-") or "-web" in norm or "_web" in norm or norm in VALID_WEB_MODELS


def is_web_only_argon_model(model_name: str | None) -> bool:
    """Legacy helper: returns False as Argon is available in CloudCode API for supported subscription tiers."""
    return False


def get_argon_fallback_model(model_name: str | None) -> str:
    """
    Returns the corresponding Google Flagship Gemini 3.8 fallback model for an Argon variant.
    Preserves reasoning level (High -> Flash High, Medium -> Flash Medium, Low -> Flash Low).
    """
    if not model_name:
        return "gemini-3.8-flash-high"
    norm = normalize_model_name(model_name)
    if "low" in norm:
        return "gemini-3.8-flash-low"
    if "med" in norm:
        return "gemini-3.8-flash-medium"
    return "gemini-3.8-flash-high"


def is_deepagent_model(model_name: str | None) -> bool:
    """Checks whether the requested model is a DeepAgent swarm preset."""
    if not model_name:
        return False
    norm = normalize_model_name(model_name)
    return "deepagent" in norm or "deep-agent" in norm


# ==============================================================================
# ANTIGRAVITY RUNTIME & SERVICE CONSTANTS
# ==============================================================================

class ThinkingLevel(str, Enum):
    """Standardized reasoning effort / thinking levels matching Antigravity SDK."""
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTRA_HIGH = "extra_high"


class ServiceTier(str, Enum):
    """Google CloudCode / Antigravity routing and latency tiers."""
    STANDARD = "standard"
    PRIORITY = "priority"
    FLEX = "flex"


# Maximum context window sizes in tokens for native and compatibility models
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # Gemini models (1M or 2M tokens)
    "gemini-4-argon": 1_048_576,
    "gemini-4-hydrogen": 1_048_576,
    "gemini-4-carbon": 1_048_576,
    "gemini-3.8-flash": 1_048_576,
    "gemini-3.7-flash": 1_048_576,
    "gemini-3.6-flash": 1_048_576,
    "gemini-3.5-flash": 1_048_576,
    "gemini-3.5-flash-lite": 1_048_576,
    "gemini-3.1-flash-lite": 1_048_576,
    "gemini-3.1-pro": 2_097_152,
    "gemini-2.5-pro": 2_097_152,
    "gemini-2.5-flash": 1_048_576,
    "gemini-pro-orchestrator-g1-pro": 1_048_576,
    "gemini-flash-worker-agy": 1_048_576,
    # Claude models (200k tokens)
    "claude-sonnet-4-6": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
    "claude-sonnet-5-5": 200_000,
    "claude-opus-5-5": 200_000,
    "claude-3-5-sonnet": 200_000,
    "claude-3-7-sonnet": 200_000,
    "claude-3-opus": 200_000,
    "claude-3-5-haiku": 200_000,
    # GPT models
    "gpt-oss-120b": 128_000,
    "gpt-oss-120b-medium": 128_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "o1": 200_000,
    "o3-mini": 200_000,
    # DeepSeek models
    "deepseek-r1": 65_536,
    "deepseek-v3": 65_536,
    "deepseek-chat": 65_536,
}


def get_model_context_window(model_name: str | None) -> int:
    """
    Returns the maximum context window size (in tokens) for a given model.
    Checks exact raw model names, registered external providers, alias normalized models,
    heuristic prefixes, and defaults to 200,000.
    """
    if not model_name:
        return 200_000
    raw = str(model_name).strip().lower()

    # 1. Direct match on raw input name (e.g. "deepseek-r1", "gpt-4o")
    if raw in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[raw]
    for k, v in MODEL_CONTEXT_WINDOWS.items():
        if raw.startswith(k) or k in raw:
            return v

    # 2. Check external provider model configurations if loaded
    try:
        from agy_proxy.providers import external_provider_manager
        ext = external_provider_manager.resolve_model(model_name)
        if ext and ext[2] and getattr(ext[2], "context_window", None):
            return int(ext[2].context_window)
    except Exception:
        pass

    # 3. Check normalized model name
    norm = normalize_model_name(model_name).lower()
    if norm in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[norm]
    for k, v in MODEL_CONTEXT_WINDOWS.items():
        if norm.startswith(k) or k in norm:
            return v

    # 4. Heuristics based on raw or normalized name
    check_str = f"{raw} {norm}"
    if "pro" in check_str and "gemini" in check_str:
        return 2_097_152
    if "gemini" in check_str or "argon" in check_str or "hydrogen" in check_str or "carbon" in check_str:
        return 1_048_576
    if "claude" in check_str:
        return 200_000
    if "deepseek" in check_str:
        return 65_536
    if "gpt" in check_str or "120b" in check_str:
        return 128_000

    return 200_000


# ---------------------------------------------------------
# OpenAI Compatible Schemas
# ---------------------------------------------------------

class OpenAIMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class OpenAIChatRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: list[OpenAIMessage | AnthropicMessage | dict[str, Any]]
    stream: bool | None = False
    temperature: float | None = None
    top_p: float | None = None
    n: int | None = 1
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    user: str | None = None
    thinking: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    stream_options: dict[str, Any] | None = None
    model_config = ConfigDict(extra="allow")


def get_default_context_window(model_id: str, max_tokens: int | None = None) -> int:
    """Returns the expected context window size in tokens for a model."""
    m = (model_id or "").lower()
    # Gemini models have at least 1M context window (some 2M)
    if any(k in m for k in ("gemini", "carbon", "argon", "hydrogen", "orchestrator", "worker")):
        if max_tokens and max_tokens > 65536:
            return max_tokens
        return 1048576
    if max_tokens and max_tokens > 0:
        return max_tokens
    if "1m" in m or "1-m" in m:
        return 1000000
    if "2m" in m or "2-m" in m:
        return 2000000
    if any(k in m for k in ("claude", "opus", "sonnet", "haiku", "fable", "mythos")):
        if any(v in m for v in ("5-5", "5.5", "5-2", "5.2")):
            return 1000000
        return 200000
    if any(k in m for k in ("gpt-6", "gpt-5", "codex")):
        return 272000
    if any(k in m for k in ("o1", "o3")):
        return 200000
    if any(k in m for k in ("gpt-4", "o4")):
        return 128000
    if "deepseek" in m:
        return 131072
    return 128000


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "antigravity"
    display_name: str | None = None
    max_tokens: int | None = None
    context_window: int | None = None
    context_length: int | None = None
    max_context_length: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    remaining_quota: float | None = None
    reset_time: str | None = None
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def populate_context_attributes(self) -> "ModelCard":
        ctx = self.context_window or self.context_length or self.max_context_length
        if not ctx:
            ctx = get_default_context_window(self.id, self.max_tokens)
        if not self.context_window:
            self.context_window = ctx
        if not self.context_length:
            self.context_length = ctx
        if not self.max_context_length:
            self.max_context_length = ctx
        if not self.max_tokens:
            self.max_tokens = ctx
        if not self.max_input_tokens:
            self.max_input_tokens = ctx
        return self


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelCard]


class OpenAIEmbeddingRequest(BaseModel):
    input: str | list[str] | list[int] | list[list[int]]
    model: str = "text-embedding-3-small"
    encoding_format: str | None = "float"
    dimensions: int | None = None
    user: str | None = None
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------
# Anthropic Compatible Schemas
# ---------------------------------------------------------

class AnthropicContentBlock(BaseModel):
    type: str
    text: str | None = None
    thinking: str | None = None
    signature: str | None = None
    source: dict[str, Any] | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    content: str | list[dict[str, Any]] | None = None
    is_error: bool | None = None
    model_config = ConfigDict(extra="allow")


class AnthropicMessage(BaseModel):
    role: str
    content: str | list[AnthropicContentBlock | dict[str, Any]]
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    model_config = ConfigDict(extra="allow")


class AnthropicRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: list[AnthropicMessage | OpenAIMessage | dict[str, Any]]
    system: str | list[dict[str, Any]] | None = None
    max_tokens: int | None = 4096
    temperature: float | None = None
    top_p: float | None = None
    stream: bool | None = False
    thinking: dict[str, Any] | None = None
    effort: str | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    output_config: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    betas: list[str] | None = None
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------
# OpenAI Compatible Image Generation Schemas
# ---------------------------------------------------------

class ImageGenerationRequest(BaseModel):
    prompt: str
    model: str = "gemini-3.1-flash-image"
    n: int | None = 1
    quality: str | None = "standard"
    response_format: str | None = "b64_json"  # "url" or "b64_json"
    size: str | None = "1024x1024"
    style: str | None = "vivid"
    user: str | None = None
    model_config = ConfigDict(extra="allow")


class ImageGenerationData(BaseModel):
    b64_json: str | None = None
    url: str | None = None
    revised_prompt: str | None = None
    model_config = ConfigDict(extra="allow")


class ImageGenerationResponse(BaseModel):
    created: int = Field(default_factory=lambda: int(time.time()))
    data: list[ImageGenerationData]
    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------
# OpenAI Compatible Audio Transcription Schemas
# ---------------------------------------------------------

class AudioTranscriptionResponse(BaseModel):
    text: str
    model_config = ConfigDict(extra="allow")


def is_image_model(model_name: Any) -> bool:
    """Returns True if the model name refers to an image generation model."""
    if model_name is None:
        return False
    cleaned = str(model_name).lower()
    return any(k in cleaned for k in ("image", "dall-e", "imagen"))


def is_audio_model(model_name: Any) -> bool:
    """Returns True if the model name refers to an audio transcription model."""
    if model_name is None:
        return False
    cleaned = str(model_name).lower()
    return any(k in cleaned for k in ("whisper", "proactive-observer", "audio"))


OpenAIChatRequest.model_rebuild()
AnthropicRequest.model_rebuild()


