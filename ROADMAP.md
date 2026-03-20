# FinNews Roadmap

> Last updated: 2026-02-09 | Current: v5.4 (local Python + SQLite + Telegram)

## Current Stack Assessment

**What works well:**
- Python ML ecosystem (VADER, FinBERT, transformers) — no other language competes here
- SQLite for single-user local deployment — zero ops overhead
- APScheduler for current scheduling needs — sufficient for dev/prod modes
- Telegram as push channel — low latency, mobile-friendly

**What will break at scale:**
- SQLite: no concurrent writes, no remote access, no query analytics
- Local-only: machine off = no data collection
- Single LLM: Grok rate limits or downtime = no enhancement
- No monitoring: pipeline failures are silent unless you watch terminal
- Manual deployment: every change requires SSH/RDP + git pull + restart

---

## Evolution Path

### Phase 3: Containerization + Cloud

**Goal**: 24/7 reliable data collection, survive machine restarts

**Docker** (recommended first step):
```
docker-compose.yml
├── app        Python app + APScheduler
├── db         PostgreSQL (replace SQLite)
└── (future)   Redis, frontend, etc.
```

Why Docker:
- Reproducible environment — eliminates "works on my machine"
- One-command deploy: `docker-compose up -d`
- Makefile wraps common commands: `make dev`, `make prod`, `make logs`
- Natural stepping stone to any cloud platform

**Makefile** (even without Docker, useful now):
```makefile
dev:        # scheduler dev mode
prod:       # scheduler prod mode
collect:    # one-shot collection
report:     # daily report
test:       # pytest
lint:       # ruff + mypy
build:      # docker build
deploy:     # docker push + restart
```

**Cloud options** (sorted by simplicity):

| Platform | Pros | Cons | Cost |
|----------|------|------|------|
| Railway | Git push deploy, free tier | Limited free hours | $5/mo hobby |
| Fly.io | Global edge, good free tier | Learning curve | $5/mo+ |
| Hetzner VPS | Cheapest, full control | Manual setup | €4/mo |
| AWS ECS/Fargate | Enterprise-grade | Complex, expensive | $15/mo+ |
| GCP Cloud Run | Auto-scale to zero | Cold start latency | Pay per use |

**Recommended**: Hetzner VPS + Docker Compose for cost efficiency, or Railway for zero-ops.

**Database migration**: SQLite → PostgreSQL
- `features.db` → PostgreSQL `features` schema
- Enables: concurrent access, remote queries, proper backups, analytics
- Migration tool: `alembic` for schema versioning
- Alternative: keep SQLite for dev, PostgreSQL for prod (same ORM layer)

---

### Phase 4: Frontend Dashboard

**Goal**: visual monitoring, no terminal needed

**Option A — Streamlit** (fastest to build, ~2 days):
- Single Python file, reuse existing DailyReportGenerator
- Charts: topic distribution, sentiment trend, fear/fomo heatmap
- Tables: latest features, pipeline run history
- Limitation: single-user, not production-grade

**Option B — FastAPI + React/Next.js** (production path):
- FastAPI backend: REST API over features.db/PostgreSQL
- React frontend: real-time dashboard, interactive filters
- More effort, but proper SPA with auth and multi-user support

**Option C — Grafana + Prometheus** (monitoring focus):
- Best for pipeline health monitoring (latency, error rates, throughput)
- Not ideal for domain-specific analytics (topic, sentiment)
- Works well alongside Option A or B for ops monitoring

**Recommended**: Start with Streamlit for immediate value. Migrate to FastAPI + React when multi-user or public access is needed.

---

### Phase 5: Multi-LLM Router

**Goal**: resilience + cost optimization + quality comparison

**Current**: Grok-3-fast only (single point of failure)

**Architecture**:
```
LLMRouter
├── Primary:   Grok-3-fast  (cheapest, fastest)
├── Fallback:  Claude Haiku  (quality upgrade on failure)
├── Premium:   Claude Sonnet  (for URGENT items only)
└── Config:    model selection per task type
```

**Implementation options**:

| Tool | Approach | Pros | Cons |
|------|----------|------|------|
| LiteLLM | Unified API proxy | Drop-in replacement, 100+ providers | Extra dependency |
| Custom router | Own fallback logic | Full control, no dependency | More code to maintain |
| OpenRouter | API gateway | One API key, many models | External dependency |

**Recommended**: LiteLLM — unified `completion()` call, swap models via config. Already compatible with our `openai` SDK usage.

**Per-task model routing**:
- Feature extraction (batch): Grok-fast (cheapest)
- Daily report summary: Claude Haiku (better Chinese)
- URGENT deep analysis: Claude Sonnet (highest quality)
- Config maintenance prompts: any model via prompts.md

---

### Phase 6: Data Source Expansion

**News APIs**:

| Source | Type | Cost | Value |
|--------|------|------|-------|
| CryptoPanic API | Aggregated crypto news | Free tier | Pre-filtered, sentiment tagged |
| NewsAPI | General news | Free 100 req/day | Broad macro coverage |
| Messari API | Crypto research | Free tier | Institutional-grade analysis |
| DeFi Llama | Protocol data | Free | TVL, yield, protocol metrics |

**On-chain data** (complement news with numbers):

| Source | Data | Use Case |
|--------|------|----------|
| Dune Analytics API | Custom SQL queries | Whale movements, DEX volume |
| Nansen API | Labeled wallets | Smart money tracking |
| Glassnode | On-chain metrics | BTC/ETH fundamental indicators |

**Social** (L3 retail layer):

| Source | Challenge | Approach |
|--------|-----------|----------|
| Reddit API | Rate limits, noise | r/cryptocurrency, r/bitcoin, keyword filter |
| Telegram channels | No official API for public channels | Telethon library, specific channels |
| Discord | Bot-based access | Specific server monitoring |

**Architecture impact**: Each new source = new Collector subclass + optional Filter. Existing pipeline pattern (`collect → persist → filter → push`) scales naturally.

---

### Phase 7: Advanced Scheduling

#### 7.1 Per-Group Interval Scheduling

**Goal**: 每个 Twitter 组按自己的 `interval_minutes` 独立采集，而非全局统一间隔

**当前状态**: `interval_minutes` 字段已写入 YAML，但 scheduler.py 使用全局 `--interval` 参数，
所有组在同一次 `full_pipeline` / `raw_data` 调用中一起采集。

**架构设计**:

```
scheduler.py
├── 读取 twitter_accounts.yaml
├── 为每个组创建独立 APScheduler Job
│   vol_micro_core:    每 20min 触发
│   systemic_risk:     每 30min 触发
│   liquidity_clusters: 每 60min 触发
│   narrative_macro:   每 120min 触发
├── RSS 保持独立间隔 (如 60min)
├── 下游 pipeline 按需触发（有新数据就跑）
└── daily_report: 保持每天一次
```

**关键改动点**:

1. `collector.py` 需要支持 `--group <group_name>` 参数，只采集指定组
2. `scheduler.py` 从 YAML 动态读取 interval_minutes 注册多个 job
3. `twitter_pipeline.py` 需要能处理单组增量数据（当前已支持）

**伪代码** (scheduler.py 改造):

```python
from src.utils.config import load_accounts

def build_per_group_jobs(scheduler, mode):
    """为每个 Twitter 组注册独立定时 job"""
    accounts_config = load_accounts()

    for group_name, group_cfg in accounts_config.items():
        interval = group_cfg.get("interval_minutes", 30)
        n_accounts = len(group_cfg.get("accounts", []))

        if mode == "dev":
            # dev: 只采集，不跑 pipeline
            job_fn = run_collector_group
        else:
            # prod: 采集 + pipeline + 推送
            job_fn = run_pipeline_group

        scheduler.add_job(
            job_fn,
            "interval",
            args=[group_name],
            minutes=interval,
            id=f"twitter_{group_name}",
            name=f"{group_name} every {interval}min ({n_accounts} accounts)",
            misfire_grace_time=interval * 60,
        )

def run_collector_group(group_name: str):
    """采集单个组: python scripts/collector.py --group <name>"""
    subprocess.run([PYTHON, "scripts/collector.py", "--group", group_name], ...)

def run_pipeline_group(group_name: str):
    """采集 + pipeline 单个组: python main.py --group <name>"""
    subprocess.run([PYTHON, "main.py", "--group", group_name], ...)
```

**RSS 独立调度** (可选):
```python
# RSS 单独一个 job，不跟 Twitter 组绑定
scheduler.add_job(run_rss_pipeline, "interval", minutes=60, id="rss_pipeline")
```

**实现优先级**: Medium — 当前全局间隔够用，但组增多后（>6组）差异化调度能节省 API quota

---

#### 7.2 Advanced Scheduling Tools

**When APScheduler isn't enough**:
- Complex DAG dependencies (e.g., "collect → wait for all sources → aggregate → push")
- Retry with exponential backoff
- Visual pipeline monitoring
- Distributed execution across multiple workers

**Options**:

| Tool | Complexity | Best For |
|------|-----------|----------|
| APScheduler (current) | Low | Simple interval/cron jobs |
| Prefect | Medium | Python-native DAGs, good UI |
| Airflow | High | Enterprise, complex workflows |
| Temporal | High | Long-running workflows, retries |
| Celery + Beat | Medium | Distributed task queue |

**Recommended**: Prefect when DAG complexity grows. Has a free cloud tier for monitoring, Python-native (no YAML configs), decorators-based.

---

### Phase 8: Config Externalization (YAML → UI/Notion)

**Goal**: 让非技术用户能在浏览器中直接编辑所有配置（关键词、账号、权重、阈值），
无需碰 YAML 文件或 Git

**当前所有 YAML 配置项清单**:

| 文件 | 可编辑项 | 典型操作 |
|------|---------|---------|
| `keywords.yaml` | topics 枚举 | 新增/删除 topic |
| | tier1_topic_mapping | 分类→topic 映射 |
| | tier2_topic_mapping | 分类→topic 映射 |
| | shill_blacklist | 添加垃圾词 |
| | tier1_event_keywords | 添加/删除事件词 |
| | tier2_topic_keywords | 添加/删除主题词 |
| | rss_keywords | 添加/删除 RSS 过滤词 |
| `twitter_accounts.yaml` | 组名 + interval | 新增组、改间隔 |
| | accounts + weight | 加/删账号、调权重 |
| `rss_sources.yaml` | RSS 源 URL + category | 加/删 RSS 源 |
| `daily_report.py` | AI_TRIGGER 阈值 | 调整异常触发灵敏度 |

**架构设计 — 三层方案**:

```
┌─────────────────────────────────────┐
│  Layer 1: UI / Notion (用户编辑)     │
│  ├─ Option A: Notion Database        │
│  ├─ Option B: Streamlit Admin Panel  │
│  └─ Option C: FastAPI + React        │
├─────────────────────────────────────┤
│  Layer 2: Config Sync Service        │
│  ├─ 定时拉取 (Notion API / DB)       │
│  ├─ 校验 (Pydantic schema)           │
│  ├─ 写入 YAML (保持文件格式)          │
│  └─ 热重载通知 (signal / flag)       │
├─────────────────────────────────────┤
│  Layer 3: YAML Files (代码读取)       │
│  └─ load_keywords() / load_accounts()│
│     保持不变，始终读本地 YAML          │
└─────────────────────────────────────┘
```

**关键设计原则**:
- YAML 仍是 Single Source of Truth，UI 只是编辑入口
- 所有 `load_*()` 函数不改，新增 `sync_*()` 函数写 YAML
- Pydantic model 做校验（防止 UI 写入非法值）
- 变更后可选自动 git commit（审计轨迹）

**Option A — Notion Database（推荐先行）**:

```
Notion Databases:
├─ "Keywords Config"
│   columns: category | keyword | tier | topic_mapping | active
│   一行 = 一个关键词
├─ "Twitter Accounts"
│   columns: group | handle | weight | interval_min | active | notes
│   一行 = 一个账号
└─ "RSS Sources"
    columns: module | name | url | category | active
    一行 = 一个 RSS 源
```

Sync 伪代码:
```python
# scripts/sync_notion.py — 可手动跑或 scheduler 定时跑
from notion_client import Client

def sync_twitter_accounts():
    """Notion DB → twitter_accounts.yaml"""
    notion = Client(auth=os.getenv("NOTION_TOKEN"))
    rows = notion.databases.query(database_id=TWITTER_DB_ID)

    # 按 group 聚合
    groups = {}
    for row in rows["results"]:
        props = row["properties"]
        if not props["active"]["checkbox"]:
            continue
        group = props["group"]["select"]["name"]
        handle = props["handle"]["title"][0]["plain_text"]
        weight = props["weight"]["number"]
        interval = props["interval_min"]["number"]
        groups.setdefault(group, {"interval_minutes": interval, "accounts": []})
        groups[group]["accounts"].append({handle: weight})

    # 写入 YAML (保留注释头)
    write_yaml_with_header("config/twitter_accounts.yaml", groups)

def sync_keywords():
    """Notion DB → keywords.yaml"""
    # 类似逻辑，按 tier + category 聚合
    ...
```

**Option B — Streamlit Admin Panel（纯 Python，快速原型）**:

```python
# frontend/config_admin.py
import streamlit as st
from src.utils.config import load_keywords, load_accounts

st.title("FinNews Config Editor")

tab1, tab2, tab3 = st.tabs(["Keywords", "Twitter Accounts", "RSS Sources"])

with tab2:
    accounts = load_accounts()
    for group, cfg in accounts.items():
        with st.expander(f"{group} (every {cfg['interval_minutes']}min)"):
            new_interval = st.number_input("Interval", value=cfg["interval_minutes"], key=f"{group}_int")
            for entry in cfg["accounts"]:
                handle, weight = next(iter(entry.items()))
                col1, col2, col3 = st.columns([3, 1, 1])
                col1.text(f"@{handle}")
                new_w = col2.number_input("W", value=weight, key=f"{group}_{handle}")
                col3.button("X", key=f"del_{group}_{handle}")  # 删除

    if st.button("Save"):
        # 写回 YAML
        ...
```

**实现优先级**:

```
Phase 8a (低成本)  Pydantic schema 定义所有配置结构 → 校验
Phase 8b (推荐)    Notion Database + sync_notion.py (手动/定时 sync)
Phase 8c (可选)    Streamlit Admin Panel (本地可视化编辑)
Phase 8d (后期)    FastAPI + React (生产级多用户编辑)
```

---

## Language & Technology Considerations

### Should we switch languages?

**Short answer: No. Python stays as the core.**

| Language | Where it helps | Where it doesn't |
|----------|---------------|-------------------|
| Python | ML/NLP (VADER, FinBERT, transformers), rapid prototyping, rich ecosystem | High-concurrency crawling |
| Go | High-perf concurrent collector, CLI tools | No ML ecosystem, rebuilds everything |
| TypeScript | Frontend (React/Next.js), full-stack if using Node | No ML, worse for data processing |
| Rust | Extremely fast data processing | Overkill, slow development |

**Practical recommendation**:
- **Keep Python** for all backend, ML, and pipeline code
- **Add TypeScript** only if building a React frontend (Phase 4 Option B)
- **Go/Rust** only makes sense for a dedicated high-throughput collector microservice (50+ sources with sub-second latency) — not our current need

### Key tools to adopt

| Tool | When | Why |
|------|------|-----|
| Docker + Compose | Phase 3 | Reproducible deployment |
| Makefile | Now | Standardize commands, even without Docker |
| Alembic | Phase 3 (PostgreSQL) | Database schema versioning |
| Pydantic | Refactor | Validate configs, API responses, data models |
| LiteLLM | Phase 5 | Multi-LLM routing |
| Pytest fixtures | Now | Better test organization |

---

## Recommended Execution Order

```
Now          Makefile (standardize commands)
             Pydantic models (type safety for configs)

Phase 3      Docker + docker-compose
             Cloud VPS deployment (Hetzner/Railway)
             PostgreSQL migration

Phase 4      Streamlit dashboard (quick win)
             → Later: FastAPI + React (if needed)

Phase 5      LiteLLM multi-model router
             Per-task model routing config

Phase 6      CryptoPanic API (easy, high value)
             On-chain data (Dune/DeFi Llama)

Phase 7.1    Per-group interval scheduling (APScheduler)
             collector.py --group support
Phase 7.2    Prefect (when pipeline DAGs get complex)

Phase 8a     Pydantic schema for all config files
Phase 8b     Notion Database + sync_notion.py
Phase 8c     Streamlit config admin panel (optional)
Phase 8d     FastAPI + React config editor (production)
```

---

## File Placement Reference

When implementing these phases, new files go here:

```
├── Makefile                    # Phase 3: standardize commands
├── Dockerfile                  # Phase 3: container definition
├── docker-compose.yml          # Phase 3: service orchestration
├── alembic/                    # Phase 3: DB migrations
├── frontend/                   # Phase 4: Streamlit or React app
├── src/
│   ├── llm/
│   │   └── router.py          # Phase 5: LLM router
│   ├── collectors/
│   │   ├── cryptopanic.py     # Phase 6: new sources
│   │   ├── onchain.py
│   │   └── reddit.py
│   └── models/
│       └── schemas.py         # Phase 8a: Pydantic config models
├── scripts/
│   └── sync_notion.py         # Phase 8b: Notion → YAML sync
├── frontend/
│   └── config_admin.py        # Phase 8c: Streamlit config editor
└── deploy/
    ├── nginx.conf             # Phase 3: reverse proxy
    └── .env.production        # Phase 3: prod config
```
