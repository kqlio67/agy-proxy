"""
Diagnostic Doctor for Antigravity Proxy.
Performs comprehensive system, network, account pool, and CLI integration checks.
"""

import asyncio
import os
import shutil
import sys
import time
from typing import Any, Dict, List
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agy_proxy.auth import AccountPool, AccountSession, CLOUDCODE_BASE_URL, GENAI_BASE_URL, OAUTH_TOKEN_URL, quota_percentages

console = Console()


async def validate_account_live(acc: AccountSession) -> Dict[str, Any]:
    """
    Performs real authentication validation for a single account:
    refreshes/validates the OAuth token (or API key) against Google and fetches live quotas.
    """
    result: Dict[str, Any] = {"token_ok": None, "error": "", "quota_summary": {}}
    try:
        if acc.auth_method == "api_key":
            # Validate the key by listing models from Google AI Studio
            client = await acc.get_http_client()
            resp = await client.get(
                f"{GENAI_BASE_URL}/models?key={acc.api_key or acc.refresh_token}",
                timeout=10.0,
            )
            result["token_ok"] = resp.status_code == 200
            if not result["token_ok"]:
                result["error"] = f"API key rejected (HTTP {resp.status_code})"
        else:
            # Refresh if expired, then prove the token is accepted via userinfo
            await acc.get_valid_token()
            info = await acc.fetch_user_info()
            result["token_ok"] = bool(info)
            if not result["token_ok"]:
                result["error"] = "Token rejected by Google (userinfo check failed)"

        try:
            result["quota_summary"] = await acc.fetch_quota() or {}
        except Exception as qe:
            result["error"] = result["error"] or f"Quota fetch failed: {str(qe)[:40]}"
    except Exception as e:
        result["token_ok"] = False
        result["error"] = str(e)[:60]
    return result


async def check_network_endpoints(cloudflare_url: str = None) -> List[Dict[str, Any]]:
    """Checks latency and connectivity to Google and Cloudflare endpoints."""
    results = []
    
    endpoints = [
        ("Google OAuth Server", OAUTH_TOKEN_URL, "POST"),
        ("Google CloudCode API", f"{CLOUDCODE_BASE_URL}/fetchAvailableModels", "POST"),
        ("Google AI Studio API", f"{GENAI_BASE_URL}/models", "GET"),
    ]
    if cloudflare_url or os.environ.get("CLOUDFLARE_UPSTREAM_URL"):
        cf = cloudflare_url or os.environ.get("CLOUDFLARE_UPSTREAM_URL")
        endpoints.append(("Cloudflare Edge Proxy", cf.rstrip("/") + "/api/hello", "GET"))

    async with httpx.AsyncClient(timeout=8.0) as client:
        for name, url, method in endpoints:
            start = time.time()
            status_str = "Error"
            latency_ms = 0
            detail = ""
            try:
                if method == "POST":
                    resp = await client.post(url, json={})
                else:
                    resp = await client.get(url)
                latency_ms = int((time.time() - start) * 1000)
                # 200, 400, 401, 403, 404, 405 all mean server is reached and responsive
                if resp.status_code in (200, 400, 401, 403, 404, 405):
                    status_str = "OK"
                    detail = f"{resp.status_code} ({latency_ms}ms)"
                else:
                    status_str = "Degraded"
                    detail = f"HTTP {resp.status_code} ({latency_ms}ms)"
            except httpx.ConnectTimeout:
                status_str = "Timeout"
                detail = "Connection timed out (>8s)"
            except Exception as e:
                status_str = "Failed"
                detail = str(e)[:40]

            results.append({
                "name": name,
                "url": url,
                "status": status_str,
                "latency_ms": latency_ms,
                "detail": detail,
            })

    return results


def check_claude_cli() -> Dict[str, Any]:
    """Checks Claude Code CLI installation, binary path, and version."""
    claude_path = shutil.which("claude")
    if not claude_path:
        return {
            "installed": False,
            "path": None,
            "version": None,
            "detail": "Claude CLI not found in PATH. Install with `npm i -g @anthropic-ai/claude-code`",
        }

    import subprocess
    try:
        ver_output = subprocess.check_output([claude_path, "--version"], stderr=subprocess.STDOUT, text=True, timeout=3).strip()
    except Exception as e:
        ver_output = f"Detected ({e})"

    return {
        "installed": True,
        "path": claude_path,
        "version": ver_output,
        "detail": f"Installed at {claude_path}",
    }


async def run_doctor(host: str = "127.0.0.1", port: int = 8000, cloudflare_url: str = None) -> bool:
    """Executes full diagnostic suite and prints beautiful report."""
    console.print(Panel("[bold cyan]🩺 Antigravity Proxy Diagnostic Doctor[/bold cyan]\n[dim]Checking system health, network endpoints, account tokens, and CLI integrations...[/dim]", border_style="blue"))

    overall_healthy = True

    # 1. Environment & System Table
    env_table = Table(title="1. Environment & System", border_style="blue", show_header=True)
    env_table.add_column("Component", style="cyan")
    env_table.add_column("Status", justify="center")
    env_table.add_column("Details", style="white")

    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    env_table.add_row("Python Runtime", "[bold green]✅ OK[/bold green]", f"Python {py_ver} ({sys.executable})")

    token_dir = os.path.expanduser("~/.gemini/antigravity-cli")
    has_token_dir = os.path.isdir(token_dir)
    env_table.add_row(
        "Antigravity Token Storage",
        "[bold green]✅ OK[/bold green]" if has_token_dir else "[bold yellow]⚠️ Missing[/bold yellow]",
        token_dir if has_token_dir else "Not found (Will be created on first login)"
    )

    config_dir = os.path.expanduser("~/.config/agy-proxy")
    has_config = os.path.isdir(config_dir)
    env_table.add_row(
        "Proxy Config Storage",
        "[bold green]✅ OK[/bold green]" if has_config else "[dim]Created on start[/dim]",
        config_dir
    )

    console.print(env_table)
    console.print("")

    # 2. Network Reachability Table
    net_table = Table(title="2. Network Connectivity & Latency", border_style="blue", show_header=True)
    net_table.add_column("Endpoint", style="cyan")
    net_table.add_column("Status", justify="center")
    net_table.add_column("Latency / Detail", style="white")

    net_results = await check_network_endpoints(cloudflare_url=cloudflare_url)
    for res in net_results:
        st = res["status"]
        if st == "OK":
            st_badge = "[bold green]✅ Reachable[/bold green]"
        elif st == "Degraded":
            st_badge = "[bold yellow]⚠️ Degraded[/bold yellow]"
            overall_healthy = False
        else:
            st_badge = "[bold red]❌ Unreachable[/bold red]"
            overall_healthy = False
        net_table.add_row(res["name"], st_badge, res["detail"])

    console.print(net_table)
    console.print("")

    # 3. Account Pool & Quotas Table (with LIVE token validation)
    console.print("[dim]Validating account tokens against Google (live refresh + quota fetch)...[/dim]\n")
    pool_table = Table(title="3. Account Pool & Authentication", border_style="blue", show_header=True)
    pool_table.add_column("Account", style="cyan")
    pool_table.add_column("Type", style="magenta")
    pool_table.add_column("Status", justify="center")
    pool_table.add_column("Token Expiry", style="yellow")
    pool_table.add_column("Gemini Quota", style="green")
    pool_table.add_column("Claude Quota", style="yellow")

    pool = AccountPool()
    pool.load_accounts()

    # Live-validate every account concurrently
    live_results: Dict[str, Dict[str, Any]] = {}
    if pool.accounts:
        async def _safe_validate(a: AccountSession):
            try:
                return a.account_id, await validate_account_live(a)
            except Exception as e:
                return a.account_id, {"token_ok": False, "error": str(e)[:60], "quota_summary": {}}

        pairs = await asyncio.gather(*[_safe_validate(a) for a in pool.accounts.values()])
        live_results = dict(pairs)

    auth_failures = 0
    if not pool.accounts:
        pool_table.add_row("[bold red]No accounts found[/bold red]", "-", "[bold red]❌ Empty[/bold red]", "-", "-", "-")
        overall_healthy = False
    else:
        for acc in pool.accounts.values():
            live = live_results.get(acc.account_id, {})

            # Status merges enabled/disabled with the live token verdict
            if acc.disabled:
                status_text = "[yellow]⏸️ Disabled[/yellow]"
            elif live.get("token_ok") is True:
                status_text = "[bold green]✅ Verified[/bold green]"
            elif live.get("token_ok") is False:
                status_text = f"[bold red]❌ Auth Failed[/bold red] [dim]{live.get('error', '')}[/dim]"
                auth_failures += 1
            else:
                status_text = "[dim]? Unknown[/dim]"

            # Check token expiration from cached metadata
            if acc.auth_method == "api_key":
                expiry_text = "[dim]N/A (API Key)[/dim]"
            else:
                remaining_sec = int(acc.expiry_timestamp - time.time())
                if remaining_sec > 0:
                    expiry_text = f"Valid ({remaining_sec}s left)"
                else:
                    expiry_text = "[dim]Expired → refreshed just now[/dim]" if live.get("token_ok") else "[dim]Expired[/dim]"

            gemini_q, claude_q = quota_percentages(live.get("quota_summary") or {})
            if gemini_q == "?" and claude_q == "?":
                gemini_q = "[dim]n/a[/dim]"
                claude_q = "[dim]n/a[/dim]"

            pool_table.add_row(
                f"{acc.name or acc.email} {'⭐' if acc.is_primary else ''}",
                "Google AI Studio" if acc.auth_method == "api_key" else "Google OAuth",
                status_text,
                expiry_text,
                gemini_q,
                claude_q,
            )

    if auth_failures:
        overall_healthy = False

    console.print(pool_table)
    console.print("")

    # 4. Coding Agent CLI Integration
    agent_table = Table(title="4. Claude Code CLI Integration", border_style="blue", show_header=True)
    agent_table.add_column("Check", style="cyan")
    agent_table.add_column("Status", justify="center")
    agent_table.add_column("Details", style="white")

    cli_info = check_claude_cli()
    if cli_info["installed"]:
        agent_table.add_row("Claude Code CLI Binary", "[bold green]✅ Found[/bold green]", f"{cli_info['version']} ({cli_info['path']})")
    else:
        agent_table.add_row("Claude Code CLI Binary", "[bold yellow]⚠️ Not Found[/bold yellow]", cli_info["detail"])

    launcher_path = os.path.expanduser("~/.local/bin/claude-agy")
    has_launcher = os.path.isfile(launcher_path)
    agent_table.add_row(
        "Launcher Command (`claude-agy`)",
        "[bold green]✅ Ready[/bold green]" if has_launcher else "[dim]Optional (in project dir: ./run_claude.sh)[/dim]",
        launcher_path if has_launcher else "Run `./run_claude.sh` from project folder"
    )

    console.print(agent_table)
    console.print("")

    # Final Verdict Panel
    if overall_healthy and pool.accounts:
        verdict = Panel(
            "[bold green]🎉 ALL SYSTEMS OPERATIONAL AND HEALTHY![/bold green]\n"
            f"[white]• Web Dashboard:[/white] [cyan]http://{host}:{port}[/cyan]\n"
            f"[white]• OpenAI API Base:[/white] [yellow]http://{host}:{port}/v1[/yellow]\n"
            f"[white]• Claude Code Launcher:[/white] [magenta]claude-agy[/magenta] or [magenta]./run_claude.sh[/magenta]",
            title="[bold green]Doctor Verdict[/bold green]",
            border_style="green",
            padding=(1, 2),
        )
    else:
        verdict = Panel(
            "[bold yellow]⚠️ SOME CHECKS NEED ATTENTION[/bold yellow]\n"
            "[white]Review the warnings above to ensure optimal performance.[/white]",
            title="[bold yellow]Doctor Verdict[/bold yellow]",
            border_style="yellow",
            padding=(1, 2),
        )

    console.print(verdict)
    return overall_healthy
