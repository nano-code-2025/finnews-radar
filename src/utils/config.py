"""配置加载工具"""
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def load_env() -> dict[str, str]:
    """加载环境变量"""
    load_dotenv()
    return {
        "xai_api_key": os.getenv("XAI_API_KEY", ""),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "telegram_daily_chat_id": os.getenv("TELEGRAM_DAILY_CHAT_ID", ""),
        "daily_report_user_name": os.getenv("DAILY_REPORT_USER_NAME", ""),
        "twitter_tweets_per_account": os.getenv("TWITTER_TWEETS_PER_ACCOUNT", ""),
        "twitter_lookback_days": os.getenv("TWITTER_LOOKBACK_DAYS", ""),
        "twitter_max_concurrent_requests": os.getenv("TWITTER_MAX_CONCURRENT_REQUESTS", ""),
    }


def load_config(config_name: str) -> dict[str, Any]:
    """加载 YAML 配置文件"""
    config_dir = Path(__file__).parent.parent.parent / "config"
    config_path = config_dir / f"{config_name}.yaml"

    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_keywords() -> dict[str, Any]:
    """加载关键词配置

    返回:
        {
            "shill_blacklist": [...],
            "tier1_event_keywords": {"security": [...], ...},
            "tier2_topic_keywords": {"crypto": [...], ...},
        }

    TODO: 后续可替换为 Notion API 同步
    """
    return load_config("keywords")


def load_accounts() -> dict[str, Any]:
    """加载 Twitter 账号配置

    返回 twitter_accounts.yaml 的内容。
    TODO: 后续可替换为 Notion API 同步
    """
    return load_config("twitter_accounts")


def load_account_groups() -> dict[str, set[str]]:
    """从 twitter_accounts.yaml 提取各组 handle 集合

    Returns:
        {"risk_detectors": {"zachxbt", "whale_alert", ...}, ...}

    支持两种 YAML 格式:
        - 个人权重: [{handle: weight}, ...]
        - 纯列表: [handle, ...]
    """
    config = load_accounts()
    groups: dict[str, set[str]] = {}
    for group_name, group_cfg in config.items():
        handles: set[str] = set()
        for entry in group_cfg.get("accounts", []):
            if isinstance(entry, dict):
                handles.update(entry.keys())
            else:
                handles.add(str(entry))
        groups[group_name] = handles
    return groups
