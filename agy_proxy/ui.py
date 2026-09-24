"""
Web Dashboard and Chat Playground UI Template Loader for Antigravity Proxy.
Loads and caches the dashboard HTML template from the templates directory,
with support for standalone PyInstaller binaries and development hot-reloading.
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger("agy_proxy.ui")


def _resolve_template_path() -> Path:
    """Resolves the absolute path to the dashboard.html template file."""
    # 1. PyInstaller frozen standalone binary path
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(sys._MEIPASS)
        candidate1 = meipass / "agy_proxy" / "templates" / "dashboard.html"
        if candidate1.exists():
            return candidate1
        candidate2 = meipass / "templates" / "dashboard.html"
        if candidate2.exists():
            return candidate2
        candidate3 = meipass / "agy_proxy" / "static" / "dashboard.html"
        if candidate3.exists():
            return candidate3

    # 2. Standard package directory path
    pkg_dir = Path(__file__).parent
    candidate_pkg = pkg_dir / "templates" / "dashboard.html"
    if candidate_pkg.exists():
        return candidate_pkg

    # 3. Static fallback path
    candidate_static = pkg_dir / "static" / "dashboard.html"
    if candidate_static.exists():
        return candidate_static

    return candidate_pkg


TEMPLATE_PATH = _resolve_template_path()
_cached_dashboard_html: str | None = None
_last_mtime: float = 0.0


def get_dashboard_html(force_reload: bool = False) -> str:
    """
    Returns the dashboard HTML content.
    Caches content in memory, but automatically reloads if the template file
    is modified on disk during local development.
    """
    global _cached_dashboard_html, _last_mtime

    template_file = _resolve_template_path()

    if not template_file.exists():
        if _cached_dashboard_html:
            return _cached_dashboard_html
        logger.error("Dashboard template not found at %s", template_file)
        return """<!DOCTYPE html>
<html><head><title>Antigravity Proxy</title></head>
<body style="font-family:sans-serif;padding:2rem;background:#0f172a;color:#fff;">
<h2>Antigravity Proxy</h2>
<p>Dashboard template file not found at <code>""" + str(template_file) + """</code></p>
<p>API endpoints are functional at <code>/v1</code>.</p>
</body></html>"""

    try:
        current_mtime = template_file.stat().st_mtime
        if force_reload or _cached_dashboard_html is None or current_mtime > _last_mtime:
            from agy_proxy import __version__ as APP_VERSION
            raw_html = template_file.read_text(encoding="utf-8")
            _cached_dashboard_html = (
                raw_html
                .replace("{{APP_VERSION}}", APP_VERSION)
                .replace("{{ APP_VERSION }}", APP_VERSION)
            )
            _last_mtime = current_mtime
            logger.debug("Loaded dashboard HTML template from %s (%d bytes, v%s)", template_file, len(_cached_dashboard_html), APP_VERSION)
    except Exception as e:
        logger.error("Failed to read dashboard template from %s: %s", template_file, e)
        if _cached_dashboard_html is None:
            _cached_dashboard_html = "<h1>Antigravity Proxy</h1>"

    return _cached_dashboard_html


# Backward compatibility export
DASHBOARD_HTML = get_dashboard_html()
