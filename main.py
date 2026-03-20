"""主程序入口 — Pipeline 编排层 (v4)"""
import asyncio
import sys
from datetime import datetime

# Windows UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8')

from src.collectors import RSSCollector, TwitterCollector
from src.collectors.twitter_collector import Tweet
from src.pipelines import RSSPipeline, TwitterPipeline
from src.pushers import TelegramPusher
from src.utils.db import RSSDatabase, TwitterDatabase


def print_twitter_details(tweets: list[Tweet], verbose: bool = True) -> None:
    """打印 Twitter 采集详情"""
    if not tweets:
        return

    by_group: dict[str, dict[str, list[Tweet]]] = {}
    for t in tweets:
        group = t.monitoring_group or "unknown"
        handle = t.author_handle
        by_group.setdefault(group, {}).setdefault(handle, []).append(t)

    print("\n" + "=" * 70)
    print("Twitter 采集详情")
    print("=" * 70)

    for group, accounts in by_group.items():
        total_in_group = sum(len(ts) for ts in accounts.values())
        print(f"\n[{group.upper()}] {len(accounts)} 账号, 共 {total_in_group} 条推文")
        print("-" * 70)

        for handle, handle_tweets in sorted(accounts.items()):
            print(f"\n  @{handle} ({len(handle_tweets)} 条):")

            for i, tweet in enumerate(handle_tweets, 1):
                content = tweet.content.replace("\n", " ").strip()
                if len(content) > 80:
                    content = content[:77] + "..."

                time_str = tweet.published_at.strftime("%m-%d %H:%M") if tweet.published_at else "N/A"
                eng = f"L:{tweet.likes:,} R:{tweet.reposts:,} V:{tweet.views:,}"

                print(f"    [{i}] {time_str} | {eng}")
                print(f"        {content}")

                if verbose and tweet.tweet_url:
                    print(f"        {tweet.tweet_url}")

    print("\n" + "=" * 70)


def print_rss_summary(items: list, verbose: bool = False) -> None:
    """打印 RSS 采集摘要"""
    if not items:
        return

    by_source: dict[str, list] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)

    print("\n" + "-" * 50)
    print("RSS 采集摘要")
    print("-" * 50)

    for source, source_items in sorted(by_source.items(), key=lambda x: -len(x[1])):
        print(f"  {source}: {len(source_items)} 条")

        if verbose:
            for item in source_items[:3]:
                title = item.title[:50] + "..." if len(item.title) > 50 else item.title
                print(f"    - {title}")


async def run_pipeline(verbose: bool = True, use_mock: bool = False, show_legend: bool = True) -> None:
    """运行完整的采集-分析-推送流程（v4 Pipeline 版）"""
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 开始采集...")

    # ── 1. 数据采集（并行） ──
    rss_collector = RSSCollector()
    twitter_collector = TwitterCollector(tweets_per_account=5)

    try:
        rss_items, twitter_tweets = await asyncio.gather(
            rss_collector.collect(),
            twitter_collector.collect_tweets(),
            return_exceptions=True,
        )

        if isinstance(rss_items, Exception):
            print(f"[RSS] 采集异常: {rss_items}")
            rss_items = []
        if isinstance(twitter_tweets, Exception):
            print(f"[Twitter] 采集异常: {twitter_tweets}")
            twitter_tweets = []

        print(f"[采集] RSS {len(rss_items)} 条, Twitter {len(twitter_tweets)} 条")

        if verbose and twitter_tweets:
            print_twitter_details(twitter_tweets, verbose=True)
        if verbose and rss_items:
            print_rss_summary(rss_items, verbose=False)

        # 检查 Twitter 采集错误，有则发 Telegram 告警
        twitter_errors = twitter_collector.errors if hasattr(twitter_collector, "errors") else []

    finally:
        await rss_collector.close()
        await twitter_collector.close()

    if twitter_errors:
        # 去重：429 错误只报一次
        has_429 = any("429" in e or "额度" in e for e in twitter_errors)
        if has_429:
            alert_lines = [
                "\u26A0\uFE0F *FinNews Twitter Alert*",
                "",
                "xAI API 额度不足 (HTTP 429)",
                f"影响 {sum(1 for e in twitter_errors if '429' in e or '额度' in e)} 个账号",
                "",
                "请检查: https://console.x.ai",
                f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            ]
        else:
            alert_lines = [
                "\u26A0\uFE0F *FinNews Twitter Alert*",
                "",
                f"{len(twitter_errors)} 个账号采集异常:",
            ]
            for err in twitter_errors[:10]:
                alert_lines.append(f"  {err}")
            alert_lines.append(f"\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        alert_text = "\n".join(alert_lines)
        print(f"[Alert] 发送 Telegram 告警 ({len(twitter_errors)} 个错误)")
        telegram = TelegramPusher()
        try:
            await telegram.send_alert(alert_text)
        finally:
            await telegram.close()

    # ── 2. 入库 ──
    rss_db = RSSDatabase()
    twitter_db = TwitterDatabase()

    new_rss_count = 0
    for item in rss_items:
        if not item.url:
            continue
        inserted = rss_db.insert({
            "url": item.url,
            "title": item.title,
            "content": item.content,
            "source": item.source,
            "category": item.category,
            "published_at": item.published_at.isoformat() if item.published_at else None,
        })
        if inserted:
            new_rss_count += 1
    print(f"[RSS] 新增 {new_rss_count} 条（去重后）")

    new_twitter_count = 0
    for tweet in twitter_tweets:
        if not tweet.tweet_id:
            continue
        inserted = twitter_db.insert(twitter_collector.get_tweet_dict(tweet))
        if inserted:
            new_twitter_count += 1
    print(f"[Twitter] 新增 {new_twitter_count} 条（去重后）")

    # ── 3. 管道过滤 ──
    rss_pipeline = RSSPipeline(use_mock=use_mock)
    twitter_pipeline = TwitterPipeline()

    rss_result = rss_pipeline.run()
    twitter_result = twitter_pipeline.run()

    # ── 4. 推送 ──
    items_to_push = rss_result.passed + twitter_result.passed
    total_skipped = len(rss_result.skipped) + len(twitter_result.skipped)
    total_collected = len(rss_pipeline.get_all_ids()) + len(twitter_pipeline.get_all_ids())

    print(f"[过滤] 推送 {len(items_to_push)} 条, 过滤 {total_skipped} 条")

    if not items_to_push:
        print("[Pipeline] 无重要消息，退出")
    else:
        telegram = TelegramPusher(show_legend=show_legend)
        try:
            await telegram.push(items_to_push, total_collected, total_skipped)
        finally:
            await telegram.close()

    # ── 5. 标记已推送（全部，含 skipped） ──
    rss_db.mark_pushed(rss_pipeline.get_all_ids())
    twitter_db.mark_pushed(twitter_pipeline.get_all_ids())

    pushed_rss = len(rss_result.passed)
    pushed_twitter = len(twitter_result.passed)
    print(f"[推送完成] RSS {pushed_rss} 条, Twitter {pushed_twitter} 条")
    print(f"[标记完成] 共标记 {len(rss_pipeline.get_all_ids()) + len(twitter_pipeline.get_all_ids())} 条为已处理")
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 流程完成")


async def main() -> None:
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description="宏观 + 加密新闻智能监控系统")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式，不显示详细输出")
    parser.add_argument("--mock", action="store_true", help="使用 Mock FinBERT（调试模式）")
    parser.add_argument("--legend", action="store_true", help="附带情感分析/推特评分图例说明")
    parser.add_argument("--no-legend", action="store_true", help="不附情感分析/推特评分图例说明（覆盖 --legend）")
    args = parser.parse_args()

    print("=" * 60)
    print("宏观 + 加密新闻智能监控系统 (v4 - Pipeline 重构版)")
    print("=" * 60)

    show_legend = args.legend and not args.no_legend
    await run_pipeline(verbose=not args.quiet, use_mock=args.mock, show_legend=show_legend)


if __name__ == "__main__":
    asyncio.run(main())
