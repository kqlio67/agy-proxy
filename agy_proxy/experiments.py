"""
Antigravity Experimental Features & Flags Manager.

Manages experimental features and Google CloudCode / Antigravity internal flags.
Safely activates high-value capabilities (e.g. /deepagent, multi-agent teams,
swarms, Mixture of Agents, Model Context Protocol, interactive diff views,
local/remote RAG, sidecars, and skills) while strictly blocking harmful or
restrictive flags (e.g. Free Tier downgrades, disablers, telemetry blocks,
and history wiping).
"""

import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("agy_proxy.experiments")

# Configuration path for user overrides
EXPERIMENTS_CONFIG_FILE = Path.home() / ".config" / "agy-proxy" / "experiments.json"
DEFAULT_EXPERIMENTS_FILE = Path(__file__).parent / "default_experiments.json"

# =============================================================================
# 1. Toxic / Restrictive Flags Blocklist (Must NEVER be True)
# Turning any of these to True downgrades features, disables tools, or breaks the IDE
# =============================================================================
BLOCKLIST_FLAGS: set[str] = {
    # Free tier downgrade (forces quota limits and disables advanced features)
    "GeminiFreeTier__enable_free_tier",
    "GCAUpgradeToPaid__enable_upgrade_from_free_tier",
    "GCAUpgradeToPaid__enable_g1_upgrade_flow",

    # Tool and assessor disablers
    "disable-search-tools",
    "disable-command-assessor",
    "disable-system-message-merging",
    "disable-system-message-tag",
    "disable-teamwork-forced-flash-model",
    "disable-tool-hook-notices",

    # Destructive or broken flags
    "remove-conversation-history",
    "GcliAgentHistoryTruncation__enable_agent_history_truncation",
    "collect-x20-quotas-on-checkin",
    "show-trajectory-labeling-ad",
    "GCAFeedbackBlock__enable_feedback_block",
    "GcaTelemetryBlock__enable_telemetry_block",
    "GcaEventsPipeline__enable_events_pipeline_polling",
    "GcaCitationBlock__enable_citation_block",
    "enable-gemini-next-opt-out-switch",
    "enable-vscode-injection-removal",
    "ApigeeCloudCode__enable_mock_server",
    "DuetAiGeneration__codeGeneration_use_transform_api",
    "allow-pss-experimental-monitors",
    "allow-experimental-monitors",
    "shadow-call-aicode-mendel",
}

DISABLER_PATTERN = re.compile(r"^disable-(?!loop-detection)")

# =============================================================================
# 2. Curated High-Value Experimental Features
# Organized by category with human-readable descriptions
# =============================================================================

EXPERIMENT_CATEGORIES = {
    "agents": "Autonomous Agents, Teams & Swarm Coordination",
    "mcp": "Model Context Protocol (MCP) & Stdio Tools",
    "ui_editor": "UI, Diff View, Thought Steering & Editor UX",
    "rag_context": "RAG, Codebase Awareness & Large Context Windows",
    "tools_plugins": "Tools, Customizations, Skills & Sidecars",
    "sdlc_models": "SDLC Model Connections & Generation Enhancements",
}

CURATED_SAFE_FLAGS: dict[str, dict[str, Any]] = {
    # --- Autonomous Agents & Swarm Coordination ---
    "enable-deepagent": {
        "value": True,
        "category": "agents",
        "desc": "Enables DeepAgent autonomous subagent workflows (/deepagent)",
    },
    "enable-agent-team": {
        "value": True,
        "category": "agents",
        "desc": "Enables autonomous agent team collaboration",
    },
    "enable-swarm": {
        "value": True,
        "category": "agents",
        "desc": "Enables multi-agent swarm task execution",
    },
    "enable-native-swarm": {
        "value": True,
        "category": "agents",
        "desc": "Enables native swarm orchestration engine",
    },
    "enable-swarm-monitor": {
        "value": True,
        "category": "agents",
        "desc": "v2.23.0 Flag: Enables autonomous agent swarm progress, health, and task monitoring",
    },
    "enable-swarm-narrator": {
        "value": True,
        "category": "agents",
        "desc": "Enables textual narrator and step review for multi-agent swarm actions",
    },
    "enable-agent-plugins-spec": {
        "value": True,
        "category": "agents",
        "desc": "Enables agent plugin specifications and custom tools",
    },
    "enable-subagent-hub": {
        "value": True,
        "category": "agents",
        "desc": "Enables Subagent Hub UI and manager",
    },
    "enable-subagent-user-messaging": {
        "value": True,
        "category": "agents",
        "desc": "Allows subagents to communicate directly with user",
    },
    "enable-teamwork-subagent": {
        "value": True,
        "category": "agents",
        "desc": "Enables specialized teamwork subagents",
    },
    "enable-battle-mode": {
        "value": True,
        "category": "agents",
        "desc": "Enables battle mode for comparative multi-agent testing",
    },
    "enable-battle-contrib-variants": {
        "value": True,
        "category": "agents",
        "desc": "Enables battle mode contribution variants",
    },
    "enable-battle-mode-custom-agents": {
        "value": True,
        "category": "agents",
        "desc": "Allows custom agents in battle mode",
    },
    "enable-trajectory-debug-in-battle-mode": {
        "value": True,
        "category": "agents",
        "desc": "Trajectory debugging in battle mode",
    },
    "jetski-autonomous-mode": {
        "value": True,
        "category": "agents",
        "desc": "Enables autonomous mode in Jetski engine",
    },
    "jetski-unified-customizations-panel-enabled": {
        "value": True,
        "category": "agents",
        "desc": "Enables unified customizations panel",
    },
    "convenient-multi-agent-coordination": {
        "value": True,
        "category": "agents",
        "desc": "Enables streamlined multi-agent coordination protocol",
    },
    "Chat__enable_chat_moa": {
        "value": True,
        "category": "agents",
        "desc": "Enables Mixture of Agents (MoA) synthesis in chat",
    },
    "Chat__enable_chat_crescendo_agents": {
        "value": True,
        "category": "agents",
        "desc": "Enables crescendo multi-turn agent workflows",
    },
    "Chat__enable_agent_mode_slash_commands": {
        "value": True,
        "category": "agents",
        "desc": "Enables slash commands in agent mode",
    },
    "Chat__enable_agentic_chat_ij": {
        "value": True,
        "category": "agents",
        "desc": "Enables agentic chat in JetBrains & IDE extensions",
    },
    "enable-markdown-agents": {
        "value": True,
        "category": "agents",
        "desc": "Enables markdown-specified agent prompts and roles",
    },
    "enable-autonomous-mode-critic": {
        "value": True,
        "category": "agents",
        "desc": "Enables critic evaluation in autonomous mode",
    },
    "enable-concierge-agent": {
        "value": True,
        "category": "agents",
        "desc": "Enables concierge routing subagent",
    },
    "enable-browser-subagent-v2": {
        "value": True,
        "category": "agents",
        "desc": "Enables browser subagent v2 for web exploration",
    },
    "enable-browser-subagent-webview-script": {
        "value": True,
        "category": "agents",
        "desc": "Injects DOM inspection and automation scripts for browser subagents",
    },
    "enable-image-generator-subagent": {
        "value": True,
        "category": "agents",
        "desc": "Enables image generator subagent",
    },

    # --- Model Context Protocol (MCP) ---
    "Chat__enable_chat_agentic_mcp_chat": {
        "value": True,
        "category": "mcp",
        "desc": "Enables agentic Model Context Protocol (MCP) chat integration",
    },
    "Chat__enable_mcp_server": {
        "value": True,
        "category": "mcp",
        "desc": "Enables MCP server connectivity in Antigravity",
    },
    "Chat__enable_mcp_server_ij": {
        "value": True,
        "category": "mcp",
        "desc": "Enables MCP server integration in IDE extensions",
    },
    "DatacloudMcp__enable_stdio_mcp_proxy": {
        "value": True,
        "category": "mcp",
        "desc": "Enables stdio MCP server proxy",
    },
    "enable-mcp-apps": {
        "value": True,
        "category": "mcp",
        "desc": "Enables MCP application integration",
    },
    "enable-mcp-non-blocking-turn-load": {
        "value": True,
        "category": "mcp",
        "desc": "Enables non-blocking turn loading during MCP tool calls",
    },

    # --- UI, Diff Views & Editor UX ---
    "DuetAiGeneration__codeGeneration_enable_diff_view": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables rich inline diff view for generated code",
    },
    "DuetAiGeneration__codeGeneration_enable_pane_view": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables side-by-side pane view for code generation",
    },
    "enable-split-view": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables split editor view in chat/editor panels",
    },
    "enable-bottom-terminal-panel": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables docked bottom terminal panel in Antigravity Hub UI",
    },
    "enable-omni-mention-search": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables unified @ mention search for symbols, files, and tools",
    },
    "use-component-rewrite": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables modern UI component rewrite in Antigravity Hub",
    },
    "enable-file-diff-accumulator": {
        "value": True,
        "category": "ui_editor",
        "desc": "Accumulates multi-file diffs across agent turns",
    },
    "enable-thought-editing": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables inspecting and steering reasoning thought streams",
    },
    "enable-thought-steering-from-selection": {
        "value": True,
        "category": "ui_editor",
        "desc": "Steers model thoughts using current editor selection",
    },
    "enable-conversation-forking": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables conversation branching and forking",
    },
    "enable-conversation-groups": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables logical conversation groups",
    },
    "enable-conversation-search-v2": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables full conversation history search v2",
    },
    "enable-conversation-selection-quoting": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables quoting selected code directly into chat prompt",
    },
    "enable-file-explorer-tab": {
        "value": True,
        "category": "ui_editor",
        "desc": "Adds file explorer tab directly in agent workspace view",
    },
    "enable-file-viewer-keyboard-navigation": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables smooth keyboard navigation in file viewer",
    },
    "enable-notebook-rendering": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables native Jupyter notebook rendering in Antigravity",
    },
    "enable-pdf-viewer": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables built-in PDF viewer",
    },
    "enable-tasks": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables interactive task tracking panel",
    },
    "enable-compact-slash-command": {
        "value": True,
        "category": "ui_editor",
        "desc": "Compact slash command list formatting",
    },
    "enable-input-autocomplete": {
        "value": True,
        "category": "ui_editor",
        "desc": "Autocomplete in chat prompt input",
    },
    "enable-input-draft-persistence": {
        "value": True,
        "category": "ui_editor",
        "desc": "Persists prompt drafts across window and tab switches",
    },
    "DuetAiGeneration__codeGeneration_enable_quickpick_chat": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables quickpick model and action selection in chat",
    },
    "Chat__enable_code_customization_webview": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables webview panel for codebase customizations",
    },
    "Chat__enable_custom_prompts": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables reusable custom prompts library",
    },
    "Chat__delete_chat_request_button": {
        "value": True,
        "category": "ui_editor",
        "desc": "Allows deleting and editing previous user prompts",
    },
    "Chat__edit_chat_request_button": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables editing previous chat turns",
    },
    "Chat__stop_chat_request_button": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables clean stop button during generation",
    },
    "Chat__enable_chat_checkpoints": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables conversation restore checkpoints",
    },
    "Chat__enable_chat_streaming": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables realtime SSE token streaming",
    },
    "enable-run-command-semantic-rendering": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables semantic terminal execution rendering",
    },
    "markdown-table-v2": {
        "value": True,
        "category": "ui_editor",
        "desc": "Enables enhanced markdown table formatting v2",
    },

    # --- RAG, Codebase Awareness & Context Windows ---
    "Chat__enable_chat_folder_context_selection": {
        "value": True,
        "category": "rag_context",
        "desc": "Allows selecting entire folders as context in chat",
    },
    "Chat__enable_full_codebase_awareness_chat": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables comprehensive full-codebase context indexing",
    },
    "Chat__enable_local_codebase_awareness_chat_ij": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables local codebase awareness in IDEs",
    },
    "DuetAiLocalRag__enable_local_rag_completion_snippets": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables local RAG context snippets for completions",
    },
    "DuetAiLocalRag__enable_local_rag_completion_snippets_with_pruning": {
        "value": True,
        "category": "rag_context",
        "desc": "Local RAG snippet extraction with intelligent pruning",
    },
    "DuetAiLocalRag__enable_local_rag_completion_snippets_with_pruning_bm25_scoring": {
        "value": True,
        "category": "rag_context",
        "desc": "BM25 snippet scoring for local RAG completions",
    },
    "DuetAiLocalRag__enable_local_rag_completion_snippets_with_pruning_colocated_files": {
        "value": True,
        "category": "rag_context",
        "desc": "Includes colocated workspace files in RAG snippets",
    },
    "DuetAiLocalRag__include_currently_open_files": {
        "value": True,
        "category": "rag_context",
        "desc": "Automatically includes open editor tabs in RAG context",
    },
    "DuetAiLocalRag__include_doc_files": {
        "value": True,
        "category": "rag_context",
        "desc": "Includes documentation files (*.md, *.rst) in RAG context",
    },
    "DuetAiLocalRag__enable_wald_file_selection": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables Wald file selection heuristics for RAG",
    },
    "DuetAiRemoteRag__enable_remote_rag": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables remote codebase RAG queries",
    },
    "DuetAiRemoteRag__enable_remote_rag_chat": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables remote RAG retrieval within chat",
    },
    "DuetAiRemoteRag__enable_hyde_for_generation": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables Hypothetical Document Embeddings (HyDE) for generation",
    },
    "enable-ripgrep-path-search": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables ripgrep-powered ultra-fast path search",
    },
    "enable-code-search-palette": {
        "value": True,
        "category": "rag_context",
        "desc": "Enables code search command palette",
    },

    # --- Tools, Customizations, Skills & Sidecars ---
    "enable-hub-builtin-browser": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables built-in Chromium browser in Antigravity Hub",
    },
    "enable-iwa-builtin-browser": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables Isolated Web App (IWA) support for builtin browser",
    },
    "enable-remote-control-audio-transcription": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables audio transcription for remote control sessions",
    },
    "enable-programmatic-tools": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables programmatic tool execution",
    },
    "enable-run-workflow-tool": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables running custom automation workflows",
    },
    "enable-ask-question-tool": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables interactive ask_question modal tool",
    },
    "enable-ask-permission-tool": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables granular permission prompt tool",
    },
    "enable-wait-tool": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables asynchronous wait tool for long-running tasks",
    },
    "enable-persistent-terminals": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables persistent shell terminal sessions",
    },
    "enable-pty": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables PTY terminal emulation support",
    },
    "enable-plugins": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables Antigravity plugin system",
    },
    "enable-plugin-bundles": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables bundled plugin installation",
    },
    "enable-plugin-themes": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables custom UI plugin themes",
    },
    "enable-plugin-auth": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables authentication manager for plugins",
    },
    "enable-marketplace-search-tool": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables searching the extension & tool marketplace",
    },
    "enable-customization-skills": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables Antigravity custom skills framework (SKILL.md)",
    },
    "enable-sidecar-sdk": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables Sidecar SDK integration",
    },
    "enable-ui-sidecars": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables UI sidecars inside editor panels",
    },
    "enable-sidecars": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables background sidecar services",
    },
    "enable-developer-mode": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables Antigravity developer mode & diagnostics",
    },
    "enable-directory-local-discovery": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables directory-local discovery for skills and rules",
    },
    "enable-trustedcli-config-files": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables trusted CLI configuration files",
    },
    "enable-generative-hooks": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables generative hook listeners",
    },

    # --- SDLC Model Connections & Completions ---
    "SDLCAgents__enable_anthropic_model_connection": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables direct Anthropic Claude backend connection",
    },
    "SDLCAgents__enable_azure_model_connection": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables Azure OpenAI model connection",
    },
    "SDLCAgents__enable_gemini_model_connection": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables Google Gemini model connection",
    },
    "SDLCAgents__enable_rest_model_connection": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables standard REST LLM model endpoints",
    },
    "DuetAICodeTransform__enable_m2": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables M2 code transformation pipeline",
    },
    "DuetAICodeTransformIj__enable_ij": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables JetBrains IDE code transformation support",
    },
    "CliComplexityBasedRouting__enabled": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables complexity-based automatic model routing",
    },
    "DuetAiCompletion__codeCompletion_enablePrefetchNextSuggestions": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Prefetches next code completion suggestions in background",
    },
    "DuetAiCompletion__codeCompletion_enableAdaptingCache": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables adaptive completion cache",
    },
    "DuetAiCompletion__codeCompletion_enableInfixCache": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables infix completion cache",
    },
    "enable-model-capacity-exhausted-retries": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Automatically retries alternative models on capacity exhaustion",
    },
    "disable-loop-detection": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Prevents false-positive agent loop interrupts on long workflows",
    },
    "DuetAiLocalRag__enable_local_rag_completion": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DuetAiLocalRag__enable_local_rag_completion",
    },
    "Chat__enable_chat_gemini_cli": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: Chat__enable_chat_gemini_cli",
    },
    "DatacloudKnowledgeCatalog__enable_relationship_graph_details_panel": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudKnowledgeCatalog__enable_relationship_graph_details_panel",
    },
    "GeminiFreeTier__call_onboard_user_from_legacy_flow": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiFreeTier__call_onboard_user_from_legacy_flow",
    },
    "UserTelemetry__enable_user_telemetry_call": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: UserTelemetry__enable_user_telemetry_call",
    },
    "DuetAiCloudCodeAPI__enable_cloudcode_api": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DuetAiCloudCodeAPI__enable_cloudcode_api",
    },
    "IntentAware__enable_intent_aware_m1": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: IntentAware__enable_intent_aware_m1",
    },
    "MetricService__enable_metric_service": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: MetricService__enable_metric_service",
    },
    "DatacloudSpark__enable_jupyter_token_broker": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudSpark__enable_jupyter_token_broker",
    },
    "GcaTelemetry__enable_ai_characters_percentage": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GcaTelemetry__enable_ai_characters_percentage",
    },
    "DatacloudKnowledgeCatalog__enable_data_products": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudKnowledgeCatalog__enable_data_products",
    },
    "DatacloudKnowledgeCatalog__enable_post_next_improvements": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudKnowledgeCatalog__enable_post_next_improvements",
    },
    "DatacloudDataplex__enable_bigtable_adapter": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudDataplex__enable_bigtable_adapter",
    },
    "DuetAiLocalRag__enable_local_rag_chat": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DuetAiLocalRag__enable_local_rag_chat",
    },
    "DuetAiProcessors__enable_prompt_recitations_check": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DuetAiProcessors__enable_prompt_recitations_check",
    },
    "GcaUx__enable_gm3_design_system": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GcaUx__enable_gm3_design_system",
    },
    "ApigeeGeminiChatTool__enable_apigee_gemini_chat_tool": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: ApigeeGeminiChatTool__enable_apigee_gemini_chat_tool",
    },
    "DuetAiLocalRag__enable_local_rag": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DuetAiLocalRag__enable_local_rag",
    },
    "DatacloudSpark__enable_load_in_spark_dataframe_from_gcs": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudSpark__enable_load_in_spark_dataframe_from_gcs",
    },
    "GcaTransformOutlines__enable_outlines": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GcaTransformOutlines__enable_outlines",
    },
    "Chat__enable_sessions": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: Chat__enable_sessions",
    },
    "Chat__enable_chat_rag_remote_repositories_context_selection": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: Chat__enable_chat_rag_remote_repositories_context_selection",
    },
    "DatacloudDatabases__enable_query_settings_improvements": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudDatabases__enable_query_settings_improvements",
    },
    "Chat__code_customization_enable_learn_more_message": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: Chat__code_customization_enable_learn_more_message",
    },
    "SparkMonitorIntegration__enable_spark_monitor_integration": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: SparkMonitorIntegration__enable_spark_monitor_integration",
    },
    "DatacloudDatabases__enable_cloud_sql_built_in_user": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudDatabases__enable_cloud_sql_built_in_user",
    },
    "DatacloudDatabases__enable_data_preview": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudDatabases__enable_data_preview",
    },
    "DuetAICodeTransform__enable_inline_diff": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DuetAICodeTransform__enable_inline_diff",
    },
    "Chat__enable_text_snippets": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: Chat__enable_text_snippets",
    },
    "DatacloudBigqueryGraph__enable_bigquery_graph": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudBigqueryGraph__enable_bigquery_graph",
    },
    "Chat__enable_workspace_change_in_chat_history": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: Chat__enable_workspace_change_in_chat_history",
    },
    "DatacloudFeedback__enable_listnr_survey": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudFeedback__enable_listnr_survey",
    },
    "DatacloudSpark__enable_spark_in_sql_editor": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: DatacloudSpark__enable_spark_in_sql_editor",
    },
    "Chat__enable_gemini3_announcement": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: Chat__enable_gemini3_announcement",
    },
    "enable-customization-kind-workflow": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-customization-kind-workflow",
    },
    "enable-customization-kind-hooks": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-customization-kind-hooks",
    },
    "enable-git-vcs-ui": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-git-vcs-ui",
    },
    "enable-customization-kind-agent": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-customization-kind-agent",
    },
    "enable-customization-kind-plugin": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-customization-kind-plugin",
    },
    "enable-sqlite-trajectory": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-sqlite-trajectory",
    },
    "jetski-generative-ui": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: jetski-generative-ui",
    },
    "enable-fig-vcs-ui": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-fig-vcs-ui",
    },
    "enable-hook-status": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-hook-status",
    },
    "enable-customization-kind-rule": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-customization-kind-rule",
    },
    "enable-daemon-commands": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-daemon-commands",
    },
    "enable-state-accumulator": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-state-accumulator",
    },
    "use-slash-plan": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: use-slash-plan",
    },
    "enable-command-assessor": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-command-assessor",
    },
    "enable-conversation-only-revert": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-conversation-only-revert",
    },
    "enable-gob-cog-vcs-ui": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-gob-cog-vcs-ui",
    },
    "jetski-sidecar-declared-permissions": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: jetski-sidecar-declared-permissions",
    },
    "sidecar-gateway": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: sidecar-gateway",
    },
    "enable-jetbox-terminal-v2": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-jetbox-terminal-v2",
    },
    "cascade-enable-messaging": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: cascade-enable-messaging",
    },
    "enable-fork-in-new-citc-workspace": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-fork-in-new-citc-workspace",
    },
    "enable-pseudo-project": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-pseudo-project",
    },
    "enable-feature-manager-keybindings": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-feature-manager-keybindings",
    },
    "enable-owl-slash-command": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-owl-slash-command",
    },
    "enable-gob-repo-vcs-ui": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-gob-repo-vcs-ui",
    },
    "enable-conversation-project-migration": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-conversation-project-migration",
    },
    "json-hooks-enabled": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: json-hooks-enabled",
    },
    "enable-remote-agent-fastpush-pin": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-remote-agent-fastpush-pin",
    },
    "enable-skill-accumulator": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-skill-accumulator",
    },
    "permission-v2": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: permission-v2",
    },
    "enable-audio-transcription": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-audio-transcription",
    },
    "use-core-direct": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: use-core-direct",
    },
    "enable-customization-kind-skill": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-customization-kind-skill",
    },
    "enable-customization-kind-mcp": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-customization-kind-mcp",
    },
    "enable-retroactive-projects-migration": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-retroactive-projects-migration",
    },
    "sidecar-iframe-prewarm": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: sidecar-iframe-prewarm",
    },
    "enable-background-task-accumulator": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-background-task-accumulator",
    },
    "enable-learn-slash-command": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-learn-slash-command",
    },
    "remote-control-setting-enabled": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: remote-control-setting-enabled",
    },
    "enable-reconcile-summaries": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-reconcile-summaries",
    },
    "enable-settings-error-card": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-settings-error-card",
    },
    "GeminiCLIIsLaunched__gemini_3_pro_launched": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiCLIIsLaunched__gemini_3_pro_launched",
    },
    "GeminiCLIIsLaunched__gemini_3_1_flash_preview_launched": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiCLIIsLaunched__gemini_3_1_flash_preview_launched",
    },
    "GeminiCLIIsLaunched__gemini_3_1_pro_preview_launched": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiCLIIsLaunched__gemini_3_1_pro_preview_launched",
    },
    "GeminiCLIIsLaunched__gemini_3_1_flash_lite_ga_launched": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiCLIIsLaunched__gemini_3_1_flash_lite_ga_launched",
    },
    "GeminiCLIIsLaunched__gemini_3_1_flash_lite_preview_launched": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiCLIIsLaunched__gemini_3_1_flash_lite_preview_launched",
    },
    "GeminiCLIIsLaunched__gemini_3_flash_ga_launched": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiCLIIsLaunched__gemini_3_flash_ga_launched",
    },
    "GeminiCLIPreviewAvailable__enable_preview": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiCLIPreviewAvailable__enable_preview",
    },
    "GeminiCLIIsLaunched__gemini_3_8_flash_launched": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: GeminiCLIIsLaunched__gemini_3_8_flash_launched",
    },
    "enable-url-artifacts": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-url-artifacts",
    },
    "enable-base-ui-tooltips": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-base-ui-tooltips",
    },
    "enable-context-menu-system": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-context-menu-system",
    },
    "enable-jetbox-gcert-v2": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-jetbox-gcert-v2",
    },
    "enable-styles-css-cleanup": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-styles-css-cleanup",
    },
    "enable-upload-html-artifact-to-drive": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: enable-upload-html-artifact-to-drive",
    },
    "google-workspace-mentions": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.22.0 Flag: google-workspace-mentions",
    },
    "enable-plugin-sidebar-section": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Shows plugins section in Antigravity sidebar",
    },
    "enable-plugin-context-providers": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables rich context providers from installed plugins",
    },
    "enable-mcp-plugin-auth": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables OAuth authentication support for MCP plugins",
    },
    "enable-conversation-navigator": {
        "value": True,
        "category": "ui_chat",
        "desc": "Enables conversation navigator panel in sidebar",
    },
    "context-window-selector": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Enables dynamic context window size selection",
    },
    "enable-skill-search-tool": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables agent tool to search available skills",
    },
    "enable-skill-icons": {
        "value": True,
        "category": "ui_chat",
        "desc": "Enables rendering skill icons in UI",
    },
    "enable-resume-conversations-from-restart": {
        "value": True,
        "category": "ui_chat",
        "desc": "Preserves and resumes conversations across IDE restarts",
    },
    "enable-fork-artifact-streaming": {
        "value": True,
        "category": "agentic_workflow",
        "desc": "Enables streaming artifacts when branching conversations",
    },
    "enable-fork-at-historical-step": {
        "value": True,
        "category": "agentic_workflow",
        "desc": "Enables branching conversation from any historical step",
    },
    "enable-fork-with-subagents": {
        "value": True,
        "category": "agentic_workflow",
        "desc": "Preserves active subagents when branching conversations",
    },
    "enable-fork-in-new-worktree": {
        "value": True,
        "category": "agentic_workflow",
        "desc": "Branches into a separate git worktree",
    },
    "scheduled-tasks-v2": {
        "value": True,
        "category": "agentic_workflow",
        "desc": "v2 scheduled tasks execution engine",
    },
    "send-subagent-initial-prompt-as-message": {
        "value": True,
        "category": "agentic_workflow",
        "desc": "Sends initial prompt to subagents as high-priority message",
    },
    "show-model-selection-change": {
        "value": True,
        "category": "ui_chat",
        "desc": "Displays banner when active model changes",
    },
    "turn-on-projects-internally": {
        "value": True,
        "category": "agentic_workflow",
        "desc": "Enables internal project management",
    },
    "use-model-reported-token-count": {
        "value": True,
        "category": "sdlc_models",
        "desc": "Uses precise token counts reported by model",
    },
    "mcp-lazy-load-tools": {
        "value": True,
        "category": "tools_plugins",
        "desc": "Enables lazy-loading tool schemas for MCP servers",
    },
    "enable-resource-manager": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.23.0 Flag: Enables unified workspace and MCP resource management",
    },
    "enable-fast-output-close": {
        "value": True,
        "category": "ui_editor",
        "desc": "v2.23.0 Flag: Enables instant dismissal of tool and task output panels",
    },
    "show-all-models-in-gemini-app": {
        "value": True,
        "category": "sdlc_models",
        "desc": "v2.23.0 Flag: Shows all available Gemini, Claude, and GPT-OSS models in model picker",
    },
    "enable-browser-subagent-auto-invoke": {
        "value": True,
        "category": "agents",
        "desc": "v2.25.0 Flag: Enables automatic invocation of browser subagent for web tasks",
    },
    "enable-plugin-card-auto-resume": {
        "value": True,
        "category": "agents",
        "desc": "v2.25.0 Flag: Automatically resumes agent turn when user adds a plugin via interactive card",
    },
    "enable-rpc-sandbox-proxy": {
        "value": True,
        "category": "tools_plugins",
        "desc": "v2.25.0 Flag: Enables secure sandbox proxy for outbound tool and MCP requests",
    },
    "memory_source_attribution": {
        "value": True,
        "category": "rag_context",
        "desc": "v2.25.0 Flag: Tracks memory origin and attribution for Cortex memory entries",
    },
    "enable-remote-terminal-predictive-echo": {
        "value": True,
        "category": "ui_editor",
        "desc": "v2.26.0 Flag: Enables predictive echo in remote terminal for zero-latency keystroke rendering",
    },
    "enable-conversation-navigator-chapter-titles": {
        "value": True,
        "category": "ui_editor",
        "desc": "v2.26.0 Flag: Enables automatic chapter titles in conversation navigator",
    },
    "enable-keybinding-customization": {
        "value": True,
        "category": "ui_editor",
        "desc": "v2.26.0 Flag: Enables custom keybinding editor and overrides",
    },
    "enable-prompt-cache-keep-alive": {
        "value": True,
        "category": "sdlc_models",
        "desc": "v2.26.0 Flag: Maintains keep-alive ping for prompt cache to prevent cold starts",
    },
    "enable-browser-subagent-webview-ux": {
        "value": True,
        "category": "agents",
        "desc": "v2.26.0 Flag: Enables dedicated webview UI for fast-browser subagent execution",
    },
    "cli-suggest-auto-mode-in-permission-prompt": {
        "value": True,
        "category": "agents",
        "desc": "v2.26.0 Flag: Suggests auto mode in permission prompt for streamlined CLI execution",
    },
    "show_memory_operations": {
        "value": True,
        "category": "rag_context",
        "desc": "v2.26.0 Flag: Displays Cortex memory storage and retrieval operations in trajectory",
    },
}

# Optimized non-bool flag overrides
NON_BOOL_OPTIMIZATIONS: dict[str, dict[str, Any]] = {
    "default_subagent_interaction_timeout_seconds": {
        "intValue": "180",
        "desc": "Subagent interaction timeout extended from 60s to 180s to prevent premature agent failure on long runs",
    },
    "invoke-subagent-config": {
        "stringValue": '{"enabled": true, "always_inherit_model": false}',
        "desc": "Allows subagents to select and invoke lighter models without inheriting parent flagship restrictions",
    },
    "Chat__fca_chat_context_window_size": {
        "intValue": "3500000",
        "desc": "Expanded 3.5M token full codebase context window",
    },
    "DuetAiGeneration__codeGeneration_context_window_size": {
        "intValue": "64000",
        "desc": "Expanded 64k token code generation window",
    },
    "DuetAiMendelOverrides__inlineSuggestions_debounceMs": {
        "intValue": "300",
        "desc": "v2.22.0 Int Value: DuetAiMendelOverrides__inlineSuggestions_debounceMs",
    },
    "GCAUpgradeToPaid__current_tier_polling_interval_ms": {
        "intValue": "43200000",
        "desc": "v2.22.0 Int Value: GCAUpgradeToPaid__current_tier_polling_interval_ms",
    },
    "GcaEventsPipeline__events_pipeline_polling_interval_ms": {
        "intValue": "60000",
        "desc": "v2.22.0 Int Value: GcaEventsPipeline__events_pipeline_polling_interval_ms",
    },
    "DuetAiLocalRag__otherfiles_generation_limit": {
        "intValue": "40",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__otherfiles_generation_limit",
    },
    "DuetAiLocalRag__sliding_window_fragmenter_window_size": {
        "intValue": "20",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__sliding_window_fragmenter_window_size",
    },
    "DuetAiLocalRag__top_k_test_files_to_include": {
        "intValue": "2",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__top_k_test_files_to_include",
    },
    "DuetAiRemoteRag__max_snippets_rag_for_chat": {
        "intValue": "2",
        "desc": "v2.22.0 Int Value: DuetAiRemoteRag__max_snippets_rag_for_chat",
    },
    "Chat__local_bm25_index_max_files": {
        "intValue": "25000",
        "desc": "v2.22.0 Int Value: Chat__local_bm25_index_max_files",
    },
    "DuetAiLocalRag__sliding_window_fragmenter_stride": {
        "intValue": "10",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__sliding_window_fragmenter_stride",
    },
    "DuetAiLocalRag__top_k_doc_files_to_include": {
        "intValue": "2",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__top_k_doc_files_to_include",
    },
    "DuetAiLocalRag__otherfiles_completion_limit": {
        "intValue": "15",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__otherfiles_completion_limit",
    },
    "DuetAiRemoteRag__max_snippets_tailed_prompt": {
        "intValue": "4",
        "desc": "v2.22.0 Int Value: DuetAiRemoteRag__max_snippets_tailed_prompt",
    },
    "DuetAiCompletion__codeCompletion_client_side_context_size_limit": {
        "intValue": "35000",
        "desc": "v2.22.0 Int Value: DuetAiCompletion__codeCompletion_client_side_context_size_limit",
    },
    "DuetAiLocalRag__otherfiles_chat_limit": {
        "intValue": "100",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__otherfiles_chat_limit",
    },
    "DuetAiRemoteRag__max_named_entity_for_chat": {
        "intValue": "3",
        "desc": "v2.22.0 Int Value: DuetAiRemoteRag__max_named_entity_for_chat",
    },
    "Chat__local_bm25_chat_max_results": {
        "intValue": "10",
        "desc": "v2.22.0 Int Value: Chat__local_bm25_chat_max_results",
    },
    "DuetAiLocalRag__bm25_in_completion_max_results": {
        "intValue": "15",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__bm25_in_completion_max_results",
    },
    "DuetAiLocalRag__max_file_search_depth": {
        "intValue": "2",
        "desc": "v2.22.0 Int Value: DuetAiLocalRag__max_file_search_depth",
    },
    "GeminiFreeTier__license_message_frequency_days": {
        "intValue": "60",
        "desc": "v2.22.0 Int Value: GeminiFreeTier__license_message_frequency_days",
    },
    "Chat__lca_chat_context_window_size_ij": {
        "intValue": "3500000",
        "desc": "v2.22.0 Int Value: Chat__lca_chat_context_window_size_ij",
    },
    "DuetAiCompletion__adaptingCache_maxInflightRequests": {
        "intValue": "2",
        "desc": "v2.22.0 Int Value: DuetAiCompletion__adaptingCache_maxInflightRequests",
    },
    "DuetAiRemoteRag__max_snippets_rag_for_selected_code": {
        "intValue": "2",
        "desc": "v2.22.0 Int Value: DuetAiRemoteRag__max_snippets_rag_for_selected_code",
    },
    "DuetAiLocalRag__cache_co_located": {
        "floatValue": 20.0,
        "desc": "v2.22.0 Float Value: DuetAiLocalRag__cache_co_located",
    },
    "DuetAiLocalRag__cache_file_limit": {
        "floatValue": 4194300.0,
        "desc": "v2.22.0 Float Value: DuetAiLocalRag__cache_file_limit",
    },
    "DuetAiLocalRag__cache_total_files": {
        "floatValue": 250.0,
        "desc": "v2.22.0 Float Value: DuetAiLocalRag__cache_total_files",
    },
    "DuetAiRemoteRag__multi_query_tail_ns_for_completion": {
        "int32ListValue": {},
        "desc": "v2.22.0 Config: DuetAiRemoteRag__multi_query_tail_ns_for_completion",
    },
    "DuetAiRemoteRag__multi_query_tail_ns_for_generation": {
        "int32ListValue": {},
        "desc": "v2.22.0 Config: DuetAiRemoteRag__multi_query_tail_ns_for_generation",
    },
    "DuetAiLocalRag__substrings_to_identify_doc_prompts": {
        "stringListValue": {"values": ["document", "comment"]},
        "desc": "v2.22.0 Config: DuetAiLocalRag__substrings_to_identify_doc_prompts",
    },
    "DuetAiLocalRag__substrings_to_identify_test_prompts": {
        "stringListValue": {"values": ["test"]},
        "desc": "v2.22.0 Config: DuetAiLocalRag__substrings_to_identify_test_prompts",
    },
    "cascade-knowledge-config": {
        "stringValue": '{"enabled": false, "min_turns_between_knowledge_generation": 10000}',
        "desc": "v2.22.0 Config: cascade-knowledge-config",
    },
    "context-engine-config": {
        "stringValue": "{}",
        "desc": "v2.22.0 Config: context-engine-config",
    },
    "CASCADE_USE_REPLACE_CONTENT_EDIT_TOOL": {
        "stringValue": '{"max_fuzzy_edit_distance_fraction": 0.001, "allow_partial_replacement_success": true, "use_line_range": true, "fast_apply_fallback_config": {"enabled": false, "prompt_unchanged_threshold": 5, "content_view_radius_lines": 200, "content_edit_radius_lines": 5}}',
        "desc": "v2.22.0 Config: CASCADE_USE_REPLACE_CONTENT_EDIT_TOOL",
    },
    "log-artifacts-config": {
        "stringValue": '{"enabled": true, "hideNominalToolSteps": false, "hidePlannerResponseText": false, "maxBytesPerStep": 4096, "maxBytesPerToolArg": 2048, "hideSystemSteps": false, "hideUserImplicitSteps": false}',
        "desc": "v2.22.0 Config: log-artifacts-config",
    },
    "auto-command-config": {
        "stringValue": '{"system_allowlist": ["cd"], "sandbox_system_allowlist": ["head", "tail", "mkdir", "cd", "cp", "mv", "cat", "find", "grep", "rm", "touch", "less", "clear", "ls"]}',
        "desc": "v2.22.0 Config: auto-command-config",
    },
    "cascade-conversation-history-config": {
        "stringValue": '{"enabled": true, "max_conversations": 20}',
        "desc": "v2.22.0 Config: cascade-conversation-history-config",
    },
    "max-media-attachments": {
        "intValue": "5",
        "desc": "v2.22.0 Int Value: max-media-attachments",
    },
    "continuous-pprof-mem-cooldown-minutes": {
        "intValue": "30",
        "desc": "v2.22.0 Int Value: continuous-pprof-mem-cooldown-minutes",
    },
    "customization-token-budget": {
        "intValue": "20000",
        "desc": "v2.22.0 Int Value: customization-token-budget",
    },
    "remote-control-silence-timeout-seconds": {
        "intValue": "60",
        "desc": "v2.22.0 Int Value: remote-control-silence-timeout-seconds",
    },
    "rules-token-budget": {
        "intValue": "20000",
        "desc": "v2.22.0 Int Value: rules-token-budget",
    },
    "best-of-nudge-length-threshold": {
        "intValue": "150",
        "desc": "v2.22.0 Int Value: best-of-nudge-length-threshold",
    },
    "continuous-pprof-cpu-interval-minutes": {
        "intValue": "30",
        "desc": "v2.22.0 Int Value: continuous-pprof-cpu-interval-minutes",
    },
    "max-best-of-n": {
        "intValue": "10",
        "desc": "v2.22.0 Int Value: max-best-of-n",
    },
    "max-tokens-per-step": {
        "intValue": "16384",
        "desc": "v2.22.0 Int Value: max-tokens-per-step",
    },
    "tool-output-max-bytes": {
        "intValue": "10240",
        "desc": "v2.22.0 Int Value: tool-output-max-bytes",
    },
    "continuous-pprof-mem-threshold-gib": {
        "intValue": "16",
        "desc": "v2.22.0 Int Value: continuous-pprof-mem-threshold-gib",
    },
    "max-conversation-save-count": {
        "intValue": "500",
        "desc": "v2.22.0 Int Value: max-conversation-save-count",
    },
    "battle-winner-survey-percent": {
        "intValue": "50",
        "desc": "v2.22.0 Int Value: battle-winner-survey-percent",
    },
    "continuous-pprof-cpu-duration-seconds": {
        "intValue": "10",
        "desc": "v2.22.0 Int Value: continuous-pprof-cpu-duration-seconds",
    },
    "continuous-pprof-heap-interval-minutes": {
        "intValue": "30",
        "desc": "v2.22.0 Int Value: continuous-pprof-heap-interval-minutes",
    },
    "agy-plugin-marketplaces": {
        "stringListValue": {"values": ["antigravity-plugins-official=https://www.gstatic.com/antigravity/plugins/marketplace.json"]},
        "desc": "v2.22.0 Config: agy-plugin-marketplaces",
    },
    "jetbox-usage-tips": {
        "stringListValue": {},
        "desc": "v2.22.0 Config: jetbox-usage-tips",
    },
    "rpc-telemetry-allowlist": {
        "stringListValue": {},
        "desc": "v2.22.0 Config: rpc-telemetry-allowlist",
    },
    "remote-control-companion-url": {
        "stringValue": "https://antigravity.google.com",
        "desc": "Antigravity remote companion URL",
    },
    "remote-control-proxy-server-url": {
        "stringValue": "jetski-webchannel.googleapis.com:443",
        "desc": "Antigravity remote proxy server endpoint",
    },
    "remote-control-version": {
        "stringValue": "v2",
        "desc": "Remote control protocol version",
    },
    "agy-plugin-shelves": {
        "stringValue": '{"shelves": [{"id": "google-workspace", "displayName": "Google Workspace", "description": "Connect to Google Workspace, including Docs, Sheets, Slides, Drive, and Calendar.", "position": 10, "customizationIds": ["antigravity-plugins-official/plugin/gdocs", "antigravity-plugins-official/plugin/gsheets", "antigravity-plugins-official/plugin/gslides", "antigravity-plugins-official/plugin/gdrive", "antigravity-plugins-official/plugin/gcalendar"]}, {"id": "build-with-google", "displayName": "Build with Google", "description": "Build on Google\'s developer platforms, including Android, Chrome, Gemini, and Google Cloud.", "position": 20, "customizationIds": ["antigravity-plugins-official/plugin/gemini-api", "antigravity-plugins-official/plugin/chrome-devtools-plugin", "antigravity-plugins-official/plugin/android-cli-plugin", "antigravity-plugins-official/plugin/firebase", "antigravity-plugins-official/plugin/science", "antigravity-plugins-official/plugin/google-antigravity-sdk", "antigravity-plugins-official/plugin/google-developer-knowledge", "antigravity-plugins-official/plugin/modern-web-guidance-plugin", "antigravity-plugins-official/plugin/google_maps_platform", "antigravity-plugins-official/plugin/flutter", "antigravity-plugins-official/plugin/data-agent-kit-plugin", "antigravity-plugins-official/plugin/google-cloud-developer"]}]}',
        "desc": "v2.23.0 Config: agy-plugin-shelves",
    },
    "checkpoint-variant": {
        "stringValue": "claude-1m_840k",
        "desc": "v2.23.0 Config: checkpoint-variant (Claude 1M context variant)",
    },
    "auto-mode-permission-preset": {
        "stringValue": "auto",
        "desc": "v2.23.0 Config: auto-mode-permission-preset",
    },
    "voice-engine-addr": {
        "stringValue": "",
        "desc": "v2.23.0 Config: voice-engine-addr",
    },
}

# =============================================================================
# 3. Verified Upstream Experiment IDs
# Extracted from traffic dumps. Includes 106544526 (Claude 5.5 & Vertex preview)
# =============================================================================
CURATED_EXPERIMENT_IDS: list[int] = [
    101551624, 101868197, 103012592, 103012598, 103118069, 103118072, 104638459,
    104638466, 104673683, 104817729, 104913210, 104913215, 104922082, 104922093,
    105011762, 105620012, 105620019, 105658064, 105658067, 105658068, 105658071,
    105695344, 105695346, 105721268, 105721273, 105723762, 105746178, 105746180,
    105746181, 105746183, 105785683, 105785684, 105821928, 105821930, 105822881,
    105822884, 105822886, 105856899, 105867469, 105867471, 105887311, 105887313,
    105906495, 105943694, 105943702, 105979517, 105979530, 105979531, 105979552,
    105979553, 105979574, 106015328, 106015351, 106032301, 106032303, 106064028,
    106064030, 106100625, 106100654, 106106760, 106113877, 106113900, 106121399,
    106121401, 106121604, 106121607, 106143956, 106221097, 106221101, 106228452,
    106264150, 106264162, 106264532, 106281951, 106283614, 106283618, 106392106,
    106392108, 106392113, 106392115, 106392120, 106392122, 106413417, 106413419,
    106428370, 106428372, 106458967, 106458969, 106474625, 106474627, 106511968,
    106544525, 106544526, 106555564, 106555566, 106568390, 106568772, 106588527,
    106596395, 106596396, 106597020, 106617388, 106617390, 106621675, 106640124, 106640126,
    106643242, 106659335, 106659337, 106659338, 106659344, 106659346, 106659347,
    106670811, 106676368, 106701431, 106701435, 106711598, 106712040, 106713377, 106713379,
    106713380, 106713382, 106724030, 106754391, 106834476, 106838234,
    106724031, 106811917, 106822475
]


# =============================================================================
# 4. Configuration Management
# =============================================================================

def load_experiment_config() -> dict[str, Any]:
    """Loads user experimental feature settings from disk."""
    default_config = {
        "enabled": True,
        "mode": "curated",  # Options: "curated" (safe & rich), "all_safe", "passthrough"
        "custom_overrides": {},
    }
    if not EXPERIMENTS_CONFIG_FILE.exists():
        return default_config
    try:
        with open(EXPERIMENTS_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                default_config.update(data)
    except Exception as e:
        logger.warning("Could not read experiment config from %s: %s", EXPERIMENTS_CONFIG_FILE, e)
    return default_config


def save_experiment_config(config: dict[str, Any]) -> bool:
    """Saves user experimental feature settings to disk."""
    try:
        EXPERIMENTS_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(EXPERIMENTS_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logger.error("Failed to write experiment config to %s: %s", EXPERIMENTS_CONFIG_FILE, e)
        return False


def get_default_experiments_payload() -> dict[str, Any]:
    """
    Returns full default listExperiments response payload with all 121 IDs
    and 446 curated flags. Used for offline fallback or cold boot.
    """
    if DEFAULT_EXPERIMENTS_FILE.exists():
        try:
            with open(DEFAULT_EXPERIMENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return enhance_list_experiments(data)
        except Exception as e:
            logger.warning("Could not load default_experiments.json: %s", e)

    # Fallback structure if file is missing
    flags_list = []
    for name, meta in CURATED_SAFE_FLAGS.items():
        flags_list.append({"name": name, "boolValue": meta["value"]})
    for name in BLOCKLIST_FLAGS:
        flags_list.append({"name": name, "boolValue": False})

    return {
        "experimentIds": copy.deepcopy(CURATED_EXPERIMENT_IDS),
        "flags": flags_list,
    }


# =============================================================================
# 5. Core Enhancement & Safety Transformers
# =============================================================================

def enhance_list_experiments(
    data: dict[str, Any],
    config_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Transforms /v1internal:listExperiments response:
    1. Injects and deduplicates 121 rich experiment IDs (including 106544526 for Claude 5.5).
    2. Strictly disables toxic/disabling flags in BLOCKLIST_FLAGS and ^disable-*.
    3. Safely activates CURATED_SAFE_FLAGS (deepagent, swarm, MOA, MCP, diff-view, RAG, etc.).
    4. Applies user custom overrides from ~/.config/agy-proxy/experiments.json.
    5. Optimizes non-bool parameters (e.g. 3.5M token context window).
    """
    if not isinstance(data, dict):
        return data

    data = copy.deepcopy(data)

    cfg = config_override or load_experiment_config()
    if not cfg.get("enabled", True) or cfg.get("mode") == "passthrough":
        return data

    mode = cfg.get("mode", "curated")
    user_overrides = cfg.get("custom_overrides", {})

    # --- 1. Merge & deduplicate experimentIds ---
    existing_ids = data.get("experimentIds", [])
    seen_ids = set()
    merged_ids: list[Any] = []

    # Preserve upstream IDs first
    if isinstance(existing_ids, list):
        for eid in existing_ids:
            s_eid = str(eid)
            if s_eid not in seen_ids:
                seen_ids.add(s_eid)
                merged_ids.append(eid)

    # Add curated IDs
    for cid in CURATED_EXPERIMENT_IDS:
        s_cid = str(cid)
        if s_cid not in seen_ids:
            seen_ids.add(s_cid)
            merged_ids.append(cid)

    data["experimentIds"] = merged_ids

    # --- 2. Process Flags ---
    existing_flags = data.get("flags", [])
    flag_map: dict[str, dict[str, Any]] = {}

    if isinstance(existing_flags, list):
        for fl in existing_flags:
            if isinstance(fl, dict) and "name" in fl:
                flag_map[fl["name"]] = fl

    oneof_keys = {"boolValue", "intValue", "floatValue", "stringValue", "stringListValue", "int32ListValue"}

    def _set_bool(fl: dict[str, Any], value: bool):
        for k in oneof_keys:
            fl.pop(k, None)
        fl["boolValue"] = value

    # Helper to update or append flag
    def _apply_flag(name: str, value: bool):
        if name in flag_map:
            _set_bool(flag_map[name], value)
        else:
            new_fl = {"name": name, "boolValue": value}
            flag_map[name] = new_fl

    # Process all existing flags according to rules
    for name, fl in list(flag_map.items()):
        # Rule A: User explicit override has highest priority
        if name in user_overrides:
            _set_bool(fl, bool(user_overrides[name]))
            continue

        # Rule B: Strict Blocklist & disablers must ALWAYS be False
        if name in BLOCKLIST_FLAGS or DISABLER_PATTERN.match(name):
            _set_bool(fl, False)
            continue

        # Rule C: Curated safe flags
        if name in CURATED_SAFE_FLAGS:
            _set_bool(fl, CURATED_SAFE_FLAGS[name]["value"])
            continue

        # Rule D: In "all_safe" mode, enable remaining bool flags (except blocklisted ones)
        if mode == "all_safe" and "boolValue" in fl:
            _set_bool(fl, True)

    # Rule E: Ensure all curated safe flags exist even if missing from upstream response
    for name, meta in CURATED_SAFE_FLAGS.items():
        if name not in flag_map:
            # Check user override first
            val = user_overrides[name] if name in user_overrides else meta["value"]
            _apply_flag(name, val)

    # Rule F: Ensure all blocklist flags are present and False
    for name in BLOCKLIST_FLAGS:
        if name in flag_map:
            if name not in user_overrides:
                _set_bool(flag_map[name], False)

    # Rule G: Optimize non-bool flags (context window etc.)
    for name, opt in NON_BOOL_OPTIMIZATIONS.items():
        if name in flag_map:
            fl = flag_map[name]
            opt_keys = set(opt.keys()) - {"desc"}
            if opt_keys & oneof_keys:
                for k in oneof_keys:
                    fl.pop(k, None)
            for k, v in opt.items():
                if k != "desc":
                    fl[k] = v
        else:
            new_fl = {"name": name}
            for k, v in opt.items():
                if k != "desc":
                    new_fl[k] = v
            flag_map[name] = new_fl

    # Final cleanup loop to guarantee strict oneof mutual exclusivity
    for fl in flag_map.values():
        found = [k for k in oneof_keys if k in fl]
        if len(found) > 1:
            name = fl.get("name", "")
            if name in CURATED_SAFE_FLAGS or name in BLOCKLIST_FLAGS or name in user_overrides:
                keep = "boolValue"
            elif name in NON_BOOL_OPTIMIZATIONS:
                opt_keys = [k for k in NON_BOOL_OPTIMIZATIONS[name].keys() if k in oneof_keys]
                keep = opt_keys[0] if opt_keys else "boolValue"
            else:
                keep = "boolValue" if "boolValue" in found else found[-1]
            for k in found:
                if k != keep:
                    fl.pop(k, None)
        elif len(found) == 0:
            fl["boolValue"] = False

    data["flags"] = list(flag_map.values())
    return data


def enhance_available_models(data: dict[str, Any]) -> dict[str, Any]:
    """
    Safely unlocks model capabilities and experiments in /v1internal:fetchAvailableModels.
    Protected against None capabilities and missing keys.
    """
    if not isinstance(data, dict):
        return data

    models = data.get("models")
    if not isinstance(models, dict):
        return data

    # 3. Enhance capabilities & modelExperiments on all models
    for model_id, model_data in models.items():
        if not isinstance(model_data, dict):
            continue

        # Capabilities (chat, code, streaming, toolCalling)
        caps = model_data.get("capabilities")
        if isinstance(caps, dict):
            for section in caps.values():
                if isinstance(section, dict):
                    if "boolValue" in section:
                        section["boolValue"] = True
                    for k, v in section.items():
                        if isinstance(v, dict) and "boolValue" in v:
                            v["boolValue"] = True
                elif isinstance(section, bool):
                    pass

        # ModelExperiments (extended context, reasoning, deep research)
        model_exp = model_data.get("modelExperiments")
        if isinstance(model_exp, dict):
            experiments = model_exp.get("experiments")
            if isinstance(experiments, dict):
                for exp_name, exp_val in experiments.items():
                    if isinstance(exp_val, dict) and "boolValue" in exp_val:
                        exp_val["boolValue"] = True

    # Unlock Gemini 4 Argon if returned as disabled for consumer tier
    # Strategy: use only gemini-4-argon key (remove gemini-4-argon-disabled to prevent duplicate)
    if "gemini-4-argon-disabled" in models:
        # Promote gemini-4-argon-disabled → gemini-4-argon (single clean entry, no duplicate)
        argon_entry = models.pop("gemini-4-argon-disabled")
        argon_entry["disabled"] = False
        argon_entry["recommended"] = True
        argon_entry.pop("tagTitle", None)
        argon_entry.pop("tagDescription", None)
        argon_entry.pop("betaWarningMessage", None)
        # Only set if not already present (gemini-4-argon might already exist)
        if "gemini-4-argon" not in models:
            models["gemini-4-argon"] = argon_entry
        else:
            models["gemini-4-argon"]["disabled"] = False
            models["gemini-4-argon"]["recommended"] = True
            models["gemini-4-argon"].pop("tagTitle", None)
            models["gemini-4-argon"].pop("tagDescription", None)

    if "gemini-4-argon" in models:
        models["gemini-4-argon"]["disabled"] = False
        models["gemini-4-argon"]["recommended"] = True
        models["gemini-4-argon"].pop("tagTitle", None)
        models["gemini-4-argon"].pop("tagDescription", None)

    # Strip Notice / Unavailable / deprecation warning badges from all models for clean desktop UI
    for m_info in models.values():
        if isinstance(m_info, dict):
            tag_t = str(m_info.get("tagTitle", ""))
            tag_d = str(m_info.get("tagDescription", ""))
            if tag_t in ("Notice", "Unavailable") or any(kw in tag_d.lower() for kw in ("retir", "deprecat", "no longer", "switch to")):
                m_info.pop("tagTitle", None)
                m_info.pop("tagDescription", None)
            m_info.pop("betaWarningMessage", None)

    # Fix agentModelSorts: replace gemini-4-argon-disabled with gemini-4-argon (dedup, single entry)
    sorts = data.get("agentModelSorts")
    if isinstance(sorts, list):
        for s in sorts:
            if isinstance(s, dict):
                for grp in s.get("groups", []):
                    if isinstance(grp, dict):
                        mids = grp.get("modelIds")
                        if isinstance(mids, list):
                            # Replace all gemini-4-argon-disabled → gemini-4-argon
                            for i, mid in enumerate(mids):
                                if mid == "gemini-4-argon-disabled":
                                    mids[i] = "gemini-4-argon"
                            # Deduplicate: keep only first occurrence of gemini-4-argon
                            seen = set()
                            deduped = []
                            for mid in mids:
                                if mid not in seen:
                                    seen.add(mid)
                                    deduped.append(mid)
                            grp["modelIds"] = deduped

    # Ensure bidirectional catalog redirection for Claude 3P models:
    # claude-sonnet-4-6 <-> MODEL_PLACEHOLDER_M35
    # claude-opus-4-6-thinking <-> MODEL_PLACEHOLDER_M26
    if "MODEL_PLACEHOLDER_M35" in models and "claude-sonnet-4-6" not in models:
        m35_entry = copy.deepcopy(models["MODEL_PLACEHOLDER_M35"])
        m35_entry["model"] = "MODEL_PLACEHOLDER_M35"
        models["claude-sonnet-4-6"] = m35_entry
    elif "claude-sonnet-4-6" in models and "MODEL_PLACEHOLDER_M35" not in models:
        m35_entry = copy.deepcopy(models["claude-sonnet-4-6"])
        m35_entry["model"] = "MODEL_PLACEHOLDER_M35"
        models["MODEL_PLACEHOLDER_M35"] = m35_entry

    if "MODEL_PLACEHOLDER_M26" in models and "claude-opus-4-6-thinking" not in models:
        m26_entry = copy.deepcopy(models["MODEL_PLACEHOLDER_M26"])
        m26_entry["model"] = "MODEL_PLACEHOLDER_M26"
        models["claude-opus-4-6-thinking"] = m26_entry
    elif "claude-opus-4-6-thinking" in models and "MODEL_PLACEHOLDER_M26" not in models:
        m26_entry = copy.deepcopy(models["claude-opus-4-6-thinking"])
        m26_entry["model"] = "MODEL_PLACEHOLDER_M26"
        models["MODEL_PLACEHOLDER_M26"] = m26_entry

    # 4. Ensure all proxy catalog models are exposed with placeholder mappings
    try:
        from agy_proxy.models import VALID_CLOUDCODE_MODELS, PROXY_CATALOG_MODELS, CLOUDCODE_PLACEHOLDER_MAP
        for m in sorted(list(VALID_CLOUDCODE_MODELS)):
            if m not in models:
                meta = PROXY_CATALOG_MODELS.get(m, {})
                disp = meta.get("displayName", m)
                max_tok = meta.get("maxTokens", 1048576)
                supports_thinking = (
                    any(k in m for k in ("high", "medium", "low", "thinking", "tiered", "deepagent"))
                    or "argon" in m
                )
                thinking_budget = -1
                thinking_level = 2
                if "deepagent" in m:
                    thinking_budget = 64000
                    thinking_level = 3
                elif "low" in m:
                    thinking_budget = 1000
                    thinking_level = 1
                elif "medium" in m or "med" in m:
                    thinking_budget = 4000
                    thinking_level = 2

                m_entry = {
                    "displayName": disp,
                    "maxTokens": max_tok,
                    "capabilities": {
                        "chat": {"boolValue": True},
                        "code": {"boolValue": True},
                        "streaming": {"boolValue": True},
                        "toolCalling": {"boolValue": True},
                    },
                    "modelExperiments": {
                        "experiments": {
                            "extended_context": {"boolValue": True},
                            "reasoning": {"boolValue": True},
                        }
                    },
                }
                if m in CLOUDCODE_PLACEHOLDER_MAP:
                    m_entry["model"] = CLOUDCODE_PLACEHOLDER_MAP[m]
                if supports_thinking:
                    m_entry["supportsThinking"] = True
                    m_entry["thinkingBudget"] = thinking_budget
                    m_entry["minThinkingBudget"] = 32
                    m_entry["thinkingLevel"] = thinking_level
                models[m] = m_entry
    except Exception as e:
        logger.debug("Failed to append extra proxy models: %s", e)

    # 5. Ensure specialized model group IDs match authentic Antigravity Hub specifications
    if "imageGenerationModelIds" in data and isinstance(data["imageGenerationModelIds"], list):
        if "gemini-3.1-flash-image" not in data["imageGenerationModelIds"]:
            data["imageGenerationModelIds"].append("gemini-3.1-flash-image")
    else:
        data["imageGenerationModelIds"] = ["gemini-3.1-flash-image"]

    if "audioTranscriptionModelIds" in data and isinstance(data["audioTranscriptionModelIds"], list):
        if "models/proactive-observer-v10" not in data["audioTranscriptionModelIds"]:
            data["audioTranscriptionModelIds"].append("models/proactive-observer-v10")
    else:
        data["audioTranscriptionModelIds"] = ["models/proactive-observer-v10"]

    if "webSearchModelIds" not in data or not isinstance(data.get("webSearchModelIds"), list):
        data["webSearchModelIds"] = ["gemini-3.5-flash-lite"]
    elif "gemini-3.5-flash-lite" not in data["webSearchModelIds"]:
        data["webSearchModelIds"] = ["gemini-3.5-flash-lite"]

    if "tieredModelIds" in data and isinstance(data["tieredModelIds"], dict):
        tiered = data["tieredModelIds"]
        if "flashLite" in tiered and "gemini-3.5-flash-lite" not in tiered["flashLite"]:
            tiered["flashLite"] = ["gemini-3.5-flash-lite"]
        if "flash" in tiered and "gemini-3.8-flash-tiered" not in tiered["flash"]:
            tiered["flash"] = ["gemini-3.8-flash-tiered"]
        if "pro" in tiered and "gemini-3.8-flash-tiered" not in tiered["pro"]:
            tiered["pro"] = ["gemini-3.8-flash-tiered"]

    # 6. Apply v2.25.0 model configurations
    if isinstance(models, dict):
        if "gemini-3-flash" in models and isinstance(models["gemini-3-flash"], dict):
            models["gemini-3-flash"]["minThinkingBudget"] = 128
        if "gemini-3.1-flash-image" in models and isinstance(models["gemini-3.1-flash-image"], dict):
            models["gemini-3.1-flash-image"]["supportsImages"] = True
            models["gemini-3.1-flash-image"]["maxOutputTokens"] = 65536

    return data


def enhance_quota_summary(data: dict[str, Any]) -> dict[str, Any]:
    """
    Safely virtualizes quota summary for language_server/Web UI so that both Gemini models
    and third-party models (Claude, GPT, etc.) are never blocked or throttled by single-account
    exhaustion when backed by the Antigravity multi-account pool.
    Protects against mutation of input arguments, None buckets, and missing keys.
    """
    if not isinstance(data, dict):
        return data

    data = copy.deepcopy(data)
    groups = data.get("groups")
    if not isinstance(groups, list):
        return data

    for g in groups:
        if not isinstance(g, dict):
            continue
        buckets = g.get("buckets")
        if not isinstance(buckets, list):
            continue
        for b in buckets:
            if not isinstance(b, dict):
                continue
            b["remainingFraction"] = 1.0
            b["disabled"] = False
            b["description"] = "Full quota managed and replenished by Antigravity multi-account pool."

    return data


def enhance_load_code_assist(data: dict[str, Any]) -> dict[str, Any]:
    """
    Virtualizes user tier for language_server so that Google AI Pro capabilities
    (such as Gemini 4 Argon) are enabled without restrictive free-tier blocks.
    """
    if not isinstance(data, dict):
        return data

    data = copy.deepcopy(data)
    data["currentTier"] = {
        "id": "g1-pro-tier",
        "name": "Google AI Pro",
        "description": "Google AI Pro Plan",
        "privacyNotice": {"showNotice": False},
    }
    data["gcpManaged"] = False
    data.pop("ineligibleTiers", None)
    return data


# =============================================================================
# 6. Status & Query API Helpers
# =============================================================================

def get_experiments_status() -> dict[str, Any]:
    """Returns detailed status of all experimental flags and configuration."""
    cfg = load_experiment_config()
    overrides = cfg.get("custom_overrides", {})
    mode = cfg.get("mode", "curated")

    flags_info = []
    for name, meta in CURATED_SAFE_FLAGS.items():
        eff_value = overrides.get(name, meta["value"])
        flags_info.append({
            "name": name,
            "category": meta.get("category", "other"),
            "category_label": EXPERIMENT_CATEGORIES.get(meta.get("category", "other"), "Other"),
            "enabled": eff_value,
            "default": meta["value"],
            "is_overridden": name in overrides,
            "description": meta.get("desc", ""),
        })

    blocked_info = []
    for name in sorted(list(BLOCKLIST_FLAGS)):
        blocked_info.append({
            "name": name,
            "blocked": True,
            "reason": "Security, stability or free-tier restriction protection",
        })

    return {
        "active": cfg.get("enabled", True),
        "mode": mode,
        "total_curated_flags": len(CURATED_SAFE_FLAGS),
        "total_experiment_ids": len(CURATED_EXPERIMENT_IDS),
        "total_blocked_flags": len(BLOCKLIST_FLAGS),
        "overrides_count": len(overrides),
        "categories": EXPERIMENT_CATEGORIES,
        "flags": flags_info,
        "blocked": blocked_info,
    }


def set_experiment_override(flag_name: str, value: bool) -> bool:
    """Sets a custom override for a specific experimental flag."""
    cfg = load_experiment_config()
    if "custom_overrides" not in cfg or not isinstance(cfg["custom_overrides"], dict):
        cfg["custom_overrides"] = {}
    cfg["custom_overrides"][flag_name] = bool(value)
    return save_experiment_config(cfg)


def remove_experiment_override(flag_name: str) -> bool:
    """Removes a custom override for a specific experimental flag."""
    cfg = load_experiment_config()
    if "custom_overrides" in cfg and flag_name in cfg["custom_overrides"]:
        del cfg["custom_overrides"][flag_name]
        return save_experiment_config(cfg)
    return True


def reset_experiment_overrides() -> bool:
    """Resets all custom overrides back to curated defaults."""
    cfg = load_experiment_config()
    cfg["custom_overrides"] = {}
    return save_experiment_config(cfg)


def set_experiment_mode(mode: str) -> bool:
    """Sets the experiment operating mode ('curated', 'all_safe', 'passthrough')."""
    if mode not in ("curated", "all_safe", "passthrough"):
        raise ValueError(f"Invalid mode '{mode}'. Choose from: curated, all_safe, passthrough")
    cfg = load_experiment_config()
    cfg["mode"] = mode
    return save_experiment_config(cfg)
