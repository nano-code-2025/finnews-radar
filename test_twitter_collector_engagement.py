"""TwitterCollector engagement probe (engineering version).

Checks:
1) Whether likes/reposts/replies/views are present in raw posts.
2) How many tweets are returned per request for a single account.
3) Whether the same tweet can be fetched across rounds (engagement change).
"""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.collectors.twitter_collector import DEFAULT_LOOKBACK_DAYS, TwitterCollector


class InspectingTwitterCollector(TwitterCollector):
    """Collector that keeps raw posts for engagement key checks."""

    def __init__(self, tweets_per_account: int) -> None:
        super().__init__(tweets_per_account=tweets_per_account)
        self.last_posts_raw: list[dict[str, Any]] = []

    def _parse_tweets(self, text: str, group: str) -> list:  # type: ignore[override]
        self.last_posts_raw = self._parse_raw_posts(text)
        return super()._parse_tweets(text, group)

    @staticmethod
    def _parse_raw_posts(text: str) -> list[dict[str, Any]]:
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        posts = parsed.get("posts", [])
        return posts if isinstance(posts, list) else []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TwitterCollector engagement probe")
    parser.add_argument("--handle", required=True, help="Target @handle (without @)")
    parser.add_argument("--count", type=int, default=5, help="Requested tweets per account")
    parser.add_argument("--rounds", type=int, default=2, help="Fetch rounds for engagement change")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between rounds")
    parser.add_argument("--from-date", default="", help="Override from_date (YYYY-MM-DD)")
    parser.add_argument("--group", default="adhoc", help="Monitoring group label")
    return parser.parse_args()


def engagement_presence(posts: list[dict[str, Any]]) -> dict[str, int]:
    keys = ["likes", "reposts", "replies", "views"]
    counts = {k: 0 for k in keys}
    for p in posts:
        if not isinstance(p, dict):
            continue
        engagement = p.get("engagement", {}) or {}
        if not isinstance(engagement, dict):
            continue
        for k in keys:
            if k in engagement and engagement.get(k) is not None:
                counts[k] += 1
    return counts


def normalize_metrics(tweets: list) -> dict[str, tuple[int, int, int, int]]:
    metrics: dict[str, tuple[int, int, int, int]] = {}
    for t in tweets:
        if not getattr(t, "tweet_id", ""):
            continue
        metrics[t.tweet_id] = (t.likes, t.reposts, t.replies, t.views)
    return metrics


async def run_probe() -> None:
    args = parse_args()
    collector = InspectingTwitterCollector(tweets_per_account=args.count)

    if args.from_date:
        from_dt = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        delta_days = max(0, int((datetime.now(timezone.utc) - from_dt).days))
        import src.collectors.twitter_collector as tc
        tc.DEFAULT_LOOKBACK_DAYS = max(1, delta_days) if delta_days else 1
        print(f"[Config] from_date={args.from_date} -> lookback_days={tc.DEFAULT_LOOKBACK_DAYS}")
    else:
        print(f"[Config] lookback_days={DEFAULT_LOOKBACK_DAYS}")

    all_rounds: list[list] = []
    all_raw_posts: list[list[dict[str, Any]]] = []

    for i in range(args.rounds):
        tweets = await collector._search_single_account(args.handle, args.group)
        raw_posts = collector.last_posts_raw
        all_rounds.append(tweets)
        all_raw_posts.append(raw_posts)

        print(f"[Round {i + 1}] returned={len(tweets)} requested={args.count}")
        presence = engagement_presence(raw_posts)
        total_posts = len(raw_posts) if raw_posts else 0
        print(f"[Round {i + 1}] engagement keys present (of {total_posts} posts): {presence}")

        if i < args.rounds - 1 and args.interval > 0:
            await asyncio.sleep(args.interval)

    per_tweet_metrics: dict[str, list[tuple[int, int, int, int]]] = defaultdict(list)
    for tweets in all_rounds:
        metrics = normalize_metrics(tweets)
        for tweet_id, m in metrics.items():
            per_tweet_metrics[tweet_id].append(m)

    changed = []
    for tweet_id, metric_list in per_tweet_metrics.items():
        if len(metric_list) < 2:
            continue
        if any(m != metric_list[0] for m in metric_list[1:]):
            changed.append(tweet_id)

    print(f"[Summary] rounds={args.rounds} unique_ids={len(per_tweet_metrics)}")
    print(f"[Summary] changed_engagement_ids={len(changed)}")
    if changed:
        print(f"[Summary] sample_changed_ids={changed[:5]}")

    await collector.close()


if __name__ == "__main__":
    asyncio.run(run_probe())

