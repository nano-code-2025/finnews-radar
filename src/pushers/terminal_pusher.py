"""Terminal 输出推送器"""
from datetime import datetime

from ..analyzers.base import AnalyzedItem


# ANSI 颜色
RED = "\033[91m"
ORANGE = "\033[93m"
GREEN = "\033[92m"
RESET = "\033[0m"
BOLD = "\033[1m"


class TerminalPusher:
    """终端输出推送器"""

    def push(self, items: list[AnalyzedItem]):
        """输出到终端"""
        if not items:
            print("[Terminal] 无新消息")
            return

        print(f"\n{'='*60}")
        print(f"{BOLD}新闻监控报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
        print(f"{'='*60}")

        # 按评分排序
        sorted_items = sorted(items, key=lambda x: x.score, reverse=True)

        for item in sorted_items:
            self._print_item(item)

        print(f"{'='*60}")
        print(f"共 {len(items)} 条新闻")

    def _print_item(self, item: AnalyzedItem):
        """打印单条新闻"""
        # 根据紧急程度选择颜色
        if item.urgency == "URGENT":
            color = RED
            icon = "[!!!]"
        elif item.urgency == "IMPORTANT":
            color = ORANGE
            icon = "[!!]"
        else:
            color = GREEN
            icon = "[i]"

        print(f"\n{color}{icon} [{item.score}] {item.title}{RESET}")
        print(f"    来源: {item.source} | 分类: {item.category}")
        print(f"    时间: {item.published_at.strftime('%H:%M:%S')}")

        if item.keywords:
            print(f"    关键词: {', '.join(item.keywords[:5])}")

        if item.grok_analysis:
            print(f"    {BOLD}Grok分析:{RESET} {item.grok_analysis[:100]}...")

        if item.url:
            print(f"    链接: {item.url}")
