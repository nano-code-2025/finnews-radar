"""采集器基类"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class NewsItem:
    """新闻条目"""
    id: str
    title: str
    content: str
    source: str
    category: str
    url: str
    published_at: datetime
    raw_data: dict[str, Any] = field(default_factory=dict)


class BaseCollector(ABC):
    """采集器基类"""

    @abstractmethod
    async def collect(self) -> list[NewsItem]:
        """采集新闻，返回 NewsItem 列表"""
        pass
