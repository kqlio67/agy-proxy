"""
Shared prompt compaction, tool schema compression, and streaming tool-call
parsing utilities for web-session LLM providers (DeepSeek Web, ChatGPT Web,
Gemini Web).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

# Verbose Antigravity IDE prompt sections that confuse web-chat models and waste context
_IDE_BLOAT_TAG_NAMES = (
    "artifacts",
    "conversation_transcript",
    "slash_commands",
    "messaging",
    "mcp_servers",
    "subagents",
    "skills",
    "featured_plugins",
)

_IDE_BLOAT_RE = re.compile(
    r"<(?P<tag>" + "|".join(_IDE_BLOAT_TAG_NAMES) + r")\b[^>]*>[\s\S]*?</(?P=tag)>\s*",
    re.IGNORECASE,
)

# Tool-call stream detection patterns
_TOOL_START_RE = re.compile(
    r"<\s*(?:[｜|]+\s*DSML\s*[｜|]+\s*(?:(?:function_)?calls|invoke)\b|tool_call\b)[^>]*>"
)
_DSML_END_CALLS_RE = re.compile(r"</\s*[｜|]+\s*DSML\s*[｜|]+\s*(?:function_)?calls\s*>")
_DSML_END_INVOKE_RE = re.compile(r"</\s*[｜|]+\s*DSML\s*[｜|]+\s*invoke\s*>")
_TOOL_CALL_END_RE = re.compile(r"</\s*tool_call\s*>")
_DSML_INVOKE_RE = re.compile(
    r'<\s*[｜|]+\s*DSML\s*[｜|]+\s*invoke\s+name=["\']([^"\']+)["\'][^>]*>(.*?)(?:</\s*[｜|]+\s*DSML\s*[｜|]+\s*invoke\s*>|$)',
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r'<\s*[｜|]+\s*DSML\s*[｜|]+\s*parameter\s+name=["\']([^"\']+)["\'](?:\s+string=["\'](true|false)["\'])?[^>]*>(.*?)</\s*[｜|]+\s*DSML\s*[｜|]+\s*parameter\s*>',
    re.DOTALL,
)
_JSON_FENCE_RE = re.compile(r"```json\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)


def compact_web_system_prompt(text: str) -> str:
    """
    Trims heavy IDE-only boilerplate (MCP catalogs, plugin shelves, artifact
    formatting guides, slash command menus) from system prompts for web providers
    while preserving identity, guidelines, and <user_information> (OS/workspace).
    """
    if not text or not isinstance(text, str):
        return ""
    cleaned = _IDE_BLOAT_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _truncate_text(text: str, max_len: int) -> str:
    """Returns a concise single-line summary up to max_len characters."""
    if not text:
        return ""
    one_line = " ".join(text.strip().split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3].rstrip() + "..."


def _compact_schema_node(node: Any) -> Any:
    """Compacts a JSON schema object while preserving types, enums, and required fields."""
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    if "type" in node:
        out["type"] = node["type"]
    if "enum" in node and isinstance(node["enum"], list):
        out["enum"] = node["enum"]
    if "required" in node and isinstance(node["required"], list):
        out["required"] = node["required"]
    if "properties" in node and isinstance(node["properties"], dict):
        out["properties"] = {
            k: _compact_schema_node(v) for k, v in node["properties"].items()
        }
    if "items" in node and isinstance(node["items"], dict):
        out["items"] = _compact_schema_node(node["items"])
    if "description" in node and isinstance(node["description"], str) and "properties" not in out:
        desc = _truncate_text(node["description"], 80)
        if desc:
            out["description"] = desc
    return out


def build_tool_schema_map(tools: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Builds a mapping of tool_name -> parameter properties schema."""
    schema_map: dict[str, dict[str, Any]] = {}
    if not tools:
        return schema_map
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if t.get("type") == "function" else t
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        params = fn.get("parameters") or {}
        props = params.get("properties") if isinstance(params, dict) else {}
        schema_map[name] = props if isinstance(props, dict) else {}
    return schema_map


def format_tools_instruction(tools: list[dict[str, Any]], style: str = "dsml") -> str:
    """
    Formats OpenAI tool declarations concisely for web chat providers.
    style="dsml" uses DeepSeek's native DSML tags; style="tool_call" uses <tool_call> JSON.
    """
    if not tools:
        return ""

    if style == "dsml":
        lines = [
            "[AVAILABLE TOOLS]",
            "To invoke a tool, output ONLY a DSML block (no markdown code fences):",
            "<｜DSML｜calls>",
            '<｜DSML｜invoke name="TOOL_NAME">',
            '<｜DSML｜parameter name="PARAM_NAME" string="true">VALUE</｜DSML｜parameter>',
            "</｜DSML｜invoke>",
            "</｜DSML｜calls>",
            'Use string="false" for numbers, booleans, arrays, and objects. Use exact parameter names from the schemas below.',
            "",
        ]
    else:
        lines = [
            "[AVAILABLE TOOLS]",
            "To invoke a tool, output ONLY a <tool_call> block (no markdown code fences):",
            '<tool_call>{"name": "TOOL_NAME", "arguments": {"PARAM_NAME": "VALUE"}}</tool_call>',
            "Use exact parameter names and JSON types from the schemas below.",
            "",
        ]

    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if t.get("type") == "function" else t
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        name = fn["name"]
        desc = _truncate_text(fn.get("description") or "", 140)
        compact_params = _compact_schema_node(fn.get("parameters") or {})
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
        if compact_params:
            lines.append(f"  params: {json.dumps(compact_params, ensure_ascii=False, separators=(',', ':'))}")
    return "\n".join(lines)


def _coerce_param_value(raw_val: str, string_attr: str | None, prop_schema: dict[str, Any] | None) -> Any:
    """Coerces a DSML parameter string to its JSON type using schema or string attribute."""
    expected_type = (prop_schema or {}).get("type")
    if isinstance(expected_type, list) and expected_type:
        expected_type = next((t for t in expected_type if t != "null"), expected_type[0])

    trimmed = raw_val.strip()
    if expected_type == "string" or string_attr == "true":
        if expected_type in ("integer", "number", "boolean", "array", "object") and trimmed:
            pass
        else:
            return raw_val

    if expected_type == "integer":
        try:
            return int(trimmed)
        except ValueError:
            return raw_val
    if expected_type == "number":
        try:
            return float(trimmed)
        except ValueError:
            return raw_val
    if expected_type == "boolean":
        low = trimmed.lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
        return raw_val
    if expected_type in ("array", "object") or string_attr == "false":
        try:
            return json.loads(trimmed)
        except Exception:
            return raw_val

    if string_attr is None and trimmed and trimmed[0] in ("{", "["):
        try:
            return json.loads(trimmed)
        except Exception:
            pass
    return raw_val


def parse_tool_calls_from_block(
    block_text: str,
    schema_map: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Parses DSML (<｜DSML｜calls>...) or <tool_call> JSON blocks into OpenAI tool calls."""
    parsed: list[dict[str, Any]] = []
    schema_map = schema_map or {}

    if "<tool_call" in block_text:
        inner = re.sub(r"</?\s*tool_call\s*>", "", block_text).strip()
        try:
            obj = json.loads(inner)
            items = obj if isinstance(obj, list) else [obj]
            for item in items:
                if isinstance(item, dict) and item.get("name"):
                    args = item.get("arguments", {})
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    parsed.append({"name": str(item["name"]), "arguments": args})
            if parsed:
                return parsed
        except Exception:
            pass

    for inv_match in _DSML_INVOKE_RE.finditer(block_text):
        tool_name = inv_match.group(1).strip()
        inv_body = inv_match.group(2)
        tool_props = schema_map.get(tool_name, {})
        args_dict: dict[str, Any] = {}
        for p_match in _DSML_PARAM_RE.finditer(inv_body):
            p_name = p_match.group(1).strip()
            p_str_attr = p_match.group(2)
            p_val = p_match.group(3)
            prop_schema = tool_props.get(p_name) if isinstance(tool_props, dict) else None
            args_dict[p_name] = _coerce_param_value(p_val, p_str_attr, prop_schema)
        parsed.append({
            "name": tool_name,
            "arguments": json.dumps(args_dict, ensure_ascii=False),
        })
    return parsed


def format_web_prompt(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_style: str = "dsml",
) -> str:
    """
    Formats OpenAI messages and optional tools into a clean, compact prompt for
    web-session chat interfaces (DeepSeek Web, ChatGPT Web, etc.).
    """
    if not messages and not tools:
        return ""

    # Fast path for simple single user message with no tools
    if len(messages) == 1 and messages[0].get("role") == "user" and not tools:
        content = messages[0].get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")

    system_parts: list[str] = []
    conv_turns: list[str] = []

    for m in messages:
        role_raw = str(m.get("role", "user")).lower()
        content = m.get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        elif content is None:
            content = ""
        else:
            content = str(content)

        if role_raw == "system":
            compacted = compact_web_system_prompt(content)
            if compacted:
                system_parts.append(compacted)
        elif role_raw == "assistant" and m.get("tool_calls"):
            tc_blocks = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                t_name = fn.get("name", "")
                raw_args = fn.get("arguments", "{}")
                try:
                    args_obj = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except Exception:
                    args_obj = {}
                if tool_style == "dsml":
                    param_lines = []
                    if isinstance(args_obj, dict):
                        for k, v in args_obj.items():
                            is_str = "true" if isinstance(v, str) else "false"
                            val_s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                            param_lines.append(f'<｜DSML｜parameter name="{k}" string="{is_str}">{val_s}</｜DSML｜parameter>')
                    tc_blocks.append(f'<｜DSML｜invoke name="{t_name}">\n' + "\n".join(param_lines) + "\n</｜DSML｜invoke>")
                else:
                    tc_blocks.append(f"<tool_call>{json.dumps({'name': t_name, 'arguments': args_obj}, ensure_ascii=False)}</tool_call>")
            calls_text = (
                ("<｜DSML｜calls>\n" + "\n".join(tc_blocks) + "\n</｜DSML｜calls>")
                if tool_style == "dsml"
                else "\n".join(tc_blocks)
            )
            combined = f"{content}\n{calls_text}".strip() if content else calls_text
            conv_turns.append(f"Assistant: {combined}")
        elif role_raw == "tool":
            t_name = m.get("name") or m.get("tool_call_id") or "tool"
            conv_turns.append(f"Tool Result ({t_name}):\n{content}")
        elif role_raw == "assistant":
            conv_turns.append(f"Assistant: {content}")
        else:
            conv_turns.append(f"User: {content}")

    sections: list[str] = []
    if system_parts:
        sections.append("[System Context]\n" + "\n\n".join(system_parts))
    if tools:
        sections.append(format_tools_instruction(tools, style=tool_style))

    if len(conv_turns) == 1 and conv_turns[0].startswith("User: "):
        sections.append(conv_turns[0][6:])
    elif conv_turns:
        history = conv_turns[:-1]
        latest = conv_turns[-1]
        if history:
            sections.append("[Conversation History]\n" + "\n\n".join(history))
        if latest.startswith("User: "):
            sections.append(latest[6:])
        else:
            sections.append(latest)

    if tools:
        if tool_style == "dsml":
            sections.append(
                "[Reminder: Answer the user's request directly. If a tool is needed, emit <｜DSML｜calls>...</｜DSML｜calls> using exact schema parameter names.]"
            )
        else:
            sections.append(
                '[Reminder: Answer the user\'s request directly. If a tool is needed, emit <tool_call>{"name":"...","arguments":{...}}</tool_call> using exact schema parameter names.]'
            )

    return "\n\n".join(sections)


class WebToolCallStreamParser:
    """Buffers response text only as needed to cleanly extract DSML or <tool_call> tool invocations."""

    def __init__(self, req_id: str, model: str, schema_map: dict[str, dict[str, Any]]):
        self.req_id = req_id
        self.model = model
        self.schema_map = schema_map
        self.text_buf = ""
        self.tool_buf = ""
        self.in_tool_block = False
        self.tool_kind = ""
        self.tool_call_index = 0
        self.emitted_tool_calls = False

    def _make_chunk(self, delta: dict[str, Any], finish_reason: str | None = None) -> dict[str, Any]:
        return {
            "id": self.req_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": self.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    def _emit_parsed_tools(self, block: str) -> list[dict[str, Any]]:
        calls = parse_tool_calls_from_block(block, self.schema_map)
        if not calls:
            return [self._make_chunk({"content": block})]
        chunks: list[dict[str, Any]] = []
        for c in calls:
            idx = self.tool_call_index
            self.tool_call_index += 1
            self.emitted_tool_calls = True
            chunks.append(
                self._make_chunk({
                    "tool_calls": [{
                        "index": idx,
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }]
                })
            )
        return chunks

    def feed(self, delta_text: str) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        if self.in_tool_block:
            self.tool_buf += delta_text
        else:
            self.text_buf += delta_text

        while True:
            if not self.in_tool_block:
                m = _TOOL_START_RE.search(self.text_buf)
                if m:
                    prefix = self.text_buf[:m.start()]
                    if prefix:
                        chunks.append(self._make_chunk({"content": prefix}))
                    tag = m.group(0)
                    if "tool_call" in tag:
                        self.tool_kind = "tool_call"
                    elif "invoke" in tag:
                        self.tool_kind = "dsml_invoke"
                    else:
                        self.tool_kind = "dsml_calls"
                    self.in_tool_block = True
                    self.tool_buf = self.text_buf[m.start():]
                    self.text_buf = ""
                    continue

                last_lt = self.text_buf.rfind("<")
                if last_lt != -1 and (len(self.text_buf) - last_lt) <= 48:
                    prefix = self.text_buf[:last_lt]
                    if prefix:
                        chunks.append(self._make_chunk({"content": prefix}))
                    self.text_buf = self.text_buf[last_lt:]
                    break

                if self.text_buf:
                    chunks.append(self._make_chunk({"content": self.text_buf}))
                    self.text_buf = ""
                break

            end_re = (
                _TOOL_CALL_END_RE
                if self.tool_kind == "tool_call"
                else (_DSML_END_INVOKE_RE if self.tool_kind == "dsml_invoke" else _DSML_END_CALLS_RE)
            )
            em = end_re.search(self.tool_buf)
            if em:
                block = self.tool_buf[:em.end()]
                remainder = self.tool_buf[em.end():]
                chunks.extend(self._emit_parsed_tools(block))
                self.in_tool_block = False
                self.tool_kind = ""
                self.tool_buf = ""
                self.text_buf = remainder
                continue
            break

        return chunks

    def flush(self) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        if self.in_tool_block and self.tool_buf:
            chunks.extend(self._emit_parsed_tools(self.tool_buf))
            self.tool_buf = ""
            self.in_tool_block = False
        if self.text_buf:
            chunks.append(self._make_chunk({"content": self.text_buf}))
            self.text_buf = ""
        return chunks
