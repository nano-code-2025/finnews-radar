"""
One-shot Grok (xAI) Twitter multi-account monitoring test.

Goal:
- Use xAI API + X search tool to fetch recent posts from multiple handles
- Verify real tool return structure (id/author/timestamp/content/engagement/media/urls)
- Normalize posts (construct tweet_url, parse timestamp) and save JSON outputs

Usage (PowerShell):
  python test_grok_twitter_structured.py
  python test_grok_twitter_structured.py --max-handles 10 --mode Latest --limit 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI


DEFAULT_MODEL = "grok-4-fast"
XAI_BASE_URL = "https://api.x.ai/v1"
MAX_HANDLES_PER_REQUEST = 10


@dataclass(frozen=True)
class GroupHandles:
    group: str
    handles: list[str]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_filename(ts: str) -> str:
    return ts.replace(":", "").replace("-", "").replace("Z", "Z")


def load_twitter_accounts(config_path: Path) -> list[GroupHandles]:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    groups: list[GroupHandles] = []
    for group, group_cfg in cfg.items():
        accounts = list(group_cfg.get("accounts", []) or [])
        if accounts:
            groups.append(GroupHandles(group=group, handles=accounts))
    return groups


def build_raw_posts_prompt(handles: list[str], mode: str, limit: int) -> str:
    """Ask Grok to return ONLY raw tool posts as JSON."""
    handles_json = json.dumps(handles, ensure_ascii=False)
    return (
        "You will use the X search tool results you just fetched. "
        "Return EXACTLY one JSON object (no markdown, no code fences, no extra text).\n\n"
        "Goal: Provide the raw posts as returned by the tool, where each post is an independent object.\n"
        "Do NOT merge posts. Do NOT invent fields.\n\n"
        f"handles={handles_json}\n"
        f"mode={mode}\n"
        f"limit={limit}\n\n"
        "Output schema:\n"
        "{\n"
        '  "tool": "x_keyword_search",\n'
        '  "mode": "<mode>",\n'
        '  "limit": <limit>,\n'
        '  "posts": [ {id, conversation_id, author{name,handle,avatar,bio}, '
        "timestamp, content, engagement{likes,reposts,quotes,replies,bookmarks,views}, media, urls} ]\n"
        "}\n"
    )


def extract_response_text(resp: Any) -> str:
    """
    Best-effort extraction from xAI/OpenAI-compatible Responses API object.
    We avoid relying on private attributes; try known fields and fallbacks.
    """
    # Newer SDKs often provide output_text helper
    output_text = getattr(resp, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    texts: list[str] = []
    for out in getattr(resp, "output", []) or []:
        if getattr(out, "type", None) != "message":
            continue
        for c in getattr(out, "content", []) or []:
            # Some SDKs use "output_text" or "text"
            t = getattr(c, "text", None)
            if isinstance(t, str) and t.strip():
                texts.append(t)
    return "\n".join(texts).strip()


def construct_tweet_url(author_handle: str, post_id: str) -> str:
    if not author_handle or not post_id:
        return ""
    handle = author_handle.lstrip("@").strip()
    if not handle:
        return ""
    return f"https://x.com/{handle}/status/{post_id}"


def parse_rfc1123_to_iso(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt = parsedate_to_datetime(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc).replace(microsecond=0)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def validate_tool_posts(posts: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    for i, p in enumerate(posts[:50]):
        pid = p.get("id")
        author = p.get("author", {}) or {}
        handle = author.get("handle", "")
        ts = p.get("timestamp", "")
        content = p.get("content", "")
        engagement = p.get("engagement", {}) or {}
        if not pid:
            issues.append(f"posts[{i}].id missing")
        if not handle:
            issues.append(f"posts[{i}].author.handle missing")
        if not ts:
            issues.append(f"posts[{i}].timestamp missing")
        if not isinstance(content, str) or not content.strip():
            issues.append(f"posts[{i}].content missing/empty")
        if not isinstance(engagement, dict):
            issues.append(f"posts[{i}].engagement not an object")
    return issues


def normalize_posts(tool_posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    norm: list[dict[str, Any]] = []
    for p in tool_posts:
        pid = str(p.get("id", "") or "")
        author = p.get("author", {}) or {}
        handle = str(author.get("handle", "") or "")
        created_iso = parse_rfc1123_to_iso(str(p.get("timestamp", "") or ""))
        engagement = p.get("engagement", {}) or {}
        norm.append(
            {
                "post_id": pid,
                "conversation_id": str(p.get("conversation_id", "") or ""),
                "url": construct_tweet_url(handle, pid),
                "author": {
                    "name": str(author.get("name", "") or ""),
                    "handle": handle,
                    "avatar": str(author.get("avatar", "") or ""),
                    "bio": str(author.get("bio", "") or ""),
                },
                "published_at": created_iso,
                "content": str(p.get("content", "") or ""),
                "engagement": {
                    "likes": engagement.get("likes"),
                    "reposts": engagement.get("reposts"),
                    "quotes": engagement.get("quotes"),
                    "replies": engagement.get("replies"),
                    "bookmarks": engagement.get("bookmarks"),
                    "views": engagement.get("views"),
                },
                "media": p.get("media", None),
                "urls": p.get("urls", None),
            }
        )
    return norm


async def run_once(
    model: str,
    window_minutes: int,
    max_handles: int,
    mode: str,
    limit: int,
    config_path: Path,
    out_dir: Path,
) -> Path:
    load_dotenv()
    import os  # noqa: PLC0415

    api_key = os.getenv("XAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing XAI_API_KEY. Put it in .env or environment variables.")

    groups = load_twitter_accounts(config_path)
    if not groups:
        raise RuntimeError(f"No accounts found in {config_path}")

    # Flatten handles across groups up to max_handles
    flat: list[str] = []
    for g in groups:
        for h in g.handles:
            if h not in flat:
                flat.append(h)
    # x_search 限制每次最多 10 个 handles（见项目内注释/文档）
    max_handles = min(int(max_handles), MAX_HANDLES_PER_REQUEST)
    flat = flat[:max_handles]

    client = AsyncOpenAI(api_key=api_key, base_url=XAI_BASE_URL)

    # x_search: one tool call per handle (x_keyword_search behavior)
    tool_calls = []
    for h in flat:
        tool_calls.append(
            {
                "type": "x_search",
                "x_search": {
                    "query": f"from:{h}",
                    "mode": mode,
                    "limit": limit,
                },
            }
        )

    prompt = build_raw_posts_prompt(handles=flat, mode=mode, limit=limit)

    resp = await client.responses.create(
        model=model,
        tools=tool_calls,
        input=prompt,
    )

    text = extract_response_text(resp)
    if not text:
        raise RuntimeError("Empty model response text; cannot parse JSON.")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = _safe_filename(_utc_now_iso())
        raw_path = out_dir / f"{ts}_raw.txt"
        raw_path.write_text(text, encoding="utf-8")
        raise RuntimeError(f"Response is not valid JSON. Saved raw text to {raw_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _safe_filename(_utc_now_iso())
    tool_posts_path = out_dir / f"{ts}_tool_posts.json"
    tool_posts_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

    # Also save raw response object (best-effort) for debugging tool output richness
    try:
        raw_obj = resp.model_dump()
        raw_json_path = out_dir / f"{ts}_response.json"
        raw_json_path.write_text(json.dumps(raw_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    tool_posts = list(parsed.get("posts", []) or [])
    if not isinstance(tool_posts, list):
        raise RuntimeError("Parsed JSON does not contain posts[] list.")

    issues = validate_tool_posts([p for p in tool_posts if isinstance(p, dict)])
    normalized = normalize_posts([p for p in tool_posts if isinstance(p, dict)])
    norm_bundle = {
        "run": {
            "generated_at": _utc_now_iso(),
            "window_minutes": int(window_minutes),
            "mode": mode,
            "limit": limit,
            "handles": flat,
            "issues": issues,
        },
        "posts": normalized,
    }
    norm_path = out_dir / f"{ts}_normalized.json"
    norm_path.write_text(json.dumps(norm_bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    await client.close()
    return norm_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--config",
        default="config/twitter_accounts.yaml",
        help="Path to twitter_accounts.yaml",
    )
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--window-minutes", type=int, default=30)
    p.add_argument("--max-handles", type=int, default=10, help="Total unique handles (<=10 recommended)")
    p.add_argument("--mode", default="Latest")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument(
        "--out-dir",
        default="data/twitter_grok_runs",
        help="Directory to write JSON outputs",
    )
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    config_path = Path(args.config)
    out_dir = Path(args.out_dir)

    path = await run_once(
        model=args.model,
        window_minutes=args.window_minutes,
        max_handles=min(int(args.max_handles), 50),
        mode=str(args.mode),
        limit=int(args.limit),
        config_path=config_path,
        out_dir=out_dir,
    )
    print(f"Saved normalized JSON to: {path}")


if __name__ == "__main__":
    asyncio.run(main())


