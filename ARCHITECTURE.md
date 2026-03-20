# FinNews System Architecture

> v5.4 | 2026-02-09

## Overview

Financial news collection, sentiment analysis, and intelligent push notification system.

```
                    ┌─────────────────────────────────────────┐
                    │           scheduler.py (APScheduler)    │
                    │  dev:  raw_data + daily_report           │
                    │  prod: full_pipeline + daily_report      │
                    └──────────┬──────────────┬───────────────┘
                               │              │
                    ┌──────────▼──────┐  ┌────▼────────────┐
                    │  collector.py   │  │  main.py         │
                    │  (raw_data)     │  │  (full_pipeline) │
                    └──────────┬──────┘  └────┬────────────┘
                               │              │
              ┌────────────────▼──────────────▼─────────────────┐
              │                 Data Layer                       │
              │   twitter.db    rss.db    features.db            │
              └─────────────────────────────────────────────────┘
```

## Data Flow

### Full Pipeline (main.py)

```
1. Collect (parallel)
   ├── RSSCollector    → list[NewsItem]
   └── TwitterCollector → list[Tweet]

2. Persist raw data
   ├── RSSDatabase.insert()     → rss.db
   └── TwitterDatabase.insert() → twitter.db

3. Pipeline (from unpushed rows)
   ├── RSSPipeline.run()      → FilterResult → rss_features table
   └── TwitterPipeline.run()  → FilterResult → post_features table

4. Push
   └── TelegramPusher.push(merged passed items)

5. Mark pushed
   ├── rss_db.mark_pushed(all urls)
   └── twitter_db.mark_pushed(all tweet_ids)
```

### Twitter Pipeline

```
TwitterPipeline.run()
├── DB get unpushed → dict → NewsItem → AnalyzedItem
├── Deduplicator (SHA256)
├── TwitterFilter.filter()
│   ├── Sourcing: shill blacklist → Tier1 event tag → Tier2 keyword filter
│   ├── Ranking:  author_weight × engagement × time_decay
│   └── Sentiment: VADER scoring + level classification
├── FeatureExtractor.extract_batch()
│   ├── Rule baseline: topic, sentiment, fear, fomo, rationality, length
│   └── LLM enhance:   llm_sentiment, llm_topic, llm_fear, llm_fomo, llm_summary
└── FeaturesDatabase.insert_batch() → features.db post_features
```

**Sourcing (from config/keywords.yaml)**

| Layer | Config Key | Behavior | Count |
|-------|-----------|----------|-------|
| Shill blacklist | `shill_blacklist` | Match → skip | 27 |
| Tier 1 event | `tier1_event_keywords` | Tag only, always pass | 6 categories ~25 words |
| Tier 2 topic | `tier2_topic_keywords` | Must match to pass | 3 categories ~120 words |

Pass condition: Tier2 match, or Tier1 match (even without Tier2).

**Ranking**

```
engagement = likes×0.5 + replies×13 + reposts×10 + log10(views)×2
time_decay = 1 / (1 + 0.3 × hours_old)
score = author_weight × engagement × time_decay
```

**Sentiment levels**: |LLM_sentiment| >= 0.5 → URGENT, >= 0.2 → IMPORTANT, else NORMAL (fallback to VADER)

### RSS Pipeline

```
RSSPipeline.run()
├── DB get unpushed → dict → NewsItem → AnalyzedItem
├── Deduplicator (SHA256)
├── RSSFilter.filter()
│   ├── Sourcing: whitelist pass-through + keyword match (from config)
│   └── Ranking:  VADER + FinBERT dual-track + divergence detection
└── FeaturesDatabase.insert_rss_batch() → features.db rss_features
    (both passed AND skipped items persisted)
```

**Sourcing**: Whitelist (CoinDesk, Cointelegraph, The Block, Decrypt) → direct pass. Others need keyword match from `rss_keywords` section (macro/regulation/security/crypto).

**Ranking**

```
sentiment = 0.3 × |vader_title| + 0.7 × |finbert_summary|
score = sentiment×0.30 + relevance×0.25 + macro×0.25 + divergence×0.20
```

| Level | Condition | Action |
|-------|-----------|--------|
| URGENT | score >= 0.6 | Push |
| IMPORTANT | score >= 0.3 | Push |
| SKIP | score < 0.3 | No push |

Divergence: |vader - finbert| > 0.5 → flagged

### Feature Extraction (v4)

**Rule engine** (always runs):

| Field | Method |
|-------|--------|
| topic | event_tags → keyword → category → "other" (11 classes) |
| sentiment | VADER compound |
| fear_score | Keyword intensity (0-1) |
| fomo_score | Keyword intensity (0-1) |
| rationality | Data signals vs hype signals |
| length | len(text) |

**LLM enhance** (Grok fast model, default on, fallback to rules on failure):

| Field | Range |
|-------|-------|
| llm_sentiment | -1.0 ~ 1.0 |
| llm_topic | 11-class enum |
| llm_fear_score | 0.0 ~ 1.0 |
| llm_fomo_score | 0.0 ~ 1.0 |
| llm_rationality | 0.0 ~ 1.0 |
| llm_summary | Chinese one-line summary |

Rule and LLM values coexist — never overwritten.

**Topic enum** (11 classes): regulation, security, etf, macro, defi, market, exchange, geopolitical, memecoin, mining, other

### 24h Daily Report

```
DailyReportGenerator.generate()
├── features.db query by date
├── Group by topic → aggregate stats
│   (sentiment, fear, fomo, engagement, rationality — prefer LLM values)
├── Global summary (bullish/bearish/neutral, high_fear/high_fomo counts)
└── HHI topic concentration index
└── Report window: completed US trading day (ET yesterday 00:00–24:00, converted to UTC)
└── Scheduler: run at ET 02:00; auto converts to SGT 14:00/15:00 via timezone rules

Output:
├── Terminal print (table format)
├── JSON save  → data/reports/daily_report_YYYY-MM-DD.json
├── CSV export → data/reports/daily_report_YYYY-MM-DD.csv (per-post rows; aligned with `features_inspect.py --csv`)
├── Log append → data/logs/daily_report.log
└── Telegram push (mobile-friendly; optional AI insights gated by anomaly detection)
(Anomaly gate: HHI/topic concentration, extreme topic fear/FOMO/sentiment, high-fear/high-FOMO counts, and engagement-driven panic heuristics.)
```

---

## Database Schema

### twitter.db / rss.db

Raw collected data + `is_pushed` flag. Pipeline reads unpushed rows, processes them, then marks pushed.

### features.db

```sql
post_features (
    tweet_id, author_handle, published_at, extracted_at,
    -- Rule baseline
    topic, sentiment, fear_score, fomo_score,
    rationality, length, event_tags, llm_enhanced,
    -- Engagement
    likes, replies, reposts, views, engagement_score,
    -- Metadata
    source_tier, author_weight, ranking_score,
    -- LLM enhanced (dual values)
    llm_sentiment, llm_topic, llm_rationality, llm_summary,
    llm_fear_score, llm_fomo_score,
    UNIQUE(tweet_id, extracted_at)
)

rss_features (
    url, title, source, published_at, processed_at,
    -- Sourcing
    sourcing_category, matched_keywords, matched_count, is_macro,
    -- Ranking
    vader_score, finbert_score, sentiment, sentiment_direction,
    divergence, is_divergent, score,
    -- Result
    result,       -- 'passed' | 'skipped'
    skip_reason,
    UNIQUE(url, processed_at)
)

engagement_snapshots (  -- TODO: write logic pending
    tweet_id, snapshot_at,
    likes, replies, reposts, views,
    delta_likes, delta_replies, delta_reposts, delta_views,
    UNIQUE(tweet_id, snapshot_at)
)
```

---

## Core Interfaces

```python
@dataclass
class FilterResult:
    passed: list[AnalyzedItem]   # Pass filter → push
    skipped: list[AnalyzedItem]  # Filtered out → still mark pushed

class NewsItem:    # src/collectors/base.py — raw collector output
class AnalyzedItem: # src/analyzers/base.py — pipeline unit with score/raw_data
class Tweet:       # src/collectors/twitter_collector.py — Twitter output
```

---

## Configuration

All mutable data in `config/`, loaded via abstraction layer (`load_keywords()` / `load_accounts()` / `load_config()`).

| File | Content |
|------|---------|
| `config/keywords.yaml` | shill blacklist + Tier1 + Tier2 + RSS keywords |
| `config/twitter_accounts.yaml` | 5 groups, 17 accounts + per-account weight |
| `config/rss_sources.yaml` | RSS source URLs + categories |
| `config/prompts.md` | All LLM maintenance prompts |

---

## Scheduling (APScheduler)

```
scheduler.py --mode dev   = raw_data (collector.py) + daily_report
scheduler.py --mode prod  = full_pipeline (main.py) + daily_report

raw_data and full_pipeline are mutually exclusive.
full_pipeline includes collection — running both wastes API calls.
```

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| RSS / Twitter separate pipelines | Completely different scoring logic |
| Feature Extraction as independent stage | Pure computation, decoupled from Ranking |
| LLM is buffer, not required | System runs without LLM; enhancement is optional |
| Dual values coexist | Rule baseline + LLM values saved together for comparison |
| Config externalized to YAML | Zero hardcoding, each file has embedded maintenance prompt |
| Independent features.db | Separate from twitter.db for long-term storage and backtesting |
| Skipped items also marked pushed | Prevent reprocessing |
| RSS: both passed+skipped persisted | Audit trail + filtering statistics |
| Whitelist pass-through | Crypto media is almost always relevant |
| Pre-compiled regex | Compile once at module load, zero runtime cost |
