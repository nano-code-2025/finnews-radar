"""Telegram Bot 测试脚本 - 发送消息

用法:
    python scripts/test_telegram.py                    # 发送默认测试消息
    python scripts/test_telegram.py "自定义消息内容"    # 发送自定义消息
"""
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.config import load_env


async def test_telegram_message(text: str = "🔔 测试消息 - FinNews 系统连接成功！") -> bool:
    """测试 Telegram 消息发送

    Args:
        text: 要发送的消息内容

    Returns:
        是否成功
    """
    env = load_env()
    bot_token = env.get("telegram_bot_token", "")
    chat_id = env.get("telegram_chat_id", "")

    if not bot_token or not chat_id:
        print("❌ 错误: TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未配置")
        return False

    api_url = f"https://api.telegram.org/bot{bot_token}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. 测试 Bot 连接
        print("1. 测试 Bot 连接...")
        try:
            resp = await client.get(f"{api_url}/getMe")
            data = resp.json()
            if data.get("ok"):
                bot_info = data["result"]
                print(f"   ✅ Bot 名称: {bot_info['first_name']}")
                print(f"   ✅ Bot 用户名: @{bot_info.get('username', 'N/A')}")
            else:
                print(f"   ❌ 连接失败: {data}")
                return False
        except Exception as e:
            print(f"   ❌ 连接异常: {e}")
            return False

        # 2. 发送测试消息
        print(f"2. 发送消息: {text[:50]}...")
        try:
            resp = await client.post(
                f"{api_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
            )
            data = resp.json()
            if data.get("ok"):
                print("   ✅ 消息发送成功!")
                return True
            else:
                # Markdown 失败则用纯文本重试
                print(f"   ⚠️  Markdown 模式失败，尝试纯文本...")
                resp = await client.post(
                    f"{api_url}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": text,
                    },
                )
                data = resp.json()
                if data.get("ok"):
                    print("   ✅ 消息发送成功 (纯文本模式)!")
                    return True
                else:
                    print(f"   ❌ 发送失败: {data.get('description', 'unknown error')}")
                    return False
        except Exception as e:
            print(f"   ❌ 发送异常: {e}")
            return False


async def main() -> None:
    """主函数"""
    print("=" * 60)
    print("  Telegram Bot 测试")
    print("=" * 60)

    # 支持命令行参数指定消息内容
    text = sys.argv[1] if len(sys.argv) > 1 else "🔔 测试消息 - FinNews 系统连接成功！"

    success = await test_telegram_message(text)

    print("\n" + "=" * 60)
    print(f"  结果: {'✅ PASS' if success else '❌ FAIL'}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

