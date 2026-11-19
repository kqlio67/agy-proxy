import copy
from starlette.testclient import TestClient

from agy_proxy.experiments import (
    BLOCKLIST_FLAGS,
    CURATED_SAFE_FLAGS,
    enhance_list_experiments,
    enhance_available_models,
    enhance_quota_summary,
    enhance_load_code_assist,
    get_default_experiments_payload,
    get_experiments_status,
    set_experiment_override,
    remove_experiment_override,
    reset_experiment_overrides,
    load_experiment_config,
)
from agy_proxy.server import create_app
from agy_proxy.auth.pool import AccountPool


def test_get_default_experiments_payload():
    payload = get_default_experiments_payload()
    assert isinstance(payload, dict)
    assert "experimentIds" in payload
    assert "flags" in payload

    exp_ids = payload["experimentIds"]
    assert len(exp_ids) >= 121
    assert 106544526 in exp_ids

    flags_dict = {f["name"]: f.get("boolValue") for f in payload["flags"] if "name" in f}
    assert flags_dict.get("enable-deepagent") is True
    assert flags_dict.get("enable-agent-team") is True
    assert flags_dict.get("enable-swarm") is True
    assert flags_dict.get("Chat__enable_chat_moa") is True
    assert flags_dict.get("Chat__enable_chat_agentic_mcp_chat") is True
    assert flags_dict.get("DuetAiGeneration__codeGeneration_enable_diff_view") is True

    # Blocklist verification
    for b_flag in BLOCKLIST_FLAGS:
        assert flags_dict.get(b_flag) is False or flags_dict.get(b_flag) is None


def test_enhance_list_experiments_blocklist_enforcement():
    # Simulate an upstream response where toxic flags were accidentally True
    mock_upstream = {
        "experimentIds": [12345],
        "flags": [
            {"name": "GeminiFreeTier__enable_free_tier", "boolValue": True},
            {"name": "disable-search-tools", "boolValue": True},
            {"name": "disable-command-assessor", "boolValue": True},
            {"name": "remove-conversation-history", "boolValue": True},
            {"name": "GCAFeedbackBlock__enable_feedback_block", "boolValue": True},
            {"name": "disable-loop-detection", "boolValue": True},
            {"name": "enable-deepagent", "boolValue": False},
        ],
    }

    enhanced = enhance_list_experiments(mock_upstream, config_override={"enabled": True, "mode": "curated", "custom_overrides": {}})
    flags_dict = {f["name"]: f.get("boolValue") for f in enhanced["flags"]}

    # Critical blocklist must be forced to False
    assert flags_dict["GeminiFreeTier__enable_free_tier"] is False
    assert flags_dict["disable-search-tools"] is False
    assert flags_dict["disable-command-assessor"] is False
    assert flags_dict["remove-conversation-history"] is False
    assert flags_dict["GCAFeedbackBlock__enable_feedback_block"] is False

    # disable-loop-detection should remain True
    assert flags_dict["disable-loop-detection"] is True

    # Curated safe flag must be enabled
    assert flags_dict["enable-deepagent"] is True

    # Experiment IDs must be merged and contain Claude 5.5 preview ID 106544526
    assert 12345 in enhanced["experimentIds"]
    assert 106544526 in enhanced["experimentIds"]


def test_enhance_list_experiments_non_bool_optimizations():
    mock_upstream = {
        "experimentIds": [],
        "flags": [
            {"name": "Chat__fca_chat_context_window_size", "intValue": "1000"},
            {"name": "DuetAiGeneration__codeGeneration_context_window_size", "intValue": "2000"},
        ],
    }
    enhanced = enhance_list_experiments(mock_upstream, config_override={"enabled": True, "mode": "curated", "custom_overrides": {}})
    flags_dict = {f["name"]: f.get("intValue") for f in enhanced["flags"]}

    assert flags_dict["Chat__fca_chat_context_window_size"] == "3500000"
    assert flags_dict["DuetAiGeneration__codeGeneration_context_window_size"] == "64000"


def test_enhance_list_experiments_modes():
    mock_upstream = {
        "experimentIds": [100],
        "flags": [
            {"name": "some_random_feature", "boolValue": False},
            {"name": "enable-deepagent", "boolValue": False},
            {"name": "disable-search-tools", "boolValue": False},
        ],
    }

    # 1. Mode "passthrough"
    pass_res = enhance_list_experiments(copy.deepcopy(mock_upstream), config_override={"enabled": True, "mode": "passthrough"})
    pass_map = {f["name"]: f.get("boolValue") for f in pass_res["flags"]}
    assert pass_map["enable-deepagent"] is False
    assert pass_map["some_random_feature"] is False

    # 2. Mode "all_safe"
    safe_res = enhance_list_experiments(copy.deepcopy(mock_upstream), config_override={"enabled": True, "mode": "all_safe", "custom_overrides": {}})
    safe_map = {f["name"]: f.get("boolValue") for f in safe_res["flags"]}
    assert safe_map["enable-deepagent"] is True
    assert safe_map["some_random_feature"] is True
    assert safe_map["disable-search-tools"] is False  # Blocklist still enforced!


def test_enhance_available_models_resilience():
    # Test with None capabilities and nested None structures
    mock_models_data = {
        "models": {
            "gemini-3.1-pro-high": {
                "capabilities": None,
                "modelExperiments": None,
            },
            "claude-opus-4-6-thinking": {
                "capabilities": None,
                "modelExperiments": {
                    "experiments": {
                        "reasoning": {"boolValue": False},
                        "extended_context": {"boolValue": False},
                    }
                },
            },
            "gemini-3.8-flash-high": {
                "capabilities": {
                    "chat": {"boolValue": False},
                    "code": {"boolValue": False},
                },
                "modelExperiments": {
                    "experiments": {
                        "deep_research": {"boolValue": False},
                    }
                },
            },
        }
    }

    enhanced = enhance_available_models(mock_models_data)
    assert enhanced["models"]["gemini-3.1-pro-high"]["capabilities"] is None

    claude_exp = enhanced["models"]["claude-opus-4-6-thinking"]["modelExperiments"]["experiments"]
    assert claude_exp["reasoning"]["boolValue"] is True
    assert claude_exp["extended_context"]["boolValue"] is True

    flash_caps = enhanced["models"]["gemini-3.8-flash-high"]["capabilities"]
    assert flash_caps["chat"]["boolValue"] is True
    assert flash_caps["code"]["boolValue"] is True


def test_enhance_load_code_assist():
    from agy_proxy.experiments import enhance_load_code_assist
    data = {
        "currentTier": {"id": "free-tier"},
        "ineligibleTiers": [{"tierId": "free-tier"}],
    }
    enhanced = enhance_load_code_assist(data)
    assert enhanced["currentTier"]["id"] == "g1-pro-tier"
    assert enhanced["currentTier"]["name"] == "Google AI Pro"
    assert "ineligibleTiers" not in enhanced

    # Paid tier (e.g. g1-ultra-tier) must never be downgraded to g1-pro-tier
    ultra_data = {
        "currentTier": {"id": "g1-ultra-tier", "name": "Google AI Ultra", "description": "Google AI Ultra Plan"},
        "paidTier": {"id": "g1-ultra-tier", "name": "Google AI Ultra"},
        "gcpManaged": True,
    }
    enhanced_ultra = enhance_load_code_assist(ultra_data)
    assert enhanced_ultra["currentTier"]["id"] == "g1-ultra-tier"
    assert enhanced_ultra["currentTier"]["name"] == "Google AI Ultra"
    assert enhanced_ultra["gcpManaged"] is True


def test_non_destructive_experiment_preservation():
    """Verify unknown disable-* flags and upstream knowledge/history configs are preserved."""
    upstream = {
        "experimentIds": [],
        "flags": [
            {"name": "disable-stained-loas2-creds", "boolValue": True},
            {"name": "disable-teamwork-forced-flash-model", "boolValue": True},
            {"name": "cascade-knowledge-config", "stringValue": '{"enabled": true}'},
            {"name": "max-media-attachments", "intValue": "20"},
        ],
    }
    enhanced = enhance_list_experiments(upstream, config_override={"enabled": True, "mode": "curated", "custom_overrides": {}})
    fmap = {f["name"]: f for f in enhanced["flags"]}
    assert fmap["disable-stained-loas2-creds"]["boolValue"] is True
    assert fmap["disable-teamwork-forced-flash-model"]["boolValue"] is True
    assert fmap["cascade-knowledge-config"]["stringValue"] == '{"enabled": true}'
    assert fmap["max-media-attachments"]["intValue"] == "20"


def test_experiments_status_and_overrides():
    status = get_experiments_status()
    assert "active" in status
    assert "mode" in status
    assert "flags" in status
    assert len(status["flags"]) >= len(CURATED_SAFE_FLAGS)

    # Test override lifecycle
    test_flag = "test_custom_experimental_flag"
    try:
        set_experiment_override(test_flag, True)
        cfg = load_experiment_config()
        assert cfg.get("custom_overrides", {}).get(test_flag) is True

        remove_experiment_override(test_flag)
        cfg = load_experiment_config()
        assert test_flag not in cfg.get("custom_overrides", {})
    finally:
        reset_experiment_overrides()


def test_experiments_api_endpoints():
    pool = AccountPool()
    app = create_app(account_pool=pool)
    client = TestClient(app)

    # GET /api/experiments
    resp = client.get("/api/experiments")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is True
    assert "mode" in data
    assert "flags" in data
    assert data["total_curated_flags"] >= 50

    # POST /api/experiments
    post_resp = client.post("/api/experiments", json={
        "mode": "curated",
        "overrides": {"enable-deepagent": True}
    })
    assert post_resp.status_code == 200
    post_data = post_resp.json()
    assert post_data["status"] == "ok"

    # POST /api/experiments/reset
    reset_resp = client.post("/api/experiments/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["status"] == "ok"


def test_enhance_list_experiments_strict_oneof_mutual_exclusivity():
    """Verify that every flag in the output has strictly EXACTLY ONE oneof value field."""
    oneof_keys = {"boolValue", "intValue", "floatValue", "stringValue", "stringListValue", "int32ListValue"}

    # 1. Test with default experiments
    payload = get_default_experiments_payload()
    for f in payload.get("flags", []):
        present = set(f.keys()) & oneof_keys
        assert len(present) == 1, f"Flag {f.get('name')} does not have exactly 1 oneof value: {present}"

    # 2. Test with artificially corrupted upstream flags containing multiple oneof fields
    corrupted_upstream = {
        "flags": [
            {
                "name": "enable-deepagent",
                "boolValue": False,
                "stringValue": "corrupted",
                "intValue": "999",
            },
            {
                "name": "GeminiFreeTier__enable_free_tier",
                "boolValue": True,
                "stringValue": "toxic_free_tier",
            },
            {
                "name": "DuetAiLocalRag__cache_co_located",
                "intValue": "20",
                "stringValue": "should_be_float",
            },
            {
                "name": "agy-plugin-marketplaces",
                "stringValue": '{"values": ["old"]}',
            },
            {
                "name": "unknown_flag_with_no_value",
            },
        ]
    }

    enhanced = enhance_list_experiments(corrupted_upstream)
    flags_map = {f["name"]: f for f in enhanced["flags"]}

    for name, f in flags_map.items():
        present = set(f.keys()) & oneof_keys
        assert len(present) == 1, f"Enhanced flag {name} does not have exactly 1 oneof value: {present}"

    # Curated flag should have boolValue True
    assert flags_map["enable-deepagent"]["boolValue"] is True
    assert "stringValue" not in flags_map["enable-deepagent"]
    assert "intValue" not in flags_map["enable-deepagent"]

    # Blocklist flag should have boolValue False
    assert flags_map["GeminiFreeTier__enable_free_tier"]["boolValue"] is False
    assert "stringValue" not in flags_map["GeminiFreeTier__enable_free_tier"]

    # DuetAiLocalRag__cache_co_located should have floatValue
    assert isinstance(flags_map["DuetAiLocalRag__cache_co_located"].get("floatValue"), (int, float))
    assert "intValue" not in flags_map["DuetAiLocalRag__cache_co_located"]

    # agy-plugin-marketplaces should have stringListValue
    assert isinstance(flags_map["agy-plugin-marketplaces"].get("stringListValue"), dict)
    assert "stringValue" not in flags_map["agy-plugin-marketplaces"]

    # Flag with no value should default to boolValue False
    assert flags_map["unknown_flag_with_no_value"]["boolValue"] is False


def test_enhance_list_experiments_input_immutability():
    """Verify that enhance_list_experiments does not mutate its input argument."""
    original_input = {
        "experimentIds": [1001],
        "flags": [
            {"name": "enable-deepagent", "boolValue": False},
            {"name": "GeminiFreeTier__enable_free_tier", "boolValue": True},
        ],
    }
    input_copy = copy.deepcopy(original_input)

    enhanced = enhance_list_experiments(original_input)
    assert original_input == input_copy, "enhance_list_experiments mutated its input argument!"
    assert enhanced["flags"] != original_input["flags"]


def test_v2_26_0_updates():
    """Verify v2.29.0 & v2.31.0 User-Agent, experiment IDs, and experimental flags."""
    from agy_proxy.auth.constants import USER_AGENT
    assert any(v in USER_AGENT for v in ("2.29.0", "2.31.0"))
    assert any(cl in USER_AGENT for cl in ("1006921819", "1013060670", "1017595969"))

    payload = get_default_experiments_payload()
    exp_ids = payload["experimentIds"]
    assert 106724031 in exp_ids
    assert 106811917 in exp_ids
    assert 106822475 in exp_ids
    assert 106712040 in exp_ids
    assert 106712041 in exp_ids
    assert 106761937 in exp_ids
    assert 106793467 in exp_ids
    assert 106863974 in exp_ids
    # v2.31.0 Experiment IDs
    assert 106986919 in exp_ids
    assert 106828998 in exp_ids
    assert 107036252 in exp_ids

    flags_dict = {f["name"]: f.get("boolValue") for f in payload["flags"] if "name" in f}
    assert flags_dict.get("enable-remote-terminal-predictive-echo") is True
    assert flags_dict.get("enable-browser-subagent-webview-ux") is True
    assert flags_dict.get("enable-conversation-navigator-chapter-titles") is True
    assert flags_dict.get("enable-keybinding-customization") is True
    assert flags_dict.get("enable-prompt-cache-keep-alive") is True
    assert flags_dict.get("show_memory_operations") is True
    assert flags_dict.get("ambient_prefetch_enabled") is False
    assert flags_dict.get("ambient_memory_si_enabled") is True
    assert flags_dict.get("enable-browser-subagent-webview") is False
    assert flags_dict.get("enable-citc-workspace-hygiene-rule") is True
    assert flags_dict.get("enable-swarm-monitor") is True
    assert flags_dict.get("enable-resource-manager") is True
    assert flags_dict.get("enable-fast-output-close") is True
    assert flags_dict.get("show-all-models-in-gemini-app") is True
    assert flags_dict.get("enable-browser-subagent-auto-invoke") is True
    assert flags_dict.get("enable-plugin-card-auto-resume") is True
    assert flags_dict.get("enable-rpc-sandbox-proxy") is True
    assert flags_dict.get("memory_source_attribution") is True

    # Test enhanced optimizations
    enhanced = enhance_list_experiments({"experimentIds": [], "flags": []})
    enh_flags = {f["name"]: f for f in enhanced["flags"]}
    assert enh_flags["checkpoint-variant"]["stringValue"] == "claude-1m_840k"
    assert enh_flags["auto-mode-permission-preset"]["stringValue"] == "auto"
    assert "shelves" in enh_flags["agy-plugin-shelves"]["stringValue"]
    assert 106596396 in enhanced["experimentIds"]
    assert 106834476 in enhanced["experimentIds"]
    assert 106838234 in enhanced["experimentIds"]


def test_deepagent_and_swarm_flags():
    """Verify deepagent, swarm-narrator, agent-plugins-spec, and subagent timeout optimization."""
    payload = get_default_experiments_payload()
    flags_dict = {f["name"]: f.get("boolValue") for f in payload["flags"] if "name" in f}
    assert flags_dict.get("enable-deepagent") is True
    assert flags_dict.get("enable-swarm-narrator") is True
    assert flags_dict.get("enable-agent-plugins-spec") is True

    # Test enhanced non-bool subagent timeout optimization (180s)
    enhanced = enhance_list_experiments({"experimentIds": [], "flags": []})
    enh_flags = {f["name"]: f for f in enhanced["flags"]}
    assert enh_flags["default_subagent_interaction_timeout_seconds"]["intValue"] == "180"
    assert "always_inherit_model" in enh_flags["invoke-subagent-config"]["stringValue"]
    assert enh_flags["enable-swarm-narrator"]["boolValue"] is True
    assert enh_flags["enable-agent-plugins-spec"]["boolValue"] is True


def test_enhance_quota_summary():
    """Verify that enhance_quota_summary unlocks 3p buckets and protects against mutation."""
    mock_quota = {
        "groups": [
            {
                "displayName": "Third Party Models",
                "buckets": [
                    {
                        "bucketId": "3p-weekly",
                        "displayName": "Weekly Limit Remaining",
                        "remainingFraction": 0,
                        "description": "You have hit your weekly limit.",
                    },
                    {
                        "bucketId": "3p-5h",
                        "displayName": "Five Hour Limit Remaining",
                        "remainingFraction": 0.02,
                        "description": "Low limit.",
                    },
                ],
            },
            {
                "displayName": "Gemini Models",
                "buckets": [
                    {
                        "bucketId": "gemini-weekly",
                        "displayName": "Weekly Limit Remaining",
                        "remainingFraction": 0.95,
                        "description": "Active.",
                    },
                    {
                        "bucketId": "gemini-5h",
                        "displayName": "Five Hour Limit Remaining",
                        "remainingFraction": 0.0,
                        "description": "Exhausted.",
                    },
                ],
            },
        ]
    }
    input_copy = copy.deepcopy(mock_quota)
    enhanced = enhance_quota_summary(mock_quota)

    # Input immutability
    assert mock_quota == input_copy, "enhance_quota_summary mutated its input!"

    # 3p buckets virtualized to 1.0
    g3p = next(g for g in enhanced["groups"] if g["displayName"] == "Third Party Models")
    b_3p_weekly = next(b for b in g3p["buckets"] if b["bucketId"] == "3p-weekly")
    b_3p_5h = next(b for b in g3p["buckets"] if b["bucketId"] == "3p-5h")
    assert b_3p_weekly["remainingFraction"] == 1.0
    assert b_3p_weekly["disabled"] is False
    assert "Antigravity" in b_3p_weekly["description"]
    assert b_3p_5h["remainingFraction"] == 1.0
    assert b_3p_5h["disabled"] is False

    # Gemini buckets virtualized to 1.0
    ggem = next(g for g in enhanced["groups"] if g["displayName"] == "Gemini Models")
    b_gem_weekly = next(b for b in ggem["buckets"] if b["bucketId"] == "gemini-weekly")
    b_gem_5h = next(b for b in ggem["buckets"] if b["bucketId"] == "gemini-5h")
    assert b_gem_weekly["remainingFraction"] == 1.0
    assert b_gem_weekly["disabled"] is False
    assert b_gem_5h["remainingFraction"] == 1.0
    assert b_gem_5h["disabled"] is False

    # Resilience against invalid/empty payloads
    assert enhance_quota_summary({}) == {}
    assert enhance_quota_summary(None) is None
    assert enhance_quota_summary({"groups": "invalid"}) == {"groups": "invalid"}


def test_enhance_available_models_argon_unlocking():
    """Verify that gemini-4-argon is fully unlocked (disabled: false, tags stripped) and agentModelSorts synced."""
    mock_models_data = {
        "models": {
            "gemini-4-argon-disabled": {
                "displayName": "Gemini 4 Argon",
                "supportsImages": True,
                "supportsThinking": True,
                "thinkingBudget": -1,
                "minThinkingBudget": 32,
                "recommended": False,
                "maxTokens": 1048576,
                "maxOutputTokens": 65536,
                "disabled": True,
                "tagTitle": "Disabled on Free Tier",
                "tagDescription": "Upgrade to Pro to use Argon",
                "model": "MODEL_PLACEHOLDER_M421",
            },
            "gemini-3.8-flash-high": {
                "displayName": "Gemini 3.8 Flash (High)",
                "disabled": False,
            },
        },
        "agentModelSorts": [
            {
                "displayName": "Recommended",
                "groups": [
                    {
                        "modelIds": [
                            "gemini-3.8-flash-high",
                            "gemini-4-argon-disabled",
                        ]
                    }
                ],
            }
        ],
    }

    enhanced = enhance_available_models(mock_models_data)

    # 1. Verify gemini-4-argon-disabled is removed to prevent duplicate
    assert "gemini-4-argon-disabled" not in enhanced["models"]

    # 2. Verify synthesized gemini-4-argon is created and fully unlocked
    assert "gemini-4-argon" in enhanced["models"]
    m_argon = enhanced["models"]["gemini-4-argon"]
    assert m_argon["disabled"] is False
    assert m_argon["recommended"] is True
    assert "tagTitle" not in m_argon
    assert "tagDescription" not in m_argon
    assert m_argon["model"] == "MODEL_PLACEHOLDER_M421"

    # 3. Verify agentModelSorts replaced gemini-4-argon-disabled with gemini-4-argon
    rec_group = enhanced["agentModelSorts"][0]["groups"][0]["modelIds"]
    assert "gemini-4-argon-disabled" not in rec_group
    assert "gemini-4-argon" in rec_group


def test_new_curated_safe_flags_activation():
    """Verify newly added safe flags from Antigravity Hub v2.24.0 and v2.25.0 are activated."""
    from agy_proxy.experiments import CURATED_SAFE_FLAGS

    new_flag_names = [
        "enable-bottom-terminal-panel",
        "enable-browser-subagent-webview-script",
        "enable-iwa-builtin-browser",
        "enable-omni-mention-search",
        "enable-remote-control-audio-transcription",
        "use-component-rewrite",
        "enable-browser-subagent-auto-invoke",
        "enable-plugin-card-auto-resume",
        "enable-rpc-sandbox-proxy",
        "memory_source_attribution",
    ]

    for name in new_flag_names:
        assert name in CURATED_SAFE_FLAGS, f"Flag {name} missing from CURATED_SAFE_FLAGS"
        assert CURATED_SAFE_FLAGS[name]["value"] is True

    # Test that enhance_list_experiments activates them when upstream is False
    upstream = {
        "flags": [{"name": n, "boolValue": False} for n in new_flag_names],
        "experimentIds": [],
    }
    enhanced = enhance_list_experiments(upstream, config_override={"enabled": True, "mode": "curated", "custom_overrides": {}})
    flags_map = {f["name"]: f.get("boolValue") for f in enhanced["flags"]}

    for name in new_flag_names:
        assert flags_map[name] is True, f"Flag {name} was not enabled by enhance_list_experiments"


def test_default_experiments_catalog_completeness():
    """Verify default_experiments.json contains exactly 509 flags and updated experimentIds."""
    payload = get_default_experiments_payload()
    assert 106712040 in payload["experimentIds"]
    assert 106724031 in payload["experimentIds"]
    assert 106811917 in payload["experimentIds"]
    assert 106822475 in payload["experimentIds"]
    assert 106908755 in payload["experimentIds"]
    assert 106937777 in payload["experimentIds"]

    flags = payload["flags"]
    flag_names = {f["name"] for f in flags}

    # Verify new flags are present
    assert "best-of-n-default-model-id" in flag_names
    assert "enable-bottom-terminal-panel" in flag_names
    assert "enable-browser-subagent-webview-script" in flag_names
    assert "enable-iwa-builtin-browser" in flag_names
    assert "enable-omni-mention-search" in flag_names
    assert "science-plugin-split-targets" in flag_names
    assert "enable-remote-control-audio-transcription" in flag_names
    assert "use-component-rewrite" in flag_names
    assert "enable-browser-subagent-auto-invoke" in flag_names
    assert "enable-plugin-card-auto-resume" in flag_names
    assert "enable-rpc-sandbox-proxy" in flag_names
    assert "memory_source_attribution" in flag_names
    assert "enable-remote-terminal-predictive-echo" in flag_names
    assert "enable-browser-subagent-webview-ux" in flag_names
    assert "enable-conversation-navigator-chapter-titles" in flag_names
    assert "enable-keybinding-customization" in flag_names
    assert "enable-prompt-cache-keep-alive" in flag_names
    assert "show_memory_operations" in flag_names
    assert "ambient_prefetch_enabled" in flag_names

    # Check raw default file
    import json
    from agy_proxy.experiments import DEFAULT_EXPERIMENTS_FILE
    with open(DEFAULT_EXPERIMENTS_FILE, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    assert len(raw_data["flags"]) >= 529
    raw_flag_names = {f["name"] for f in raw_data["flags"]}
    assert "enable-voice-remote" in raw_flag_names
    assert "scheduled-tasks-v2" in raw_flag_names
    assert "enable-notebook-rendering" in raw_flag_names
    assert "ReturnAdminControls__enable_for_cli" not in raw_flag_names
    assert "enable-swarm" not in raw_flag_names
    assert 106712040 in raw_data["experimentIds"]
    assert 106724031 in raw_data["experimentIds"]
    assert 106811917 in raw_data["experimentIds"]
    assert 106822474 in raw_data["experimentIds"]
    assert 106913264 in raw_data["experimentIds"]
    assert "enable-browser-subagent-auto-invoke" in raw_flag_names
    assert "enable-plugin-card-auto-resume" in raw_flag_names
    assert "enable-rpc-sandbox-proxy" in raw_flag_names
    assert "memory_source_attribution" in raw_flag_names
    assert "enable-remote-terminal-predictive-echo" in raw_flag_names
    assert "enable-browser-subagent-webview-ux" in raw_flag_names
    assert "enable-conversation-navigator-chapter-titles" in raw_flag_names
    assert "enable-keybinding-customization" in raw_flag_names
    assert "enable-prompt-cache-keep-alive" in raw_flag_names
    assert "show_memory_operations" in raw_flag_names
    assert "ambient_prefetch_enabled" in raw_flag_names
    assert "enable-context-usage-indicator" in raw_flag_names
    assert "cli-enable-btw-threads" in raw_flag_names
    assert "enable-new-themes" in raw_flag_names
    assert "enable-user-message-bubbles" in raw_flag_names
    assert "enable-hub-builtin-browser" in raw_flag_names


def test_v2_25_0_model_enhancements():
    """Verify v2.25.0 model catalog adjustments in enhance_available_models."""
    raw_models = {
        "models": {
            "gemini-3-flash": {
                "displayName": "Gemini 3 Flash",
                "minThinkingBudget": 32,
            },
            "gemini-3.1-flash-image": {
                "displayName": "Gemini 3.1 Flash Image",
            },
        }
    }
    enhanced = enhance_available_models(raw_models)
    assert enhanced["models"]["gemini-3-flash"]["minThinkingBudget"] == 128
    assert enhanced["models"]["gemini-3.1-flash-image"]["supportsImages"] is True
    assert enhanced["models"]["gemini-3.1-flash-image"]["maxOutputTokens"] == 65536


def test_deepagent_cohort_and_effort_injected():
    """Verify deepagent-arm treatment and deepagent_effort are injected in enhance_list_experiments."""
    input_data = {
        "flags": [
            {"name": "deepagent-arm", "stringValue": ""},
            {"name": "enable-deepagent", "boolValue": False},
        ]
    }
    enhanced = enhance_list_experiments(input_data)
    flags_map = {f["name"]: f for f in enhanced["flags"]}
    assert flags_map["enable-deepagent"]["boolValue"] is True
    assert flags_map["deepagent-arm"]["stringValue"] == "treatment"
    assert flags_map["deepagent_effort"]["intValue"] == "3"


def test_v2_28_2_flags_fetch_config_and_nuxes():
    """Verify v2.28.2 curated flags, blocklist, partner marketplace, fetchConfig endpoint, and Argon NUX."""
    import json
    from pathlib import Path
    from agy_proxy.experiments import enhance_fetch_config

    v228_safe = [
        "enable-context-usage-indicator",
        "cli-enable-btw-threads",
        "cli-suggest-auto-mode-in-permission-prompt",
        "enable-new-themes",
        "enable-user-message-bubbles",
        "enable-paste-text-as-snippet",
        "enable-conversation-only-revert",
        "enable-customization-load-error-notice",
        "enable-hook-status",
        "enable-hub-builtin-browser",
        "enable-plugin-auth",
        "enable-remote-agent-fastpush-pin",
    ]
    v228_blocked = [
        "enforce-default-permission-preset",
        "enable-scoped-csrf-tokens",
        "use-aicode-jetski-endpoint",
    ]

    upstream = {
        "experimentIds": [],
        "flags": (
            [{"name": n, "boolValue": False} for n in v228_safe]
            + [{"name": n, "boolValue": True} for n in v228_blocked]
            + [{"name": "jetski-hub-announcement-message", "stringValue": "Please update Jetski..."}]
        ),
    }
    enhanced = enhance_list_experiments(
        upstream,
        config_override={"enabled": True, "mode": "curated", "custom_overrides": {}},
    )
    fmap = {f["name"]: f for f in enhanced["flags"]}
    for n in v228_safe:
        assert fmap[n]["boolValue"] is True, f"Expected {n} to be True"
    for n in v228_blocked:
        assert fmap[n]["boolValue"] is False, f"Expected {n} to be False"

    assert fmap["jetski-hub-announcement-message"]["stringValue"] == ""
    assert fmap["browser-subagent-model"]["stringValue"] == "gemini-3.8-flash-low"
    assert fmap["continuous-pprof-cpu-interval-minutes"]["intValue"] == "90"
    marketplaces = fmap["agy-plugin-marketplaces"]["stringListValue"]["values"]
    assert any("antigravity-plugins-partners=" in m for m in marketplaces)

    # Verify enhance_fetch_config & GET /v1alpha:fetchConfig
    fc_out = enhance_fetch_config(
        {
            "experimentConfig": {
                "enabledExperimentIds": [106838234],
                "experimentFlags": [{"name": "enable-deepagent", "boolValue": False}],
            },
            "availableExperiences": [],
            "agySurfaceConfig": {"agentExperiences": {"experienceNames": ["gemini-3.8-flash-high"]}},
        },
        config_override={"enabled": True, "mode": "curated", "custom_overrides": {}},
    )
    fc_flags = {f["name"]: f for f in fc_out["experimentConfig"]["experimentFlags"]}
    assert fc_flags["enable-deepagent"]["boolValue"] is True
    assert 106913264 in fc_out["experimentConfig"]["enabledExperimentIds"]
    exp_enums = {
        e["name"]: e["surfaceConfig"]["clientModelEnumName"]
        for e in fc_out["availableExperiences"]
    }
    assert exp_enums["gemini-4-argon-high"] == "MODEL_PLACEHOLDER_M417"
    assert exp_enums["gemini-4-argon-medium"] == "MODEL_PLACEHOLDER_M418"
    assert exp_enums["gemini-4-argon-low"] == "MODEL_PLACEHOLDER_M419"
    assert exp_enums["tab_flash_lite_preview"] == "MODEL_PLACEHOLDER_M19"
    assert exp_enums["tab_jump_flash_lite_preview"] == "MODEL_PLACEHOLDER_M28"

    agent_names = fc_out["agySurfaceConfig"]["agentExperiences"]["experienceNames"]
    assert "gemini-4-argon-high" in agent_names
    assert "tab_flash_lite_preview" in agent_names
    assert "tab_jump_flash_lite_preview" in agent_names

    pool = AccountPool()
    app = create_app(account_pool=pool)
    client = TestClient(app)
    resp = client.get("/v1alpha:fetchConfig")
    assert resp.status_code == 200
    body = resp.json()
    assert "experimentConfig" in body
    assert any(
        e.get("name") == "gemini-4-argon-medium"
        for e in body.get("availableExperiences", [])
    )

    nuxes_path = Path(__file__).resolve().parent.parent / "agy_proxy" / "default_nuxes.json"
    nuxes_data = json.loads(nuxes_path.read_text(encoding="utf-8"))
    uids = {n.get("uid") for n in nuxes_data.get("cascadeNuxes", [])}
    assert 58 in uids


def test_enhance_quota_summary_pool_sync():
    """Verify that enhance_quota_summary synchronizes real fractions and accounts from active pool."""
    from unittest.mock import MagicMock

    mock_pool = MagicMock()
    mock_acc1 = MagicMock()
    mock_acc1.email = "dev1@gmail.com"
    mock_acc1.is_primary = True
    mock_acc1.auth_method = "consumer"
    mock_acc1.quota_details = {
        "gemini": {"percent": 45.0, "is_rate_limited": False},
        "3p": {"percent": 80.0, "is_rate_limited": False},
        "argon": {"percent": 0.0, "is_rate_limited": False},
    }
    mock_acc1.quota_summary = {
        "groups": [
            {
                "buckets": [
                    {
                        "bucketId": "gemini-weekly",
                        "remainingFraction": 0.45,
                        "description": "You have used some of your weekly limit, it will fully refresh in 3 days.",
                    }
                ]
            }
        ]
    }

    mock_acc2 = MagicMock()
    mock_acc2.email = "dev2@gmail.com"
    mock_acc2.is_primary = False
    mock_acc2.auth_method = "consumer"
    mock_acc2.quota_details = {
        "gemini": {"percent": 100.0, "is_rate_limited": False},
        "3p": {"percent": 100.0, "is_rate_limited": False},
        "argon": {"percent": 100.0, "is_rate_limited": False},
    }

    mock_pool.accounts = {"acc1": mock_acc1, "acc2": mock_acc2}
    mock_pool.get_primary_account.return_value = mock_acc1

    raw_summary = {
        "groups": [
            {
                "displayName": "Gemini Models",
                "buckets": [
                    {
                        "bucketId": "gemini-weekly",
                        "displayName": "Weekly Limit Remaining",
                        "remainingFraction": 1.0,
                        "description": "Standard",
                    }
                ],
            }
        ]
    }

    enhanced = enhance_quota_summary(raw_summary, pool=mock_pool)
    bucket = enhanced["groups"][0]["buckets"][0]

    # Fraction reflects primary real fraction
    assert round(bucket["remainingFraction"] * 100) == 45
    # Description reflects active account, reset string, and pool status
    assert "dev1@gmail.com" in bucket["description"]
    assert "45%" in bucket["description"]
    assert "Pool: 2/2 accounts ready" in bucket["description"]
    assert "Resets in 3 days" in bucket["description"]

    # Also verify enhance_load_code_assist displays pool metadata
    code_assist = enhance_load_code_assist({}, pool=mock_pool)
    assert "dev1@gmail.com" in code_assist["currentTier"]["description"]
    assert "2 Google accounts connected" in code_assist["currentTier"]["description"]


def test_v2_29_0_flags_and_categories():
    """Verify Antigravity v2.29.0 categories, Voice Mode, Remote Agents, and Notebook flags."""
    from agy_proxy.experiments import (
        EXPERIMENT_CATEGORIES,
        CURATED_SAFE_FLAGS,
        enhance_list_experiments,
        enhance_available_models,
    )

    # Categories
    assert "voice" in EXPERIMENT_CATEGORIES
    assert "remote_agents" in EXPERIMENT_CATEGORIES
    assert "automations" in EXPERIMENT_CATEGORIES

    # Curated safe flags
    v2_29_flags = [
        "enable-notebook-rendering",
        "scheduled-tasks-v2",
        "enable-voice-relay",
        "enable-voice-remote",
        "enable-voice-remote-control",
        "enable-remote-control-audio-transcription",
        "enable-remote-ai",
        "enable-enterprise-remote-ai",
        "remote-control-setting-enabled",
        "antigravity-browser-history",
        "enable-fill-conversation-layout",
    ]
    for flag in v2_29_flags:
        assert flag in CURATED_SAFE_FLAGS
        assert CURATED_SAFE_FLAGS[flag]["value"] is True

    # Check enhance_list_experiments activates them
    mock_upstream = {"experimentIds": [], "flags": []}
    enhanced = enhance_list_experiments(mock_upstream, config_override={"enabled": True, "mode": "curated", "custom_overrides": {}})
    flags_dict = {f["name"]: f.get("boolValue") for f in enhanced["flags"]}

    for flag in v2_29_flags:
        assert flags_dict.get(flag) is True, f"Expected {flag} to be True in enhanced experiments"

    # Check enhance_available_models exposes claude-haiku-4-5
    models_res = enhance_available_models({"models": {}})
    models_dict = models_res["models"]
    assert "claude-haiku-4-5" in models_dict
    assert "claude-haiku-4-5-thinking" in models_dict
    assert models_dict["claude-haiku-4-5"]["capabilities"]["chat"]["boolValue"] is True


def test_v2_31_0_flags_and_capabilities():
    """Verify Antigravity v2.31.0 experimental flags and capabilities unlock."""
    from agy_proxy.experiments import CURATED_SAFE_FLAGS, enhance_list_experiments

    v2_31_flags = [
        "jetski-generative-ui-prompt",
        "enable-customization-kind-flow",
        "enable-advanced-builtin-workflow",
        "enable-swarm-raw-tab",
        "native-swarm-channels-local",
        "enable-browser-subagent-webmcp",
        "enable-browser-element-picker",
        "enable-agent-omnitrace",
        "enable-review",
        "enable-side-question-rework",
        "enable-thumbs-down-reroll",
        "enable-help-slash-command",
        "enable-enhanced-file-viewer",
        "show_memory_operation_diffs",
        "configure-artifact-width",
        "cortex-snapshot-recording",
        "cortex-snapshot-revert",
        "enable-composer-connectors",
    ]
    for flag in v2_31_flags:
        assert flag in CURATED_SAFE_FLAGS, f"Expected {flag} in CURATED_SAFE_FLAGS"
        assert CURATED_SAFE_FLAGS[flag]["value"] is True

    enhanced = enhance_list_experiments(
        {"experimentIds": [], "flags": []},
        config_override={"enabled": True, "mode": "curated", "custom_overrides": {}},
    )
    flags_dict = {f["name"]: f.get("boolValue") for f in enhanced["flags"]}
    for flag in v2_31_flags:
        assert flags_dict.get(flag) is True, f"Expected {flag} to be True in enhanced experiments"

    fmap = {f["name"]: f for f in enhanced["flags"]}
    assert fmap["deepagent-arm"]["stringValue"] == "treatment"
    assert fmap["deepagent_effort"]["intValue"] == "3"


def test_enhance_available_models_neon_unlocking():
    """Verify that gemini-4-neon and gemini-pro-orchestrator-neon are unlocked and sorted at top."""
    mock_models_data = {
        "models": {
            "gemini-4-neon-medium": {
                "displayName": "Gemini 4 Neon (Medium)",
                "disabled": True,
                "model": "MODEL_PLACEHOLDER_M533",
            },
            "gemini-pro-orchestrator-neon": {
                "displayName": "Orchestrator",
                "disabled": True,
                "model": "MODEL_PLACEHOLDER_M537",
            },
            "gemini-3.8-flash-high": {
                "displayName": "Gemini 3.8 Flash (High)",
                "disabled": False,
            },
        },
        "agentModelSorts": [
            {
                "displayName": "Recommended",
                "groups": [
                    {
                        "modelIds": [
                            "gemini-3.8-flash-high",
                            "gemini-4-neon-medium",
                            "gemini-pro-orchestrator-neon",
                        ]
                    }
                ],
            }
        ],
    }
    enhanced = enhance_available_models(mock_models_data)
    models = enhanced["models"]
    assert models["gemini-4-neon-medium"]["disabled"] is False
    assert models["gemini-4-neon-medium"]["recommended"] is True
    assert models["gemini-pro-orchestrator-neon"]["disabled"] is False
    assert models["gemini-pro-orchestrator-neon"]["displayName"] == "Orchestrator (Neon)"

    # Verify sort ranking: neon should be sorted before flash
    mids = enhanced["agentModelSorts"][0]["groups"][0]["modelIds"]
    assert mids.index("gemini-4-neon-medium") < mids.index("gemini-3.8-flash-high")
    assert mids.index("gemini-pro-orchestrator-neon") < mids.index("gemini-3.8-flash-high")






