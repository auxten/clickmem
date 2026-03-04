# OpenClaw Memory — Product Specification

## One-liner

Give your OpenClaw agent real memory — three-layer, self-maintaining, locally searchable.

---

## Problem

OpenClaw's compaction loses detail. The tech preferences you told your agent last week, the decisions you made, the names you mentioned — gone after compression. The native MEMORY.md is plain text with keyword-only search that mostly misses. Embeddings require remote API calls that cost money and leak data.

## Solution

An OpenClaw plugin that stores memories in local chDB (embedded ClickHouse), generates vectors with Qwen3-Embedding-0.6B on-device, and runs semantic search via ClickHouse's native HNSW index. Fully local, zero API cost, zero data leakage.

---

## Three-Layer Memory Model

This is the core of the entire system. The three layers are not tag categories — they are **storage tiers with fundamentally different lifecycles**, each with its own write rules, injection strategy, decay mechanics, and capacity constraints.

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  L0  Working Memory  (current focus)                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ "User is building memory system Phase 2, last discussed    │  │
│  │  HNSW index config"                                        │  │
│  │ "Follow-up: user said demo due by Friday"                  │  │
│  └────────────────────────────────────────────────────────────┘  │
│  Always injected · Overwritten after every conversation · ≤500t  │
│                                                                  │
├──────────────────────── ▲ overwrite ────────────────────────────┤
│                                                                  │
│  L1  Episodic Memory  (event stream)                             │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ 03-04 14:30  Decided on Python core + JS thin-shell arch   │  │
│  │ 03-04 10:15  Aligned API design with Alice, adopted gRPC   │  │
│  │ 03-03 16:00  Researched chDB HNSW index, confirmed 25.8 GA│  │
│  │ 03-01 09:00  Project kickoff, goal: replace sqlite-vec     │  │
│  │ ...                                                        │  │
│  └────────────────────────────────────────────────────────────┘  │
│  Retrieved on demand · Time-decayed · Compressed after 30d       │
│  · 500 entries/month cap                                         │
│                                                                  │
├──────── ▲ promote (recurring patterns)  ▼ compress (old → sum) ─┤
│                                                                  │
│  L2  Semantic Memory  (durable knowledge)                        │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ [preference]  Prefers SwiftUI over UIKit                   │  │
│  │ [preference]  Keep answers concise, skip basic explanations│  │
│  │ [knowledge]   iOS developer, based in Singapore            │  │
│  │ [person]      Alice is the backend lead                    │  │
│  │ [project]     Memory system project, goal: replace native  │  │
│  │ [todo]        Demo due by April 15                         │  │
│  └────────────────────────────────────────────────────────────┘  │
│  Always injected · Rarely changed · Never auto-deleted           │
│  · Overwritten only on contradiction                             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Layer Specifications

#### L0 Working Memory — Current Focus

| Property | Spec |
|----------|------|
| Nature | The agent's "scratchpad" — what it's doing right now |
| Capacity | Strictly ≤ 500 tokens (~3–5 sentences) |
| Injection | **Always injected in full** at the top of the system prompt |
| Write timing | LLM rewrites (not appends — overwrites) at end of every conversation |
| Decay | None. Each rewrite naturally keeps it fresh |
| Persistence | Stored in chDB, but only the latest 1 entry retained (per agent) |
| Typical content | Current task focus, where the last conversation left off, pending follow-ups, interaction style notes |

#### L1 Episodic Memory — Event Stream

| Property | Spec |
|----------|------|
| Nature | Timeline of events — "what happened when" |
| Capacity | 500 entries/month cap; exceeding triggers compression |
| Injection | **Retrieved on demand** via semantic matching against current conversation |
| Injection budget | ≤ 3000 tokens |
| Write timing | Extracted at end of conversation + emergency flush before compaction |
| Decay | Exponential decay, half-life 60 days. 120 days with no access and access_count=0 → auto-cleaned |
| Compression | Entries older than 30 days: LLM generates monthly summary to replace originals |
| Promotion | A tag/pattern appears ≥ 3 times → LLM decides whether to distill into a semantic memory |
| Typical content | Decisions made, meetings held, problems encountered, milestones reached |

#### L2 Semantic Memory — Durable Knowledge

| Property | Spec |
|----------|------|
| Nature | Persistent facts about the user — "who the user is" |
| Capacity | No hard limit, but recommended ≤ 200 entries (keep it lean) |
| Injection | **Always injected in full** into system prompt (after working memory) |
| Injection budget | ≤ 2000 tokens |
| Write timing | Promoted from episodic / new persistent facts discovered in conversation / manual user input |
| Decay | **No automatic decay.** LLM reviews once a month for stale information |
| Deletion | Only on contradiction (delete old + add new) |
| Typical content | User profile, tech preferences, people, project overviews, long-term todos |

### Inter-Layer Flow

```
Conversation input
  │
  ├── direct write ──▶ L0 (overwritten at end of every conversation)
  │
  ├── extract ──▶ L1 (specific events, decisions)
  │
  └── extract ──▶ L2 (newly discovered persistent facts — less frequent)

L1 ── compress ──▶ L1 summary entry (30+ day old entries merged into monthly summary)

L1 ── promote ──▶ L2 (tag recurs ≥ 3 times, LLM decides, then distills)

L2 ── contradiction override ──▶ L2 (new info conflicts with old → delete old + add new)

L1 ── decay cleanup ──▶ deleted (120 days without access + access_count=0)
```

### Context Injection Example

What the agent sees at the start of every conversation:

```
[Working Memory — Current Focus]
User is building memory system Phase 2, last discussed HNSW index config.
Follow-up: user said demo due by Friday.

[Semantic Memory — User Profile]
- [preference] Prefers SwiftUI over UIKit
- [preference] Keep answers concise, skip basic explanations
- [knowledge] iOS developer, based in Singapore
- [person] Alice is the backend lead
- [project] Memory system project, goal: replace OpenClaw native memory
- [todo] Demo due by April 15

[Relevant Episodic — Events Related to This Conversation]
- 03-04 Decided on Python core + JS thin-shell architecture (score=0.85)
- 03-03 Researched chDB HNSW index, confirmed 25.8 GA (score=0.78)
- 03-01 Project kickoff, goal: replace sqlite-vec (score=0.71)
```

Injection order and budget:

| Section | Source | Injection method | Token budget |
|---------|--------|-----------------|-------------|
| Working Memory | L0 latest entry | Always injected in full | ≤ 500 |
| Semantic Memory | All active L2 entries | Always injected in full | ≤ 2000 |
| Relevant Episodic | L1 semantic search top-K | Retrieved on demand | ≤ 3000 |
| **Total** | | | **≤ 5500** |

---

## Architecture

```
You (Telegram / WhatsApp / CLI)
 │
 ▼
OpenClaw Gateway ──── agent loop ──── LLM (Claude / GPT / local)
 │                        │
 │  plugin hooks          │  tool calls
 │                        │
 ▼                        ▼
┌─────────────────────────────────────────────┐
│  openclaw-memory  plugin                     │
│                                              │
│  ┌──────────────┐    ┌───────────────────┐  │
│  │  JS bridge    │───▶│  Python core      │  │
│  │  (plugin SDK) │◀───│  memory-core      │  │
│  └──────────────┘    └────────┬──────────┘  │
│       OpenClaw hooks          │              │
│       before_agent_start      │              │
│       agent_end               ▼              │
│       before_compaction  ┌─────────┐         │
│                          │  chDB   │         │
│                          │ (local) │         │
│                          └────┬────┘         │
│                               │              │
│                          ┌────▼─────┐        │
│                          │ Qwen3    │        │
│                          │ Embed    │        │
│                          │ 0.6B     │        │
│                          │ (local)  │        │
│                          └──────────┘        │
└─────────────────────────────────────────────┘
         │
         ▼
  ~/.openclaw/memory/
    chdb-data/           ← MergeTree compressed storage (L0+L1+L2 same table, layer column)
    MEMORY.md            ← Human-readable mirror of L2 (auto-synced)
    memory/2026-03-04.md ← Human-readable mirror of L1 daily entries
```

### Data Flow

| Timing | Action |
|--------|--------|
| **Conversation start** | Load L0 full + L2 full + L1 semantic search → inject into system prompt |
| **During conversation** | Agent can call `memory recall` / `memory remember` tools |
| **Conversation end** | LLM analyzes conversation → rewrite L0 → extract L1/L2 entries → write to chDB → sync .md |
| **Approaching compaction** | Emergency extract key information into L1 to prevent compaction loss |
| **Periodic maintenance** | L1 decay cleanup → L1 monthly compression → L1→L2 promotion → physical cleanup |

### Storage

Single `memories` table; the `layer` column distinguishes the three tiers. Vectors are embedded as a column; ClickHouse HNSW index accelerates semantic search.

| Field | Description |
|-------|-------------|
| id | UUID |
| **layer** | **`working` / `episodic` / `semantic`** — the core field that determines lifecycle |
| category | `decision` / `preference` / `event` / `person` / `project` / `knowledge` / `todo` / `insight` |
| content | Natural-language memory text |
| tags | Tag array |
| entities | Extracted entity name array |
| embedding | Float32 vector (256-dim, HNSW bf16 quantized index) |
| session_id | Source session |
| source | `agent` / `cli` / `user_edit` / `compaction_flush` / `maintenance` |
| created_at / updated_at / accessed_at | Timestamps |
| access_count | Retrieval count (basis for L1 decay) |

Auxiliary table `session_log`: raw conversation archive, 90-day TTL auto-cleanup.

---

## CLI Reference

Commands are designed as natural-language verbs, usable by both agents and humans. All commands support `--json`.

### remember — Store a memory

```bash
# Write to L2 semantic (default)
$ memory remember "User prefers SwiftUI over UIKit" \
    --category preference --tags swift,ui

✓ Stored [semantic/preference]: User prefers SwiftUI over UIKit
  id=a1b2c3d4  tags=swift,ui

# Write to L1 episodic
$ memory remember "Decided to replace SQLite with chDB" \
    --layer episodic --category decision --tags chdb,storage

✓ Stored [episodic/decision]: Decided to replace SQLite with chDB
  id=e5f6g7h8  tags=chdb,storage

# Write to L0 working (overwrite)
$ memory remember "Debugging HNSW index config, follow up on bf16 quantization results" \
    --layer working

✓ Working memory updated.

# Agent-friendly: JSON output
$ memory remember "Alice is the backend lead" --category person --json
{"id":"q7r8s9t0","layer":"semantic","category":"person","status":"stored"}
```

### recall — Semantic search

```bash
$ memory recall "tech stack decisions"

── Semantic ──────────────────────────────────────
  [preference] Prefers SwiftUI over UIKit  (score=0.82)
  [knowledge]  iOS developer, based in Singapore  (score=0.65)

── Episodic ──────────────────────────────────────
  03-04 Decided on Python core + JS thin-shell arch  (score=0.85)
  03-03 Researched chDB HNSW index  (score=0.78)
  03-01 Project kickoff, goal: replace sqlite-vec  (score=0.71)
```

```bash
# Filter by layer
$ memory recall "Alice" --layer semantic

── Semantic ──────────────────────────────────────
  [person] Alice is the backend lead  (score=0.93)

$ memory recall "decisions made last week" --layer episodic --category decision

── Episodic ──────────────────────────────────────
  03-04 Decided on Python core + JS thin-shell arch  (score=0.88)
  03-01 Decided to replace SQLite with chDB  (score=0.76)
  02-28 Decided against graph modeling, keep it simple  (score=0.72)
```

```bash
# Agent invocation
$ memory recall "user's coding habits" --json | jq '.[].content'
"Prefers SwiftUI over UIKit"
"Code style: concise, avoids over-abstraction"
"Prefers declarative programming"
```

### forget — Delete a memory

```bash
$ memory forget a1b2c3d4
✓ Forgotten: a1b2c3d4 [semantic/preference] Prefers SwiftUI over UIKit

$ memory forget a1b2 --json    # ID prefix supported
{"id":"a1b2c3d4-...","status":"deleted"}
```

### review — Browse by layer

```bash
# View L0 current focus
$ memory review --layer working

[Working Memory]
Debugging HNSW index config, follow up on bf16 quantization results
Updated: 2026-03-04 15:30

# View all L2 durable knowledge
$ memory review --layer semantic

┌──────────┬────────────┬──────────────────────────────────────────┬────────────┐
│ ID       │ Category   │ Content                                  │ Updated    │
├──────────┼────────────┼──────────────────────────────────────────┼────────────┤
│ q7r8s9t0 │ person     │ Alice is the backend lead                │ 2026-03-04 │
│ a1b2c3d4 │ preference │ Prefers SwiftUI over UIKit               │ 2026-03-04 │
│ k8l9m0n1 │ knowledge  │ iOS developer, based in Singapore        │ 2026-02-20 │
│ i9j0k1l2 │ todo       │ Demo due by April 15                     │ 2026-03-04 │
│ p2q3r4s5 │ project    │ Memory system project, replace native    │ 2026-03-01 │
└──────────┴────────────┴──────────────────────────────────────────┴────────────┘

# View recent L1 episodic events
$ memory review --layer episodic --limit 5

┌──────────┬──────────┬────────────────────────────────────────────┬──────────────────┐
│ ID       │ Category │ Content                                    │ Created          │
├──────────┼──────────┼────────────────────────────────────────────┼──────────────────┤
│ e5f6g7h8 │ decision │ Decided on Python core + JS thin-shell     │ 2026-03-04 14:30 │
│ f6g7h8i9 │ event    │ Aligned API design with Alice, adopted gRPC│ 2026-03-04 10:15 │
│ g7h8i9j0 │ insight  │ chDB HNSW underperforms brute-force at low │ 2026-03-03 16:00 │
│          │          │ volume                                     │                  │
│ h8i9j0k1 │ decision │ No graph modeling, keep it simple          │ 2026-02-28 11:00 │
│ j0k1l2m3 │ event    │ Project kickoff, goal: replace sqlite-vec  │ 2026-03-01 09:00 │
└──────────┴──────────┴────────────────────────────────────────────┴──────────────────┘
```

### status — Per-layer statistics

```bash
$ memory status

Embedding: qwen3-0.6b (256d, bf16) ✓ loaded
Storage: 6.2 MB (chdb-data/)

L0 Working     1 entry      0.4 KB
L1 Episodic   26 entries   17.4 KB   oldest=2026-02-01  newest=2026-03-04
L2 Semantic   15 entries    5.1 KB   oldest=2026-01-20  newest=2026-03-04
─────────────────────────────────────
Total         42 entries   22.9 KB

L1 Breakdown:
  event=12  decision=8  insight=4  summary=2

L2 Breakdown:
  preference=5  knowledge=4  person=2  project=2  todo=2
```

### sql — Direct query

```bash
$ memory sql "SELECT layer, count() c FROM memories \
    WHERE is_active=1 GROUP BY layer ORDER BY layer"
┌───────────┬────┐
│ layer     │  c │
├───────────┼────┤
│ working   │  1 │
│ episodic  │ 26 │
│ semantic  │ 15 │
└───────────┴────┘

$ memory sql "SELECT content, access_count FROM memories \
    WHERE layer='semantic' ORDER BY access_count DESC LIMIT 5"
┌──────────────────────────────────────────┬──────────────┐
│ content                                  │ access_count │
├──────────────────────────────────────────┼──────────────┤
│ iOS developer, based in Singapore        │           47 │
│ Keep answers concise, skip basics        │           35 │
│ Code style: concise, avoids abstraction  │           22 │
└──────────────────────────────────────────┴──────────────┘

$ memory sql "SELECT formatDateTime(created_at,'%Y-%m') month, \
    count() cnt FROM memories WHERE layer='episodic' \
    GROUP BY month ORDER BY month"
┌─────────┬─────┐
│ month   │ cnt │
├─────────┼─────┤
│ 2026-01 │   3 │
│ 2026-02 │  12 │
│ 2026-03 │  11 │
└─────────┴─────┘
```

### maintain — Maintenance

```bash
$ memory maintain --dry-run

L1 Episodic:
  Stale (120+ days, 0 accesses): 3 entries to clean
    - Discussed weather... (125d)
    - Tested an API endpoint... (121d)
    - Chatted about weekend plans... (130d)
  Compress: 2026-01 has 47 entries → would generate monthly summary

L1 → L2 Promotion candidates:
  "chdb" appeared 5× in episodic → candidate for semantic/knowledge
  "deadline" appeared 3× → candidate for semantic/todo

$ memory maintain
✓ L1: Cleaned 3 stale entries
✓ L1: Compressed 2026-01 (47 → 1 summary)
✓ L1→L2: Promoted 2 patterns to semantic
✓ Storage optimized
```

---

## Python API Reference

### MemoryDB

```python
from memory_core import MemoryDB, Memory

db = MemoryDB("~/.openclaw/memory/chdb-data")

# ---- L0 Working: overwrite ----
db.set_working("Debugging HNSW index config, follow up on bf16 quantization")
db.get_working()  # → "Debugging HNSW index config..."

# ---- L1 Episodic: append ----
db.insert(Memory(
    content="Decided to replace SQLite with chDB",
    layer="episodic", category="decision",
    tags=["chdb", "storage"],
))

# ---- L2 Semantic: persistent facts ----
db.insert(Memory(
    content="User prefers SwiftUI over UIKit",
    layer="semantic", category="preference",
    tags=["swift", "ui"],
))

# ---- Update (insert new version + deactivate old) ----
db.update_content(memory_id, "User prefers SwiftUI but is also fluent in UIKit")

# ---- Soft delete ----
db.deactivate(memory_id)

# ---- Per-layer queries ----
db.count()              # → 42
db.count_by_layer()     # → {"working": 1, "episodic": 26, "semantic": 15}
db.stats()              # → grouped by layer × category

# ---- Raw SQL ----
db.query("SELECT content FROM memories WHERE layer='semantic' AND hasAny(tags, ['swift'])")
```

### EmbeddingEngine

```python
from memory_core import EmbeddingEngine

emb = EmbeddingEngine("~/.openclaw/models/qwen3-embedding-0.6b-q4.gguf", dimension=256)
emb.load()

query_vec  = emb.encode_query("tech stack decisions")    # with instruct prefix
doc_vec    = emb.encode_document("Prefers SwiftUI")      # without instruct
batch_vecs = emb.encode_batch(["text1", "text2"])
```

### Retrieval

```python
from memory_core import hybrid_search, RetrievalConfig

results = hybrid_search(
    db, emb,
    query="tech stack decisions",
    cfg=RetrievalConfig(
        top_k=10,
        w_vector=0.5, w_keyword=0.5,
        decay_days=60, mmr_lambda=0.7,
    ),
)

for r in results:
    print(r["layer"], r["category"], r["content"], r["final_score"])
```

Retrieval strategy auto-switches:

| Condition | Strategy |
|-----------|----------|
| Embedding available + memories < 50K | Single SQL hybrid (brute-force cosineDistance + keywords) |
| Embedding available + memories ≥ 50K | Two-stage (HNSW recall top-100 → rerank) |
| No embedding | Keywords only + tag matching + time decay |

Note: Retrieval only applies to L1 Episodic. L0 and L2 are injected in full — they bypass retrieval entirely.

### Extraction

```python
from memory_core import MemoryExtractor

extractor = MemoryExtractor(db, emb)

# End of conversation: auto-extract and write to appropriate layers
new_ids = extractor.extract(
    messages=[...],
    llm_complete=my_llm_function,
    session_id="sess-001",
)
# LLM automatically determines which layer (L0/L1/L2) each memory belongs to

# Before compaction: emergency extract → write to L1
new_ids = extractor.emergency_flush(
    context="...", llm_complete=my_llm_function,
)
```

### Maintenance

```python
from memory_core import maintenance

maintenance.run_all(db, llm_complete=my_llm, emb=emb)

# Or run individually:
maintenance.cleanup_stale(db, decay_days=120)           # L1: clean long-unaccessed
maintenance.purge_deleted(db, days=7)                    # Physical deletion
maintenance.compress_episodic(db, my_llm, emb,           # L1: monthly compression
                              month="2026-01")
maintenance.promote_to_semantic(db, my_llm, emb)         # L1 → L2: pattern promotion
maintenance.review_semantic(db, my_llm)                  # L2: review stale information
```

### .md Sync

```python
from memory_core import md_sync

md_sync.export_memory_md(db, workspace_path)    # L2 → MEMORY.md
md_sync.export_daily_md(db, workspace_path)     # L1 today → memory/2026-03-04.md
```

---

## OpenClaw Integration

| Hook | Timing | Behavior |
|------|--------|----------|
| `before_agent_start` | Conversation start | Load L0 + L2 in full, L1 semantic search → inject into system prompt |
| `agent_end` | Conversation end | LLM analyzes conversation → rewrite L0 → extract L1/L2 → write → sync .md |
| `before_compaction` | Approaching context limit | Emergency extract to L1 to prevent compaction loss |
| CLI `memory *` | Anytime | Agent and humans operate memory directly via CLI |
| Background service | Continuously running | .md sync + periodic maintenance |

### .md Compatibility

- `MEMORY.md` = human-readable mirror of L2 Semantic
- `memory/YYYY-MM-DD.md` = human-readable mirror of L1 Episodic daily entries
- OpenClaw native memory_search / memory_get still work
- Plugin disabled → zero impact, seamless fallback

---

## Specifications Summary

### Embedding

| Property | Value |
|----------|-------|
| Model | Qwen3-Embedding-0.6B (GGUF Q4_K_M) |
| Disk | ~350 MB |
| RAM | ~500 MB (during inference) |
| Dimensions | 256 (MRL truncation, configurable 128/256/512/1024) |
| HNSW quantization | bf16 (halves index size) |

### Storage

| Memory count | Text (LZ4) | Vectors (bf16) | Total |
|-------------|-----------|---------------|-------|
| 1,000 | ~0.2 MB | ~0.5 MB | ~0.7 MB |
| 5,000 | ~1 MB | ~2.5 MB | ~3.5 MB |
| 10,000 | ~2 MB | ~5 MB | ~7 MB |

### Retrieval Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| top_k | 15 | Number of L1 results returned |
| w_vector | 0.5 | Vector similarity weight |
| w_keyword | 0.5 | Keyword hit rate weight |
| decay_days | 60 | L1 time decay half-life (days) |
| mmr_lambda | 0.7 | MMR diversity (1.0 = pure relevance, 0.0 = pure diversity) |

### Automatic Maintenance

| Task | Target layer | Trigger | Behavior |
|------|-------------|---------|----------|
| Decay cleanup | L1 | 120+ days unaccessed, access_count=0 | Soft delete |
| Physical cleanup | All | 7 days after soft delete | Remove from disk |
| Monthly compression | L1 | Month exceeds 500 entries | LLM generates summary to replace originals |
| Pattern promotion | L1→L2 | A tag appears ≥ 3 times | LLM decides whether to distill into semantic |
| Staleness review | L2 | Monthly | LLM reviews semantic memories for accuracy |
| Index optimization | All | Every maintenance run | OPTIMIZE TABLE FINAL |

### Dependencies

| Component | Dependency | Size |
|-----------|-----------|------|
| chDB | `pip install chdb` | ~100 MB |
| Qwen3 Embedding | GGUF model + llama-cpp-python | ~350 MB + ~50 MB |
| CLI | typer + rich | ~2 MB |
| JS plugin | Node.js built-in modules only | 0 |