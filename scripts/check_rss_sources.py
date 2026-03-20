"""RSS sources checker."""
from __future__ import annotations

import argparse
import sys

import feedparser
import httpx

sys.path.insert(0, ".")
from src.utils.config import load_config  # noqa: E402


def is_valid_feed(url: str) -> bool:
    """Return True if the RSS URL is reachable and parseable."""
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True)
        feed = feedparser.parse(resp.text)
        return 200 <= resp.status_code < 400 and not feed.bozo
    except Exception:
        return False


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for selecting config and output options."""
    parser = argparse.ArgumentParser(description="Check RSS sources.")
    parser.add_argument(
        "--config",
        default="rss_sources",
        help="Config base name (without .yaml), e.g. rss_sources.test",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Shortcut for --config rss_sources.test",
    )
    parser.add_argument(
        "--show-failures",
        action="store_true",
        help="Print failure reasons when available.",
    )
    return parser.parse_args()


def iter_sources(cfg: dict) -> list[dict]:
    """Flatten grouped RSS sources into a single list."""
    sources: list[dict] = []
    for group in cfg.values():
        sources.extend(group or [])
    return sources


def main() -> int:
    """Run RSS source checks and print a summary."""
    args = parse_args()
    config_name = "rss_sources.test" if args.test else args.config
    cfg = load_config(config_name) or {}
    sources = iter_sources(cfg)

    ok_count = 0
    per_group_total: dict[str, int] = {}
    per_group_ok: dict[str, int] = {}
    failures: list[str] = []

    for group_name, group_items in cfg.items():
        items = group_items or []
        per_group_total[group_name] = len(items)
        per_group_ok[group_name] = 0
        for item in items:
            name = item.get("name", "?")
            url = item.get("url", "")
            ok = is_valid_feed(url)
            ok_count += int(ok)
            per_group_ok[group_name] += int(ok)
            status = "OK" if ok else "FAIL"
            print(f"{name:20s}  {status:4s}  {url}")
            if not ok and args.show_failures:
                failures.append(f"{group_name}/{name}: {url}")

    print(f"\nSummary: {ok_count}/{len(sources)} OK")
    if per_group_total:
        print("By group:")
        for group_name in per_group_total:
            total = per_group_total[group_name]
            ok = per_group_ok[group_name]
            print(f"  {group_name:12s}  {ok}/{total} OK")
    if failures:
        print("\nFailures:")
        for item in failures:
            print(f"  - {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

