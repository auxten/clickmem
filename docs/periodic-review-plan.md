# ClickMem 定期 Review 与自我改进计划

## 背景

ClickMem 已经从多台机器（auxten 本地 + mini）、多个 agent（Claude Code、Cursor、OpenClaw、Codex）采集对话和知识。随着数据量增长和功能迭代，需要一个**定期自动化的 review 机制**来：

1. 评估各数据源的采集质量
2. 检验 recall 的效果是否退化
3. 识别改进方向并自动执行
4. 完成代码提交和多机部署

## 历史工作回顾

### Phase 1: CEO Brain 架构 (2026-03)
- L0/L1/L2 三层模型 → 五实体知识系统（projects, decisions, principles, episodes, facts）
- 多阶段 recall：query 分析 → 子查询搜索 → LLM rerank

### Phase 2: 质量修复与测试 (2026-03)
- 修复 6 个质量问题（项目误归属、principle 爆炸、outcome 追踪）
- 25 个 recall 测试用例基线：16 PASS / 6 PARTIAL / 3 FAIL (64%)
- Session replay：恢复 hook 中断期间丢失的对话

### Phase 3: 多源采集 (2026-04, 本次)
- Claude Code auto-memory 直接解析为 Principle/Fact（无 LLM）
- Stop hook + PostToolUse hook 实时同步
- Cursor .mdc 支持 + 全局 rules 扫描
- Codex CLI 支持（待实现）

## 定期 Review 任务设计

### 任务触发
- **频率**: 每天凌晨 3:00 (mini launchd)
- **超时**: 30 分钟
- **日志**: `~/.openclaw/memory/logs/review-YYYY-MM-DD.log`

### 任务流程

```
┌─────────────────────────────────────────────┐
│  Phase 1: 数据采集健康检查                    │
│  - 各 agent 最近 24h 新增 session 数          │
│  - 各机器最近 24h 新增 principle/fact/episode  │
│  - 孤立 raw_transcript 数量（未提取的）        │
│  - auto-memory 文件 vs 已同步数量对比          │
├─────────────────────────────────────────────┤
│  Phase 2: Recall 质量自动测试                 │
│  - 运行 docs/recall-test-cases.md 中的        │
│    25 个 case，统计 pass/partial/fail         │
│  - 对比上次 baseline，检测退化                 │
│  - 记录新的 baseline 到 JSON                  │
├─────────────────────────────────────────────┤
│  Phase 3: 知识质量评估                        │
│  - 重复 principle 检测（similarity > 0.85）    │
│  - 过期 episode 统计（超过 90 天未访问）       │
│  - 空 embedding 实体数量                      │
│  - 无 project 归属的实体占比                   │
├─────────────────────────────────────────────┤
│  Phase 4: 报告生成                            │
│  - 生成 review 报告存入                       │
│    ~/.clickmem/reviews/YYYY-MM-DD.md          │
│  - 如果有严重退化，写入 fact 供下次 session     │
│    主动提醒                                   │
└─────────────────────────────────────────────┘
```

### 各 Phase 具体指标

#### Phase 1: 数据采集健康检查

| 指标 | 数据源 | 告警阈值 |
|------|--------|---------|
| 新增 session 数 | raw_transcripts WHERE created_at > now() - 24h | < 1 (无新数据) |
| 新增 principle 数 | principles WHERE created_at > now() - 24h | 仅记录 |
| 新增 fact 数 | facts WHERE created_at > now() - 24h | 仅记录 |
| 孤立 raw_transcript | raw_transcripts 无对应 episode | > 50% 告警 |
| auto-memory 覆盖率 | `~/.claude/projects/*/memory/*.md` vs import-state.json | < 80% 告警 |

#### Phase 2: Recall 质量测试

- 解析 `docs/recall-test-cases.md` 中的 25 个 case
- 对每个 case 调用 `memory recall "<query>" -k <top_k>`
- 检查 probe words 是否出现在返回结果中
- 计算 pass/partial/fail 率
- 与上次 baseline 对比，recall rate 下降 > 10% 告警

#### Phase 3: 知识质量评估

| 指标 | SQL / 方法 | 健康值 |
|------|-----------|--------|
| 重复 principle | vector search similarity > 0.85 pairs | < 10 对 |
| 孤儿 principle | project_id 非空但 project 不存在 | 0 |
| 空 embedding | WHERE length(embedding) = 0 | < 5% |
| 超期 episode | TTL > 180d 未清理 | 0 |

### 自动改进能力（Phase 5, 后期）

当 review 发现可自动修复的问题时：

| 问题类型 | 自动修复方案 |
|---------|-------------|
| 重复 principle | 调用 maintenance 合并 |
| 空 embedding | 重新计算 embedding |
| 孤儿实体 | 重新 detect_project 归属 |
| recall 退化 | 记录到 fact，触发人工关注 |

后期目标：当 review 发现代码层面的问题模式时，由 Claude Code agent 自动生成修复 PR、跑测试、部署。

## 实现步骤

### Step 1: `memory review` CLI 命令
新增 `src/memory_core/review.py`：
- `run_health_check(ceo_db)` → dict
- `run_recall_test(ceo_db, emb, test_cases_path)` → dict
- `run_quality_audit(ceo_db)` → dict
- `generate_report(results)` → markdown string

CLI 入口：`memory review [--output PATH]`

### Step 2: launchd 定时任务
在 `service.py` 中新增 `install_review_schedule()`:
- 写入 `~/Library/LaunchAgents/com.clickmem.review.plist`
- 每天 3:00 执行 `memory review --output ~/.clickmem/reviews/`
- 日志重定向到 `~/.openclaw/memory/logs/`

CLI 入口：`memory service install-review`

### Step 3: recall test case 自动化
- 解析 `docs/recall-test-cases.md` 的 markdown 表格
- 对每个 case 调用 recall API
- 比较 probe words，输出结果

### Step 4: Codex CLI 支持
新增 `~/.codex/` 数据源：
- 读取 `sessions/` 下的 rollout JSONL
- 读取 `memories/` 下的文件
- 读取 `AGENTS.md` 
- hook 集成（hooks.json 格式与 Claude Code 几乎一致）

## 文件清单

| 文件 | 说明 |
|------|------|
| `src/memory_core/review.py` | Review 引擎：健康检查 + recall 测试 + 质量审计 |
| `src/memory_core/service.py` | 新增 review 定时任务安装 |
| `src/memory_core/cli.py` | 新增 `memory review` 命令 |
| `src/memory_core/import_agent.py` | 新增 Codex reader |
| `docs/recall-test-cases.md` | 持续更新的 recall 基线 |
| `~/.clickmem/reviews/` | review 报告输出目录 |

## 验收标准

1. `memory review` 本地跑通，输出报告
2. mini 上 launchd 定时任务安装成功
3. 每天自动生成 review 报告，recall 退化可告警
4. Codex JSONL 导入可用
