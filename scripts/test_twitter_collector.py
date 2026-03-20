"""测试 Twitter 采集器

用法:
    python scripts/test_twitter_collector.py
    python scripts/test_twitter_collector.py --group policy
    python scripts/test_twitter_collector.py --handles elonmusk zachxbt --limit 3
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# 设置 UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8')

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.collectors.twitter_collector import TwitterCollector, Tweet


async def test_single_request(handles: list[str], limit: int = 5) -> list[Tweet]:
    """测试单次请求（不使用配置文件）"""
    from openai import AsyncOpenAI
    from src.utils.config import load_env

    env = load_env()
    api_key = env.get("xai_api_key")
    if not api_key:
        print("[Error] XAI_API_KEY 未配置")
        return []

    client = AsyncOpenAI(api_key=api_key, base_url="https://api.x.ai/v1")

    # 官方格式（3天回溯）
    from datetime import timedelta
    from_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
    tool = {
        "type": "x_search",
        "allowed_x_handles": handles,
        "from_date": from_date,
    }

    prompt = (
        f"获取以下账号最近的推文: {', '.join('@' + h for h in handles)}\n"
        f"每个账号最多返回 {limit} 条推文。\n\n"
        "请以 JSON 格式返回：\n"
        '{"posts": [{"id": "...", "author": {"name": "...", "handle": "..."}, '
        '"timestamp": "...", "content": "...", '
        '"engagement": {"likes": 0, "reposts": 0, "replies": 0, "views": 0}}]}'
    )

    print(f"[Test] 请求账号: {handles}")
    print(f"[Test] 每账号限制: {limit} 条")
    print(f"[Test] Tool 配置: {json.dumps(tool, indent=2)}")
    print("-" * 50)

    try:
        response = await asyncio.wait_for(
            client.responses.create(
                model="grok-4-fast",
                tools=[tool],
                input=[{"role": "user", "content": prompt}],
            ),
            timeout=90.0,
        )

        # 打印原始响应结构
        print(f"[Test] Response type: {type(response)}")

        # 提取文本
        output_text = getattr(response, "output_text", None)
        if output_text:
            print(f"[Test] output_text 长度: {len(output_text)}")
        else:
            print("[Test] 无 output_text，检查 output 列表...")
            for i, out in enumerate(getattr(response, "output", []) or []):
                print(f"  output[{i}].type = {getattr(out, 'type', 'N/A')}")

        # 保存原始响应
        try:
            raw_data = response.model_dump()
            out_path = Path("data/test_twitter_response.json")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(raw_data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[Test] 原始响应已保存: {out_path}")
        except Exception as e:
            print(f"[Test] 保存响应失败: {e}")

        await client.close()
        return []

    except asyncio.TimeoutError:
        print("[Test] 请求超时 (90s)")
        await client.close()
        return []
    except Exception as e:
        print(f"[Test] 请求失败: {type(e).__name__}: {e}")
        await client.close()
        return []


async def test_collector(group: str | None = None, limit: int = 5):
    """测试完整的 TwitterCollector"""
    print("=" * 60)
    print("测试 TwitterCollector")
    print("=" * 60)

    collector = TwitterCollector(tweets_per_account=limit)

    print(f"\n[Config] 每账号限制: {limit} 条")
    print(f"[Config] 监控组: {list(collector.config.keys())}")

    for grp, cfg in collector.config.items():
        print(f"  - {grp}: {len(cfg['accounts'])} 账号")

    print("\n" + "-" * 60)
    print("开始采集...")
    print("-" * 60)

    tweets = await collector.collect_tweets(group)

    print("\n" + "=" * 60)
    print(f"采集结果: 共 {len(tweets)} 条推文")
    print("=" * 60)

    # 按账号分组统计
    by_author: dict[str, list[Tweet]] = {}
    for t in tweets:
        by_author.setdefault(t.author_handle, []).append(t)

    print(f"\n[统计] 按账号分组:")
    for author, author_tweets in sorted(by_author.items(), key=lambda x: -len(x[1])):
        print(f"  @{author}: {len(author_tweets)} 条")

    # 显示前 10 条详情
    print(f"\n[详情] 前 10 条推文:")
    print("-" * 60)

    for i, tweet in enumerate(tweets[:10], 1):
        content = tweet.content[:100].replace("\n", " ") if tweet.content else ""
        if len(tweet.content) > 100:
            content += "..."

        print(f"\n[{i}] @{tweet.author_handle} ({tweet.monitoring_group})")
        print(f"    {content}")
        print(f"    Likes: {tweet.likes:,} | Reposts: {tweet.reposts:,} | Views: {tweet.views:,}")
        print(f"    URL: {tweet.tweet_url}")
        print(f"    Time: {tweet.published_at}")

    await collector.close()

    # 保存结果
    out_path = Path("data/test_twitter_results.json")
    results = [collector.get_tweet_dict(t) for t in tweets]
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[Test] 结果已保存: {out_path}")

    return tweets


async def main():
    parser = argparse.ArgumentParser(description="测试 Twitter 采集器")
    parser.add_argument("--group", help="只测试指定组 (policy/security/whale/leaders)")
    parser.add_argument("--handles", nargs="+", help="直接指定账号测试（跳过配置）")
    parser.add_argument("--limit", type=int, default=5, help="每账号推文数量 (默认 5)")
    parser.add_argument("--raw", action="store_true", help="只测试原始 API 调用")
    args = parser.parse_args()

    if args.handles:
        # 直接测试指定账号
        await test_single_request(args.handles, args.limit)
    elif args.raw:
        # 原始 API 测试
        await test_single_request(["WhiteHouse", "elonmusk"], args.limit)
    else:
        # 完整 collector 测试
        await test_collector(args.group, args.limit)


if __name__ == "__main__":
    asyncio.run(main())
