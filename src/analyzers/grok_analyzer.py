"""Grok 深度分析器"""
from openai import AsyncOpenAI

from .base import AnalyzedItem
from ..utils.config import load_env


ANALYSIS_PROMPT = """分析以下金融/加密新闻的市场影响：

标题：{title}
内容：{content}
来源：{source}
分类：{category}

请简要分析（100字内）：
1. 市场影响预测（利好/利空/中性）
2. 影响范围（BTC/特定币种/整体市场）
3. 建议关注点
"""


class GrokAnalyzer:
    """Grok 深度分析器，用于高分事件"""

    def __init__(self):
        env = load_env()
        api_key = env.get("xai_api_key")
        if not api_key:
            raise ValueError("XAI_API_KEY 未配置")
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.x.ai/v1",
        )
        self.threshold = 9.0  # 只分析评分>=9的事件

    async def process(self, items: list[AnalyzedItem]) -> list[AnalyzedItem]:
        """对高分事件进行深度分析"""
        for item in items:
            if item.score >= self.threshold:
                try:
                    item.grok_analysis = await self._analyze(item)
                except Exception as e:
                    print(f"[Grok] 分析失败: {e}")
                    item.grok_analysis = ""

        return items

    async def _analyze(self, item: AnalyzedItem) -> str:
        """调用 Grok 进行深度分析"""
        prompt = ANALYSIS_PROMPT.format(
            title=item.title,
            content=item.content[:500],  # 限制长度
            source=item.source,
            category=item.category,
        )

        response = await self.client.chat.completions.create(
            model="grok-4-fast",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
        )

        return response.choices[0].message.content or ""

    async def close(self):
        """释放资源"""
        await self.client.close()
