"""
Data models and schemas for OpenAI, Anthropic, and Gemini API requests and responses.
"""

import time
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------
# Standard Model Mappings and Aliases
# ---------------------------------------------------------

DEFAULT_MODEL = "gemini-3.8-flash-high"

MODEL_ALIASES: dict[str, str] = {
    # Gemini 3.8 Aliases (Latest default)
    "gemini-3.8-flash": "gemini-3.8-flash-high",
    "gemini-3.8-flash-high": "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low": "gemini-3.8-flash-low",
    "gemini-3.8-flash-tiered": "gemini-3.8-flash-tiered",
    "gemini-3.8": "gemini-3.8-flash-high",
    "gemini-3-8": "gemini-3.8-flash-high",
    "gemini-3.8-flash-preview": "gemini-3.8-flash-high",

    # Generic Gemini Aliases
    "flash": "gemini-3.8-flash-high",
    "flash-lite": "gemini-3.1-flash-lite",
    "pro": "gemini-3.1-pro-low",
    "gemini-flash": "gemini-3.8-flash-high",
    "gemini-flash-thinking": "gemini-3.8-flash-high",
    "gemini-pro": "gemini-3.1-pro-low",

    # Legacy & Specific Gemini Models
    "gemini-3.7-flash": "gemini-3.7-flash-high",
    "gemini-3.7-flash-high": "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium": "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low": "gemini-3.7-flash-low",
    "gemini-3.7-flash-tiered": "gemini-3.7-flash-tiered",
    "gemini-3.6-flash": "gemini-3.6-flash-tiered",
    "gemini-3.6-flash-high": "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium": "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low": "gemini-3.6-flash-low",
    "gemini-3.6-flash-tiered": "gemini-3.6-flash-tiered",
    "gemini-3.5-flash": "gemini-3.5-flash-low",
    "gemini-3.5-flash-low": "gemini-3.5-flash-low",
    "gemini-3.5-flash-extra-low": "gemini-3.5-flash-extra-low",
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3-5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.5-lite": "gemini-3.5-flash-lite",
    "gemini-3-flash": "gemini-3.8-flash-tiered",
    "gemini-3-flash-agent": "gemini-3.8-flash-tiered",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "gemini-3.1-flash-image": "gemini-3.1-flash-image",
    "gemini-3.1-pro": "gemini-3.1-pro-low",
    "gemini-3.1-pro-high": "gemini-3.1-pro-high",
    "gemini-3.1-pro-low": "gemini-3.1-pro-low",
    "gemini-pro-agent": "gemini-pro-agent",
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
    "gemini-2.5-flash-thinking": "gemini-2.5-flash-thinking",

    # Claude Aliases
    "claude": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
    "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
    "claude-opus-4-6": "claude-opus-4-6-thinking",
    "claude-opus-4.6": "claude-opus-4-6-thinking",
    "claude-3-7-sonnet": "claude-sonnet-4-6",
    "claude-3-7-sonnet-20250219": "claude-sonnet-4-6",
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-3-5-sonnet-latest": "claude-sonnet-4-6",
    "claude-3-opus": "claude-opus-4-6-thinking",
    "claude-3-opus-20240229": "claude-opus-4-6-thinking",
    "claude-3-5-haiku": "gemini-3.1-flash-lite",
    "claude-3-haiku": "gemini-3.1-flash-lite",

    # OpenAI Codex CLI Model Aliases
    "gpt-6-astra": "gemini-3.8-flash-high",
    "gpt-6": "gemini-3.8-flash-high",
    "gpt-5.6-sol": "gemini-3.8-flash-high",
    "gpt-5.6-terra": "gemini-3.8-flash-medium",
    "gpt-5.6-luna": "gemini-3.1-flash-lite",
    "gpt-5.5": "gemini-3.8-flash-high",
    "gpt-5.4": "gemini-3.8-flash-high",
    "gpt-5.4-mini": "gemini-3.1-flash-lite",
    "gpt-5.2": "gemini-3.7-flash-high",
    "gpt-daybreak-blue-latest": "gemini-3.8-flash-high",
    "gpt-daybreak-red-latest": "gemini-3.8-flash-high",
    "codex-auto-review": "gemini-3.1-flash-lite",

    # OpenAI Aliases -> mapped to highest reasoning default (Gemini 3.8 Flash High)
    "gpt-4o": "gemini-3.8-flash-high",
    "gpt-4o-mini": "gemini-3.1-flash-lite",
    "gpt-4-turbo": "gemini-3.8-flash-high",
    "gpt-4": "gemini-3.8-flash-high",
    "gpt-3.5-turbo": "gemini-3.1-flash-lite",
    "o1": "gemini-3.8-flash-high",
    "o1-mini": "gemini-3.8-flash-high",
    "o3-mini": "gemini-3.8-flash-high",
    "gpt-oss-120b": "gpt-oss-120b-medium",
    "gpt-oss-120b-medium": "gpt-oss-120b-medium",

    # DeepSeek / Open Source Aliases
    "deepseek-r1": "gemini-3.8-flash-high",
    "deepseek-v3": "gemini-3.8-flash-high",
}


VALID_CLOUDCODE_MODELS = {
    # Gemini 3.8
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "gemini-3.8-flash-tiered",
    # Gemini 3.7
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low",
    "gemini-3.7-flash-tiered",
    # Gemini 3.6
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low",
    "gemini-3.6-flash-tiered",
    # Gemini 3.5 & earlier
    "gemini-3.5-flash-low",
    "gemini-3.5-flash-extra-low",
    "gemini-3.5-flash-lite",
    "gemini-pro-agent",
    "gemini-3.1-pro-high",
    "gemini-3.1-pro-low",
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
    "gpt-oss-120b-medium",
}

EXACT_MODEL_METADATA: dict[str, dict[str, Any]] = {
    # Gemini 3.8
    "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash (High)", "maxTokens": 1048576},
    "gemini-3.8-flash-medium": {"displayName": "Gemini 3.8 Flash (Medium)", "maxTokens": 1048576},
    "gemini-3.8-flash-low": {"displayName": "Gemini 3.8 Flash (Low)", "maxTokens": 1048576},
    "gemini-3.8-flash-tiered": {"displayName": "Gemini 3.8 Flash (Tiered)", "maxTokens": 1048576},
    # Gemini 3.7
    "gemini-3.7-flash-high": {"displayName": "Gemini 3.7 Flash (High)", "maxTokens": 1048576},
    "gemini-3.7-flash-medium": {"displayName": "Gemini 3.7 Flash (Medium)", "maxTokens": 1048576},
    "gemini-3.7-flash-low": {"displayName": "Gemini 3.7 Flash (Low)", "maxTokens": 1048576},
    "gemini-3.7-flash-tiered": {"displayName": "Gemini 3.7 Flash (Tiered)", "maxTokens": 1048576},
    # Gemini 3.6
    "gemini-3.6-flash-high": {"displayName": "Gemini 3.6 Flash (High)", "maxTokens": 1048576},
    "gemini-3.6-flash-medium": {"displayName": "Gemini 3.6 Flash (Medium)", "maxTokens": 1048576},
    "gemini-3.6-flash-low": {"displayName": "Gemini 3.6 Flash (Low)", "maxTokens": 1048576},
    "gemini-3.6-flash-tiered": {"displayName": "Gemini 3.6 Flash (Tiered)", "maxTokens": 1048576},
    # Gemini 3.5 & earlier
    "gemini-3.5-flash-low": {"displayName": "Gemini 3.5 Flash (Low)", "maxTokens": 1048576},
    "gemini-3.5-flash-extra-low": {"displayName": "Gemini 3.5 Flash (Extra Low)", "maxTokens": 1048576},
    "gemini-3.5-flash-lite": {"displayName": "Gemini 3.5 Flash Lite", "maxTokens": 1048576},
    "gemini-pro-agent": {"displayName": "Gemini Pro Agent", "maxTokens": 1048576},
    "gemini-3.1-pro-high": {"displayName": "Gemini 3.1 Pro (High)", "maxTokens": 1048576},
    "gemini-3.1-pro-low": {"displayName": "Gemini 3.1 Pro (Low)", "maxTokens": 1048576},
    "gemini-3.1-flash-lite": {"displayName": "Gemini 3.1 Flash Lite", "maxTokens": 1048576},
    "gemini-3.1-flash-image": {"displayName": "Gemini 3.1 Flash Image", "maxTokens": 1048576},
    "gemini-3-flash": {"displayName": "Gemini 3 Flash", "maxTokens": 1048576},
    "gemini-3-flash-agent": {"displayName": "Gemini 3 Flash Agent", "maxTokens": 1048576},
    "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro", "maxTokens": 1048576},
    "gemini-2.5-flash": {"displayName": "Gemini 2.5 Flash", "maxTokens": 1048576},
    "gemini-2.5-flash-lite": {"displayName": "Gemini 2.5 Flash Lite", "maxTokens": 1048576},
    "gemini-2.5-flash-thinking": {"displayName": "Gemini 2.5 Flash (Thinking)", "maxTokens": 1048576},
    # 3rd Party models
    "claude-sonnet-4-6": {"displayName": "Claude Sonnet 4.6 (Thinking)", "maxTokens": 200000},
    "claude-opus-4-6-thinking": {"displayName": "Claude Opus 4.6 (Thinking)", "maxTokens": 200000},
    "gpt-oss-120b-medium": {"displayName": "GPT-OSS 120B (Medium)", "maxTokens": 131072},
    "gpt-oss-120b": {"displayName": "GPT-OSS 120B", "maxTokens": 131072},
    # OpenAI & Codex models available in agy-proxy
    "gpt-6-astra": {"displayName": "GPT-6 Astra (Flagship)", "maxTokens": 272000},
    "gpt-6": {"displayName": "GPT-6", "maxTokens": 272000},
    "gpt-5.6-sol": {"displayName": "GPT-5.6 Sol", "maxTokens": 272000},
    "gpt-5.6-terra": {"displayName": "GPT-5.6 Terra", "maxTokens": 272000},
    "gpt-5.6-luna": {"displayName": "GPT-5.6 Luna", "maxTokens": 272000},
    "gpt-5.5": {"displayName": "GPT-5.5", "maxTokens": 272000},
    "gpt-5.4": {"displayName": "GPT-5.4", "maxTokens": 272000},
    "gpt-5.2": {"displayName": "GPT-5.2", "maxTokens": 272000},
    "gpt-4o": {"displayName": "GPT-4o (Omni)", "maxTokens": 128000},
    "gpt-4o-mini": {"displayName": "GPT-4o Mini", "maxTokens": 128000},
    "gpt-4-turbo": {"displayName": "GPT-4 Turbo", "maxTokens": 128000},
    "o1": {"displayName": "OpenAI o1", "maxTokens": 200000},
    "o1-mini": {"displayName": "OpenAI o1 Mini", "maxTokens": 128000},
    "o3-mini": {"displayName": "OpenAI o3 Mini", "maxTokens": 200000},
    "deepseek-r1": {"displayName": "DeepSeek R1", "maxTokens": 128000},
    "deepseek-v3": {"displayName": "DeepSeek V3", "maxTokens": 128000},
    "claude-3-7-sonnet": {"displayName": "Claude 3.7 Sonnet", "maxTokens": 200000},
    "claude-3-5-sonnet": {"displayName": "Claude 3.5 Sonnet", "maxTokens": 200000},
    "claude-3-opus": {"displayName": "Claude 3 Opus", "maxTokens": 200000},
    "claude-3-5-haiku": {"displayName": "Claude 3.5 Haiku", "maxTokens": 200000},
    # Gemini Web browser models
    "gemini-3.5-flash-lite-extended": {"displayName": "Gemini 3.5 Flash-Lite Extended (Web Thinking)", "maxTokens": 1048576},
    "gemini-3.8-flash-extended": {"displayName": "Gemini 3.8 Flash Extended (Web Thinking)", "maxTokens": 1048576},
    "gemini-3.1-pro-extended": {"displayName": "Gemini 3.1 Pro Extended (Web Thinking)", "maxTokens": 1048576},
    "gemini-3-pro": {"displayName": "Gemini 3 Pro (Web)", "maxTokens": 1048576},
}

PROXY_CATALOG_MODELS: dict[str, dict[str, Any]] = {
    # OpenAI Codex Models
    "gpt-6-astra": {"displayName": "GPT-6-Astra", "maxTokens": 272000, "description": "OpenAI flagship Codex agent model with deep reasoning (Default)", "category": "OpenAI Codex"},
    "gpt-6": {"displayName": "GPT-6", "maxTokens": 272000, "description": "OpenAI GPT-6 advanced reasoning model", "category": "OpenAI Codex"},
    "gpt-5.6-sol": {"displayName": "GPT-5.6-Sol", "maxTokens": 272000, "description": "High-performance coding model", "category": "OpenAI Codex"},
    "gpt-5.6-terra": {"displayName": "GPT-5.6-Terra", "maxTokens": 272000, "description": "Balanced speed and depth model", "category": "OpenAI Codex"},
    "gpt-5.6-luna": {"displayName": "GPT-5.6-Luna", "maxTokens": 272000, "description": "Fast lightweight model", "category": "OpenAI Codex"},
    "gpt-5.5": {"displayName": "GPT-5.5", "maxTokens": 272000, "description": "Advanced coding agent model", "category": "OpenAI Codex"},
    "gpt-5.4": {"displayName": "GPT-5.4", "maxTokens": 272000, "description": "High reasoning coding model", "category": "OpenAI Codex"},
    "gpt-5.2": {"displayName": "GPT-5.2", "maxTokens": 272000, "description": "Stable previous generation model", "category": "OpenAI Codex"},

    # Standard OpenAI Models
    "gpt-4o": {"displayName": "GPT-4o (Omni)", "maxTokens": 128000, "description": "OpenAI GPT-4o multimodal flagship model", "category": "OpenAI"},
    "gpt-4o-mini": {"displayName": "GPT-4o Mini", "maxTokens": 128000, "description": "Fast, cost-efficient small model", "category": "OpenAI"},
    "gpt-4-turbo": {"displayName": "GPT-4 Turbo", "maxTokens": 128000, "description": "GPT-4 Turbo with high context window", "category": "OpenAI"},
    "o1": {"displayName": "OpenAI o1", "maxTokens": 200000, "description": "Deep reasoning model for math and coding", "category": "OpenAI"},
    "o1-mini": {"displayName": "OpenAI o1 Mini", "maxTokens": 128000, "description": "Fast reasoning model for coding", "category": "OpenAI"},
    "o3-mini": {"displayName": "OpenAI o3 Mini", "maxTokens": 200000, "description": "Latest reasoning model with high performance", "category": "OpenAI"},

    # Anthropic Claude Models
    "claude-sonnet-4-6": {"displayName": "Claude 3.7 Sonnet (Thinking)", "maxTokens": 200000, "description": "Anthropic flagship hybrid model with extended thinking", "category": "Anthropic"},
    "claude-opus-4-6-thinking": {"displayName": "Claude 3 Opus (Thinking)", "maxTokens": 200000, "description": "Anthropic high-capability reasoning model", "category": "Anthropic"},
    "claude-3-7-sonnet": {"displayName": "Claude 3.7 Sonnet", "maxTokens": 200000, "description": "Claude 3.7 Sonnet standard alias", "category": "Anthropic"},
    "claude-3-5-sonnet": {"displayName": "Claude 3.5 Sonnet", "maxTokens": 200000, "description": "Claude 3.5 Sonnet standard alias", "category": "Anthropic"},
    "claude-3-opus": {"displayName": "Claude 3 Opus", "maxTokens": 200000, "description": "Claude 3 Opus standard alias", "category": "Anthropic"},
    "claude-3-5-haiku": {"displayName": "Claude 3.5 Haiku", "maxTokens": 200000, "description": "Claude 3.5 Haiku fast lightweight alias", "category": "Anthropic"},

    # Google Gemini Models
    "gemini-3.8-flash-high": {"displayName": "Gemini 3.8 Flash (High Reasoning)", "maxTokens": 1048576, "description": "Google Flagship model with deep thinking & reasoning (Default)", "category": "Google Gemini"},
    "gemini-3.8-flash-medium": {"displayName": "Gemini 3.8 Flash (Medium Reasoning)", "maxTokens": 1048576, "description": "Balances speed and reasoning depth", "category": "Google Gemini"},
    "gemini-3.8-flash-low": {"displayName": "Gemini 3.8 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Fast responses with light reasoning", "category": "Google Gemini"},
    "gemini-3.8-flash-tiered": {"displayName": "Gemini 3.8 Flash (Tiered)", "maxTokens": 1048576, "description": "Dynamic tiered reasoning allocation", "category": "Google Gemini"},
    "gemini-3.7-flash-high": {"displayName": "Gemini 3.7 Flash (High Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.7 Flash with high reasoning depth", "category": "Google Gemini"},
    "gemini-3.7-flash-medium": {"displayName": "Gemini 3.7 Flash (Medium Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.7 Flash with medium reasoning", "category": "Google Gemini"},
    "gemini-3.7-flash-low": {"displayName": "Gemini 3.7 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.7 Flash fast reasoning", "category": "Google Gemini"},
    "gemini-3.7-flash-tiered": {"displayName": "Gemini 3.7 Flash (Tiered)", "maxTokens": 1048576, "description": "Gemini 3.7 Flash tiered reasoning", "category": "Google Gemini"},
    "gemini-3.6-flash-high": {"displayName": "Gemini 3.6 Flash (High Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.6 Flash with high reasoning", "category": "Google Gemini"},
    "gemini-3.6-flash-medium": {"displayName": "Gemini 3.6 Flash (Medium Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.6 Flash with medium reasoning", "category": "Google Gemini"},
    "gemini-3.6-flash-low": {"displayName": "Gemini 3.6 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.6 Flash with low reasoning", "category": "Google Gemini"},
    "gemini-3.6-flash-tiered": {"displayName": "Gemini 3.6 Flash", "maxTokens": 1048576, "description": "Gemini 3.6 Flash tiered", "category": "Google Gemini"},
    "gemini-3.5-flash-low": {"displayName": "Gemini 3.5 Flash (Low Reasoning)", "maxTokens": 1048576, "description": "Gemini 3.5 Flash low reasoning", "category": "Google Gemini"},
    "gemini-3.5-flash-lite": {"displayName": "Gemini 3.5 Flash Lite", "maxTokens": 1048576, "description": "Gemini 3.5 Flash Lite low-latency", "category": "Google Gemini"},
    "gemini-3.1-pro-high": {"displayName": "Gemini 3.1 Pro (High Reasoning)", "maxTokens": 1048576, "description": "Google Pro tier model with deep reasoning", "category": "Google Gemini"},
    "gemini-3.1-pro-low": {"displayName": "Gemini 3.1 Pro (Low Reasoning)", "maxTokens": 1048576, "description": "Google Pro tier model with standard reasoning", "category": "Google Gemini"},
    "gemini-3.1-flash-lite": {"displayName": "Gemini 3.1 Flash Lite (Ultra Fast)", "maxTokens": 1048576, "description": "Ultra fast low-latency model", "category": "Google Gemini"},
    "gemini-2.5-pro": {"displayName": "Gemini 2.5 Pro", "maxTokens": 1048576, "description": "Gemini 2.5 Pro stable architecture", "category": "Google Gemini"},
    "gemini-2.5-flash": {"displayName": "Gemini 2.5 Flash", "maxTokens": 1048576, "description": "Gemini 2.5 Flash stable", "category": "Google Gemini"},
    "gemini-2.5-flash-thinking": {"displayName": "Gemini 2.5 Flash Thinking", "maxTokens": 1048576, "description": "Gemini 2.5 with reasoning traces", "category": "Google Gemini"},
    "gemini-pro-agent": {"displayName": "Gemini Pro Agent", "maxTokens": 1048576, "description": "Gemini Pro autonomous agent model", "category": "Google Gemini"},

    # DeepSeek Models
    "deepseek-r1": {"displayName": "DeepSeek R1", "maxTokens": 128000, "description": "DeepSeek R1 open reasoning model", "category": "DeepSeek"},
    "deepseek-v3": {"displayName": "DeepSeek V3", "maxTokens": 128000, "description": "DeepSeek V3 high-throughput general purpose model", "category": "DeepSeek"},

    # Open Source Models
    "gpt-oss-120b-medium": {"displayName": "GPT-OSS 120B (Medium)", "maxTokens": 131072, "description": "Google CloudCode authentic open-source GPT model", "category": "Open Source"},
    "gpt-oss-120b": {"displayName": "GPT-OSS 120B", "maxTokens": 131072, "description": "Standard alias for GPT-OSS 120B", "category": "Open Source"},
}


def normalize_model_name(model_name: str | None) -> str:
    """Normalizes any requested model name to an Antigravity backend model identifier."""
    if not model_name:
        return DEFAULT_MODEL

    import re
    cleaned = model_name.strip().lower()

    # Remove provider prefixes (e.g. anthropic/gemini-3.7-flash-high or anthropic.gemini-3.7-flash-high)
    cleaned = re.sub(r"^(anthropic|openai|google|models)[./]", "", cleaned).strip()

    # Remove context annotations like [1m], (1m context), [thinking], etc.
    cleaned = re.sub(r"\[.*?\]", "", cleaned).strip()
    cleaned = re.sub(r"\(.*?\)", "", cleaned).strip()

    if cleaned in VALID_CLOUDCODE_MODELS:
        return cleaned

    if cleaned in MODEL_ALIASES:
        return MODEL_ALIASES[cleaned]

    # Keyword checks
    # Version specific checks
    if "3.8" in cleaned or "3-8" in cleaned:
        if "low" in cleaned:
            return "gemini-3.8-flash-low"
        if "med" in cleaned:
            return "gemini-3.8-flash-medium"
        return "gemini-3.8-flash-high"
    if "3.7" in cleaned or "3-7" in cleaned:
        if "low" in cleaned:
            return "gemini-3.7-flash-low"
        if "med" in cleaned:
            return "gemini-3.7-flash-medium"
        return "gemini-3.7-flash-high"
    if "3.6" in cleaned or "3-6" in cleaned:
        if "low" in cleaned:
            return "gemini-3.6-flash-low"
        if "med" in cleaned:
            return "gemini-3.6-flash-medium"
        return "gemini-3.6-flash-tiered"
    if "3.5" in cleaned or "3-5" in cleaned:
        if "lite" in cleaned:
            return "gemini-3.5-flash-lite"
        if "extra-low" in cleaned or "extra_low" in cleaned:
            return "gemini-3.5-flash-extra-low"
        return "gemini-3.5-flash-low"
    if "3.1-pro" in cleaned or "3_1-pro" in cleaned or "3-1-pro" in cleaned:
        return "gemini-3.1-pro-low"
    if "2.5-pro" in cleaned or "2_5-pro" in cleaned:
        return "gemini-2.5-pro"

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
    if "gemini" in m:
        return False
    if m == "anthropic":
        return True
    return any(k in m for k in ("claude", "sonnet", "opus", "haiku", "gpt-oss", "fable", "3p"))




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
    messages: list[OpenAIMessage | dict[str, Any]]
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


class AnthropicRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: list[AnthropicMessage | dict[str, Any]]
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
