"""Twitter 管道 v3 — 取数据 → 转换 → 去重 → Sourcing → Feature Extraction → Ranking → 持久化"""
from datetime import datetime, timezone

from ..analyzers.base import AnalyzedItem
from ..analyzers.deduplicator import Deduplicator
from ..analyzers.feature_extractor import FeatureExtractor
from ..analyzers.twitter_filter import TwitterFilter
from ..collectors.base import NewsItem
from ..utils.db import TwitterDatabase
from ..utils.features_db import FeaturesDatabase
from .base import BasePipeline, FilterResult


class TwitterPipeline(BasePipeline):
    """Twitter 完整管道 (v3)

    流程：DB 取 unpushed → 转换 → 去重 → TwitterFilter(Sourcing+Ranking+Sentiment)
          → Feature Extraction → 持久化 features.db → FilterResult
    """

    def __init__(self, top_n: int = 0, enable_llm: bool = True) -> None:
        self.db = TwitterDatabase()
        self.features_db = FeaturesDatabase()
        self.dedup = Deduplicator()
        self.twitter_filter = TwitterFilter(top_n=top_n)
        self.feature_extractor = FeatureExtractor(enable_llm=enable_llm)
        self._all_tweet_ids: list[str] = []

    def run(self) -> FilterResult:
        """执行完整 Twitter 管道"""
        # 1. DB 取 unpushed
        unpushed = self.db.get_unpushed()
        print(f"[TwitterPipeline] 待处理 {len(unpushed)} 条")

        if not unpushed:
            return FilterResult()

        # 2. 转换为 NewsItem → AnalyzedItem
        news_items = self._convert(unpushed)

        # 3. 去重
        analyzed = self.dedup.process(news_items)
        analyzed = self.dedup.filter_duplicates(analyzed)
        print(f"[TwitterPipeline] 去重后 {len(analyzed)} 条")

        if not analyzed:
            return FilterResult()

        # 4. Sourcing + Ranking + Sentiment 过滤
        result = self.twitter_filter.filter(analyzed)

        # 5. Feature Extraction — 对通过 Sourcing+Ranking 的推文提取特征
        if result.passed:
            features_list = self.feature_extractor.extract_batch(result.passed)

            # 回写 ranking_score 到特征
            for item, features in zip(result.passed, features_list):
                features["ranking_score"] = item.score
                # 将特征存入 raw_data 供 Telegram pusher 使用
                item.raw_data["features"] = features

            # 6. 持久化到 features.db
            saved = self.features_db.insert_batch(features_list)
            print(f"[TwitterPipeline] 特征持久化: {saved}/{len(features_list)} 条")

        # 7. 收集所有 tweet_id（含 skipped），用于 mark_pushed
        self._all_tweet_ids = [
            item.raw_data.get("tweet_id")
            for item in result.passed + result.skipped
            if item.raw_data.get("tweet_id")
        ]

        return result

    def get_all_ids(self) -> list[str]:
        """返回所有处理过的 tweet_id（含 skipped）"""
        return self._all_tweet_ids

    def _convert(self, rows: list[dict]) -> list[NewsItem]:
        """DB row → NewsItem"""
        items: list[NewsItem] = []
        for row in rows:
            item = NewsItem(
                id=row["tweet_id"] or str(row["id"]),
                title=f"@{row['author_handle']}" if row["author_handle"] else "Twitter",
                content=row["content"],
                source="Twitter",
                category="TWITTER",
                url=row["tweet_url"] or "",
                published_at=(
                    datetime.fromisoformat(row["published_at"])
                    if row["published_at"]
                    else datetime.now(timezone.utc)
                ),
                raw_data={
                    "source_type": "twitter",
                    "db_id": row["id"],
                    "tweet_id": row["tweet_id"],
                    "author_handle": row["author_handle"],
                    "likes": row["likes"],
                    "reposts": row["reposts"],
                    "replies": row["replies"],
                    "views": row["views"],
                },
            )
            items.append(item)
        return items
