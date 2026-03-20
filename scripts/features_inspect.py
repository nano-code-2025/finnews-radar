"""features.db 检查与导出工具

用法:
    python scripts/features_inspect.py                        # 统计概览 + 前10条
    python scripts/features_inspect.py --all                  # 全部记录
    python scripts/features_inspect.py --limit 20             # 前20条
    python scripts/features_inspect.py --topic market         # 按 topic 筛选
    python scripts/features_inspect.py --author elonmusk      # 按作者筛选
    python scripts/features_inspect.py --date 2026-02-08      # 按日期筛选
    python scripts/features_inspect.py --llm-only             # 仅 LLM 增强记录
    python scripts/features_inspect.py --fear 0.5             # fear_score >= 阈值
    python scripts/features_inspect.py --fomo 0.5             # fomo_score >= 阈值
    python scripts/features_inspect.py --csv output.csv       # 导出 CSV
    python scripts/features_inspect.py --stats                # 仅统计，不列记录

    筛选可组合: --topic market --llm-only --csv market_llm.csv
"""
import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
FEATURES_DB = ROOT / "data" / "features.db"



def build_query(args: argparse.Namespace) -> tuple[str, list]:
    """根据筛选参数构建 SQL"""
    conditions: list[str] = []
    params: list = []

    if args.topic:
        conditions.append(
            "(CASE WHEN llm_enhanced = 1 AND llm_topic IS NOT NULL AND llm_topic != '' "
            "THEN llm_topic ELSE topic END) = ?"
        )
        params.append(args.topic)
    if args.author:
        conditions.append("author_handle LIKE ?")
        params.append(f"%{args.author}%")
    if args.date:
        conditions.append("published_at LIKE ?")
        params.append(f"{args.date}%")
    if args.llm_only:
        conditions.append("llm_enhanced = 1")
    if args.fear is not None:
        conditions.append(
            "(CASE WHEN llm_enhanced = 1 AND llm_fear_score IS NOT NULL "
            "THEN llm_fear_score ELSE fear_score END) >= ?"
        )
        params.append(args.fear)
    if args.fomo is not None:
        conditions.append(
            "(CASE WHEN llm_enhanced = 1 AND llm_fomo_score IS NOT NULL "
            "THEN llm_fomo_score ELSE fomo_score END) >= ?"
        )
        params.append(args.fomo)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    order = " ORDER BY published_at DESC"
    limit = "" if args.all else f" LIMIT {args.limit}"

    sql = f"SELECT * FROM post_features{where}{order}{limit}"
    return sql, params


def print_stats(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    """打印统计概览"""
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM post_features").fetchone()[0]
    llm_count = conn.execute("SELECT COUNT(*) FROM post_features WHERE llm_enhanced = 1").fetchone()[0]

    print(f"\n{'=' * 70}")
    print(f"  features.db 统计概览")
    print(f"{'=' * 70}")
    print(f"  总记录: {total} 条")
    print(f"  LLM 增强: {llm_count} 条 ({llm_count/total:.0%})" if total else "  LLM 增强: 0 条")

    # 日期范围
    row = conn.execute(
        "SELECT MIN(published_at), MAX(published_at) FROM post_features"
    ).fetchone()
    if row[0]:
        print(f"  时间跨度: {row[0][:10]} ~ {row[1][:10]}")

    # 按 topic 分布
    print(f"\n  {'Topic':<14} {'Count':>5} {'Share':>6}  {'Avg Sent':>8}  {'Avg Fear':>8}  {'Avg FOMO':>8}")
    print(f"  {'─'*14} {'─'*5} {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}")
    rows = conn.execute("""
        SELECT
            (CASE WHEN llm_enhanced = 1 AND llm_topic IS NOT NULL AND llm_topic != ''
                  THEN llm_topic ELSE topic END) AS topic,
            COUNT(*) as cnt,
            AVG(CASE WHEN llm_enhanced = 1 AND llm_sentiment IS NOT NULL
                     THEN llm_sentiment ELSE sentiment END) as avg_s,
            AVG(CASE WHEN llm_enhanced = 1 AND llm_fear_score IS NOT NULL
                     THEN llm_fear_score ELSE fear_score END) as avg_f,
            AVG(CASE WHEN llm_enhanced = 1 AND llm_fomo_score IS NOT NULL
                     THEN llm_fomo_score ELSE fomo_score END) as avg_fo
        FROM post_features GROUP BY topic ORDER BY cnt DESC
    """).fetchall()
    for r in rows:
        share = r["cnt"] / total if total else 0
        avg_s = r["avg_s"] or 0
        avg_f = r["avg_f"] or 0
        avg_fo = r["avg_fo"] or 0
        print(f"  {r['topic'] or 'NULL':<14} {r['cnt']:>5} {share:>5.0%}  {avg_s:>+8.3f}  {avg_f:>8.2f}  {avg_fo:>8.2f}")

    # 按作者 top 10
    print(f"\n  Top 10 作者:")
    rows = conn.execute("""
        SELECT author_handle, COUNT(*) as cnt
        FROM post_features GROUP BY author_handle ORDER BY cnt DESC LIMIT 10
    """).fetchall()
    for r in rows:
        print(f"    @{r['author_handle']:<20} {r['cnt']:>4} 条")

    # High fear / High fomo 计数
    high_fear = conn.execute("""
        SELECT COUNT(*) FROM post_features
        WHERE (CASE WHEN llm_enhanced = 1 AND llm_fear_score IS NOT NULL
                    THEN llm_fear_score ELSE fear_score END) >= 0.7
    """).fetchone()[0]
    high_fomo = conn.execute("""
        SELECT COUNT(*) FROM post_features
        WHERE (CASE WHEN llm_enhanced = 1 AND llm_fomo_score IS NOT NULL
                    THEN llm_fomo_score ELSE fomo_score END) >= 0.7
    """).fetchone()[0]
    if high_fear or high_fomo:
        print(f"\n  High Fear (>=0.7): {high_fear} 条 | High FOMO (>=0.7): {high_fomo} 条")

    print(f"{'=' * 70}")


def print_rows(rows: list[sqlite3.Row]) -> None:
    """终端打印记录"""
    if not rows:
        print("\n  (无匹配记录)")
        return

    print(f"\n{'─' * 70}")
    print(f"  共 {len(rows)} 条记录")
    print(f"{'─' * 70}")

    for i, row in enumerate(rows, 1):
        llm_tag = "[LLM]" if row["llm_enhanced"] else "[RULE]"
        llm_on = bool(row["llm_enhanced"])
        topic = (row["llm_topic"] or row["topic"] or "?") if llm_on else (row["topic"] or "?")
        sent = (row["llm_sentiment"] if llm_on and row["llm_sentiment"] is not None else row["sentiment"]) or 0
        fear = (row["llm_fear_score"] if llm_on and row["llm_fear_score"] is not None else row["fear_score"]) or 0
        fomo = (row["llm_fomo_score"] if llm_on and row["llm_fomo_score"] is not None else row["fomo_score"]) or 0
        rat = (row["llm_rationality"] if llm_on and row["llm_rationality"] is not None else row["rationality"]) or 0
        pub = (row["published_at"] or "")[:16]

        print(f"\n  [{i}] {llm_tag} @{row['author_handle']}  |  {pub}")
        print(f"      topic={topic:<12} sent={sent:+.3f}  fear={fear:.2f}  fomo={fomo:.2f}  rat={rat:.2f}")
        print(f"      engagement: L={row['likes']}  R={row['replies']}  RP={row['reposts']}  V={row['views']}  score={row['engagement_score']:.1f}")

        if row["llm_enhanced"]:
            llm_s = row["llm_sentiment"]
            llm_t = row["llm_topic"] or ""
            llm_f = row["llm_fear_score"]
            llm_fo = row["llm_fomo_score"]
            llm_r = row["llm_rationality"]
            summary = row["llm_summary"] or ""
            print(f"      LLM: sent={llm_s}  topic={llm_t}  fear={llm_f}  fomo={llm_fo}  rat={llm_r}")
            if summary:
                print(f"      摘要: {summary}")

        print(f"      tweet_id: {row['tweet_id']}")


def get_csv_columns(conn: sqlite3.Connection) -> list[str]:
    """从表结构推断 CSV 列顺序"""
    return [r[1] for r in conn.execute("PRAGMA table_info(post_features)")]


def export_csv(rows: list[sqlite3.Row], csv_path: str, columns: list[str]) -> None:
    """导出 CSV"""
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            d = dict(row)
            # 只保留 columns 中的列
            writer.writerow({k: d.get(k) for k in columns})
    print(f"\n  [CSV] 已导出 {len(rows)} 条 → {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="features.db 检查与导出工具")
    parser.add_argument("--all", action="store_true", help="显示全部记录")
    parser.add_argument("--limit", type=int, default=10, help="显示条数 (默认 10)")
    parser.add_argument("--stats", action="store_true", help="仅统计，不列记录")
    parser.add_argument("--topic", type=str, help="按 topic 筛选")
    parser.add_argument("--author", type=str, help="按作者筛选 (模糊匹配)")
    parser.add_argument("--date", type=str, help="按日期筛选 (YYYY-MM-DD)")
    parser.add_argument("--llm-only", action="store_true", help="仅 LLM 增强记录")
    parser.add_argument("--fear", type=float, default=None, help="fear_score >= 阈值")
    parser.add_argument("--fomo", type=float, default=None, help="fomo_score >= 阈值")
    parser.add_argument("--csv", type=str, metavar="FILE", help="导出 CSV 文件路径")
    args = parser.parse_args()

    if not FEATURES_DB.exists():
        print("[ERROR] features.db 不存在:", FEATURES_DB)
        return

    conn = sqlite3.connect(FEATURES_DB)
    conn.row_factory = sqlite3.Row

    # 统计
    print_stats(conn, args)

    if args.stats and not args.csv:
        conn.close()
        return

    # 查询
    sql, params = build_query(args)
    rows = conn.execute(sql, params).fetchall()

    # CSV 导出（不受 --stats 限制，始终用完整结果集）
    if args.csv:
        columns = get_csv_columns(conn)
        # CSV 导出时忽略 limit，取全部匹配
        if not args.all:
            sql_full, _ = build_query(argparse.Namespace(
                **{**vars(args), "all": True}
            ))
            rows_full = conn.execute(sql_full, params).fetchall()
            export_csv(rows_full, args.csv, columns)
        else:
            export_csv(rows, args.csv, columns)

    # 终端打印
    if not args.stats:
        print_rows(rows)

    conn.close()


if __name__ == "__main__":
    main()
