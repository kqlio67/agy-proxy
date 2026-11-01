"""
Data models and schemas for OpenAI, Anthropic, and Gemini API requests and responses.
"""

from __future__ import annotations

import time
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


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
    # 1. Gemini 4 Argon (API models - requires Argon-supported subscription tier)
    # --------------------------------------------------------------------------
    "gemini-4-argon": "gemini-4-argon",
    "gemini-4-argon-high": "gemini-4-argon-high",
    "gemini-4-argon-medium": "gemini-4-argon-medium",
    "gemini-4-argon-low": "gemini-4-argon-low",
    "gemini-4-argon-disabled": "gemini-4-argon",

    # Gemini 3.8 Aliases (Latest default)
    "gemini-3.8-flash": "gemini-3.8-flash-high",
    "gemini-3.8-flash-high": "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low": "gemini-3.8-flash-low",
    "gemini-3.8-flash-tiered": "gemini-3.8-flash-tiered",
    "gemini-3.8-flash-n": "gemini-3.8-flash-high",
    "gemini-3.8": "gemini-3.8-flash-high",
    "gemini-3-8": "gemini-3.8-flash-high",
    "gemini-3.8-flash-preview": "gemini-3.8-flash-high",
    # Antigravity Tab & Jump Completion models
    "chat_20706": "chat_20706",
    "chat_23310": "chat_23310",
    "tab_flash_lite_preview": "tab_flash_lite_preview",
    "tab_jump_flash_lite_preview": "tab_jump_flash_lite_preview",

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
    "gemini-3.5-flash": "gemini-3.5-flash-low",
    "gemini-3.5-flash-high": "gemini-3-flash-agent",
    "gemini-3.5-flash-medium": "gemini-3.5-flash-low",
    "gemini-3.5-flash-low": "gemini-3.5-flash-low",
    "gemini-3.5-flash-extra-low": "gemini-3.5-flash-extra-low",
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
    "gemini-2.5-flash-thinking": "gemini-2.5-flash-thinking",

    # --------------------------------------------------------------------------
    # 2. GEMINI WEB MODELS (Routed EXCLUSIVELY via gemini.google.com web session)
    #    Clean canonical suffix *-web models
    # --------------------------------------------------------------------------
    "gemini-4-argon-web": "gemini-4-argon-web-high",
    "gemini-4-argon-web-high": "gemini-4-argon-web-high",
    "gemini-4-argon-web-medium": "gemini-4-argon-web-medium",
    "gemini-4-argon-web-low": "gemini-4-argon-web-low",
    "gemini-4-argon-web-spark": "gemini-4-argon-web-spark",

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
    "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
    "claude-opus-4-6": "claude-opus-4-6-thinking",
    "claude-opus-4.6": "claude-opus-4-6-thinking",
    "claude-4-6-opus": "claude-opus-4-6-thinking",
    "claude-4.6-opus": "claude-opus-4-6-thinking",
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
    "claude-3-5-haiku": "gemini-3.1-flash-lite",
    "claude-3.5-haiku": "gemini-3.1-flash-lite",
    "claude-3-5-haiku-20241022": "gemini-3.1-flash-lite",
    "claude-3.5-haiku-20241022": "gemini-3.1-flash-lite",
    "claude-3-5-haiku-latest": "gemini-3.1-flash-lite",
    "claude-3.5-haiku-latest": "gemini-3.1-flash-lite",
    "claude-3-haiku": "gemini-3.1-flash-lite",
    "claude-3-haiku-20240307": "gemini-3.1-flash-lite",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-opus": "claude-opus-4-6-thinking",
    "claude-haiku": "gemini-3.1-flash-lite",

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
    # Gemini 4 Argon (CloudCode API - requires Argon-supported subscription tier)
    "gemini-4-argon",
    "gemini-4-argon-high",
    "gemini-4-argon-medium",
    "gemini-4-argon-low",
    # Gemini 3.8
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "gemini-3.8-flash-tiered",
    "gemini-3.8-flash-deepagent",
    # Gemini 3.5 & earlier
    "gemini-3.5-flash-low",
    "gemini-3.5-flash-extra-low",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-image",
    "gemini-3-flash",
    "gemini-3-flash-agent",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-thinking",
    # 3rd Party models
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "claude-sonnet-4-6-deepagent",
    "claude-opus-4-6-deepagent",
    "claude-sonnet-5-5-high",
    "claude-sonnet-5-5-medium",
    "claude-sonnet-5-5-low",
    "claude-opus-5-5-high",
    "claude-opus-5-5-medium",
    "claude-opus-5-5-low",
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
    "gpt-oss-120b-medium": "MODEL_PLACEHOLDER_M410",
    # Official Authentic CloudCode Enums from Antigravity Hub v2.28.2 / cl=1009437749
    "claude-opus-5-5-low": "MODEL_PLACEHOLDER_M400",
    "claude-opus-5-5-medium": "MODEL_PLACEHOLDER_M401",
    "claude-opus-5-5-high": "MODEL_PLACEHOLDER_M402",
    "claude-sonnet-5-5-low": "MODEL_PLACEHOLDER_M403",
    "claude-sonnet-5-5-medium": "MODEL_PLACEHOLDER_M404",
    "claude-sonnet-5-5-high": "MODEL_PLACEHOLDER_M405",
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
    "claude-sonnet-4-6-deepagent": "MODEL_PLACEHOLDER_M527",
    "claude-opus-4-6-deepagent": "MODEL_PLACEHOLDER_M528",
    "gemini-3.7-flash-tiered": "MODEL_PLACEHOLDER_M523",
    "gemini-3.6-flash-high": "MODEL_PLACEHOLDER_M524",
    "gemini-3.6-flash-medium": "MODEL_PLACEHOLDER_M525",
    "gemini-3.6-flash-low": "MODEL_PLACEHOLDER_M526",
    "gemini-3.1-pro-high": "MODEL_PLACEHOLDER_M531",
    "gemini-3.1-pro-medium": "MODEL_PLACEHOLDER_M532",
    "gemini-3.1-pro-low": "MODEL_PLACEHOLDER_M533",
    "gemini-3.6-flash-tiered": "MODEL_PLACEHOLDER_M543",
    "gemini-3.5-flash-lite-high": "MODEL_PLACEHOLDER_M544",
    "gemini-3.5-flash-lite-medium": "MODEL_PLACEHOLDER_M545",
    "gemini-3.5-flash-lite-low": "MODEL_PLACEHOLDER_M546",
    "gemini-pro-agent": "MODEL_PLACEHOLDER_M547",
}

REVERSE_PLACEHOLDER_MAP: dict[str, str] = {
    v.lower(): k for k, v in CLOUDCODE_PLACEHOLDER_MAP.items()
}
for _m, _p in CLOUDCODE_PLACEHOLDER_MAP.items():
    if _p.startswith("MODEL_PLACEHOLDER_M"):
        try:
            _num = int(_p.split("MODEL_PLACEHOLDER_M")[1])
            _enum_val = 1000 + _num
            REVERSE_PLACEHOLDER_MAP[str(_enum_val)] = _m
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
    "340": "claude-sonnet-4-6",
    "341": "claude-sonnet-4-6",
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
    "1417": "gemini-4-argon-high",
    "1418": "gemini-4-argon-medium",
    "1419": "gemini-4-argon-low",
    "1421": "gemini-4-argon",
    "1422": "gemini-4-argon-medium",
    "1423": "gemini-4-argon-low",
    "model_placeholder_m417": "gemini-4-argon-high",
    "model_placeholder_m418": "gemini-4-argon-medium",
    "model_placeholder_m419": "gemini-4-argon-low",
    "model_placeholder_m421": "gemini-4-argon",
    "model_placeholder_m422": "gemini-4-argon-medium",
    "model_placeholder_m423": "gemini-4-argon-low",
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
    "model_placeholder_m527": "gemini-3-flash-agent",
    "model_placeholder_m528": "gemini-3.5-flash-low",
    "model_placeholder_m529": "gemini-3.5-flash-extra-low",
    "model_placeholder_m530": "gemini-3.5-flash-lite",
    "model_placeholder_m534": "gemini-3.1-flash-lite",
    "model_placeholder_m535": "gemini-3-flash",
    "model_placeholder_m536": "gemini-2.5-pro",
    "model_placeholder_m537": "gemini-2.5-flash",
    "model_placeholder_m538": "gemini-2.5-flash-lite",
    "model_placeholder_m539": "gemini-2.5-flash-thinking",
    "model_placeholder_m548": "gemini-3.1-flash-image",
    "1521": "gemini-3.8-flash-tiered",
    "1527": "gemini-3-flash-agent",
    "1528": "gemini-3.5-flash-low",
    "1529": "gemini-3.5-flash-extra-low",
    "1530": "gemini-3.5-flash-lite",
    "1534": "gemini-3.1-flash-lite",
    "1535": "gemini-3-flash",
    "1536": "gemini-2.5-pro",
    "1537": "gemini-2.5-flash",
    "1538": "gemini-2.5-flash-lite",
    "1539": "gemini-2.5-flash-thinking",
    "1548": "gemini-3.1-flash-image",
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
    "model_claude_4_sonnet": "claude-sonnet-4-6",
    "model_claude_4_sonnet_thinking": "claude-sonnet-4-6",
    "model_claude_4_opus": "claude-opus-4-6-thinking",
    "model_claude_4_opus_thinking": "claude-opus-4-6-thinking",
    "model_claude_4_5_sonnet": "claude-sonnet-4-6",
    "model_claude_4_5_sonnet_thinking": "claude-sonnet-4-6",
    "model_claude_4_5_haiku": "claude-sonnet-4-6",
    "model_claude_4_5_haiku_thinking": "claude-sonnet-4-6",
})

EXACT_MODEL_METADATA: dict[str, dict[str, Any]] = {
    # Gemini 4 Argon (CloudCode API - requires Argon-supported subscription tier)
    "gemini-4-argon": {"displayName": "Gemini 4 Argon", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    "gemini-4-argon-high": {"displayName": "Gemini 4 Argon (High)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 3},
    "gemini-4-argon-medium": {"displayName": "Gemini 4 Argon (Medium)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 2},
    "gemini-4-argon-low": {"displayName": "Gemini 4 Argon (Low)", "maxTokens": 1048576, "maxOutputTokens": 65536, "supportsImages": True, "supportsVideo": True, "supportsThinking": True, "thinkingBudget": -1, "thinkingLevel": 1},
    # Gemini 3.8 Flagship
    "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash (High)", "maxTokens": 1048576},
    "gemini-3.8-flash-medium": {"displayName": "Gemini 3.8 Flash (Medium)", "maxTokens": 1048576},
    "gemini-3.8-flash-low": {"displayName": "Gemini 3.8 Flash (Low)", "maxTokens": 1048576},
    "gemini-3.8-flash-tiered": {"displayName": "Gemini 3.8 Flash (Tiered)", "maxTokens": 1048576},
    "gemini-3.8-flash-n": {"displayName": "Gemini 3.8 Flash (High)", "maxTokens": 1048576},
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
    "claude-sonnet-5-5-high": {"displayName": "Claude Sonnet 5.5 (High)", "maxTokens": 200000},
    "claude-sonnet-5-5-medium": {"displayName": "Claude Sonnet 5.5 (Medium)", "maxTokens": 200000},
    "claude-sonnet-5-5-low": {"displayName": "Claude Sonnet 5.5 (Low)", "maxTokens": 200000},
    "claude-opus-5-5-high": {"displayName": "Claude Opus 5.5 (High)", "maxTokens": 200000},
    "claude-opus-5-5-medium": {"displayName": "Claude Opus 5.5 (Medium)", "maxTokens": 200000},
    "claude-opus-5-5-low": {"displayName": "Claude Opus 5.5 (Low)", "maxTokens": 200000},
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
    "claude-3-5-haiku": {"displayName": "Claude 3.5 Haiku", "maxTokens": 200000, "description": "Claude 3.5 Haiku fast lightweight alias", "category": "Anthropic"},
    "claude-3-5-haiku-latest": {"displayName": "Claude 3.5 Haiku (Latest)", "maxTokens": 200000, "description": "Anthropic Claude 3.5 Haiku fast lightweight alias", "category": "Anthropic"},

    # Google Gemini Models (CloudCode API)
    "gemini-4-argon": {"displayName": "Gemini 4 Argon", "maxTokens": 1048576, "description": "Google Next-Gen Gemini 4 Argon flagship CloudCode model (Argon-supported tier)", "category": "Google Gemini"},
    "gemini-4-argon-high": {"displayName": "Gemini 4 Argon (High)", "maxTokens": 1048576, "description": "Gemini 4 Argon with deep reasoning (Argon-supported tier)", "category": "Google Gemini"},
    "gemini-4-argon-medium": {"displayName": "Gemini 4 Argon (Medium)", "maxTokens": 1048576, "description": "Gemini 4 Argon with balanced reasoning (Argon-supported tier)", "category": "Google Gemini"},
    "gemini-4-argon-low": {"displayName": "Gemini 4 Argon (Low)", "maxTokens": 1048576, "description": "Gemini 4 Argon with fast reasoning (Argon-supported tier)", "category": "Google Gemini"},
    "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash (High Reasoning)", "maxTokens": 1048576, "description": "Google Flagship model with deep thinking & reasoning (Default)", "category": "Google Gemini"},
    "gemini-3.8-flash-medium": {"displayName": "Gemini 3.8 Flash (Medium Reasoning)", "maxTokens": 1048576, "description": "Balances speed and reasoning depth", "category": "Google Gemini"},
    "gemini-3.8-flash-low": {"displayName": "Gemini 3.8 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Fast responses with light reasoning", "category": "Google Gemini"},
    "gemini-3.8-flash-tiered": {"displayName": "Gemini 3.8 Flash (Tiered)", "maxTokens": 1048576, "description": "Dynamic tiered reasoning allocation", "category": "Google Gemini"},
    "gemini-3.8-flash-deepagent": {"displayName": "Gemini 3.8 Flash (DeepAgent)", "maxTokens": 1048576, "description": "Autonomous multi-agent swarm preset with 64k thinking budget", "category": "Google Gemini"},
    "gemini-3.7-flash-high": {"displayName": "Gemini 3.7 Flash (High Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.7 Flash with high reasoning depth", "category": "Google Gemini"},
    "gemini-3.7-flash-medium": {"displayName": "Gemini 3.7 Flash (Medium Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.7 Flash with medium reasoning", "category": "Google Gemini"},
    "gemini-3.7-flash-low": {"displayName": "Gemini 3.7 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.7 Flash fast reasoning", "category": "Google Gemini"},
    "gemini-3.7-flash-tiered": {"displayName": "Gemini 3.7 Flash (Tiered)", "maxTokens": 1048576, "description": "Gemini 3.7 Flash tiered reasoning", "category": "Google Gemini"},
    "gemini-3.6-flash-high": {"displayName": "Gemini 3.6 Flash (High Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.6 Flash with high reasoning", "category": "Google Gemini"},
    "gemini-3.6-flash-medium": {"displayName": "Gemini 3.6 Flash (Medium Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.6 Flash with medium reasoning", "category": "Google Gemini"},
    "gemini-3.6-flash-low": {"displayName": "Gemini 3.6 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.6 Flash with low reasoning", "category": "Google Gemini"},
    "gemini-3.6-flash-tiered": {"displayName": "Gemini 3.6 Flash", "maxTokens": 1048576, "description": "Gemini 3.6 Flash tiered", "category": "Google Gemini"},
    "gemini-3.5-flash-low": {"displayName": "Gemini 3.5 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash low reasoning", "category": "Google Gemini"},
    "gemini-3.5-flash-extra-low": {"displayName": "Gemini 3.5 Flash (Extra Low)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash extra-low latency", "category": "Google Gemini"},
    "gemini-3.5-flash-lite": {"displayName": "Gemini 3.5 Flash Lite", "maxTokens": 1048576, "description": "Gemini 3.5 Flash Lite low-latency", "category": "Google Gemini"},
    "gemini-pro-agent": {"displayName": "Gemini Pro Agent", "maxTokens": 1048576, "description": "Gemini Pro autonomous agent model", "category": "Google Gemini"},
    "gemini-3.1-pro-high": {"displayName": "Gemini 3.1 Pro (High Reasoning)", "maxTokens": 1048576, "description": "Google Pro tier model with deep reasoning", "category": "Google Gemini"},
    "gemini-3.1-pro-low": {"displayName": "Gemini 3.1 Pro (Low Reasoning)", "maxTokens": 1048576, "description": "Google Pro tier model with standard reasoning", "category": "Google Gemini"},
    "gemini-3.1-flash-lite": {"displayName": "Gemini 3.1 Flash Lite (Ultra Fast)", "maxTokens": 1048576, "description": "Ultra fast low-latency model", "category": "Google Gemini"},
    "gemini-3-flash": {"displayName": "Gemini 3 Flash", "maxTokens": 1048576, "description": "Gemini 3 Flash stable", "category": "Google Gemini"},
    "gemini-3-flash-agent": {"displayName": "Gemini 3 Flash Agent", "maxTokens": 1048576, "description": "Gemini 3 Flash autonomous agent model", "category": "Google Gemini"},
    "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro", "maxTokens": 1048576, "description": "Gemini 2.5 Pro stable architecture", "category": "Google Gemini"},
    "gemini-2.5-flash": {"displayName": "Gemini 2.5 Flash", "maxTokens": 1048576, "description": "Gemini 2.5 Flash stable", "category": "Google Gemini"},
    "gemini-2.5-flash-lite": {"displayName": "Gemini 2.5 Flash Lite", "maxTokens": 1048576, "description": "Gemini 2.5 Flash Lite fast architecture", "category": "Google Gemini"},
    "gemini-2.5-flash-thinking": {"displayName": "Gemini 2.5 Flash Thinking", "maxTokens": 1048576, "description": "Gemini 2.5 with reasoning traces", "category": "Google Gemini"},
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
    "gemini-4-argon-web-high": {"displayName": "Gemini 4 Argon (Web) (High Reasoning)", "maxTokens": 1048576, "description": "Gemini 4 Argon Web flagship model with deep reasoning", "category": "Gemini Web"},
    "gemini-4-argon-web-medium": {"displayName": "Gemini 4 Argon (Web) (Medium Reasoning)", "maxTokens": 1048576, "description": "Gemini 4 Argon Web model with balanced reasoning", "category": "Gemini Web"},
    "gemini-4-argon-web-low": {"displayName": "Gemini 4 Argon (Web) (Low / Fast)", "maxTokens": 1048576, "description": "Gemini 4 Argon Web fast low-latency model", "category": "Gemini Web"},
    "gemini-4-argon-web-spark": {"displayName": "Gemini 4 Argon (Web) (Autonomous Spark Agent)", "maxTokens": 1048576, "description": "Gemini 4 Argon Web autonomous Spark agent mode", "category": "Gemini Web"},
    "gemini-3.8-flash-web-high": {"displayName": "Gemini 3.8 Flash (Web) (High Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.8 Flash Web model with deep reasoning", "category": "Gemini Web"},
    "gemini-3.8-flash-web-medium": {"displayName": "Gemini 3.8 Flash (Web) (Medium Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.8 Flash Web model with medium reasoning", "category": "Gemini Web"},
    "gemini-3.8-flash-web-low": {"displayName": "Gemini 3.8 Flash (Web) (Low / Fast)", "maxTokens": 1048576, "description": "Gemini 3.8 Flash Web fast low-latency model", "category": "Gemini Web"},
    "gemini-3.8-flash-web-spark": {"displayName": "Gemini 3.8 Flash (Web) (Autonomous Spark Agent)", "maxTokens": 1048576, "description": "Gemini 3.8 Flash Web autonomous Spark agent mode", "category": "Gemini Web"},
    "gemini-3.1-pro-web-high": {"displayName": "Gemini 3.1 Pro (Web) (High Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.1 Pro Web model with deep reasoning", "category": "Gemini Web"},
    "gemini-3.1-pro-web-medium": {"displayName": "Gemini 3.1 Pro (Web) (Medium Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.1 Pro Web model with medium reasoning", "category": "Gemini Web"},
    "gemini-3.1-pro-web-low": {"displayName": "Gemini 3.1 Pro (Web) (Low / Fast)", "maxTokens": 1048576, "description": "Gemini 3.1 Pro Web fast model", "category": "Gemini Web"},
    "gemini-3.5-flash-lite-web-high": {"displayName": "Gemini 3.5 Flash-Lite (Web) (High Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash-Lite Web model with deep reasoning", "category": "Gemini Web"},
    "gemini-3.5-flash-lite-web-medium": {"displayName": "Gemini 3.5 Flash-Lite (Web) (Medium Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash-Lite Web model with balanced reasoning", "category": "Gemini Web"},
    "gemini-3.5-flash-lite-web-low": {"displayName": "Gemini 3.5 Flash-Lite (Web) (Low / Ultra Fast)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash-Lite Web ultra-fast model", "category": "Gemini Web"},
}


def normalize_model_name(model_name: Any) -> str:
    """Normalizes any requested model name, placeholder, or enum to an Antigravity backend model identifier."""
    if model_name is None:
        return DEFAULT_MODEL

    import re
    raw = str(model_name).strip().lower()
    cleaned = raw
    if not cleaned:
        return DEFAULT_MODEL

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
        if "spark" in cleaned:
            if "3.8" in cleaned or ("flash" in cleaned and "lite" not in cleaned):
                return "gemini-3.8-flash-web-spark"
            return "gemini-4-argon-web-spark"
        if "task" in cleaned or "agent" in cleaned:
            if "3.8" in cleaned or ("flash" in cleaned and "lite" not in cleaned):
                return "gemini-3.8-flash-web-task"
            return "gemini-4-argon-web-task"
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
    is_claude_query = any(k in cleaned for k in ("claude", "sonnet", "opus"))

    # Version specific checks (Gemini CloudCode API only)
    if not is_claude_query:
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
            if "extra-low" in cleaned or "extra_low" in cleaned:
                return "gemini-3.5-flash-extra-low"
            return "gemini-3.5-flash-low"
        if "3.1-pro" in cleaned or "3_1-pro" in cleaned or "3-1-pro" in cleaned:
            # Standard API CloudCode has no 3.1 Pro: map to CloudCode native Pro architecture
            return "gemini-2.5-pro"
        if "2.5-pro" in cleaned or "2_5-pro" in cleaned:
            return "gemini-2.5-pro"

    # Claude 5.5 models
    if ("5.5" in cleaned or "5-5" in cleaned) and "gpt" not in cleaned:
        is_opus = "opus" in cleaned
        if "low" in cleaned:
            return "claude-opus-5-5-low" if is_opus else "claude-sonnet-5-5-low"
        if "med" in cleaned:
            return "claude-opus-5-5-medium" if is_opus else "claude-sonnet-5-5-medium"
        return "claude-opus-5-5-high" if is_opus else "claude-sonnet-5-5-high"

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
    "claude-sonnet-5-5-high",
    "claude-sonnet-5-5-medium",
    "claude-sonnet-5-5-low",
    "claude-opus-5-5-high",
    "claude-opus-5-5-medium",
    "claude-opus-5-5-low",
    "claude-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-5-5-sonnet",
    "claude-5-5-opus",
}

CLAUDE_5_5_TO_4_6_FALLBACK: dict[str, str] = {
    # Sonnet 5.5 variants -> claude-sonnet-4-6
    "claude-sonnet-5-5-high": "claude-sonnet-4-6",
    "claude-sonnet-5-5-medium": "claude-sonnet-4-6",
    "claude-sonnet-5-5-low": "claude-sonnet-4-6",
    "claude-sonnet-5-5": "claude-sonnet-4-6",
    "claude-5-5": "claude-sonnet-4-6",
    "claude-5-5-sonnet": "claude-sonnet-4-6",
    # Opus 5.5 variants -> claude-opus-4-6-thinking
    "claude-opus-5-5-high": "claude-opus-4-6-thinking",
    "claude-opus-5-5-medium": "claude-opus-4-6-thinking",
    "claude-opus-5-5-low": "claude-opus-4-6-thinking",
    "claude-opus-5-5": "claude-opus-4-6-thinking",
    "claude-5-5-opus": "claude-opus-4-6-thinking",
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


def is_claude_4_6_model(model_name: str | None) -> bool:
    """Checks whether the requested model is a Claude 4.6 variant (e.g. Sonnet 4.6, Opus 4.6)."""
    if not model_name:
        return False
    norm = normalize_model_name(model_name)
    return (norm.startswith("claude-") or "claude" in norm) and not is_claude_5_5_model(norm)


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
    "gemini-4-argon-web-high",
    "gemini-4-argon-web-medium",
    "gemini-4-argon-web-low",
    "gemini-4-argon-web-spark",
}


def is_argon_model(model_name: str | None) -> bool:
    """Checks whether the requested model is a Gemini 4 Argon variant."""
    if not model_name:
        return False
    norm = normalize_model_name(model_name)
    return "argon" in norm


def resolve_cloudcode_argon_model(model_name: str) -> str:
    """Maps bare 'gemini-4-argon' (or '-disabled') to 'gemini-4-argon-high' for upstream CloudCode requests."""
    if model_name in ("gemini-4-argon", "gemini-4-argon-disabled"):
        return "gemini-4-argon-high"
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
    stream_options: dict[str, Any] | None = None
    model_config = ConfigDict(extra="allow")


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "antigravity"
    display_name: str | None = None
    max_tokens: int | None = None
    remaining_quota: float | None = None
    reset_time: str | None = None


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
    output_config: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
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


