"""
Data models and schemas for OpenAI, Anthropic, and Gemini API requests and responses.
"""

import time
import uuid
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


# ---------------------------------------------------------
# Standard Model Mappings and Aliases
# ---------------------------------------------------------

DEFAULT_MODEL = "gemini-3.7-flash-high"

MODEL_ALIASES: Dict[str, str] = {
    # Gemini Aliases
    "gemini-2.5-pro": "gemini-2.5-pro",
    "gemini-2.5-flash": "gemini-2.5-flash",
    "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
    "gemini-2.5-flash-thinking": "gemini-2.5-flash-thinking",
    "gemini-3-flash": "gemini-3-flash",
    "gemini-3-flash-agent": "gemini-3-flash-agent",
    "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
    "gemini-3.1-flash-image": "gemini-3.1-flash-image",
    "gemini-3.1-pro": "gemini-3.1-pro-high",
    "gemini-3.1-pro-high": "gemini-3.1-pro-high",
    "gemini-3.1-pro-low": "gemini-3.1-pro-low",
    "gemini-3.5-flash": "gemini-3.5-flash-low",
    "gemini-3.5-flash-low": "gemini-3.5-flash-low",
    "gemini-3.5-flash-extra-low": "gemini-3.5-flash-extra-low",
    "gemini-3.6-flash": "gemini-3.6-flash-high",
    "gemini-3.6-flash-high": "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium": "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low": "gemini-3.6-flash-low",
    "gemini-3.7-flash": "gemini-3.7-flash-high",
    "gemini-3.7-flash-high": "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium": "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low": "gemini-3.7-flash-low",
    "gemini-pro": "gemini-3.7-flash-high",
    "gemini-flash": "gemini-3.7-flash-high",
    "gemini-flash-thinking": "gemini-3.7-flash-high",

    # Claude Aliases
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "claude-opus-4-6-thinking": "claude-opus-4-6-thinking",
    "claude-3-7-sonnet": "claude-sonnet-4-6",
    "claude-3-7-sonnet-20250219": "claude-sonnet-4-6",
    "claude-3-5-sonnet": "claude-sonnet-4-6",
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-6",
    "claude-3-5-sonnet-latest": "claude-sonnet-4-6",
    "claude-3-opus": "claude-opus-4-6-thinking",
    "claude-3-opus-20240229": "claude-opus-4-6-thinking",
    "claude-3-5-haiku": "gemini-3.1-flash-lite",

    # OpenAI Aliases
    "gpt-4o": "gemini-3.7-flash-high",
    "gpt-4o-mini": "gemini-3.1-flash-lite",
    "gpt-4-turbo": "gemini-3.7-flash-high",
    "gpt-4": "gemini-3.7-flash-high",
    "gpt-3.5-turbo": "gemini-3.1-flash-lite",
    "o1": "gemini-3.7-flash-high",
    "o1-mini": "gemini-3.7-flash-high",
    "o3-mini": "gemini-3.7-flash-high",
    "gpt-oss-120b": "gpt-oss-120b-medium",
    "gpt-oss-120b-medium": "gpt-oss-120b-medium",

    # DeepSeek / Open Source Aliases
    "deepseek-r1": "gemini-3.7-flash-high",
    "deepseek-v3": "gemini-3.7-flash-high",
}


VALID_CLOUDCODE_MODELS = {
    "gemini-3.7-flash-high",
    "gemini-3.7-flash-medium",
    "gemini-3.7-flash-low",
    "gemini-3.7-flash-tiered",
    "gemini-3.6-flash-high",
    "gemini-3.6-flash-medium",
    "gemini-3.6-flash-low",
    "gemini-3.6-flash-tiered",
    "gemini-3.5-flash-low",
    "gemini-3.5-flash-extra-low",
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
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
}


def normalize_model_name(model_name: Optional[str]) -> str:
    """Normalizes any requested model name to an Antigravity backend model identifier."""
    if not model_name:
        return DEFAULT_MODEL

    import re
    cleaned = model_name.strip().lower()

    # Remove provider prefixes (e.g. anthropic/gemini-3.7-flash-high -> gemini-3.7-flash-high)
    cleaned = re.sub(r"^(anthropic|openai|google|models)/", "", cleaned).strip()

    # Remove context annotations like [1m], (1m context), [thinking], etc.
    cleaned = re.sub(r"\[.*?\]", "", cleaned).strip()
    cleaned = re.sub(r"\(.*?\)", "", cleaned).strip()

    if cleaned in VALID_CLOUDCODE_MODELS:
        return cleaned

    if cleaned in MODEL_ALIASES:
        return MODEL_ALIASES[cleaned]

    # Keyword checks
    if "opus" in cleaned:
        return "claude-opus-4-6-thinking"
    if "sonnet" in cleaned:
        return "claude-sonnet-4-6"
    if "haiku" in cleaned:
        return "gemini-3.1-flash-lite"
    if "3.7" in cleaned or "3-7" in cleaned:
        return "gemini-3.7-flash-high"
    if "2.5-pro" in cleaned or "2_5-pro" in cleaned:
        return "gemini-2.5-pro"
    if "flash" in cleaned:
        return "gemini-3.7-flash-high"

    # Prefix match
    for k, v in MODEL_ALIASES.items():
        if cleaned.startswith(k):
            return v

    # If it's already a valid model, keep it; otherwise fallback to DEFAULT_MODEL
    if model_name.strip() in VALID_CLOUDCODE_MODELS:
        return model_name.strip()

    return DEFAULT_MODEL


# ---------------------------------------------------------
# OpenAI Compatible Schemas
# ---------------------------------------------------------

class OpenAIMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[Dict[str, Any]]]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class OpenAIChatRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: List[Union[OpenAIMessage, Dict[str, Any]]]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    n: Optional[int] = 1
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    response_format: Optional[Dict[str, Any]] = None
    user: Optional[str] = None
    thinking: Optional[Dict[str, Any]] = None
    reasoning_effort: Optional[str] = None
    stream_options: Optional[Dict[str, Any]] = None

    class Config:
        extra = "allow"


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "antigravity"
    display_name: Optional[str] = None
    max_tokens: Optional[int] = None
    remaining_quota: Optional[float] = None
    reset_time: Optional[str] = None


class ModelListResponse(BaseModel):
    object: str = "list"
    data: List[ModelCard]


# ---------------------------------------------------------
# Anthropic Compatible Schemas
# ---------------------------------------------------------

class AnthropicContentBlock(BaseModel):
    type: str
    text: Optional[str] = None
    source: Optional[Dict[str, Any]] = None
    id: Optional[str] = None
    name: Optional[str] = None
    input: Optional[Dict[str, Any]] = None
    tool_use_id: Optional[str] = None
    content: Optional[Union[str, List[Dict[str, Any]]]] = None


class AnthropicMessage(BaseModel):
    role: str
    content: Union[str, List[Union[AnthropicContentBlock, Dict[str, Any]]]]


class AnthropicRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: List[Union[AnthropicMessage, Dict[str, Any]]]
    system: Optional[Union[str, List[Dict[str, Any]]]] = None
    max_tokens: Optional[int] = 4096
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stream: Optional[bool] = False
    thinking: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None

    class Config:
        extra = "allow"
