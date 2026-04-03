# Recall Quality Test Cases

Cases accumulated from real user sessions. Each case has a natural query, probe words (key content that should appear in top results), and a baseline rating.

Run manually: `memory recall "<query>" -k <top_k>`, check if probe words appear in top-N.

## Infrastructure & Deployment

| # | Query | Probe Words (top-3) | Top-K | Baseline |
|---|-------|---------------------|-------|----------|
| 1 | OpenClaw 部署在哪台机器上，怎么 SSH 登录 | `tong`, `mini`, `100.86.126.80` | 3 | ✅ |
| 2 | 登录 mini 主机用什么用户名 | `tong`, `tong@mini` | 3 | ⚠️ fallback episode 排在 fact 前 |
| 3 | mini 的 Tailscale IP 地址 | `100.86.126.80` | 3 | ✅ |
| 4 | ssh 到 mini 的命令 | `tong@mini`, `100.86.126.80` | 3 | ⚠️ preference 排在 fact 前 |
| 5 | clickmem server 跑在哪个端口 | `9527` | 3 | ✅ |
| 6 | ClickHouse Cloud 的端口号是多少 | `9440`, `secure` | 3 | ✅ |
| 7 | Chrome CDP 用的哪个端口 | `18800`, `18801` | 3 | ✅ |

## Bug Fixes & Debugging

| # | Query | Probe Words (top-3) | Top-K | Baseline |
|---|-------|---------------------|-------|----------|
| 8 | WhatsApp channel 之前崩溃是怎么修的 | `extraDirs`, `openclaw.json` | 3 | ✅ |
| 9 | botschat 发图片失败是什么原因 | `mediaUrls`, `deliver` | 3 | ✅ |
| 10 | chdb Connection 的 __del__ 之前出了什么问题 | `__del__`, `lifecycle`, `race condition` | 3 | ✅ |
| 11 | AppsFlyer 收入没上报是怎么修的 | `trackSubscription`, `never invoked` | 3 | ✅ |
| 12 | BotChat 一直重启是怎么回事 | `state sync`, `health monitor`, `restart` | 3 | ✅ |
| 13 | wrangler 部署后旧的 cron trigger 还在跑 | `crons = []`, `commenting out does not delete` | 3 | ✅ |

## Decisions & Strategy

| # | Query | Probe Words (top-3) | Top-K | Baseline |
|---|-------|---------------------|-------|----------|
| 14 | X 上发帖的策略后来改成什么了 | `Reply Guy`, `high-volume replies`, `disable` | 3 | ✅ |
| 15 | 安装 plugin 时为什么不能删除 settings.json | `coexist`, `inline hooks`, `current session` | 3 | ✅ |
| 16 | clickmem Claude Code plugin 为什么加载失败 | `.cursor-plugin`, `.claude-plugin` | 3 | ⚠️ 缺 root cause |

## Principles & Guidelines

| # | Query | Probe Words (top-3) | Top-K | Baseline |
|---|-------|---------------------|-------|----------|
| 17 | 我们开发产品遇到 bug 应该怎么处理 | `product`, `opportunity`, `bug less` | 3 | ✅ |
| 18 | 我的 agent 开发原则 | `agent`, `Decision Tree`, `knowledge scope`, `workflow` | 5 | ⚠️ 有 CEO 结果但 principle 排名低 |
| 19 | 配置应该用环境变量还是配置文件 | `environment variable`, `defaults` | 3 | ✅ |
| 20 | 可以手动修改 clickmem 里的记忆数据吗 | `never`, `禁止`, `手动修改` | 3 | ❌ 数据未 ingest |

## Project Knowledge

| # | Query | Probe Words (top-3) | Top-K | Baseline |
|---|-------|---------------------|-------|----------|
| 21 | clickmem 支持哪些 AI coding agent | `Claude Code`, `Cursor`, `OpenClaw` | 3 | ✅ |
| 22 | 改了 botschat 源码之后怎么 rebuild | `dist`, `channel.js`, `rebuild` | 3 | ✅ |
| 23 | AiNote 录音按钮不够明显怎么优化的 | `animation`, `glow`, `orange` | 3 | ✅ |
| 24 | 代码改完之后 deploy 到 mini 的完整流程 | `commit`, `push`, `ssh`, `uv pip install` | 3 | ⚠️ 零散 decision |

## Identity

| # | Query | Probe Words (top-5) | Top-K | Baseline |
|---|-------|---------------------|-------|----------|
| 25 | 我叫什么名字 | `Auxten`, `Pengcheng` | 5 | ❌ #1 无关，#2 才命中 |

## Auto-Research Probes (2026-04-03)

Cases auto-generated from real conversations by the auto-research system.

| # | Query | Probe Words (top-5) | Top-K | Baseline |
|---|-------|---------------------|-------|----------|
| 26 | Why was the direct push to main branch rejected and what was the alternative? | `push protection`, `PR`, `main branch`, `feature branch` | 5 | ⚠️ found PR only |
| 27 | What is the status of the P0 task 'fix test failure'? | `P0`, `fix test failure`, `resolved` | 5 | ⚠️ found P0 only |
| 28 | How does the clickmem agent manage multiple tasks? | `TaskCreate`, `TaskList`, `TaskGet` | 5 | ❌ data_gap |

## Summary (2026-04-03, keyword+scoring improvements)

- ✅ PASS: 11/25 (44%)
- ⚠️ PARTIAL: 10/25 (40%)
- ❌ FAIL: 4/25 (16%)
- Pass+Partial: 84%

Previous baseline (original): 64% PASS, 24% PARTIAL, 12% FAIL (Pass+Partial: 88%)

## Changes Since Baseline

- Tokenizer: handles dot-prefixed paths (.claude-plugin), dunder names (__del__), bigrams
- Keyword scoring: includes entities list, length normalization on reasoning
- Keyword weight increased: 0.3→0.6, cap 1.5→2.0
- Fact specificity boost: 1.3x when keyword_score > 0.3
- Entity exact-match boost: 1.5x for named entity matches
- Principle confidence: less punishing (0.7+0.3*conf)

## Known Issues

- **#16**: data not ingested — `.cursor-plugin`/`.claude-plugin` content missing from DB
- **#20**: data not ingested — "禁止手动修改" rule never extracted
- **#23**: data not ingested — AiNote animation/glow/orange episode missing
- **#25**: identity query — no user profile facts in DB, short query has low vector discrimination
- **#2, #13, #14, #18**: 跨语言查询（中文 query vs 英文 content），需要多语言 query embedding 支持
- **Latency**: LLM keyword extraction on mini adds ~2-5s per query
