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


def print_banner(host: str, port: int, pool: AccountPool, api_key: str = None):
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
    code_input = input("Authorization Code / URL: ").strip()

    if not code_input:
        console.print("[bold red]Aborted: No code entered.[/bold red]")
        return

    console.print("\n[dim]Exchanging authorization code for OAuth tokens...[/dim]")
    try:
        acc = await pool.complete_oauth_flow(code_or_url=code_input, verifier=verifier, state=state)
        console.print(f"[bold green]✓ Successfully added account:[/bold green] [bold white]{acc.email}[/bold white] (ID: {acc.account_id})")
        console.print(f"Project: [cyan]{acc.project_id}[/cyan], Tier: [magenta]{acc.tier_info.get('name', 'Antigravity')}[/magenta]")
    except Exception as e:
        console.print(f"[bold red]✗ Failed to add account:[/bold red] {e}")


async def handle_auth_list():
    """Lists all accounts in the pool and their quota fractions."""
    pool = AccountPool()
    pool.load_accounts()
    await pool.initialize_all()

    table = Table(title="Antigravity Account Pool", border_style="blue")
    table.add_column("Account ID", style="cyan")
    table.add_column("Email", style="white")
    table.add_column("Project", style="dim")
    table.add_column("Tier", style="magenta")
    table.add_column("Gemini 5h Quota", style="green")
    table.add_column("Claude/3P Quota", style="yellow")
    table.add_column("Requests", justify="right")

    for acc in pool.accounts.values():
        gemini_q = "100%"
        claude_q = "100%"
        if acc.quota_summary.get("groups"):
            for g in acc.quota_summary["groups"]:
                name = g.get("displayName", "").lower()
                b = g.get("buckets", [{}])[0]
                pct = f"{int(b.get('remainingFraction', 1.0) * 100)}%"
                if "claude" in name or "gpt" in name:
                    claude_q = pct
                else:
                    gemini_q = pct

        table.add_row(
            f"{acc.account_id} {'⭐' if acc.is_primary else ''}",
            acc.email,
            acc.project_id or "N/A",
            acc.tier_info.get("name", "Antigravity"),
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
    auth_subparsers.add_parser("login", help="Log in a new Google account via OAuth PKCE")
    auth_subparsers.add_parser("list", help="List all accounts and quotas in pool")

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
        elif args.auth_action == "list":
            asyncio.run(handle_auth_list())
            return
        else:
            auth_parser.print_help()
            return

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Initialize AccountPool
    pool = AccountPool(token_path=args.token_file)
    pool.load_accounts()

    if not pool.accounts:
        console.print(
            f"[bold red]Warning:[/bold red] No accounts loaded.\n"
            "Run `python main.py auth login` or log in with `agy auth login` to add an account."
        )

    # Create FastAPI app
    app = create_app(account_pool=pool, api_key=args.api_key)

    # Display startup info
    print_banner(
        host=args.host,
        port=args.port,
        pool=pool,
        api_key=args.api_key,
    )

    # Run Uvicorn
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
