"""Minimal Telegram pusher test using recent RSS DB items."""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from src.analyzers.base import AnalyzedItem  # noqa: E402
from src.pushers.telegram_pusher import TelegramPusher  # noqa: E402
from src.utils.db import RSSDatabase, TwitterDatabase  # noqa: E402


def _parse_dt(value: object) -> datetime:
    """Parse DB datetime or fallback to now."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _parse_args() -> argparse.Namespace:
    """Parse CLI args for mock mode."""
    parser = argparse.ArgumentParser(description="Test Telegram pusher.")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Send mock items even if database is empty.",
    )
    return parser.parse_args()


def _build_mock_rss_items() -> list[AnalyzedItem]:
    """Build minimal mock RSS items for testing."""
    now = datetime.now(timezone.utc)
    samples = [
        ("Mock Fed Update", "Federal Reserve", "macro", "https://example.com/fed"),
        ("Mock Crypto Market", "CoinDesk", "crypto", "https://example.com/market"),
    ]
    items: list[AnalyzedItem] = []
    for idx, (title, source, category, url) in enumerate(samples):
        score = 0.7 if idx == 0 else 0.5
        items.append(
            AnalyzedItem(
                id=f"mock-{idx}",
                title=title,
                content="Mock content for Telegram pusher test.",
                source=source,
                category=category,
                url=url,
                published_at=now,
                score=score,
                raw_data={
                    "source_type": "rss",
                    "sourcing_category": category,
                    "vader_score": 0.2,
                    "finbert_score": 0.1,
                    "sentiment": 0.14,
                    "sentiment_direction": "bullish",
                    "is_divergent": False,
                },
            )
        )
    return items


def _build_mock_twitter_items() -> list[AnalyzedItem]:
    """Build minimal mock Twitter items for testing."""
    now = datetime.now(timezone.utc)
    items: list[AnalyzedItem] = []
    items.append(
        AnalyzedItem(
            id="mock-tw-0",
            title="",
            content="BTC breaks above key resistance as ETF inflows surge.",
            source="Twitter",
            category="twitter",
            url="https://twitter.com/example/status/1",
            published_at=now,
            score=0.0,
            raw_data={
                "source_type": "twitter",
                "author_handle": "example",
                "group": "macro",
                "likes": 1200,
                "reposts": 230,
                "replies": 45,
                "views": 560000,
                "engagement": 1200 * 0.5 + 45 * 13 + 230 * 10 + 2 * 5.0,
                "vader_score": 0.35,
                "sentiment_direction": "bullish",
                "sentiment_level": "IMPORTANT",
            },
        )
    )
    return items


def _calc_engagement(row: dict) -> float:
    """Compute engagement using the same formula as Telegram legend."""
    likes = row.get("likes", 0) or 0
    replies = row.get("replies", 0) or 0
    reposts = row.get("reposts", 0) or 0
    views = row.get("views", 0) or 0
    views_score = 0.0
    if views:
        views_score = 2.0 * len(str(int(views)))
    return likes * 0.5 + replies * 13 + reposts * 10 + views_score


async def main() -> None:
    args = _parse_args()
    rss_db = RSSDatabase()
    twitter_db = TwitterDatabase()
    rss_rows = rss_db.get_unpushed()[:5]
    twitter_rows = twitter_db.get_unpushed()[:5]
    if not rss_rows and not twitter_rows and not args.mock:
        print("[TestTelegram] No unpushed RSS or Twitter items found.")
        return

    items: list[AnalyzedItem] = []
    if rss_rows:
        for idx, row in enumerate(rss_rows):
            score = 0.7 if idx == 0 else 0.5
            items.append(
                AnalyzedItem(
                    id=str(row["id"]),
                    title=row["title"],
                    content=row.get("content", "") or "",
                    source=row["source"],
                    category=row.get("category", "") or "",
                    url=row["url"],
                    published_at=_parse_dt(row.get("published_at")),
                    score=score,
                    raw_data={
                        "source_type": "rss",
                        "sourcing_category": row.get("category", "") or "",
                        "vader_score": 0.2,
                        "finbert_score": 0.1,
                        "sentiment": 0.14,
                        "sentiment_direction": "bullish",
                        "is_divergent": False,
                    },
                )
            )
    if twitter_rows:
        for row in twitter_rows:
            items.append(
                AnalyzedItem(
                    id=str(row["id"]),
                    title="",
                    content=row.get("content", "") or "",
                    source=row.get("author_handle", "Twitter"),
                    category="twitter",
                    url=row.get("tweet_url", "") or "",
                    published_at=_parse_dt(row.get("published_at")),
                    score=0.0,
                    raw_data={
                        "source_type": "twitter",
                        "author_handle": row.get("author_handle", ""),
                        "group": row.get("group", ""),
                        "likes": row.get("likes", 0),
                        "reposts": row.get("reposts", 0),
                        "replies": row.get("replies", 0),
                        "views": row.get("views", 0),
                        "engagement": _calc_engagement(row),
                        "vader_score": row.get("vader_score"),
                        "sentiment_direction": row.get("sentiment_direction", ""),
                        "sentiment_level": row.get("sentiment_level", ""),
                    },
                )
            )
    if not items and args.mock:
        items.extend(_build_mock_rss_items())
        items.extend(_build_mock_twitter_items())

    pusher = TelegramPusher(show_legend=False)
    await pusher.push(items, total_collected=len(items), total_skipped=0)
    await pusher.close()


if __name__ == "__main__":
    asyncio.run(main())

