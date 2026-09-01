/**
 * =============================================================================
 * Antigravity Cloudflare Edge Proxy & Serverless Worker (agy-proxy-edge)
 * =============================================================================
 * - Global Edge Geo-Bypass for Google CloudCode, Gemini & Anthropic Antigravity APIs
 * - Transparent Streaming (SSE) pass-through with zero buffering
 * - Dual Mode:
 *   1. Gateway / Upstream Mode (for agy-proxy)
 *   2. Standalone Serverless Mode (routes /v1/chat/completions using ACCOUNTS_JSON secret)
 */

const UPSTREAM_TARGETS = {
  cloudcode: "https://daily-cloudcode-pa.googleapis.com",
  genai: "https://generativelanguage.googleapis.com",
  oauth: "https://oauth2.googleapis.com",
  userinfo: "https://www.googleapis.com/oauth2/v2",
};

const DEFAULT_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com";
const DEFAULT_CLIENT_SECRET = "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";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Expose-Headers": "*",
  "Access-Control-Max-Age": "86400",
  "Retry-After": "2",
};

// In-memory token cache across warm worker invocations
const tokenCache = new Map();

async function getAccessToken(account) {
  if (account.auth_method === "api_key" || account.api_key) {
    return { token: account.api_key || account.refresh_token, type: "api_key" };
  }

  const cacheKey = account.account_id || account.email || "primary";
  const cached = tokenCache.get(cacheKey);
  const now = Date.now();

  if (cached && cached.expires_at > now + 60000) {
    return { token: cached.token, type: "oauth", project_id: account.project_id };
  }

  const body = new URLSearchParams({
    client_id: account.client_id || DEFAULT_CLIENT_ID,
    client_secret: account.client_secret || DEFAULT_CLIENT_SECRET,
    refresh_token: account.refresh_token,
    grant_type: "refresh_token",
  });

  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Google OAuth refresh failed: ${err}`);
  }

  const data = await res.json();
  const expiresAt = now + (data.expires_in || 3600) * 1000;
  tokenCache.set(cacheKey, { token: data.access_token, expires_at: expiresAt });

  return { token: data.access_token, type: "oauth", project_id: account.project_id };
}

function parseAccounts(env) {
  if (!env || !env.ACCOUNTS_JSON) return [];
  try {
    const parsed = JSON.parse(env.ACCOUNTS_JSON);
    return Array.isArray(parsed) ? parsed : Object.values(parsed);
  } catch (e) {
    return [];
  }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Handle CORS Preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    // 2. Optional Edge API Key Protection
    if (env && env.PROXY_API_KEY) {
      const authHeader = request.headers.get("Authorization") || "";
      const xApiKey = request.headers.get("x-proxy-key") || request.headers.get("x-api-key") || "";
      const bearerToken = authHeader.startsWith("Bearer ") ? authHeader.slice(7).trim() : "";

      if (bearerToken !== env.PROXY_API_KEY && xApiKey !== env.PROXY_API_KEY) {
        return new Response(
          JSON.stringify({ error: "Unauthorized: Invalid or missing Proxy Key." }),
          { status: 401, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
        );
      }
    }

    // 3. Health & Info page on root
    if (url.pathname === "/" || url.pathname === "/health") {
      const accounts = parseAccounts(env);
      const info = {
        name: "Antigravity Cloudflare Edge Proxy",
        status: "active",
        mode: accounts.length > 0 ? "Serverless Standalone & Gateway" : "Upstream Gateway",
        geo_edge: request.cf?.country || "Cloudflare Edge",
        accounts_loaded: accounts.length,
        endpoints: {
          openai_chat: `${url.origin}/v1/chat/completions`,
          anthropic_messages: `${url.origin}/v1/messages`,
          models: `${url.origin}/v1/models`,
          gateway_cloudcode: `${url.origin}/cloudcode/v1internal:streamGenerateContent`,
          gateway_genai: `${url.origin}/genai/v1beta/models`,
        },
        version: "2.0.0",
      };
      return new Response(JSON.stringify(info, null, 2), {
        headers: { "Content-Type": "application/json", ...CORS_HEADERS },
      });
    }

    // 4. Standalone Mode: GET /v1/models
    if (url.pathname === "/v1/models" && request.method === "GET") {
      const modelList = [
        { id: "gemini-3.7-flash-high", object: "model", owned_by: "google" },
        { id: "gemini-3.1-pro-high", object: "model", owned_by: "google" },
        { id: "gemini-3.1-flash-lite", object: "model", owned_by: "google" },
        { id: "claude-sonnet-4-6", object: "model", owned_by: "anthropic" },
        { id: "claude-opus-4-6-thinking", object: "model", owned_by: "anthropic" },
        { id: "gpt-4o", object: "model", owned_by: "openai-alias" },
        { id: "claude-3-7-sonnet", object: "model", owned_by: "anthropic-alias" },
      ];
      return new Response(JSON.stringify({ object: "list", data: modelList }), {
        headers: { "Content-Type": "application/json", ...CORS_HEADERS },
      });
    }

    // 5. Standalone Mode: POST /v1/chat/completions (OpenAI compatible)
    if (url.pathname === "/v1/chat/completions" && request.method === "POST") {
      const accounts = parseAccounts(env);
      if (accounts.length === 0) {
        return new Response(
          JSON.stringify({
            error: "No ACCOUNTS_JSON secret configured. Run `npx wrangler secret put ACCOUNTS_JSON` or use this Worker as an upstream proxy.",
          }),
          { status: 500, headers: { "Content-Type": "application/json", ...CORS_HEADERS } }
        );
      }

      // Pick healthy account (round-robin / random)
      const acc = accounts[Math.floor(Math.random() * accounts.length)];
      const reqJson = await request.json();
      const model = reqJson.model || "gemini-3.7-flash-high";
      const isStream = Boolean(reqJson.stream);

      try {
        const auth = await getAccessToken(acc);

        if (auth.type === "api_key") {
          // Google AI Studio endpoint (auto-map to gemini-3.7-flash / 3.6-flash)
          let targetModel = "gemini-3.7-flash";
          if (model.includes("3.6")) targetModel = "gemini-3.6-flash";
          else if (model.includes("lite")) targetModel = "gemini-3.1-flash-lite";

          const targetUrl = `https://generativelanguage.googleapis.com/v1beta/models/${targetModel}:streamGenerateContent?alt=sse&key=${auth.token}`;
          const contents = (reqJson.messages || []).map((m) => ({
            role: m.role === "assistant" ? "model" : "user",
            parts: [{ text: typeof m.content === "string" ? m.content : JSON.stringify(m.content) }],
          }));

          const gRes = await fetch(targetUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ contents }),
          });

          return new Response(gRes.body, {
            status: gRes.status,
            headers: { "Content-Type": isStream ? "text/event-stream" : "application/json", ...CORS_HEADERS },
          });
        } else {
          // Google CloudCode / Antigravity endpoint
          const targetUrl = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse";
          const contents = (reqJson.messages || []).map((m) => ({
            role: m.role === "assistant" ? "model" : "user",
            parts: [{ text: typeof m.content === "string" ? m.content : JSON.stringify(m.content) }],
          }));

          const payload = {
            project: auth.project_id || "default",
            model: model,
            request: { contents },
          };

          const gRes = await fetch(targetUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${auth.token}`,
              "User-Agent": "antigravity/cli/1.1.23 (aidev_client; os_type=linux; arch=amd64; cl=974125021; auth_method=consumer)",
            },
            body: JSON.stringify(payload),
          });

          return new Response(gRes.body, {
            status: gRes.status,
            headers: { "Content-Type": isStream ? "text/event-stream" : "application/json", ...CORS_HEADERS },
          });
        }
      } catch (err) {
        return new Response(JSON.stringify({ error: { message: err.message, type: "serverless_error" } }), {
          status: 500,
          headers: { "Content-Type": "application/json", ...CORS_HEADERS },
        });
      }
    }

    // 6. Upstream Gateway Mode (Transparent Forwarder for agy-proxy)
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
    } else if (url.pathname.includes("v1internal:") || url.pathname.includes("loadCodeAssist")) {
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

    if (!reqHeaders.has("User-Agent")) {
      reqHeaders.set("User-Agent", "antigravity/cli/1.1.23 (aidev_client; os_type=linux; arch=amd64; cl=974125021; auth_method=consumer)");
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
