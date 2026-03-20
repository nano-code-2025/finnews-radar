"""Twitter 采集器 (使用 Grok x_search)

官方文档: https://docs.x.ai/docs/guides/tools/search-tools#x-search-parameters

经测试发现:
- 单个 tool 单账号最可靠
- 多个 tools 时 Grok 可能返回空结果
- 因此采用并行发起多个独立请求的策略
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from openai import AsyncOpenAI

from .base import BaseCollector, NewsItem
from ..utils.config import load_config, load_env

# 每个账号获取的推文数量（默认值，可被环境变量覆盖）
DEFAULT_TWEETS_PER_ACCOUNT = 2

# 搜索时间范围（天数）（默认值，可被环境变量覆盖）
DEFAULT_LOOKBACK_DAYS = 3

# 并行请求数限制（默认值，可被环境变量覆盖）
DEFAULT_MAX_CONCURRENT_REQUESTS = 5


@dataclass
class Tweet:
    """结构化推文"""
    tweet_id: str
    tweet_url: str
    author_handle: str
    author_name: str
    content: str
    published_at: datetime | None
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    views: int = 0
    media_urls: list[str] | None = None
    external_urls: list[str] | None = None
    monitoring_group: str = ""


class TwitterCollector(BaseCollector):
    """Twitter 采集器，使用 xAI Grok x_search"""

    def __init__(self, tweets_per_account: int = DEFAULT_TWEETS_PER_ACCOUNT):
        self.config = load_config("twitter_accounts")
        env = load_env()
        self.tweets_per_account = int(
            env.get("twitter_tweets_per_account") or tweets_per_account
        )
        self.lookback_days = int(
            env.get("twitter_lookback_days") or DEFAULT_LOOKBACK_DAYS
        )
        self.max_concurrent_requests = int(
            env.get("twitter_max_concurrent_requests") or DEFAULT_MAX_CONCURRENT_REQUESTS
        )
        api_key = env.get("xai_api_key")
        if not api_key:
            raise ValueError("XAI_API_KEY 未配置")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )
        self.errors: list[str] = []

    async def collect(self, group: str | None = None) -> list[NewsItem]:
        """采集 Twitter 内容，返回 NewsItem 列表"""
        tweets = await self.collect_tweets(group)
        return [self._tweet_to_news_item(t) for t in tweets]

    async def collect_tweets(self, group: str | None = None) -> list[Tweet]:
        """采集 Twitter 内容，返回结构化 Tweet 列表"""
        tweets: list[Tweet] = []
        groups = [group] if group else list(self.config.keys())

        for grp in groups:
            if grp not in self.config:
                continue
            group_config = self.config[grp]
            raw_accounts = group_config["accounts"]
            # 支持个人权重格式: [{handle: weight}, ...] 或 [handle, ...]
            accounts = []
            for entry in raw_accounts:
                if isinstance(entry, dict):
                    accounts.extend(entry.keys())
                else:
                    accounts.append(entry)

            print(f"[Twitter] 采集组 {grp} ({len(accounts)} 账号)...")
            try:
                batch_tweets = await self._search_accounts_parallel(accounts, grp)
                tweets.extend(batch_tweets)
                print(f"[Twitter] 组 {grp} 完成，获取 {len(batch_tweets)} 条")
            except Exception as e:
                print(f"[Twitter] 采集失败 {grp}: {e}")

        return tweets

    async def _search_accounts_parallel(self, accounts: list[str], group: str) -> list[Tweet]:
        """并行搜索多个账号（每个账号独立请求）"""
        semaphore = asyncio.Semaphore(self.max_concurrent_requests)

        async def search_with_limit(handle: str) -> list[Tweet]:
            async with semaphore:
                return await self._search_single_account(handle, group)

        tasks = [search_with_limit(handle) for handle in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        tweets: list[Tweet] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"[Twitter] @{accounts[i]} 失败: {type(result).__name__}")
            else:
                tweets.extend(result)

        return tweets

    async def _search_single_account(self, handle: str, group: str) -> list[Tweet]:
        """搜索单个账号的推文

        官方格式:
        tools=[{
            "type": "x_search",
            "allowed_x_handles": ["handle"],
            "from_date": "2026-02-01",  # 3天前
        }]
        """
        from datetime import timedelta
        from_date = (
            datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        ).strftime("%Y-%m-%d")

        tool = {
            "type": "x_search",
            "allowed_x_handles": [handle],
            "from_date": from_date,
        }

        prompt = (
            f"获取 @{handle} 最近 {self.tweets_per_account} 条推文。\n"
            "以 JSON 格式返回，不要 markdown 代码块：\n"
            '{"posts": [{"id": "推文ID", "author": {"name": "名称", "handle": "用户名"}, '
            '"timestamp": "时间", "content": "内容", '
            '"engagement": {"likes": 0, "reposts": 0, "replies": 0, "views": 0}}]}'
        )

        try:
            response = await asyncio.wait_for(
                self.client.responses.create(
                    model="grok-4-fast",
                    tools=[tool],
                    input=[{"role": "user", "content": prompt}],
                ),
                timeout=60.0,
            )
        except asyncio.TimeoutError:
            print(f"[Twitter] @{handle} 超时")
            self.errors.append(f"@{handle} 超时 (>60s)")
            return []
        except Exception as e:
            err_str = str(e)
            print(f"[Twitter] @{handle} 请求失败: {e}")
            # 429 = 额度耗尽，记录告警
            if "429" in err_str or "exhausted" in err_str or "spending limit" in err_str:
                self.errors.append(f"@{handle} API 额度不足 (429)")
            else:
                self.errors.append(f"@{handle} 请求失败: {type(e).__name__}")
            return []

        text = self._extract_response_text(response)
        if not text:
            print(f"[Twitter] @{handle} 返回空响应")
            return []

        return self._parse_tweets(text, group)

    def _extract_response_text(self, resp: Any) -> str:
        """从响应中提取文本"""
        output_text = getattr(resp, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        texts: list[str] = []
        for out in getattr(resp, "output", []) or []:
            if getattr(out, "type", None) != "message":
                continue
            for c in getattr(out, "content", []) or []:
                t = getattr(c, "text", None)
                if isinstance(t, str) and t.strip():
                    texts.append(t)
        return "\n".join(texts).strip()

    def _parse_tweets(self, text: str, group: str) -> list[Tweet]:
        """解析 JSON 响应为 Tweet 列表"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            print(f"[Twitter] JSON 解析失败: {e}")
            return []

        posts = parsed.get("posts", [])
        if not isinstance(posts, list):
            return []

        tweets: list[Tweet] = []
        for p in posts:
            if not isinstance(p, dict):
                continue

            tweet_id = str(p.get("id", "") or "")
            author = p.get("author", {}) or {}
            handle = str(author.get("handle", "") or "").lstrip("@")
            engagement = p.get("engagement", {}) or {}

            if not handle or not p.get("content"):
                continue

            tweet_url = f"https://x.com/{handle}/status/{tweet_id}" if tweet_id else ""
            published_at = self._parse_timestamp(p.get("timestamp"))

            tweet = Tweet(
                tweet_id=tweet_id,
                tweet_url=tweet_url,
                author_handle=handle,
                author_name=str(author.get("name", "") or ""),
                content=str(p.get("content", "") or ""),
                published_at=published_at,
                likes=self._safe_int(engagement.get("likes")),
                reposts=self._safe_int(engagement.get("reposts")),
                replies=self._safe_int(engagement.get("replies")),
                views=self._safe_int(engagement.get("views")),
                media_urls=p.get("media"),
                external_urls=p.get("urls"),
                monitoring_group=group,
            )
            tweets.append(tweet)

        return tweets

    def _safe_int(self, val: Any) -> int:
        if val is None:
            return 0
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    def _parse_timestamp(self, ts: Any) -> datetime | None:
        if not ts:
            return None
        try:
            dt = parsedate_to_datetime(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return None

    def _tweet_to_news_item(self, tweet: Tweet) -> NewsItem:
        return NewsItem(
            id=tweet.tweet_id or f"tw_{hash(tweet.content)}"[:12],
            title=f"@{tweet.author_handle}" if tweet.author_handle else "Twitter",
            content=tweet.content,
            source="Twitter",
            category=f"TWITTER_{tweet.monitoring_group.upper()}",
            url=tweet.tweet_url,
            published_at=tweet.published_at or datetime.now(timezone.utc),
            raw_data={
                "source_type": "twitter",
                "group": tweet.monitoring_group,
                "tweet_id": tweet.tweet_id,
                "author_handle": tweet.author_handle,
                "author_name": tweet.author_name,
                "likes": tweet.likes,
                "reposts": tweet.reposts,
                "replies": tweet.replies,
                "views": tweet.views,
                "media_urls": tweet.media_urls,
                "external_urls": tweet.external_urls,
            },
        )

    def get_tweet_dict(self, tweet: Tweet) -> dict[str, Any]:
        """转换为数据库存储格式（不含业务分组）"""
        return {
            "tweet_id": tweet.tweet_id,
            "tweet_url": tweet.tweet_url,
            "author_handle": tweet.author_handle,
            "author_name": tweet.author_name,
            "content": tweet.content,
            "published_at": tweet.published_at.isoformat() if tweet.published_at else None,
            "likes": tweet.likes,
            "reposts": tweet.reposts,
            "replies": tweet.replies,
            "views": tweet.views,
            "media_urls": tweet.media_urls,
            "external_urls": tweet.external_urls,
        }

    async def close(self):
        await self.client.close()
