"""去重器"""
from hashlib import sha256

from ..collectors.base import NewsItem
from .base import AnalyzedItem


class Deduplicator:
    """新闻去重器"""

    def __init__(self):
        self.seen_hashes: set[str] = set()

    def process(self, items: list[NewsItem]) -> list[AnalyzedItem]:
        """去重并转换为 AnalyzedItem"""
        result = []

        for item in items:
            # 基于标题+内容前100字生成哈希
            text = f"{item.title}{item.content[:100]}"
            content_hash = sha256(text.encode()).hexdigest()

            is_dup = content_hash in self.seen_hashes
            self.seen_hashes.add(content_hash)

            analyzed = AnalyzedItem(
                id=item.id,
                title=item.title,
                content=item.content,
                source=item.source,
                category=item.category,
                url=item.url,
                published_at=item.published_at,
                is_duplicate=is_dup,
                raw_data=item.raw_data,
            )
            result.append(analyzed)

        return result

    def filter_duplicates(self, items: list[AnalyzedItem]) -> list[AnalyzedItem]:
        """过滤掉重复项"""
        return [i for i in items if not i.is_duplicate]

    def clear_cache(self):
        """清空缓存"""
        self.seen_hashes.clear()
