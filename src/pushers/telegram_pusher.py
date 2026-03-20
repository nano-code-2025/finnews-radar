"""Telegram 推送器 - 按重要度分级展示"""
from datetime import datetime

import httpx

from ..analyzers.base import AnalyzedItem
from ..utils.config import load_env


# Sentiment/score legends (disabled by default, kept as reference)
_SENTIMENT_LEGEND = (
    "━━━━━━━━━━━━━━━━\n"
    "\U0001F4D6 Sentiment Notes\n"
    "Title senti \u2014 headline sentiment intensity (market momentum)\n"
    "Abstract senti \u2014 summary financial semantics (deep logic)\n"
    "Overall \u2014 vader*0.2 + finbert*0.8, direction is reference only\n"
    "Recommendation \u2014 delivery priority, independent of direction"
)

# Twitter legend (disabled by default, kept as reference)
_TWITTER_LEGEND = (
    "━━━━━━━━━━━━━━━━\n"
    "\U0001F4D6 Twitter Scoring\n"
    "Engagement = likes*0.5 + replies*13 + reposts*10 + log10(views)*2\n"
    "Time decay = 1 / (1 + 0.3 * hours_old)\n"
    "Score = author_weight * engagement * time_decay\n"
    "Direction: LLM sentiment > 0 \u2192 Bullish, < 0 \u2192 Bearish (fallback to VADER)\n"
    "Levels: |LLM_sentiment|>=0.5 \u2192 \u26A1URGENT, |LLM_sentiment|>=0.2 \u2192 \u2757IMPORTANT, else NORMAL"
)


class TelegramPusher:
    """Telegram 消息推送器 - 按 URGENT / IMPORTANT / VIRAL 分区"""

    def __init__(self, show_legend: bool = False) -> None:
        env = load_env()
        self.bot_token = env["telegram_bot_token"]
        self.chat_id = env["telegram_chat_id"]
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.client = httpx.AsyncClient(timeout=30.0)
        self.show_legend = show_legend

    async def push(
        self,
        items: list[AnalyzedItem],
        total_collected: int = 0,
        total_skipped: int = 0,
    ) -> None:
        """推送汇总报告到 Telegram

        Args:
            items: 通过过滤的条目
            total_collected: 采集总数（用于统计展示）
            total_skipped: 被过滤掉的数量
        """
        if not self.bot_token or not self.chat_id:
            print("[Telegram] 未配置，跳过推送")
            return

        if not items:
            print("[Telegram] 无消息，跳过推送")
            return

        try:
            messages = self._build_messages(items, total_collected, total_skipped)
            for msg in messages:
                await self._send_single(msg)
            print(f"[Telegram] 推送成功，共 {len(items)} 条，分 {len(messages)} 段")
        except Exception as e:
            print(f"[Telegram] 推送失败: {e}")

    def _build_messages(
        self,
        items: list[AnalyzedItem],
        total_collected: int,
        total_skipped: int,
    ) -> list[str]:
        """构建按等级分区的消息列表"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 分离 RSS 和 Twitter
        rss_items = [i for i in items if i.raw_data.get("source_type") == "rss"]
        twitter_items = [i for i in items if i.raw_data.get("source_type") == "twitter"]

        # RSS 按评分分级（0-1 范围）
        urgent = [i for i in rss_items if i.score >= 0.6]
        important = [i for i in rss_items if 0.3 <= i.score < 0.6]

        # Twitter 全部作为 VIRAL
        viral = twitter_items

        all_parts: list[str] = []

        # 头部统计
        stats_line = f"Total {len(items)} items"
        if total_skipped > 0:
            stats_line += f" | Filtered {total_skipped}"

        header = [
            f"\U0001F4CA *Intel Summary* | {now}",
            "",
            stats_line,
            "",
        ]
        all_parts.append("\n".join(header))

        # 🔴 Urgent
        if urgent:
            section = "━━━ \U0001F534 *URGENT* ━━━\n"
            entries = [self._format_rss(i, "[!!!]") for i in urgent]
            all_parts.append(section + "\n\n".join(entries))

        # 🟠 Important
        if important:
            section = "━━━ \U0001F7E0 *IMPORTANT* ━━━\n"
            entries = [self._format_rss(i, "[!!]") for i in important]
            all_parts.append(section + "\n\n".join(entries))

        # 🔥 Viral tweets
        if viral:
            section = "━━━ \U0001F525 *VIRAL* ━━━\n"
            entries = [self._format_twitter(i) for i in viral]
            all_parts.append(section + "\n\n".join(entries))

        # Empty fallback
        if len(all_parts) == 1:
            all_parts.append("No important messages this round")

        # 可选图例说明
        if self.show_legend and rss_items:
            all_parts.append(_SENTIMENT_LEGEND)
        if self.show_legend and twitter_items:
            all_parts.append(_TWITTER_LEGEND)

        return self._split_into_messages(all_parts)

    def _format_rss(self, item: AnalyzedItem, tag: str) -> str:
        """格式化 RSS 条目（含情感明细）"""
        lines = []
        raw = item.raw_data

        title = item.title[:80] if item.title else "(No title)"
        lines.append(f"{tag} *{title}*")

        # Score line: source | category | recommendation
        category = raw.get("sourcing_category", item.category)
        score_line = f"Source: {item.source} | {category.upper()} | Recommendation: {item.score}"
        lines.append(score_line)

        if item.url:
            lines.append(f"\U0001F517 {item.url}")

        if item.published_at:
            lines.append(f"\U0001F4C5 {item.published_at.strftime('%m-%d %H:%M')}")

        # Sentiment detail line
        vader = raw.get("vader_score", 0)
        finbert = raw.get("finbert_score", 0)
        sentiment = raw.get("sentiment", 0)
        direction = raw.get("sentiment_direction", "")
        if direction == "bullish":
            direction_icon = "\U0001F4C8 Bullish"
        elif direction == "bearish":
            direction_icon = "\U0001F4C9 Bearish"
        else:
            direction_icon = ""

        sentiment_line = (
            f"\U0001F4CA Title senti: {vader:+.2f} | "
            f"Abstract senti: {finbert:+.2f} | Overall: {sentiment:+.2f}"
        )
        if raw.get("is_divergent"):
            sentiment_line += " | \u26A0\uFE0F Divergent"
        if direction_icon:
            sentiment_line += f" {direction_icon}"
        lines.append(sentiment_line)

        return "\n".join(lines)

    def _format_twitter(self, item: AnalyzedItem) -> str:
        """格式化 Twitter 条目（LLM 增强参数优先展示）"""
        lines = []
        raw = item.raw_data
        feat = raw.get("features", {})

        # ── 1. 头部：@handle (group) ──
        handle = raw.get("author_handle", "")
        group = raw.get("group", "")
        header_parts = []
        if handle:
            header_parts.append(f"*@{handle}*")
        if group:
            header_parts.append(f"({group})")
        if header_parts:
            lines.append(" ".join(header_parts))

        # ── 2. 正文摘要 ──
        content = item.content[:200].replace("\n", " ") if item.content else ""
        if len(item.content) > 200:
            content += "..."
        lines.append(content)

        # ── 3. LLM Summary（最有价值，优先展示）──
        llm_summary = feat.get("llm_summary") if feat else None
        if llm_summary:
            lines.append(f"\U0001F4DD {llm_summary}")

        # ── 4. LLM 核心指标（sentiment / topic / rationality）──
        if feat and feat.get("llm_enhanced"):
            llm_sent = feat.get("llm_sentiment")
            llm_topic = feat.get("llm_topic", "")
            llm_rat = feat.get("llm_rationality")

            # Sentiment 方向图标
            if llm_sent is not None:
                if llm_sent > 0.05:
                    s_icon = "\U0001F4C8"
                elif llm_sent < -0.05:
                    s_icon = "\U0001F4C9"
                else:
                    s_icon = "\u2696\uFE0F"
                sent_text = f"{s_icon} Senti: {llm_sent:+.2f}"
            else:
                sent_text = ""

            parts = [p for p in [
                sent_text,
                f"Topic: {llm_topic}" if llm_topic else "",
                f"Rational: {llm_rat:.2f}" if llm_rat is not None else "",
            ] if p]
            if parts:
                lines.append(" | ".join(parts))

        # ── 5. Fear / FOMO（显示实际分值 + 高位警告）──
        if feat:
            fear = feat.get("llm_fear_score") if feat.get("llm_enhanced") else None
            if fear is None:
                fear = feat.get("fear_score") or 0
            fomo = feat.get("llm_fomo_score") if feat.get("llm_enhanced") else None
            if fomo is None:
                fomo = feat.get("fomo_score") or 0

            fear_str = f"Fear: {fear:.2f}"
            if fear > 0.7:
                fear_str += " \u26A0\uFE0F"
            fomo_str = f"FOMO: {fomo:.2f}"
            if fomo > 0.7:
                fomo_str += " \U0001F525"
            lines.append(f"{fear_str} | {fomo_str}")

        # ── 6. Engagement 统计 ──
        likes = raw.get("likes", 0) or 0
        reposts = raw.get("reposts", 0) or 0
        replies = raw.get("replies", 0) or 0
        views = raw.get("views", 0) or 0
        engagement = raw.get("engagement", 0)
        lines.append(
            f"\U0001F4CA Engage: {engagement:,.1f} | "
            f"\U0001F44D {likes:,} | \U0001F504 {reposts:,} | "
            f"\U0001F4AC {replies:,} | \U0001F441 {views:,}"
        )

        # ── 7. VADER fallback（仅无 LLM 时显示）──
        if not (feat and feat.get("llm_enhanced")):
            vader = raw.get("vader_score")
            if vader is not None:
                direction = raw.get("sentiment_direction", "")
                if direction == "bullish":
                    direction_text = "\U0001F4C8 Bullish"
                elif direction == "bearish":
                    direction_text = "\U0001F4C9 Bearish"
                else:
                    direction_text = "Neutral"

                level = raw.get("sentiment_level", "")
                if level == "URGENT":
                    level_text = " \u26A1URGENT"
                elif level == "IMPORTANT":
                    level_text = " \u2757IMPORTANT"
                else:
                    level_text = ""

                lines.append(f"VADER: {vader:+.2f} {direction_text}{level_text}")

        # ── 8. URL + 时间 ──
        if item.url:
            lines.append(f"\U0001F517 {item.url}")
        if item.published_at:
            lines.append(f"\U0001F4C5 {item.published_at.strftime('%m-%d %H:%M')}")

        return "\n".join(lines)

    def _split_into_messages(self, parts: list[str]) -> list[str]:
        """将多个部分合并并按 Telegram 限制分割"""
        max_len = 4000
        messages: list[str] = []
        current = ""

        for part in parts:
            if len(part) > max_len:
                if current:
                    messages.append(current)
                    current = ""
                sub_parts = self._split_long_text(part, max_len)
                messages.extend(sub_parts)
            elif len(current) + len(part) + 2 > max_len:
                if current:
                    messages.append(current)
                current = part
            else:
                if current:
                    current += "\n\n" + part
                else:
                    current = part

        if current:
            messages.append(current)

        if len(messages) > 1:
            for i in range(len(messages)):
                messages[i] = f"({i+1}/{len(messages)})\n\n" + messages[i]

        return messages

    def _split_long_text(self, text: str, max_len: int) -> list[str]:
        """按行分割超长文本"""
        lines = text.split("\n")
        parts: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in lines:
            if current_len + len(line) + 1 > max_len:
                if current:
                    parts.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line) + 1

        if current:
            parts.append("\n".join(current))

        return parts

    async def _send_single(self, text: str) -> None:
        """发送单条消息"""
        resp = await self.client.post(
            f"{self.api_url}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
        )
        data = resp.json()
        if not data.get("ok"):
            plain_text = text.replace("*", "").replace("_", "").replace("`", "")
            await self.client.post(
                f"{self.api_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": plain_text,
                    "disable_web_page_preview": True,
                },
            )

    async def send_alert(self, text: str) -> None:
        """发送纯文本告警消息（不受 items 为空的限制）"""
        if not self.bot_token or not self.chat_id:
            return
        try:
            await self._send_single(text)
        except Exception as e:
            print(f"[Telegram] 告警发送失败: {e}")

    async def close(self) -> None:
        await self.client.aclose()
