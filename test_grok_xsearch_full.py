"""
Full end-to-end test for Grok X search return structure + normalization.

What this script verifies (based on latest real x_search behavior):
- Each post is returned as an independent object (id/author/timestamp/content/engagement/media/urls)
- No "title" exists for X posts
- tweet_url can be deterministically constructed from author.handle + id
- published_at should use the real timestamp (UTC), not fetch time
- (optional) replies require x_thread_fetch(post_id)

Usage (PowerShell):
  .\\venv\\Scripts\\python.exe test_grok_xsearch_full.py
  .\\venv\\Scripts\\python.exe test_grok_xsearch_full.py --limit 3 --mode Latest --max-handles 10
  .\\venv\\Scripts\\python.exe test_grok_xsearch_full.py --thread-fetch 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

import yaml
from dotenv import load_dotenv
from openai import AsyncOpenAI


XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_MODEL = "grok-4-fast"
MAX_HANDLES_PER_REQUEST = 10


@dataclass(frozen=True)
class PostAuthor:
    name: str
    handle: str
    avatar: str
    bio: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_ts_filename(ts: str) -> str:
    return ts.replace(":", "").replace("-", "").replace("Z", "Z")


def load_handles(config_path: Path, max_handles: int) -> list[str]:
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    handles: list[str] = []
    for group_cfg in cfg.values():
        for h in list(group_cfg.get("accounts", []) or []):
            if h not in handles:
                handles.append(h)

    return handles[:max_handles]


def construct_tweet_url(author_handle: str, post_id: str) -> str:
    """Deterministically construct X status URL from @handle + id."""
    if not author_handle or not post_id:
        return ""
    h = author_handle.lstrip("@").strip()
    if not h:
        return ""
    return f"https://x.com/{h}/status/{post_id}"


def parse_rfc1123_to_iso(ts: str) -> str:
    """
    Convert RFC1123 timestamp to ISO8601 Z.
    Example: Wed, 04 Feb 2026 09:27:23 GMT -> 2026-02-04T09:27:23Z
    """
    if not ts:
        return ""
    try:
        dt = parsedate_to_datetime(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc).replace(microsecond=0)
        return dt_utc.isoformat().replace("+00:00", "Z")
    except Exception:
        return ""


def extract_response_text(resp: Any) -> str:
    output_text = getattr(resp, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    texts: list[str] = []
    for out in getattr(resp, "output", []) or []:
        if getattr(out, "type", None) != "message":
            continue
        for c in getattr(out, "content", []) or []:
            t = getattr(c, "text", None)
            if isinstance(t, str) and t.strip():
                texts.append(t)
    return "\n".join(texts).strip()


def coerce_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def validate_tool_posts(posts: list[dict[str, Any]]) -> list[str]:
    """Return list of human-readable validation issues."""
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
        media = p.get("media", None)
        urls = p.get("urls", None)

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
                "media": media,
                "urls": urls,
            }
        )
    return norm


def build_input_prompt(handles: list[str], mode: str, limit: int) -> str:
    """
    Ask Grok to return ONLY JSON containing the raw tool posts.
    We intentionally avoid analysis here to verify ground-truth tool structure first.
    """
    handles_json = json.dumps(handles, ensure_ascii=False)
    return (
        "You will use the X search tool results you fetched. "
        "Return EXACTLY one JSON object (no markdown, no code fences, no extra text).\n\n"
        "Goal: Provide the raw posts as returned by the tool, where each post is an independent object.\n"
        "Do NOT merge posts, do NOT invent fields.\n\n"
        f"handles={handles_json}\n"
        f"mode={mode}\n"
        f"limit={limit}\n\n"
        "Output schema:\n"
        "{\n"
        '  "tool": "x_keyword_search",\n'
        '  "mode": "<mode>",\n'
        '  "limit": <limit>,\n'
        '  "posts": [ {id, conversation_id, author{name,handle,avatar,bio}, timestamp, content, engagement{likes,reposts,quotes,replies,bookmarks,views}, media, urls} ]\n'
        "}\n"
    )


async def run_x_keyword_search(
    client: AsyncOpenAI,
    model: str,
    handles: list[str],
    mode: str,
    limit: int,
) -> Any:
    tools = []
    for h in handles:
        tools.append(
            {
                "type": "x_search",
                "x_search": {
                    "query": f"from:{h}",
                    "mode": mode,
                    "limit": limit,
                },
            }
        )

    return await client.responses.create(
        model=model,
        tools=tools,
        input=build_input_prompt(handles=handles, mode=mode, limit=limit),
    )


async def run_thread_fetch(
    client: AsyncOpenAI,
    model: str,
    post_id: str,
) -> Any:
    """
    Best-effort thread fetch. Some accounts/environments expose this tool as x_thread_fetch.
    If unsupported, the API will error and the caller will handle it.
    """
    return await client.responses.create(
        model=model,
        tools=[
            {
                "type": "x_thread_fetch",
                "x_thread_fetch": {
                    "post_id": post_id,
                },
            }
        ],
        input=(
            "Return EXACTLY one JSON object (no markdown). "
            "Include parent posts and replies as provided by the tool. Do not invent anything."
        ),
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/twitter_accounts.yaml")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--max-handles", type=int, default=10)
    p.add_argument("--mode", default="Latest")
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--out-dir", default="data/twitter_grok_runs_full")
    p.add_argument(
        "--thread-fetch",
        type=int,
        default=0,
        help="If >0, try x_thread_fetch for the first N posts (best-effort).",
    )
    return p.parse_args()


async def main() -> None:
    args = parse_args()
    load_dotenv()
    import os  # noqa: PLC0415

    api_key = os.getenv("XAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing XAI_API_KEY. Put it in .env or environment variables.")

    max_handles = min(int(args.max_handles), MAX_HANDLES_PER_REQUEST)
    handles = load_handles(Path(args.config), max_handles=max_handles)
    if not handles:
        raise RuntimeError("No handles loaded from config.")

    client = AsyncOpenAI(api_key=api_key, base_url=XAI_BASE_URL)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = safe_ts_filename(utc_now_iso())

    # 1) Fetch raw tool posts through x_keyword_search behavior (via x_search tool)
    resp = await run_x_keyword_search(
        client=client,
        model=args.model,
        handles=handles,
        mode=str(args.mode),
        limit=int(args.limit),
    )

    # Save raw response object for debugging
    raw_path = out_dir / f"{ts}_response.json"
    raw_path.write_text(json.dumps(resp.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

    # Extract JSON text and parse
    text = extract_response_text(resp)
    text_path = out_dir / f"{ts}_model_text.txt"
    text_path.write_text(text, encoding="utf-8")

    parsed = json.loads(text)
    tool_posts = list(parsed.get("posts", []) or [])
    if not isinstance(tool_posts, list):
        raise RuntimeError("Parsed JSON does not contain posts[] list.")

    tool_posts_path = out_dir / f"{ts}_tool_posts.json"
    tool_posts_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2) Validate + normalize (construct url, parse timestamp)
    issues = validate_tool_posts([p for p in tool_posts if isinstance(p, dict)])
    norm = normalize_posts([p for p in tool_posts if isinstance(p, dict)])

    norm_bundle = {
        "run": {
            "generated_at": utc_now_iso(),
            "mode": args.mode,
            "limit": args.limit,
            "handles": handles,
            "issues": issues,
        },
        "posts": norm,
    }
    norm_path = out_dir / f"{ts}_normalized.json"
    norm_path.write_text(json.dumps(norm_bundle, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3) Optional: thread fetch for first N posts
    if int(args.thread_fetch) > 0 and norm:
        n = min(int(args.thread_fetch), len(norm))
        for i in range(n):
            pid = norm[i].get("post_id", "")
            if not pid:
                continue
            try:
                t_resp = await run_thread_fetch(client=client, model=args.model, post_id=str(pid))
                t_path = out_dir / f"{ts}_thread_{i+1}_{pid}.json"
                t_path.write_text(
                    json.dumps(t_resp.model_dump(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                err_path = out_dir / f"{ts}_thread_{i+1}_{pid}_error.txt"
                err_path.write_text(str(e), encoding="utf-8")

    await client.close()
    print(f"Saved raw response: {raw_path}")
    print(f"Saved model text:  {text_path}")
    print(f"Saved tool posts:  {tool_posts_path}")
    print(f"Saved normalized:  {norm_path}")
    if issues:
        print("Validation issues:")
        for x in issues[:20]:
            print(f"- {x}")


if __name__ == "__main__":
    asyncio.run(main())


