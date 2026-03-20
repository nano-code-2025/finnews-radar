"""管道基类 — RSS 和 Twitter 管道的统一接口"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..analyzers.base import AnalyzedItem


@dataclass
class FilterResult:
    """统一的管道过滤结果

    passed: 通过过滤，需要推送的条目
    skipped: 被过滤掉，但仍需标记 pushed 防止重复处理
    """
    passed: list[AnalyzedItem] = field(default_factory=list)
    skipped: list[AnalyzedItem] = field(default_factory=list)


class BasePipeline(ABC):
    """管道基类

    每个管道负责：取数据 → 转换 → 去重 → 过滤 → 返回结果
    """

    @abstractmethod
    def run(self) -> FilterResult:
        """执行完整管道，返回过滤结果"""
        pass

    @abstractmethod
    def get_all_ids(self) -> list[str]:
        """返回所有处理过的 ID（含 skipped），用于 mark_pushed"""
        pass
