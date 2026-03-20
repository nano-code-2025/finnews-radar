"""24h Daily Report CLI

Default: terminal print + CSV export + JSON save + Conditional AI + Log + Telegram push.
AI insights 仅在数据异常时触发（节省 token），可用 --force-ai 强制触发。

Usage:
  python scripts/daily_report.py                    # today (full default, AI 仅异常触发)
  python scripts/daily_report.py 2026-02-08         # specific date
  python scripts/daily_report.py --force-ai         # 强制触发 AI 解读（无视异常检测）
  python scripts/daily_report.py --no-telegram      # skip Telegram push
  python scripts/daily_report.py --no-csv           # skip CSV export
  python scripts/daily_report.py --no-save          # skip JSON + CSV save
  python scripts/daily_report.py --no-ai            # 完全跳过 AI（即使异常也不调用）
  python scripts/daily_report.py --quiet            # no terminal print
"""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.report.daily_report import DailyReportGenerator  # noqa: E402


def print_report(report: dict, ai_insights: str = "") -> None:
    """Terminal-friendly report print."""
    date = report["date"]
    total = report["total_posts"]
    hhi = report["hhi"]
    llm_rate = report["llm_enhanced_rate"]
    summary = report.get("summary", {})

    print(f"\n{'=' * 70}")
    print(f"  24h Intel Report | {date}")
    print(f"{'=' * 70}")
    print(f"  Posts: {total}")
    print(f"  HHI: {hhi:.4f}", end="")
    if hhi > 0.5:
        print(" (Highly Concentrated)")
    elif hhi > 0.25:
        print(" (Moderately Concentrated)")
    else:
        print(" (Well Diversified)")

    print(f"  LLM Enhanced: {llm_rate:.0%}")
    print(f"  Avg Sentiment: {summary.get('avg_sentiment', 0):+.3f}")

    bullish = summary.get("bullish_count", 0)
    bearish = summary.get("bearish_count", 0)
    neutral = summary.get("neutral_count", 0)
    print(f"  Bullish/Bearish/Neutral: {bullish} / {bearish} / {neutral}")

    avg_fear = summary.get("avg_fear", 0)
    avg_fomo = summary.get("avg_fomo", 0)
    high_fear = summary.get("high_fear_count", 0)
    high_fomo = summary.get("high_fomo_count", 0)
    print(f"  Fear: {avg_fear:.2f} (high: {high_fear}) | FOMO: {avg_fomo:.2f} (high: {high_fomo})")
    print(f"  Total Engagement: {summary.get('total_engagement', 0):,.0f}")

    print(f"\n{'─' * 70}")
    print(f"  {'Topic':<12} {'Count':>5} {'Share':>6} {'Senti':>8} {'Fear':>6} {'FOMO':>6} {'Engage':>10} {'Rational':>8} {'Len':>6}")
    print(f"  {'─'*12} {'─'*5} {'─'*6} {'─'*8} {'─'*6} {'─'*6} {'─'*10} {'─'*8} {'─'*6}")

    for ts in report.get("topics", []):
        print(
            f"  {ts['topic']:<12} {ts['count']:>5} {ts['share']:>5.0%} "
            f"{ts['avg_sentiment']:>+8.3f} {ts.get('avg_fear', 0):>6.2f} {ts.get('avg_fomo', 0):>6.2f} "
            f"{ts['total_engagement']:>10,.0f} {ts['avg_rationality']:>8.3f} {ts['avg_length']:>6.0f}"
        )

    if ai_insights:
        print(f"\n{'─' * 70}")
        print(f"  🤖 AI Insights")
        print(f"{'─' * 70}")
        for line in ai_insights.split("\n"):
            print(f"  {line}")

    print(f"{'=' * 70}\n")


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="24h Intel Report Generator")
    parser.add_argument("date", nargs="?", default=None, help="Date YYYY-MM-DD (default: today)")
    parser.add_argument("--no-telegram", action="store_true", help="Skip Telegram push")
    parser.add_argument("--no-csv", action="store_true", help="Skip CSV export")
    parser.add_argument("--no-save", action="store_true", help="Skip JSON + CSV save")
    parser.add_argument("--no-ai", action="store_true", help="完全跳过 AI（即使异常也不调用）")
    parser.add_argument("--force-ai", action="store_true", help="强制触发 AI 解读（无视异常检测）")
    parser.add_argument("--quiet", "-q", action="store_true", help="No terminal print")
    args = parser.parse_args()

    gen = DailyReportGenerator()
    report = gen.generate(args.date)

    if report["total_posts"] == 0:
        print(f"[DailyReport] {report['date']} — no data")
        return

    # AI insights — 条件触发（节省 token）
    ai_insights = ""
    if not args.no_ai:
        should_ai, reasons = gen.should_generate_ai(report)

        if args.force_ai:
            print("[DailyReport] --force-ai: 强制触发 AI 解读")
            ai_insights = await gen.generate_ai_insights(report)
        elif should_ai:
            print(f"[DailyReport] 检测到 {len(reasons)} 项异常，触发 AI 解读:")
            for r in reasons:
                print(f"  → {r}")
            ai_insights = await gen.generate_ai_insights(report)
        else:
            print("[DailyReport] 数据正常，跳过 AI 解读（节省 token）")

    # Terminal print (default on)
    if not args.quiet:
        print_report(report, ai_insights)

    # JSON save (default on)
    if not args.no_save:
        gen.save_local(report)

    # CSV export (default on)
    csv_path = None
    if not args.no_save and not args.no_csv:
        csv_path = gen.save_csv(report)

    # Log (default on)
    if not args.no_save:
        gen.save_log(report, ai_insights)

    # Telegram push (default on)
    if not args.no_telegram:
        await gen.push_telegram(report, ai_insights)
        # 推送 CSV 文件（如果已生成）
        if csv_path:
            await gen.push_csv_telegram(csv_path)


if __name__ == "__main__":
    asyncio.run(main())
