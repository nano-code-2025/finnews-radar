"""数据库模块 - RSS 和 Twitter 分开存储"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_db_summary() -> dict[str, Any]:
    """获取所有数据库的摘要统计

    Returns:
        {
            "rss": {"total": N, "pushed": N, "unpushed": N, "by_source": {...}},
            "twitter": {"total": N, "pushed": N, "unpushed": N, "by_group": {...}, "top_authors": {...}}
        }
    """
    rss_db = RSSDatabase()
    twitter_db = TwitterDatabase()

    stats: dict[str, Any] = {"rss": {}, "twitter": {}}

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
            "SELECT author_handle, COUNT(*) as cnt FROM tweets GROUP BY author_handle ORDER BY cnt DESC"
        )
        stats["twitter"]["by_author"] = dict(cursor.fetchall())

        cursor = conn.execute(
            "SELECT author_handle, COUNT(*) as cnt FROM tweets GROUP BY author_handle ORDER BY cnt DESC LIMIT 10"
        )
        stats["twitter"]["top_authors"] = dict(cursor.fetchall())

    return stats


class RSSDatabase:
    """RSS 数据库"""

    def __init__(self, db_path: str = "data/rss.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rss_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT,
                    source TEXT NOT NULL,
                    category TEXT,
                    published_at TIMESTAMP,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_pushed INTEGER DEFAULT 0,
                    pushed_at TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rss_url ON rss_items(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rss_pushed ON rss_items(is_pushed, fetched_at)")
            conn.commit()

    def is_exists(self, url: str) -> bool:
        """检查 URL 是否已存在"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM rss_items WHERE url = ?", (url,))
            return cursor.fetchone() is not None

    def insert(self, item: dict[str, Any]) -> bool:
        """插入新条目，返回是否成功（重复则失败）"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO rss_items (url, title, content, source, category, published_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["url"],
                        item["title"],
                        item.get("content", ""),
                        item["source"],
                        item.get("category", ""),
                        item.get("published_at"),
                    ),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_pushed(self, urls: list[str]) -> None:
        """标记为已推送"""
        if not urls:
            return
        with sqlite3.connect(self.db_path) as conn:
            now = datetime.now(timezone.utc).isoformat()
            placeholders = ",".join("?" * len(urls))
            conn.execute(
                f"UPDATE rss_items SET is_pushed = 1, pushed_at = ? WHERE url IN ({placeholders})",
                [now, *urls],
            )
            conn.commit()

    def get_unpushed(self) -> list[dict[str, Any]]:
        """获取未推送的条目"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, url, title, content, source, category, published_at, fetched_at
                FROM rss_items
                WHERE is_pushed = 0
                ORDER BY fetched_at DESC
                """
            )
            return [dict(row) for row in cursor.fetchall()]


class TwitterDatabase:
    """Twitter 数据库"""

    def __init__(self, db_path: str = "data/twitter.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表（标准推文存储，不含业务分组）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tweets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tweet_id TEXT UNIQUE,
                    tweet_url TEXT,
                    author_handle TEXT NOT NULL,
                    author_name TEXT,
                    content TEXT NOT NULL,
                    published_at TIMESTAMP,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    likes INTEGER DEFAULT 0,
                    reposts INTEGER DEFAULT 0,
                    replies INTEGER DEFAULT 0,
                    views INTEGER DEFAULT 0,
                    media_urls TEXT,
                    external_urls TEXT,
                    is_pushed INTEGER DEFAULT 0,
                    pushed_at TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_id ON tweets(tweet_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_pushed ON tweets(is_pushed, fetched_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tweets_author ON tweets(author_handle)")
            conn.commit()

    def is_exists(self, tweet_id: str) -> bool:
        """检查推文 ID 是否已存在"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT 1 FROM tweets WHERE tweet_id = ?", (tweet_id,))
            return cursor.fetchone() is not None

    def insert(self, tweet: dict[str, Any]) -> bool:
        """插入新推文，返回是否成功（重复则失败）"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO tweets (
                        tweet_id, tweet_url, author_handle, author_name, content,
                        published_at, likes, reposts, replies, views,
                        media_urls, external_urls
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tweet.get("tweet_id"),
                        tweet.get("tweet_url", ""),
                        tweet["author_handle"],
                        tweet.get("author_name", ""),
                        tweet["content"],
                        tweet.get("published_at"),
                        tweet.get("likes", 0),
                        tweet.get("reposts", 0),
                        tweet.get("replies", 0),
                        tweet.get("views", 0),
                        json.dumps(tweet.get("media_urls")) if tweet.get("media_urls") else None,
                        json.dumps(tweet.get("external_urls")) if tweet.get("external_urls") else None,
                    ),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def mark_pushed(self, tweet_ids: list[str]) -> None:
        """标记为已推送"""
        if not tweet_ids:
            return
        with sqlite3.connect(self.db_path) as conn:
            now = datetime.now(timezone.utc).isoformat()
            placeholders = ",".join("?" * len(tweet_ids))
            conn.execute(
                f"UPDATE tweets SET is_pushed = 1, pushed_at = ? WHERE tweet_id IN ({placeholders})",
                [now, *tweet_ids],
            )
            conn.commit()

    def get_unpushed(self) -> list[dict[str, Any]]:
        """获取未推送的推文"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, tweet_id, tweet_url, author_handle, author_name, content,
                       published_at, fetched_at, likes, reposts, replies, views,
                       media_urls, external_urls
                FROM tweets
                WHERE is_pushed = 0
                ORDER BY fetched_at DESC
                """
            )
            rows = []
            for row in cursor.fetchall():
                d = dict(row)
                # 解析 JSON 字段
                if d.get("media_urls"):
                    d["media_urls"] = json.loads(d["media_urls"])
                if d.get("external_urls"):
                    d["external_urls"] = json.loads(d["external_urls"])
                rows.append(d)
            return rows
