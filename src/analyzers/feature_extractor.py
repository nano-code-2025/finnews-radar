"""特征提取器 — 规则引擎 baseline + LLM 增强 (v3)

Feature Extraction 是纯计算层，不做过滤。
规则值和 LLM 值并存，不覆盖，供下游对比和选用。

LLM buffer 模式:
  - enable_llm=True 时调用 Grok fast（默认开启）
  - 逐条调用，返回结构化 JSON
  - 失败时 fallback 到规则 baseline
  - 两套值同时写入 features.db
"""
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .base import AnalyzedItem
from ..utils.config import load_keywords


# ═══════════════════════════════════════════════
# Topic 统一枚举 — 从 keywords.yaml 动态加载
# ═══════════════════════════════════════════════

_kw_config = load_keywords()
TOPIC_ENUM: list[str] = _kw_config.get("topics", ["other"])

_KEYWORD_TO_TOPIC: dict[str, str] = {}

# Tier 1 映射 — 从 YAML 读取
_tier1_map = _kw_config.get("tier1_topic_mapping", {})
for _cat, _kws in _kw_config.get("tier1_event_keywords", {}).items():
    _topic = _tier1_map.get(_cat, "other")
    for _kw in _kws:
        _KEYWORD_TO_TOPIC[_kw.lower()] = _topic

# Tier 2 映射 — 从 YAML 读取
_tier2_map = _kw_config.get("tier2_topic_mapping", {})
for _cat, _kws in _kw_config.get("tier2_topic_keywords", {}).items():
    _topic = _tier2_map.get(_cat, "other")
    for _kw in _kws:
        _KEYWORD_TO_TOPIC[_kw.lower()] = _topic


# ═══════════════════════════════════════════════
# Rationality 启发式规则
# ═══════════════════════════════════════════════

_IRRATIONAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(lfg|wagmi|ngmi|gm|to the moon|moon|lambo)\b", re.IGNORECASE),
    re.compile(r"\b(guaranteed|easy money|free money|get rich)\b", re.IGNORECASE),
    re.compile(r"🚀{2,}|🔥{2,}|💰{2,}|💎{2,}"),
]
_NUMBER_PATTERN = re.compile(r"\$?\d[\d,.]*[KMBkmb%]?")
_ANALYTICAL_PATTERN = re.compile(
    r"\b(chart|data|analysis|report|research|study|metric|ratio|indicator)\b",
    re.IGNORECASE,
)

# Fear/FOMO 规则引擎词表
_FEAR_PATTERN = re.compile(
    r"\b(crash|dump|capitulation|panic|rekt|liquidat\w*|rug\s*pull|collapse|plunge|sell[\s-]?off|"
    r"wipeout|bankrupt|insolvent|meltdown|contagion|bank\s*run)\b",
    re.IGNORECASE,
)
_FOMO_PATTERN = re.compile(
    r"\b(moon|lambo|lfg|wagmi|don'?t\s+miss|last\s+chance|easy\s+money|guaranteed|"
    r"100x|1000x|to\s+the\s+moon|generational\s+wealth|life[\s-]?changing)\b",
    re.IGNORECASE,
)
_ROCKET_DIAMOND_RE = re.compile(r"[🚀💎]")


# ═══════════════════════════════════════════════
# Grok fast LLM prompt
# ═══════════════════════════════════════════════

_LLM_TOPIC_LIST = ", ".join(t for t in TOPIC_ENUM if t != "other") + ", other"

_LLM_PROMPT = """Analyze this crypto/finance tweet. Return ONLY valid JSON, no markdown:
{{{{
  "sentiment": <float -1.0 to 1.0, bearish to bullish>,
  "fear_score": <float 0.0 to 1.0, 0=calm, 1=extreme panic/capitulation>,
  "fomo_score": <float 0.0 to 1.0, 0=rational, 1=extreme greed/urgency>,
  "topic": "<one of: {topics}>",
  "rationality": <float 0.0 to 1.0, 0=pure hype, 1=data-driven analysis>,
  "summary": "<one line Chinese summary, max 50 chars>"
}}}}

Tweet by @{{author}}:
{{text}}""".format(topics=_LLM_TOPIC_LIST)


class FeatureExtractor:
    """特征提取器：规则引擎 baseline + LLM 可选增强

    规则值和 LLM 值并存，不覆盖。
    """

    def __init__(self, enable_llm: bool = True) -> None:
        self.enable_llm = enable_llm
        self.vader = SentimentIntensityAnalyzer()
        self._grok_client: OpenAI | None = None

        if enable_llm:
            api_key = os.getenv("XAI_API_KEY", "")
            if api_key:
                self._grok_client = OpenAI(
                    api_key=api_key,
                    base_url="https://api.x.ai/v1",
                )
            else:
                print("[FeatureExtractor] XAI_API_KEY 未配置，LLM 增强已禁用")
                self.enable_llm = False

    def extract_features(self, item: AnalyzedItem) -> dict[str, Any]:
        """提取单条推文的全部特征（规则 + LLM 并存）"""
        raw = item.raw_data
        text = item.content

        # 规则 baseline（始终计算）
        sentiment = self._compute_sentiment(text)
        topic = self._infer_topic(raw)
        rationality = self._compute_rationality(text, raw.get("external_urls"))
        fear_score = self._compute_fear_score(text)
        fomo_score = self._compute_fomo_score(text)
        length = len(text)
        event_tags = raw.get("event_tags", [])

        features: dict[str, Any] = {
            "tweet_id": raw.get("tweet_id", item.id),
            "author_handle": raw.get("author_handle", ""),
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "extracted_at": datetime.now(timezone.utc).isoformat(),

            # 规则 baseline
            "topic": topic,
            "sentiment": sentiment,
            "fear_score": fear_score,
            "fomo_score": fomo_score,
            "engagement": {
                "likes": raw.get("likes", 0),
                "replies": raw.get("replies", 0),
                "reposts": raw.get("reposts", 0),
                "views": raw.get("views", 0),
                "score": raw.get("engagement", 0.0),
            },
            "rationality": rationality,
            "length": length,
            "event_tags": event_tags,
            "llm_enhanced": False,

            # LLM 值（默认 None，有 LLM 时填充）
            "llm_sentiment": None,
            "llm_topic": None,
            "llm_rationality": None,
            "llm_summary": None,
            "llm_fear_score": None,
            "llm_fomo_score": None,

            # 元数据
            "source_tier": "L1",
            "author_weight": raw.get("author_weight", 1.0),

            # engagement 明细（展平，便于 DB 存储）
            "likes": raw.get("likes", 0),
            "replies": raw.get("replies", 0),
            "reposts": raw.get("reposts", 0),
            "views": raw.get("views", 0),
            "engagement_score": raw.get("engagement", 0.0),
        }

        # LLM 增强
        if self.enable_llm and self._grok_client:
            llm_result = self._llm_enhance(text, raw.get("author_handle", ""))
            if llm_result:
                features["llm_sentiment"] = llm_result.get("sentiment")
                features["llm_topic"] = llm_result.get("topic")
                features["llm_rationality"] = llm_result.get("rationality")
                features["llm_summary"] = llm_result.get("summary")
                features["llm_fear_score"] = llm_result.get("fear_score")
                features["llm_fomo_score"] = llm_result.get("fomo_score")
                features["llm_enhanced"] = True

        return features

    def extract_batch(self, items: list[AnalyzedItem]) -> list[dict[str, Any]]:
        """批量提取特征"""
        results = []
        for i, item in enumerate(items):
            features = self.extract_features(item)
            if self.enable_llm:
                handle = item.raw_data.get("author_handle", "?")
                llm_tag = "LLM" if features["llm_enhanced"] else "RULE"
                print(f"  [{llm_tag}] {i+1}/{len(items)} @{handle}")
            results.append(features)
        return results

    # ── 规则引擎 ──

    def _compute_sentiment(self, text: str) -> float:
        return self.vader.polarity_scores(text)["compound"]

    def _infer_topic(self, raw_data: dict[str, Any]) -> str:
        event_tags = raw_data.get("event_tags", [])
        if event_tags:
            first_tag = event_tags[0]
            if first_tag in _KEYWORD_TO_TOPIC:
                return _KEYWORD_TO_TOPIC[first_tag]

        matched_kw = raw_data.get("matched_keyword", "")
        if matched_kw:
            return _KEYWORD_TO_TOPIC.get(matched_kw.lower(), "other")

        category = raw_data.get("sourcing_category", "")
        if category in TOPIC_ENUM:
            return category

        return "other"

    def _compute_fear_score(self, text: str) -> float:
        """恐慌程度评分 (0=calm, 1=extreme panic)"""
        score = 0.0
        text_len = max(len(text), 1)

        # 恐慌词匹配
        fear_hits = len(_FEAR_PATTERN.findall(text))
        score += min(fear_hits * 0.2, 0.6)

        # 大写比例 >30% → 恐慌信号
        caps_ratio = sum(1 for c in text if c.isupper()) / text_len
        if caps_ratio > 0.3:
            score += 0.15

        # 感叹号密度 >5%
        exclaim_ratio = text.count("!") / text_len
        if exclaim_ratio > 0.05:
            score += 0.1

        # 分析性语言 → 降低恐慌
        if _ANALYTICAL_PATTERN.search(text):
            score -= 0.1

        return max(0.0, min(1.0, round(score, 2)))

    def _compute_fomo_score(self, text: str) -> float:
        """贪婪/急迫评分 (0=rational, 1=extreme FOMO)"""
        score = 0.0
        text_len = max(len(text), 1)

        # FOMO 词匹配
        fomo_hits = len(_FOMO_PATTERN.findall(text))
        score += min(fomo_hits * 0.2, 0.6)

        # 火箭/钻石 emoji 密度
        emoji_count = len(_ROCKET_DIAMOND_RE.findall(text))
        if emoji_count >= 3:
            score += 0.15

        # 复用 _IRRATIONAL_PATTERNS
        for pattern in _IRRATIONAL_PATTERNS:
            if pattern.search(text):
                score += 0.1
                break

        # 数据引用 → 降低 FOMO
        if _NUMBER_PATTERN.search(text):
            score -= 0.1

        return max(0.0, min(1.0, round(score, 2)))

    def _compute_rationality(self, text: str, urls: list[str] | None = None) -> float:
        score = 0.5

        if _NUMBER_PATTERN.search(text):
            score += 0.15
        if urls and len(urls) > 0:
            score += 0.15
        if len(text) > 150:
            score += 0.10
        if _ANALYTICAL_PATTERN.search(text):
            score += 0.10

        text_len = max(len(text), 1)
        caps_ratio = sum(1 for c in text if c.isupper()) / text_len
        if caps_ratio > 0.3:
            score -= 0.15

        exclaim_ratio = text.count("!") / text_len
        if exclaim_ratio > 0.05:
            score -= 0.10

        for pattern in _IRRATIONAL_PATTERNS:
            if pattern.search(text):
                score -= 0.15
                break

        return max(0.0, min(1.0, round(score, 2)))

    # ── LLM 增强 (Grok fast) ──

    def _llm_enhance(self, text: str, author: str) -> dict[str, Any] | None:
        """调用 Grok fast 获取结构化特征，失败返回 None"""
        if not self._grok_client:
            return None

        prompt = _LLM_PROMPT.format(author=author, text=text[:500])

        try:
            response = self._grok_client.chat.completions.create(
                model="grok-3-fast",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )

            content = response.choices[0].message.content or ""
            # 清理可能的 markdown 包裹
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1]
                content = content.rsplit("```", 1)[0]
            content = content.strip()

            result = json.loads(content)

            # 校验字段
            sentiment = float(result.get("sentiment", 0))
            sentiment = max(-1.0, min(1.0, sentiment))

            fear = float(result.get("fear_score", 0))
            fear = max(0.0, min(1.0, fear))

            fomo = float(result.get("fomo_score", 0))
            fomo = max(0.0, min(1.0, fomo))

            topic = result.get("topic", "other")
            if topic not in TOPIC_ENUM:
                # 兼容旧 "hack" → "security"
                topic = "security" if topic == "hack" else "other"

            rationality = float(result.get("rationality", 0.5))
            rationality = max(0.0, min(1.0, rationality))

            summary = str(result.get("summary", ""))[:100]

            return {
                "sentiment": round(sentiment, 3),
                "fear_score": round(fear, 2),
                "fomo_score": round(fomo, 2),
                "topic": topic,
                "rationality": round(rationality, 2),
                "summary": summary,
            }

        except Exception as e:
            print(f"  [LLM] Grok 调用失败: {type(e).__name__}: {e}")
            return None
