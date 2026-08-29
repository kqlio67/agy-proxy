"""
Command-line interface and entry point for Antigravity Proxy.
Supports starting the proxy server and interactive Multi-Account authentication commands.
"""

import argparse
import asyncio
import logging
import os
import sys
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agy_proxy.auth import AccountPool, AuthManager
from agy_proxy.server import create_app

console = Console()


def print_banner(host: str, port: int, pool: AccountPool, api_key: str = None, update_info: dict = None):
    url = f"http://{host}:{port}"
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", no_wrap=True)
    table.add_column("Value")
    table.add_row("[bold cyan]Web Dashboard & Pool UI:[/bold cyan]", f"[bold green]{url}[/bold green]")
    table.add_row("[bold cyan]OpenAI API Base:[/bold cyan]", f"[bold yellow]{url}/v1[/bold yellow]")
    table.add_row("[bold cyan]Anthropic API Base:[/bold cyan]", f"[bold magenta]{url}/v1[/bold magenta]")
    table.add_row("[bold cyan]Accounts in Pool:[/bold cyan]", f"[bold white]{len(pool.accounts)} active account(s)[/bold white]")

    for acc in pool.accounts.values():
        tag = "[bold green](Primary)[/bold green]" if acc.is_primary else "[blue](Secondary)[/blue]"
        email = f"[white]{acc.email}[/white]" if acc.email else "[dim]unknown@gmail.com[/dim]"
        proj = f"[dim]Project: {acc.project_id or 'default'}[/dim]"
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
        title="[bold white]⚡ Google Antigravity AI Proxy (Multi-Account Enabled)[/bold white]",
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
            g_pct = int(q_details.get("gemini", {}).get("percent", 100))
            c_pct = int(q_details.get("3p", {}).get("percent", 100))
            gemini_q = f"[bold red]{g_pct}%[/bold red]" if g_pct <= 0 else f"{g_pct}%"
            claude_q = f"[bold red]{c_pct}%[/bold red]" if c_pct <= 0 else f"{c_pct}%"

        table.add_row(
            display_name,
            acc_type,
            acc.email or "N/A",
            gemini_q,
            claude_q,
            str(acc.total_requests),
        )

    console.print(table)


def main():
    parser = argparse.ArgumentParser(
        description="Antigravity Proxy - OpenAI & Anthropic compatible API server for Antigravity with Multi-Account pooling."
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand to execute")

    # auth subcommand
    auth_parser = subparsers.add_parser("auth", help="Manage Antigravity Google accounts in pool")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action", help="Auth action")
    auth_subparsers.add_parser("login", help="Log in a Google account via browser OAuth PKCE")
    auth_subparsers.add_parser("list", help="List all accounts and quotas in pool")

    # Dedicated API key subcommands: `auth api` and `auth apikey`
    for alias_cmd in ("api", "apikey"):
        api_sub = auth_subparsers.add_parser(alias_cmd, help="Add a Google AI Studio Gemini API Key")
        api_sub.add_argument("positional_key", nargs="?", default=None, help="Gemini API Key (AIza...)")
        api_sub.add_argument("positional_name", nargs="?", default=None, help="Friendly display name for this API key")
        api_sub.add_argument("--key", "-k", type=str, default=None, help="Gemini API Key (AIza...)")
        api_sub.add_argument("--name", "-n", type=str, default=None, help="Friendly display name for this API key")

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

    args = parser.parse_args()

    if args.cloudflare_url:
        os.environ["CLOUDFLARE_UPSTREAM_URL"] = args.cloudflare_url.rstrip("/")

    # Handle subcommands
    if args.subcommand == "auth":
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
        else:
            auth_parser.print_help()
            return

    # Configure logging
    effective_log_level = "debug" if args.debug else args.log_level
    logging.basicConfig(
        level=getattr(logging, effective_log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Silence noisy HTTP client and server access logs in normal mode (unless --debug is explicitly enabled)
    if not args.debug and effective_log_level.lower() != "debug":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("urllib3").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

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
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        log_level="warning" if (not args.debug and effective_log_level.lower() != "debug") else effective_log_level,
        access_log=bool(args.debug or effective_log_level.lower() == "debug"),
    )
    server = uvicorn.Server(config)

    try:
        server.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        if not args.debug:
            console.print(f"\n[bold yellow]Proxy stopped.[/bold yellow]")
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
