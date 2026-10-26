import os
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from agy_proxy.cli import build_parser
from agy_proxy.server import create_app
from agy_proxy.web_launcher import (
    DEFAULT_GOOGLE_API_URL,
    DEFAULT_GOOGLE_CC_ENDPOINT,
    build_web_args,
    find_language_server,
    get_csrf_token,
    get_web_status,
    is_port_in_use,
    kill_electron_instances,
)


def test_find_language_server_explicit_file(tmp_path):
    mock_bin = tmp_path / "language_server"
    mock_bin.write_text("#!/bin/sh\nexit 0\n")
    mock_bin.chmod(0o755)

    # persist=False prevents overwriting the real ~/.config/agy-proxy/config.json
    found = find_language_server(custom_path=mock_bin, persist=False)
    assert found == mock_bin.resolve()


def test_find_language_server_explicit_dir(tmp_path):
    mock_bin = tmp_path / "language_server"
    mock_bin.write_text("#!/bin/sh\nexit 0\n")
    mock_bin.chmod(0o755)

    # persist=False prevents overwriting the real ~/.config/agy-proxy/config.json
    found = find_language_server(custom_path=tmp_path, persist=False)
    assert found == mock_bin.resolve()


def test_find_language_server_env_var(tmp_path, monkeypatch):
    mock_bin = tmp_path / "language_server_linux_x64"
    mock_bin.write_text("#!/bin/sh\nexit 0\n")
    mock_bin.chmod(0o755)

    monkeypatch.setenv("ANTIGRAVITY_LS_PATH", str(mock_bin))
    found = find_language_server()
    assert found == mock_bin.resolve()


def test_get_csrf_token(monkeypatch):
    # When CSRF_TOKEN is in env
    monkeypatch.setenv("CSRF_TOKEN", "custom-secret-token-12345")
    assert get_csrf_token() == "custom-secret-token-12345"

    # When CSRF_TOKEN is unset
    monkeypatch.delenv("CSRF_TOKEN", raising=False)
    token = get_csrf_token()
    assert isinstance(token, str)
    assert len(token) >= 8


def test_is_port_in_use():
    # Bind a temporary socket to guarantee it's in use
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert is_port_in_use(port) is True

    # Once closed, verify it's free
    assert is_port_in_use(port) is False


def test_build_web_args_with_proxy(tmp_path):
    fake_bin = tmp_path / "language_server"
    args = build_web_args(
        ls_path=fake_bin,
        port=44741,
        csrf_token="my-token",
        proxy_url="http://127.0.0.1:8000",
        ide_version="2.25.0",
    )
    assert str(fake_bin) in args
    assert "--standalone" in args
    assert "--override_ide_name" in args
    assert "antigravity" in args
    assert "--subclient_type" in args
    assert "hub" in args
    assert "--override_ide_version" in args
    assert "2.25.0" in args
    assert "--https_server_port" in args
    assert "44741" in args
    assert "--csrf_token" in args
    assert "my-token" in args
    assert "--enable_sidecars" in args
    assert "--disable_telemetry=true" in args
    assert "--persistent_mode=true" in args
    assert "--api_server_url" in args
    assert "http://127.0.0.1:8000" in args
    assert "--cloud_code_endpoint" in args


def test_build_web_args_without_proxy(tmp_path):
    fake_bin = tmp_path / "language_server"
    args = build_web_args(
        ls_path=fake_bin,
        port=44741,
        csrf_token="my-token",
        proxy_url=None,
    )
    assert "--standalone" in args
    assert "--api_server_url" in args
    assert DEFAULT_GOOGLE_API_URL in args
    assert "--cloud_code_endpoint" in args
    assert DEFAULT_GOOGLE_CC_ENDPOINT in args


def test_build_web_args_direct_mode(tmp_path):
    fake_bin = tmp_path / "language_server"
    args = build_web_args(
        ls_path=fake_bin,
        port=44741,
        csrf_token="my-token",
        proxy_url="http://127.0.0.1:8000",
        direct=True,
    )
    assert "--standalone" in args
    assert "--api_server_url" in args
    assert DEFAULT_GOOGLE_API_URL in args
    assert "http://127.0.0.1:8000" not in args
    assert "--cloud_code_endpoint" in args
    assert DEFAULT_GOOGLE_CC_ENDPOINT in args


def test_get_web_status_mock():
    with patch("agy_proxy.web_launcher.is_port_in_use", return_value=True), \
         patch("agy_proxy.web_launcher.get_running_instances", return_value={
             "electron_running": False,
             "electron_pids": [],
             "language_server_running": True,
             "language_server_pids": [99999],
             "language_server_details": [{"pid": 99999, "standalone": True}],
         }):
        status = get_web_status(port=44741)
        assert status["status"] == "running"
        assert status["url"] == "https://127.0.0.1:44741/"
        assert status["pid"] == 99999
        assert status["electron_running"] is False
        assert status["estimated_ram_saved_mb"] == 1600


def test_kill_electron_instances():
    mock_proc = MagicMock()
    mock_proc.is_running.return_value = False

    with patch("agy_proxy.web_launcher.get_running_instances", return_value={
        "electron_pids": [12345],
    }), patch("psutil.Process", return_value=mock_proc):
        killed = kill_electron_instances()
        assert killed == [12345]
        mock_proc.terminate.assert_called_once()


def test_cli_parser_web_subcommand():
    parser = build_parser()

    # Test 'web' subcommand
    args = parser.parse_args(["web", "--port", "44800", "--kill-electron", "--no-open"])
    assert args.subcommand == "web"
    assert args.port == 44800
    assert args.kill_electron is True
    assert args.no_open is True

    # Test alias 'antigravity-web'
    args_alias = parser.parse_args(["antigravity-web", "--status"])
    assert args_alias.subcommand == "antigravity-web"
    assert args_alias.status is True

    # Test alias 'web-mode'
    args_mode = parser.parse_args(["web-mode", "--browser", "helium", "-v"])
    assert args_mode.subcommand == "web-mode"
    assert args_mode.browser == "helium"
    assert args_mode.verbose is True

    # Test --debug sets verbose
    args_dbg = parser.parse_args(["web", "--debug"])
    assert args_dbg.subcommand == "web"
    assert args_dbg.verbose is True

    # Test --open sets open
    args_open = parser.parse_args(["web", "--open"])
    assert args_open.open is True

    # Test default open is False and verbose is False
    args_default = parser.parse_args(["web"])
    assert args_default.verbose is False
    assert args_default.open is False
    assert args_default.direct is False

    # Test --direct and --no-proxy
    args_direct = parser.parse_args(["web", "--direct"])
    assert args_direct.direct is True

    args_no_proxy = parser.parse_args(["web", "--no-proxy"])
    assert args_no_proxy.direct is True


def test_server_api_web_status():
    app = create_app()
    client = TestClient(app)

    with patch("agy_proxy.web_launcher.get_web_status", return_value={
        "status": "running",
        "port": 44741,
        "url": "https://127.0.0.1:44741/",
        "pid": 11872,
        "electron_running": False,
        "estimated_ram_saved_mb": 1600,
    }):
        resp = client.get("/api/web/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["url"] == "https://127.0.0.1:44741/"
        assert data["pid"] == 11872
        assert data["estimated_ram_saved_mb"] == 1600


def test_server_api_web_launch():
    app = create_app()
    client = TestClient(app)

    with patch("agy_proxy.web_launcher.launch_antigravity_web", return_value=0):
        resp = client.post("/api/web/launch", json={"port": 44741, "no_open": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["url"] == "https://127.0.0.1:44741/"


def test_build_web_args_with_bundle(tmp_path):
    fake_bin = tmp_path / "language_server"
    bundle_dir = tmp_path / "web_ui"
    args = build_web_args(
        ls_path=fake_bin,
        port=44741,
        csrf_token="my-token",
        web_bundle_path=bundle_dir,
    )
    assert "--web_bundle_path" in args
    assert str(bundle_dir) in args


def test_extract_and_patch_web_bundle(tmp_path):
    import io
    import zipfile
    from agy_proxy.web_launcher import (
        is_bundle_patched,
        prepare_patched_web_bundle,
    )

    # 1. Create a dummy zip archive containing main.js and index.html
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        dummy_js = (
            'restrictToCorpDomain:!0; '
            'var tk=a=>a.modelOrAlias?{label:a.label,value:a.modelOrAlias.choice.case==="model"?a.modelOrAlias.choice.value:I.Model.UNSPECIFIED,modelAlias:a.modelOrAlias.choice.case==="alias"?a.modelOrAlias.choice.value:void 0; '
            'function gMa({children:a,userStatus:b}){return .Provider,{value:g},a)}'
        )
        z.writestr("main.js", dummy_js)
        z.writestr("index.html", '<html><head><title>Test</title></head><body></body></html>')

    zip_bytes = zip_buffer.getvalue()

    # 2. Embed this zip inside a dummy binary
    fake_bin = tmp_path / "language_server"
    fake_bin.write_bytes(b"\x7fELF_PREFIX_HEADER" + zip_bytes)
    os.chmod(fake_bin, 0o755)

    out_bundle = tmp_path / "extracted_bundle"
    assert not is_bundle_patched(out_bundle)

    # 3. Test prepare_patched_web_bundle
    res_dir = prepare_patched_web_bundle(fake_bin, bundle_dir=out_bundle, force=True)
    assert res_dir == out_bundle
    assert is_bundle_patched(out_bundle)

    # Check patched main.js content
    main_js_content = (out_bundle / "main.js").read_text()
    assert "MODEL_ENUM_MAP" in main_js_content
    assert "_sep_ordered_v16" in main_js_content
    assert "cleanTemplate" in main_js_content
    assert "gemini-3.1-flash-image" in main_js_content
    assert "tab_flash_lite_preview" in main_js_content
    assert "chat_20706" not in main_js_content
    assert "gemini-3-pro-image" not in main_js_content
    assert "gemini-2.5-flash-image" not in main_js_content
    assert "image/jpeg" in main_js_content
    assert "── Fast Completion (Unlimited) ───" in main_js_content
    assert "agy.selectedModelLabel" in main_js_content

    # Check patched index.html content
    index_html_content = (out_bundle / "index.html").read_text()
    assert "Auto-unlock Developer" in index_html_content
    assert "enable-jetbox-terminal-v2" in index_html_content


def test_cli_web_patched_flags():
    parser = build_parser()
    args_p = parser.parse_args(["web", "--patched", "-f", "-d", "/tmp/custom_bundle"])
    assert args_p.subcommand == "web"
    assert args_p.patched is True
    assert args_p.force_patch is True
    assert args_p.bundle_dir == "/tmp/custom_bundle"

    args_p_short = parser.parse_args(["web", "-P"])
    assert args_p_short.patched is True


def test_patch_web_bundle_dynamic_structural(tmp_path):
    from agy_proxy.web_launcher import patch_web_bundle

    bundle_dir = tmp_path / "dynamic_bundle"
    bundle_dir.mkdir()

    # JS with completely arbitrary minified identifiers
    dummy_js = (
        'restrictToCorpDomain:!0; '
        'var myConv=p=>p.modelOrAlias?{label:p.label,value:p.modelOrAlias.choice.case==="model"?p.modelOrAlias.choice.value:EnumNs.Model.UNSPECIFIED,modelAlias:p.modelOrAlias.choice.case==="alias"?p.modelOrAlias.choice.value:void 0; '
        'return ReactLib.createElement(ArbitraryItemComp,{"data-testid":"model-selector-item",value:123}); '
        'function customStateProv({children:a,userStatus:b,lastSelectedAgentModel:c,setLastSelectedAgentModel:e,supportsCustomModels:f,lastModelDefaultOverrideVersionId:g,setLastModelDefaultOverrideVersionId:h,customModelsState:k}){'
        'var [l,m]=(0,ReactLib.useState)(null),u=(0,ReactLib.useMemo)(()=>customHelper(k),[k]);'
        'return ReactLib.createElement(CustomContext.Provider,{value:g},a)}'
    )
    (bundle_dir / "main.js").write_text(dummy_js, encoding="utf-8")
    (bundle_dir / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")

    assert patch_web_bundle(bundle_dir) is True

    patched_js = (bundle_dir / "main.js").read_text(encoding="utf-8")
    # Verify dynamic converter patched
    assert "MODEL_ENUM_MAP[p.modelId||p.label]" in patched_js
    # Verify dynamic item renderer patched
    assert "ArbitraryItemComp" in patched_js
    assert "cleanTitle" in patched_js
    # Verify dynamic state provider patched
    assert "function customStateProv({" in patched_js
    assert "CustomContext.Provider" in patched_js
    assert "__enhanceUserStatus(b)" in patched_js
    assert "MODEL_ENUM_MAP" in patched_js


def test_patch_web_bundle_redux_toolkit(tmp_path):
    from agy_proxy.web_launcher import patch_web_bundle, is_bundle_patched

    bundle_dir = tmp_path / "redux_bundle"
    bundle_dir.mkdir()

    dummy_js = (
        "'use strict'; "
        "restrictToCorpDomain:!0; "
        'var myConv=p=>p.modelOrAlias?{label:p.label,value:p.modelOrAlias.choice.case==="model"?p.modelOrAlias.choice.value:EnumNs.Model.UNSPECIFIED,modelAlias:p.modelOrAlias.choice.case==="alias"?p.modelOrAlias.choice.value:void 0; '
        'return ReactLib.createElement(ArbitraryItemComp,{"data-testid":"model-selector-item",value:123}); '
        'const SCa=vk({name:"userStatus",initialState:()=>q(I.UserStatusSchema),reducers:{setUserStatus:(a,b)=>b.payload}}); '
        'const ICa=a=>a.userStatus?.cascadeModelConfigData; '
        'function Ls({visibleModelConfigs:a,customModels:b,modelSorts:c},e){if(a.length!==0||b.length!==0){var f=e?.model;if(f===I.Model.GOOGLE_GEMINI_INTERNAL_BYOM){if(a=(e?.customLabel?b.find(g=>g.label===e.customLabel):void 0)??b.at(0))return a}else if(f!==void 0&&(a=Sk(f,a)))return a;return c.at(0)?.groups.at(0)?.options.at(0)??b.at(0)}} '
        'var T=R.isCustom?R:xac(p,k,R.label);if(T){ '
    )
    (bundle_dir / "main.js").write_text(dummy_js, encoding="utf-8")
    (bundle_dir / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")

    assert patch_web_bundle(bundle_dir) is True

    patched_js = (bundle_dir / "main.js").read_text(encoding="utf-8")
    assert "MODEL_ENUM_MAP" in patched_js
    assert "__enhanceUserStatus(b.payload)" in patched_js
    assert "__enhanceUserStatus(a.userStatus)?.cascadeModelConfigData" in patched_js
    assert 'localStorage.getItem("agy.selectedModelLabel")' in patched_js
    assert 'localStorage.setItem("agy.selectedModelLabel",R.label)' in patched_js
    assert is_bundle_patched(bundle_dir) is True


def test_patch_web_bundle_graceful_degradation(tmp_path):
    from agy_proxy.web_launcher import patch_web_bundle

    bundle_dir = tmp_path / "unrecognized_bundle"
    bundle_dir.mkdir()

    # JS with no recognizable state provider at all
    dummy_js = (
        'restrictToCorpDomain:!0; '
        'console.log("Unrecognized bundle with different layout");'
    )
    (bundle_dir / "main.js").write_text(dummy_js, encoding="utf-8")
    (bundle_dir / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")

    # Should succeed without throwing exception
    assert patch_web_bundle(bundle_dir) is True

    patched_js = (bundle_dir / "main.js").read_text(encoding="utf-8")
    assert "restrictToCorpDomain:!1" in patched_js
    assert (bundle_dir / ".patched").is_file()


def test_sync_bundle_proxy_mode_direct_vs_proxy(tmp_path):
    from agy_proxy.web_launcher import (
        prepare_patched_web_bundle,
        sync_bundle_proxy_mode,
    )
    import io
    import zipfile

    # 1. Create a dummy binary with zip
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as z:
        dummy_js = 'restrictToCorpDomain:!0; var tk=a=>a.modelOrAlias?{label:a.label,value:1}; function gMa({children:a,userStatus:b}){return .Provider,{value:g},a)}'
        z.writestr("main.js", dummy_js)
        z.writestr("index.html", '<html><head><title>Test</title></head><body></body></html>')
    zip_bytes = zip_buffer.getvalue()

    fake_bin = tmp_path / "language_server"
    fake_bin.write_bytes(b"\x7fELF_PREFIX_HEADER" + zip_bytes)
    os.chmod(fake_bin, 0o755)

    out_bundle = tmp_path / "bundle_mode_test"

    # Test 1: prepare in direct mode (proxy_active=False)
    prepare_patched_web_bundle(fake_bin, bundle_dir=out_bundle, force=True, direct=True)
    html_direct = (out_bundle / "index.html").read_text()
    assert "window.__AGY_PROXY_ACTIVE__ = false;" in html_direct

    # Test 2: sync to proxy mode without re-extracting
    sync_bundle_proxy_mode(out_bundle, proxy_active=True)
    html_proxy = (out_bundle / "index.html").read_text()
    assert "window.__AGY_PROXY_ACTIVE__ = true;" in html_proxy

    # Test 3: prepare again with proxy_active=True and direct=False
    prepare_patched_web_bundle(fake_bin, bundle_dir=out_bundle, force=False, direct=False, proxy_active=True)
    html_proxy2 = (out_bundle / "index.html").read_text()
    assert "window.__AGY_PROXY_ACTIVE__ = true;" in html_proxy2

    # Check main.js has image models and conditional webModels
    js_content = (out_bundle / "main.js").read_text()
    assert "gemini-3.1-flash-image" in js_content
    assert "isProxyActive" in js_content


def test_enhance_user_status_direct_and_proxy_models(tmp_path):
    import json
    import subprocess
    import shutil
    from agy_proxy.web_launcher import patch_web_bundle

    bundle_dir = tmp_path / "model_test_bundle"
    bundle_dir.mkdir()
    dummy_js = 'restrictToCorpDomain:!0; var tk=a=>a.modelOrAlias?{label:a.label,value:1}; function gMa({children:a,userStatus:b}){return .Provider,{value:g},a)}'
    (bundle_dir / "main.js").write_text(dummy_js, encoding="utf-8")
    (bundle_dir / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")

    assert patch_web_bundle(bundle_dir) is True
    patched_js = (bundle_dir / "main.js").read_text(encoding="utf-8")

    # Verify all expected model IDs and enums are present in MODEL_ENUM_MAP
    assert '"gemini-4-argon":1421' in patched_js
    assert '"gemini-4-argon-high":1421' in patched_js
    assert '"gemini-4-argon-medium":1422' in patched_js
    assert '"gemini-4-argon-low":1423' in patched_js
    assert '"claude-sonnet-5-5-high":1515' in patched_js
    assert '"claude-opus-5-5-high":1518' in patched_js
    assert '"gemini-3.8-flash-deepagent":1522' in patched_js
    assert '"gemini-3.7-flash":1315' in patched_js
    assert '"gemini-3.6-flash-high":1524' in patched_js
    assert '"gemini-3.1-pro-high":1531' in patched_js

    # If node is installed, execute the patched enhanceUserStatus in both direct and proxy modes
    node_bin = shutil.which("node")
    if node_bin:
        start = patched_js.find("var MODEL_ENUM_MAP={")
        end = patched_js.find("};", patched_js.find("return b}}", start)) + 2
        enhancer_js = patched_js[start:end]

        test_script = f"""
        {enhancer_js}
        
        function runTest(isProxy) {{
            global.window = {{ __AGY_PROXY_ACTIVE__: isProxy }};
            var initialUserStatus = {{
                cascadeModelConfigData: {{
                    clientModelConfigs: [
                        {{ label: "Gemini 3.8 Flash (High)", disabled: false }},
                        {{ label: "Gemini 4 Argon", modelId: "gemini-4-argon-disabled", disabled: true }}
                    ],
                    clientModelSorts: [
                        {{ name: "Recommended", groups: [{{ modelLabels: ["Gemini 3.8 Flash (High)", "Gemini 4 Argon"] }}] }}
                    ]
                }}
            }};
            var enhanced = __enhanceUserStatus(initialUserStatus);
            var recGroup = enhanced.cascadeModelConfigData.clientModelSorts[0].groups[0];
            var configs = enhanced.cascadeModelConfigData.clientModelConfigs;
            return {{
                labels: recGroup.modelLabels,
                configs: configs.map(c => ({{ label: c.label, disabled: c.disabled, value: c.value }}))
            }};
        }}
        
        var directRes = runTest(false);
        var proxyRes = runTest(true);
        console.log(JSON.stringify({{ direct: directRes, proxy: proxyRes }}));
        """
        proc = subprocess.run([node_bin, "-e", test_script], capture_output=True, text=True, check=True)
        out = json.loads(proc.stdout)
        
        # In direct mode (offline / without proxy):
        direct_labels = out["direct"]["labels"]
        assert "── Default ──────────────────" in direct_labels
        assert "── Web ──────────────────" not in direct_labels
        assert "── Antigravity ───────────" in direct_labels
        assert "── Fast Completion (Unlimited) ───" in direct_labels
        assert "── Image Generation ─────────" in direct_labels
        
        # Verify key models are in direct mode
        assert "Gemini 4 Argon (High)" in direct_labels
        assert "Claude Sonnet 4.6 (Thinking)" in direct_labels
        assert "Claude Opus 4.6 (Thinking)" in direct_labels
        assert "Claude Sonnet 5.5 (High)" in direct_labels
        assert "Claude Opus 5.5 (High)" in direct_labels
        assert "Gemini 3.8 Flash (DeepAgent)" in direct_labels
        assert "Gemini 3.8 Flash (Tiered)" in direct_labels
        assert "Gemini 3.7 Flash (High)" in direct_labels
        assert "Gemini 3.6 Flash (High)" in direct_labels
        assert "Gemini 3.1 Pro (High)" in direct_labels
        assert "Gemini 2.5 Pro" in direct_labels
        assert "Gemini 3.1 Flash (Image)" in direct_labels
        assert "Tab Flash Lite Preview [Unlimited]" in direct_labels
        
        # Verify no Web models leaked into direct mode
        for lbl in direct_labels:
            assert "(Web)" not in lbl
            
        # Verify all models have disabled: false
        for cfg in out["direct"]["configs"]:
            if not cfg["label"].startswith("──"):
                assert cfg["disabled"] is False, f"Model {cfg['label']} should be enabled"
                
        # In proxy mode:
        proxy_labels = out["proxy"]["labels"]
        assert "── Web ──────────────────" in proxy_labels
        assert "Gemini 4 Argon (Web) (High)" in proxy_labels
        assert "Gemini 3.8 Flash (Web) (High)" in proxy_labels
        assert "Gemini 3.1 Pro (Web) (High)" in proxy_labels
        assert "Gemini 4 Argon (High)" in proxy_labels
        assert "Claude Sonnet 5.5 (High)" in proxy_labels


def test_can_open_browser_cooldown(tmp_path, monkeypatch):
    from agy_proxy.web_launcher import can_open_browser

    lock_file = tmp_path / ".config" / "agy-proxy" / ".browser_open_ts"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # First call should succeed and record timestamp
    assert can_open_browser(cooldown=5.0) is True
    assert lock_file.exists()

    # Immediate second call should be throttled
    assert can_open_browser(cooldown=5.0) is False

    # Simulate timestamp from 10 seconds ago
    import time
    old_time = time.time() - 10.0
    (tmp_path / ".config" / "agy-proxy" / ".browser_open_ts").write_text(str(old_time))

    # Now it should succeed again
    assert can_open_browser(cooldown=5.0) is True


def test_launch_antigravity_web_default_no_open(tmp_path):
    from agy_proxy.web_launcher import launch_antigravity_web

    mock_bin = tmp_path / "language_server"
    mock_bin.write_text("#!/bin/sh\nexit 0\n")
    mock_bin.chmod(0o755)

    with patch("agy_proxy.web_launcher.find_language_server", return_value=mock_bin), \
         patch("agy_proxy.web_launcher.get_running_instances", return_value={"electron_running": False, "language_server_running": True}), \
         patch("agy_proxy.web_launcher.is_port_in_use", return_value=True), \
         patch("agy_proxy.web_launcher.open_browser_async") as mock_open:

        # Default launch with open_browser=False
        res = launch_antigravity_web(open_browser=False, ls_path=mock_bin)
        assert res == 0
        mock_open.assert_not_called()

        # Explicit launch with open_browser=True
        res = launch_antigravity_web(open_browser=True, ls_path=mock_bin)
        assert res == 0
        mock_open.assert_called_once()


def test_detect_platform_key():
    from agy_proxy.web_launcher import detect_platform_key, DOWNLOAD_URLS
    key = detect_platform_key()
    # Must return a string or None
    assert key is None or isinstance(key, str)
    # On this test system (Linux x64), should return linux-x64
    import platform as _platform
    system = _platform.system().lower()
    machine = _platform.machine().lower()
    if system == "linux" and machine in ("x86_64", "amd64"):
        assert key == "linux-x64"
    elif system == "linux" and machine in ("aarch64", "arm64"):
        assert key == "linux-arm"
    # Verify all known keys have valid URL entries
    for pkey, (url, atype) in DOWNLOAD_URLS.items():
        assert url.startswith("https://")
        assert atype in ("tar.gz", "dmg", "exe")


def test_download_language_server_tar(tmp_path):
    """Simulates downloading and extracting a tar.gz archive with a fake language_server."""
    import io
    import tarfile as _tarfile
    from unittest.mock import patch
    from agy_proxy.web_launcher import download_language_server

    # Create a fake tar.gz in memory
    buf = io.BytesIO()
    with _tarfile.open(fileobj=buf, mode="w:gz") as tar:
        content = b"#!/bin/sh\necho language_server\n"
        info = _tarfile.TarInfo(name="Antigravity/resources/bin/language_server")
        info.size = len(content)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(content))
    tar_bytes = buf.getvalue()

    def fake_download(url, dest: Path):
        dest.write_bytes(tar_bytes)

    with patch("agy_proxy.web_launcher._download_with_progress", side_effect=fake_download):
        result = download_language_server(
            dest_dir=tmp_path / "bin",
            interactive=False,
            platform_key="linux-x64",
        )

    assert result is not None
    assert result.is_file()
    assert result.name == "language_server"
    assert os.access(result, os.X_OK)


def test_download_language_server_dmg_and_exe(tmp_path):
    """Verifies that DMG and EXE extractors are defined and handle missing 7z / invalid archives gracefully."""
    from agy_proxy.web_launcher import (
        _extract_language_server_from_dmg,
        _extract_language_server_from_exe,
    )

    dummy_file = tmp_path / "dummy.dmg"
    dummy_file.write_bytes(b"not a valid archive")

    # Graceful degradation on invalid archive
    res_dmg = _extract_language_server_from_dmg(dummy_file, tmp_path / "out_dmg")
    assert res_dmg is None

    res_exe = _extract_language_server_from_exe(dummy_file, tmp_path / "out_exe")
    assert res_exe is None


def test_cli_download_server_flag(tmp_path):
    """agy-proxy web --download-server flag should be parsed without error."""
    from agy_proxy.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["web", "--download-server"])
    assert args.download_server is True


def test_fetch_latest_hub_release_and_build_tag():
    import json
    from agy_proxy.web_launcher import (
        fetch_latest_hub_release,
        get_hub_build_tag,
        get_download_urls,
        _BUILD_TAG,
    )

    # 1. Test fallback when updater is unreachable or mock returns error
    with patch("urllib.request.urlopen", side_effect=Exception("network unreachable")):
        rel = fetch_latest_hub_release()
        assert rel is None
        tag = get_hub_build_tag(fetch_dynamic=True)
        assert tag == _BUILD_TAG

    # 2. Test successful dynamic release query
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps([
        {"version": "2.26.0", "execution_id": "6546645232517120"}
    ]).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        rel = fetch_latest_hub_release()
        assert rel is not None
        assert rel["version"] == "2.26.0"
        assert rel["execution_id"] == "6546645232517120"
        tag = get_hub_build_tag(fetch_dynamic=True)
        assert tag == "2.26.0-6546645232517120"

    # 3. Test get_download_urls
    urls = get_download_urls("2.26.0-12345")
    assert "linux-x64" in urls
    assert "2.26.0-12345" in urls["linux-x64"][0]


def test_patch_web_bundle_plugin_scheme(tmp_path):
    from agy_proxy.web_launcher import patch_web_bundle

    bundle = tmp_path / "web_bundle"
    bundle.mkdir()
    (bundle / "main.js").write_text("console.log('test');", encoding="utf-8")
    (bundle / "index.html").write_text("<html><head><title>Web Hub</title></head><body></body></html>", encoding="utf-8")

    success = patch_web_bundle(bundle)
    assert success is True

    patched_html = (bundle / "index.html").read_text(encoding="utf-8")
    assert "Plugin scheme compatibility for Web Hub" in patched_html
    assert "plugin://" in patched_html
    assert "/plugin/" in patched_html


