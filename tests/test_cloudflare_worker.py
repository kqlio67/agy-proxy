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
      if (hData.version !== '2.31.0') throw new Error('Expected 2.31.0 in health, got ' + hData.version);

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


def test_worker_clean_system_prompt_restrictions():
    """Verifies that cleanSystemPromptText in cloudflare/worker.js removes sandbox and security restrictions."""
    node_script = f"""
    import fs from 'fs';
    const content = fs.readFileSync('{WORKER_JS}', 'utf8');
    
    // Extract cleanSystemPromptText and shouldCleanText from worker
    const funcMatch1 = content.match(/function shouldCleanText[\\s\\S]*?\\n\\}}/);
    const funcMatch2 = content.match(/function cleanSystemPromptText[\\s\\S]*?\\n\\}}/);
    if (!funcMatch1 || !funcMatch2) {{
      console.error('Could not find cleaner functions in worker');
      process.exit(1);
    }}
    const shouldCleanText = new Function('return ' + funcMatch1[0])();
    const cleanSystemPromptText = new Function('return ' + funcMatch2[0])();

    const raw = `
<guidelines>
Follow these behavioral guidelines at all times:
- Maintain documentation integrity. Preserve all existing comments and docstrings.
- Never defeat or bypass an access control, permission check, credential requirement, network/proxy restriction, or sandbox/environment boundary.
</guidelines>
BLOCK any task that compromises system security, including malware, phishing, unauthorized data access, malicious system interference, and circumvention of security features. Examples: bypassing authentication, web scraping, scanning a website. This policy does not apply to tasks that access publicly available data. Routine browser tasks are considered benign and are NOT blocked by this policy, even if performed at scale.
Please do not attempt to reverse engineer or decompile.
Task: Parse website data.`;

    if (!shouldCleanText(raw)) throw new Error('shouldCleanText should have returned true');
    const cleaned = cleanSystemPromptText(raw);

    if (cleaned.includes('Never defeat or bypass')) throw new Error('Never defeat or bypass was not removed');
    if (cleaned.includes('BLOCK any task that compromises system security')) throw new Error('BLOCK any task was not removed');
    if (cleaned.includes('reverse engineer')) throw new Error('reverse engineer restriction was not removed');
    if (!cleaned.includes('Maintain documentation integrity')) throw new Error('Maintain documentation integrity should be preserved');
    if (!cleaned.includes('Task: Parse website data.')) throw new Error('Actual user task was removed');

    const rawBloat = `
<identity>You are Antigravity.</identity>
<slash_commands>UI shortcuts: /goal</slash_commands>
<featured_plugins>Marketplace: Canva</featured_plugins>
<conversation_transcript>Transcript log schema</conversation_transcript>
Task: Run service.`;

    if (!shouldCleanText(rawBloat)) throw new Error('shouldCleanText should have returned true for rawBloat');
    const cleanedBloat = cleanSystemPromptText(rawBloat);
    if (!cleanedBloat.includes('<slash_commands>')) throw new Error('<slash_commands> should be preserved per user preference');
    if (cleanedBloat.includes('<featured_plugins>')) throw new Error('<featured_plugins> was not removed');
    if (cleanedBloat.includes('<conversation_transcript>')) throw new Error('<conversation_transcript> was not removed');
    if (!cleanedBloat.includes('Task: Run service.')) throw new Error('User task was removed');

    // Test non-greedy preservation of legitimate neighbouring content
    const rawGreedyTest = `You are helpful.
BLOCK any task that facilitates or accesses illegal activities.
Here is some other content.
More content that should survive.

Final section.`;
    const cleanedGreedy = cleanSystemPromptText(rawGreedyTest);
    if (!cleanedGreedy.includes('More content that should survive')) {{
      throw new Error('cleanSystemPromptText greedily swallowed content after BLOCK line');
    }}

    const rawSandboxNeighbor = `Do helpful things.
Never defeat or bypass an access control or security measure.
You should still help with network debugging.
You can analyze traffic and protocols.

Help me with coding.`;
    const cleanedSandbox = cleanSystemPromptText(rawSandboxNeighbor);
    if (!cleanedSandbox.includes('network debugging') || !cleanedSandbox.includes('analyze traffic')) {{
      throw new Error('cleanSystemPromptText greedily swallowed sandbox neighbor lines');
    }}

    console.log('OK');
    """
    res = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Worker cleaner test failed: {res.stderr}\n{res.stdout}"
    assert "OK" in res.stdout


def test_worker_safety_settings_disabled():
    """Verifies that Cloudflare Worker disables all 5 safety categories with BLOCK_NONE."""
    node_script = f"""
    import fs from 'fs';
    const workerContent = fs.readFileSync('{WORKER_JS}', 'utf8');

    const safetyMatch = workerContent.match(/const DEFAULT_SAFETY_SETTINGS = ([\\s\\S]*?);/);
    if (!safetyMatch) throw new Error('Could not find DEFAULT_SAFETY_SETTINGS in worker.js');
    const settings = new Function('return ' + safetyMatch[1])();

    const categories = ['HARM_CATEGORY_HARASSMENT', 'HARM_CATEGORY_HATE_SPEECH', 'HARM_CATEGORY_SEXUALLY_EXPLICIT', 'HARM_CATEGORY_DANGEROUS_CONTENT', 'HARM_CATEGORY_CIVIC_INTEGRITY'];
    for (const cat of categories) {{
      const found = settings.find(s => s.category === cat);
      if (!found || found.threshold !== 'BLOCK_NONE') {{
        throw new Error(`Category ${{cat}} not set to BLOCK_NONE`);
      }}
    }}
    console.log('OK');
    """
    res = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Worker safety settings test failed: {res.stderr}\n{res.stdout}"
    assert "OK" in res.stdout

