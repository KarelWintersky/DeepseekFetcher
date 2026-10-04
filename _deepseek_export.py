#!/usr/bin/env python3
"""
Export DeepSeek shared conversation to Markdown.

Requires uv — the fast Python package installer.
Install: curl -LsSf https://astral.sh/uv/install.sh | sh

Usage (just run it — venv is created automatically):
    python deepseek_export.py <share_url_or_id> [-o output.md] [--json] [--txt] [--token TOKEN]

Examples:
    python deepseek_export.py https://chat.deepseek.com/share/nvy7v2ps1r6e2wyoyj
    python deepseek_export.py nvy7v2ps1r6e2wyoyj -o my_chat.md
    python deepseek_export.py nvy7v2ps1r6e2wyoyj --json -o chat.json
    python deepseek_export.py nvy7v2ps1r6e2wyoyj --txt

First run will create .venv/ and install requests via uv.
"""

# ── auto-venv bootstrap ─────────────────────────────────────────────────────
import os
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_VENV_DIR = _SCRIPT_DIR / ".venv"
_VENV_PYTHON = _VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

_USAGE_TEXT = (
    "Export DeepSeek shared conversations to Markdown/JSON/text.\n"
    "Run with -h for full help."
)


def _find_uv() -> str | None:
    return shutil.which("uv")


def _install_uv() -> bool:
    print("[boot] uv is not installed.", flush=True)
    answer = input("Install uv now? [Y/n] ").strip().lower()
    if answer and answer != "y":
        print("Aborted. Install uv manually: curl -LsSf https://astral.sh/uv/install.sh | sh")
        return False
    print("[boot] Installing uv …", flush=True)
    try:
        subprocess.check_call(
            ["curl", "-LsSf", "https://astral.sh/uv/install.sh", "|", "sh"],
            cwd=_SCRIPT_DIR,
        )
    except Exception:
        # curl ... | sh doesn't work via subprocess.check_call, use shell
        subprocess.check_call("curl -LsSf https://astral.sh/uv/install.sh | sh", shell=True)
    if not _find_uv():
        print("[boot] uv installation failed. Install manually: curl -LsSf https://astral.sh/uv/install.sh | sh")
        return False
    return True


def _ensure_venv():
    """Create venv + install deps if missing, then re-exec inside it."""
    if sys.prefix != sys.base_prefix:
        return  # already inside a venv

    print(_USAGE_TEXT, flush=True)
    print(flush=True)

    uv = _find_uv()
    if not uv:
        if not _install_uv():
            sys.exit(1)
        uv = _find_uv()
        if not uv:
            sys.exit(1)

    if not _VENV_PYTHON.exists():
        print("[boot] Creating .venv …", flush=True)
        subprocess.check_call([uv, "venv", str(_VENV_DIR)], cwd=_SCRIPT_DIR)
        print("[boot] Installing dependencies …", flush=True)
        subprocess.check_call(
            [uv, "pip", "install", "--python", str(_VENV_PYTHON), "requests"],
            cwd=_SCRIPT_DIR,
        )

    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])


_ensure_venv()
# ── end bootstrap ────────────────────────────────────────────────────────────

import argparse
import json
import re
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SHARE_URL_RE = re.compile(
    r"(?:https?://chat\.deepseek\.com)?/share/(?P<id>[A-Za-z0-9_-]+)"
)


def extract_share_id(raw: str) -> str:
    m = SHARE_URL_RE.search(raw)
    if m:
        return m.group("id")
    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", raw):
        return raw
    raise ValueError(f"Cannot extract share id from: {raw}")


HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://chat.deepseek.com/",
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
}


# ---------------------------------------------------------------------------
# Strategy 1 — plain requests (works when WAF is not aggressive)
# ---------------------------------------------------------------------------

def fetch_via_requests(share_id: str, token: str | None = None) -> dict:
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get("https://chat.deepseek.com/", timeout=15)

    if token:
        s.headers["authorization"] = f"Bearer {token}"

    url = f"https://chat.deepseek.com/api/v0/share/content?share_id={share_id}"
    r = s.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    if data.get("code") != 0:
        raise RuntimeError(f"API error: {data.get('msg', data)}")
    return data


# ---------------------------------------------------------------------------
# Strategy 2 — Playwright headless browser (bypasses WAF reliably)
# ---------------------------------------------------------------------------

def fetch_via_playwright(share_id: str, token: str | None = None) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed. Run:\n"
            "  uv pip install playwright && uv run playwright install chromium"
        )

    captured = {}

    def on_response(response):
        if "/api/v0/share/content" in response.url:
            try:
                captured["data"] = response.json()
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["user-agent"],
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        page.on("response", on_response)

        if token:
            ctx.add_cookies([{
                "name": "ds_session_id",
                "value": token,
                "domain": "chat.deepseek.com",
                "path": "/",
            }])

        page.goto(
            f"https://chat.deepseek.com/share/{share_id}",
            wait_until="networkidle",
            timeout=30000,
        )
        page.wait_for_timeout(3000)
        browser.close()

    if not captured.get("data"):
        raise RuntimeError("Playwright could not capture the share API response")
    data = captured["data"]
    if data.get("code") != 0:
        raise RuntimeError(f"API error: {data.get('msg', data)}")
    return data


# ---------------------------------------------------------------------------
# Main fetch (tries requests first, falls back to playwright)
# ---------------------------------------------------------------------------

def fetch_share(share_id: str, token: str | None = None) -> dict:
    try:
        print("[1/2] Trying direct API…", end=" ", flush=True)
        data = fetch_via_requests(share_id, token)
        print("OK")
        return data
    except Exception as e:
        print(f"failed ({e})")

    print("[2/2] Falling back to Playwright…", end=" ", flush=True)
    try:
        data = fetch_via_playwright(share_id, token)
        print("OK")
        return data
    except Exception as e:
        print(f"failed ({e})")
        raise RuntimeError(
            "Both strategies failed. If WAF is blocking you, install Playwright:\n"
            "  uv pip install playwright && uv run playwright install chromium\n"
            "Or pass your token with --token."
        )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _ts(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


# ---------------------------------------------------------------------------
# Normalisation — the share API serves the message body in two shapes:
#   * legacy:  {"role": ..., "content": "...", "thinking_content": "..."}
#   * current: {"role": ..., "fragments": [{"type": "REQUEST|THINK|RESPONSE",
#              "content": "...", "references": [...]}]}
# Both are handled here so the formatters only ever see one shape.
# ---------------------------------------------------------------------------

THINK_FRAGMENT_TYPES = {"THINK", "THINKING", "REASONING", "REASON"}
SEARCH_FRAGMENT_TYPES = {"SEARCH", "SEARCH_RESULT", "SEARCH_RESULTS"}

_REFERENCE_WRAPPERS = ("search_result", "reference", "result", "webpage", "site", "source")


def _biz_data(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    node = data.get("data")
    if not isinstance(node, dict):
        return {}
    biz = node.get("biz_data")
    return biz if isinstance(biz, dict) else {}


def _messages(data: dict) -> list:
    biz = _biz_data(data)
    messages = biz.get("messages")
    if not isinstance(messages, list) or not messages:
        messages = biz.get("chat_messages")
    if not isinstance(messages, list):
        messages = []
    return [m for m in messages if isinstance(m, dict)]


def _normalize_references(raw) -> list:
    """Flatten whatever shape the API used for citations into {url, title, snippet}."""
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    out, seen = [], set()
    for item in raw:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict):
            continue

        src = item
        for key in _REFERENCE_WRAPPERS:
            if isinstance(item.get(key), dict):
                src = item[key]
                break

        url = src.get("url") or src.get("link") or src.get("source_url") or ""
        title = src.get("title") or src.get("name") or ""
        snippet = src.get("snippet") or src.get("summary") or src.get("description") or ""
        if not url and not title:
            continue
        marker = url or title
        if marker in seen:
            continue
        seen.add(marker)
        out.append({"url": url, "title": title, "snippet": snippet})
    return out


def parse_message(msg: dict) -> dict:
    role = (msg.get("role") or "").upper()
    fragments = msg.get("fragments")

    if isinstance(fragments, list) and fragments:
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        search_notes: list[str] = []
        references: list[dict] = []
        elapsed = None

        for frag in fragments:
            if not isinstance(frag, dict):
                continue
            ftype = (frag.get("type") or "").upper()
            text = frag.get("content") or ""
            refs = _normalize_references(frag.get("references"))

            if ftype in THINK_FRAGMENT_TYPES:
                if text:
                    thinking_parts.append(text)
                secs = frag.get("elapsed_secs")
                if isinstance(secs, (int, float)):
                    elapsed = (elapsed or 0.0) + float(secs)
            elif ftype in SEARCH_FRAGMENT_TYPES:
                if refs:
                    references.extend(refs)
                if text:
                    search_notes.append(text)
            else:
                if text:
                    content_parts.append(text)
                references.extend(refs)

        return {
            "role": role,
            "content": "\n\n".join(content_parts),
            "thinking": "\n\n".join(thinking_parts),
            "elapsed": elapsed,
            "references": _normalize_references(references),
            "search_notes": search_notes,
            "timestamp": _ts(msg.get("inserted_at")),
        }

    return {
        "role": role,
        "content": msg.get("content") or msg.get("message") or "",
        "thinking": msg.get("thinking_content") or msg.get("reasoning_content") or "",
        "elapsed": msg.get("thinking_elapsed_secs"),
        "references": _normalize_references(msg.get("search_results") or msg.get("references")),
        "search_notes": [],
        "timestamp": _ts(msg.get("inserted_at")),
    }


def _role_header(role: str) -> str:
    if role == "USER":
        return "You"
    if role in ("ASSISTANT", "AI", "BOT"):
        return "DeepSeek"
    if role == "SYSTEM":
        return "System"
    return role.title() or "Message"


def _sources_block(references: list) -> str:
    lines = []
    for ref in references:
        url, title = ref.get("url") or "", ref.get("title") or ""
        if url and title:
            lines.append(f"- [{title}]({url})")
        elif url:
            lines.append(f"- {url}")
        elif title:
            lines.append(f"- {title}")
    return "\n".join(lines)


def to_markdown(data: dict) -> str:
    biz = _biz_data(data)
    messages = _messages(data)

    title = biz.get("title") or "DeepSeek Conversation"
    model = biz.get("model_type") or biz.get("model") or ""

    parts = [f"# {title}\n"]
    if model:
        parts.append(f"**Model:** {model}")
    parts.append("")

    if not messages:
        parts.append("_No messages found._\n")
        return "\n".join(parts)

    for msg in messages:
        parsed = parse_message(msg)
        role = parsed["role"]
        content = parsed["content"]
        if not content and not role:
            continue

        parts.append(f"## {_role_header(role)}\n")

        if parsed["timestamp"]:
            parts.append(f"_{parsed['timestamp']}_\n")

        if parsed["thinking"]:
            secs = parsed["elapsed"]
            label = f"Thinking ({secs:.0f}s)" if isinstance(secs, (int, float)) and secs else "Thinking"
            parts.append(f"<details><summary>{label}</summary>\n\n{parsed['thinking']}\n\n</details>\n")

        for note in parsed["search_notes"]:
            parts.append(f"<details><summary>Search</summary>\n\n{note}\n\n</details>\n")

        refs = _sources_block(parsed["references"])
        if refs:
            parts.append("**Sources:**\n" + refs + "\n")

        if content:
            parts.append(f"{content}\n")
        parts.append("---\n")

    return "\n".join(parts)


def to_plain_text(data: dict) -> str:
    biz = _biz_data(data)
    messages = _messages(data)
    title = biz.get("title") or "DeepSeek Conversation"

    parts = [f"=== {title} ===\n"]
    for msg in messages:
        parsed = parse_message(msg)
        role = parsed["role"]
        content = parsed["content"]
        header = _role_header(role)
        if role in ("ASSISTANT", "AI", "BOT"):
            if parsed["thinking"]:
                parts.append(f"[Thinking]\n{parsed['thinking']}\n")
            parts.append(f"[DeepSeek]\n{content}\n")
        elif role == "USER":
            parts.append(f"[User]\n{content}\n")
        else:
            parts.append(f"[{header}]\n{content}\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write("\n")
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(2)


def main():
    ap = _ArgumentParser(
        description="Export a DeepSeek shared conversation",
        epilog="Example: %(prog)s https://chat.deepseek.com/share/nvy7v2ps1r6e2wyoyj",
    )
    ap.add_argument("link", help="Share URL or share ID")
    ap.add_argument("-o", "--output", help="Output file path (default: auto-named)")
    ap.add_argument("--json", action="store_true", help="Dump raw JSON instead of Markdown")
    ap.add_argument("--txt", action="store_true", help="Dump plain text")
    ap.add_argument("--token", help="Bearer token for auth (optional, needed for private shares)")
    args = ap.parse_args()

    share_id = extract_share_id(args.link)
    print(f"Share ID: {share_id}")

    data = fetch_share(share_id, args.token)

    if args.output:
        out = Path(args.output)
    elif args.json:
        out = Path(f"export_{share_id}.json")
    elif args.txt:
        out = Path(f"export_{share_id}.txt")
    else:
        out = Path(f"export_{share_id}.md")

    if args.json:
        content = json.dumps(data, indent=2, ensure_ascii=False)
    elif args.txt:
        content = to_plain_text(data)
    else:
        content = to_markdown(data)

    out.write_text(content, encoding="utf-8")
    print(f"Saved to {out}  ({len(content)} bytes)")


if __name__ == "__main__":
    main()
