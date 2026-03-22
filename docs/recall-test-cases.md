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

## Summary

- ✅ PASS: 16/25 (64%)
- ⚠️ PARTIAL: 6/25 (24%)
- ❌ FAIL: 3/25 (12%)

## Known Issues

- **#2, #4**: 长 preference 内容因关键词覆盖面广，keyword boost 后排名虚高，压过精准 fact
- **#18**: 跨语言查询（中文 query vs 英文 principles），LLM 关键词扩展已改善但 principle 排名仍低于 decision
- **#20**: 数据丢失 — hooks 中断期间的对话未被 ingest（session replay 已实现，待验证）
- **#25**: 短 query 区分度低，向量相似度对身份信息不敏感
