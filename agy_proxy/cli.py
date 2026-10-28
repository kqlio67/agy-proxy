"""
Command-line interface and entry point for Antigravity Proxy.
Supports starting the proxy server and interactive Multi-Account authentication commands.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import uvicorn
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.table import Table

from agy_proxy import __version__ as APP_VERSION
from agy_proxy.auth import AccountPool
from agy_proxy.server import create_app

console = Console()


def print_banner(host: str, port: int, pool: AccountPool, api_key: str = None, update_info: dict = None):
    try:
        term_cols, term_lines = shutil.get_terminal_size(fallback=(80, 24))
    except Exception:
        term_cols, term_lines = 80, 24

    console_w = getattr(console, "width", None)
    term_width = console_w if (console_w is not None and console_w > 0) else term_cols
    is_compact = term_width < 90
    is_narrow = term_width < 75

    if term_lines >= 40:
        max_active_accounts = 6
    elif term_lines >= 30:
        max_active_accounts = 4
    else:
        max_active_accounts = 3

    url = f"http://{host}:{port}"
    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column("Key", style="bold cyan", no_wrap=True)
    table.add_column("Value", ratio=1)
    table.add_row("Web Dashboard:", f"[bold green]{url}[/bold green]")
    try:
        from agy_proxy.web_launcher import get_web_status
        web_st = get_web_status()
        if web_st.get("status") == "running":
            mode_suffix = " [dim](Active · No-Electron)[/dim]" if not is_compact else " [dim](Active)[/dim]"
            table.add_row("Antigravity Web:", f"[bold green]{web_st['url']}[/bold green]{mode_suffix}")
        else:
            hint = " [dim](run: agy-proxy web)[/dim]" if not is_compact else ""
            table.add_row("Antigravity Web:", f"[dim]{web_st['url']}{hint}[/dim]")
    except Exception:
        pass

    if is_compact:
        table.add_row("Universal API:", f"[bold yellow]{url}/v1[/bold yellow]")
    else:
        table.add_row("Universal API:", f"[bold yellow]{url}/v1[/bold yellow] [dim](OpenAI / Claude Code)[/dim]")

    table.add_row("Gemini Native:", f"[bold blue]{url}/v1beta[/bold blue]")
    table.add_row("Prompt Caching:", "[bold green]Enabled[/bold green] [dim](75% token discount)[/dim]")
    try:
        from agy_proxy.compactor import compactor_settings
        if compactor_settings.enabled:
            comp_status = f"[bold green]Active[/bold green] [dim]({compactor_settings.threshold_tokens:,} tokens)[/dim]"
        else:
            comp_status = "[dim]Safe Mode (Claude-managed)[/dim]"
        table.add_row("Context Compactor:", comp_status)

        if compactor_settings.pruning_enabled:
            prune_status = f"[bold green]Active[/bold green] [dim](keep {compactor_settings.prune_keep_tools} tools)[/dim]"
        else:
            prune_status = "[dim]Safe Mode (preserved)[/dim]"
        table.add_row("Smart Tool Pruning:", prune_status)
    except Exception:
        pass

    cooldown_val = float(os.environ.get("AGY_RATE_LIMIT_COOLDOWN", "0"))
    if cooldown_val > 0:
        cd_status = f"[bold yellow]{cooldown_val:.0f}s[/bold yellow]"
    else:
        cd_status = "[bold green]0s (instant retry)[/bold green]"
    table.add_row("429 Cooldown:", cd_status)

    all_accs = list(pool.accounts.values())
    oauth_accs = [a for a in all_accs if getattr(a, "auth_method", "consumer") == "consumer"]
    key_accs = [a for a in all_accs if getattr(a, "auth_method", "") == "api_key"]
    web_accs = [a for a in all_accs if getattr(a, "auth_method", "") == "gemini_web"]

    active_oauth = [a for a in oauth_accs if a.enabled]
    active_keys = [a for a in key_accs if a.enabled]
    active_web = [a for a in web_accs if a.enabled]
    active_all = [a for a in all_accs if a.enabled]
    paused_all = [a for a in all_accs if not a.enabled]

    pool_parts = []
    if oauth_accs:
        p = f"[bold white]{len(active_oauth)} OAuth[/bold white]"
        if len(oauth_accs) > len(active_oauth):
            p += f" [dim yellow]({len(oauth_accs) - len(active_oauth)} paused)[/dim yellow]"
        pool_parts.append(p)
    if key_accs:
        if active_keys:
            p = f"[bold white]{len(active_keys)} API Key(s)[/bold white]"
            if len(key_accs) > len(active_keys):
                p += f" [dim yellow]({len(key_accs) - len(active_keys)} paused)[/dim yellow]"
        else:
            p = f"[dim yellow]{len(key_accs)} API Keys (paused)[/dim yellow]"
        pool_parts.append(p)
    if web_accs:
        if active_web:
            p = f"[bold white]{len(active_web)} Gemini Web[/bold white]"
            if len(web_accs) > len(active_web):
                p += f" [dim yellow]({len(web_accs) - len(active_web)} paused)[/dim yellow]"
        else:
            p = f"[dim yellow]{len(web_accs)} Web (paused)[/dim yellow]"
        pool_parts.append(p)

    sep = " · " if not is_compact else " | "
    pool_status = sep.join(pool_parts) if pool_parts else "[dim]No accounts in pool[/dim]"
    table.add_row("Accounts in Pool:", pool_status)

    strat = pool.routing_strategy
    if strat in ("round_robin", "rotate"):
        strat_display = "[bold magenta]Round-Robin (Automatic rotation)[/bold magenta]"
    elif strat in ("balanced", "lru"):
        strat_display = "[bold magenta]Balanced (Least Recently Used)[/bold magenta]"
    else:
        strat_display = "[bold green]Primary + Failover[/bold green]"
    table.add_row("Routing Strategy:", strat_display)

    if api_key:
        masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 12 and is_compact else api_key
        table.add_row("Proxy API Key:", f"[bold red]{masked}[/bold red]")
    else:
        table.add_row("Proxy API Key:", "[dim](None - Open access)[/dim]")

    if update_info and update_info.get("has_update"):
        latest = update_info.get("latest_version")
        curr = update_info.get("current_version")
        rel_url = update_info.get("release_url", "")
        if is_compact or not rel_url:
            up_val = f"[bold green]v{latest}[/bold green] [dim](current: v{curr}) · run 'agy-proxy update'[/dim]"
        else:
            up_val = f"[bold green]v{latest}[/bold green] [dim](current: v{curr})[/dim] - [underline blue]{rel_url}[/underline blue]"
        table.add_row("Update Available:", up_val)

    acc_table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    acc_table.add_column(ratio=1)

    def sort_key(acc):
        is_pri = 0 if getattr(acc, "is_primary", False) else 1
        m_ord = {"consumer": 0, "api_key": 1, "gemini_web": 2}.get(getattr(acc, "auth_method", "consumer"), 3)
        return (is_pri, m_ord)

    active_sorted = sorted(active_all, key=sort_key)

    if active_sorted:
        for acc in active_sorted[:max_active_accounts]:
            m = getattr(acc, "auth_method", "consumer")
            if m == "consumer":
                pri_badge = "[bold green]★ Primary[/bold green]" if acc.is_primary else "[blue]Secondary[/blue]"
                proj = acc.project_id or "default"
                if len(proj) > 22 and is_narrow:
                    proj = proj[:19] + "..."
                email_str = acc.email or "unknown@gmail.com"
                if len(email_str) > 28 and is_narrow:
                    email_str = email_str[:13] + "..." + email_str[-12:]
                acc_table.add_row(f"  [cyan][OAuth][/cyan] [white]{email_str}[/white] ({pri_badge}) [dim]· {proj}[/dim]")
            elif m == "api_key":
                raw = getattr(acc, "api_key", "") or ""
                hint = (raw[:5] + "..." + raw[-4:]) if len(raw) > 10 else "API Key"
                name = acc.name or f"AI Studio ({hint})"
                if name.endswith(" - Gemini API Key"):
                    name = name[:-17].strip()
                if len(name) > 28 and is_compact:
                    name = name[:25] + "..."
                acc_table.add_row(f"  [yellow][API Key][/yellow] [white]{name}[/white] [dim]({hint} · Google AI Studio)[/dim]")
            elif m == "gemini_web":
                label = acc.email or acc.name or acc.account_id
                if len(label) > 28 and is_compact:
                    label = label[:25] + "..."
                acc_table.add_row(f"  [magenta][Gemini Web][/magenta] [white]{label}[/white] [dim](Browser Session)[/dim]")

        if len(active_sorted) > max_active_accounts:
            rem = len(active_sorted) - max_active_accounts
            acc_table.add_row(f"  [dim]• ... and {rem} more active account(s) (view in Dashboard or 'agy-proxy auth')[/dim]")

    if paused_all:
        p_oauth = len([a for a in paused_all if getattr(a, "auth_method", "consumer") == "consumer"])
        p_keys = len([a for a in paused_all if getattr(a, "auth_method", "") == "api_key"])
        p_web = len([a for a in paused_all if getattr(a, "auth_method", "") == "gemini_web"])
        parts = []
        if p_oauth: parts.append(f"{p_oauth} OAuth")
        if p_keys: parts.append(f"{p_keys} API Key{'s' if p_keys > 1 else ''}")
        if p_web: parts.append(f"{p_web} Web")
        detail_str = f" ({', '.join(parts)})" if parts else ""
        if is_compact:
            acc_table.add_row(f"  [dim yellow]⏸ {len(paused_all)} paused accounts{detail_str}[/dim yellow]")
        else:
            acc_table.add_row(f"  [dim yellow]⏸ {len(paused_all)} paused accounts{detail_str} · run 'agy-proxy auth' to manage[/dim yellow]")

    if not all_accs:
        acc_table.add_row("  [dim yellow]ℹ️  No accounts configured yet · open Dashboard to connect Google Account[/dim yellow]")

    rule_title = "[dim]Active Accounts[/dim]" if active_sorted else "[dim]Account Pool[/dim]"
    panel_content = Group(table, Rule(rule_title, style="dim blue"), acc_table)

    panel = Panel(
        panel_content,
        title=f"[bold white]Google Antigravity AI Proxy [dim](v{APP_VERSION})[/dim][/bold white]",
        border_style="blue",
        subtitle="[dim]Press Ctrl+C to stop[/dim]",
        padding=(0, 1),
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
    table.add_column("Tier / Plan", style="bold")
    table.add_column("Identity / Email", style="white")
    table.add_column("Gemini Quota", style="green")
    table.add_column("Claude/3P Quota", style="yellow")
    table.add_column("Requests", justify="right")

    for acc in pool.accounts.values():
        is_api_key = acc.auth_method == "api_key"
        is_web = acc.auth_method == "gemini_web"
        acc_type = "API Key" if is_api_key else ("Web" if is_web else "OAuth")
        display_name = acc.name or ("Primary Account" if acc.is_primary else acc.account_id)
        if acc.is_primary:
            display_name += " ⭐"

        # Tier / Plan badge
        tid = getattr(acc, "tier_id", "free-tier")
        if is_api_key:
            tier_display = "[yellow]AI Studio PayG[/yellow]"
        elif is_web:
            tier_display = "[teal]Gemini Web Pro[/teal]"
        elif tid == "g1-ultra-tier":
            tier_display = "[bold magenta]Google AI Ultra[/bold magenta]"
        elif tid == "g1-pro-tier":
            tier_display = "[bold cyan]Google AI Pro[/bold cyan]"
        elif tid == "standard-tier":
            tier_display = "[blue]GCP Enterprise[/blue]"
        else:
            tier_info = getattr(acc, "tier_info", {}) or {}
            paid_info = tier_info.get("paidTier") or {}
            credits = paid_info.get("availableCredits") or []
            has_g1 = any(c.get("creditType") == "GOOGLE_ONE_AI" for c in credits)
            if has_g1:
                tier_display = "[dim]Free Tier[/dim] [yellow](G1 Ready)[/yellow]"
            else:
                tier_display = "[dim]Free Tier[/dim]"

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
            tier_display,
            acc.email or "N/A",
            gemini_q,
            claude_q,
            str(acc.total_requests),
        )

    console.print(table)


def handle_accounts_export(output: str | None = None) -> None:
    """Exports all accounts in the pool to JSON file or stdout."""
    pool = AccountPool()
    pool.load_accounts()
    data = pool.export_pool(output_file=output)
    count = len(data.get("accounts", []))
    if output:
        console.print(f"[bold green]✓ Exported {count} account(s) to [white]{output}[/white][/bold green]")
    else:
        print(json.dumps(data, indent=2))


def handle_accounts_import(file_path: str, merge: bool = True) -> None:
    """Imports accounts from a JSON file into the pool."""
    pool = AccountPool()
    pool.load_accounts()
    try:
        count = pool.import_pool(file_path, merge=merge)
        mode_str = "merged" if merge else "replaced"
        console.print(f"[bold green]✓ Successfully {mode_str} {count} account(s) into pool from [white]{file_path}[/white][/bold green]")
    except Exception as e:
        console.print(f"[bold red]✗ Failed to import accounts from {file_path}:[/bold red] {e}")


def handle_auth_toggle(target: str, enabled: bool) -> bool:
    """Enables or disables (pauses) one or more accounts in the pool."""
    pool = AccountPool()
    pool.load_accounts()

    action_verb = "enabled" if enabled else "paused"
    action_color = "green" if enabled else "yellow"
    target_clean = (target or "").strip()

    if not target_clean or target_clean.lower() == "all":
        pool.set_all_accounts_enabled(enabled)
        console.print(f"[bold {action_color}]✓ Successfully {action_verb} all {len(pool.accounts)} account(s) in pool.[/bold {action_color}]")
        return True

    lower_t = target_clean.lower()
    if lower_t in ("oauth", "consumer", "apikey", "api_key", "api", "keys", "web", "gemini_web"):
        sec = "api_key" if "api" in lower_t or "key" in lower_t else ("gemini_web" if "web" in lower_t else "consumer")
        count = pool.set_section_accounts_enabled(sec, enabled)
        sec_name = "API Key" if sec == "api_key" else ("Gemini Web" if sec == "gemini_web" else "OAuth")
        console.print(f"[bold {action_color}]✓ Successfully {action_verb} {count} {sec_name} account(s).[/bold {action_color}]")
        return True

    acc = pool.get_account(target_clean)
    if not acc:
        console.print(f"[bold red]✗ Account '{target_clean}' not found in pool.[/bold red]")
        return False

    ok = pool.set_account_enabled(acc.account_id, enabled)
    if ok:
        display = acc.email or acc.name or acc.account_id
        console.print(f"[bold {action_color}]✓ Successfully {action_verb} account [white]{display}[/white] ({acc.account_id}).[/bold {action_color}]")
        return True
    else:
        console.print(f"[bold red]✗ Failed to update account '{target_clean}'.[/bold red]")
        return False


def handle_auth_remove(target: str) -> bool:
    """Removes an account from the pool."""
    pool = AccountPool()
    pool.load_accounts()

    target_clean = (target or "").strip()
    if not target_clean:
        console.print("[bold red]✗ Error: Specify an account ID, email, or name to remove.[/bold red]")
        return False

    acc = pool.get_account(target_clean)
    if not acc:
        console.print(f"[bold red]✗ Account '{target_clean}' not found in pool.[/bold red]")
        return False

    display = acc.email or acc.name or acc.account_id
    acc_id = acc.account_id
    ok = pool.remove_account(acc_id)
    if ok:
        console.print(f"[bold green]✓ Successfully removed account [white]{display}[/white] ({acc_id}) from pool.[/bold green]")
        return True
    else:
        console.print(f"[bold red]✗ Failed to remove account '{target_clean}'.[/bold red]")
        return False


async def handle_switch_command(
    target: str | None = None,
    to_next: bool = False,
    list_only: bool = False,
    set_primary: bool = True,
    target_env: str = "all",
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
        cli_acc, ide_acc, standalone_acc = get_active_antigravity_accounts(pool)
        cli_id = cli_acc.account_id if cli_acc else None
        ide_id = ide_acc.account_id if ide_acc else None
        standalone_id = standalone_acc.account_id if standalone_acc else None

        table = Table(title="Google Antigravity Accounts in Pool", show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=4)
        table.add_column("Account / Email", style="white")
        table.add_column("Name", style="green")
        table.add_column("Status", style="yellow")

        for idx, acc in enumerate(oauth_accounts, start=1):
            if acc.account_id == cli_id and acc.account_id == standalone_id:
                status = "[bold green]Active (CLI & Standalone) ⭐[/bold green]"
            elif acc.account_id == cli_id:
                status = "[bold green]Active (CLI) ⭐[/bold green]"
            elif acc.account_id == ide_id:
                status = "[bold green]Active (IDE) ⭐[/bold green]"
            elif acc.account_id == standalone_id:
                status = "[bold green]Active (Standalone) ⭐[/bold green]"
            else:
                status = "[dim]Ready[/dim]"
            
            table.add_row(str(idx), acc.email or acc.account_id, acc.name or "", status)

        console.print(table)
        if list_only:
            return

        try:
            choice = Prompt.ask("\nEnter account #, email, name, or 'next' to rotate", default="next")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Operation cancelled by user.[/dim]")
            return

        if not choice or choice.lower() in ("next", "n"):
            to_next = True
            target = None
        else:
            to_next = False
            target = choice

        if not env_explicitly_set:
            try:
                target_env = Prompt.ask(
                    "Target destination environment",
                    choices=["all", "cli", "standalone"],
                    default="all",
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Operation cancelled by user.[/dim]")
                return

    try:
        env_label = "CLI only" if target_env == "cli" else ("Standalone only" if target_env == "standalone" else "All Environments")
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
        f"• [bold cyan]Default Model:[/bold cyan] [magenta]{res['model']}[/magenta]\n"
        f"• [bold cyan]Model Provider:[/bold cyan] [green]model_providers.antigravity (Responses wire_api)[/green]"
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


def handle_experiments_command(args):
    """Command for inspecting and managing Antigravity experimental flags."""
    from agy_proxy.experiments import (
        get_experiments_status,
        set_experiment_override,
        reset_experiment_overrides,
        set_experiment_mode,
        BLOCKLIST_FLAGS,
    )

    action = getattr(args, "exp_action", "list") or "list"

    if action == "list":
        status = get_experiments_status()
        mode = status["mode"]
        active = status["active"]

        header_text = (
            f"[bold cyan]Antigravity Experimental Features & Flags Manager[/bold cyan]\n"
            f"Status: [{'bold green' if active else 'bold red'}]{'Active' if active else 'Disabled'}[/] | "
            f"Mode: [bold magenta]{mode}[/bold magenta] | "
            f"Curated Flags: [bold white]{status['total_curated_flags']}[/bold white] | "
            f"Experiment IDs: [bold white]{status['total_experiment_ids']}[/bold white] | "
            f"Overrides: [bold yellow]{status['overrides_count']}[/bold yellow]"
        )
        console.print(Panel(header_text, border_style="blue"))

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Flag Name", style="white")
        table.add_column("Category", style="cyan")
        table.add_column("State", justify="center")
        table.add_column("Description", style="dim")

        for fl in status["flags"]:
            is_on = fl["enabled"]
            is_overridden = fl["is_overridden"]
            state_str = "[bold green]ON[/bold green]" if is_on else "[dim]OFF[/dim]"
            if is_overridden:
                state_str += " [yellow]*[/yellow]"
            table.add_row(
                fl["name"],
                fl["category_label"],
                state_str,
                fl["description"],
            )

        console.print(table)
        console.print("[dim]* = Manually overridden in ~/.config/agy-proxy/experiments.json[/dim]\n")

    elif action == "enable":
        flag = args.flag
        if flag in BLOCKLIST_FLAGS:
            console.print(f"[bold red]Cannot enable '{flag}': This flag is in the critical blocklist (would break functionality).[/bold red]")
            return
        set_experiment_override(flag, True)
        console.print(f"[bold green]✓ Successfully enabled experimental flag:[/bold green] [bold white]{flag}[/bold white]")

    elif action == "disable":
        flag = args.flag
        set_experiment_override(flag, False)
        console.print(f"[bold yellow]✓ Successfully disabled experimental flag:[/bold yellow] [bold white]{flag}[/bold white]")

    elif action == "mode":
        mode = args.mode
        try:
            set_experiment_mode(mode)
            console.print(f"[bold green]✓ Set experiment operating mode to:[/bold green] [bold magenta]{mode}[/bold magenta]")
        except Exception as e:
            console.print(f"[bold red]Failed to set mode:[/bold red] {e}")

    elif action == "reset":
        reset_experiment_overrides()
        console.print("[bold green]✓ Successfully reset all experimental flag overrides back to curated defaults.[/bold green]")


def handle_config_command(args: argparse.Namespace) -> None:
    """Handles 'agy-proxy config' CLI commands (get, set, list, unset)."""
    from agy_proxy.config import (
        CONFIG_FILE,
        delete_config_value,
        get_config_value,
        load_config,
        save_config,
        set_config_value,
        set_configured_editor_path,
        set_configured_ls_path,
    )

    action = getattr(args, "config_action", None)
    if not action or action in ("list", "show"):
        cfg = load_config()
        if not cfg:
            console.print("[dim]No persistent custom configuration set yet.[/dim]")
            console.print(f"[dim]Configuration file location: {CONFIG_FILE}[/dim]\n")
            console.print("To configure language server or editor path permanently:")
            console.print("  [cyan]agy-proxy config set ls-path /path/to/language_server[/cyan]")
            console.print("  [cyan]agy-proxy config set editor /path/to/editor_directory[/cyan]")
            return

        table = Table(title="Antigravity Proxy Configuration", border_style="cyan")
        table.add_column("Setting Key", style="bold cyan")
        table.add_column("Configured Value", style="white")
        for k, v in sorted(cfg.items()):
            table.add_row(str(k), str(v))
        console.print(table)
        console.print(f"[dim]Stored at: {CONFIG_FILE}[/dim]")

    elif action == "set":
        k = getattr(args, "key", "")
        v = getattr(args, "value", "")
        if k in ("ls-path", "ls_path", "language_server_path"):
            set_configured_ls_path(v)
            console.print(f"[bold green]✓ language_server_path saved:[/bold green] [cyan]{v}[/cyan]")
        elif k in ("editor", "editor_path", "editor-path"):
            set_configured_editor_path(v)
            console.print(f"[bold green]✓ editor_path saved:[/bold green] [cyan]{v}[/cyan]")
        else:
            set_config_value(k, v)
            console.print(f"[bold green]✓ '{k}' saved:[/bold green] [cyan]{v}[/cyan]")

    elif action == "get":
        k = getattr(args, "key", "")
        norm_k = "language_server_path" if k in ("ls-path", "ls_path") else ("editor_path" if k in ("editor", "editor-path") else k)
        val = get_config_value(norm_k)
        if val is not None:
            console.print(str(val))
        else:
            console.print(f"[yellow]Setting '{k}' is not configured.[/yellow]")

    elif action in ("unset", "reset", "delete"):
        k = getattr(args, "key", None)
        if k:
            norm_k = "language_server_path" if k in ("ls-path", "ls_path") else ("editor_path" if k in ("editor", "editor-path") else k)
            if delete_config_value(norm_k):
                console.print(f"[bold green]✓ Removed setting '{k}'.[/bold green]")
            else:
                console.print(f"[yellow]Setting '{k}' was not found in configuration.[/yellow]")
        else:
            save_config({})
            console.print("[bold green]✓ All custom configuration settings have been reset.[/bold green]")


def handle_service_command(args: argparse.Namespace) -> None:
    """Handles 'agy-proxy service' commands for background daemon management."""
    action = getattr(args, "service_action", None)
    from agy_proxy import service

    if action == "install":
        port = getattr(args, "port", 8000)
        host = getattr(args, "host", "127.0.0.1")
        ok, msg = service.install_service(port=port, host=host)
        if ok:
            console.print(f"[bold green]✓ {msg}[/bold green]")
        else:
            console.print(f"[bold red]✗ {msg}[/bold red]")
            sys.exit(1)
    elif action == "uninstall":
        ok, msg = service.uninstall_service()
        if ok:
            console.print(f"[bold green]✓ {msg}[/bold green]")
        else:
            console.print(f"[bold red]✗ {msg}[/bold red]")
            sys.exit(1)
    elif action == "status":
        st = service.get_service_status()
        color = "bold green" if st["running"] else ("bold yellow" if st["installed"] else "bold red")
        status_text = "RUNNING" if st["running"] else ("INSTALLED (STOPPED)" if st["installed"] else "NOT INSTALLED")
        details = st.get("details", "")
        content = (
            f"[bold cyan]Platform:[/bold cyan] {st['platform']}\n"
            f"[bold cyan]Service File:[/bold cyan] {st['service_file']}\n"
            f"[bold cyan]Status:[/bold cyan] [{color}]{status_text}[/{color}]"
        )
        if details:
            content += f"\n\n[dim]{details}[/dim]"
        console.print(Panel(
            content,
            title="[bold white]Antigravity Proxy Service Status[/bold white]",
            border_style="blue",
        ))
    elif action == "start":
        ok, msg = service.start_service()
        if ok:
            console.print(f"[bold green]✓ Service started:[/bold green] {msg}")
        else:
            console.print(f"[bold red]✗ Failed to start service:[/bold red] {msg}")
            sys.exit(1)
    elif action == "stop":
        ok, msg = service.stop_service()
        if ok:
            console.print(f"[bold green]✓ Service stopped:[/bold green] {msg}")
        else:
            console.print(f"[bold red]✗ Failed to stop service:[/bold red] {msg}")
            sys.exit(1)
    elif action == "restart":
        ok, msg = service.restart_service()
        if ok:
            console.print(f"[bold green]✓ Service restarted:[/bold green] {msg}")
        else:
            console.print(f"[bold red]✗ Failed to restart service:[/bold red] {msg}")
            sys.exit(1)
    else:
        console.print("[yellow]Usage: agy-proxy service [install|uninstall|status|start|stop|restart][/yellow]")


def handle_patch_command(args: argparse.Namespace) -> None:
    """Command for managing Antigravity language_server desktop patching."""
    from agy_proxy.patcher import LanguageServerPatcher

    action = getattr(args, "patch_action", None) or "status"
    target = getattr(args, "target", None) or getattr(args, "ls_path", None) or getattr(args, "editor", None)

    proxy_url = getattr(args, "url", None)
    if not proxy_url:
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8000)
        proxy_url = f"http://{host}:{port}"

    wait_timeout = 0 if getattr(args, "no_wait", False) else getattr(args, "timeout", 2)
    enable_watchdog = not getattr(args, "no_watchdog", False)

    patcher = LanguageServerPatcher(
        target_path=target,
        proxy_url=proxy_url,
        wait_timeout=wait_timeout,
        enable_watchdog=enable_watchdog,
    )

    if action in ("install", "patch"):
        console.print("[bold cyan]🔧 Patching Antigravity Language Server (Desktop App / Electron)[/bold cyan]")
        res = patcher.install()
        if res.get("success"):
            console.print(f"[bold green]✓ Successfully installed wrapper at:[/bold green] {res.get('ls_file')}")
            console.print(f"[bold green]✓ Original binary backup at:[/bold green] {res.get('real_file')}")
            console.print(f"[bold cyan]⚡ Requests will route through:[/bold cyan] {res.get('proxy_url')}")
            if getattr(args, "yes", False):
                killed = patcher.kill_running_instances(res.get("real_file"))
                if killed:
                    console.print(f"[bold green]✓ Restarted {len(killed)} language_server instance(s).[/bold green]")
        else:
            console.print(f"[bold red]❌ Error: {res.get('error')}[/bold red]")
            sys.exit(1)

    elif action in ("restore", "unpatch"):
        console.print("[bold yellow]⏪ Restoring Original Antigravity Language Server Binary[/bold yellow]")
        res = patcher.restore()
        if res.get("success"):
            console.print(f"[bold green]✓ Original binary restored:[/bold green] {res.get('ls_file')}")
            if getattr(args, "yes", False):
                killed = patcher.kill_running_instances(res.get("ls_file"))
                if killed:
                    console.print(f"[bold green]✓ Restarted {len(killed)} language_server instance(s).[/bold green]")
        else:
            console.print(f"[bold red]❌ Error: {res.get('error')}[/bold red]")
            sys.exit(1)

    elif action == "restart":
        killed = patcher.kill_running_instances()
        if killed:
            console.print(f"[bold green]✓ Terminated {len(killed)} running language_server process(es). Antigravity will respawn automatically.[/bold green]")
        else:
            console.print("[bold yellow]ℹ️  No active language_server processes found.[/bold yellow]")

    elif action == "status":
        info = patcher.status()
        ls_file = info.get("ls_file")
        real_file = info.get("real_file")
        is_patched = info.get("is_patched")
        pids = info.get("running_pids", [])
        proxy_alive = info.get("proxy_alive", False)

        patch_status = "[bold green]PATCHED (Wrapper Active)[/bold green]" if is_patched else "[bold yellow]ORIGINAL (Unpatched Binary)[/bold yellow]"
        proxy_status = "[bold green]ONLINE[/bold green]" if proxy_alive else "[bold red]OFFLINE (Direct Fallback)[/bold red]"

        lines = [
            f"[bold cyan]Target Binary:[/bold cyan] {ls_file or 'Not found'}",
            f"[bold cyan]Backup (.real):[/bold cyan] {real_file if info.get('backup_exists') else 'None'}",
            f"[bold cyan]Patch State:[/bold cyan] {patch_status}",
            f"[bold cyan]Target Proxy:[/bold cyan] {info.get('proxy_url')} ({proxy_status})",
            f"[bold cyan]Running Processes:[/bold cyan] {len(pids)} active",
        ]
        for pid in pids:
            mode = info.get("pid_modes", {}).get(pid, "unknown").upper()
            lines.append(f"  • PID {pid}: [bold green]{mode}[/bold green] mode")

        console.print(Panel(
            "\n".join(lines),
            title="[bold white]Antigravity Language Server Desktop Patch Status[/bold white]",
            border_style="cyan",
        ))


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

    # doctor subcommand (agy-proxy doctor)
    doctor_parser = subparsers.add_parser("doctor", help="Run comprehensive health and diagnostic checks")
    doctor_parser.add_argument("--host", type=str, default="127.0.0.1", help="Proxy host to check")
    doctor_parser.add_argument("--port", "-p", type=int, default=8000, help="Proxy port to check")

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
    switch_parser.add_argument("--env", "--target-env", dest="target_env", choices=["cli", "ide", "standalone", "all"], default=None, help="Target destination environment (cli, standalone, or all; default: all)")
    switch_parser.add_argument("--cli", action="store_true", help="Switch session ONLY for terminal agy CLI (~/.gemini/antigravity-cli)")
    switch_parser.add_argument("--ide", action="store_true", help="Switch session ONLY for IDE (~/.gemini/antigravity-ide)")
    switch_parser.add_argument("--standalone", action="store_true", help="Switch session ONLY for Standalone Web UI (~/.gemini/jetski-standalone-oauth-token)")
    switch_parser.add_argument("--all", action="store_true", help="Switch session for ALL environments (default)")

    # usage / quota subcommand (agy-proxy usage / agy-proxy quota)
    for u_name in ("usage", "quota"):
        u_p = subparsers.add_parser(u_name, help="View model quota usage")
        u_p.add_argument("target", nargs="?", default=None, help="Target account email, name, ID, or #")

    # auth subcommand (also accessible via 'accounts')
    auth_parser = subparsers.add_parser("auth", aliases=["accounts"], help="Manage Antigravity Google accounts in pool")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action", help="Auth action")
    auth_subparsers.add_parser("login", help="Log in a Google account via browser OAuth PKCE")
    auth_subparsers.add_parser("list", help="List all accounts and quotas in pool")

    # accounts / auth export
    auth_export = auth_subparsers.add_parser("export", help="Export account pool to JSON file")
    auth_export.add_argument("-o", "--output", dest="output", type=str, default=None, help="Output JSON file path (e.g. pool.json; default: stdout)")

    # accounts / auth import
    auth_import = auth_subparsers.add_parser("import", help="Import account pool from JSON file")
    auth_import.add_argument("file", help="Input JSON file containing exported accounts")
    auth_import.add_argument("--merge", action="store_true", default=True, help="Merge into existing accounts (default: True)")
    auth_import.add_argument("--replace", "--no-merge", dest="merge", action="store_false", help="Replace existing accounts instead of merging")

    # auth switch subcommand (agy-proxy auth switch)
    auth_switch = auth_subparsers.add_parser("switch", help="Switch active Antigravity CLI/IDE session")
    auth_switch.add_argument("target", nargs="?", default=None, help="Target account email, name, ID, or #")
    auth_switch.add_argument("--set-primary", dest="set_primary", action="store_true", default=True, help="Also set as primary proxy account (default: True)")
    auth_switch.add_argument("--no-set-primary", dest="set_primary", action="store_false", help="Do not set as primary proxy account")
    auth_switch.add_argument("--next", "-n", action="store_true", help="Switch to next account with highest quota")
    auth_switch.add_argument("--list", "-l", action="store_true", help="List available OAuth accounts and quotas")
    auth_switch.add_argument("--env", "--target-env", dest="target_env", choices=["cli", "ide", "standalone", "all"], default=None, help="Target destination environment (cli, standalone, or all; default: all)")
    auth_switch.add_argument("--cli", action="store_true", help="Switch session ONLY for terminal agy CLI (~/.gemini/antigravity-cli)")
    auth_switch.add_argument("--ide", action="store_true", help="Switch session ONLY for IDE (~/.gemini/antigravity-ide)")
    auth_switch.add_argument("--standalone", action="store_true", help="Switch session ONLY for Standalone Web UI (~/.gemini/jetski-standalone-oauth-token)")
    auth_switch.add_argument("--all", action="store_true", help="Switch session for ALL environments (default)")

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

    # auth enable / resume / unpause
    for en_cmd in ("enable", "resume", "unpause"):
        auth_en = auth_subparsers.add_parser(en_cmd, help="Enable or unpause account(s) in pool (use 'all' or section 'oauth'/'apikey'/'web')")
        auth_en.add_argument("target", nargs="?", default="all", help="Target account ID, email, name, 'all', or type ('oauth'/'apikey'/'web')")

    # auth disable / pause
    for dis_cmd in ("disable", "pause"):
        auth_dis = auth_subparsers.add_parser(dis_cmd, help="Pause or disable account(s) in pool (use 'all' or section 'oauth'/'apikey'/'web')")
        auth_dis.add_argument("target", nargs="?", default="all", help="Target account ID, email, name, 'all', or type ('oauth'/'apikey'/'web')")

    # auth remove / delete / rm
    for rm_cmd in ("remove", "delete", "rm"):
        auth_rm = auth_subparsers.add_parser(rm_cmd, help="Remove an account from the pool")
        auth_rm.add_argument("target", help="Account ID, email, or name to remove")

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

    # experiments subcommand (agy-proxy experiments)
    exp_parser = subparsers.add_parser(
        "experiments",
        aliases=["exp"],
        help="Inspect and manage Antigravity experimental features and flags",
    )
    exp_subparsers = exp_parser.add_subparsers(dest="exp_action")
    exp_subparsers.add_parser("list", help="List all experimental flags and current status")
    exp_enable_p = exp_subparsers.add_parser("enable", help="Force enable an experimental flag")
    exp_enable_p.add_argument("flag", help="Flag name to enable (e.g. enable-deepagent)")
    exp_disable_p = exp_subparsers.add_parser("disable", help="Disable an experimental flag")
    exp_disable_p.add_argument("flag", help="Flag name to disable")
    exp_mode_p = exp_subparsers.add_parser("mode", help="Set experiment operating mode")
    exp_mode_p.add_argument("mode", choices=["curated", "all", "none"], help="Operating mode (curated, all, or none)")
    exp_subparsers.add_parser("reset", help="Reset all overrides back to curated defaults")

    # web subcommand (agy-proxy web / agy-proxy antigravity-web)
    web_parser = subparsers.add_parser(
        "web",
        aliases=["antigravity-web", "web-mode"],
        help="Launch Google Antigravity in lightweight browser Web Mode (No Electron, saves ~1.6 GB RAM)",
    )
    web_parser.add_argument("--port", "-p", type=int, default=44741, help="HTTPS port for Antigravity Web UI (default: 44741)")
    web_parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address to bind Web UI (default: 127.0.0.1)")
    web_parser.add_argument("--proxy-url", type=str, default="http://127.0.0.1:8000", help="Antigravity Proxy URL (default: http://127.0.0.1:8000)")
    web_parser.add_argument("--open", "-o", action="store_true", default=False, help="Open web browser on startup (default: False, disabled to avoid tab spam)")
    web_parser.add_argument("--no-open", action="store_true", default=False, help="Explicitly prevent opening web browser on startup")
    web_parser.add_argument("--browser", "-b", type=str, default=None, help="Custom browser executable to open (e.g. helium, google-chrome, firefox)")
    web_parser.add_argument("--kill-electron", "-k", action="store_true", help="Automatically terminate running Electron instances without prompt to free RAM")
    web_parser.add_argument("--ls-path", "--bin", type=str, default=None, help="Explicit path to language_server binary")
    web_parser.add_argument("--editor", "-e", type=str, default=None, help="Path to Antigravity editor root directory (e.g. /opt/Antigravity, /opt/Antigravity IDE, /Applications/Antigravity.app)")
    web_parser.add_argument("--detach", action="store_true", help="Run in background daemon mode")
    web_parser.add_argument("--status", "-s", action="store_true", help="Check current status of Antigravity Web Mode")
    web_parser.add_argument("--debug", "--verbose", "-v", dest="verbose", action="store_true", help="Show raw language_server debug output in console (default: hidden, logged to file)")
    web_parser.add_argument("--direct", "--no-proxy", "-D", action="store_true", help="Bypass agy-proxy and connect directly to official Google backend")
    web_parser.add_argument("--restart", "-r", action="store_true", help="Force restart Antigravity Web even if already running")
    web_parser.add_argument("--ide-version", type=str, default="2.26.0", help="Reported IDE version (default: 2.26.0)")
    web_parser.add_argument("--patched", "-P", action="store_true", help="Launch with feature-patched Web UI bundle (unlocks Web Terminal, custom models, and UI enhancements)")
    web_parser.add_argument("--force-patch", "-f", "--force", action="store_true", help="Force re-extraction and re-patching of the Web UI bundle")
    web_parser.add_argument("--bundle-dir", "-d", type=str, default=None, help="Target directory for extracted & patched bundle (default: ~/.config/agy-proxy/web_ui)")
    web_parser.add_argument("--download-server", action="store_true", help="Download Antigravity Hub language_server binary for this platform to ~/.config/agy-proxy/bin/ and exit")

    # patch subcommand (agy-proxy patch [install|restore|status|restart])
    patch_parser = subparsers.add_parser(
        "patch",
        aliases=["ide-patch"],
        help="Manage Antigravity Language Server desktop patching (route Electron IDE through proxy with auto-fallback and live watchdog)",
    )
    patch_parser.add_argument("-f", "--file", "--path", "--ls-path", dest="ls_path", default=None, help="Direct path to language_server file or editor directory")
    patch_parser.add_argument("-e", "--editor", default=None, help="Path to editor installation root (e.g. /opt/Antigravity)")
    patch_parser.add_argument("--port", type=int, default=8000, help="Port where agy-proxy is running (default: 8000)")
    patch_parser.add_argument("--host", default="127.0.0.1", help="Host where agy-proxy is running (default: 127.0.0.1)")
    patch_parser.add_argument("--url", default=None, help="Full proxy URL (e.g. http://127.0.0.1:8000)")
    patch_parser.add_argument("--timeout", type=int, default=2, help="Seconds to wait for agy-proxy before falling back to Google (default: 2)")
    patch_parser.add_argument("--no-wait", action="store_true", help="Disable waiting for agy-proxy before launching")
    patch_parser.add_argument("--no-watchdog", action="store_true", help="Disable background watchdog for dynamic live switching")
    patch_parser.add_argument("-y", "--yes", action="store_true", help="Automatically confirm prompts")

    patch_sub = patch_parser.add_subparsers(dest="patch_action", help="Patch action")

    p_inst = patch_sub.add_parser("install", aliases=["patch"], help="Install wrapper script to route language_server through agy-proxy")
    p_inst.add_argument("target", nargs="?", default=None, help="Path to editor directory or language_server file")
    p_inst.add_argument("-f", "--file", "--path", "--ls-path", dest="ls_path", default=None, help="Direct path to language_server file or editor directory")
    p_inst.add_argument("-e", "--editor", default=None, help="Path to editor installation root (e.g. /opt/Antigravity)")
    p_inst.add_argument("--port", type=int, default=8000, help="Port where agy-proxy is running (default: 8000)")
    p_inst.add_argument("--host", default="127.0.0.1", help="Host where agy-proxy is running (default: 127.0.0.1)")
    p_inst.add_argument("--url", default=None, help="Full proxy URL (e.g. http://127.0.0.1:8000)")
    p_inst.add_argument("--timeout", type=int, default=2, help="Seconds to wait for agy-proxy before falling back to Google (default: 2)")
    p_inst.add_argument("--no-wait", action="store_true", help="Disable waiting for agy-proxy before launching")
    p_inst.add_argument("--no-watchdog", action="store_true", help="Disable background watchdog for dynamic live switching")
    p_inst.add_argument("-y", "--yes", action="store_true", help="Automatically confirm prompts")

    p_rest = patch_sub.add_parser("restore", aliases=["unpatch"], help="Restore original language_server binary")
    p_rest.add_argument("target", nargs="?", default=None, help="Path to editor directory or language_server file")
    p_rest.add_argument("-f", "--file", "--path", "--ls-path", dest="ls_path", default=None, help="Path to language_server binary or directory")
    p_rest.add_argument("-e", "--editor", default=None, help="Path to editor installation directory")
    p_rest.add_argument("-y", "--yes", action="store_true", help="Automatically confirm prompts")

    p_stat = patch_sub.add_parser("status", help="Display current patching and process status")
    p_stat.add_argument("target", nargs="?", default=None, help="Path to editor directory or language_server file")
    p_stat.add_argument("-f", "--file", "--path", "--ls-path", dest="ls_path", default=None, help="Path to language_server binary or directory")
    p_stat.add_argument("-e", "--editor", default=None, help="Path to editor installation directory")
    p_stat.add_argument("--url", default=None, help="Proxy URL to test")

    p_re = patch_sub.add_parser("restart", aliases=["kill"], help="Restart running language_server so Antigravity respawns it")
    p_re.add_argument("target", nargs="?", default=None, help="Path to editor directory or language_server file")
    p_re.add_argument("-f", "--file", "--path", "--ls-path", dest="ls_path", default=None, help="Path to language_server binary or directory")
    p_re.add_argument("-e", "--editor", default=None, help="Path to editor installation directory")

    # config subcommand (agy-proxy config [get|set|list|reset])
    config_parser = subparsers.add_parser("config", help="Manage persistent user configuration (e.g. language_server and editor paths)")
    config_sub = config_parser.add_subparsers(dest="config_action", help="Config action")

    cfg_set = config_sub.add_parser("set", help="Set a configuration value")
    cfg_set.add_argument("key", help="Configuration key to set (e.g. ls-path, editor)")
    cfg_set.add_argument("value", help="Value to assign")

    cfg_get = config_sub.add_parser("get", help="Get a configuration value")
    cfg_get.add_argument("key", help="Configuration key to retrieve (e.g. ls-path, editor)")

    config_sub.add_parser("list", aliases=["show"], help="List all current configuration values")

    cfg_unset = config_sub.add_parser("unset", aliases=["reset", "delete"], help="Unset a configuration key")
    cfg_unset.add_argument("key", nargs="?", default=None, help="Configuration key to remove (or omit to reset all)")

    # service subcommand (agy-proxy service [install|uninstall|status|start|stop|restart])
    service_parser = subparsers.add_parser("service", help="Manage background system service (systemd user unit on Linux, launchd on macOS)")
    service_sub = service_parser.add_subparsers(dest="service_action", help="Service action")

    srv_install = service_sub.add_parser("install", help="Install and enable user background service")
    srv_install.add_argument("--port", type=int, default=8000, help="Port for background proxy service (default: 8000)")
    srv_install.add_argument("--host", type=str, default="127.0.0.1", help="Host address to bind to (default: 127.0.0.1)")

    service_sub.add_parser("uninstall", help="Uninstall and remove background service")
    service_sub.add_parser("status", help="Check status of background service")
    service_sub.add_parser("start", help="Start background service")
    service_sub.add_parser("stop", help="Stop background service")
    service_sub.add_parser("restart", help="Restart background service")

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
    parser.add_argument(
        "--experiments-mode",
        type=str,
        default=None,
        choices=["curated", "all_safe", "passthrough"],
        help="Experimental features operating mode (default: curated)",
    )
    parser.add_argument(
        "--routing-strategy",
        type=str,
        default=None,
        choices=["primary", "round_robin", "balanced", "lru"],
        help="Account pool routing strategy: primary (default, priority to primary account) or round_robin (rotates all active accounts evenly)",
    )
    parser.add_argument(
        "--round-robin",
        "--load-balance",
        dest="round_robin",
        action="store_true",
        default=False,
        help="Rotate requests across all active pool accounts in round-robin fashion",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="Run Antigravity Proxy with a system tray icon",
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
    elif args.subcommand == "doctor":
        from agy_proxy.doctor import run_doctor
        healthy = asyncio.run(run_doctor(
            host=getattr(args, "host", "127.0.0.1"),
            port=getattr(args, "port", 8000),
            cloudflare_url=getattr(args, "cloudflare_url", None),
        ))
        sys.exit(0 if healthy else 1)
    elif args.subcommand == "switch":
        target_env = "all"
        env_set = False
        if getattr(args, "cli", False):
            target_env = "cli"
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
    elif args.subcommand in ("auth", "accounts"):
        if args.auth_action == "login":
            asyncio.run(handle_auth_login())
            return
        elif args.auth_action == "export":
            handle_accounts_export(output=getattr(args, "output", None))
            return
        elif args.auth_action == "import":
            handle_accounts_import(
                file_path=getattr(args, "file"),
                merge=getattr(args, "merge", True),
            )
            return
        elif args.auth_action in ("api", "apikey"):
            key_val = getattr(args, "key", None) or getattr(args, "positional_key", None)
            name_val = getattr(args, "name", None) or getattr(args, "positional_name", None)
            asyncio.run(handle_auth_apikey(api_key=key_val, name=name_val))
            return
        elif args.auth_action in ("enable", "resume", "unpause"):
            handle_auth_toggle(target=getattr(args, "target", "all"), enabled=True)
            return
        elif args.auth_action in ("disable", "pause"):
            handle_auth_toggle(target=getattr(args, "target", "all"), enabled=False)
            return
        elif args.auth_action in ("remove", "delete", "rm"):
            handle_auth_remove(target=getattr(args, "target", None))
            return
        elif args.auth_action == "list":
            asyncio.run(handle_auth_list())
            return
        elif args.auth_action == "switch":
            target_env = "all"
            env_set = False
            if getattr(args, "cli", False):
                target_env = "cli"
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
        elif not args.auth_action:
            asyncio.run(handle_auth_list())
            return
        else:
            parser.print_help()
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
    elif args.subcommand in ("experiments", "exp"):
        handle_experiments_command(args)
        return
    elif args.subcommand == "config":
        handle_config_command(args)
        return
    elif args.subcommand == "service":
        handle_service_command(args)
        return
    elif args.subcommand in ("patch", "ide-patch"):
        handle_patch_command(args)
        return
    elif args.subcommand in ("web", "antigravity-web", "web-mode"):
        from agy_proxy.web_launcher import launch_antigravity_web, get_web_status, download_language_server, detect_platform_key
        if getattr(args, "download_server", False):
            pkey = detect_platform_key()
            result = download_language_server(interactive=True, platform_key=pkey)
            if result:
                from agy_proxy.config import set_configured_ls_path
                set_configured_ls_path(str(result))
                console.print(f"[bold green]✅ language_server downloaded and configured:[/bold green] [cyan]{result}[/cyan]")
                console.print("[dim]You can now run: agy-proxy web -P[/dim]")
                sys.exit(0)
            else:
                sys.exit(1)
        if getattr(args, "status", False):
            status = get_web_status(port=getattr(args, "port", 44741))
            st_color = "bold green" if status["status"] == "running" else "bold red"
            el_color = "yellow" if status["electron_running"] else "green"
            console.print(Panel(
                f"[bold cyan]Web Status:[/bold cyan] [{st_color}]{status['status'].upper()}[/{st_color}]\n"
                f"[bold cyan]Web URL:[/bold cyan] {status['url']}\n"
                f"[bold cyan]Server PID:[/bold cyan] {status['pid'] or 'N/A'}\n"
                f"[bold cyan]Port in use:[/bold cyan] {'Yes' if status['port_in_use'] else 'No'}\n"
                f"[bold cyan]Electron GUI:[/bold cyan] [{el_color}]{'Running (High RAM)' if status['electron_running'] else 'Not Running (Optimized)'}[/{el_color}]\n"
                f"[bold cyan]RAM Saved:[/bold cyan] [bold green]~{status['estimated_ram_saved_mb']} MB[/bold green]",
                title="[bold white]Google Antigravity Web Mode Status[/bold white]",
                border_style="cyan",
            ))
            return
        code = launch_antigravity_web(
            port=getattr(args, "port", 44741),
            host=getattr(args, "host", "127.0.0.1"),
            proxy_url=getattr(args, "proxy_url", "http://127.0.0.1:8000"),
            proxy_port=getattr(args, "proxy_port", 8000),
            open_browser=getattr(args, "open", False) and not getattr(args, "no_open", False),
            browser=getattr(args, "browser", None),
            kill_electron=getattr(args, "kill_electron", False),
            ls_path=getattr(args, "ls_path", None),
            editor_path=getattr(args, "editor", None),
            detach=getattr(args, "detach", False),
            ide_version=getattr(args, "ide_version", "2.26.0"),
            verbose=getattr(args, "verbose", False),
            direct=getattr(args, "direct", False),
            restart=getattr(args, "restart", False),
            patched=getattr(args, "patched", False),
            force_patch=getattr(args, "force_patch", False),
            bundle_dir=getattr(args, "bundle_dir", None),
        )
        sys.exit(code)


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

    if getattr(args, "experiments_mode", None):
        from agy_proxy.experiments import set_experiment_mode
        try:
            set_experiment_mode(args.experiments_mode)
        except Exception:
            pass

    # Configure logging
    effective_log_level = "debug" if args.debug else args.log_level
    if args.debug or effective_log_level.lower() == "debug":
        os.environ["AGY_DEBUG"] = "1"
        log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    else:
        os.environ.pop("AGY_DEBUG", None)
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
        POLLING_PATHS = (
            "/api/cache/stats",
            "/api/context/settings",
            "/api/experiments",
            "/api/accounts",
            "/api/processes/status",
            "/api/processes",
            "/api/quota",
            "/api/usage",
            "/health",
            "/v1internal:recordCodeAssistMetrics",
            "/v1internal:listExperiments",
            "/v1internal:loadCodeAssist",
            "/v1internal:fetchUserInfo",
            "/v1internal:fetchAvailableModels",
            "/v1internal:retrieveUserQuotaSummary",
            "/v1internal/cascadeNuxes",
        )

        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return not any(p in msg for p in self.POLLING_PATHS)

    if not (args.debug or effective_log_level.lower() == "debug"):
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
    effective_routing_strategy = "round_robin" if getattr(args, "round_robin", False) else getattr(args, "routing_strategy", None)
    pool = AccountPool(token_path=args.token_file, routing_strategy=effective_routing_strategy)
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
    shutdown_timeout = float(os.environ.get("AGY_PROXY_SHUTDOWN_TIMEOUT", "2.0"))
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level=uvicorn_log_level,
        access_log=bool(args.debug or effective_log_level.lower() == "debug"),
        timeout_graceful_shutdown=shutdown_timeout,
    )
    server = uvicorn.Server(config)

    if getattr(args, "tray", False):
        try:
            from agy_proxy.tray import start_tray_in_thread
            start_tray_in_thread(port=args.port)
            console.print("[dim]System tray icon started.[/dim]")
        except Exception as e:
            console.print(f"[bold red]Failed to start system tray:[/bold red] {e}")

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
