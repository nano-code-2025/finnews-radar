"""RSS 采集器"""
import hashlib
from datetime import datetime
from time import mktime

import feedparser
import httpx

from .base import BaseCollector, NewsItem
from ..utils.config import load_config


class RSSCollector(BaseCollector):
    """RSS 新闻采集器"""

    def __init__(self):
        self.config = load_config("rss_sources")
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; RSSCollector/1.0; +https://example.com/bot)"
            },
        )

    async def collect(self) -> list[NewsItem]:
        """采集所有 RSS 源"""
        items = []
        total_sources = sum(len(sources) for sources in self.config.values())
        print(f"[RSS] 开始采集 {total_sources} 个源...")

        for group_name, sources in self.config.items():
            for source in sources:
                try:
                    news = await self._fetch_feed(source)
                    items.extend(news)
                    print(f"[RSS] {source['name']}: {len(news)} 条")
                except Exception as e:
                    print(f"[RSS] 采集失败 {source['name']}: {e}")

        print(f"[RSS] 采集完成，共 {len(items)} 条")
        return items

    async def _fetch_feed(self, source: dict) -> list[NewsItem]:
        """获取单个 RSS 源"""
        feed = await self._parse_feed_with_fallback(source["url"])

        items = []
        for entry in feed.entries[:10]:  # 每个源取最新10条
            item = NewsItem(
                id=self._generate_id(entry.get("link", entry.get("title"))),
                title=entry.get("title", ""),
                content=entry.get("summary", entry.get("description", "")),
                source=source["name"],
                category=source["category"],
                url=entry.get("link", ""),
                published_at=self._parse_time(entry),
                raw_data={"source_type": "rss", "feed_name": source["name"]},
            )
            items.append(item)

        return items

    async def _parse_feed_with_fallback(self, url: str):
        """尽最大努力解析 RSS，失败时退化为直接解析 URL."""
        try:
            resp = await self.client.get(url)
            if 200 <= resp.status_code < 400 and resp.text:
                feed = feedparser.parse(resp.text)
                if not feed.bozo:
                    return feed
        except Exception:
            pass

        try:
            return feedparser.parse(url)
        except Exception:
            return feedparser.parse("")

    def _generate_id(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()[:12]

    def _parse_time(self, entry) -> datetime:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime.fromtimestamp(mktime(entry.published_parsed))
        return datetime.now()

    async def close(self):
        await self.client.aclose()
