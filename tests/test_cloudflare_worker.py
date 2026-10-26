"""
Tests for Cloudflare Worker script (cloudflare/worker.js) and parity with agy-proxy.
"""

import json
import subprocess
from pathlib import Path
from agy_proxy.models import MODEL_ALIASES

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKER_JS = REPO_ROOT / "cloudflare" / "worker.js"


def test_worker_syntax_validity():
    """Validates that cloudflare/worker.js has valid ECMAScript syntax."""
    res = subprocess.run(["node", "-c", str(WORKER_JS)], capture_output=True, text=True)
    assert res.returncode == 0, f"Worker JS syntax error: {res.stderr}"


def test_worker_model_aliases_parity():
    """Ensures 100% parity between Python MODEL_ALIASES and Cloudflare worker MODEL_ALIASES."""
    script = """
    import fs from 'fs';
    import { createRequire } from 'module';
    const content = fs.readFileSync(process.argv[1], 'utf8');
    const aliasesMatch = content.match(/const MODEL_ALIASES = \\{([\\s\\S]*?)\\n\\};/);
    if (!aliasesMatch) {
      console.error("Could not find MODEL_ALIASES");
      process.exit(1);
    }
    const raw = aliasesMatch[1];
    const keys = [...raw.matchAll(/["']?([a-zA-Z0-9_.\\-]+)["']?\\s*:/g)].map(m => m[1]);
    console.log(JSON.stringify(keys));
    """
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(WORKER_JS)],
        capture_output=True,
        text=True,
        check=True,
    )
    worker_alias_keys = set(json.loads(res.stdout.strip()))
    py_alias_keys = set(MODEL_ALIASES.keys())

    missing = py_alias_keys - worker_alias_keys
    assert not missing, f"Aliases missing in cloudflare/worker.js: {sorted(missing)}"


def test_worker_endpoints_and_deepagent():
    """Verifies worker health version and listExperiments enable-deepagent injection."""
    node_test_script = f"""
    import worker from '{WORKER_JS}';

    async function run() {{
      // 1. Health check version
      const hReq = new Request('https://edge.workers.dev/health');
      const hRes = await worker.fetch(hReq, {{}});
      const hData = await hRes.json();
      if (hData.version !== '2.26.0') throw new Error('Expected 2.26.0 in health');

      // 2. Gateway listExperiments deepagent injection
      const origFetch = globalThis.fetch;
      globalThis.fetch = async () => new Response(JSON.stringify({{
        flags: [{{ name: 'enable-deepagent', boolValue: false }}]
      }}), {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }});

      try {{
        const expReq = new Request('https://edge.workers.dev/cloudcode/v1internal:listExperiments', {{
          method: 'POST',
          headers: {{ 'Authorization': 'Bearer test' }},
          body: JSON.stringify({{}})
        }});
        const expRes = await worker.fetch(expReq, {{}});
        const expData = await expRes.json();
        const flag = expData.flags.find(f => f.name === 'enable-deepagent');
        if (!flag || flag.boolValue !== true) {{
          throw new Error('Deepagent flag not set to true');
        }}

        // 3. Count tokens endpoint
        const ctReq = new Request('https://edge.workers.dev/v1/messages/count_tokens', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ model: 'claude-sonnet-4-6', messages: [{{ role: 'user', content: 'test message' }}] }})
        }});
        const ctRes = await worker.fetch(ctReq, {{}});
        const ctData = await ctRes.json();
        if (typeof ctData.input_tokens !== 'number' || ctData.input_tokens <= 0) {{
          throw new Error('count_tokens failed: ' + JSON.stringify(ctData));
        }}
      }} finally {{
        globalThis.fetch = origFetch;
      }}
    }}

    run().then(() => console.log('OK')).catch(err => {{
      console.error(err);
      process.exit(1);
    }});
    """
    res = subprocess.run(
        ["node", "--input-type=module", "-e", node_test_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Worker endpoint test failed: {res.stderr}\n{res.stdout}"
    assert "OK" in res.stdout
