"""
Command-line interface and entry point for Antigravity Proxy.
Supports starting the proxy server and interactive Multi-Account authentication commands.
"""

import argparse
import asyncio
import logging
import os
import re
import sys
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from agy_proxy import __version__ as APP_VERSION
from agy_proxy.auth import AccountPool
from agy_proxy.server import create_app

console = Console()


def print_banner(host: str, port: int, pool: AccountPool, api_key: str = None, update_info: dict = None):
    url = f"http://{host}:{port}"
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", no_wrap=True)
    table.add_column("Value")
    table.add_row("[bold cyan]Web Dashboard & Pool UI:[/bold cyan]", f"[bold green]{url}[/bold green]")
    table.add_row("[bold cyan]Universal API Base (OpenAI/Claude):[/bold cyan]", f"[bold yellow]{url}/v1[/bold yellow]")
    table.add_row("[bold cyan]Gemini Native API Base:[/bold cyan]", f"[bold blue]{url}/v1beta[/bold blue]")
    table.add_row("[bold cyan]Prompt Caching & Session Affinity:[/bold cyan]", "[bold green]Enabled (75% token discount)[/bold green]")
    try:
        from agy_proxy.compactor import compactor_settings
        if compactor_settings.enabled:
            comp_status = f"[bold green]Active[/bold green] [dim]({compactor_settings.threshold_tokens:,} tokens threshold)[/dim]"
        else:
            comp_status = "[bold green]Safe Mode (Disabled)[/bold green] [dim](managed by Claude Code)[/dim]"
        table.add_row("[bold cyan]Context Auto-Compactor:[/bold cyan]", comp_status)

        if compactor_settings.pruning_enabled:
            prune_status = f"[bold green]Active[/bold green] [dim](keep {compactor_settings.prune_keep_tools} tools, max {compactor_settings.prune_max_chars:,} chars)[/dim]"
        else:
            prune_status = "[bold green]Safe Mode (Disabled)[/bold green] [dim](full tool history preserved)[/dim]"
        table.add_row("[bold cyan]Smart Tool Pruning:[/bold cyan]", prune_status)
    except Exception:
        pass

    cooldown_val = float(os.environ.get("AGY_RATE_LIMIT_COOLDOWN", "0"))
    if cooldown_val > 0:
        cd_status = f"[bold yellow]{cooldown_val:.0f}s[/bold yellow]"
    else:
        cd_status = "[bold green]Disabled (0s - instant retry)[/bold green]"
    table.add_row("[bold cyan]Rate-Limit 429 Cooldown:[/bold cyan]", cd_status)
    active_accs = [a for a in pool.accounts.values() if a.enabled]
    paused_accs = [a for a in pool.accounts.values() if not a.enabled]
    if paused_accs:
        pool_status = f"[bold white]{len(active_accs)} active account(s)[/bold white] [dim yellow]({len(paused_accs)} paused)[/dim yellow]"
    else:
        pool_status = f"[bold white]{len(active_accs)} active account(s)[/bold white]"
    table.add_row("[bold cyan]Accounts in Pool:[/bold cyan]", pool_status)

    for acc in pool.accounts.values():
        if acc.enabled:
            tag = "[bold green](Primary)[/bold green]" if acc.is_primary else "[blue](Secondary)[/blue]"
            email = f"[white]{acc.email}[/white]" if acc.email else "[dim]unknown@gmail.com[/dim]"
            proj = f"[dim]Project: {acc.project_id or 'default'}[/dim]"
        else:
            tag = "[dim yellow](Paused - Primary)[/dim yellow]" if acc.is_primary else "[dim yellow](Paused)[/dim yellow]"
            email = f"[dim strike]{acc.email}[/dim strike]" if acc.email else "[dim](paused)[/dim]"
            proj = f"[dim]Project: {acc.project_id or 'default'} [PAUSED][/dim]"
        table.add_row(f"  • {tag} {email}", proj)

    if api_key:
        table.add_row("[bold cyan]Proxy API Key:[/bold cyan]", f"[bold red]{api_key}[/bold red]")
    else:
        table.add_row("[bold cyan]Proxy API Key:[/bold cyan]", "[dim](None - Open access)[/dim]")

    if update_info and update_info.get("has_update"):
        table.add_row(
            "[bold yellow]Update Available:[/bold yellow]",
            f"[bold green]v{update_info.get('latest_version')}[/bold green] (current: v{update_info.get('current_version')}) - [underline blue]{update_info.get('release_url')}[/underline blue]",
        )

    panel = Panel(
        table,
        title=f"[bold white]Google Antigravity AI Proxy [dim](v{APP_VERSION})[/dim][/bold white]",
        border_style="blue",
        subtitle="[dim]Press Ctrl+C to stop[/dim]",
        padding=(1, 2),
    )
    console.print(panel)


async def handle_auth_login():
    """Interactive PKCE OAuth login command for adding Google Accounts to the pool."""
    console.print(Panel("[bold cyan]Google Antigravity OAuth Login[/bold cyan]", border_style="blue"))
    pool = AccountPool()
    pool.load_accounts()

    flow = pool.start_oauth_flow()
    auth_url = flow["auth_url"]
    state = flow["state"]
    verifier = flow["code_verifier"]

    console.print("\n[bold yellow]Step 1:[/bold yellow] Open the following URL in your browser to authorize:")
    console.print(f"[underline blue]{auth_url}[/underline blue]\n")

    console.print("[bold yellow]Step 2:[/bold yellow] After authorizing, copy and paste the authorization code (or the full redirect URL):")
    try:
        code_input = input("Authorization Code / URL: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Login cancelled by user.[/dim]")
        return

    if not code_input:
        console.print("[bold red]Aborted: No input entered.[/bold red]")
        return

    console.print("\n[dim]Exchanging authorization code for OAuth tokens...[/dim]")
    try:
        acc = await pool.complete_oauth_flow(code_or_url=code_input, verifier=verifier, state=state)
        console.print(f"[bold green]✓ Successfully added account:[/bold green] [bold white]{acc.email}[/bold white] (ID: {acc.account_id})")
        console.print(f"Project: [cyan]{acc.project_id}[/cyan], Tier: [magenta]{acc.tier_info.get('name', 'Antigravity')}[/magenta]")
    except Exception as e:
        console.print(f"[bold red]✗ Failed to add account:[/bold red] {e}")


async def handle_auth_apikey(api_key: str = None, name: str = None):
    """Command for adding a Google AI Studio API Key to the pool."""
    console.print(Panel("[bold cyan]Add Google AI Studio API Key[/bold cyan]", border_style="blue"))
    pool = AccountPool()
    pool.load_accounts()

    if not api_key:
        try:
            api_key = input("Enter Gemini API Key (AIza...): ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Cancelled by user.[/dim]")
            return
    if not api_key:
        console.print("[bold red]Aborted: No key entered.[/bold red]")
        return

    if not name:
        try:
            display_name = input("Optional Account Name [Gemini API Key]: ").strip() or "Gemini API Key"
        except (KeyboardInterrupt, EOFError):
            display_name = "Gemini API Key"
    else:
        display_name = name

    console.print("\n[dim]Validating API Key with Google AI Studio...[/dim]")
    try:
        acc = await pool.add_api_key_account(api_key=api_key, name=display_name)
        console.print(f"[bold green]✓ Successfully added API Key account:[/bold green] [bold white]{acc.name}[/bold white] ({acc.email})")
        console.print(f"Discovered Models: [cyan]{len(acc.available_models)} model(s)[/cyan]")
    except Exception as e:
        console.print(f"[bold red]✗ Failed to add API Key:[/bold red] {e}")


async def handle_auth_list():
    """Lists all accounts in the pool and their quota fractions."""
    pool = AccountPool()
    pool.load_accounts()
    await pool.initialize_all()

    table = Table(title="Antigravity Account Pool", border_style="blue")
    table.add_column("Account / Name", style="cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Identity / Email", style="white")
    table.add_column("Gemini Quota", style="green")
    table.add_column("Claude/3P Quota", style="yellow")
    table.add_column("Requests", justify="right")

    for acc in pool.accounts.values():
        is_api_key = acc.auth_method == "api_key"
        acc_type = "API Key" if is_api_key else "OAuth"
        display_name = acc.name or ("Primary Account" if acc.is_primary else acc.account_id)
        if acc.is_primary:
            display_name += " ⭐"

        if is_api_key:
            if acc.error_message:
                gemini_q = "[bold red]Invalid/Expired[/bold red]"
            else:
                gemini_q = "[bold green]PayG Active[/bold green]"
            claude_q = "[dim]N/A[/dim]"
        else:
            q_details = acc.get_quota_details()
            g_5h = int(q_details.get("gemini", {}).get("5h", {}).get("percent", q_details.get("gemini", {}).get("percent", 100)))
            g_wk = int(q_details.get("gemini", {}).get("weekly", {}).get("percent", q_details.get("gemini", {}).get("percent", 100)))
            c_wk = int(q_details.get("3p", {}).get("weekly", {}).get("percent", q_details.get("3p", {}).get("percent", 100)))
            c_5h = int(q_details.get("3p", {}).get("5h", {}).get("percent", q_details.get("3p", {}).get("percent", 100)))

            gemini_q = f"5h: [bold red]{g_5h}%[/bold red] | Wk: {g_wk}%" if g_5h <= 0 else f"5h: {g_5h}% | Wk: {g_wk}%"
            claude_q = f"Wk: [bold red]{c_wk}%[/bold red]" if c_wk <= 0 else f"Wk: {c_wk}%"
            if c_5h < 100:
                claude_q += f" (5h: {c_5h}%)"

        table.add_row(
            display_name,
            acc_type,
            acc.email or "N/A",
            gemini_q,
            claude_q,
            str(acc.total_requests),
        )

    console.print(table)


async def handle_switch_command(
    target: str | None = None,
    to_next: bool = False,
    list_only: bool = False,
    set_primary: bool = True,
    target_env: str = "both",
    env_explicitly_set: bool = False,
):
    """Handles CLI session switching."""
    from agy_proxy.switcher import switch_antigravity_session

    pool = AccountPool()
    pool.load_accounts()

    oauth_accounts = [a for a in pool.accounts.values() if a.auth_method == "consumer" and a.refresh_token]
    if not oauth_accounts:
        console.print("[bold red]No Google OAuth accounts found in accounts.json.[/bold red]")
        console.print("Add an account first via: [cyan]agy-proxy auth login[/cyan]")
        return

    if list_only or (not target and not to_next):
        from agy_proxy.switcher import get_active_antigravity_accounts
        cli_acc, ide_acc = get_active_antigravity_accounts(pool)
        cli_id = cli_acc.account_id if cli_acc else None
        ide_id = ide_acc.account_id if ide_acc else None

        table = Table(title="Google Antigravity Accounts in Pool", show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("Account / Email", style="white")
        table.add_column("Name", style="green")
        table.add_column("Status", style="yellow")

        for idx, acc in enumerate(oauth_accounts, start=1):
            if acc.account_id == cli_id and acc.account_id == ide_id:
                status = "[bold green]Active (CLI & IDE) ⭐[/bold green]"
            elif acc.account_id == cli_id:
                status = "[bold green]Active (CLI) ⭐[/bold green]"
            elif acc.account_id == ide_id:
                status = "[bold green]Active (IDE) ⭐[/bold green]"
            else:
                status = "[dim]Ready[/dim]"
            
            table.add_row(str(idx), acc.email or acc.account_id, acc.name or "", status)

        console.print(table)
        if list_only:
            return

        choice = Prompt.ask("\nEnter account #, email, name, or 'next' to rotate", default="next")
        if not choice or choice.lower() in ("next", "n"):
            to_next = True
            target = None
        else:
            to_next = False
            target = choice

        if not env_explicitly_set:
            target_env = Prompt.ask(
                "Target destination environment",
                choices=["both", "cli", "ide"],
                default="both",
            )

    try:
        env_label = "CLI only" if target_env == "cli" else ("IDE only" if target_env == "ide" else "Both CLI & IDE")
        console.print(f"[dim]Switching Antigravity session [{env_label}]...[/dim]")
        acc, paths = await switch_antigravity_session(
            identifier=target,
            pool=pool,
            to_next=to_next,
            target_env=target_env,
            allow_overwrite=True,
            set_primary=set_primary,
        )
        paths_str = ", ".join(str(p) for p in paths)
        console.print(f"\n[bold green]✓ Activated Antigravity Session:[/bold green] [bold white]{acc.email}[/bold white] ({acc.name or 'OAuth'}) [cyan][{env_label}][/cyan]")
        console.print(f"Updated token destination: [cyan]{paths_str}[/cyan]")
        if target_env == "cli":
            console.print("[dim]Your `agy` CLI commands will now execute under this account.[/dim]")
        elif target_env == "ide":
            console.print("[dim]Your Antigravity IDE editor will now execute under this account.[/dim]")
        else:
            console.print("[dim]Your `agy` CLI and IDE commands will now execute under this account.[/dim]")
        console.print("[dim]Run [cyan]agy-proxy usage[/cyan] or [cyan]python switcher.py usage[/cyan] to view model quota usage.[/dim]")
    except Exception as e:
        console.print(f"[bold red]✗ Failed to switch session:[/bold red] {e}")


async def handle_quota_command(target: str | None = None):
    """Displays models and quota for the target account (or all accounts) matching agy format."""
    from agy_proxy.switcher import format_agy_quota_display

    pool = AccountPool()
    pool.load_accounts()

    oauth_accounts = [a for a in pool.accounts.values() if a.auth_method == "consumer" and a.refresh_token]
    if not oauth_accounts:
        console.print("[bold red]No Google OAuth accounts found in accounts.json.[/bold red]")
        return

    if target:
        acc = pool.get_account(target)
        if not acc:
            console.print(f"[bold red]Account not found:[/bold red] {target}")
            return
        accounts_to_show = [acc]
    else:
        # Show all OAuth accounts when no target specified
        accounts_to_show = oauth_accounts

    for acc in accounts_to_show:
        try:
            if hasattr(acc, "fetch_quota"):
                await acc.fetch_quota()
        except Exception:
            pass
        console.print()
        console.print(format_agy_quota_display(acc))
        if len(accounts_to_show) > 1:
            console.print("[dim]" + "─" * 60 + "[/dim]")


async def handle_update_command(check_only: bool = False):

    """Checks for releases and performs self-update."""
    from agy_proxy.updater import check_for_updates, perform_self_update

    console.print(Panel("[bold cyan]Antigravity Proxy Update Manager[/bold cyan]", border_style="blue"))
    console.print("Checking GitHub Releases...")
    info = await check_for_updates(force=True)

    curr_v = info.get("current_version", APP_VERSION)
    latest_v = info.get("latest_version", APP_VERSION)
    has_update = info.get("has_update", False)

    console.print(f"Current Version: [bold white]v{curr_v}[/bold white]")
    console.print(f"Latest Version:  [bold green]v{latest_v}[/bold green]")

    if not has_update:
        console.print("\n[bold green]✅ You are on the latest version of Antigravity Proxy![/bold green]")
        return

    console.print(f"\n[bold yellow]🚀 New update available:[/bold yellow] [bold green]v{latest_v}[/bold green]")
    if info.get("release_name"):
        console.print(f"Release: [bold white]{info.get('release_name')}[/bold white]")
    if info.get("release_url"):
        console.print(f"Release URL: [underline blue]{info.get('release_url')}[/underline blue]")

    if check_only:
        return

    console.print("\n📦 Pulling latest update...")
    res = await perform_self_update()
    if res.get("success"):
        console.print(f"[bold green]✅ Successfully updated to latest version via {res.get('method')}![/bold green]")
        if res.get("output"):
            console.print(f"[dim]{res.get('output')}[/dim]")
        console.print("\n[bold cyan]Restart your proxy server to apply changes:[/bold cyan] `agy-proxy`")
    else:
        console.print(f"[bold red]❌ Automatic update failed:[/bold red] {res.get('output') or res.get('error')}")
        console.print("\n[bold yellow]Manual update command:[/bold yellow] `git pull origin main`")

def handle_setup_codex(port: int = 8000, model: str = "gemini-3.8-flash-high"):
    """Configures Codex CLI to use Antigravity Proxy with the full 25+ model catalog."""
    from agy_proxy.codex_helper import setup_codex
    res = setup_codex(port=port, model=model)
    backup_text = f"\n• [bold cyan]Backup Saved:[/bold cyan] [green]{res['backup_path']}[/green]" if res.get("backup_created") else ""
    console.print(Panel(
        f"[bold green]✓ Codex CLI Successfully Configured![/bold green]\n\n"
        f"• [bold cyan]Model Catalog:[/bold cyan] [white]{res['catalog_path']}[/white] ({res['models_count']} models)\n"
        f"• [bold cyan]Configuration:[/bold cyan] [white]{res['config_path']}[/white]\n"
        f"• [bold cyan]Proxy Endpoint:[/bold cyan] [yellow]{res['proxy_url']}[/yellow]\n"
        f"• [bold cyan]Default Model:[/bold cyan] [magenta]{res['model']}[/magenta]"
        f"{backup_text}\n\n"
        f"[dim]All 25+ models are now available in Codex CLI's [/dim][bold cyan]/model[/bold cyan][dim] menu.[/dim]\n"
        f"[dim]To restore your original config anytime, run:[/dim] [bold yellow]agy-proxy restore-codex[/bold yellow]",
        title="[bold white]Codex CLI Integration[/bold white]",
        border_style="green",
    ))


def handle_restore_codex():
    """Restores the original Codex CLI configuration from backup."""
    from agy_proxy.codex_helper import restore_codex
    res = restore_codex()
    if res.get("restored_from_backup"):
        console.print("[bold green]✓ Successfully restored original ~/.codex/config.toml from backup![/bold green]")
    elif res.get("cleaned_config"):
        console.print("[bold green]✓ Cleaned up Antigravity Proxy settings from ~/.codex/config.toml.[/bold green]")
    else:
        console.print("[dim]No active Antigravity Proxy settings found in ~/.codex/config.toml.[/dim]")


def handle_setup_claude(port: int = 8000, model: str = "anthropic.gemini-3.8-flash-high", url: str | None = None):
    """Configures Claude Code CLI to route through Antigravity Proxy with zero 'Not logged in' errors."""
    from agy_proxy.claude_helper import setup_claude
    res = setup_claude(port=port, model=model, proxy_url=url)
    if res.get("ok"):
        backup_text = f"\n• [bold cyan]Backup Saved:[/bold cyan] [green]{res['backup_path']}[/green]" if res.get("backup_created") else ""
        console.print(Panel(
            f"[bold green]✓ Claude Code CLI Successfully Configured![/bold green]\n\n"
            f"• [bold cyan]Settings File:[/bold cyan] [white]{res['settings_path']}[/white]\n"
            f"• [bold cyan]Proxy Endpoint:[/bold cyan] [yellow]{res['proxy_url']}[/yellow]\n"
            f"• [bold cyan]Default Model:[/bold cyan] [magenta]{res['model']}[/magenta]\n"
            f"• [bold cyan]Authentication:[/bold cyan] [green]Logged In (oauth_token - background daemons enabled)[/green]"
            f"{backup_text}\n\n"
            f"[dim]Foreground sessions and all background agents will now automatically route through Antigravity Proxy.[/dim]\n"
            f"[dim]To restore original settings anytime, run:[/dim] [bold yellow]agy-proxy restore-claude[/bold yellow]",
            title="[bold white]Claude Code Integration[/bold white]",
            border_style="green",
        ))
    else:
        console.print(f"[bold red]✗ Failed to configure Claude Code:[/bold red] {res.get('error')}")


def handle_restore_claude():
    """Restores the original Claude Code CLI settings.json from backup."""
    from agy_proxy.claude_helper import restore_claude
    res = restore_claude()
    if res.get("ok"):
        console.print(f"[bold green]✓ Successfully restored Claude Code settings from {res.get('restored_from')}![/bold green]")
    else:
        console.print(f"[bold red]✗ Failed to restore Claude Code settings:[/bold red] {res.get('error')}")


def handle_run_codex(port: int = 8000, model: str = "gemini-3.8-flash-high", extra_args: list = None):
    """Launches Codex CLI in ephemeral test mode with proxy and catalog without modifying ~/.codex/config.toml."""
    import shutil
    import subprocess

    codex_bin = shutil.which("codex")
    if not codex_bin:
        default_local = os.path.expanduser("~/.local/bin/codex")
        if os.path.isfile(default_local) and os.access(default_local, os.X_OK):
            codex_bin = default_local

    if not codex_bin:
        console.print("[bold red]Error:[/bold red] Codex CLI ('codex') is not found in PATH or ~/.local/bin/codex.")
        console.print("[dim]Install Codex CLI first, e.g. via npm or standalone installer.[/dim]")
        return

    catalog_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "codex_models.json")
    if not os.path.isfile(catalog_path):
        catalog_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "codex_models.json")

    cmd = [
        codex_bin,
        "-c", f'openai_base_url="http://127.0.0.1:{port}/v1"',
        "-c", f'model_catalog_json="{catalog_path}"',
        "-m", model,
    ]
    if extra_args:
        cmd.extend(extra_args)

    env = dict(os.environ)
    if "OPENAI_API_KEY" not in env:
        env["OPENAI_API_KEY"] = "dummy"
    # Redirect Codex OpenTelemetry metrics to proxy sink (absorbed locally, never forwarded)
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"http://127.0.0.1:{port}"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/json"

    console.print("[bold cyan]Launching Codex CLI (Ephemeral Mode - Zero Config Changes):[/bold cyan]\n")
    try:
        subprocess.run(cmd, env=env)
    except KeyboardInterrupt:
        pass


def build_parser() -> argparse.ArgumentParser:
    """Constructs the argument parser for agy-proxy CLI."""
    parser = argparse.ArgumentParser(
        description=f"Antigravity Proxy (v{APP_VERSION}) - OpenAI & Anthropic compatible API server for Antigravity with Multi-Account pooling."
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version=f"%(prog)s v{APP_VERSION}",
        help="Show program version number and exit",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # update subcommand (agy-proxy update)
    update_parser = subparsers.add_parser("update", help="Check and install latest Antigravity Proxy updates")
    update_parser.add_argument("--check", "-c", action="store_true", help="Only check for updates without installing")

    # bump subcommand (agy-proxy bump [patch|minor|major|<version>] [--dry-run])
    bump_parser = subparsers.add_parser("bump", help="Bump project version across __init__.py and pyproject.toml")
    bump_parser.add_argument("target", nargs="?", default="patch", help="Increment component (patch, minor, major) or explicit semver (e.g. 1.4.1)")
    bump_parser.add_argument("--dry-run", action="store_true", help="Preview version bump without modifying files")

    # switch subcommand (agy-proxy switch)
    switch_parser = subparsers.add_parser("switch", help="Switch active Antigravity CLI/IDE session")
    switch_parser.add_argument("target", nargs="?", default=None, help="Target account email, name, ID, or #")
    switch_parser.add_argument("--set-primary", dest="set_primary", action="store_true", default=True, help="Also set as primary proxy account (default: True)")
    switch_parser.add_argument("--no-set-primary", dest="set_primary", action="store_false", help="Do not set as primary proxy account")
    switch_parser.add_argument("--next", "-n", action="store_true", help="Switch to next account with highest quota")
    switch_parser.add_argument("--list", "-l", action="store_true", help="List available OAuth accounts and quotas")
    switch_parser.add_argument("--env", "--target-env", dest="target_env", choices=["cli", "ide", "both"], default=None, help="Target destination environment (cli, ide, or both; default: both)")
    switch_parser.add_argument("--cli", action="store_true", help="Switch session ONLY for Antigravity CLI")
    switch_parser.add_argument("--ide", action="store_true", help="Switch session ONLY for Antigravity IDE")
    switch_parser.add_argument("--both", action="store_true", help="Switch session for BOTH Antigravity CLI and IDE (default)")

    # usage / quota subcommand (agy-proxy usage / agy-proxy quota)
    for u_name in ("usage", "quota"):
        u_p = subparsers.add_parser(u_name, help="View model quota usage")
        u_p.add_argument("target", nargs="?", default=None, help="Target account email, name, ID, or #")

    # auth subcommand
    auth_parser = subparsers.add_parser("auth", help="Manage Antigravity Google accounts in pool")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action", help="Auth action")
    auth_subparsers.add_parser("login", help="Log in a Google account via browser OAuth PKCE")
    auth_subparsers.add_parser("list", help="List all accounts and quotas in pool")

    # auth switch subcommand (agy-proxy auth switch)
    auth_switch = auth_subparsers.add_parser("switch", help="Switch active Antigravity CLI/IDE session")
    auth_switch.add_argument("target", nargs="?", default=None, help="Target account email, name, ID, or #")
    auth_switch.add_argument("--set-primary", dest="set_primary", action="store_true", default=True, help="Also set as primary proxy account (default: True)")
    auth_switch.add_argument("--no-set-primary", dest="set_primary", action="store_false", help="Do not set as primary proxy account")
    auth_switch.add_argument("--next", "-n", action="store_true", help="Switch to next account with highest quota")
    auth_switch.add_argument("--list", "-l", action="store_true", help="List available OAuth accounts and quotas")
    auth_switch.add_argument("--env", "--target-env", dest="target_env", choices=["cli", "ide", "both"], default=None, help="Target destination environment (cli, ide, or both; default: both)")
    auth_switch.add_argument("--cli", action="store_true", help="Switch session ONLY for Antigravity CLI")
    auth_switch.add_argument("--ide", action="store_true", help="Switch session ONLY for Antigravity IDE")
    auth_switch.add_argument("--both", action="store_true", help="Switch session for BOTH Antigravity CLI and IDE (default)")

    # auth usage / quota subcommand (agy-proxy auth usage / agy-proxy auth quota)
    for au_name in ("usage", "quota"):
        au_p = auth_subparsers.add_parser(au_name, help="View model quota usage")
        au_p.add_argument("target", nargs="?", default=None, help="Target account email, name, ID, or #")

    # Dedicated API key subcommands: `auth api` and `auth apikey`
    for alias_cmd in ("api", "apikey"):
        api_sub = auth_subparsers.add_parser(alias_cmd, help="Add a Google AI Studio Gemini API Key")
        api_sub.add_argument("positional_key", nargs="?", default=None, help="Gemini API Key (AIza...)")
        api_sub.add_argument("positional_name", nargs="?", default=None, help="Friendly display name for this API key")
        api_sub.add_argument("--key", "-k", type=str, default=None, help="Gemini API Key (AIza...)")
        api_sub.add_argument("--name", "-n", type=str, default=None, help="Friendly display name for this API key")

    # setup-claude subcommand (agy-proxy setup-claude)
    setup_claude_p = subparsers.add_parser(
        "setup-claude",
        aliases=["claude-setup"],
        help="Configure Claude Code CLI (~/.claude/settings.json) to route through Antigravity Proxy",
    )
    setup_claude_p.add_argument("--port", "-p", type=int, default=8000, help="Proxy server port (default: 8000)")
    setup_claude_p.add_argument("--url", type=str, default=None, help="Custom proxy base URL (e.g. http://127.0.0.1:8000)")
    setup_claude_p.add_argument("--model", "-m", type=str, default="anthropic.gemini-3.8-flash-high", help="Default model (default: anthropic.gemini-3.8-flash-high)")

    # restore-claude subcommand (agy-proxy restore-claude)
    subparsers.add_parser(
        "restore-claude",
        aliases=["claude-restore"],
        help="Restore original Claude Code CLI settings.json from backup",
    )

    # setup-codex subcommand (agy-proxy setup-codex)
    setup_codex_p = subparsers.add_parser(
        "setup-codex",
        aliases=["codex-setup"],
        help="Configure OpenAI Codex CLI to use Antigravity Proxy with full model catalog",
    )
    setup_codex_p.add_argument("--port", "-p", type=int, default=8000, help="Proxy server port (default: 8000)")
    setup_codex_p.add_argument("--model", "-m", type=str, default="gemini-3.8-flash-high", help="Default model (default: gemini-3.8-flash-high)")

    # restore-codex subcommand (agy-proxy restore-codex)
    subparsers.add_parser(
        "restore-codex",
        aliases=["codex-restore"],
        help="Restore original OpenAI Codex CLI configuration from backup",
    )

    # codex ephemeral runner (agy-proxy codex)
    codex_run_p = subparsers.add_parser(
        "codex",
        help="Launch OpenAI Codex CLI with proxy in ephemeral mode without modifying ~/.codex/config.toml",
    )
    codex_run_p.add_argument("--port", "-p", type=int, default=8000, help="Proxy server port (default: 8000)")
    codex_run_p.add_argument("--model", "-m", type=str, default="gemini-3.8-flash-high", help="Model to use (default: gemini-3.8-flash-high)")
    codex_run_p.add_argument("codex_args", nargs=argparse.REMAINDER, help="Additional arguments passed directly to codex")

    # Server arguments
    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("HOST", "127.0.0.1"),
        help="Host address to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Port number to listen on (default: 8000)",
    )
    parser.add_argument(
        "--token-file",
        type=str,
        default=os.environ.get("AGY_TOKEN_FILE", None),
        help="Custom path to primary token file",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.environ.get("PROXY_API_KEY", None),
        help="Require this API key in client requests (Authorization: Bearer <key>)",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="Enable detailed debug logging including raw HTTP traffic",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level (default: info)",
    )
    parser.add_argument(
        "--cloudflare-url",
        type=str,
        default=os.environ.get("CLOUDFLARE_UPSTREAM_URL", None),
        help="Route CloudCode traffic through Cloudflare Worker edge URL for Geo-Bypass",
    )
    # Context Auto-Compactor & Smart Tool Pruning controls
    parser.add_argument(
        "--compact",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable automatic context compaction for long chats (default: disabled)",
    )
    parser.add_argument(
        "--prune",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable or disable smart tool pruning for older tool results (default: disabled)",
    )
    parser.add_argument(
        "--compact-threshold",
        type=int,
        default=None,
        help="Token threshold to trigger context auto-compaction (default: 130000)",
    )
    parser.add_argument(
        "--prune-keep-tools",
        type=int,
        default=None,
        help="Number of recent tool results to leave intact during pruning (default: 15)",
    )
    parser.add_argument(
        "--prune-max-chars",
        type=int,
        default=None,
        help="Maximum characters to retain per pruned tool result (default: 15000)",
    )
    parser.add_argument(
        "--cooldown",
        "--rate-limit-cooldown",
        type=float,
        default=None,
        help="Rate-limit cooldown in seconds upon receiving HTTP 429 from provider (default: 0 / disabled)",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.cloudflare_url:
        os.environ["CLOUDFLARE_UPSTREAM_URL"] = args.cloudflare_url.rstrip("/")

    # Handle subcommands
    if args.subcommand == "bump":
        from agy_proxy.version import handle_bump_command
        handle_bump_command(target=getattr(args, "target", "patch"), dry_run=getattr(args, "dry_run", False))
        return
    elif args.subcommand == "update":
        check_only = getattr(args, "check", False)
        asyncio.run(handle_update_command(check_only=check_only))
        return
    elif args.subcommand == "switch":
        target_env = "both"
        env_set = False
        if getattr(args, "cli", False):
            target_env = "cli"
            env_set = True
        elif getattr(args, "ide", False):
            target_env = "ide"
            env_set = True
        elif getattr(args, "both", False):
            target_env = "both"
            env_set = True
        elif getattr(args, "target_env", None):
            target_env = args.target_env
            env_set = True

        asyncio.run(handle_switch_command(
            target=getattr(args, "target", None),
            to_next=getattr(args, "next", False),
            list_only=getattr(args, "list", False),
            set_primary=getattr(args, "set_primary", True),
            target_env=target_env,
            env_explicitly_set=env_set,
        ))
        return
    elif args.subcommand in ("usage", "quota"):
        asyncio.run(handle_quota_command(
            target=getattr(args, "target", None),
        ))
        return
    elif args.subcommand == "auth":
        if args.auth_action == "login":
            asyncio.run(handle_auth_login())
            return
        elif args.auth_action in ("api", "apikey"):
            key_val = getattr(args, "key", None) or getattr(args, "positional_key", None)
            name_val = getattr(args, "name", None) or getattr(args, "positional_name", None)
            asyncio.run(handle_auth_apikey(api_key=key_val, name=name_val))
            return
        elif args.auth_action == "list":
            asyncio.run(handle_auth_list())
            return
        elif args.auth_action == "switch":
            target_env = "both"
            env_set = False
            if getattr(args, "cli", False):
                target_env = "cli"
                env_set = True
            elif getattr(args, "ide", False):
                target_env = "ide"
                env_set = True
            elif getattr(args, "both", False):
                target_env = "both"
                env_set = True
            elif getattr(args, "target_env", None):
                target_env = args.target_env
                env_set = True

            asyncio.run(handle_switch_command(
                target=getattr(args, "target", None),
                to_next=getattr(args, "next", False),
                list_only=getattr(args, "list", False),
                set_primary=getattr(args, "set_primary", True),
                target_env=target_env,
                env_explicitly_set=env_set,
            ))
            return
        elif args.auth_action in ("usage", "quota"):
            asyncio.run(handle_quota_command(
                target=getattr(args, "target", None),
            ))
            return
        else:
            auth_parser.print_help()
            return
    elif args.subcommand in ("setup-claude", "claude-setup"):
        handle_setup_claude(
            port=getattr(args, "port", 8000),
            model=getattr(args, "model", "anthropic.gemini-3.8-flash-high"),
            url=getattr(args, "url", None),
        )
        return
    elif args.subcommand in ("restore-claude", "claude-restore"):
        handle_restore_claude()
        return
    elif args.subcommand in ("setup-codex", "codex-setup"):
        handle_setup_codex(port=getattr(args, "port", 8000), model=getattr(args, "model", "gemini-3.8-flash-high"))
        return
    elif args.subcommand in ("restore-codex", "codex-restore"):
        handle_restore_codex()
        return
    elif args.subcommand == "codex":
        handle_run_codex(
            port=getattr(args, "port", 8000),
            model=getattr(args, "model", "gemini-3.8-flash-high"),
            extra_args=getattr(args, "codex_args", []),
        )
        return


    # Apply CLI context auto-compaction and tool pruning overrides
    try:
        from agy_proxy.compactor import compactor_settings
        if getattr(args, "compact", None) is not None:
            compactor_settings.enabled = bool(args.compact)
        if getattr(args, "prune", None) is not None:
            compactor_settings.pruning_enabled = bool(args.prune)
        if getattr(args, "compact_threshold", None) is not None:
            compactor_settings.threshold_tokens = int(args.compact_threshold)
        if getattr(args, "prune_keep_tools", None) is not None:
            compactor_settings.prune_keep_tools = int(args.prune_keep_tools)
        if getattr(args, "prune_max_chars", None) is not None:
            compactor_settings.prune_max_chars = int(args.prune_max_chars)
    except Exception:
        pass

    if getattr(args, "cooldown", None) is not None:
        os.environ["AGY_RATE_LIMIT_COOLDOWN"] = str(args.cooldown)

    # Configure logging
    effective_log_level = "debug" if args.debug else args.log_level
    if args.debug:
        log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    else:
        log_format = "%(asctime)s [%(levelname)s] %(message)s"

    logging.basicConfig(
        level=getattr(logging, effective_log_level.upper()),
        format=log_format,
        datefmt="%H:%M:%S",
    )

    # Silence noisy low-level socket/HTTP connection logs
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("websockets.client").setLevel(logging.WARNING)
    logging.getLogger("websockets.server").setLevel(logging.WARNING)

    class SanitizingFilter(logging.Filter):
        """Redacts sensitive API keys and tokens from log messages."""
        KEY_PATTERN = re.compile(r'(key=)([A-Za-z0-9_\-\.]{6})[A-Za-z0-9_\-\.]+([A-Za-z0-9_\-\.]{4})')

        def filter(self, record: logging.LogRecord) -> bool:
            if isinstance(record.msg, str):
                record.msg = self.KEY_PATTERN.sub(r'\1\2...\3', record.msg)
            if record.args:
                new_args = []
                for a in record.args:
                    if isinstance(a, str):
                        new_args.append(self.KEY_PATTERN.sub(r'\1\2...\3', a))
                    else:
                        new_args.append(a)
                record.args = tuple(new_args)
            return True

    logging.getLogger("httpx").addFilter(SanitizingFilter())

    class EndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return not any(p in msg for p in ["/api/cache/stats", "/api/context/settings", "/api/accounts", "/health", "/api/usage"])

    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())
    logging.getLogger("uvicorn").addFilter(EndpointFilter())

    if args.debug or effective_log_level.lower() == "debug":
        logging.getLogger("httpx").setLevel(logging.INFO)
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)
        logging.getLogger("uvicorn").setLevel(logging.INFO)
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    elif effective_log_level.lower() == "info":
        # Standard mode: show server startup / lifecycle messages, silence periodic HTTP polling noise
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)
        logging.getLogger("uvicorn").setLevel(logging.INFO)
    else:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
        logging.getLogger("uvicorn").setLevel(logging.WARNING)

    # Initialize AccountPool and preload accounts from disk for banner
    pool = AccountPool(token_path=args.token_file)
    pool.load_accounts()

    # Create FastAPI app
    app = create_app(account_pool=pool, api_key=args.api_key)

    # Check for updates in quick background task
    update_info = None
    try:
        from agy_proxy.updater import check_for_updates
        update_info = asyncio.run(check_for_updates())
    except Exception:
        pass

    # Display startup info
    print_banner(
        host=args.host,
        port=args.port,
        pool=pool,
        api_key=args.api_key,
        update_info=update_info,
    )

    # Run Uvicorn with graceful shutdown handling
    uvicorn_log_level = "debug" if (args.debug or effective_log_level.lower() == "debug") else effective_log_level.lower()
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level=uvicorn_log_level,
        access_log=bool(args.debug or effective_log_level.lower() == "debug"),
    )
    server = uvicorn.Server(config)

    try:
        server.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        if not args.debug:
            console.print("\n[bold yellow]Proxy stopped.[/bold yellow]")
        else:
            raise e
    finally:
        if not args.debug:
            console.print("[dim]Antigravity Proxy stopped successfully. Bye![/dim]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
