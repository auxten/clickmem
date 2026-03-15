# ClickMem CEO Brain 重设计方案

## 一、设计哲学

**核心转变**：从"AI 编码助手的记忆补丁" → "一人公司 CEO 的认知操作系统"

**类比**：如果 Claude/GPT 是 CEO 的"手和嘴"（执行和表达），clickmem 就是 CEO 的"前额叶"（记忆、判断框架、决策模式）。它不做推理，但让推理者拥有完整的认知基底。

**三个设计原则**：
1. **Project-first** — 一切知识都挂在项目维度上（或标记为全局），因为 CEO 的世界观以项目为核心
2. **Opinion over Data** — 不只记事实，更要提炼观点、原则、方法论。"我们用 React" 是事实，"我选 React 因为生态成熟、招人容易" 是认知
3. **Proactive Injection** — 不等被问，在 agent 启动时就把最相关的 CEO 认知注入上下文，让 agent 天然带有 CEO 视角

---

## 二、数据架构重设计

### 2.1 废弃当前三层模型，改为五类知识实体

当前的 L0/L1/L2 层次是面向"记忆保留时长"设计的，对 CEO 场景不够用。新模型按**知识类型**划分：

```
┌─────────────────────────────────────────────────────────────┐
│                    CEO Knowledge Graph                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────┐    belongs_to    ┌──────────┐                  │
│  │ Decision │───────────────→ │ Project  │                  │
│  └─────────┘                  └──────────┘                  │
│       │                            ↑                         │
│   led_to                      part_of                        │
│       ↓                            │                         │
│  ┌─────────┐               ┌──────────┐                    │
│  │ Outcome │               │ Principle│ (全局/项目级)       │
│  └─────────┘               └──────────┘                    │
│                                    ↑                         │
│                              derived_from                    │
│                                    │                         │
│                             ┌──────────┐                    │
│                             │ Episode  │ (对话事件流)       │
│                             └──────────┘                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### Entity 1: `projects` — 项目组合

```sql
CREATE TABLE projects (
    id String,
    name String,                    -- "ClickMem", "OpenClaw", "MyShop"
    description String,             -- 一句话描述
    status String,                  -- ideation | building | launched | maintaining | sunset
    vision String,                  -- 产品愿景
    target_users String,            -- 目标用户画像
    north_star_metric String,       -- 北极星指标
    tech_stack Array(String),       -- ["Python", "chDB", "FastAPI"]
    repo_url String,                -- Git 仓库地址
    related_files Array(String),    -- ["CLAUDE.md", "AGENTS.md", "README.md"]
    metadata String,                -- JSON: 任意扩展字段
    embedding Array(Float32),       -- 256-dim，用于语义匹配
    created_at DateTime64(3, 'UTC'),
    updated_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (id)
```

**为什么需要**：CEO 的第一个问题永远是"这是哪个项目"。没有项目维度，所有知识都是散乱的碎片。

#### Entity 2: `decisions` — 决策日志

```sql
CREATE TABLE decisions (
    id String,
    project_id String,              -- 关联项目，空=全局决策
    title String,                   -- "选择 chDB 作为存储引擎"
    context String,                 -- 决策背景：当时面临什么问题
    choice String,                  -- 做了什么选择
    reasoning String,               -- 为什么这样选（tradeoffs）
    alternatives String,            -- 考虑过但没选的方案
    outcome String,                 -- 结果如何（可后续补充）
    outcome_status String,          -- pending | validated | invalidated | unknown
    domain String,                  -- product | tech | design | marketing | ops
    tags Array(String),
    source_episodes Array(String),  -- 关联的 episode IDs（溯源）
    embedding Array(Float32),
    created_at DateTime64(3, 'UTC'),
    updated_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (id)
```

**为什么需要**：决策是 CEO 最有价值的输出。记住"做了什么"不够，要记住"为什么这样做"和"结果如何"。下次遇到类似决策时，agent 可以参考历史。

#### Entity 3: `principles` — 原则与方法论

```sql
CREATE TABLE principles (
    id String,
    project_id String,              -- 空=全局原则
    content String,                 -- "产品设计优先于技术实现"
    domain String,                  -- product | tech | design | marketing | ops | management
    confidence Float32,             -- 0-1，越高越稳定
    evidence_count UInt32,          -- 被多少个 decision/episode 支撑
    source_decisions Array(String), -- 从哪些决策中提炼出来
    embedding Array(Float32),
    is_active UInt8 DEFAULT 1,
    created_at DateTime64(3, 'UTC'),
    updated_at DateTime64(3, 'UTC')
) ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (id)
```

**为什么需要**：原则是 CEO 认知的精华。当 agent 需要做判断时，注入相关原则 = 让 agent 带着 CEO 的价值观行动。`confidence` 和 `evidence_count` 让原则有"重量"——被反复验证的原则权重更高。

#### Entity 4: `episodes` — 事件流（原 L1 episodic 的演进）

```sql
CREATE TABLE episodes (
    id String,
    project_id String,              -- 关联项目
    session_id String,              -- 对话 session
    agent_source String,            -- claude_code | cursor | openclaw | other
    content String,                 -- 提炼后的事件摘要
    user_intent String,             -- 用户在这次对话中想达成什么
    key_outcomes Array(String),     -- 这次对话的关键产出
    domain String,                  -- product | tech | design | marketing | ops
    tags Array(String),
    entities Array(String),
    raw_id String,                  -- 溯源到 raw_transcripts
    embedding Array(Float32),
    created_at DateTime64(3, 'UTC')
) ENGINE = MergeTree()
ORDER BY (created_at, id)
TTL created_at + INTERVAL 180 DAY
```

**变化**：从"记住对话内容"变为"提炼对话事件"。重点是 `user_intent`（CEO 想干什么）和 `key_outcomes`（干成了什么）。

#### Entity 5: `raw_transcripts` — 原始对话（保留，微调）

```sql
CREATE TABLE raw_transcripts (
    id String,
    session_id String,
    project_id String,              -- 新增：关联项目
    agent_source String,            -- 新增：来源 agent
    content String,                 -- 原始对话文本（以用户输入为主）
    char_count UInt32,
    is_processed UInt8 DEFAULT 0,
    processed_at DateTime64(3, 'UTC') DEFAULT '1970-01-01',
    created_at DateTime64(3, 'UTC')
) ENGINE = MergeTree()
ORDER BY (created_at, id)
TTL created_at + INTERVAL 90 DAY
```

### 2.2 删除的概念

| 删除 | 原因 |
|------|------|
| `layer` 字段 (working/episodic/semantic) | 被实体类型替代 |
| `memories` 单表 | 拆分为 projects/decisions/principles/episodes |
| L0 Working Memory | 改为动态组装（见 Context Engine） |
| `category` 字段 | 被 `domain` + 实体类型替代 |
| `access_count` / time decay | 简化：principles 用 confidence，episodes 用 TTL，decisions 永不过期 |

---

## 三、核心引擎重设计

### 3.1 Ingestion Pipeline（数据摄入）

```
对话来源                     Ingestion Pipeline                    Knowledge Graph
─────────                   ──────────────────                    ───────────────

Claude Code ──hook──→ ┌─────────────────────┐
Cursor ──────hook──→ │  1. Raw Capture      │──→ raw_transcripts
OpenClaw ────plugin─→ │     (用户输入优先)    │
手动导入 ────CLI────→ └─────────────────────┘
                              │
                              ▼
                     ┌─────────────────────┐
                     │  2. Project Detect   │──→ 识别属于哪个项目
                     │     (路径/上下文)     │    (或创建新项目)
                     └─────────────────────┘
                              │
                              ▼
                     ┌─────────────────────┐     ┌→ episodes
                     │  3. Multi-pass      │─────┤→ decisions
                     │     Extraction      │─────┤→ principles (候选)
                     │     (远程 LLM)       │     └→ project updates
                     └─────────────────────┘
                              │
                              ▼
                     ┌─────────────────────┐
                     │  4. Dedup & Merge    │──→ 去重、合并、更新
                     │     (向量+LLM)       │
                     └─────────────────────┘
```

#### Step 1: Raw Capture — 数据过滤策略

核心原则：**以用户输入为主**

```python
def filter_conversation(messages: list[dict]) -> str:
    """
    优先级：
    1. 用户输入 — 全部保留
    2. Agent 的关键反馈 — 保留（决策建议、错误信息、确认结果）
    3. Agent 的代码输出 — 只保留摘要（文件名+变更描述）
    4. Agent 的解释性文本 — 丢弃（冗长的分析、重复的确认）
    """
```

**按 agent 来源的适配**：

| 来源 | 获取方式 | 初次导入 | 增量收集 |
|------|---------|---------|---------|
| Claude Code | HTTP hook (Stop 事件) | 读取 `~/.claude/projects/` 下的历史 | hook 自动触发 |
| Cursor | hooks.json (afterAgentResponse) | 读取 Cursor 历史目录 | hook 自动触发 |
| OpenClaw | Plugin (agent_end) | `memory import-openclaw` | plugin 自动触发 |
| 手动 | CLI `memory ingest` | N/A | 用户主动 |

#### Step 2: Project Detection — 项目自动识别

```python
def detect_project(raw_content: str, cwd: str, session_meta: dict) -> str:
    """
    策略（按优先级）：
    1. session_meta 中明确指定了 project_id
    2. cwd 匹配已知项目的 repo 路径
    3. 内容中提及已知项目名
    4. LLM 判断（基于内容语义 + 已有项目列表）
    5. 标记为 "unassigned"，等待后续归类
    """
```

#### Step 3: Multi-pass Extraction — CEO 视角的提取

**与现有 extractor.py 的核心区别**：当前只提取 facts，新版提取**结构化的 CEO 知识**。

```python
EXTRACTION_PROMPT = """
你是一个一人公司 CEO 的认知助手。分析以下对话，提取以下类型的知识：

## 1. Episodes（事件）
对话中发生了什么？CEO 想达成什么目标？结果如何？
输出：{ "type": "episode", "content": "...", "user_intent": "...", "key_outcomes": [...], "domain": "..." }

## 2. Decisions（决策）
CEO 做了哪些选择？为什么？考虑过哪些替代方案？
输出：{ "type": "decision", "title": "...", "context": "...", "choice": "...", "reasoning": "...", "alternatives": "...", "domain": "..." }

## 3. Principles（原则候选）
对话中是否体现了 CEO 的某些一贯性偏好或方法论？
（只有高置信度的才输出，宁缺勿滥）
输出：{ "type": "principle", "content": "...", "domain": "...", "confidence": 0.0-1.0 }

## 4. Project Updates（项目状态更新）
对话是否改变了项目的状态、技术栈、目标等？
输出：{ "type": "project_update", "field": "...", "new_value": "..." }

注意：
- 以用户（CEO）的视角提取，不是 agent 的视角
- 重点关注 WHY，不只是 WHAT
- Decision 的 reasoning 是最有价值的部分
- Principle 要求高置信度，不确定的不要提取
"""
```

**LLM 选择**：此步骤使用**远程强模型**（Claude Sonnet 或同等），因为提取质量直接决定整个系统的价值。

#### Step 4: Dedup & Merge

- **Episodes**：向量相似度 > 0.95 视为重复，跳过
- **Decisions**：向量搜索找相似决策 → LLM 判断是"相同决策的更新"还是"新决策"
- **Principles**：向量搜索找相似原则 → 如果找到，增加 `evidence_count`；如果矛盾，标记为需要 CEO review
- **Project updates**：直接更新项目字段

### 3.2 Context Engine（上下文组装引擎）

**这是 CEO Brain 的核心输出** — 在 agent SessionStart 时，动态组装最相关的 CEO 认知。

替代当前的"注入全部 L2 + 搜索 L1"策略，改为**智能组装**：

```python
def build_ceo_context(
    project_id: str,          # 当前项目（从 cwd 识别）
    agent_source: str,        # claude_code | cursor | openclaw
    task_hint: str = "",      # 用户首条输入（如果有）
) -> str:
    """
    组装 CEO 上下文，目标：让 agent 天然带有 CEO 视角。

    输出结构：
    ═══════════════════════════════════════
    ## CEO Context (by ClickMem)

    ### 你正在为 {project.name} 工作
    {project.description}
    状态: {project.status}
    愿景: {project.vision}
    目标用户: {project.target_users}
    北极星指标: {project.north_star_metric}

    ### CEO 的核心原则
    {top_principles}  # 全局 + 项目级，按 confidence 排序

    ### 近期决策（本项目）
    {recent_decisions}  # 最近 5 个决策的 title + choice + reasoning

    ### 近期动态
    {recent_episodes}  # 最近 3 个 episode 的摘要

    ### 相关上下文  (仅当 task_hint 非空时)
    {semantic_search_results}  # 基于 task_hint 语义搜索的相关知识
    ═══════════════════════════════════════
    """
```

**Token 预算管理**：

| 区块 | 预算 | 策略 |
|------|------|------|
| Project Info | ~200 tokens | 固定注入 |
| Principles | ~300 tokens | Top-N by confidence，全局+项目级 |
| Recent Decisions | ~500 tokens | 最近 5 个，只含 title+choice+reasoning |
| Recent Episodes | ~200 tokens | 最近 3 个摘要 |
| Semantic Search | ~500 tokens | 仅当 task_hint 存在时触发 |
| **Total** | **~1700 tokens** | 远低于大多数 agent 的 context 容量 |

### 3.3 CEO Skills（主动能力）

通过 MCP Tools 暴露给 agent，让 agent 在需要时主动调用。

#### Skill 1: `ceo_brief` — 项目简报

```
Agent 调用场景：开始一个新任务前，想了解项目全貌
输入：project_id (可选), query (可选)
输出：项目信息 + 相关决策 + 相关原则 + 相关历史

与 context_inject 的区别：
- context_inject 是 SessionStart 自动注入（轻量、通用）
- ceo_brief 是 agent 主动请求（详细、可定向查询）
```

#### Skill 2: `ceo_decide` — 决策辅助

```
Agent 调用场景：遇到需要 CEO 判断的选择题
输入：question, options[], context
输出：
  - 相关历史决策（类似问题之前怎么决定的）
  - 相关原则（CEO 的价值观倾向）
  - 建议（如果历史信息足够明确）
  - "需要 CEO 确认" 标记（如果信息不足）

关键：不是替 CEO 做决策，而是提供决策所需的历史上下文
```

#### Skill 3: `ceo_remember` — 结构化记忆

```
Agent 调用场景：对话中产生了重要决策或洞察
输入：
  - type: decision | principle | episode
  - content: 结构化内容
  - project_id: 关联项目
输出：存储确认 + 去重结果

与当前 remember 的区别：强制要求结构化输入，按类型走不同存储路径
```

#### Skill 4: `ceo_review` — 一致性检查

```
Agent 调用场景：制定计划或做重大变更前
输入：proposed_plan (计划内容)
输出：
  - 与已有 principles 的一致性分析
  - 与历史 decisions 的关联（是否有类似先例）
  - 潜在矛盾点
  - 建议调整

实现：语义搜索 principles + decisions → LLM 分析一致性
LLM 选择：远程强模型（需要高质量推理）
```

#### Skill 5: `ceo_retro` — 复盘与方法论提炼

```
Agent/用户 调用场景：项目阶段结束、或定期触发
输入：project_id, time_range (可选)
输出：
  - 时间段内的决策摘要
  - 哪些决策被验证了（outcome_status = validated）
  - 哪些决策被证伪了（outcome_status = invalidated）
  - 提炼出的新 principles 候选
  - 发现的认知矛盾

实现：聚合 decisions + episodes → LLM 分析 → 候选 principles
LLM 选择：远程强模型
```

#### Skill 6: `ceo_portfolio` — 项目组合视图

```
Agent 调用场景：需要跨项目决策（资源分配、优先级排序）
输入：无
输出：
  - 所有活跃项目的状态概览
  - 每个项目的最近动态
  - 跨项目的资源冲突或依赖
  - 建议关注的优先级

实现：聚合所有 projects + 最近 episodes → 组装视图
LLM 选择：可本地（结构化组装为主）
```

---

## 四、Extraction LLM 选择策略

| 任务 | 模型选择 | 原因 |
|------|---------|------|
| Multi-pass Extraction | 远程强模型 (Claude Sonnet) | 决定数据质量，是系统的基础 |
| Project Detection | 本地小模型 (2-4B) | 简单分类任务，规则+小模型足够 |
| Dedup 判断 | 远程强模型 | 需要理解语义差异，小模型容易误判 |
| Principle 提炼 (retro) | 远程强模型 | CEO 方法论提炼需要高质量推理 |
| Context 组装 | 无需 LLM | 纯模板拼接 + 向量检索 |
| 一致性检查 | 远程强模型 | 核心能力，质量优先 |
| Portfolio 视图 | 本地小模型或无 LLM | 结构化聚合为主 |

**Fallback 策略**：远程不可用时，降级到本地模型，但标记结果为 `low_confidence`，等远程可用时重新处理。

---

## 五、集成架构

### 5.1 保留的集成方式

- **MCP Protocol** — 通过 MCP Tools 暴露 CEO Skills（Claude Code / Cursor 原生支持）
- **HTTP Hooks** — 各 agent 的生命周期钩子（SessionStart / Stop / SessionEnd）
- **REST API** — 单端口(9527)服务，保持现有模式
- **CLI** — `memory` 命令行工具

### 5.2 改变的集成流程

**SessionStart（agent 启动时）**：
```
当前：recall 搜索 → 注入 L2 全量 + L1 搜索结果
新版：detect_project(cwd) → build_ceo_context(project, agent) → 注入结构化 CEO 上下文
```

**Stop（每轮对话结束）**：
```
当前：buffer 用户输入 → 简单 extract
新版：filter_conversation(messages) → detect_project → multi_pass_extract → dedup_merge
```

**SessionEnd（会话结束）**：
```
当前：maintenance (cleanup/compress/promote)
新版：
  1. 标记处理完成的 raw_transcripts
  2. 触发 principle 候选检查（如果 evidence_count 达到阈值）
  3. 定期触发 retro（每周一次或每 N 个 session）
```

### 5.3 CLAUDE.md / AGENTS.md 集成

**新增能力**：自动生成和更新项目的 CLAUDE.md / AGENTS.md。

```python
def sync_project_files(project_id: str):
    """
    将 CEO Brain 中的项目知识同步到项目仓库的配置文件中。

    CLAUDE.md 生成内容：
    - 项目描述和愿景（from projects）
    - 关键技术决策（from decisions where domain='tech'）
    - 编码原则（from principles where domain='tech'）
    - 最近动态（from recent episodes）

    AGENTS.md 生成内容：
    - CEO 的全局偏好（from global principles）
    - 项目级偏好（from project principles）
    - Workspace facts（from project + decisions）
    """
```

这样，每个项目的 AI 配置文件都从 CEO Brain 生成，保持一致性。

---

## 六、Migration 策略

### 6.1 数据迁移

```
Phase 1: Schema Migration
  - 创建新表 (projects, decisions, principles, episodes)
  - 保留旧表 (memories, raw_transcripts) 暂不删除

Phase 2: Data Migration
  - memories where category='project' → projects 表
  - memories where category='decision' → decisions 表
  - memories where category='preference' → principles 表
  - memories where layer='episodic' → episodes 表
  - 其余 semantic memories → 通过 LLM 重新分类

Phase 3: Re-extraction
  - 对 raw_transcripts 中未过期的记录，用新的 multi-pass prompt 重新提取
  - 这是最耗时但价值最大的步骤

Phase 4: Cleanup
  - 验证新表数据完整性
  - 删除旧 memories 表
```

### 6.2 API 兼容性

**不考虑向前兼容**（用户明确要求）。新 API：

```
POST /v1/ingest          — 摄入对话（替代 /v1/extract）
POST /v1/context         — 获取 CEO 上下文（替代 /v1/recall）
POST /v1/brief           — 项目简报
POST /v1/decide          — 决策辅助
POST /v1/remember        — 结构化记忆（保留但增强）
POST /v1/review          — 一致性检查
POST /v1/retro           — 复盘
GET  /v1/portfolio       — 项目组合视图
GET  /v1/projects        — 项目列表
GET  /v1/decisions       — 决策列表
GET  /v1/principles      — 原则列表
GET  /v1/health          — 健康检查（保留）
```

---

## 七、开发计划（分阶段）

### Phase 1: 基础重构 (Core)
**目标**：新数据模型 + 基本 CRUD

1. 设计并创建新的 DB schema（projects, decisions, principles, episodes）
2. 重写 `models.py` — 新的 dataclass 定义
3. 重写 `db.py` — 新表的 CRUD 操作
4. Project Detection 模块 — 基于 cwd 路径匹配 + 项目名匹配
5. 基本 CLI 命令更新 — `memory project`, `memory decision`, `memory principle`
6. 单元测试

### Phase 2: 摄入管线 (Ingestion)
**目标**：从对话中提取结构化 CEO 知识

1. 重写 Conversation Filter — 用户输入优先策略
2. 重写 Extraction Prompt — Multi-pass CEO 视角提取
3. 重写 `extractor.py` — 支持多类型输出（episode/decision/principle/project_update）
4. 重写 Dedup/Merge — 按实体类型的不同去重策略
5. Claude Code Hook 更新 — 适配新 pipeline
6. Cursor Hook 更新
7. OpenClaw Plugin 更新
8. 初次导入工具 — 重新处理已有 raw_transcripts
9. 集成测试

### Phase 3: 上下文引擎 (Context Engine)
**目标**：智能注入 CEO 认知

1. `build_ceo_context()` 实现 — 结构化上下文组装
2. Token 预算管理 — 按区块分配，不超限
3. SessionStart Hook 重写 — 使用新的 context engine
4. 语义搜索适配 — 跨多表的 hybrid search
5. CLAUDE.md / AGENTS.md 自动生成
6. 集成测试

### Phase 4: CEO Skills (MCP Tools)
**目标**：让 agent 拥有 CEO 级别的主动能力

1. `ceo_brief` — 项目简报 tool
2. `ceo_decide` — 决策辅助 tool
3. `ceo_remember` — 结构化记忆 tool
4. `ceo_review` — 一致性检查 tool
5. `ceo_retro` — 复盘与方法论提炼 tool
6. `ceo_portfolio` — 项目组合视图 tool
7. MCP Server 更新 — 注册新 tools
8. REST API 更新 — 新端点
9. Skill 文档更新

### Phase 5: 迁移与打磨
**目标**：数据迁移 + 端到端验证

1. Migration 脚本 — 旧数据 → 新 schema
2. Re-extraction — 用新 prompt 重处理 raw_transcripts
3. CLI 全面更新 — 新命令体系
4. setup.sh 更新
5. 文档更新（design.md, README.md）
6. 端到端测试
7. 性能测试与优化

---

## 八、关键文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `src/memory_core/models.py` | **重写** | 新 dataclass: Project, Decision, Principle, Episode |
| `src/memory_core/db.py` | **重写** | 新 schema, 多表 CRUD |
| `src/memory_core/extractor.py` | **重写** | Multi-pass CEO extraction |
| `src/memory_core/retrieval.py` | **大改** | 跨多表检索，按实体类型的不同评分策略 |
| `src/memory_core/context_engine.py` | **新建** | CEO 上下文组装引擎 |
| `src/memory_core/project_detect.py` | **新建** | 项目自动识别 |
| `src/memory_core/skills.py` | **新建** | CEO Skills 实现 |
| `src/memory_core/migration.py` | **新建** | 数据迁移工具 |
| `src/memory_core/upsert.py` | **大改** | 多类型 dedup 策略 |
| `src/memory_core/server.py` | **大改** | 新 API 端点 |
| `src/memory_core/mcp_server.py` | **大改** | 新 MCP Tools |
| `src/memory_core/cli.py` | **大改** | 新命令体系 |
| `src/memory_core/transport.py` | **中改** | 适配新 API |
| `src/memory_core/maintenance_mod.py` | **重写** | 新维护策略 (principle promotion, retro 触发) |
| `src/memory_core/refinement.py` | **大改** | 适配新实体类型 |
| `src/memory_core/llm.py` | **小改** | 增加模型选择策略 |
| `src/memory_core/md_sync_mod.py` | **重写** | 从 CEO Brain 生成 CLAUDE.md |
| `design.md` | **重写** | 新架构文档 |

---

## 九、风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| Extraction 质量不稳定 | 垃圾进垃圾出 | Extraction 用远程强模型；增加 confidence 字段；人工 review 机制 |
| LLM API 成本 | 频繁调用消耗 token | 批处理优化；非关键路径用本地模型；增量处理（只处理新对话） |
| 项目识别误判 | 知识挂错项目 | 多策略融合（路径>名称>语义）；支持手动修正 |
| 迁移丢数据 | 旧数据丢失 | 旧表保留到验证完成；migration 可重跑 |
| Context 注入太长 | 浪费 agent token | Token 预算管理；按优先级截断 |
