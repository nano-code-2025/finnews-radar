"""RSS 采集测试脚本"""
import asyncio
from src.collectors import RSSCollector


async def test_rss():
    """测试 RSS 采集并显示内容"""
    print("=" * 70)
    print("RSS 采集测试")
    print("=" * 70)

    collector = RSSCollector()

    try:
        items = await collector.collect()

        print(f"\n共采集到 {len(items)} 条新闻\n")

        # 按来源分组显示
        by_source: dict[str, list] = {}
        for item in items:
            if item.source not in by_source:
                by_source[item.source] = []
            by_source[item.source].append(item)

        for source, source_items in by_source.items():
            print(f"\n{'='*60}")
            print(f"来源: {source} ({len(source_items)} 条)")
            print("=" * 60)

            for i, item in enumerate(source_items, 1):
                print(f"\n[{i}] {item.title}")
                print(f"    分类: {item.category}")
                print(f"    时间: {item.published_at.strftime('%Y-%m-%d %H:%M')}")
                print(f"    链接: {item.url}")

                # 显示内容摘要（前200字符）
                content = item.content.replace("\n", " ").strip()
                if len(content) > 200:
                    content = content[:200] + "..."
                if content:
                    print(f"    摘要: {content}")

    except Exception as e:
        print(f"采集失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await collector.close()

    print("\n" + "=" * 70)
    print("测试完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_rss())
