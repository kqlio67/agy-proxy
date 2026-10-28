/**
 * =============================================================================
 * Antigravity Cloudflare Edge Proxy & Serverless Worker (agy-proxy-edge)
 * =============================================================================
 * - Global Edge Geo-Bypass for Google CloudCode, Gemini & Anthropic Antigravity APIs
 * - Transparent Streaming (SSE) pass-through with zero buffering in Gateway Mode
 * - Full Dual Mode:
 *   1. Gateway / Upstream Mode (for agy-proxy)
 *   2. Standalone Serverless Mode (OpenAI / Anthropic / Responses APIs using ACCOUNTS_JSON)
 * - Multi-account failover loop on 429/401/5xx errors
 * - Role sanitization (user/model) preventing CloudCode 500 errors
 * - Full model catalog & normalization matching agy-proxy v1.5.0+
 */

const DEFAULT_USER_AGENT =
  "antigravity/hub/2.26.0 (aidev_client; os_type=linux; arch=amd64; cl=1005016104)";

const DEFAULT_MODEL = "gemini-3.8-flash-high";

const DEFAULT_CLIENT_ID =
  "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com";
const DEFAULT_CLIENT_SECRET = "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";

const UPSTREAM_TARGETS = {
  cloudcode: "https://daily-cloudcode-pa.googleapis.com",
  genai: "https://generativelanguage.googleapis.com",
  oauth: "https://oauth2.googleapis.com",
  userinfo: "https://www.googleapis.com/oauth2/v2",
};

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Expose-Headers": "*",
  "Access-Control-Max-Age": "86400",
  "Retry-After": "2",
};

// -----------------------------------------------------------------------------
// Model Definitions & Aliases (matches agy_proxy/models.py)
// -----------------------------------------------------------------------------

const VALID_CLOUDCODE_MODELS = new Set([
  // Gemini 4 Argon
  "gemini-4-argon",
  "gemini-4-argon-high",
  "gemini-4-argon-medium",
  "gemini-4-argon-low",
  // Gemini 3.8
  "gemini-3.8-flash-high",
  "gemini-3.8-flash-medium",
  "gemini-3.8-flash-low",
  "gemini-3.8-flash-tiered",
  "gemini-3.8-flash-deepagent",
  // Gemini 3.7
  "gemini-3.7-flash-high",
  "gemini-3.7-flash-medium",
  "gemini-3.7-flash-low",
  "gemini-3.7-flash-tiered",
  // Gemini 3.6
  "gemini-3.6-flash-high",
  "gemini-3.6-flash-medium",
  "gemini-3.6-flash-low",
  "gemini-3.6-flash-tiered",
  // Gemini 3.5 & earlier
  "gemini-3.5-flash-low",
  "gemini-3.5-flash-extra-low",
  "gemini-3.5-flash-lite",
  "gemini-3.5-flash-lite-high",
  "gemini-3.5-flash-lite-medium",
  "gemini-3.5-flash-lite-low",
  "gemini-pro-agent",
  "gemini-3.1-pro-high",
  "gemini-3.1-pro-medium",
  "gemini-3.1-pro-low",
  "gemini-3.1-flash-lite",
  "gemini-3.1-flash-image",
  "gemini-3-flash",
  "gemini-3-flash-agent",
  "gemini-2.5-pro",
  "gemini-2.5-flash",
  "gemini-2.5-flash-lite",
  "gemini-2.5-flash-thinking",
  // Gemini Web browser models
  "gemini-4-argon-web-high",
  "gemini-4-argon-web-medium",
  "gemini-4-argon-web-low",
  "gemini-4-argon-web-spark",
  "gemini-3.8-flash-web-high",
  "gemini-3.8-flash-web-medium",
  "gemini-3.8-flash-web-low",
  "gemini-3.8-flash-web-spark",
  "gemini-3.1-pro-web-high",
  "gemini-3.1-pro-web-medium",
  "gemini-3.1-pro-web-low",
  "gemini-3.5-flash-lite-web-high",
  "gemini-3.5-flash-lite-web-medium",
  "gemini-3.5-flash-lite-web-low",
  "chat_20706",
  "chat_23310",
  "tab_flash_lite_preview",
  "tab_jump_flash_lite_preview",
  // 3rd Party models
  "claude-sonnet-4-6",
  "claude-opus-4-6-thinking",
  "claude-sonnet-4-6-deepagent",
  "claude-opus-4-6-deepagent",
  "gpt-oss-120b-medium",
]);

const MODEL_ALIASES = {
  "gemini-4-argon": "gemini-4-argon",
  "gemini-4-argon-high": "gemini-4-argon-high",
  "gemini-4-argon-medium": "gemini-4-argon-medium",
  "gemini-4-argon-low": "gemini-4-argon-low",
  "gemini-4-argon-disabled": "gemini-4-argon",
  "gemini-3.8-flash": "gemini-3.8-flash-high",
  "gemini-3.8-flash-high": "gemini-3.8-flash-high",
  "gemini-3.8-flash-medium": "gemini-3.8-flash-medium",
  "gemini-3.8-flash-low": "gemini-3.8-flash-low",
  "gemini-3.8-flash-tiered": "gemini-3.8-flash-tiered",
  "gemini-3.8-flash-n": "gemini-3.8-flash-high",
  "gemini-3.8": "gemini-3.8-flash-high",
  "gemini-3-8": "gemini-3.8-flash-high",
  "gemini-3.8-flash-preview": "gemini-3.8-flash-high",
  "chat_20706": "chat_20706",
  "chat_23310": "chat_23310",
  "tab_flash_lite_preview": "tab_flash_lite_preview",
  "tab_jump_flash_lite_preview": "tab_jump_flash_lite_preview",
  "gemini-3.1-flash-image": "gemini-3.1-flash-image",
  "gemini-image": "gemini-3.1-flash-image",
  "image": "gemini-3.1-flash-image",
  "image-generation": "gemini-3.1-flash-image",
  "imagen": "gemini-3.1-flash-image",
  "imagen-3": "gemini-3.1-flash-image",
  "dall-e": "gemini-3.1-flash-image",
  "dall-e-3": "gemini-3.1-flash-image",
  "dall-e-2": "gemini-3.1-flash-image",
  "proactive-observer-v10": "models/proactive-observer-v10",
  "proactive-observer": "models/proactive-observer-v10",
  "whisper": "models/proactive-observer-v10",
  "whisper-1": "models/proactive-observer-v10",
  "whisper-large-v3": "models/proactive-observer-v10",
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
  "deepseek-r1": "gemini-3.8-flash-high",
  "deepseek-v3": "gemini-3.8-flash-high",
  "gemini-deepagent": "gemini-3.8-flash-deepagent",
  "gemini-3.8-flash-deepagent": "gemini-3.8-flash-deepagent",
  "claude-deepagent": "claude-sonnet-4-6-deepagent",
  "claude-sonnet-deepagent": "claude-sonnet-4-6-deepagent",
  "claude-sonnet-4-6-deepagent": "claude-sonnet-4-6-deepagent",
  "claude-opus-deepagent": "claude-opus-4-6-deepagent",
  "claude-opus-4-6-deepagent": "claude-opus-4-6-deepagent"
};

const PROXY_MODELS_LIST = [
  // Flagship Gemini 4 Argon
  { id: "gemini-4-argon", object: "model", owned_by: "google", description: "Google Gemini 4 Argon (Web Flagship)" },
  { id: "gemini-4-argon-high", object: "model", owned_by: "google", description: "Gemini 4 Argon High (Web Thinking)" },
  { id: "gemini-4-argon-medium", object: "model", owned_by: "google", description: "Gemini 4 Argon Medium (Web Thinking)" },
  { id: "gemini-4-argon-low", object: "model", owned_by: "google", description: "Gemini 4 Argon Low (Web)" },
  { id: "gemini-4-argon-web-spark", object: "model", owned_by: "google", description: "Gemini 4 Argon (Web) (Spark Agent)" },

  // Flagship Gemini 3.8
  { id: "gemini-3.8-flash-high", object: "model", owned_by: "google", description: "Google Gemini 3.8 Flash High Reasoning (Default)" },
  { id: "gemini-3.8-flash-medium", object: "model", owned_by: "google", description: "Gemini 3.8 Flash Medium Reasoning" },
  { id: "gemini-3.8-flash-low", object: "model", owned_by: "google", description: "Gemini 3.8 Flash Low Reasoning" },
  { id: "gemini-3.8-flash-web-spark", object: "model", owned_by: "google", description: "Gemini 3.8 Flash (Web) (Spark Agent)" },
  { id: "gemini-3.8-flash-tiered", object: "model", owned_by: "google", description: "Gemini 3.8 Flash Tiered" },
  { id: "gemini-3.7-flash-high", object: "model", owned_by: "google", description: "Gemini 3.7 Flash High Reasoning" },
  { id: "gemini-3.7-flash-medium", object: "model", owned_by: "google", description: "Gemini 3.7 Flash Medium Reasoning" },
  { id: "gemini-3.6-flash-tiered", object: "model", owned_by: "google", description: "Gemini 3.6 Flash Tiered" },
  { id: "gemini-3.5-flash-lite", object: "model", owned_by: "google", description: "Gemini 3.5 Flash Lite" },
  { id: "gemini-3.1-pro-high", object: "model", owned_by: "google", description: "Gemini 3.1 Pro High" },
  { id: "gemini-3.1-pro-low", object: "model", owned_by: "google", description: "Gemini 3.1 Pro Low" },
  { id: "gemini-3.1-flash-lite", object: "model", owned_by: "google", description: "Gemini 3.1 Flash Lite" },
  { id: "gemini-2.5-pro", object: "model", owned_by: "google", description: "Gemini 2.5 Pro" },
  { id: "gemini-2.5-flash", object: "model", owned_by: "google", description: "Gemini 2.5 Flash" },

  // Anthropic Claude
  { id: "claude-sonnet-4-6", object: "model", owned_by: "anthropic", description: "Claude Sonnet 4.6 (Extended Thinking)" },
  { id: "claude-opus-4-6-thinking", object: "model", owned_by: "anthropic", description: "Claude Opus 4.6 (Extended Thinking)" },
  { id: "claude-sonnet-4-6-deepagent", object: "model", owned_by: "anthropic", description: "Claude Sonnet 4.6 (DeepAgent)" },
  { id: "claude-opus-4-6-deepagent", object: "model", owned_by: "anthropic", description: "Claude Opus 4.6 (DeepAgent)" },
  { id: "claude-3-7-sonnet", object: "model", owned_by: "anthropic-alias", description: "Claude 3.7 Sonnet alias" },
  { id: "claude-3-5-sonnet", object: "model", owned_by: "anthropic-alias", description: "Claude 3.5 Sonnet alias" },
  { id: "claude-3-opus", object: "model", owned_by: "anthropic-alias", description: "Claude 3 Opus alias" },
  { id: "claude-3-5-haiku", object: "model", owned_by: "anthropic-alias", description: "Claude 3.5 Haiku alias" },

  // OpenAI Codex & Standards
  { id: "gpt-6-astra", object: "model", owned_by: "openai-alias", description: "GPT-6 Astra Flagship Codex model" },
  { id: "gpt-5.6-sol", object: "model", owned_by: "openai-alias", description: "GPT-5.6 Sol" },
  { id: "gpt-5.4", object: "model", owned_by: "openai-alias", description: "GPT-5.4" },
  { id: "gpt-4o", object: "model", owned_by: "openai-alias", description: "GPT-4o Omni alias" },
  { id: "gpt-4o-mini", object: "model", owned_by: "openai-alias", description: "GPT-4o Mini alias" },
  { id: "o1", object: "model", owned_by: "openai-alias", description: "OpenAI o1 alias" },
  { id: "o3-mini", object: "model", owned_by: "openai-alias", description: "OpenAI o3-mini alias" },

  // DeepSeek & Open Source
  { id: "deepseek-r1", object: "model", owned_by: "deepseek-alias", description: "DeepSeek R1 reasoning alias" },
  { id: "deepseek-v3", object: "model", owned_by: "deepseek-alias", description: "DeepSeek V3 alias" },
  { id: "gpt-oss-120b-medium", object: "model", owned_by: "google", description: "Google GPT-OSS 120B Medium" },
];

// Add anthropic/ and anthropic. prefixed models
const fullModelCards = [...PROXY_MODELS_LIST];
for (const m of PROXY_MODELS_LIST) {
  fullModelCards.push({
    id: `anthropic/${m.id}`,
    object: "model",
    owned_by: m.owned_by,
    description: m.description,
  });
  fullModelCards.push({
    id: `anthropic.${m.id}`,
    object: "model",
    owned_by: m.owned_by,
    description: m.description,
  });
}

function normalizeModelName(rawModel) {
  if (!rawModel || typeof rawModel !== "string") return DEFAULT_MODEL;

  let cleaned = rawModel.trim().toLowerCase();

  // Remove provider prefixes (anthropic/, openai/, google/, models/)
  cleaned = cleaned.replace(/^(anthropic|openai|google|models)[./]/, "").trim();

  // Remove context annotations like [1m], (1m context), [thinking]
  cleaned = cleaned.replace(/\[.*?\]/g, "").replace(/\(.*?\)/g, "").trim();

  if (VALID_CLOUDCODE_MODELS.has(cleaned)) {
    return cleaned;
  }

  if (MODEL_ALIASES[cleaned]) {
    return MODEL_ALIASES[cleaned];
  }

  // Version-specific keyword matches
  if (cleaned.includes("task") || cleaned.includes("spark")) {
    if (cleaned.includes("3.8") || (cleaned.includes("flash") && !cleaned.includes("lite"))) {
      return "gemini-3.8-flash-web-spark";
    }
    return "gemini-4-argon-web-spark";
  }

  if (cleaned.includes("argon")) {
    if (cleaned.includes("low")) return "gemini-4-argon-low";
    if (cleaned.includes("med")) return "gemini-4-argon-medium";
    return "gemini-4-argon";
  }
  if (cleaned.includes("3.8") || cleaned.includes("3-8")) {
    if (cleaned.includes("low")) return "gemini-3.8-flash-low";
    if (cleaned.includes("med")) return "gemini-3.8-flash-medium";
    return "gemini-3.8-flash-high";
  }
  if (cleaned.includes("3.7") || cleaned.includes("3-7")) {
    if (cleaned.includes("low")) return "gemini-3.7-flash-low";
    if (cleaned.includes("med")) return "gemini-3.7-flash-medium";
    return "gemini-3.7-flash-high";
  }
  if (cleaned.includes("3.6") || cleaned.includes("3-6")) {
    if (cleaned.includes("low")) return "gemini-3.6-flash-low";
    if (cleaned.includes("med")) return "gemini-3.6-flash-medium";
    return "gemini-3.6-flash-tiered";
  }
  if (cleaned.includes("3.5") || cleaned.includes("3-5")) {
    if (cleaned.includes("lite")) return "gemini-3.5-flash-lite";
    if (cleaned.includes("extra-low") || cleaned.includes("extra_low")) {
      return "gemini-3.5-flash-extra-low";
    }
    return "gemini-3.5-flash-low";
  }
  if (cleaned.includes("3.1-pro") || cleaned.includes("3-1-pro")) {
    return "gemini-3.1-pro-low";
  }
  if (cleaned.includes("2.5-pro") || cleaned.includes("2-5-pro")) {
    return "gemini-2.5-pro";
  }


  // Family-specific keyword matches
  if (cleaned.includes("opus")) return "claude-opus-4-6-thinking";
  if (cleaned.includes("sonnet")) return "claude-sonnet-4-6";
  if (
    cleaned.includes("haiku") ||
    cleaned.includes("flash-lite") ||
    cleaned.includes("lite") ||
    cleaned.includes("mini") ||
    cleaned.includes("small")
  ) {
    return "gemini-3.1-flash-lite";
  }
  if (cleaned.includes("flash")) return "gemini-3.8-flash-high";
  if (cleaned.includes("claude")) return "claude-sonnet-4-6";

  for (const [k, v] of Object.entries(MODEL_ALIASES)) {
    if (cleaned.startsWith(k)) return v;
  }

  return DEFAULT_MODEL;
}

// -----------------------------------------------------------------------------
// Account Management & Token Refresh
// -----------------------------------------------------------------------------

const tokenCache = new Map();
const rateLimitMap = new Map();

function isAccountRateLimited(acc, model) {
  const is3p = model.includes("claude") || model.includes("gpt-oss");
  const key = `${acc.account_id || acc.email || "acc"}:${is3p ? "3p" : "gemini"}`;
  const expiry = rateLimitMap.get(key);
  if (!expiry) return false;
  if (Date.now() > expiry) {
    rateLimitMap.delete(key);
    return false;
  }
  return true;
}

function isModelSupported(acc, model) {
  const norm = normalizeModelName(model);
  const isC55 = (norm.includes("5-5") || norm.includes("5.5")) && norm.includes("claude");
  const isC46 = norm.includes("claude") && !isC55;
  const avail = acc.available_models || [];
  const availList = Array.isArray(avail) ? avail : Object.keys(avail);
  const has55 = Boolean(acc.supports_claude_5_5) || availList.some(m => (m.includes("5-5") || m.includes("5.5")) && m.includes("claude"));

  if (isC55) {
    if (acc.supports_claude_5_5) return true;
    if (has55) {
      if (availList.length > 0) return availList.includes(norm) || availList.some(m => (m.includes("5-5") || m.includes("5.5")) && m.includes("claude"));
      return true;
    }
    return false;
  }
  if (isC46) {
    if (has55) return false;
    if (availList.length > 0) {
      return availList.includes(norm) || availList.some(m => m.includes("claude") && !m.includes("5-5") && !m.includes("5.5"));
    }
    return true;
  }
  const isArgon = norm.includes("argon");
  if (isArgon) {
    if (acc.auth_method === "gemini_web") return true;
    if (availList.length > 0 && availList.includes(norm)) {
      if (typeof acc.available_models === "object" && acc.available_models[norm] && acc.available_models[norm].disabled) {
        return false;
      }
      return true;
    }
    return false;
  }
  return true;
}

function parseResetDuration(text) {
  if (!text || typeof text !== "string") return null;
  const m = text.match(
    /resets?\s+(?:in|after)\s+(?:(?:(\d+)\s*d(?:ays?)?)?\s*(?:(\d+)\s*h(?:ours?|rs?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?\s*(?:(\d+)\s*s(?:ec(?:onds?)?)?)?|(\d+)\s*(?:seconds|sec|s)?)/i
  );
  if (m) {
    if (m[5]) return parseFloat(m[5]);
    const days = parseInt(m[1] || "0", 10);
    const hours = parseInt(m[2] || "0", 10);
    const minutes = parseInt(m[3] || "0", 10);
    const seconds = parseInt(m[4] || "0", 10);
    const total = days * 86400 + hours * 3600 + minutes * 60 + seconds;
    if (total > 0) return total;
  }
  const m2 = text.match(/retry\s+(?:after|in)\s+(\d+)\s*(?:seconds|sec|s)?/i);
  if (m2) return parseFloat(m2[1]);
  return null;
}

function markAccountRateLimited(acc, model, durationMs = 15000) {
  const is3p = model.includes("claude") || model.includes("gpt-oss");
  const key = `${acc.account_id || acc.email || "acc"}:${is3p ? "3p" : "gemini"}`;
  rateLimitMap.set(key, Date.now() + durationMs);
}

function parseAccounts(env) {
  if (!env || !env.ACCOUNTS_JSON) return [];
  try {
    const raw =
      typeof env.ACCOUNTS_JSON === "string"
        ? JSON.parse(env.ACCOUNTS_JSON)
        : env.ACCOUNTS_JSON;

    let list = [];
    if (Array.isArray(raw)) {
      list = raw;
    } else if (raw && typeof raw === "object") {
      if (Array.isArray(raw.accounts)) list.push(...raw.accounts);
      if (Array.isArray(raw.api_keys)) list.push(...raw.api_keys);
      if (list.length === 0) {
        for (const v of Object.values(raw)) {
          if (Array.isArray(v)) list.push(...v);
          else if (v && typeof v === "object") list.push(v);
        }
      }
    }

    return list.filter(
      (acc) =>
        acc &&
        typeof acc === "object" &&
        acc.enabled !== false &&
        (acc.refresh_token || acc.api_key || acc.access_token)
    );
  } catch (e) {
    return [];
  }
}

async function getAccessToken(account, forceRefresh = false) {
  if (account.auth_method === "api_key" || account.api_key) {
    return { token: account.api_key || account.refresh_token, type: "api_key" };
  }

  const cacheKey = account.account_id || account.email || "primary";
  const now = Date.now();

  if (!forceRefresh) {
    const cached = tokenCache.get(cacheKey);
    if (cached && cached.expires_at > now + 60000) {
      return { token: cached.token, type: "oauth", project_id: account.project_id };
    }

    if (account.access_token && account.expiry_timestamp) {
      const expMs =
        account.expiry_timestamp > 1e11
          ? account.expiry_timestamp
          : account.expiry_timestamp * 1000;
      if (expMs > now + 60000) {
        tokenCache.set(cacheKey, { token: account.access_token, expires_at: expMs });
        return {
          token: account.access_token,
          type: "oauth",
          project_id: account.project_id,
        };
      }
    }
  }

  if (!account.refresh_token) {
    if (account.access_token) {
      return {
        token: account.access_token,
        type: "oauth",
        project_id: account.project_id,
      };
    }
    throw new Error(
      `Account ${account.email || account.account_id} has no refresh_token or access_token`
    );
  }

  const body = new URLSearchParams({
    client_id: account.client_id || DEFAULT_CLIENT_ID,
    client_secret: account.client_secret || DEFAULT_CLIENT_SECRET,
    refresh_token: account.refresh_token,
    grant_type: "refresh_token",
  });

  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": DEFAULT_USER_AGENT,
    },
    body: body.toString(),
  });

  if (!res.ok) {
    const err = await res.text();
    tokenCache.delete(cacheKey);
    throw new Error(`Google OAuth refresh failed: ${err}`);
  }

  const data = await res.json();
  const expiresAt = now + (data.expires_in || 3600) * 1000;
  tokenCache.set(cacheKey, { token: data.access_token, expires_at: expiresAt });

  return { token: data.access_token, type: "oauth", project_id: account.project_id };
}

// -----------------------------------------------------------------------------
// Message Conversion & Role Sanitization
// -----------------------------------------------------------------------------

function cleanSystemPromptText(text) {
  if (!text || typeof text !== "string") return "";
  const lines = text.split("\n");
  const cleaned = lines.filter(
    (line) => !line.trim().toLowerCase().startsWith("x-anthropic-billing-header:")
  );
  return cleaned.join("\n").trim();
}

function convertMessagesToGemini(reqJson) {
  let systemText = "";

  // Anthropic format: system prompt
  if (typeof reqJson.system === "string") {
    systemText = cleanSystemPromptText(reqJson.system);
  } else if (Array.isArray(reqJson.system)) {
    systemText = cleanSystemPromptText(
      reqJson.system
        .map((s) => (typeof s === "string" ? s : s?.text || ""))
        .filter(Boolean)
        .join("\n\n")
    );
  } else if (typeof reqJson.instructions === "string") {
    // OpenAI Responses API format
    systemText = cleanSystemPromptText(reqJson.instructions);
  }

  let rawMsgs = reqJson.messages || [];
  if (!Array.isArray(rawMsgs) && reqJson.input) {
    if (typeof reqJson.input === "string") {
      rawMsgs = [{ role: "user", content: reqJson.input }];
    } else if (Array.isArray(reqJson.input)) {
      rawMsgs = reqJson.input;
    }
  }

  const contents = [];

  for (const m of rawMsgs) {
    if (!m) continue;
    const roleLower = String(m.role || "user").toLowerCase();

    // Extract system messages into systemInstruction
    if (roleLower === "system" || roleLower === "developer") {
      let sysContent = "";
      if (typeof m.content === "string") {
        sysContent = m.content;
      } else if (Array.isArray(m.content)) {
        sysContent = m.content
          .map((p) => (typeof p === "string" ? p : p?.text || ""))
          .filter(Boolean)
          .join("\n");
      }
      if (sysContent) {
        sysContent = cleanSystemPromptText(sysContent);
        if (sysContent) {
          systemText = systemText ? `${systemText}\n\n${sysContent}` : sysContent;
        }
      }
      continue;
    }

    // Role sanitization: CloudCode strictly requires 'model' or 'user' (lowercase)
    const geminiRole =
      roleLower === "assistant" || roleLower === "model" ? "model" : "user";
    const parts = [];

    // OpenAI tool calls on assistant message
    if (Array.isArray(m.tool_calls) && m.tool_calls.length > 0) {
      for (const tc of m.tool_calls) {
        const fnName = tc?.function?.name || tc?.name || "tool";
        let fnArgs = tc?.function?.arguments || tc?.arguments || {};
        if (typeof fnArgs === "string") {
          try { fnArgs = JSON.parse(fnArgs); } catch (e) { fnArgs = {}; }
        }
        parts.push({
          functionCall: {
            name: fnName,
            args: fnArgs,
            id: tc?.id || `call_${Math.random().toString(36).substring(2, 9)}`,
          },
          thoughtSignature: "context_engineering_is_the_way_to_go",
        });
      }
    }

    // OpenAI tool response message (role == "tool")
    if (roleLower === "tool") {
      let respContent = m.content;
      if (typeof respContent === "string") {
        try { respContent = JSON.parse(respContent); } catch (e) { respContent = { result: respContent }; }
      }
      if (!respContent || typeof respContent !== "object") {
        respContent = { result: String(respContent || "") };
      }
      parts.push({
        functionResponse: {
          name: m.name || "tool_call",
          id: m.tool_call_id || "",
          response: respContent,
        },
      });
    } else if (typeof m.content === "string") {
      const cleaned = m.content.toLowerCase().includes("x-anthropic-billing-header:")
        ? cleanSystemPromptText(m.content)
        : m.content;
      if (cleaned) parts.push({ text: cleaned });
    } else if (Array.isArray(m.content)) {
      for (const block of m.content) {
        if (!block) continue;
        if (typeof block === "string") {
          const cleaned = block.toLowerCase().includes("x-anthropic-billing-header:")
            ? cleanSystemPromptText(block)
            : block;
          if (cleaned) parts.push({ text: cleaned });
        } else if (block.type === "text" && block.text) {
          const cleaned = block.text.toLowerCase().includes("x-anthropic-billing-header:")
            ? cleanSystemPromptText(block.text)
            : block.text;
          if (cleaned) parts.push({ text: cleaned });
        } else if (block.type === "thinking" && block.thinking) {
          parts.push({ thought: true, text: block.thinking });
        } else if (block.type === "tool_use") {
          parts.push({
            functionCall: {
              name: block.name || "tool",
              args: block.input || {},
              id: block.id || `call_${Math.random().toString(36).substring(2, 9)}`,
            },
            thoughtSignature: "context_engineering_is_the_way_to_go",
          });
        } else if (block.type === "tool_result") {
          parts.push({
            functionResponse: {
              name: block.name || "tool",
              id: block.tool_use_id || "",
              response: { result: block.content },
            },
          });
        } else if (block.type === "image" && block.source) {
          parts.push({
            inlineData: {
              mimeType: block.source.media_type || "image/jpeg",
              data: block.source.data || "",
            },
          });
        } else if (block.type === "image_url" && block.image_url?.url) {
          const u = block.image_url.url;
          const match = u.match(/^data:([^;]+);base64,(.+)$/);
          if (match) {
            parts.push({ inlineData: { mimeType: match[1], data: match[2] } });
          } else {
            parts.push({ text: `[Image: ${u}]` });
          }
        } else if (block.text) {
          parts.push({ text: block.text });
        }
      }
    }

    if (parts.length === 0) {
      parts.push({ text: "" });
    }

    contents.push({ role: geminiRole, parts });
  }

  if (contents.length === 0) {
    contents.push({ role: "user", parts: [{ text: "" }] });
  }

  return { systemText, contents };
}

function sanitizeGeminiSchema(schema) {
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    return schema;
  }
  const forbidden = [
    "$schema", "$id", "$defs", "$ref", "definitions", "title",
    "additionalProperties", "exclusiveMinimum", "exclusiveMaximum", "const",
    "examples", "deprecated", "readOnly", "writeOnly", "contentMediaType",
    "contentEncoding", "uniqueItems", "multipleOf", "propertyNames",
    "prefixItems", "unevaluatedProperties", "unevaluatedItems", "minContains",
    "maxContains", "patternProperties",
  ];

  function normalizeNode(node) {
    if (!node || typeof node !== "object" || Array.isArray(node)) return node;
    const res = { ...node };

    if ("const" in res) {
      const cVal = res.const;
      delete res.const;
      if (!res.enum) res.enum = [cVal];
    }
    if (typeof res.exclusiveMinimum === "number" && !("minimum" in res)) {
      res.minimum = res.exclusiveMinimum;
    }
    if (typeof res.exclusiveMaximum === "number" && !("maximum" in res)) {
      res.maximum = res.exclusiveMaximum;
    }

    for (const f of forbidden) delete res[f];

    if (Array.isArray(res.type)) {
      const nonNull = res.type.filter((t) => t !== "null");
      res.type = nonNull.length > 0 ? String(nonNull[0]).toLowerCase() : "string";
      if (res.type.includes("null")) res.nullable = true;
    } else if (typeof res.type === "string") {
      res.type = res.type.toLowerCase();
    }

    if (res.properties && typeof res.properties === "object" && !Array.isArray(res.properties)) {
      const newProps = {};
      for (const [k, v] of Object.entries(res.properties)) {
        newProps[k] = normalizeNode(v);
      }
      res.properties = newProps;
      if (!res.type) res.type = "object";
    }

    if (res.items) {
      if (Array.isArray(res.items)) {
        res.items = res.items.map(normalizeNode);
      } else if (typeof res.items === "object") {
        res.items = normalizeNode(res.items);
      }
      if (!res.type) res.type = "array";
    }

    for (const comb of ["anyOf", "oneOf"]) {
      if (Array.isArray(res[comb])) {
        const sub = res[comb];
        delete res[comb];
        let hasNull = false;
        const valid = [];
        for (const opt of sub) {
          if (opt && typeof opt === "object") {
            const nOpt = normalizeNode(opt);
            if (nOpt.type === "null" || nOpt.nullable === true) hasNull = true;
            if (nOpt.type !== "null") valid.push(nOpt);
          }
        }
        if (hasNull) res.nullable = true;
        if (valid.length > 0) {
          const first = valid[0];
          for (const [k, v] of Object.entries(first)) {
            if (!(k in res)) res[k] = v;
          }
        }
      }
    }

    if (Array.isArray(res.allOf)) {
      const sub = res.allOf;
      delete res.allOf;
      for (const opt of sub) {
        if (opt && typeof opt === "object") {
          const nOpt = normalizeNode(opt);
          if (nOpt.properties && typeof nOpt.properties === "object") {
            res.properties = { ...(res.properties || {}), ...nOpt.properties };
          }
          if (Array.isArray(nOpt.required)) {
            res.required = Array.from(new Set([...(res.required || []), ...nOpt.required]));
          }
          if (nOpt.type && !res.type) res.type = nOpt.type;
          if (nOpt.description && !res.description) res.description = nOpt.description;
        }
      }
    }

    if (!res.type) {
      if (res.properties) res.type = "object";
      else if (res.items) res.type = "array";
      else if (res.enum) res.type = "string";
      else res.type = "string";
    }

    return res;
  }

  const sanitized = normalizeNode(schema);
  if (!sanitized.type) sanitized.type = "object";
  return sanitized;
}

function buildCloudCodePayload(reqJson, backendModel, auth) {
  const { systemText, contents } = convertMessagesToGemini(reqJson);
  const innerRequest = { contents };

  if (systemText) {
    innerRequest.systemInstruction = {
      role: "user",
      parts: [{ text: systemText }],
    };
  }

  let targetBackendModel = backendModel;
  if (backendModel === "gemini-3.8-flash-deepagent" || backendModel === "gemini-deepagent") {
    targetBackendModel = "gemini-3.8-flash-high";
  } else if (
    backendModel === "claude-sonnet-4-6-deepagent" ||
    backendModel === "claude-deepagent" ||
    backendModel === "claude-sonnet-deepagent"
  ) {
    targetBackendModel = "claude-sonnet-4-6";
  } else if (
    backendModel === "claude-opus-4-6-deepagent" ||
    backendModel === "claude-opus-deepagent"
  ) {
    targetBackendModel = "claude-opus-4-6-thinking";
  }

  const generationConfig = {};
  if (typeof reqJson.temperature === "number") {
    generationConfig.temperature = reqJson.temperature;
  }
  if (typeof reqJson.top_p === "number") {
    generationConfig.topP = reqJson.top_p;
  }

  const thinkingConfig = { includeThoughts: true, thinkingBudget: -1 };
  const isDeepAgent =
    backendModel.includes("deepagent") ||
    targetBackendModel.includes("deepagent");

  if (isDeepAgent) {
    thinkingConfig.includeThoughts = true;
    thinkingConfig.thinkingBudget = 64000;
    thinkingConfig.thinkingLevel = "HIGH";
  } else if (reqJson.thinking && typeof reqJson.thinking === "object") {
    if (typeof reqJson.thinking.budget_tokens === "number") {
      thinkingConfig.thinkingBudget = reqJson.thinking.budget_tokens;
    } else if (reqJson.thinking.type === "disabled") {
      thinkingConfig.includeThoughts = false;
      thinkingConfig.thinkingBudget = 0;
    }
  }

  let maxTokens = reqJson.max_tokens || reqJson.max_completion_tokens || 65536;
  if (
    thinkingConfig.thinkingBudget > 0 &&
    maxTokens <= thinkingConfig.thinkingBudget
  ) {
    maxTokens = Math.max(
      maxTokens + thinkingConfig.thinkingBudget,
      thinkingConfig.thinkingBudget + 1536
    );
  }
  generationConfig.maxOutputTokens = maxTokens;
  generationConfig.thinkingConfig = thinkingConfig;

  innerRequest.generationConfig = generationConfig;

  if (targetBackendModel === "claude-sonnet-4-6") {
    innerRequest.labels = {
      ...(innerRequest.labels || {}),
      model_enum: "MODEL_PLACEHOLDER_M35",
      used_claude: "true",
      used_non_gemini_model: "true",
    };
  } else if (targetBackendModel === "claude-opus-4-6-thinking") {
    innerRequest.labels = {
      ...(innerRequest.labels || {}),
      model_enum: "MODEL_PLACEHOLDER_M26",
      used_claude: "true",
      used_non_gemini_model: "true",
    };
  }

  if (Array.isArray(reqJson.tools) && reqJson.tools.length > 0) {
    const decls = [];
    for (const t of reqJson.tools) {
      if (!t || typeof t !== "object") continue;
      const custom = (t.custom && typeof t.custom === "object") ? t.custom : {};
      const fn = (t.function && typeof t.function === "object") ? t.function : {};
      const name = t.name || custom.name || fn.name;
      if (name) {
        const desc = t.description || custom.description || fn.description || "";
        const rawParams =
          t.input_schema ||
          custom.input_schema ||
          t.parameters ||
          custom.parameters ||
          fn.parameters ||
          fn.input_schema;
        decls.push({
          name: name,
          description: desc,
          parameters:
            rawParams && typeof rawParams === "object"
              ? sanitizeGeminiSchema(rawParams)
              : { type: "object", properties: {} },
        });
      }
    }
    if (decls.length > 0) {
      innerRequest.tools = [{ functionDeclarations: decls }];
    }
  }

  const isImageGen = backendModel.includes("image");
  return {
    project: auth.project_id || "default",
    requestId: `${isImageGen ? "image_gen" : "agent"}-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`,
    model: targetBackendModel,
    userAgent: "antigravity",
    requestType: isImageGen ? "image_gen" : "agent",
    request: innerRequest,
  };
}

// -----------------------------------------------------------------------------
// Stream Parsers and Chunk Creators
// -----------------------------------------------------------------------------

function extractCandidateDelta(chunkJson) {
  const cand = (chunkJson.response?.candidates || chunkJson.candidates || [])[0];
  if (!cand) return { text: "", thought: "", finishReason: null };

  let text = "";
  let thought = "";
  const parts = cand.content?.parts || [];
  for (const p of parts) {
    if (p.thought === true) {
      thought += p.text || "";
    } else if (p.text) {
      text += p.text;
    }
  }
  return {
    text,
    thought,
    finishReason: cand.finishReason || null,
  };
}

// -----------------------------------------------------------------------------
// Main Worker Fetch Handler
// -----------------------------------------------------------------------------

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. CORS Preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // 2. Health & Diagnostic Endpoints (Bypass PROXY_API_KEY check)
    const publicPaths = [
      "/",
      "/health",
      "/api/hello",
      "/hello",
      "/v1/hello",
      "/v1/api/hello",
      "/v1/oauth/hello",
      "/oauth/hello",
      "/api/claude_cli/bootstrap",
      "/api/claude_code_penguin_mode",
      "/api/web/domain_info",
      "/api/event_logging/v2/batch",
      "/otlp/v1/metrics",
      "/otlp/v1/traces",
      "/otlp/v1/logs",
      "/log",
      "/api/client/metrics",
    ];

    if (publicPaths.includes(url.pathname)) {
      if (
        url.pathname === "/api/hello" ||
        url.pathname === "/hello" ||
        url.pathname === "/v1/hello" ||
        url.pathname === "/v1/api/hello" ||
        url.pathname === "/v1/oauth/hello" ||
        url.pathname === "/oauth/hello"
      ) {
        return new Response(
          JSON.stringify({
            status: "ok",
            message: "hello",
            proxy: "agy-proxy-edge",
            version: "2.26.0",
          }),
          { status: 200, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
        );
      }

      if (url.pathname === "/" || url.pathname === "/health") {
        const accounts = parseAccounts(env);
        const info = {
          name: "Antigravity Cloudflare Edge Proxy",
          status: "active",
          mode:
            accounts.length > 0
              ? "Serverless Standalone & Gateway"
              : "Upstream Gateway",
          geo_edge: request.cf?.country || "Cloudflare Edge",
          accounts_loaded: accounts.length,
          default_model: DEFAULT_MODEL,
          endpoints: {
            openai_chat: `${url.origin}/v1/chat/completions`,
            anthropic_messages: `${url.origin}/v1/messages`,
            responses: `${url.origin}/v1/responses`,
            models: `${url.origin}/v1/models`,
            gateway_cloudcode: `${url.origin}/cloudcode/v1internal:streamGenerateContent`,
            gateway_genai: `${url.origin}/genai/v1beta/models`,
          },
          version: "2.26.0",
        };
        return new Response(JSON.stringify(info, null, 2), {
          headers: { "Content-Type": "application/json", ...CORS_HEADERS },
        });
      }

      if (url.pathname === "/api/claude_cli/bootstrap") {
        return new Response(
          JSON.stringify({
            status: "ok",
            features: { fast_mode: true, extended_context: true },
            model_configs: {},
          }),
          { status: 200, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
        );
      }

      if (url.pathname === "/api/claude_code_penguin_mode") {
        return new Response(
          JSON.stringify({ enabled: false, status: "disabled" }),
          { status: 200, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
        );
      }

      if (url.pathname === "/api/web/domain_info") {
        return new Response(
          JSON.stringify({ allowed: true, can_fetch: true }),
          { status: 200, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
        );
      }

      // Telemetry absorb endpoints
      return new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json", ...CORS_HEADERS },
      });
    }

    // 3. Optional Edge API Key Protection
    if (env && env.PROXY_API_KEY) {
      const authHeader = request.headers.get("Authorization") || "";
      const xApiKey =
        request.headers.get("x-proxy-key") ||
        request.headers.get("x-api-key") ||
        "";
      const bearerToken = authHeader.startsWith("Bearer ")
        ? authHeader.slice(7).trim()
        : "";

      if (bearerToken !== env.PROXY_API_KEY && xApiKey !== env.PROXY_API_KEY) {
        return new Response(
          JSON.stringify({ error: "Unauthorized: Invalid or missing Proxy Key." }),
          { status: 401, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
        );
      }
    }

    // 4. Standalone Mode: GET /v1/models & /models
    if (
      (url.pathname === "/v1/models" ||
        url.pathname === "/models" ||
        url.pathname.startsWith("/v1/models/")) &&
      request.method === "GET"
    ) {
      if (url.pathname.startsWith("/v1/models/")) {
        const reqModelId = url.pathname.replace(/^\/v1\/models\//, "");
        const normalized = normalizeModelName(reqModelId);
        const card =
          fullModelCards.find((m) => m.id === reqModelId) ||
          fullModelCards.find((m) => m.id === normalized) || {
            id: reqModelId,
            object: "model",
            owned_by: "antigravity",
          };
        return new Response(JSON.stringify(card), {
          headers: { "Content-Type": "application/json", ...CORS_HEADERS },
        });
      }

      return new Response(
        JSON.stringify({ object: "list", data: fullModelCards }),
        { headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
      );
    }

    // 4.5. Token Counting Route
    const isCountTokensRoute =
      url.pathname === "/v1/messages/count_tokens" ||
      url.pathname === "/messages/count_tokens";

    if (isCountTokensRoute && request.method === "POST") {
      let body = {};
      try {
        body = await request.json();
      } catch (e) {}
      const serialized = JSON.stringify(body);
      const estimatedTokens = Math.max(1, Math.floor(serialized.length / 3.7));
      return new Response(JSON.stringify({ input_tokens: estimatedTokens }), {
        status: 200,
        headers: { "Content-Type": "application/json", ...CORS_HEADERS },
      });
    }

    // 5. Standalone Serverless Mode: Chat Completions, Messages & Responses
    const isChatRoute =
      url.pathname === "/v1/chat/completions" ||
      url.pathname === "/chat/completions" ||
      url.pathname === "/v1/v1/chat/completions";
    const isMessagesRoute =
      url.pathname === "/v1/messages" ||
      url.pathname === "/messages" ||
      url.pathname === "/v1/v1/messages";
    const isResponsesRoute =
      url.pathname === "/v1/responses" ||
      url.pathname === "/responses" ||
      url.pathname === "/v1/v1/responses";

    if (
      (isChatRoute || isMessagesRoute || isResponsesRoute) &&
      request.method === "POST"
    ) {
      const accounts = parseAccounts(env);
      if (accounts.length === 0) {
        return new Response(
          JSON.stringify({
            error: {
              message:
                "No valid accounts found in ACCOUNTS_JSON secret. Upload your accounts using `npx wrangler secret put ACCOUNTS_JSON < ~/.config/agy-proxy/accounts.json` or route through this Worker as an upstream proxy.",
              type: "configuration_error",
            },
          }),
          { status: 500, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
        );
      }

      const reqJson = await request.json();
      const rawModel = reqJson.model || DEFAULT_MODEL;
      const backendModel = normalizeModelName(rawModel);
      const isStream = Boolean(reqJson.stream);

      // Filter candidate accounts supporting the requested model
      let candidates = accounts.filter(
        (a) => isModelSupported(a, backendModel) && !isAccountRateLimited(a, backendModel)
      );
      if (candidates.length === 0) {
        // Fallback: If 5.5 requested but no 5.5 accounts available, try 4.6 accounts
        if (backendModel.includes("5-5") || backendModel.includes("5.5")) {
          const fbModel = backendModel.includes("opus") ? "claude-opus-4-6-thinking" : "claude-sonnet-4-6";
          candidates = accounts.filter((a) => isModelSupported(a, fbModel) && !isAccountRateLimited(a, fbModel));
        } else if (backendModel.includes("claude")) {
          const upModel = backendModel.includes("opus") ? "claude-opus-5-5-high" : "claude-sonnet-5-5-high";
          candidates = accounts.filter((a) => isModelSupported(a, upModel) && !isAccountRateLimited(a, upModel));
        } else if (backendModel.includes("argon")) {
          const fbModel = (backendModel.includes("task") || backendModel.includes("spark"))
            ? "gemini-3.8-flash-task"
            : backendModel.includes("low")
            ? "gemini-3.8-flash-low"
            : backendModel.includes("med")
            ? "gemini-3.8-flash-medium"
            : "gemini-3.8-flash-high";
          candidates = accounts.filter((a) => isModelSupported(a, fbModel) && !isAccountRateLimited(a, fbModel));
        }
      }
      if (candidates.length === 0) {
        candidates = accounts.filter((a) => isModelSupported(a, backendModel));
      }
      if (candidates.length === 0) {
        candidates = [...accounts];
      }

      // Shuffle candidate pool for load balancing
      for (let i = candidates.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [candidates[i], candidates[j]] = [candidates[j], candidates[i]];
      }

      let lastError = null;

      for (let idx = 0; idx < candidates.length; idx++) {
        const acc = candidates[idx];
        const isLastCandidate = idx === candidates.length - 1;

        try {
          const auth = await getAccessToken(acc);

          if (auth.type === "api_key") {
            // Google AI Studio endpoint
            let targetModel = "gemini-2.5-flash";
            if (backendModel.includes("pro")) targetModel = "gemini-2.5-pro";
            else if (backendModel.includes("lite")) targetModel = "gemini-2.5-flash-lite";

            const targetUrl = `https://generativelanguage.googleapis.com/v1beta/models/${targetModel}:streamGenerateContent?alt=sse&key=${auth.token}`;
            const { systemText, contents } = convertMessagesToGemini(reqJson);
            const genAiBody = { contents };
            if (systemText) {
              genAiBody.systemInstruction = { parts: [{ text: systemText }] };
            }

            const gRes = await fetch(targetUrl, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "User-Agent": DEFAULT_USER_AGENT,
              },
              body: JSON.stringify(genAiBody),
            });

            if (gRes.status === 429 && !isLastCandidate) {
              const errBody = await gRes.text().catch(() => "");
              const durSec = parseResetDuration(errBody);
              markAccountRateLimited(acc, backendModel, durSec ? durSec * 1000 : 15000);
              continue;
            }
            if (gRes.status === 401 && !isLastCandidate) {
              tokenCache.delete(acc.account_id || acc.email || "primary");
              continue;
            }
            if (gRes.status >= 500 && gRes.status <= 504 && !isLastCandidate) {
              continue;
            }

            if (!gRes.ok) {
              const errBody = await gRes.text();
              if (!isLastCandidate) continue;
              return new Response(
                JSON.stringify({
                  error: {
                    message: `Google GenAI upstream error: ${errBody}`,
                    code: gRes.status,
                  },
                }),
                {
                  status: gRes.status,
                  headers: { "Content-Type": "application/json", ...CORS_HEADERS },
                }
              );
            }

            return handleStreamOrJsonResponse(
              gRes,
              isStream,
              isMessagesRoute,
              rawModel,
              backendModel
            );
          } else {
            // Google CloudCode endpoint
            const targetUrl =
              "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse";
            const payload = buildCloudCodePayload(reqJson, backendModel, auth);

            const gRes = await fetch(targetUrl, {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${auth.token}`,
                "User-Agent": DEFAULT_USER_AGENT,
              },
              body: JSON.stringify(payload),
            });

            if (gRes.status === 401 && !isLastCandidate) {
              tokenCache.delete(acc.account_id || acc.email || "primary");
              try {
                await getAccessToken(acc, true);
              } catch (_) {}
              continue;
            }

            if (gRes.status === 429 && !isLastCandidate) {
              const errBody = await gRes.text().catch(() => "");
              const durSec = parseResetDuration(errBody);
              markAccountRateLimited(acc, backendModel, durSec ? durSec * 1000 : 15000);
              continue;
            }

            if (gRes.status === 404 && !isLastCandidate) {
              continue;
            }

            if (gRes.status >= 500 && gRes.status <= 504 && !isLastCandidate) {
              continue;
            }

            if (!gRes.ok) {
              const errBody = await gRes.text();
              const isClaudeDep = errBody.includes("no longer available") || (errBody.includes("4.6") && errBody.includes("5.5"));
              if (isClaudeDep) {
                acc.supports_claude_5_5 = true;
                console.warn(`[Cloudflare] ⚠️ Account does not support Claude 4.6 (marked as Claude 5.5-only).`);
              }

              if ((gRes.status === 404 || gRes.status === 429) && isLastCandidate && (backendModel.includes("5-5") || backendModel.includes("5.5"))) {
                const fbModel = backendModel.includes("opus") ? "claude-opus-4-6-thinking" : "claude-sonnet-4-6";
                console.warn(`[Cloudflare] ⚠️ Model ${backendModel} returned ${gRes.status}. Falling back to ${fbModel}`);
                const altAccounts = accounts.filter((a) => (a.account_id !== acc.account_id) && isModelSupported(a, fbModel) && !isAccountRateLimited(a, fbModel));
                const targetAcc = altAccounts.length > 0 ? altAccounts[0] : acc;
                const fbAuth = altAccounts.length > 0 ? await getAccessToken(targetAcc) : auth;
                const fbPayload = buildCloudCodePayload(reqJson, fbModel, fbAuth);
                const fbRes = await fetch(targetUrl, {
                  method: "POST",
                  headers: {
                    "Content-Type": "application/json",
                    Authorization: `Bearer ${fbAuth.token}`,
                    "User-Agent": DEFAULT_USER_AGENT,
                  },
                  body: JSON.stringify(fbPayload),
                });
                if (fbRes.ok) {
                  return handleStreamOrJsonResponse(
                    fbRes,
                    isStream,
                    isMessagesRoute,
                    rawModel,
                    fbModel
                  );
                }
              }

              if ((gRes.status === 404 || gRes.status === 400 || gRes.status === 429) && isLastCandidate && backendModel.includes("argon")) {
                const fbModel = (backendModel.includes("task") || backendModel.includes("spark"))
                  ? "gemini-3.8-flash-task"
                  : backendModel.includes("low")
                  ? "gemini-3.8-flash-low"
                  : backendModel.includes("med")
                  ? "gemini-3.8-flash-medium"
                  : "gemini-3.8-flash-high";
                console.warn(`[Cloudflare] ⚠️ Model ${backendModel} returned ${gRes.status}. Falling back to ${fbModel}`);
                const altAccounts = accounts.filter((a) => (a.account_id !== acc.account_id) && isModelSupported(a, fbModel) && !isAccountRateLimited(a, fbModel));
                const targetAcc = altAccounts.length > 0 ? altAccounts[0] : acc;
                const fbAuth = altAccounts.length > 0 ? await getAccessToken(targetAcc) : auth;
                const fbPayload = buildCloudCodePayload(reqJson, fbModel, fbAuth);
                const fbRes = await fetch(targetUrl, {
                  method: "POST",
                  headers: {
                    "Content-Type": "application/json",
                    Authorization: `Bearer ${fbAuth.token}`,
                    "User-Agent": DEFAULT_USER_AGENT,
                  },
                  body: JSON.stringify(fbPayload),
                });
                if (fbRes.ok) {
                  return handleStreamOrJsonResponse(
                    fbRes,
                    isStream,
                    isMessagesRoute,
                    rawModel,
                    fbModel
                  );
                }
              }
              if (!isLastCandidate) continue;
              return new Response(
                JSON.stringify({
                  error: {
                    message: `CloudCode upstream error: ${errBody}`,
                    code: gRes.status,
                  },
                }),
                {
                  status: gRes.status,
                  headers: { "Content-Type": "application/json", ...CORS_HEADERS },
                }
              );
            }

            return handleStreamOrJsonResponse(
              gRes,
              isStream,
              isMessagesRoute,
              rawModel,
              backendModel
            );
          }
        } catch (err) {
          lastError = err;
          if (!isLastCandidate) continue;
        }
      }

      return new Response(
        JSON.stringify({
          error: {
            message:
              lastError?.message ||
              "All accounts in pool were exhausted or failed.",
            type: "failover_exhausted",
          },
        }),
        { status: 502, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
      );
    }

    // 6. Upstream Gateway Mode (Transparent Forwarder for local agy-proxy)
    let targetBase = UPSTREAM_TARGETS.cloudcode;
    let targetPath = url.pathname;

    if (url.pathname.startsWith("/cloudcode/")) {
      targetBase = UPSTREAM_TARGETS.cloudcode;
      targetPath = url.pathname.replace(/^\/cloudcode/, "");
    } else if (url.pathname.startsWith("/genai/")) {
      targetBase = UPSTREAM_TARGETS.genai;
      targetPath = url.pathname.replace(/^\/genai/, "");
    } else if (url.pathname.startsWith("/oauth/")) {
      targetBase = UPSTREAM_TARGETS.oauth;
      targetPath = url.pathname.replace(/^\/oauth/, "");
    } else if (url.pathname.startsWith("/userinfo/")) {
      targetBase = UPSTREAM_TARGETS.userinfo;
      targetPath = url.pathname.replace(/^\/userinfo/, "");
    } else if (
      url.pathname.includes("v1internal:") ||
      url.pathname.includes("v1internal/") ||
      url.pathname.includes("loadCodeAssist") ||
      url.pathname.includes("fetchAvailableModels")
    ) {
      targetBase = UPSTREAM_TARGETS.cloudcode;
    } else if (url.pathname.startsWith("/v1beta/")) {
      targetBase = UPSTREAM_TARGETS.genai;
    }

    const targetUrl = new URL(targetPath + url.search, targetBase);

    // Prepare Upstream Headers
    const reqHeaders = new Headers(request.headers);
    reqHeaders.delete("host");
    reqHeaders.delete("cf-connecting-ip");
    reqHeaders.delete("cf-ray");
    reqHeaders.delete("cf-visitor");
    reqHeaders.delete("x-forwarded-for");
    reqHeaders.delete("x-proxy-key");

    if (
      !reqHeaders.has("User-Agent") ||
      reqHeaders.get("User-Agent").includes("cloudflare") ||
      reqHeaders.get("User-Agent").includes("curl")
    ) {
      reqHeaders.set("User-Agent", DEFAULT_USER_AGENT);
    }

    try {
      const response = await fetch(targetUrl.toString(), {
        method: request.method,
        headers: reqHeaders,
        body: ["GET", "HEAD"].includes(request.method) ? null : request.body,
        redirect: "follow",
      });

      const resHeaders = new Headers(response.headers);
      for (const [k, v] of Object.entries(CORS_HEADERS)) {
        resHeaders.set(k, v);
      }

      if (targetPath.includes("listExperiments") && response.ok) {
        try {
          const expData = await response.json();
          if (expData && typeof expData === "object") {
            const flags = Array.isArray(expData.flags) ? expData.flags : [];
            let foundDeepAgent = false;
            for (const f of flags) {
              if (f.name === "enable-deepagent") {
                f.boolValue = true;
                foundDeepAgent = true;
                break;
              }
            }
            if (!foundDeepAgent) {
              flags.push({ name: "enable-deepagent", boolValue: true });
            }
            expData.flags = flags;
            return new Response(JSON.stringify(expData), {
              status: response.status,
              statusText: response.statusText,
              headers: resHeaders,
            });
          }
        } catch (_) {}
      }

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: resHeaders,
      });
    } catch (err) {
      return new Response(
        JSON.stringify({
          error: {
            message: `Cloudflare Edge upstream error: ${err.message}`,
            type: "gateway_error",
            code: 502,
          },
        }),
        { status: 502, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
      );
    }
  },
};

// -----------------------------------------------------------------------------
// Stream & Response Handlers
// -----------------------------------------------------------------------------

async function handleStreamOrJsonResponse(
  upstreamRes,
  isStream,
  isAnthropic,
  rawModel,
  backendModel
) {
  const reqId = isAnthropic
    ? `msg_${Date.now()}_${Math.random().toString(36).substring(2, 8)}`
    : `chatcmpl-${Date.now()}-${Math.random().toString(36).substring(2, 8)}`;
  const createdTime = Math.floor(Date.now() / 1000);

  if (!isStream) {
    // Non-streaming: Read all SSE lines, assemble full text and return JSON
    const textBuffer = await upstreamRes.text();
    let accumulatedText = "";
    let finishReason = "stop";

    const lines = textBuffer.split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data:")) continue;
      const jsonStr = trimmed.slice(5).trim();
      if (!jsonStr || jsonStr === "[DONE]") continue;

      try {
        const parsed = JSON.parse(jsonStr);
        const delta = extractCandidateDelta(parsed);
        if (delta.text) accumulatedText += delta.text;
        if (delta.finishReason) finishReason = delta.finishReason;
      } catch (_) {}
    }

    if (isAnthropic) {
      const respPayload = {
        id: reqId,
        type: "message",
        role: "assistant",
        content: [{ type: "text", text: accumulatedText }],
        model: rawModel,
        stop_reason: "end_turn",
        stop_sequence: null,
        usage: { input_tokens: 0, output_tokens: 0 },
      };
      return new Response(JSON.stringify(respPayload), {
        status: 200,
        headers: { "Content-Type": "application/json", ...CORS_HEADERS },
      });
    } else {
      const respPayload = {
        id: reqId,
        object: "chat.completion",
        created: createdTime,
        model: rawModel,
        choices: [
          {
            index: 0,
            message: { role: "assistant", content: accumulatedText },
            finish_reason: finishReason.toLowerCase() === "stop" ? "stop" : finishReason,
          },
        ],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
      };
      return new Response(JSON.stringify(respPayload), {
        status: 200,
        headers: { "Content-Type": "application/json", ...CORS_HEADERS },
      });
    }
  }

  // Streaming Mode: Transform SSE stream from CloudCode to OpenAI / Anthropic format
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();

  const { readable, writable } = new TransformStream({
    start(controller) {
      if (isAnthropic) {
        controller.enqueue(
          encoder.encode(
            `event: message_start\ndata: ${JSON.stringify({
              type: "message_start",
              message: {
                id: reqId,
                type: "message",
                role: "assistant",
                content: [],
                model: rawModel,
                stop_reason: null,
                stop_sequence: null,
                usage: { input_tokens: 0, output_tokens: 0 },
              },
            })}\n\n`
          )
        );
        controller.enqueue(
          encoder.encode(
            `event: content_block_start\ndata: ${JSON.stringify({
              type: "content_block_start",
              index: 0,
              content_block: { type: "text", text: "" },
            })}\n\n`
          )
        );
      }
    },
    transform(chunk, controller) {
      const text = typeof chunk === "string" ? chunk : decoder.decode(chunk);
      const lines = text.split("\n");

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const jsonStr = trimmed.slice(5).trim();
        if (!jsonStr || jsonStr === "[DONE]") continue;

        try {
          const parsed = JSON.parse(jsonStr);
          const delta = extractCandidateDelta(parsed);

          if (isAnthropic) {
            if (delta.text) {
              controller.enqueue(
                encoder.encode(
                  `event: content_block_delta\ndata: ${JSON.stringify({
                    type: "content_block_delta",
                    index: 0,
                    delta: { type: "text_delta", text: delta.text },
                  })}\n\n`
                )
              );
            }
          } else {
            if (delta.text || delta.thought) {
              const deltaObj = {};
              if (delta.text) deltaObj.content = delta.text;
              if (delta.thought) deltaObj.reasoning_content = delta.thought;

              controller.enqueue(
                encoder.encode(
                  `data: ${JSON.stringify({
                    id: reqId,
                    object: "chat.completion.chunk",
                    created: createdTime,
                    model: rawModel,
                    choices: [
                      {
                        index: 0,
                        delta: deltaObj,
                        finish_reason: null,
                      },
                    ],
                  })}\n\n`
                )
              );
            }
          }
        } catch (_) {}
      }
    },
    flush(controller) {
      if (isAnthropic) {
        controller.enqueue(
          encoder.encode(
            `event: content_block_stop\ndata: ${JSON.stringify({
              type: "content_block_stop",
              index: 0,
            })}\n\n`
          )
        );
        controller.enqueue(
          encoder.encode(
            `event: message_delta\ndata: ${JSON.stringify({
              type: "message_delta",
              delta: { stop_reason: "end_turn", stop_sequence: null },
              usage: { output_tokens: 0 },
            })}\n\n`
          )
        );
        controller.enqueue(
          encoder.encode(
            `event: message_stop\ndata: ${JSON.stringify({
              type: "message_stop",
            })}\n\n`
          )
        );
      } else {
        controller.enqueue(
          encoder.encode(
            `data: ${JSON.stringify({
              id: reqId,
              object: "chat.completion.chunk",
              created: createdTime,
              model: rawModel,
              choices: [
                {
                  index: 0,
                  delta: {},
                  finish_reason: "stop",
                },
              ],
            })}\n\n`
          )
        );
        controller.enqueue(encoder.encode("data: [DONE]\n\n"));
      }
    },
  });

  upstreamRes.body.pipeTo(writable).catch(() => {});

  return new Response(readable, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      ...CORS_HEADERS,
    },
  });
}
