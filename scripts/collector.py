"""一次性采集脚本 — Twitter + RSS → 入库，不做管道/推送

从上游数据源采集新数据并写入 twitter.db / rss.db。
不执行下游管道（Sourcing/Ranking/Feature Extraction），不标记 is_pushed。
配合 scheduler.py 使用可实现定时采集。

用法:
    python scripts/collector.py              # 采集 Twitter + RSS
    python scripts/collector.py --tw-only    # 只采集 Twitter
    python scripts/collector.py --rss-only   # 只采集 RSS
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from src.collectors import RSSCollector, TwitterCollector  # noqa: E402
from src.utils.db import RSSDatabase, TwitterDatabase  # noqa: E402


async def collect_rss() -> int:
    """采集 RSS → rss.db，返回新增条数"""
    collector = RSSCollector()
    try:
        items = await collector.collect()
    finally:
        await collector.close()

    if not items:
        print("[RSS] 采集 0 条")
        return 0

    db = RSSDatabase()
    new_count = 0
    for item in items:
        if not item.url:
            continue
        inserted = db.insert({
            "url": item.url,
            "title": item.title,
            "content": item.content,
            "source": item.source,
            "category": item.category,
            "published_at": item.published_at.isoformat() if item.published_at else None,
        })
        if inserted:
            new_count += 1

    print(f"[RSS] 采集 {len(items)} 条, 新增 {new_count} 条")
    return new_count


async def collect_twitter() -> int:
    """采集 Twitter → twitter.db，返回新增条数"""
    collector = TwitterCollector(tweets_per_account=5)
    try:
        tweets = await collector.collect_tweets()
    finally:
        await collector.close()

    if not tweets:
        print("[Twitter] 采集 0 条")
        return 0

    db = TwitterDatabase()
    new_count = 0
    for tweet in tweets:
        if not tweet.tweet_id:
            continue
        inserted = db.insert(collector.get_tweet_dict(tweet))
        if inserted:
            new_count += 1

    print(f"[Twitter] 采集 {len(tweets)} 条, 新增 {new_count} 条")
    return new_count


async def main() -> None:
    from datetime import datetime

    parser = argparse.ArgumentParser(description="上游数据采集 (Twitter + RSS → DB)")
    parser.add_argument("--tw-only", action="store_true", help="只采集 Twitter")
    parser.add_argument("--rss-only", action="store_true", help="只采集 RSS")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[Collector] {ts} 开始采集...")

    if args.tw_only:
        await collect_twitter()
    elif args.rss_only:
        await collect_rss()
    else:
        # 并行采集
        results = await asyncio.gather(
            collect_rss(),
            collect_twitter(),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                print(f"[Collector] 采集异常: {r}")

    print(f"[Collector] {datetime.now().strftime('%H:%M:%S')} 完成\n")


if __name__ == "__main__":
    asyncio.run(main())
