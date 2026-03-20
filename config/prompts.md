# FinNews 项目 Prompt 集合

> 本文件集中管理所有 LLM prompt。
> 每个 config 文件的头部注释包含该文件对应的维护 prompt 摘要，完整版见本文件。
> 使用方法：将对应 prompt 复制给 Grok / Claude，附上当前文件内容，获取更新建议。

---

## 1. Keywords 维护 Prompt (keywords.yaml)

### 目的
定期（建议每 1-2 周）让 Grok 审查并更新关键词库，确保：
- 覆盖当前热点话题和新兴概念
- 淘汰已过时或不再活跃的术语
- 黑名单跟上最新 shill/scam 话术
- 各分类间保持平衡覆盖

### Prompt

```
你是一个加密货币与宏观金融关键词维护助手。

我有一个用于金融新闻/推文 Sourcing 过滤的关键词配置文件（YAML 格式），分为以下层级：
- shill_blacklist: 垃圾/骗局/广告词，命中即过滤
- tier1_event_keywords: 高冲击事件词（按 security/regulatory/etf/macro_event/whale/institutional 分类），仅打标签不过滤
- tier2_topic_keywords: 主题相关词（按 crypto/macro/sentiment 分类），命中才通过过滤
- rss_keywords: RSS 新闻过滤词（按 macro/regulation/security/crypto 分类），命中才通过

请基于以下当前配置 + 最近 7 天的加密/宏观热点，执行以下任务：

1. **过时词检测**: 找出已不再活跃或过时的关键词（如已死项目、过期事件），建议移除
2. **缺失词补充**: 找出当前热门但未覆盖的关键词，按分类建议添加（每类 3-8 个）
3. **黑名单更新**: 检查最新 shill/scam 话术，补充 shill_blacklist
4. **分类校正**: 检查是否有放错分类的词
5. **覆盖度评估**: 给出各分类的覆盖度评分 (1-10) 和改进建议

输出格式（YAML 片段，可直接合并）：

```yaml
# === 建议移除 ===
remove:
  tier2_topic_keywords.crypto:
    - "过时词1"
  shill_blacklist:
    - "过时词2"

# === 建议添加 ===
add:
  tier1_event_keywords.security:
    - "新事件词1"
  tier2_topic_keywords.crypto:
    - "新主题词1"
  shill_blacklist:
    - "新骗局词1"
  rss_keywords.crypto:
    - "新RSS词1"

# === 覆盖度评估 ===
coverage:
  shill_blacklist: 8/10
  tier1_event_keywords: 7/10
  tier2_topic_keywords: 8/10
  rss_keywords: 7/10
  notes: "..."
```

当前配置文件内容：
---
[粘贴 keywords.yaml 完整内容]
---
```

---

## 2. Twitter 账号维护 Prompt (twitter_accounts.yaml)

### 目的
定期（建议每 2-4 周）让 Grok 审查并更新监控账号列表，确保：
- 账号仍然活跃且发布相关内容
- 权重反映当前影响力
- 覆盖新兴重要 KOL
- 各组功能明确、不重叠

### Prompt

```
你是一个加密货币 Twitter KOL 监控列表维护助手。

我有一个用于加密/宏观舆情监控的 Twitter 账号配置文件（YAML 格式），分为 5 组：
- risk_detectors: 风险探测器（链上侦探、安全团队、突发消息源），高权重
- narrative_hype: 叙事指挥官（顶级影响力人物、行业风向标）
- onchain_data: 链上聪明钱（链上数据分析、机构级数据）
- macro_regime: 宏观与快讯（宏观环境、机构快讯）
- technical_logic: 技术共识（TA 分析师、趋势判断）

每个账号格式: `- handle: weight`，weight 范围 1.0-10.0，反映信息可靠度和影响力。

请基于以下当前配置 + X 平台实时数据，执行以下任务：

1. **活跃度检查**: 检查每个账号最近 7 天的发帖频率，标记不活跃账号（< 2 条/周）
2. **相关性检查**: 检查每个账号最近内容是否仍以加密/宏观为主，标记偏题账号
3. **权重校准**: 基于当前粉丝数、互动量、信息质量，建议权重调整
4. **新 KOL 发现**: 在每个组的功能范围内，搜索 3-5 个当前活跃且有影响力的候选账号
   - 搜索条件: 粉丝 > 50K, 最近 7 天有 > 5 条相关推文, 互动量高
5. **组平衡评估**: 各组账号数量是否合理（建议每组 3-5 个）

输出格式：

```yaml
# === 活跃度 & 相关性检查 ===
status:
  zachxbt: {active: true, relevant: true, recent_topics: ["hack investigation", "scam exposure"]}
  elonmusk: {active: true, relevant: partial, note: "大量非加密内容，但关键推文影响力极大"}
  ...

# === 权重调整建议 ===
weight_changes:
  - handle: xxx
    current: 8.0
    suggested: 9.0
    reason: "近期影响力显著提升"

# === 新 KOL 候选 ===
candidates:
  risk_detectors:
    - handle: xxx
      weight: 8.5
      followers: 150K
      reason: "安全审计专家，近期多次首发漏洞预警"
  narrative_hype:
    - handle: xxx
      weight: 8.0
      followers: 500K
      reason: "..."

# === 建议移除 ===
remove:
  - handle: xxx
    reason: "已 30 天未发帖"
```

当前配置文件内容：
---
[粘贴 twitter_accounts.yaml 完整内容]
---
```

---

## 3. RSS 源维护 Prompt (rss_sources.yaml)

### 目的
定期（建议每 1-3 月）审查 RSS 源可用性和覆盖度。

### Prompt

```
你是一个金融新闻 RSS 源维护助手。

我有一个用于加密货币和宏观金融新闻采集的 RSS 源配置（YAML 格式），分为：
- policy: 政策与宏观新闻（Federal Reserve, SEC 等）
- crypto: 加密行业新闻（CoinDesk, Cointelegraph 等）

请执行以下任务：

1. **可用性检查**: 验证每个 RSS URL 是否仍然可访问
2. **覆盖度评估**: 评估当前源对以下领域的覆盖度
   - 加密行业新闻
   - 美国宏观政策
   - 全球宏观经济
   - 监管动态
3. **新源推荐**: 推荐 5-10 个高质量 RSS 源，附 URL 和分类建议
   - 优先: Investing.com, CNBC, Yahoo Finance, CryptoPanic, DeFi Llama
   - 格式: `{name, url, category, reason}`
4. **分类建议**: 是否需要新增分组（如 defi, exchange 等）

当前配置文件内容：
---
[粘贴 rss_sources.yaml 完整内容]
---
```

---

## 4. Feature Extraction LLM Prompt (feature_extractor.py 内置)

### 用途
每条推文经过 Grok-3-fast 分析，提取结构化特征（含 fear/fomo 双维度）。

### 当前 Prompt (内嵌于代码, v4)

```
Analyze this crypto/finance tweet. Return ONLY valid JSON, no markdown:
{
  "sentiment": <float -1.0 to 1.0, bearish to bullish>,
  "fear_score": <float 0.0 to 1.0, 0=calm, 1=extreme panic/capitulation>,
  "fomo_score": <float 0.0 to 1.0, 0=rational, 1=extreme greed/urgency>,
  "topic": "<one of: regulation, security, etf, macro, defi, market, exchange, geopolitical, memecoin, mining, other>",
  "rationality": <float 0.0 to 1.0, 0=pure hype, 1=data-driven analysis>,
  "summary": "<one line Chinese summary, max 50 chars>"
}

Tweet by @{author}:
{text}
```

### 字段说明
- **fear_score**: 恐慌程度 — 衡量文本中的恐慌/崩溃信号（crash, dump, capitulation, rug pull 等），与 sentiment 正交
- **fomo_score**: 贪婪急迫度 — 衡量 FOMO/炒作信号（moon, lambo, guaranteed, don't miss 等），与 rationality 互补
- **topic**: 扩展至 11 类 — 合并 hack→security，新增 exchange/geopolitical/memecoin/mining
- 规则引擎 baseline + LLM 增强值并存，不覆盖

---

## 5. Twitter 采集 Prompt (twitter_collector.py 内置)

### 用途
通过 Grok x_search 获取指定账号的最近推文。

### 当前 Prompt (内嵌于代码)

```
获取 @{handle} 最近 {count} 条推文。
以 JSON 格式返回，不要 markdown 代码块：
{"posts": [{"id": "推文ID", "author": {"name": "名称", "handle": "用户名"},
"timestamp": "时间", "content": "内容",
"engagement": {"likes": 0, "reposts": 0, "replies": 0, "views": 0}}]}
```

---

## 使用流程

1. 打开 Grok (grok.x.ai) 或 API
2. 复制对应 prompt
3. 将当前 config 文件内容粘贴到 prompt 末尾的占位符处
4. 执行并审查 Grok 的建议
5. 手动或批量更新 config 文件
6. 运行 `python main.py -q --mock` 验证无报错

> TODO: 未来通过 Notion API 自动化此流程
