""" RSS NEWS 评分器"""
from .base import AnalyzedItem


# 来源权重
SOURCE_WEIGHTS = {
    "Federal Reserve": 10,
    "SEC Press": 9,
    "Twitter": 8,
    "CoinDesk": 7,
    "The Block": 7,
    "Cointelegraph": 6,
    "Decrypt": 6,
}

# 关键词紧急度
URGENCY_KEYWORDS = {
    # 10分 - 极紧急
    "hack": 10, "exploit": 10, "rug pull": 10, "emergency": 10,
    # 9分 - 高紧急
    "sec lawsuit": 9, "rate decision": 9, "fomc": 9, "stolen": 9,
    # 8分 - 重要
    "regulation": 8, "etf approved": 8, "whale": 8, "investigation": 8,
    # 7分 - 较重要
    "cpi": 7, "inflation": 7, "interest rate": 7, "upgrade": 7,
}

# 分类基础分
CATEGORY_BASE_SCORES = {
    "FED_POLICY": 8,
    "REGULATION": 8,
    "SECURITY": 9,
    "WHALE": 7,
    "ETF": 7,
    "INFLATION": 6,
    "TECH": 5,
    "CRYPTO_NEWS": 4,
}


class Scorer:
    """新闻评分器"""

    def process(self, items: list[AnalyzedItem]) -> list[AnalyzedItem]:
        """计算每条新闻的重要性评分"""
        for item in items:
            item.score = self._calculate_score(item)
        return items

    def _calculate_score(self, item: AnalyzedItem) -> float:
        """计算综合评分 (0-10)"""
        text = f"{item.title} {item.content}".lower()

        # 1. 来源权重 (0-10)
        source_score = SOURCE_WEIGHTS.get(item.source, 5)

        # 2. 关键词紧急度 (0-10)
        keyword_score = 0
        for keyword, score in URGENCY_KEYWORDS.items():
            if keyword in text:
                keyword_score = max(keyword_score, score)

        # 3. 分类基础分 (0-10)
        category_score = CATEGORY_BASE_SCORES.get(item.category, 4)

        # 综合计算: 来源30% + 关键词40% + 分类30%
        final = source_score * 0.3 + keyword_score * 0.4 + category_score * 0.3

        return round(min(10, final), 1)
