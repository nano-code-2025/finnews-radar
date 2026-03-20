"""RSS 管道 — 取数据 → 转换 → 去重 → Sourcing + Ranking → 持久化过滤结果

TODO: 管道执行耗时统计（每阶段计时）
TODO: 错误恢复机制（FinBERT 失败时 fallback 到纯规则评分）
TODO: 异步管道（当前 RSSFilter 是同步的，FinBERT 推理可异步）
"""
from datetime import datetime, timezone

from ..analyzers.base import AnalyzedItem
from ..analyzers.deduplicator import Deduplicator
from ..analyzers.rss_filter import RSSFilter
from ..collectors.base import NewsItem
from ..utils.db import RSSDatabase
from ..utils.features_db import FeaturesDatabase
from .base import BasePipeline, FilterResult


class RSSPipeline(BasePipeline):
    """RSS 完整管道

    流程：DB 取 unpushed → 转换 AnalyzedItem → 去重 → RSSFilter → FilterResult
    """

    def __init__(self, use_mock: bool = False) -> None:
        self.db = RSSDatabase()
        self.features_db = FeaturesDatabase()
        self.dedup = Deduplicator()
        self.rss_filter = RSSFilter(use_mock=use_mock)
        self._all_urls: list[str] = []

    def run(self) -> FilterResult:
        """执行完整 RSS 管道"""
        # 1. DB 取 unpushed
        unpushed = self.db.get_unpushed()
        print(f"[RSSPipeline] 待处理 {len(unpushed)} 条")

        if not unpushed:
            return FilterResult()

        # 2. 转换为 NewsItem → AnalyzedItem
        news_items = self._convert(unpushed)

        # 3. 去重
        analyzed = self.dedup.process(news_items)
        analyzed = self.dedup.filter_duplicates(analyzed)
        print(f"[RSSPipeline] 去重后 {len(analyzed)} 条")

        if not analyzed:
            return FilterResult()

        # 4. Sourcing + Ranking 过滤
        result = self.rss_filter.filter(analyzed)

        # 5. 持久化过滤结果到 features.db（passed + skipped 都写入）
        self._persist_rss_features(result)

        # 6. 收集所有 URL（含 skipped），用于 mark_pushed
        self._all_urls = [
            item.url for item in result.passed + result.skipped if item.url
        ]

        return result

    def get_all_ids(self) -> list[str]:
        """返回所有处理过的 URL（含 skipped）"""
        return self._all_urls

    def _persist_rss_features(self, result: FilterResult) -> None:
        """将 passed + skipped 的过滤结果写入 features.db rss_features 表"""
        now = datetime.now(timezone.utc).isoformat()
        records: list[dict] = []

        for item in result.passed:
            records.append(self._item_to_rss_record(item, "passed", now))

        for item in result.skipped:
            skip_reason = item.raw_data.get("skip_reason", "below_threshold")
            records.append(self._item_to_rss_record(item, "skipped", now, skip_reason))

        saved = self.features_db.insert_rss_batch(records)
        print(f"[RSSPipeline] RSS 过滤结果持久化: {saved}/{len(records)} 条")

    @staticmethod
    def _item_to_rss_record(
        item: AnalyzedItem, result: str, processed_at: str, skip_reason: str | None = None
    ) -> dict:
        """AnalyzedItem → rss_features 记录"""
        raw = item.raw_data
        return {
            "url": item.url,
            "title": item.title,
            "source": item.source,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "processed_at": processed_at,
            "sourcing_category": raw.get("sourcing_category"),
            "matched_keywords": raw.get("matched_keywords", []),
            "matched_count": raw.get("matched_count", 0),
            "is_macro": raw.get("is_macro", False),
            "vader_score": raw.get("vader_score"),
            "finbert_score": raw.get("finbert_score"),
            "sentiment": raw.get("sentiment"),
            "sentiment_direction": raw.get("sentiment_direction"),
            "divergence": raw.get("divergence"),
            "is_divergent": raw.get("is_divergent", False),
            "score": item.score,
            "result": result,
            "skip_reason": skip_reason,
        }

    def _convert(self, rows: list[dict]) -> list[NewsItem]:
        """DB row → NewsItem"""
        items: list[NewsItem] = []
        for row in rows:
            item = NewsItem(
                id=str(row["id"]),
                title=row["title"],
                content=row["content"] or "",
                source=row["source"],
                category=row["category"] or "",
                url=row["url"],
                published_at=(
                    datetime.fromisoformat(row["published_at"])
                    if row["published_at"]
                    else datetime.now(timezone.utc)
                ),
                raw_data={"source_type": "rss", "db_id": row["id"]},
            )
            items.append(item)
        return items
