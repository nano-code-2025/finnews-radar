# FinNews Radar

> Real-time financial news intelligence — collect, analyze, and push actionable insights to Telegram.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

FinNews Radar is an automated pipeline that monitors **Twitter/X KOLs** and **RSS news feeds** for crypto & macro events, runs multi-layer sentiment analysis (VADER + FinBERT + Grok LLM), and delivers filtered, scored alerts via Telegram.

---

## Highlights

- **Dual-source collection** — Twitter (via Grok xAI `x_search`) + 10+ RSS feeds (Fed, SEC, CoinDesk, etc.)
- **3-stage filtering** — Shill blacklist → keyword sourcing → sentiment ranking, reducing noise by ~70%
- **Feature extraction** — Rule engine + LLM buffer producing structured features: topic, fear/FOMO scores, rationality index
- **Tiered Telegram push** — Critical alerts (hacks, regulatory) push immediately; routine news batched
- **24h daily report** — Automated summary with AI insights, delivered to Telegram on schedule
- **Config-driven** — Add/remove Twitter accounts, RSS sources, or keywords via YAML — zero code changes

---

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/nano-code-2025/finnews-radar.git
cd finnews-radar

python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
cp config/keywords.yaml.example config/keywords.yaml
cp config/twitter_accounts.yaml.example config/twitter_accounts.yaml
cp config/rss_sources.yaml.example config/rss_sources.yaml
```

Edit `.env` with your API keys:

| Key | Required | Description |
|-----|----------|-------------|
| `XAI_API_KEY` | Yes | Grok xAI API — powers Twitter collection + LLM analysis |
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token ([BotFather](https://t.me/BotFather)) |
| `TELEGRAM_CHAT_ID` | Yes | Target chat/group ID for real-time alerts |
| `TELEGRAM_DAILY_CHAT_ID` | No | Separate chat for daily reports (defaults to `CHAT_ID`) |
| `DAILY_REPORT_USER_NAME` | No | Name used in daily report greeting |

### 3. Run

```bash
# Full pipeline: collect → filter → analyze → push
python main.py -q

# Dev mode (mock FinBERT to skip 400MB model download)
python main.py -q --mock
```

---

## Usage

### Pipeline Modes

```bash
# Collect only (no filtering/push)
python scripts/collector.py
python scripts/collector.py --tw-only       # Twitter only
python scripts/collector.py --rss-only      # RSS only
```

### Daily Report

```bash
python scripts/daily_report.py              # Full report + Telegram
python scripts/daily_report.py 2026-02-08   # Specific date
python scripts/daily_report.py --force-ai   # Force AI insights
python scripts/daily_report.py --no-telegram # Terminal only
```

> Default window: completed US trading day (ET yesterday, 00:00–24:00).

### Scheduler (Automated)

```bash
python scripts/scheduler.py                 # Dev mode
python scripts/scheduler.py --mode prod     # Production: full pipeline + daily report
python scripts/scheduler.py --interval 15   # Collection every 15 min
python scripts/scheduler.py --list          # Show job config
```

| Mode | Jobs | Use Case |
|------|------|----------|
| `dev` | `raw_data` + `daily_report` | Collect data while developing |
| `prod` | `full_pipeline` + `daily_report` | Fully automated flow |

### Inspect Data

```bash
python scripts/features_inspect.py              # All features
python scripts/features_inspect.py --topic market
python scripts/features_inspect.py --fear 0.7    # High fear only
python scripts/features_inspect.py --csv         # Export CSV
python scripts/features_inspect.py --stats       # Stats summary
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    scheduler.py (APScheduler)                │
│           dev: raw_data    prod: full_pipeline               │
└───────────────────────┬──────────────────────────────────────┘
                        │
              ┌─────────▼──────────┐
              │      main.py       │
              │   (orchestrator)   │
              └─────────┬──────────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
   ┌──────────┐  ┌───────────┐  ┌──────────┐
   │ Collect   │  │ Analyze   │  │  Push    │
   │ RSS + TW  │  │ Filter +  │  │ Telegram │
   │           │  │ Sentiment │  │          │
   └─────┬────┘  └─────┬─────┘  └──────────┘
         │              │
         ▼              ▼
   ┌─────────────────────────┐
   │     SQLite (3 DBs)      │
   │  twitter · rss · features│
   └─────────────────────────┘
```

**Filtering pipeline** (per source):

```
Raw items → Dedup (SHA256) → Shill blacklist → Keyword sourcing
         → Sentiment (VADER + FinBERT) → Feature extraction (rule + LLM)
         → Scoring & ranking → Telegram push
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for full schema and data flow details.

---

## Project Structure

```
finnews-radar/
├── main.py                      # Pipeline orchestrator
├── config/
│   ├── keywords.yaml.example    # Keyword filters template
│   ├── twitter_accounts.yaml.example  # KOL watchlist template
│   ├── rss_sources.yaml.example # RSS feeds template
│   └── prompts.md               # LLM maintenance prompts
├── src/
│   ├── collectors/              # Data collection (RSS + Twitter)
│   ├── analyzers/               # Filtering, sentiment, feature extraction
│   ├── pipelines/               # End-to-end processing pipelines
│   ├── pushers/                 # Notification delivery (Telegram)
│   ├── report/                  # Daily report generation
│   └── utils/                   # Config, database, helpers
├── scripts/                     # CLI tools (scheduler, collector, inspector)
├── tests/                       # Unit tests
├── ARCHITECTURE.md              # System design documentation
├── ROADMAP.md                   # Future development plans
└── requirements.txt
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| Twitter API | Grok xAI (`x_search`) |
| RSS parsing | feedparser |
| Sentiment | VADER + FinBERT (transformers) |
| LLM analysis | Grok xAI (OpenAI-compatible) |
| Database | SQLite (3 DBs: twitter, rss, features) |
| Push | Telegram Bot API (httpx) |
| Scheduling | APScheduler |
| Config | YAML + python-dotenv |

---

## Configuration

All config files use `.example` templates. Copy and customize:

- **`config/twitter_accounts.yaml`** — Twitter KOL watchlist with per-account weights (1.0–10.0). Groups are auto-discovered.
- **`config/rss_sources.yaml`** — RSS feed URLs with categories. Add/remove freely.
- **`config/keywords.yaml`** — Shill blacklist, event keywords (tier 1), topic keywords (tier 2), RSS filter words.
- **`config/prompts.md`** — LLM prompts for periodic config maintenance (copy prompt → send to Grok → review suggestions → update YAML).

---

## Known Limitations

- Grok API cannot fetch reposts/retweets, only original tweets
- CoinDesk RSS occasionally returns 0 items
- FinBERT first load downloads ~400MB model
- LLM prompt JSON `{}` must be escaped as `{{}}` (Python `.format()` conflict)

---

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned features including:
- Web dashboard with sentiment visualization
- Historical data & backtesting engine
- Multi-LLM support (Claude, GPT-4)
- Telegram Bot self-service configuration

---

## License

MIT — see [LICENSE](LICENSE) for details.
