"""RSS 新闻过滤器 — Sourcing + Ranking 两阶段 (v2)

Stage 1 - Sourcing: Source 白名单直通 + 关键词匹配。
Stage 2 - Ranking:  VADER(标题) + FinBERT(摘要) 双轨情感分析
                    + 分歧检测 + 四因子加权评分。

评分公式 (v2):
  sentiment = 0.3 * abs(vader_score) + 0.7 * abs(finbert_score)
  divergence = abs(vader_score - finbert_score)
  divergence_bonus = 1.0 if divergence > 0.5 else 0.0
  score = sentiment*0.30 + relevance*0.25 + macro_bonus*0.25 + divergence_bonus*0.20

sentiment_direction（展示用）:
  raw_sentiment = vader*0.2 + finbert*0.8
  direction = bullish if raw_sentiment > 0 else bearish

阈值:
  score >= 0.6 → URGENT
  score >= 0.3 → IMPORTANT
  score <  0.3 → 跳过

TODO: FinBERT 批量推理 — 当前逐条处理，候选>50条时应批量(pipeline batch_size)
TODO: FinBERT 模型缓存 — 单例模式或模块级变量，避免重复加载 ~400MB 模型
TODO: Novelty score — 与过去 24h 新闻做 cosine similarity，避免重复话题
TODO: Entity extraction — 提取具体实体(BTC, ETH, SEC)用于产出B聚合
TODO: Surprise factor — 实际新闻 vs 市场预期的偏差评分(如CPI高于预期)
TODO: Source authority 动态调整 — 基于历史推送后的价格影响回测
TODO: 时间衰减 — 越新的新闻分数越高
TODO: 多样性惩罚 — 同一话题不连续推送
TODO: Stage 3 LLM 终审层 — 仅 URGENT 级新闻做深度分析
       Crypto 项目/安全新闻 → Claude API
       宏观/Fed 新闻 → Gemini Flash
       输出: {refined_score, confidence, reasoning, time_horizon}
       预计成本 ~$2-5/天，延迟 3-10 秒/条（可异步）
"""
import re

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from transformers import pipeline as hf_pipeline

from .base import AnalyzedItem
from ..pipelines.base import FilterResult
from ..utils.config import load_keywords


# ═══════════════════════════════════════════════
# 可调参数
# ═══════════════════════════════════════════════

# 评分阈值
SCORE_THRESHOLD_URGENT = 0.6
SCORE_THRESHOLD_IMPORTANT = 0.3

# sentiment_direction 展示用权重（vader*0.2 + finbert*0.8 的正负号）
DIRECTION_VADER_WEIGHT = 0.2
DIRECTION_FINBERT_WEIGHT = 0.8

# 综合评分中 sentiment 因子的内部权重（分别取绝对值）
SENTIMENT_VADER_WEIGHT = 0.3
SENTIMENT_FINBERT_WEIGHT = 0.7

# 四因子综合评分权重
SENTIMENT_WEIGHT = 0.30
RELEVANCE_WEIGHT = 0.25
MACRO_WEIGHT = 0.25
DIVERGENCE_WEIGHT = 0.20

# 分歧检测阈值
DIVERGENCE_THRESHOLD = 0.8

# Relevance 归一化分母（匹配 N 个关键词即满分）
RELEVANCE_MAX_KEYWORDS = 10

# 宏观来源列表（命中则 macro_bonus = 1.0）
MACRO_SOURCES: set[str] = {
    "Federal Reserve",
    "SEC Press",
    "BEA_US",
}

# Source 白名单（Crypto 专业媒体，跳过关键词匹配直通 Ranking）
SOURCE_WHITELIST: set[str] = {
    "CoinDesk",
    "Cointelegraph",
    "The_Block",
    "CoinDesk_Markets",
    "Cointelegraph_Main",
    "Decrypt",
    "Blockworks",
    "CryptoSlate",
}


# ═══════════════════════════════════════════════
# Sourcing: 从 config/keywords.yaml 加载 RSS 关键词
# 短关键词(<= 4 字符)使用词边界匹配防止误中
# ═══════════════════════════════════════════════

_kw_config = load_keywords()
RSS_KEYWORDS: dict[str, list[str]] = _kw_config.get("rss_keywords", {})

# 合并所有关键词并预编译正则
_ALL_KEYWORDS: list[tuple[str, str]] = []  # (category, keyword)
for _cat, _words in RSS_KEYWORDS.items():
    for _kw in _words:
        _ALL_KEYWORDS.append((_cat, _kw))

_SHORT_THRESHOLD = 4
_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = []
for _cat, _kw in _ALL_KEYWORDS:
    if len(_kw) <= _SHORT_THRESHOLD:
        _PATTERNS.append((_cat, _kw, re.compile(r"\b" + re.escape(_kw) + r"\b", re.IGNORECASE)))
    else:
        _PATTERNS.append((_cat, _kw, re.compile(re.escape(_kw), re.IGNORECASE)))


class RSSFilter:
    """RSS 新闻过滤器：Sourcing → Ranking (v2)

    Stage 1 (Sourcing): Source 白名单直通 + 关键词匹配。
    Stage 2 (Ranking):  VADER + FinBERT 双轨情感 + 分歧检测 + 四因子评分。
    """

    def __init__(self, use_mock: bool = False) -> None:
        self.vader = SentimentIntensityAnalyzer()
        self._use_mock = use_mock

        if use_mock:
            print("[RSSFilter] 使用 Mock FinBERT（调试模式）")
            self.finbert = None
        else:
            # TODO: 模型缓存 — 考虑单例模式避免多次加载
            self.finbert = hf_pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert",
            )

    def filter(self, items: list[AnalyzedItem]) -> FilterResult:
        """两阶段过滤：Sourcing → Ranking → 阈值"""
        # Stage 1: Sourcing — 白名单直通 + 关键词筛选
        candidates, skipped = self._source(items)

        # Stage 2: Ranking — 双轨情感 + 分歧检测 + 四因子评分
        ranked = self._rank(candidates)

        # 阈值过滤
        passed: list[AnalyzedItem] = []
        for item in ranked:
            if item.score >= SCORE_THRESHOLD_IMPORTANT:
                passed.append(item)
            else:
                skipped.append(item)

        result = FilterResult(passed=passed, skipped=skipped)

        # 日志
        print(f"[RSSFilter] Sourcing: {len(candidates)}/{len(items)} 条相关")
        print(f"[RSSFilter] Ranking: 推送 {len(passed)} 条, 过滤 {len(skipped)} 条")

        if passed:
            for item in passed:
                direction = item.raw_data.get("sentiment_direction", "?")
                arrow = "\u2191" if direction == "bullish" else "\u2193"
                divergent = " [DIVERGENT]" if item.raw_data.get("is_divergent") else ""
                print(f"  {arrow} {item.score:.3f} [{item.category}] {item.title[:60]}{divergent}")

        return result

    # ── Stage 1: Sourcing ──

    def _source(
        self, items: list[AnalyzedItem]
    ) -> tuple[list[AnalyzedItem], list[AnalyzedItem]]:
        """白名单直通 + 关键词相关性筛选，返回 (candidates, skipped)"""
        candidates: list[AnalyzedItem] = []
        skipped: list[AnalyzedItem] = []

        for item in items:
            # 白名单源直通
            if item.source in SOURCE_WHITELIST:
                item.raw_data["sourcing_category"] = "whitelist"
                item.raw_data["matched_keywords"] = []
                item.raw_data["matched_count"] = 0
                item.raw_data["is_macro"] = item.source in MACRO_SOURCES
                candidates.append(item)
                continue

            # 关键词匹配
            matched = self._match_keywords(item)
            if matched:
                item.raw_data["matched_keywords"] = [kw for _, kw in matched]
                item.raw_data["matched_count"] = len(matched)

                # 分类：命中最多的类别
                category_counts: dict[str, int] = {}
                for cat, _ in matched:
                    category_counts[cat] = category_counts.get(cat, 0) + 1
                best_category = max(category_counts, key=category_counts.get)  # type: ignore[arg-type]
                item.raw_data["sourcing_category"] = best_category

                # 宏观标记
                item.raw_data["is_macro"] = (
                    best_category == "macro" or item.source in MACRO_SOURCES
                )

                candidates.append(item)
            else:
                skipped.append(item)

        return candidates, skipped

    def _match_keywords(
        self, item: AnalyzedItem
    ) -> list[tuple[str, str]]:
        """返回所有命中的 (category, keyword) 列表，无命中返回空列表"""
        text = f"{item.title} {item.content}"
        matched: list[tuple[str, str]] = []
        for cat, kw, pattern in _PATTERNS:
            if pattern.search(text):
                matched.append((cat, kw))
        return matched

    # ── Stage 2: Ranking ──

    def _rank(self, candidates: list[AnalyzedItem]) -> list[AnalyzedItem]:
        """双轨情感 + 分歧检测 + 四因子评分，按分数降序排列"""
        for item in candidates:
            # VADER 分析标题（快速，情绪动量）
            vader_score = self.vader.polarity_scores(item.title)["compound"]

            # FinBERT 分析摘要（深度，逻辑校验）
            content = item.content[:512] if item.content else item.title
            finbert_score = self._finbert_score(content)

            # 记录原始分数
            item.raw_data["vader_score"] = round(vader_score, 3)
            item.raw_data["finbert_score"] = round(finbert_score, 3)

            # sentiment_direction（展示用，加权合并的正负号）
            raw_sentiment = (
                vader_score * DIRECTION_VADER_WEIGHT
                + finbert_score * DIRECTION_FINBERT_WEIGHT
            )
            item.raw_data["sentiment"] = round(raw_sentiment, 3)
            item.raw_data["sentiment_direction"] = (
                "bullish" if raw_sentiment > 0 else "bearish"
            )

            # sentiment 因子（分别取绝对值再加权）
            sentiment = (
                SENTIMENT_VADER_WEIGHT * abs(vader_score)
                + SENTIMENT_FINBERT_WEIGHT * abs(finbert_score)
            )

            # 分歧检测
            divergence = abs(vader_score - finbert_score)
            is_divergent = divergence > DIVERGENCE_THRESHOLD
            divergence_bonus = 1.0 if is_divergent else 0.0
            item.raw_data["divergence"] = round(divergence, 3)
            item.raw_data["is_divergent"] = is_divergent

            # Relevance：关键词匹配度归一化
            matched_count = item.raw_data.get("matched_count", 1)
            relevance = min(matched_count / RELEVANCE_MAX_KEYWORDS, 1.0)

            # Macro bonus
            is_macro = item.raw_data.get("is_macro", False)
            macro_bonus = 1.0 if is_macro else 0.0

            # 四因子综合评分
            item.score = round(
                sentiment * SENTIMENT_WEIGHT
                + relevance * RELEVANCE_WEIGHT
                + macro_bonus * MACRO_WEIGHT
                + divergence_bonus * DIVERGENCE_WEIGHT,
                3,
            )

        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates

    def _finbert_score(self, text: str) -> float:
        """FinBERT 推理 → [-1, 1] 分数

        FinBERT 输出: {'label': 'positive/negative/neutral', 'score': 0.0-1.0}
        Mock 模式: 用 VADER 对 content 的结果作为近似值
        """
        if self._use_mock:
            # Mock: 用 VADER 分析 content 作为 FinBERT 的近似
            return self.vader.polarity_scores(text)["compound"]

        try:
            result = self.finbert(text, truncation=True, max_length=512)
            label = result[0]["label"]
            score = result[0]["score"]
            if label == "negative":
                return -score
            elif label == "positive":
                return score
            return 0.0
        except Exception as e:
            # TODO: FinBERT 失败时 fallback 到纯规则评分
            print(f"[RSSFilter] FinBERT 推理失败: {e}")
            return 0.0
