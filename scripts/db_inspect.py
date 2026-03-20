"""数据库检查工具

用法:
    python scripts/db_inspect.py          # 显示统计 + 前10条
    python scripts/db_inspect.py --all    # 显示所有记录
    python scripts/db_inspect.py --stats  # 仅显示统计
"""
import argparse
import sys
from pathlib import Path

# 设置 UTF-8 输出
sys.stdout.reconfigure(encoding='utf-8')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.db import RSSDatabase, TwitterDatabase


def get_db_stats(rss_db: RSSDatabase, twitter_db: TwitterDatabase) -> dict:
    """获取数据库统计信息"""
    import sqlite3

    stats = {"rss": {}, "twitter": {}}

    # RSS 统计
    with sqlite3.connect(rss_db.db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM rss_items")
        stats["rss"]["total"] = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM rss_items WHERE is_pushed = 0")
        stats["rss"]["unpushed"] = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM rss_items WHERE is_pushed = 1")
        stats["rss"]["pushed"] = cursor.fetchone()[0]

        cursor = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM rss_items GROUP BY source ORDER BY cnt DESC"
        )
        stats["rss"]["by_source"] = dict(cursor.fetchall())

    # Twitter 统计
    with sqlite3.connect(twitter_db.db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM tweets")
        stats["twitter"]["total"] = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM tweets WHERE is_pushed = 0")
        stats["twitter"]["unpushed"] = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM tweets WHERE is_pushed = 1")
        stats["twitter"]["pushed"] = cursor.fetchone()[0]

        cursor = conn.execute(
            "SELECT author_handle, COUNT(*) as cnt FROM tweets GROUP BY author_handle ORDER BY cnt DESC LIMIT 10"
        )
        stats["twitter"]["by_author"] = dict(cursor.fetchall())

    return stats


def print_stats(stats: dict) -> None:
    """打印统计信息"""
    print("=" * 60)
    print("[Stats] 数据库统计")
    print("=" * 60)

    # RSS
    print("\n[RSS] 数据库 (data/rss.db)")
    print(f"  总计: {stats['rss']['total']} 条")
    print(f"  已推送: {stats['rss']['pushed']} 条")
    print(f"  未推送: {stats['rss']['unpushed']} 条")
    if stats["rss"]["by_source"]:
        print("  按来源:")
        for source, count in stats["rss"]["by_source"].items():
            print(f"    - {source}: {count}")

    # Twitter
    print("\n[Twitter] 数据库 (data/twitter.db)")
    print(f"  总计: {stats['twitter']['total']} 条")
    print(f"  已推送: {stats['twitter']['pushed']} 条")
    print(f"  未推送: {stats['twitter']['unpushed']} 条")
    if stats["twitter"]["by_author"]:
        print("  按作者:")
        for author, count in stats["twitter"]["by_author"].items():
            print(f"    - @{author}: {count}")


def print_rss_items(rss_db: RSSDatabase, limit: int | None = 10) -> None:
    """打印 RSS 条目"""
    import sqlite3

    print("\n" + "=" * 60)
    print("[RSS] 条目" + (f" (前 {limit} 条)" if limit else " (全部)"))
    print("=" * 60)

    with sqlite3.connect(rss_db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM rss_items ORDER BY fetched_at DESC"
        if limit:
            query += f" LIMIT {limit}"
        cursor = conn.execute(query)

        for i, row in enumerate(cursor.fetchall(), 1):
            pushed = "[OK]" if row["is_pushed"] else "[..]"
            title = row['title'][:60] if row['title'] else '(无标题)'
            print(f"\n[{i}] {pushed} {title}")
            print(f"    来源: {row['source']} | 分类: {row['category']}")
            url = row['url'][:70] if row['url'] else '(无URL)'
            print(f"    URL: {url}")
            print(f"    发布: {row['published_at']} | 抓取: {row['fetched_at']}")


def print_tweets(twitter_db: TwitterDatabase, limit: int | None = 10) -> None:
    """打印推文"""
    import sqlite3

    print("\n" + "=" * 60)
    print("[Twitter] 推文" + (f" (前 {limit} 条)" if limit else " (全部)"))
    print("=" * 60)

    with sqlite3.connect(twitter_db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        query = "SELECT * FROM tweets ORDER BY fetched_at DESC"
        if limit:
            query += f" LIMIT {limit}"
        cursor = conn.execute(query)

        for i, row in enumerate(cursor.fetchall(), 1):
            pushed = "[OK]" if row["is_pushed"] else "[..]"
            content_preview = (row["content"][:80] if row["content"] else "").replace("\n", " ")
            print(f"\n[{i}] {pushed} @{row['author_handle']}")
            print(f"    {content_preview}...")
            print(f"    Likes: {row['likes']} | Reposts: {row['reposts']} | Views: {row['views']}")
            print(f"    URL: {row['tweet_url']}")
            print(f"    发布: {row['published_at']} | 抓取: {row['fetched_at']}")


def main():
    parser = argparse.ArgumentParser(description="数据库检查工具")
    parser.add_argument("--all", action="store_true", help="显示所有记录")
    parser.add_argument("--stats", action="store_true", help="仅显示统计")
    parser.add_argument("--limit", type=int, default=10, help="显示条目数量 (默认 10)")
    args = parser.parse_args()

    rss_db = RSSDatabase()
    twitter_db = TwitterDatabase()

    # 统计
    stats = get_db_stats(rss_db, twitter_db)
    print_stats(stats)

    # 条目
    if not args.stats:
        limit = None if args.all else args.limit
        print_rss_items(rss_db, limit)
        print_tweets(twitter_db, limit)


if __name__ == "__main__":
    main()
