"""特征数据库 — 独立于 twitter.db/rss.db，长期保存特征用于回测和报告"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class FeaturesDatabase:
    """特征数据库

    Tables:
        post_features: 每条推文的特征快照（topic, sentiment, engagement, rationality, length）
        rss_features: RSS 过滤结果快照（Sourcing + Ranking 全量，含 passed 和 skipped）
        engagement_snapshots: engagement 时序快照（P1 TODO — 表已建，写入逻辑待实现）
    """

    def __init__(self, db_path: str = "data/features.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            # 主特征表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS post_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tweet_id TEXT NOT NULL,
                    author_handle TEXT NOT NULL,
                    published_at TEXT,
                    extracted_at TEXT NOT NULL,

                    -- 特征字段
                    topic TEXT,
                    sentiment REAL,
                    fear_score REAL,
                    fomo_score REAL,
                    rationality REAL,
                    length INTEGER,
                    event_tags TEXT,
                    llm_enhanced INTEGER DEFAULT 0,

                    -- engagement 明细
                    likes INTEGER DEFAULT 0,
                    replies INTEGER DEFAULT 0,
                    reposts INTEGER DEFAULT 0,
                    views INTEGER DEFAULT 0,
                    engagement_score REAL DEFAULT 0.0,

                    -- 元数据
                    source_tier TEXT DEFAULT 'L1',
                    author_weight REAL DEFAULT 1.0,
                    ranking_score REAL,

                    -- LLM 增强字段（规则值+LLM值并存，供对比）
                    llm_sentiment REAL,
                    llm_topic TEXT,
                    llm_rationality REAL,
                    llm_summary TEXT,
                    llm_fear_score REAL,
                    llm_fomo_score REAL,

                    UNIQUE(tweet_id, extracted_at)
                )
            """)
            # 已有数据库添加新列（忽略已存在错误）
            for col, col_type in [
                ("llm_sentiment", "REAL"),
                ("llm_topic", "TEXT"),
                ("llm_rationality", "REAL"),
                ("llm_summary", "TEXT"),
                ("fear_score", "REAL"),
                ("fomo_score", "REAL"),
                ("llm_fear_score", "REAL"),
                ("llm_fomo_score", "REAL"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE post_features ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass  # 列已存在
            conn.execute("CREATE INDEX IF NOT EXISTS idx_features_topic ON post_features(topic)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_features_published ON post_features(published_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_features_author ON post_features(author_handle)")

            # RSS 过滤结果表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rss_features (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT,
                    source TEXT,
                    published_at TEXT,
                    processed_at TEXT NOT NULL,

                    -- Sourcing 阶段
                    sourcing_category TEXT,
                    matched_keywords TEXT,
                    matched_count INTEGER DEFAULT 0,
                    is_macro INTEGER DEFAULT 0,

                    -- Ranking 阶段
                    vader_score REAL,
                    finbert_score REAL,
                    sentiment REAL,
                    sentiment_direction TEXT,
                    divergence REAL,
                    is_divergent INTEGER DEFAULT 0,
                    score REAL,

                    -- 结果
                    result TEXT,
                    skip_reason TEXT,

                    UNIQUE(url, processed_at)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rss_source ON rss_features(source)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rss_published ON rss_features(published_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rss_result ON rss_features(result)")

            # P1 TODO: Engagement 时序快照表
            # 重复采集的推文天然产生时序数据，dedup 前写入此表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS engagement_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tweet_id TEXT NOT NULL,
                    snapshot_at TEXT NOT NULL,

                    likes INTEGER DEFAULT 0,
                    replies INTEGER DEFAULT 0,
                    reposts INTEGER DEFAULT 0,
                    views INTEGER DEFAULT 0,

                    delta_likes INTEGER,
                    delta_replies INTEGER,
                    delta_reposts INTEGER,
                    delta_views INTEGER,

                    UNIQUE(tweet_id, snapshot_at)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_tweet ON engagement_snapshots(tweet_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_time ON engagement_snapshots(snapshot_at)")

            conn.commit()

    def insert_features(self, features: dict[str, Any]) -> bool:
        """插入特征记录，重复则跳过"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO post_features (
                        tweet_id, author_handle, published_at, extracted_at,
                        topic, sentiment, fear_score, fomo_score,
                        rationality, length, event_tags, llm_enhanced,
                        likes, replies, reposts, views, engagement_score,
                        source_tier, author_weight, ranking_score,
                        llm_sentiment, llm_topic, llm_rationality, llm_summary,
                        llm_fear_score, llm_fomo_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        features["tweet_id"],
                        features["author_handle"],
                        features.get("published_at"),
                        features.get("extracted_at", datetime.now(timezone.utc).isoformat()),
                        features.get("topic"),
                        features.get("sentiment"),
                        features.get("fear_score"),
                        features.get("fomo_score"),
                        features.get("rationality"),
                        features.get("length"),
                        json.dumps(features.get("event_tags", []), ensure_ascii=False),
                        1 if features.get("llm_enhanced") else 0,
                        features.get("likes", 0),
                        features.get("replies", 0),
                        features.get("reposts", 0),
                        features.get("views", 0),
                        features.get("engagement_score", 0.0),
                        features.get("source_tier", "L1"),
                        features.get("author_weight", 1.0),
                        features.get("ranking_score"),
                        features.get("llm_sentiment"),
                        features.get("llm_topic"),
                        features.get("llm_rationality"),
                        features.get("llm_summary"),
                        features.get("llm_fear_score"),
                        features.get("llm_fomo_score"),
                    ),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def insert_batch(self, features_list: list[dict[str, Any]]) -> int:
        """批量插入特征，返回成功数"""
        count = 0
        for features in features_list:
            if self.insert_features(features):
                count += 1
        return count

    def get_features_by_date(self, date_str: str) -> list[dict[str, Any]]:
        """按日期查询特征（YYYY-MM-DD）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM post_features
                WHERE published_at LIKE ?
                ORDER BY engagement_score DESC
                """,
                (f"{date_str}%",),
            )
            rows = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("event_tags"):
                    d["event_tags"] = json.loads(d["event_tags"])
                rows.append(d)
            return rows

    def get_features_by_time_range(self, start_utc: str, end_utc: str) -> list[dict[str, Any]]:
        """按 UTC 时间范围查询特征（ISO 字符串，闭开区间）"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM post_features
                WHERE published_at >= ?
                  AND published_at < ?
                ORDER BY engagement_score DESC
                """,
                (start_utc, end_utc),
            )
            rows = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("event_tags"):
                    d["event_tags"] = json.loads(d["event_tags"])
                rows.append(d)
            return rows

    def get_features_by_topic(self, topic: str, days: int = 7) -> list[dict[str, Any]]:
        """按 topic 查询最近 N 天的特征"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM post_features
                WHERE topic = ?
                  AND published_at >= datetime('now', ?)
                ORDER BY published_at DESC
                """,
                (topic, f"-{days} days"),
            )
            rows = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get("event_tags"):
                    d["event_tags"] = json.loads(d["event_tags"])
                rows.append(d)
            return rows

    # ── RSS features ──

    def insert_rss_features(self, features: dict[str, Any]) -> bool:
        """插入 RSS 过滤结果，重复则跳过"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO rss_features (
                        url, title, source, published_at, processed_at,
                        sourcing_category, matched_keywords, matched_count, is_macro,
                        vader_score, finbert_score, sentiment, sentiment_direction,
                        divergence, is_divergent, score,
                        result, skip_reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        features["url"],
                        features.get("title"),
                        features.get("source"),
                        features.get("published_at"),
                        features.get("processed_at", datetime.now(timezone.utc).isoformat()),
                        features.get("sourcing_category"),
                        json.dumps(features.get("matched_keywords", []), ensure_ascii=False),
                        features.get("matched_count", 0),
                        1 if features.get("is_macro") else 0,
                        features.get("vader_score"),
                        features.get("finbert_score"),
                        features.get("sentiment"),
                        features.get("sentiment_direction"),
                        features.get("divergence"),
                        1 if features.get("is_divergent") else 0,
                        features.get("score"),
                        features.get("result"),
                        features.get("skip_reason"),
                    ),
                )
                conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def insert_rss_batch(self, features_list: list[dict[str, Any]]) -> int:
        """批量插入 RSS 过滤结果，返回成功数"""
        count = 0
        for features in features_list:
            if self.insert_rss_features(features):
                count += 1
        return count

    def get_daily_summary(self, date_str: str) -> list[dict[str, Any]]:
        """获取某日按 topic 聚合的摘要，用于 24h 报告"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT
                    topic,
                    COUNT(*) as post_count,
                    AVG(sentiment) as avg_sentiment,
                    AVG(fear_score) as avg_fear,
                    AVG(fomo_score) as avg_fomo,
                    SUM(engagement_score) as total_engagement,
                    AVG(rationality) as avg_rationality,
                    AVG(length) as avg_length
                FROM post_features
                WHERE published_at LIKE ?
                GROUP BY topic
                ORDER BY total_engagement DESC
                """,
                (f"{date_str}%",),
            )
            return [dict(row) for row in cursor.fetchall()]
