"""24h 聚合报告生成器

从 features.db 读取当日数据，按 topic 聚合输出：
  - sentiment 均值
  - engagement 总和
  - rationality 均值
  - length 均值
  - HHI 话题集中度
  - LLM 增强率
  - Grok AI 解读（web_search 获取宏观背景 + 当日 insights）

输出: 本地 JSON + CSV + 日志 + 可选 Telegram 推送
TODO: Notion 输出接口
"""
import asyncio
import csv
import json
import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI
from zoneinfo import ZoneInfo

from ..utils.config import load_env
from ..utils.features_db import FeaturesDatabase

# ── AI 解读触发阈值（任一条件满足即触发，节省 token）───────────
# 修改此处可调节触发灵敏度；--force-ai 可绕过所有条件
AI_TRIGGER = {
    "sentiment_extreme": 0.35,   # |avg_sentiment| >= 此值 → 情绪极端
    "fear_elevated": 0.50,       # avg_fear >= 此值 → 恐慌升温
    "fomo_elevated": 0.50,       # avg_fomo >= 此值 → FOMO 升温
    "high_fear_min_count": 3,    # high_fear_count >= 此值 → 多条高恐慌帖
    "high_fomo_min_count": 3,    # high_fomo_count >= 此值 → 多条高 FOMO 帖
    "hhi_concentrated": 0.60,    # HHI >= 此值 → 话题垄断（注意力集中风险）
    "consensus_extreme": 0.80,   # bullish 或 bearish 占比 >= 此值 → 一边倒共识
    "topic_fear_threshold": 0.50,  # 某话题 avg_fear >= 此值且 count >= 3 → 板块恐慌
    "bear_sentiment_threshold": -0.30,  # 某话题 avg_sentiment <= 此值 → 板块看空
}
# ─────────────────────────────────────────────────────────────

# 日报日界线：美盘日（美国东部时间）
REPORT_TZ_NAME = "America/New_York"
REPORT_TZ = ZoneInfo(REPORT_TZ_NAME)



class DailyReportGenerator:
    """24h 聚合报告"""

    def __init__(self, db_path: str = "data/features.db") -> None:
        self.features_db = FeaturesDatabase(db_path)

    def generate(self, date_str: str | None = None) -> dict[str, Any]:
        """生成某日的聚合报告

        Args:
            date_str: 日期字符串 (YYYY-MM-DD)，默认“美盘日”今天（America/New_York）

        Returns:
            完整报告 dict
        """
        if not date_str:
            # 默认输出“已结束的美盘日”（ET 昨天完整 00:00-24:00）
            now_et = datetime.now(REPORT_TZ)
            date_str = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")

        # 从 features.db 获取当日所有特征（按美盘日，转换为 UTC 查询范围）
        start_utc, end_utc = self._report_window_utc(date_str)
        features = self.features_db.get_features_by_time_range(start_utc, end_utc)

        if not features:
            return {
                "date": date_str,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_posts": 0,
                "topics": [],
                "hhi": 0.0,
                "llm_enhanced_rate": 0.0,
                "summary": {},
            }

        # 按 topic 聚合
        topic_groups: dict[str, list[dict]] = {}
        for f in features:
            topic = f.get("topic") or "other"
            topic_groups.setdefault(topic, []).append(f)

        total = len(features)

        # 计算各 topic 统计
        topic_stats: list[dict[str, Any]] = []
        for topic, group in sorted(topic_groups.items(), key=lambda x: -len(x[1])):
            count = len(group)

            # 优先用 LLM 值，否则用规则值
            sentiments = []
            for f in group:
                s = f.get("llm_sentiment") if f.get("llm_enhanced") else None
                if s is None:
                    s = f.get("sentiment", 0.0)
                sentiments.append(s or 0.0)

            rationalities = []
            for f in group:
                r = f.get("llm_rationality") if f.get("llm_enhanced") else None
                if r is None:
                    r = f.get("rationality", 0.5)
                rationalities.append(r or 0.5)

            fears = []
            for f in group:
                v = f.get("llm_fear_score") if f.get("llm_enhanced") else None
                if v is None:
                    v = f.get("fear_score", 0.0)
                fears.append(v or 0.0)

            fomos = []
            for f in group:
                v = f.get("llm_fomo_score") if f.get("llm_enhanced") else None
                if v is None:
                    v = f.get("fomo_score", 0.0)
                fomos.append(v or 0.0)

            engagements = [f.get("engagement_score", 0.0) or 0.0 for f in group]
            lengths = [f.get("length", 0) or 0 for f in group]

            stat = {
                "topic": topic,
                "count": count,
                "share": round(count / total, 3),
                "avg_sentiment": round(sum(sentiments) / count, 3),
                "avg_fear": round(sum(fears) / count, 3),
                "avg_fomo": round(sum(fomos) / count, 3),
                "total_engagement": round(sum(engagements), 1),
                "avg_rationality": round(sum(rationalities) / count, 3),
                "avg_length": round(sum(lengths) / count, 0),
            }
            topic_stats.append(stat)

        # HHI (Herfindahl-Hirschman Index) — 话题集中度
        hhi = sum(s["share"] ** 2 for s in topic_stats)

        # LLM 增强率
        llm_count = sum(1 for f in features if f.get("llm_enhanced"))

        # 全局汇总
        all_sentiments = []
        for f in features:
            s = f.get("llm_sentiment") if f.get("llm_enhanced") else None
            if s is None:
                s = f.get("sentiment", 0.0)
            all_sentiments.append(s or 0.0)

        all_engagements = [f.get("engagement_score", 0.0) or 0.0 for f in features]

        all_fears = []
        for f in features:
            v = f.get("llm_fear_score") if f.get("llm_enhanced") else None
            if v is None:
                v = f.get("fear_score", 0.0)
            all_fears.append(v or 0.0)

        all_fomos = []
        for f in features:
            v = f.get("llm_fomo_score") if f.get("llm_enhanced") else None
            if v is None:
                v = f.get("fomo_score", 0.0)
            all_fomos.append(v or 0.0)

        summary = {
            "avg_sentiment": round(sum(all_sentiments) / total, 3),
            "avg_fear": round(sum(all_fears) / total, 3),
            "avg_fomo": round(sum(all_fomos) / total, 3),
            "high_fear_count": sum(1 for v in all_fears if v > 0.7),
            "high_fomo_count": sum(1 for v in all_fomos if v > 0.7),
            "total_engagement": round(sum(all_engagements), 1),
            "top_topic": topic_stats[0]["topic"] if topic_stats else "N/A",
            "bullish_count": sum(1 for s in all_sentiments if s > 0.05),
            "bearish_count": sum(1 for s in all_sentiments if s < -0.05),
            "neutral_count": sum(1 for s in all_sentiments if -0.05 <= s <= 0.05),
        }

        return {
            "date": date_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_posts": total,
            "hhi": round(hhi, 4),
            "llm_enhanced_rate": round(llm_count / total, 3) if total > 0 else 0.0,
            "topics": topic_stats,
            "summary": summary,
            "_raw_features": features,  # 逐条明细，供 CSV 导出
        }

    def _report_window_utc(self, date_str: str) -> tuple[str, str]:
        """将美盘日(ET)日期转换为 UTC 时间范围 [start, end)"""
        report_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_local = datetime.combine(report_date, time.min, tzinfo=REPORT_TZ)
        end_local = start_local + timedelta(days=1)
        start_utc = start_local.astimezone(timezone.utc).isoformat()
        end_utc = end_local.astimezone(timezone.utc).isoformat()
        return start_utc, end_utc

    # ── 异常检测：决定是否调用 AI ──

    def should_generate_ai(self, report: dict[str, Any]) -> tuple[bool, list[str]]:
        """基于多维度异常检测，判断是否值得调用 Grok AI 解读

        检测逻辑（任一条件触发即返回 True）：
          1. 情绪极端（avg_sentiment 偏离）
          2. 恐慌/FOMO 升温
          3. 高恐慌/高 FOMO 帖子数量
          4. HHI 话题垄断
          5. 一边倒共识（bullish 或 bearish 占比极端）
          6. 某话题板块整体恐慌或看空

        Returns:
            (should_trigger, reasons) — reasons 列出所有触发原因
        """
        anomalies = self._detect_anomalies(report)
        reasons_zh = [a["reason_zh"] for a in anomalies]
        return len(reasons_zh) > 0, reasons_zh

    def _detect_anomalies(self, report: dict[str, Any]) -> list[dict[str, Any]]:
        """Detect AI-trigger anomalies and return structured evidence (zh/en)."""
        T = AI_TRIGGER
        summary = report.get("summary", {})
        topics: list[dict[str, Any]] = report.get("topics", [])
        features: list[dict[str, Any]] = report.get("_raw_features", [])
        total = int(report.get("total_posts", 0) or 0)

        anomalies: list[dict[str, Any]] = []
        if total == 0:
            return anomalies

        def add(code: str, reason_zh: str, reason_en: str) -> None:
            anomalies.append(
                {
                    "code": code,
                    "reason_zh": reason_zh,
                    "reason_en": reason_en,
                }
            )

        # 1) Extreme avg sentiment
        avg_sent_abs = abs(float(summary.get("avg_sentiment", 0) or 0))
        if avg_sent_abs >= float(T["sentiment_extreme"]):
            add(
                "sentiment_extreme",
                f"情绪极端 |sent|={avg_sent_abs:.3f} >= {T['sentiment_extreme']}",
                f"Extreme market sentiment: |sentiment|={avg_sent_abs:.3f} >= {T['sentiment_extreme']}",
            )

        # 2) Elevated fear / fomo (global averages)
        avg_fear = float(summary.get("avg_fear", 0) or 0)
        avg_fomo = float(summary.get("avg_fomo", 0) or 0)
        if avg_fear >= float(T["fear_elevated"]):
            add(
                "fear_elevated",
                f"恐慌升温 fear={avg_fear:.2f} >= {T['fear_elevated']}",
                f"Fear elevated: fear={avg_fear:.2f} >= {T['fear_elevated']}",
            )
        if avg_fomo >= float(T["fomo_elevated"]):
            add(
                "fomo_elevated",
                f"FOMO 升温 fomo={avg_fomo:.2f} >= {T['fomo_elevated']}",
                f"FOMO elevated: fomo={avg_fomo:.2f} >= {T['fomo_elevated']}",
            )

        # 3) High fear / high fomo counts
        high_fear_cnt = int(summary.get("high_fear_count", 0) or 0)
        high_fomo_cnt = int(summary.get("high_fomo_count", 0) or 0)
        if high_fear_cnt >= int(T["high_fear_min_count"]):
            add(
                "high_fear_count",
                f"高恐慌帖 {high_fear_cnt} 条 >= {T['high_fear_min_count']}",
                f"Many high-fear posts: {high_fear_cnt} >= {T['high_fear_min_count']}",
            )
        if high_fomo_cnt >= int(T["high_fomo_min_count"]):
            add(
                "high_fomo_count",
                f"高 FOMO 帖 {high_fomo_cnt} 条 >= {T['high_fomo_min_count']}",
                f"Many high-FOMO posts: {high_fomo_cnt} >= {T['high_fomo_min_count']}",
            )

        # 4) Concentration (HHI)
        hhi = float(report.get("hhi", 0) or 0)
        if hhi >= float(T["hhi_concentrated"]):
            add(
                "hhi_concentrated",
                f"话题垄断 HHI={hhi:.3f} >= {T['hhi_concentrated']}",
                f"High topic concentration (HHI): {hhi:.3f} >= {T['hhi_concentrated']}",
            )

        # 5) Extreme consensus (bull/bear ratio)
        bullish = int(summary.get("bullish_count", 0) or 0)
        bearish = int(summary.get("bearish_count", 0) or 0)
        if total > 0:
            bull_ratio = bullish / total
            bear_ratio = bearish / total
            if bull_ratio >= float(T["consensus_extreme"]):
                add(
                    "consensus_bull",
                    f"极端看多共识 bull={bull_ratio:.0%} >= {T['consensus_extreme']:.0%}",
                    f"Overcrowded bullish consensus: bull={bull_ratio:.0%} >= {T['consensus_extreme']:.0%}",
                )
            if bear_ratio >= float(T["consensus_extreme"]):
                add(
                    "consensus_bear",
                    f"极端看空共识 bear={bear_ratio:.0%} >= {T['consensus_extreme']:.0%}",
                    f"Overcrowded bearish consensus: bear={bear_ratio:.0%} >= {T['consensus_extreme']:.0%}",
                )

        # 6) Topic-level fear / bearish sentiment
        for ts in topics:
            topic = str(ts.get("topic", "unknown"))
            n = int(ts.get("count", 0) or 0)
            if n < 3:
                continue
            tfear = float(ts.get("avg_fear", 0) or 0)
            tsent = float(ts.get("avg_sentiment", 0) or 0)
            if tfear >= float(T["topic_fear_threshold"]):
                add(
                    "topic_fear",
                    f"板块恐慌 {topic}(n={n}) fear={tfear:.2f} >= {T['topic_fear_threshold']}",
                    f"Topic fear spike: {topic} (n={n}) fear={tfear:.2f} >= {T['topic_fear_threshold']}",
                )
            if tsent <= float(T["bear_sentiment_threshold"]):
                add(
                    "topic_bearish",
                    f"板块看空 {topic}(n={n}) sent={tsent:+.2f} <= {T['bear_sentiment_threshold']}",
                    f"Topic turned bearish: {topic} (n={n}) sentiment={tsent:+.2f} <= {T['bear_sentiment_threshold']}",
                )

        return anomalies

    def _select_top_topics(
        self,
        topics: list[dict[str, Any]],
        key_fn: Any,
        k: int = 2,
        min_count: int = 3,
    ) -> list[dict[str, Any]]:
        """Select Top-K topics by a key function (filters tiny samples)."""
        filtered = [t for t in topics if int(t.get("count", 0) or 0) >= min_count]
        return sorted(filtered, key=key_fn, reverse=True)[:k]

    def _panic_risk_score(
        self,
        hhi: float,
        fear_peak: float,
        fomo_peak: float,
        neg_sent_peak: float,
        panic_engage_share: float,
    ) -> int:
        """Heuristic 0-100 score for 'panic risk' (interpretability > precision)."""
        # Weights tuned for readability (not a trading model).
        score = 0.0
        score += min(1.0, hhi / 0.7) * 25.0
        score += min(1.0, fear_peak / 1.0) * 35.0
        score += min(1.0, fomo_peak / 1.0) * 10.0
        score += min(1.0, abs(neg_sent_peak) / 1.0) * 20.0
        score += min(1.0, panic_engage_share / 0.5) * 10.0
        return int(max(0.0, min(100.0, round(score))))

    def save_local(self, report: dict[str, Any], output_dir: str = "data/reports") -> str:
        """保存报告到本地 JSON 文件（不含 _raw_features）

        Returns:
            文件路径
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        date_str = report.get("date", "unknown")
        file_path = out_dir / f"daily_report_{date_str}.json"

        # 排除内部字段，避免 JSON 过大
        clean = {k: v for k, v in report.items() if not k.startswith("_")}

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)

        print(f"[DailyReport] 已保存: {file_path}")
        return str(file_path)

    def _get_csv_columns(self) -> list[str]:
        """从数据库 schema 自动获取列顺序"""
        with sqlite3.connect(self.features_db.db_path) as conn:
            return [r[1] for r in conn.execute("PRAGMA table_info(post_features)")]

    def save_csv(self, report: dict[str, Any], output_dir: str = "data/reports") -> str:
        """导出逐条帖子明细为 CSV（格式与 features_inspect.py 一致）

        Returns:
            文件路径
        """
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        date_str = report.get("date", "unknown")
        file_path = out_dir / f"daily_report_{date_str}.csv"

        features = report.get("_raw_features", [])
        if not features:
            print("[DailyReport] CSV: 无数据")
            return str(file_path)

        # 自动获取列顺序
        columns = self._get_csv_columns()

        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for feat in features:
                row = {}
                for col in columns:
                    val = feat.get(col)
                    # event_tags 列表序列化为 JSON 字符串
                    if col == "event_tags" and isinstance(val, list):
                        val = json.dumps(val, ensure_ascii=False)
                    row[col] = val
                writer.writerow(row)

        print(f"[DailyReport] CSV 已保存: {file_path} ({len(features)} 条)")
        return str(file_path)

    # ── AI 解读 (Grok web_search + x_search) ──

    def _extract_signal_context(self, report: dict[str, Any]) -> str:
        """从 _raw_features 提取高价值信号，构建 prompt 上下文（纯数据驱动）"""
        features = report.get("_raw_features", [])
        if not features:
            return ""

        def _fear_val(f: dict[str, Any]) -> float:
            return float(f.get("llm_fear_score") or f.get("fear_score") or 0)

        lines: list[str] = []

        # ── 1. High Fear signals — fear_score >= 0.5, top 5 ──
        high_fear = [f for f in features if _fear_val(f) >= 0.5]
        if high_fear:
            high_fear.sort(key=_fear_val, reverse=True)
            lines.append(f"High Fear signals ({len(high_fear)}):")
            for f in high_fear[:5]:
                summary = f.get("llm_summary") or ""
                fear = _fear_val(f)
                lines.append(
                    f"  @{f.get('author_handle', '?')}: {summary} "
                    f"(fear={fear:.2f}, engage={f.get('engagement_score', 0):,.0f})"
                )

        # ── 2. Macro/Regulation signals — topic in (macro, regulation, etf), top 5 ──
        macro_topics = {"macro", "regulation", "etf"}
        macro_posts = [
            f for f in features
            if f.get("llm_topic") in macro_topics
            or f.get("topic") in macro_topics
        ]
        if macro_posts:
            macro_posts.sort(key=lambda x: x.get("engagement_score", 0), reverse=True)
            lines.append("Macro/Regulation signals:")
            for f in macro_posts[:5]:
                summary = f.get("llm_summary") or ""
                sent = float(f.get("llm_sentiment") or f.get("sentiment") or 0)
                lines.append(
                    f"  @{f.get('author_handle', '?')} [{f.get('llm_topic') or f.get('topic')}]: "
                    f"{summary} (sent={sent:+.2f})"
                )

        # ── 3. Top Engagement signals — by engagement_score, top 5 ──
        top_engage = sorted(features, key=lambda f: f.get("engagement_score", 0), reverse=True)[:5]
        lines.append("Top engagement signals:")
        for f in top_engage:
            summary = f.get("llm_summary") or ""
            lines.append(
                f"  @{f.get('author_handle', '?')} [{f.get('llm_topic') or f.get('topic')}]: "
                f"{summary} (engage={f.get('engagement_score', 0):,.0f})"
            )

        return "\n".join(lines)

    async def generate_ai_insights(self, report: dict[str, Any]) -> str:
        """调用 Grok web_search + x_search，生成宏观背景 + 当日 insights

        双工具搜索：web_search 获取宏观行情，x_search 获取 X 上最新动态。
        传入分组信号上下文，让 AI 结合实时数据做深度解读。
        失败时静默返回空字符串（不影响报告发送）。
        """
        env = load_env()
        api_key = env.get("xai_api_key", "")
        if not api_key:
            print("[DailyReport] XAI_API_KEY 未配置，跳过 AI 解读")
            return ""

        summary = report.get("summary", {})
        topics: list[dict[str, Any]] = report.get("topics", [])
        top_topics = ", ".join(
            f"{t['topic']}({t['count']}, sent={t['avg_sentiment']:+.2f}, "
            f"fear={t.get('avg_fear', 0):.2f}, fomo={t.get('avg_fomo', 0):.2f})"
            for t in topics[:5]
        )

        # 从原始数据提取分组信号
        signal_context = self._extract_signal_context(report)

        # ── anomaly-driven context (Top2 extremes, aligned with trigger logic) ──
        anomalies = self._detect_anomalies(report)
        why_triggered = "\n".join(
            f"{i+1}. {a['reason_en']}" for i, a in enumerate(anomalies[:6])
        ) or "1. Manual trigger / unspecified anomaly."

        top_fear = self._select_top_topics(topics, key_fn=lambda t: float(t.get("avg_fear", 0) or 0), k=2)
        top_fomo = self._select_top_topics(topics, key_fn=lambda t: float(t.get("avg_fomo", 0) or 0), k=2)
        top_sent_abs = self._select_top_topics(
            topics,
            key_fn=lambda t: abs(float(t.get("avg_sentiment", 0) or 0)),
            k=2,
        )
        top_engage = self._select_top_topics(
            topics,
            key_fn=lambda t: float(t.get("total_engagement", 0) or 0),
            k=2,
            min_count=1,
        )

        def fmt_topic(t: dict[str, Any]) -> str:
            return (
                f"{t.get('topic','unknown')} "
                f"(n={int(t.get('count',0) or 0)}, "
                f"sent={float(t.get('avg_sentiment',0) or 0):+.2f}, "
                f"fear={float(t.get('avg_fear',0) or 0):.2f}, "
                f"fomo={float(t.get('avg_fomo',0) or 0):.2f}, "
                f"engage={float(t.get('total_engagement',0) or 0):,.0f})"
            )

        fear_peak = max([float(t.get("avg_fear", 0) or 0) for t in top_fear] + [0.0])
        fomo_peak = max([float(t.get("avg_fomo", 0) or 0) for t in top_fomo] + [0.0])
        neg_sent_peak = min([float(t.get("avg_sentiment", 0) or 0) for t in top_sent_abs] + [0.0])
        total_eng = float(summary.get("total_engagement", 0) or 0)

        # Engagement heuristic (rule #3): high engagement + (fear high OR sentiment bearish) => panic candidate
        panic_candidates: list[str] = []
        panic_engage = 0.0
        for t in top_engage:
            tfear = float(t.get("avg_fear", 0) or 0)
            tsent = float(t.get("avg_sentiment", 0) or 0)
            teng = float(t.get("total_engagement", 0) or 0)
            if tfear >= float(AI_TRIGGER["topic_fear_threshold"]) or tsent <= float(AI_TRIGGER["bear_sentiment_threshold"]):
                panic_candidates.append(fmt_topic(t))
                panic_engage += teng
        panic_engage_share = (panic_engage / total_eng) if total_eng > 0 else 0.0

        hhi = float(report.get("hhi", 0) or 0)
        panic_score = self._panic_risk_score(
            hhi=hhi,
            fear_peak=fear_peak,
            fomo_peak=fomo_peak,
            neg_sent_peak=neg_sent_peak,
            panic_engage_share=panic_engage_share,
        )

        prompt = (
            "You are my private crypto intelligence analyst (mid-level). "
            "I run an automated KOL monitoring system (X/Twitter + RSS). "
            "I have already seen the metrics; do NOT restate them. "
            "Your value is: real-time prices, causality, and blind-spot coverage.\n\n"
            f"=== Anomaly Brief ({report['date']}) ===\n"
            "Why triggered (you must explicitly address each point):\n"
            f"{why_triggered}\n\n"
            "Extreme Topics (Top2; interpret drivers, not averages):\n"
            f"Fear spikes: {', '.join(fmt_topic(t) for t in top_fear) or 'N/A'}\n"
            f"FOMO spikes: {', '.join(fmt_topic(t) for t in top_fomo) or 'N/A'}\n"
            f"Sentiment extremes: {', '.join(fmt_topic(t) for t in top_sent_abs) or 'N/A'}\n"
            f"Engagement-panic candidates (high engage + fear↑ or sentiment↓): "
            f"{'; '.join(panic_candidates) or 'None'}\n"
            f"HHI (topic concentration): {hhi:.3f}\n"
            f"Heuristic Panic Risk Score: {panic_score}/100 "
            "(explain what is driving this score today).\n\n"
            "Monitoring group signals (KOL samples):\n"
            f"{signal_context}\n\n"
            "Top topics overview (for reference only):\n"
            f"{top_topics}\n\n"
            "=== Task ===\n"
            "Use both web and X search. Write an ENGLISH brief with exactly 4 sections:\n"
            "1) WHY TRIGGERED: one tight paragraph linking the anomaly evidence to likely causes.\n"
            "2) LIVE MARKET: BTC and ETH current price + 24h change; mention macro catalyst only if it clearly explains the move.\n"
            "3) SIGNAL VERIFICATION: 2-3 bullets; each bullet MUST include a specific project/event name and the latest status.\n"
            "4) TODAY CALL + RISK: one sentence regime label (panic / greed / chop / wait-and-see) + one actionable risk note.\n\n"
            "Formatting rules: plain text only. No markdown symbols (no *, #, -, bullets). "
            "Use numbered lines and line breaks. Max 400 words. Be decisive."
        )

        client = AsyncOpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
        try:
            response = await asyncio.wait_for(
                client.responses.create(
                    model="grok-4-fast",
                    tools=[
                        {"type": "web_search"},
                        {"type": "x_search"},
                    ],
                    input=[{"role": "user", "content": prompt}],
                ),
                timeout=90.0,
            )
            text = getattr(response, "output_text", "") or ""
            if not text:
                # fallback: 从 output 提取
                for out in getattr(response, "output", []) or []:
                    if getattr(out, "type", None) != "message":
                        continue
                    for c in getattr(out, "content", []) or []:
                        t = getattr(c, "text", None)
                        if isinstance(t, str) and t.strip():
                            text = t.strip()
                            break
                    if text:
                        break
            print(f"[DailyReport] AI 解读完成 ({len(text)} 字)")
            return text.strip()
        except asyncio.TimeoutError:
            print("[DailyReport] AI 解读超时 (>90s)，跳过")
            return ""
        except Exception as e:
            print(f"[DailyReport] AI 解读失败: {e}")
            return ""
        finally:
            await client.close()

    # ── Telegram 格式化 ──

    def format_telegram(self, report: dict[str, Any], ai_insights: str = "") -> str:
        """格式化报告为 Telegram 消息（含个性化问候 + AI 解读）"""
        env = load_env()
        user_name = env.get("daily_report_user_name", "")
        date = report["date"]
        total = report["total_posts"]
        hhi = report["hhi"]
        llm_rate = report["llm_enhanced_rate"]
        summary = report.get("summary", {})

        # ── 个性化问候 ──
        hour = datetime.now().hour
        if hour < 12:
            greeting = f"\u2615 Good morning, {user_name}"
        elif hour < 18:
            greeting = f"\u2600\uFE0F Good afternoon, {user_name}"
        else:
            greeting = f"\U0001F319 Good evening, {user_name}"

        lines = [
            greeting,
            f"*Here is your 24h Daily Report* | {date}",
            "",
        ]

        # ── 概览 ──
        lines.append(f"\U0001F4DD {total} posts | LLM {llm_rate:.0%}")
        lines.append("")

        # ── 情感 ──
        avg_sent = summary.get("avg_sentiment", 0)
        bullish = summary.get("bullish_count", 0)
        bearish = summary.get("bearish_count", 0)
        neutral = summary.get("neutral_count", 0)

        if avg_sent > 0.05:
            mood = "\U0001F4C8 Bullish"
        elif avg_sent < -0.05:
            mood = "\U0001F4C9 Bearish"
        else:
            mood = "\u2696\uFE0F Neutral"

        lines.append(f"*Sentiment:* {mood} ({avg_sent:+.3f})")
        lines.append(f"\U0001F4C8 {bullish}  \U0001F4C9 {bearish}  \u2696\uFE0F {neutral}")
        lines.append("")

        # ── Fear / FOMO ──
        avg_fear = summary.get("avg_fear", 0)
        avg_fomo = summary.get("avg_fomo", 0)
        high_fear = summary.get("high_fear_count", 0)
        high_fomo = summary.get("high_fomo_count", 0)
        lines.append(f"\u26A0\uFE0F Fear: {avg_fear:.2f} ({high_fear} high)")
        lines.append(f"\U0001F525 FOMO: {avg_fomo:.2f} ({high_fomo} high)")
        lines.append("")

        # ── HHI ──
        if hhi > 0.5:
            hhi_desc = "Concentrated"
        elif hhi > 0.25:
            hhi_desc = "Moderate"
        else:
            hhi_desc = "Diversified"
        lines.append(f"\U0001F4CA HHI {hhi:.3f} \u2014 {hhi_desc}")

        # ── 各 topic ──
        lines.append("")
        lines.append("\u2500\u2500\u2500 *Topics* \u2500\u2500\u2500")

        for ts in report.get("topics", []):
            topic = ts["topic"].upper()
            count = ts["count"]
            share = ts["share"]
            sent = ts["avg_sentiment"]
            fear = ts.get("avg_fear", 0)
            fomo = ts.get("avg_fomo", 0)
            eng = ts["total_engagement"]

            sent_icon = "\U0001F4C8" if sent > 0.05 else ("\U0001F4C9" if sent < -0.05 else "\u2696\uFE0F")

            lines.append("")
            lines.append(f"{sent_icon} *{topic}* \u2014 {count} ({share:.0%})")
            lines.append(f"Senti {sent:+.3f} | Fear {fear:.2f} | FOMO {fomo:.2f}")
            lines.append(f"Engage {eng:,.0f}")

        # ── 总互动 ──
        total_eng = summary.get("total_engagement", 0)
        lines.append("")
        lines.append(f"Total Engage: {total_eng:,.0f}")

        # ── AI 解读 ──
        if ai_insights:
            lines.append("")
            lines.append("\u2500\u2500\u2500 *Private Crypto Intelligence Analyst (Middle-level)* \u2500\u2500\u2500")
            lines.append("")
            lines.append(ai_insights)
            lines.append("")
            lines.append("_Triggered by anomaly detection only._")

        return "\n".join(lines)

    # ── 日志持久化 ──

    def save_log(
        self,
        report: dict[str, Any],
        ai_insights: str = "",
        log_dir: str = "data/logs",
    ) -> str:
        """追加保存日报摘要到日志文件（便于回溯历史趋势）

        Returns:
            日志文件路径
        """
        out_dir = Path(log_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / "daily_report.log"

        date = report.get("date", "unknown")
        summary = report.get("summary", {})

        entry_lines = [
            f"\n{'=' * 60}",
            f"[{date}] generated_at={report.get('generated_at', '')}",
            f"Posts: {report.get('total_posts', 0)} | "
            f"HHI: {report.get('hhi', 0):.4f} | "
            f"LLM: {report.get('llm_enhanced_rate', 0):.0%}",
            f"Sentiment: {summary.get('avg_sentiment', 0):+.3f} | "
            f"Fear: {summary.get('avg_fear', 0):.2f} | "
            f"FOMO: {summary.get('avg_fomo', 0):.2f}",
            f"Bull/Bear/Neutral: "
            f"{summary.get('bullish_count', 0)}/"
            f"{summary.get('bearish_count', 0)}/"
            f"{summary.get('neutral_count', 0)}",
            f"High Fear: {summary.get('high_fear_count', 0)} | "
            f"High FOMO: {summary.get('high_fomo_count', 0)} | "
            f"Total Engage: {summary.get('total_engagement', 0):,.0f}",
            "",
        ]

        # Topic 明细
        for ts in report.get("topics", []):
            entry_lines.append(
                f"  {ts['topic']:<14} {ts['count']:>3} ({ts['share']:>4.0%}) "
                f"sent={ts['avg_sentiment']:+.3f} "
                f"fear={ts.get('avg_fear', 0):.2f} "
                f"fomo={ts.get('avg_fomo', 0):.2f} "
                f"engage={ts['total_engagement']:,.0f}"
            )

        if ai_insights:
            entry_lines.append("")
            entry_lines.append("AI Insights:")
            entry_lines.append(ai_insights)

        entry_lines.append(f"{'=' * 60}")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(entry_lines) + "\n")

        print(f"[DailyReport] 日志已追加: {log_path}")
        return str(log_path)

    # ── Telegram 推送 ──

    async def push_telegram(self, report: dict[str, Any], ai_insights: str = "") -> bool:
        """推送报告到 Telegram

        优先使用 TELEGRAM_DAILY_CHAT_ID，未配置则 fallback 到 TELEGRAM_CHAT_ID。

        Returns:
            是否成功
        """
        env = load_env()
        bot_token = env.get("telegram_bot_token", "")
        # 优先用 daily 专属频道，缺省回退到通用频道
        chat_id = env.get("telegram_daily_chat_id", "") or env.get("telegram_chat_id", "")

        if not bot_token or not chat_id:
            print("[DailyReport] Telegram 未配置，跳过推送")
            return False

        text = self.format_telegram(report, ai_insights)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            data = resp.json()

            if not data.get("ok"):
                # Markdown 失败则用纯文本重试
                plain = text.replace("*", "").replace("_", "").replace("`", "")
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": plain,
                        "disable_web_page_preview": True,
                    },
                )

        print("[DailyReport] Telegram 推送完成")
        return True

    async def push_csv_telegram(self, csv_path: str) -> bool:
        """推送 CSV 文件到 Telegram（作为文档附件）

        Args:
            csv_path: CSV 文件路径

        Returns:
            是否成功
        """
        env = load_env()
        bot_token = env.get("telegram_bot_token", "")
        # 优先用 daily 专属频道，缺省回退到通用频道
        chat_id = env.get("telegram_daily_chat_id", "") or env.get("telegram_chat_id", "")

        if not bot_token or not chat_id:
            print("[DailyReport] Telegram 未配置，跳过 CSV 推送")
            return False

        csv_file = Path(csv_path)
        if not csv_file.exists():
            print(f"[DailyReport] CSV 文件不存在: {csv_path}")
            return False

        date_str = csv_file.stem.replace("daily_report_", "")
        caption = f"📊 24h Daily Report CSV | {date_str}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                with open(csv_file, "rb") as f:
                    files = {"document": (csv_file.name, f, "text/csv")}
                    data = {"chat_id": chat_id, "caption": caption}
                    resp = await client.post(
                        f"https://api.telegram.org/bot{bot_token}/sendDocument",
                        data=data,
                        files=files,
                    )
                    result = resp.json()
                    if result.get("ok"):
                        print(f"[DailyReport] CSV 文件推送成功: {csv_file.name}")
                        return True
                    else:
                        print(f"[DailyReport] CSV 推送失败: {result.get('description', 'unknown error')}")
                        return False
        except Exception as e:
            print(f"[DailyReport] CSV 推送异常: {e}")
            return False
