"""API 测试脚本"""
import asyncio
import os
from dotenv import load_dotenv
import httpx
from openai import AsyncOpenAI

load_dotenv()


async def test_telegram():
    """测试 Telegram 推送"""
    print("\n=== 测试 Telegram ===")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("错误: TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未配置")
        return False

    api_url = f"https://api.telegram.org/bot{bot_token}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. 测试 getMe
        print("1. 测试 Bot 连接...")
        try:
            resp = await client.get(f"{api_url}/getMe")
            data = resp.json()
            if data.get("ok"):
                print(f"   Bot 名称: {data['result']['first_name']}")
                print(f"   Bot 用户名: @{data['result']['username']}")
            else:
                print(f"   失败: {data}")
                return False
        except Exception as e:
            print(f"   连接失败: {e}")
            return False

        # 2. 发送测试消息
        print("2. 发送测试消息...")
        try:
            resp = await client.post(
                f"{api_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "🔔 测试消息 - FinNews 系统连接成功！",
                },
            )
            data = resp.json()
            if data.get("ok"):
                print("   发送成功!")
                return True
            else:
                print(f"   发送失败: {data}")
                return False
        except Exception as e:
            print(f"   发送失败: {e}")
            return False


async def test_grok():
    """测试 Grok API"""
    print("\n=== 测试 Grok API ===")

    api_key = os.getenv("XAI_API_KEY")

    if not api_key:
        print("错误: XAI_API_KEY 未配置")
        return False

    print(f"API Key: {api_key[:10]}...{api_key[-5:]}")

    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://api.x.ai/v1",
    )

    # 1. 测试基本对话
    print("1. 测试基本对话...")
    try:
        response = await client.chat.completions.create(
            model="grok-3-fast",
            messages=[{"role": "user", "content": "Say 'Hello' in one word."}],
            max_tokens=10,
        )
        print(f"   响应: {response.choices[0].message.content}")
    except Exception as e:
        print(f"   失败: {e}")
        return False

    # 2. 测试 x_search
    print("2. 测试 x_search (Twitter 搜索)...")
    try:
        response = await client.responses.create(
            model="grok-4-fast",
            tools=[{
                "type": "x_search",
                "x_search": {
                    "allowed_x_handles": ["VitalikButerin"],
                }
            }],
            input="获取 @VitalikButerin 的最新一条推文内容",
        )

        # 解析响应
        for output in response.output:
            if output.type == "message":
                content = output.content[0].text if output.content else ""
                print(f"   响应: {content[:200]}...")
                break

        return True
    except Exception as e:
        print(f"   失败: {e}")
        return False


async def main():
    print("=" * 50)
    print("API 测试脚本")
    print("=" * 50)

    tele_ok = await test_telegram()
    grok_ok = await test_grok()

    print("\n" + "=" * 50)
    print("Result:")
    print(f"  Telegram: {'PASS' if tele_ok else 'FAIL'}")
    print(f"  Grok API: {'PASS' if grok_ok else 'FAIL'}")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
