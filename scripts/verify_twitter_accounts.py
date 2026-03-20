"""验证 Twitter 账号活跃度

用法:
    python scripts/verify_twitter_accounts.py
    python scripts/verify_twitter_accounts.py --group hype
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from src.collectors.twitter_collector import TwitterCollector


async def verify_accounts(group: str | None = None):
    """验证账号活跃度"""
    collector = TwitterCollector(tweets_per_account=3)

    print("=" * 70)
    print("Twitter 账号活跃度验证 (3天内)")
    print("=" * 70)

    groups = [group] if group else list(collector.config.keys())
    total_accounts = sum(len(collector.config[g]["accounts"]) for g in groups if g in collector.config)
    print(f"待验证: {len(groups)} 组, {total_accounts} 账号")

    results: dict[str, dict[str, int]] = {}

    for grp in groups:
        if grp not in collector.config:
            continue

        accounts = collector.config[grp]["accounts"]
        print(f"\n[{grp.upper()}] 验证 {len(accounts)} 个账号...")

        tweets = await collector._search_accounts_parallel(accounts, grp)

        # 统计每个账号的推文数（大小写不敏感匹配）
        by_author: dict[str, int] = {}
        for t in tweets:
            handle_lower = t.author_handle.lower()
            by_author[handle_lower] = by_author.get(handle_lower, 0) + 1

        results[grp] = {}
        for acc in accounts:
            count = by_author.get(acc.lower(), 0)
            results[grp][acc] = count
            status = "✓" if count > 0 else "✗"
            print(f"  {status} @{acc}: {count} 条")

    await collector.close()

    # 汇总
    print("\n" + "=" * 70)
    print("汇总")
    print("=" * 70)

    active = []
    inactive = []
    for grp, accounts in results.items():
        for acc, count in accounts.items():
            if count == 0:
                inactive.append(f"@{acc} ({grp})")
            else:
                active.append(f"@{acc}")

    print(f"\n✓ 活跃账号: {len(active)} 个")

    if inactive:
        print(f"\n⚠️  不活跃账号 ({len(inactive)} 个):")
        for acc in inactive:
            print(f"  - {acc}")
        print("\n可能原因:")
        print("  1. 账号最近3天没发推")
        print("  2. 账号已改名/停用")
        print("  3. 只发 repost（Grok 可能不返回）")
    else:
        print("\n✓ 所有账号都有返回推文")


async def main():
    parser = argparse.ArgumentParser(description="验证 Twitter 账号活跃度")
    parser.add_argument("--group", help="只验证指定组")
    args = parser.parse_args()

    await verify_accounts(args.group)


if __name__ == "__main__":
    asyncio.run(main())
