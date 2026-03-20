"""分类器"""
import re

from .base import AnalyzedItem


# 关键词分类规则
CATEGORY_KEYWORDS = {
    "FED_POLICY": ["fed", "fomc", "interest rate", "powell", "monetary policy", "federal reserve"],
    "INFLATION": ["cpi", "inflation", "pce", "consumer price"],
    "REGULATION": ["sec", "cftc", "regulation", "lawsuit", "enforcement", "compliance"],
    "SECURITY": ["hack", "exploit", "vulnerability", "rug pull", "scam", "stolen"],
    "WHALE": ["whale", "large transfer", "million", "billion", "deposit", "withdraw"],
    "ETF": ["etf", "grayscale", "blackrock", "spot bitcoin", "spot ethereum"],
    "TECH": ["upgrade", "fork", "mainnet", "testnet", "protocol"],
}


class Classifier:
    """新闻分类器"""

    def process(self, items: list[AnalyzedItem]) -> list[AnalyzedItem]:
        """对新闻进行分类和关键词提取"""
        for item in items:
            # 提取关键词
            item.keywords = self._extract_keywords(item)

            # 重新分类（如果原分类不够精确）
            detected = self._detect_category(item)
            if detected and item.category == "CRYPTO_NEWS":
                item.category = detected

        return items

    def _extract_keywords(self, item: AnalyzedItem) -> list[str]:
        """提取关键词"""
        text = f"{item.title} {item.content}".lower()
        keywords = []

        for category, words in CATEGORY_KEYWORDS.items():
            for word in words:
                if word in text:
                    keywords.append(word)

        return list(set(keywords))[:10]

    def _detect_category(self, item: AnalyzedItem) -> str | None:
        """检测最可能的分类"""
        text = f"{item.title} {item.content}".lower()
        scores = {}

        for category, words in CATEGORY_KEYWORDS.items():
            score = sum(1 for w in words if w in text)
            if score > 0:
                scores[category] = score

        if scores:
            return max(scores, key=scores.get)
        return None
