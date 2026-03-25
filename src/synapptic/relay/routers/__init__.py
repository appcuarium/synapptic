"""Shared utilities for relay routers."""

from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def render_page(template_name: str, active: str = "") -> str:
    """Read a template and inject the shared nav bar.

    Reads nav.html fresh each time so changes take effect without restart.
    """
    html = (TEMPLATES_DIR / template_name).read_text()
    nav = (TEMPLATES_DIR / "nav.html").read_text()
    nav = nav.replace("{{DASHBOARD_ACTIVE}}", 'class="syn-active"' if active == "dashboard" else "")
    nav = nav.replace("{{SESSIONS_ACTIVE}}", 'class="syn-active"' if active == "sessions" else "")
    return html.replace("{{NAV}}", nav)
