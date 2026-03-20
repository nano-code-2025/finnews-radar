# FinNews

Financial news collection, sentiment analysis, and intelligent push notification system.

Tech stack: Python 3.11+, SQLite, Telegram Bot, Grok xAI API, VADER, FinBERT

Architecture details: see [ARCHITECTURE.md](ARCHITECTURE.md)
Future roadmap: see [ROADMAP.md](ROADMAP.md)

---

## Quick Start

```bash
# Setup
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Environment
cp .env.example .env           # Fill in API keys
```

Required `.env` keys:
- `XAI_API_KEY` — Grok xAI API (Twitter collection + LLM)
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_CHAT_ID` — Telegram chat ID
- `TELEGRAM_DAILY_CHAT_ID` — (optional) dedicated chat ID for 24h daily report (fallback to `TELEGRAM_CHAT_ID`)
- `DAILY_REPORT_USER_NAME` — (optional) name used in daily report greeting
- `TWITTER_TWEETS_PER_ACCOUNT` — (optional) tweets per account (default: 2)
- `TWITTER_LOOKBACK_DAYS` — (optional) lookback days for x_search (default: 3)
- `TWITTER_MAX_CONCURRENT_REQUESTS` — (optional) max parallel requests (default: 5)

---

## Common Commands

### Run Pipeline

```bash
# Full pipeline (collect + filter + push)
venv\Scripts\python.exe main.py -q --mock    # mock FinBERT (dev)
venv\Scripts\python.exe main.py -q           # real FinBERT (prod)

# Collect only (no filtering/push)
venv\Scripts\python.exe scripts/collector.py
venv\Scripts\python.exe scripts/collector.py --tw-only
venv\Scripts\python.exe scripts/collector.py --rss-only
```

### Daily Report

```bash
venv\Scripts\python.exe scripts/daily_report.py                  # full default (print + JSON + CSV + log + Telegram; AI only on anomalies)
venv\Scripts\python.exe scripts/daily_report.py 2026-02-08       # specific date
venv\Scripts\python.exe scripts/daily_report.py --no-telegram    # skip Telegram
venv\Scripts\python.exe scripts/daily_report.py --no-csv         # skip CSV
venv\Scripts\python.exe scripts/daily_report.py --no-save        # skip JSON + CSV
venv\Scripts\python.exe scripts/daily_report.py --no-ai          # skip Grok AI insights (even if anomalies)
venv\Scripts\python.exe scripts/daily_report.py --force-ai       # force Grok AI insights (ignore anomaly gate)
venv\Scripts\python.exe scripts/daily_report.py --quiet          # no terminal print
```
Note: default report window is the completed US trading day (ET "yesterday", 00:00–24:00).

### Scheduler

```bash
venv\Scripts\python.exe scripts/scheduler.py                     # dev mode (default)
venv\Scripts\python.exe scripts/scheduler.py --mode prod         # prod mode
venv\Scripts\python.exe scripts/scheduler.py --interval 15       # change interval (minutes)
venv\Scripts\python.exe scripts/scheduler.py --report-hour 15    # override report hour (default auto from ET 02:00)
venv\Scripts\python.exe scripts/scheduler.py --report-minute 0   # override report minute (default 00)
venv\Scripts\python.exe scripts/scheduler.py --no-report         # disable daily report
venv\Scripts\python.exe scripts/scheduler.py --list              # show job config
venv\Scripts\python.exe scripts/scheduler.py --once raw_data     # run a job once
venv\Scripts\python.exe scripts/scheduler.py --once full_pipeline
```
Note: scheduler auto-maps ET 02:00 to local SGT (DST-aware). Restart the scheduler after DST changes.

**Mode explanation:**
- `dev` — raw_data + daily_report (collect data while developing downstream code)
- `prod` — full_pipeline + daily_report (complete automated flow)
- raw_data and full_pipeline are **mutually exclusive** (full_pipeline already collects)

### Database Inspection

```bash
# Raw data DBs
venv\Scripts\python.exe scripts/db_inspect.py

# Features DB
venv\Scripts\python.exe scripts/features_inspect.py
venv\Scripts\python.exe scripts/features_inspect.py --topic market
venv\Scripts\python.exe scripts/features_inspect.py --author zachxbt
venv\Scripts\python.exe scripts/features_inspect.py --date 2026-02-09
venv\Scripts\python.exe scripts/features_inspect.py --fear 0.7        # high fear only
venv\Scripts\python.exe scripts/features_inspect.py --fomo 0.7        # high fomo only
venv\Scripts\python.exe scripts/features_inspect.py --llm-only        # LLM enhanced only
venv\Scripts\python.exe scripts/features_inspect.py --csv             # export CSV
venv\Scripts\python.exe scripts/features_inspect.py --stats           # stats only

# RSS sources check
venv\Scripts\python.exe scripts/check_rss_sources.py

# Feature data check
venv\Scripts\python.exe scripts/check_features.py
```

### Backfill (after schema upgrade)

```bash
venv\Scripts\python.exe scripts/backfill_features.py              # rule-only backfill
venv\Scripts\python.exe scripts/backfill_features.py --llm        # LLM-only backfill
venv\Scripts\python.exe scripts/backfill_features.py --all        # rule + LLM
venv\Scripts\python.exe scripts/backfill_features.py --dry-run    # preview only
```

### Testing & Quality

```bash
venv\Scripts\python.exe -m pytest tests/
venv\Scripts\python.exe -m mypy src/
venv\Scripts\python.exe -m ruff check src/

# Test Telegram connection
venv\Scripts\python.exe scripts/test_telegram_pusher.py

# Verify Twitter accounts
venv\Scripts\python.exe scripts/verify_twitter_accounts.py
```

---

## Project Structure

```
├── main.py                              # Orchestrator: collect → persist → filter → push → mark
├── ARCHITECTURE.md                      # System architecture (data flow, schemas, design)
├── README.md                            # This file (operations & maintenance)
├── CLAUDE.md                            # AI coding guide
├── config/
│   ├── keywords.yaml                    # Unified keywords: shill + tier1 + tier2 + rss
│   ├── twitter_accounts.yaml            # 5 groups, 17 accounts + per-account weight
│   ├── rss_sources.yaml                 # RSS source URLs + categories
│   └── prompts.md                       # All LLM maintenance prompts
├── data/
│   ├── rss.db                           # RSS raw data
│   ├── twitter.db                       # Twitter raw data
│   ├── features.db                      # Feature store (long-term)
│   └── reports/                         # Daily report JSON + CSV
├── src/
│   ├── collectors/
│   │   ├── base.py                      # NewsItem + BaseCollector
│   │   ├── rss_collector.py             # RSS (feedparser)
│   │   └── twitter_collector.py         # Twitter (Grok xAI API)
│   ├── analyzers/
│   │   ├── base.py                      # AnalyzedItem + BaseAnalyzer
│   │   ├── deduplicator.py              # SHA256 dedup
│   │   ├── rss_filter.py               # RSS Sourcing + Ranking
│   │   ├── twitter_filter.py            # Twitter Sourcing + Ranking + Sentiment
│   │   └── feature_extractor.py         # Rule engine + LLM buffer (Grok fast)
│   ├── pipelines/
│   │   ├── base.py                      # FilterResult + BasePipeline
│   │   ├── rss_pipeline.py              # RSS pipeline → rss_features
│   │   └── twitter_pipeline.py          # Twitter pipeline → post_features
│   ├── pushers/
│   │   └── telegram_pusher.py           # Telegram tiered push
│   ├── report/
│   │   └── daily_report.py              # 24h report generator
│   └── utils/
│       ├── db.py                        # RSSDatabase + TwitterDatabase
│       ├── features_db.py               # FeaturesDatabase
│       └── config.py                    # load_keywords / load_accounts / load_config
├── scripts/
│   ├── scheduler.py                     # APScheduler unified scheduler (dev/prod)
│   ├── collector.py                     # Standalone collector (Twitter + RSS → DB)
│   ├── daily_report.py                  # Daily report CLI
│   ├── db_inspect.py                    # Raw DB inspection
│   ├── features_inspect.py              # Features DB inspection + CSV export
│   ├── backfill_features.py             # Schema upgrade backfill
│   ├── check_features.py               # Feature data check
│   ├── check_rss_sources.py            # RSS sources check
│   ├── verify_twitter_accounts.py       # Twitter account verification
│   ├── verify_v4.py                     # v4 upgrade verification
│   ├── test_telegram_pusher.py          # Telegram connection test
│   └── test_twitter_collector.py        # Twitter collector test
├── tests/                               # Unit tests
└── requirements.txt
```

---

## Dependencies

| Package | Purpose |
|---------|---------|
| openai | Grok xAI API (Twitter collection + LLM buffer) |
| feedparser | RSS collection |
| httpx | HTTP client (Telegram) |
| vaderSentiment | VADER sentiment analysis |
| transformers + torch | FinBERT sentiment analysis |
| PyYAML | Config file parsing |
| python-dotenv | .env loading |
| apscheduler | Unified task scheduling |

---

## Config Maintenance

Each YAML config file has embedded LLM maintenance prompts in header comments. Full prompts in `config/prompts.md`.

**Workflow**: Copy prompt → attach current config → send to Grok → review suggestions → update YAML

---

## Known Limitations

- Grok API cannot fetch reposts/retweets, only original tweets
- CoinDesk RSS frequently returns 0 items
- FinBERT first load downloads ~400MB model
- LLM prompt JSON `{}` must be escaped as `{{}}` (Python `.format()` conflict)
