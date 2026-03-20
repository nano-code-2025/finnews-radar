"""Twitter 推文过滤器 — Sourcing + Ranking + Sentiment 三阶段 (v3)

v3 变更:
  - Sourcing: 关键词从 config/keywords.yaml 加载（Tier1 tag-only + Tier2 过滤 + 黑名单）
  - 账号权重从 config/twitter_accounts.yaml 加载（个人级权重）
  - Ranking / Sentiment 不变

engagement 公式 (v2, 不变):
  engagement = likes*0.5 + replies*13 + reposts*10 + log10(max(views,1))*2
  time_decay = 1.0 / (1.0 + 0.3 * hours_old)
  score = author_weight * engagement * time_decay
"""
import re
from datetime import datetime, timezone
from math import log10

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .base import AnalyzedItem
from ..pipelines.base import FilterResult
from ..utils.config import load_keywords, load_accounts


# ═══════════════════════════════════════════════
# 从 config/keywords.yaml 加载并编译正则
# ═══════════════════════════════════════════════

_kw_config = load_keywords()
_SHORT_THRESHOLD = 4


def _compile_pattern(kw: str) -> re.Pattern[str]:
    """短词用词边界，长词用子串匹配"""
    if len(kw) <= _SHORT_THRESHOLD:
        return re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
    return re.compile(re.escape(kw), re.IGNORECASE)


# Shill 黑名单
SHILL_BLACKLIST: list[str] = _kw_config.get("shill_blacklist", [])
_SHILL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(re.escape(kw), re.IGNORECASE) for kw in SHILL_BLACKLIST
]

# Tier 1 事件词: (keyword, category, pattern)
TIER1_EVENT_KEYWORDS: dict[str, list[str]] = _kw_config.get("tier1_event_keywords", {})
_TIER1_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = []
for _cat, _kws in TIER1_EVENT_KEYWORDS.items():
    for _kw in _kws:
        _TIER1_PATTERNS.append((_kw, _cat, _compile_pattern(_kw)))

# Tier 2 主题词: (keyword, category, pattern)
TIER2_TOPIC_KEYWORDS: dict[str, list[str]] = _kw_config.get("tier2_topic_keywords", {})
_TIER2_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = []
for _cat, _kws in TIER2_TOPIC_KEYWORDS.items():
    for _kw in _kws:
        _TIER2_PATTERNS.append((_kw, _cat, _compile_pattern(_kw)))


# ═══════════════════════════════════════════════
# Ranking 常量 (v2, 不变)
# ═══════════════════════════════════════════════

LIKE_WEIGHT = 0.5
REPLY_WEIGHT = 13
REPOST_WEIGHT = 10
VIEWS_LOG_WEIGHT = 2
TIME_DECAY_FACTOR = 0.3
DEFAULT_WEIGHT = 1

SENTIMENT_URGENT_THRESHOLD = 0.5
SENTIMENT_IMPORTANT_THRESHOLD = 0.2


class TwitterFilter:
    """Twitter 推文过滤器：Sourcing → Ranking → Sentiment (v3)"""

    def __init__(self, top_n: int = 0) -> None:
        self.top_n = top_n
        self.vader = SentimentIntensityAnalyzer()
        self._handle_to_weight: dict[str, float] = {}
        self._handle_to_group: dict[str, str] = {}
        self._load_weights()

    def _load_weights(self) -> None:
        """从 twitter_accounts.yaml 构建 handle → weight/group 映射

        支持两种格式:
        - 个人权重: accounts: [{handle: weight}, ...]
        - 组级权重: weight: N, accounts: [handle, ...]
        """
        config = load_accounts()
        for group_name, group_cfg in config.items():
            group_weight = group_cfg.get("weight", DEFAULT_WEIGHT)
            for entry in group_cfg.get("accounts", []):
                if isinstance(entry, dict):
                    # 个人权重格式: {handle: weight}
                    for handle, weight in entry.items():
                        self._handle_to_weight[handle.lower()] = float(weight)
                        self._handle_to_group[handle.lower()] = group_name
                else:
                    # 组级权重格式: handle (string)
                    self._handle_to_weight[entry.lower()] = float(group_weight)
                    self._handle_to_group[entry.lower()] = group_name

    def filter(self, items: list[AnalyzedItem]) -> FilterResult:
        """三阶段过滤：Sourcing → Ranking → Sentiment"""
        candidates, skipped = self._source(items)
        ranked = self._rank(candidates)

        if self.top_n > 0:
            passed = ranked[:self.top_n]
            skipped.extend(ranked[self.top_n:])
        else:
            passed = ranked

        self._analyze_sentiment(passed)
        result = FilterResult(passed=passed, skipped=skipped)

        # 日志
        print(f"[TwitterFilter] Sourcing: {len(candidates)}/{len(items)} 条相关")
        print(f"[TwitterFilter] Ranking: 推送 {len(passed)} 条, 过滤 {len(skipped)} 条")

        for item in passed:
            handle = item.raw_data.get("author_handle", "?")
            score = item.score
            direction = item.raw_data.get("sentiment_direction", "")
            level = item.raw_data.get("sentiment_level", "")
            arrow = "\u2191" if direction == "bullish" else "\u2193"
            level_tag = f" [{level}]" if level != "NORMAL" else ""
            event_tags = item.raw_data.get("event_tags", [])
            tag_str = f" {event_tags}" if event_tags else ""
            content_preview = item.content.replace("\n", " ")[:50]
            print(f"  {arrow} #{score:,.0f} @{handle}: {content_preview}{level_tag}{tag_str}")

        for item in skipped:
            handle = item.raw_data.get("author_handle", "?")
            skip_reason = item.raw_data.get("skip_reason", "")
            reason_tag = f" ({skip_reason})" if skip_reason else ""
            content_preview = item.content.replace("\n", " ")[:60]
            print(f"  \u2717 @{handle}: {content_preview}{reason_tag}")

        return result

    # ── Stage 1: Sourcing (v3) ──

    def _source(self, items: list[AnalyzedItem]) -> tuple[list[AnalyzedItem], list[AnalyzedItem]]:
        """黑名单 → Tier 1 事件标记 → Tier 2 关键词过滤"""
        candidates: list[AnalyzedItem] = []
        skipped: list[AnalyzedItem] = []

        for item in items:
            text = item.content

            if self._match_shill(text):
                item.raw_data["skip_reason"] = "shill"
                skipped.append(item)
                continue

            event_tags = self._match_tier1(text)
            if event_tags:
                item.raw_data["event_tags"] = event_tags

            matched_kw, matched_cat = self._match_tier2(text)
            if matched_kw:
                item.raw_data["matched_keyword"] = matched_kw
                item.raw_data["sourcing_category"] = matched_cat
                candidates.append(item)
            elif event_tags:
                item.raw_data["matched_keyword"] = event_tags[0]
                item.raw_data["sourcing_category"] = "event"
                candidates.append(item)
            else:
                item.raw_data["skip_reason"] = "no_keyword"
                skipped.append(item)

        return candidates, skipped

    def _match_shill(self, text: str) -> bool:
        for pattern in _SHILL_PATTERNS:
            if pattern.search(text):
                return True
        return False

    def _match_tier1(self, text: str) -> list[str]:
        tags: list[str] = []
        for kw, _cat, pattern in _TIER1_PATTERNS:
            if pattern.search(text):
                tags.append(kw)
        return tags

    def _match_tier2(self, text: str) -> tuple[str | None, str | None]:
        for kw, cat, pattern in _TIER2_PATTERNS:
            if pattern.search(text):
                return kw, cat
        return None, None

    # ── Stage 2: Ranking (不变) ──

    def _rank(self, candidates: list[AnalyzedItem]) -> list[AnalyzedItem]:
        for item in candidates:
            item.score = self._compute_score(item)
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates

    def _compute_score(self, item: AnalyzedItem) -> float:
        raw = item.raw_data
        likes = raw.get("likes", 0) or 0
        replies = raw.get("replies", 0) or 0
        reposts = raw.get("reposts", 0) or 0
        views = raw.get("views", 0) or 0

        handle = (raw.get("author_handle") or "").lower()
        weight = self._handle_to_weight.get(handle, DEFAULT_WEIGHT)

        raw["group"] = self._handle_to_group.get(handle, "unknown")
        raw["author_weight"] = weight

        engagement = (
            likes * LIKE_WEIGHT
            + replies * REPLY_WEIGHT
            + reposts * REPOST_WEIGHT
            + log10(max(views, 1)) * VIEWS_LOG_WEIGHT
        )

        time_decay = 1.0
        if item.published_at:
            now = datetime.now(timezone.utc)
            published = item.published_at
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            hours_old = (now - published).total_seconds() / 3600
            time_decay = 1.0 / (1.0 + TIME_DECAY_FACTOR * max(hours_old, 0))

        raw["time_decay"] = round(time_decay, 3)
        raw["engagement"] = round(engagement, 1)

        return weight * engagement * time_decay

    # ── Stage 3: Sentiment (不变) ──

    def _analyze_sentiment(self, items: list[AnalyzedItem]) -> None:
        for item in items:
            vader_score = self.vader.polarity_scores(item.content)["compound"]
            item.raw_data["vader_score"] = round(vader_score, 3)

            if vader_score > 0:
                item.raw_data["sentiment_direction"] = "bullish"
            elif vader_score < 0:
                item.raw_data["sentiment_direction"] = "bearish"
            else:
                item.raw_data["sentiment_direction"] = "neutral"

            abs_vader = abs(vader_score)
            if abs_vader >= SENTIMENT_URGENT_THRESHOLD:
                item.raw_data["sentiment_level"] = "URGENT"
            elif abs_vader >= SENTIMENT_IMPORTANT_THRESHOLD:
                item.raw_data["sentiment_level"] = "IMPORTANT"
            else:
                item.raw_data["sentiment_level"] = "NORMAL"

    def get_account_info(self, handle: str) -> dict:
        handle_lower = handle.lower()
        return {
            "handle": handle,
            "group": self._handle_to_group.get(handle_lower, "unknown"),
            "weight": self._handle_to_weight.get(handle_lower, DEFAULT_WEIGHT),
        }
