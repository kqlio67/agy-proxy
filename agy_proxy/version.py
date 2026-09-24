"""
Version management and release bumping automation for Antigravity Proxy.
"""

import re
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

console = Console()


def calculate_next_version(current: str, increment: str) -> str:
    """
    Calculates the next semantic version given a current version and an increment
    (patch, minor, major) or explicit version string.
    """
    cleaned_current = current.strip().lstrip("v")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", cleaned_current)
    if not match:
        raise ValueError(f"Invalid current version format: '{current}'")
    major, minor, patch, suffix = int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4)

    inc = increment.strip().lower().lstrip("v")
    if inc == "patch":
        return f"{major}.{minor}.{patch + 1}"
    elif inc == "minor":
        return f"{major}.{minor + 1}.0"
    elif inc == "major":
        return f"{major + 1}.0.0"
    elif re.match(r"^\d+\.\d+\.\d+", inc):
        return inc
    else:
        raise ValueError(
            f"Unknown bump target '{increment}'. Use 'patch', 'minor', 'major', or an explicit version like '1.5.0'."
        )


def bump_version(
    target: str = "patch",
    dry_run: bool = False,
    project_root: Path | None = None,
) -> dict[str, Any]:
    """
    Bumps the version in agy_proxy/__init__.py and pyproject.toml.
    Returns a dictionary with current_version, next_version, dry_run, and updated_files.
    """
    from agy_proxy import __version__ as current_version

    next_ver = calculate_next_version(current_version, target)

    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    init_file = project_root / "agy_proxy" / "__init__.py"
    pyproject_file = project_root / "pyproject.toml"

    if not init_file.exists() or not pyproject_file.exists():
        raise FileNotFoundError(
            f"Cannot find project configuration files in '{project_root}'. "
            "Make sure you are executing the bump command inside the agy-proxy repository."
        )

    updated_files = []

    # 1. Update agy_proxy/__init__.py
    init_content = init_file.read_text(encoding="utf-8")
    new_init_content = re.sub(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{next_ver}"',
        init_content,
        count=1,
    )
    if new_init_content == init_content:
        raise ValueError(f"Could not locate '__version__ = ...' pattern in {init_file}")

    if not dry_run:
        init_file.write_text(new_init_content, encoding="utf-8")
    updated_files.append(init_file)

    # 2. Update pyproject.toml
    pyproject_content = pyproject_file.read_text(encoding="utf-8")
    new_pyproject_content = re.sub(
        r'version\s*=\s*["\'][^"\']+["\']',
        f'version = "{next_ver}"',
        pyproject_content,
        count=1,
    )
    if new_pyproject_content == pyproject_content:
        raise ValueError(f"Could not locate 'version = ...' pattern in {pyproject_file}")

    if not dry_run:
        pyproject_file.write_text(new_pyproject_content, encoding="utf-8")
    updated_files.append(pyproject_file)

    return {
        "current_version": current_version,
        "next_version": next_ver,
        "dry_run": dry_run,
        "updated_files": [str(f) for f in updated_files],
    }


def handle_bump_command(target: str = "patch", dry_run: bool = False):
    """CLI handler for `agy-proxy bump`."""
    try:
        res = bump_version(target=target, dry_run=dry_run)
        curr = res["current_version"]
        nxt = res["next_version"]

        if dry_run:
            console.print(Panel(
                f"[bold yellow]🔍 DRY RUN (No files modified)[/bold yellow]\n\n"
                f"Current Version: [dim]v{curr}[/dim]\n"
                f"Projected Version: [bold green]v{nxt}[/bold green]\n\n"
                f"Target Files:\n" + "\n".join(f"  • [cyan]{f}[/cyan]" for f in res["updated_files"]),
                title="Antigravity Proxy Version Bump",
                border_style="yellow",
            ))
        else:
            console.print(Panel(
                f"[bold green]✅ Version successfully bumped![/bold green]\n\n"
                f"Previous Version: [dim]v{curr}[/dim]\n"
                f"New Version:      [bold cyan]v{nxt}[/bold cyan]\n\n"
                f"Updated Files:\n" + "\n".join(f"  • [green]{f}[/green]" for f in res["updated_files"]) +
                f"\n\n[dim]Note: Web Dashboard UI automatically reflects v{nxt} on launch.[/dim]",
                title="Antigravity Proxy Version Bump",
                border_style="green",
            ))
    except Exception as e:
        console.print(f"[bold red]❌ Error bumping version:[/bold red] {e}", file=sys.stderr)
        sys.exit(1)
