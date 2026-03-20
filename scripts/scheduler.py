"""统一定时调度器 — APScheduler 管理所有定时任务

三个 Job:
  raw_data       仅采集 (collector.py) → twitter.db / rss.db
  full_pipeline  完整管道 (main.py)    → 采集 + 过滤 + 推送
  daily_report   24h 日报 (daily_report.py --quiet)

互斥关系:
  raw_data 和 full_pipeline 不能同时启用！
  full_pipeline 第一步就是采集，同时跑 raw_data 会重复调用 API。

  模式 A — 开发期:  --mode dev   = raw_data + daily_report
           下游停着改代码，上游持续采集不丢数据
  模式 B — 生产期:  --mode prod  = full_pipeline + daily_report
           完整流程自动跑，采集+过滤+推送一条龙

用法:
  python scripts/scheduler.py                     # 默认 dev 模式
  python scripts/scheduler.py --mode prod         # 生产模式
  python scripts/scheduler.py --interval 15       # 改采集间隔(分钟)
  python scripts/scheduler.py --report-hour 10    # 改日报时间(默认 09:00)
  python scripts/scheduler.py --no-report         # 不跑日报
  python scripts/scheduler.py --list              # 查看 job 配置
  python scripts/scheduler.py --once raw_data     # 立即跑一次某个 job
  python scripts/scheduler.py --once full_pipeline

依赖:
  pip install apscheduler
"""
import argparse
import subprocess
import sys
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8")

try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
except ImportError:
    print("[ERROR] apscheduler 未安装，请运行: pip install apscheduler")
    sys.exit(1)

# ── 常用配置（经常修改区）──────────────────────────
COLLECT_INTERVAL_MIN = 30   # 采集/管道 间隔（分钟）

# 日报触发以纽约时间为基准：ET 02:00 输出“昨日美盘完整日”
REPORT_ET_HOUR = 2
REPORT_ET_MIN = 0
LOCAL_TZ = "Asia/Singapore"
ET_TZ = "America/New_York"


def _default_report_time_sgt() -> tuple[int, int]:
    """把 ET 固定时间换算为本地时区时间（自动处理夏令时）。"""
    et = ZoneInfo(ET_TZ)
    local = ZoneInfo(LOCAL_TZ)
    today_et = datetime.now(et).date()
    et_dt = datetime.combine(today_et, time(REPORT_ET_HOUR, REPORT_ET_MIN), tzinfo=et)
    local_dt = et_dt.astimezone(local)
    return local_dt.hour, local_dt.minute


DAILY_REPORT_HOUR, DAILY_REPORT_MIN = _default_report_time_sgt()
# ─────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"

# Linux/Mac 兼容
if not PYTHON.exists():
    PYTHON = ROOT / "venv" / "bin" / "python"

# Job 定义: name → (脚本路径, 参数列表, 超时秒数)
JOBS: dict[str, tuple[str, list[str], int]] = {
    "raw_data": (
        str(ROOT / "scripts" / "collector.py"),
        [],
        300,  # 5分钟
    ),
    "full_pipeline": (
        str(ROOT / "main.py"),
        # ["-q", "--mock"],
        # 600,  # 10分钟（含 FinBERT mock）
        ["-q"],  # prod uses real FinBERT
        600,  # 10分钟
    ),
    "daily_report": (
        str(ROOT / "scripts" / "daily_report.py"),
        ["--quiet", "--no-ai"],  # scheduler 不需要终端打印，只做 JSON+CSV+Telegram，跳过AI
        300,  # 5分钟
    ),
}


def run_job(job_name: str) -> None:
    """在子进程中运行指定 job"""
    if job_name not in JOBS:
        print(f"[Scheduler] 未知 job: {job_name}")
        return

    script, args, timeout = JOBS[job_name]
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'─' * 50}")
    print(f"[Scheduler] {ts} 触发 {job_name}...")

    try:
        result = subprocess.run(
            [str(PYTHON), script] + args,
            cwd=str(ROOT),
            capture_output=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            print(f"[Scheduler] {job_name} 退出码: {result.returncode}")
    except subprocess.TimeoutExpired:
        print(f"[Scheduler] {job_name} 超时 (>{timeout}s)")
    except Exception as e:
        print(f"[Scheduler] {job_name} 异常: {e}")


def job_listener(event: object) -> None:
    """APScheduler 事件监听"""
    if hasattr(event, "exception") and event.exception:  # type: ignore[union-attr]
        print(f"[Scheduler] Job 异常: {event.exception}")  # type: ignore[union-attr]


def print_job_list(
    mode: str,
    interval: int,
    report_hour: int,
    no_report: bool,
    report_minute: int,
) -> None:
    """打印 job 配置"""
    print(f"\n{'=' * 50}")
    print(f"  Job 配置 (mode={mode})")
    print(f"{'=' * 50}")

    if mode == "dev":
        print(f"  [ON]  raw_data       每 {interval} 分钟  仅采集入库")
        print(f"  [OFF] full_pipeline  ─────────  (dev 模式不启用)")
    else:
        print(f"  [OFF] raw_data       ─────────  (prod 模式不启用)")
        print(f"  [ON]  full_pipeline  每 {interval} 分钟  采集+过滤+推送")

    if no_report:
        print(f"  [OFF] daily_report   ─────────  (已禁用)")
    else:
        print(f"  [ON]  daily_report   每天 {report_hour:02d}:{report_minute:02d}")

    print(f"{'=' * 50}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="统一定时调度器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
模式说明:
  dev   开发期: raw_data + daily_report (上游采集，下游停着改代码)
  prod  生产期: full_pipeline + daily_report (完整自动流程)

  raw_data 和 full_pipeline 互斥，不能同时跑!
""",
    )
    parser.add_argument(
        "--mode", choices=["dev", "prod"], default="dev",
        help="运行模式: dev=仅采集, prod=完整管道 (默认 dev)",
    )
    parser.add_argument("--interval", type=int, default=COLLECT_INTERVAL_MIN, help=f"间隔(分钟), 默认 {COLLECT_INTERVAL_MIN}")
    parser.add_argument("--report-hour", type=int, default=DAILY_REPORT_HOUR, help=f"日报时间(小时), 默认 {DAILY_REPORT_HOUR}")
    parser.add_argument("--report-minute", type=int, default=DAILY_REPORT_MIN, help=f"日报时间(分钟), 默认 {DAILY_REPORT_MIN}")
    parser.add_argument("--no-report", action="store_true", help="不启用日报")
    parser.add_argument("--list", action="store_true", help="查看 job 配置")
    parser.add_argument("--once", choices=list(JOBS.keys()), help="立即跑一次指定 job 然后退出")
    args = parser.parse_args()

    if not PYTHON.exists():
        print(f"[ERROR] 找不到 Python: {PYTHON}")
        sys.exit(1)

    # --list: 打印配置
    if args.list:
        print_job_list(args.mode, args.interval, args.report_hour, args.no_report, args.report_minute)
        return

    # --once: 跑一次退出
    if args.once:
        run_job(args.once)
        return

    # 正常调度模式
    print_job_list(args.mode, args.interval, args.report_hour, args.no_report, args.report_minute)

    scheduler = BlockingScheduler()
    scheduler.add_listener(job_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    # 主 job: raw_data 或 full_pipeline（互斥）
    if args.mode == "dev":
        main_job = "raw_data"
    else:
        main_job = "full_pipeline"

    scheduler.add_job(
        run_job,
        "interval",
        args=[main_job],
        minutes=args.interval,
        misfire_grace_time=300,
        id=main_job,
        name=f"{main_job} every {args.interval}min",
    )

    # daily_report
    if not args.no_report:
        scheduler.add_job(
            run_job,
            "cron",
            args=["daily_report"],
            hour=args.report_hour,
            minute=args.report_minute,
            misfire_grace_time=3600,  # 1小时内仍补跑
            id="daily_report",
            name=f"daily_report at {args.report_hour:02d}:{args.report_minute:02d}",
        )

    print(f"[Scheduler] Ctrl+C 停止\n")

    # 启动时立即跑一次主 job
    run_job(main_job)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n[Scheduler] 已停止")


if __name__ == "__main__":
    main()
