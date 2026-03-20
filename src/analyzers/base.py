"""分析器基类"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class AnalyzedItem:
    """分析后的新闻条目"""
    id: str
    title: str
    content: str
    source: str
    category: str
    url: str
    published_at: datetime

    # 分析结果
    score: float = 0.0  # 重要性评分 0-10
    is_duplicate: bool = False
    keywords: list[str] = field(default_factory=list)
    grok_analysis: str = ""  # Grok 深度分析结果

    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def urgency(self) -> str:
        """紧急程度（RSS 0-1 范围 / Twitter 原始分数）"""
        if self.score >= 0.6:
            return "URGENT"  # 红色
        elif self.score >= 0.3:
            return "IMPORTANT"  # 橙色
        else:
            return "NORMAL"  # 常规


class BaseAnalyzer(ABC):
    """分析器基类"""

    @abstractmethod
    async def analyze(self, items: list) -> list:
        """分析处理"""
        pass
