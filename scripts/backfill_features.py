"""Backfill features.db — 用 twitter.db 原文重算规则值，修正 topic，可选重跑 LLM

场景：Feature Extraction 升级后（如新增 fear/fomo、topic 枚举变更），
旧记录字段为 NULL 或值过时，需要回填以保证日报聚合准确。

逻辑框架:
  1. 选择回填范围（3 种模式）
     - rule（默认）: 只回填 fear_score/fomo_score 为 NULL 或 topic='hack' 的记录
     - llm:          只回填 llm_fear_score/llm_fomo_score 为 NULL 的记录
     - all:          强制全部重算
  2. 数据来源 — 两个数据库交叉查询
     - features.db → 找出需回填记录（取 tweet_id）
     - twitter.db  → 用 tweet_id 取原文（content + 互动数据）
  3. 逐条重算
     - 规则 baseline: 用原文重跑 fear_score / fomo_score，修正 topic（hack→security）
     - LLM 增强（可选）: 调用 Grok API 重跑 llm_sentiment/topic/rationality/summary
     - 写回 features.db（规则值始终更新，LLM 值有则更新）
  4. 安全机制
     - --dry-run: 只打印不写入
     - --llm 时检查 XAI_API_KEY，未配置则提前退出
     - dotenv 自动从 .env 加载环境变量
     - 每 50 条 commit 一次，防止中断丢失

数据流:
  features.db (找出需回填的 tweet_id)
       ↓
  twitter.db  (取原文 content)
       ↓
  FeatureExtractor (规则重算 + 可选 LLM)
       ↓
  features.db (UPDATE 写回新值)

用法:
    python scripts/backfill_features.py              # 仅规则回填（快速，免费）
    python scripts/backfill_features.py --llm        # LLM 回填（补 llm_fear/fomo 为 NULL 的记录）
    python scripts/backfill_features.py --all        # 强制全部重算（规则）
    python scripts/backfill_features.py --all --llm  # 强制全部重算（规则 + LLM）
    python scripts/backfill_features.py --dry-run    # 预览，不写入（可搭配 --llm/--all）
"""
import argparse
import sqlite3
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from src.analyzers.feature_extractor import FeatureExtractor, TOPIC_ENUM  # noqa: E402


FEATURES_DB = ROOT / "data" / "features.db"
TWITTER_DB = ROOT / "data" / "twitter.db"


def get_rows_to_backfill(features_conn: sqlite3.Connection, mode: str) -> list[dict]:
    """找出需要回填的记录

    mode:
        "rule" — fear_score IS NULL 或 topic='hack'
        "llm"  — llm_fear_score IS NULL 或 llm_fomo_score IS NULL
        "all"  — 全部记录（强制重算）
    """
    features_conn.row_factory = sqlite3.Row
    if mode == "rule":
        where = "WHERE fear_score IS NULL OR fomo_score IS NULL OR topic = 'hack'"
    elif mode == "llm":
        where = "WHERE llm_fear_score IS NULL OR llm_fomo_score IS NULL"
    else:  # all
        where = ""
    rows = features_conn.execute(
        f"""
        SELECT id, tweet_id, author_handle, topic
        FROM post_features {where}
        ORDER BY id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_tweet_text(twitter_conn: sqlite3.Connection, tweet_id: str) -> dict | None:
    """从 twitter.db 获取原文"""
    twitter_conn.row_factory = sqlite3.Row
    row = twitter_conn.execute(
        "SELECT content, author_handle, likes, reposts, replies, views, external_urls "
        "FROM tweets WHERE tweet_id = ?",
        (tweet_id,),
    ).fetchone()
    return dict(row) if row else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill features.db with v4 fields")
    parser.add_argument("--llm", action="store_true", help="重跑 LLM 增强（慢，消耗 API）")
    parser.add_argument("--all", action="store_true", help="强制重算全部记录（不管是否已有值）")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写入数据库")
    args = parser.parse_args()

    if not FEATURES_DB.exists():
        print("[ERROR] features.db 不存在")
        return
    if not TWITTER_DB.exists():
        print("[ERROR] twitter.db 不存在")
        return

    features_conn = sqlite3.connect(FEATURES_DB)
    twitter_conn = sqlite3.connect(TWITTER_DB)

    if args.all:
        mode = "all"
    elif args.llm:
        mode = "llm"
    else:
        mode = "rule"
    rows = get_rows_to_backfill(features_conn, mode)
    print(f"[Backfill] 模式: {mode} | 需回填 {len(rows)} 条记录")

    if not rows:
        print("[Backfill] 无需回填，所有记录已是最新。")
        features_conn.close()
        twitter_conn.close()
        return

    # 初始化 FeatureExtractor
    extractor = FeatureExtractor(enable_llm=args.llm)
    if args.llm and not extractor._grok_client:
        print("[Backfill] XAI_API_KEY 未配置，无法启用 LLM 回填。")
        print("  请先设置环境变量: $env:XAI_API_KEY=\"YOUR_KEY\"")
        features_conn.close()
        twitter_conn.close()
        return

    updated = 0
    skipped = 0
    topic_fixed = 0

    for i, row in enumerate(rows):
        tweet_id = row["tweet_id"]
        tweet = get_tweet_text(twitter_conn, tweet_id)

        if not tweet:
            skipped += 1
            continue

        text = tweet["content"]

        # 规则值重算
        fear_score = extractor._compute_fear_score(text)
        fomo_score = extractor._compute_fomo_score(text)

        # Topic 修正: hack → security
        topic = row["topic"]
        if topic == "hack":
            topic = "security"
            topic_fixed += 1

        # LLM 重跑（可选）
        llm_result = None
        if args.llm and extractor._grok_client:
            author = tweet["author_handle"]
            llm_result = extractor._llm_enhance(text, author)
            if llm_result:
                topic = llm_result.get("topic", topic)

        if args.dry_run:
            llm_tag = " +LLM" if llm_result else ""
            print(f"  [DRY] #{row['id']} @{row['author_handle']} "
                  f"fear={fear_score} fomo={fomo_score} topic={topic}{llm_tag}")
        else:
            # 写入 — 规则值始终更新，LLM 值有则更新
            if llm_result:
                features_conn.execute(
                    """
                    UPDATE post_features SET
                        fear_score = ?, fomo_score = ?, topic = ?,
                        llm_fear_score = ?, llm_fomo_score = ?,
                        llm_sentiment = ?, llm_topic = ?,
                        llm_rationality = ?, llm_summary = ?,
                        llm_enhanced = 1
                    WHERE id = ?
                    """,
                    (
                        fear_score, fomo_score, topic,
                        llm_result.get("fear_score"),
                        llm_result.get("fomo_score"),
                        llm_result.get("sentiment"),
                        llm_result.get("topic"),
                        llm_result.get("rationality"),
                        llm_result.get("summary"),
                        row["id"],
                    ),
                )
            else:
                features_conn.execute(
                    """
                    UPDATE post_features SET
                        fear_score = ?, fomo_score = ?, topic = ?
                    WHERE id = ?
                    """,
                    (fear_score, fomo_score, topic, row["id"]),
                )

        updated += 1

        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(rows)}")
            if not args.dry_run:
                features_conn.commit()

    if not args.dry_run:
        features_conn.commit()

    features_conn.close()
    twitter_conn.close()

    print(f"\n[Backfill] 完成!")
    print(f"  更新: {updated} 条")
    print(f"  跳过 (twitter.db 无原文): {skipped} 条")
    print(f"  Topic hack→security: {topic_fixed} 条")
    if args.dry_run:
        print("  (dry-run 模式，未实际写入)")


if __name__ == "__main__":
    main()
