"""Twitter 采集测试脚本（交互式）"""
import asyncio
import argparse
from src.utils.config import load_config, load_env


def show_config():
    """显示当前配置"""
    config = load_config("twitter_accounts")

    print("\n" + "=" * 60)
    print("Twitter 账号配置")
    print("=" * 60)

    total_accounts = 0
    for group, group_config in config.items():
        accounts = group_config["accounts"]
        interval = group_config["interval_minutes"]
        total_accounts += len(accounts)

        print(f"\n[{group}] ({len(accounts)} 账号, 间隔 {interval} 分钟)")
        for acc in accounts:
            print(f"  - @{acc}")

    print(f"\n总计: {total_accounts} 个账号")
    return config, total_accounts


def estimate_tokens(config: dict):
    """估算 Token 消耗"""
    print("\n" + "=" * 60)
    print("Token 消耗估算")
    print("=" * 60)

    # 估算参数（基于 Grok x_search 实际使用情况）
    INPUT_TOKENS_PER_REQUEST = 150   # 输入 prompt 约 150 tokens
    OUTPUT_TOKENS_PER_REQUEST = 800  # 输出约 500-1000 tokens

    total_groups = len(config)
    total_accounts = sum(len(g["accounts"]) for g in config.values())

    # 计算需要多少次 API 调用（每次最多 10 个账号）
    api_calls = 0
    for group_config in config.values():
        accounts = group_config["accounts"]
        api_calls += (len(accounts) + 9) // 10  # 向上取整

    input_tokens = api_calls * INPUT_TOKENS_PER_REQUEST
    output_tokens = api_calls * OUTPUT_TOKENS_PER_REQUEST
    total_tokens = input_tokens + output_tokens

    print(f"\n配置统计:")
    print(f"  - 组数: {total_groups}")
    print(f"  - 账号总数: {total_accounts}")
    print(f"  - API 调用次数: {api_calls} (每次最多10账号)")

    print(f"\nToken 估算:")
    print(f"  - 输入 tokens: ~{input_tokens:,}")
    print(f"  - 输出 tokens: ~{output_tokens:,}")
    print(f"  - 总计: ~{total_tokens:,} tokens")

    # Grok 价格估算 (grok-4-fast)
    # 参考: https://docs.x.ai/docs/overview
    INPUT_PRICE = 5.0 / 1_000_000   # $5 per 1M input tokens
    OUTPUT_PRICE = 25.0 / 1_000_000  # $25 per 1M output tokens

    input_cost = input_tokens * INPUT_PRICE
    output_cost = output_tokens * OUTPUT_PRICE
    total_cost = input_cost + output_cost

    print(f"\n费用估算 (grok-4-fast):")
    print(f"  - 输入: ${input_cost:.4f}")
    print(f"  - 输出: ${output_cost:.4f}")
    print(f"  - 单次总计: ${total_cost:.4f}")

    # 每日费用估算
    daily_calls = sum(
        (24 * 60 / g["interval_minutes"]) * ((len(g["accounts"]) + 9) // 10)
        for g in config.values()
    )
    daily_cost = daily_calls * (INPUT_TOKENS_PER_REQUEST * INPUT_PRICE +
                                 OUTPUT_TOKENS_PER_REQUEST * OUTPUT_PRICE)

    print(f"\n每日费用估算 (按配置间隔):")
    print(f"  - 每日 API 调用: ~{int(daily_calls)} 次")
    print(f"  - 每日费用: ~${daily_cost:.2f}")
    print(f"  - 每月费用: ~${daily_cost * 30:.2f}")


def check_env():
    """检查环境变量配置"""
    print("\n" + "=" * 60)
    print("环境变量检查")
    print("=" * 60)

    env = load_env()
    api_key = env.get("xai_api_key", "")

    if api_key:
        print(f"\nXAI_API_KEY: {api_key[:10]}...{api_key[-5:]}")
        print("状态: 已配置")
        return True
    else:
        print("\nXAI_API_KEY: 未配置")
        print("状态: 缺失")
        return False


async def run_collect(group: str | None = None):
    """实际执行采集"""
    from src.collectors import TwitterCollector

    print("\n" + "=" * 60)
    print(f"执行 Twitter 采集 {'(组: ' + group + ')' if group else '(全部)'}")
    print("=" * 60)

    collector = TwitterCollector()

    try:
        items = await collector.collect(group=group)

        print(f"\n采集到 {len(items)} 条内容\n")

        for i, item in enumerate(items, 1):
            print(f"\n[{i}] {item.title}")
            print(f"    分类: {item.category}")
            print(f"    时间: {item.published_at.strftime('%Y-%m-%d %H:%M')}")
            print("-" * 50)

            # 显示内容（限制长度）
            content = item.content
            if len(content) > 500:
                content = content[:500] + "\n... (truncated)"
            print(content)
            print("-" * 50)

    except Exception as e:
        print(f"\n采集失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await collector.close()


def interactive_menu():
    """交互式菜单"""
    config, _ = show_config()

    while True:
        print("\n" + "=" * 60)
        print("选择操作:")
        print("  1. 查看配置")
        print("  2. Token/费用估算")
        print("  3. 检查 API Key")
        print("  4. 执行采集 (全部)")
        print("  5. 执行采集 (选择组)")
        print("  0. 退出")
        print("=" * 60)

        choice = input("\n请输入选项 [0-5]: ").strip()

        if choice == "0":
            print("\n退出")
            break
        elif choice == "1":
            show_config()
        elif choice == "2":
            estimate_tokens(config)
        elif choice == "3":
            check_env()
        elif choice == "4":
            if check_env():
                confirm = input("\n确认执行? (y/n): ").strip().lower()
                if confirm == "y":
                    asyncio.run(run_collect())
            else:
                print("\n请先配置 XAI_API_KEY")
        elif choice == "5":
            groups = list(config.keys())
            print("\n可用组:")
            for i, g in enumerate(groups, 1):
                print(f"  {i}. {g}")

            try:
                idx = int(input("\n选择组 [1-{}]: ".format(len(groups))).strip()) - 1
                if 0 <= idx < len(groups):
                    if check_env():
                        confirm = input(f"\n确认采集 [{groups[idx]}]? (y/n): ").strip().lower()
                        if confirm == "y":
                            asyncio.run(run_collect(groups[idx]))
                    else:
                        print("\n请先配置 XAI_API_KEY")
                else:
                    print("\n无效选项")
            except ValueError:
                print("\n无效输入")
        else:
            print("\n无效选项")


def main():
    parser = argparse.ArgumentParser(description="Twitter 采集测试工具")
    parser.add_argument("--config", "-c", action="store_true", help="显示配置")
    parser.add_argument("--estimate", "-e", action="store_true", help="Token/费用估算")
    parser.add_argument("--check", action="store_true", help="检查 API Key")
    parser.add_argument("--run", "-r", action="store_true", help="执行采集")
    parser.add_argument("--group", "-g", type=str, help="指定采集组")

    args = parser.parse_args()

    # 如果没有参数，进入交互模式
    if not any([args.config, args.estimate, args.check, args.run]):
        interactive_menu()
        return

    # 命令行模式
    config = load_config("twitter_accounts")

    if args.config:
        show_config()

    if args.estimate:
        estimate_tokens(config)

    if args.check:
        check_env()

    if args.run:
        if check_env():
            asyncio.run(run_collect(args.group))
        else:
            print("\n错误: XAI_API_KEY 未配置")


if __name__ == "__main__":
    main()
