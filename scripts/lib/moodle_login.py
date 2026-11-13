"""moodle_login — one-time Edge login that persists an SSO profile.

Runs under the dedicated venv at ~/.local/lib/univault/venv (Playwright).
The browser profile lives outside the repo:
  macOS: ~/Library/Application Support/univault/edge-profile
The Moodle cookies extracted after login are stored via moodle_auth (0600).
Passwords are never handled; Okta/MFA stay manual.

Pure helpers are importable without Playwright (it is imported lazily).
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit


def default_profile_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "univault"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "univault"
    return base / "edge-profile"


def cookies_to_header(cookies, host: str) -> str:
    """Build a Cookie header from Playwright cookie dicts, exact-host only."""
    host = (host or "").lower().lstrip(".")
    pairs = []
    for c in cookies or []:
        dom = (c.get("domain") or "").lower().lstrip(".")
        if dom == host:
            pairs.append(f"{c.get('name')}={c.get('value')}")
    return "; ".join(pairs)


def is_dashboard_url(url: str, host: str) -> bool:
    """True only for an authenticated Moodle page (never Okta/login pages)."""
    p = urlsplit(url or "")
    if (p.hostname or "").lower() != (host or "").lower():
        return False
    path = (p.path or "/").lower()
    if "login" in path or "sso" in path or "saml" in path:
        return False
    return path.startswith("/my") or path.startswith("/course") or path == "/"


def _wait_for_login(page, host: str, timeout: int, log) -> bool:
    deadline = time.time() + max(30, timeout)
    last_note = 0.0
    while time.time() < deadline:
        if is_dashboard_url(page.url, host):
            return True
        if time.time() - last_note > 10:
            log("waiting for Monash login (Okta/MFA) in the Edge window…")
            last_note = time.time()
        page.wait_for_timeout(2000)
    return False


def run(base: str, profile: str | None, timeout: int, check_only: bool, log=print) -> dict:
    from playwright.sync_api import sync_playwright  # venv-only dependency

    host = urlsplit(base).hostname or ""
    profile_dir = Path(profile).expanduser() if profile else default_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(profile_dir, 0o700)
    except OSError:
        pass

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile_dir), channel="msedge", headless=check_only,
            args=["--no-first-run", "--no-default-browser-check"],
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(base.rstrip("/") + "/my/", wait_until="domcontentloaded", timeout=60000)
            if check_only:
                return {"logged_in": is_dashboard_url(page.url, host), "url": page.url}
            if not is_dashboard_url(page.url, host):
                log("Edge window opened — complete the Monash login there (password + MFA).")
                if not _wait_for_login(page, host, timeout, log):
                    return {"logged_in": False, "error": "login timed out"}
            header = cookies_to_header(ctx.cookies(), host)
            if "MoodleSession=" not in header:
                return {"logged_in": False, "error": "MoodleSession cookie missing after login"}
            from lib import moodle_auth  # stdlib side of the connector
            moodle_auth.save_cookie(header, base)
            return {"logged_in": True, "url": page.url}
        finally:
            ctx.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="One-time Monash Moodle login using a persistent Edge profile.")
    ap.add_argument("--base", default=os.environ.get("MOODLE_BASE", "https://learning.monash.edu"))
    ap.add_argument("--profile", default=None, help="profile directory (default: platform app-support dir)")
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for the login (default 600)")
    ap.add_argument("--check", action="store_true", help="headless check only; do not open a window")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        result = run(args.base, args.profile, args.timeout, args.check, log=lambda m: print(m, file=sys.stderr))
    except Exception as exc:  # diagnostics only; never print cookies
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(3)
    if args.json:
        print(json.dumps(result))
    elif result.get("logged_in"):
        print("logged in; Moodle cookie stored")
    else:
        print(f"login failed: {result.get('error', 'not logged in')}", file=sys.stderr)
    sys.exit(0 if result.get("logged_in") else 2)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    main()
