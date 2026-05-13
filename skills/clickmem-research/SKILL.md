---
name: clickmem-research
description: |
  Audit the ClickMem memory store for quality and contradictions. Sample recent
  recall traces, look for failing or low-confidence retrievals, surface
  conflicted memories, and propose resolutions. Use when the user mentions
  "memory audit", "recall quality", "clickmem research", or "clean up the
  memory store".
---

# ClickMem — Memory Audit

A lightweight quality pass over the ClickMem store. The server runs zero LLM, so this skill uses the agent's current session model to score recall behaviour and propose fixes. Everything reads through the public REST + MCP surface; no direct chDB access.

Run the steps in order. Stop early if a step fails critically (server unreachable, no data).

---

## Step 1: Health check

```bash
clickmem service status
curl -s http://127.0.0.1:9527/v1/health | jq
curl -s http://127.0.0.1:9527/v1/stats/overview | jq
```

Confirm:
- Server is reachable on port 9527.
- Memory counts are non-zero; otherwise there's nothing to audit.
- Active backend (`local` chDB vs `clickhouse`) matches what the user expects.

---

## Step 2: Sample recent recall traces

The server logs every recall to the `events` table. Pull the last N traces and the per-candidate scoring:

```bash
clickmem recall-trace --sample 20 --since 7d
# or
curl -s -X POST http://127.0.0.1:9527/v1/recall/trace \
  -H 'Content-Type: application/json' \
  -d '{"sample": 20, "since": "7d"}' | jq
```

Each trace returns the query, the top hits, and the score breakdown (cosine × project multiplier × privacy filter × pinned boost). Look for:

- **Low top-1 cosine** (< 0.5) — the agent asked something with no anchor in the store.
- **Project mismatches** — top hits came from `other-project` (×0.0) and got filtered out.
- **Stale top hits** — the top memory is `status='contracted'` or hasn't been updated in months and yet is winning.

---

## Step 3: Surface unresolved conflicts

```bash
clickmem conflicts --json
# or
curl -s http://127.0.0.1:9527/v1/conflicts | jq
```

For each pair, fetch both memories:

```bash
clickmem show <id_a> --history
clickmem show <id_b> --history
```

Decide for each pair:
- One supersedes the other → propose `clickmem resolve <loser> --revise <winner>`.
- Both are correct under different scopes (different projects, privacy levels) → propose `clickmem resolve <a> --allow <b>`.
- Neither is right anymore → propose `clickmem forget` on both with a reason.

**Do not execute resolutions automatically.** Surface the proposal to the user and let them confirm.

---

## Step 4: Critical analysis

With the trace sample + conflicts list, summarise:

- **Recall pass rate**: fraction of traces where top-1 cosine ≥ 0.6 and the hit belongs to the same project. Target ≥ 60 %.
- **Conflict count**: unresolved pairs grouped by project; trend vs the previous audit if `~/.clickmem/audits/` has prior reports.
- **Garbage indicators**: memories with zero recall hits in the last 30 days (candidates for `forget`), pinned memories that no longer match recent traces (candidates for `unpin` + `edit`).
- **Blacklist health**: any patterns with `hit_count = 0` in the last 30 d are dead — propose removal.

---

## Step 5: Privacy masking (before any public artefact)

If the user asks you to file a GitHub issue or share the audit externally, mask aggressively. Treat any audit artefact as public.

- IP / hostnames: `100.86.x.x` → `[INTERNAL_IP]`, machine names → `[HOST_A]`.
- Usernames in paths: `/Users/auxten/` → `/Users/[USER]/`.
- API keys, tokens, SSH creds: `[REDACTED]`.
- Memory contents: describe the pattern (e.g. "decision about deployment target"), never paste literal text.
- Internal project names: generalise unless the repo itself is open-source.

Re-read the full body before submitting and confirm no PII remains.

---

## Step 6: Optional — file a GitHub issue

If the audit reveals systemic issues (recall pass rate dropping, conflict backlog growing, an adapter consistently producing low-quality commits), file an issue against the active repo:

```bash
REPO=$(git remote get-url origin 2>/dev/null \
  | sed -E 's|.*github\.com[:/]||;s|\.git$||' \
  || echo "auxten/clickmem")

gh issue create --repo "$REPO" \
  --title "ClickMem audit: YYYY-MM-DD" \
  --body "..."
```

Issue body sections:
- **Summary** — pass rate, conflict count, delta from previous audit.
- **Health** — backend, memory count, server status.
- **Top failures** — masked patterns, not literal queries.
- **Suggestions** — config tweaks (`CLICKMEM_CONFLICT_THRESHOLD`), proposed resolutions for top conflicts, adapter-specific issues.

---

## Step 7: Record probes locally

If the audit surfaces probes that should regress-test future changes, append them to `docs/recall-test-cases.md` (or wherever the repo keeps them). These stay in-repo, so literal queries are fine — but the GitHub issue body still must be masked.
