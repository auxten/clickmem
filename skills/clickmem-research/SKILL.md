---
name: clickmem-research
description: |
  Run ClickMem auto-research: sample conversations, generate recall probes, 
  evaluate recall quality, check system health, and submit findings as a 
  GitHub issue. Use when the user mentions "clickmem research", "recall quality", 
  "memory audit", or "auto-research".
---

# ClickMem Auto-Research

Run the steps below in order. Stop early if a step fails critically.

## Step 1: Health Check

```bash
memory status
```

Check:
- Is the server reachable?
- How many unprocessed raws are accumulating? (high count = refinement stalled)
- Is recent data flowing in? (check last raw timestamp)
- Compare with previous reports in `~/.clickmem/reviews/` — is unprocessed count trending down?

## Step 2: Generate Probes

```bash
memory research probes --samples 10 --days 7
```

Samples 10 recent conversations and uses LLM to design recall probes with a critical mindset.

## Step 3: Evaluate Probes

```bash
memory research eval
```

Runs probes against live recall. Classifies each as pass/partial/fail and attributes failures.

## Step 4: Read Report

Read the latest report:
```bash
ls -t ~/.clickmem/reviews/report-*.md | head -1
```
Then read that file.

## Step 5: Critical Analysis

With the report, analyze:
- What percentage of probes passed? (target: >60%)
- Are failures systemic (same category repeating) or scattered?
- Is the data gap shrinking compared to previous reports?
- Any health issues (unprocessed backlog, server down)?

## Step 6: Privacy Masking

**CRITICAL**: Before submitting anything to GitHub, mask ALL private information. This issue will be public.

Replace the following patterns in ALL output text:
- **IP addresses**: `100.86.126.80` → `[INTERNAL_IP]`, `192.168.x.x` → `[LAN_IP]`
- **Hostnames**: actual machine names → `[HOST_A]`, `[HOST_B]`
- **Usernames**: system usernames in paths → `[USER]` (e.g. `/Users/auxten/` → `/Users/[USER]/`)
- **File paths**: full home paths → relative or masked (e.g. `~/.claude/projects/...`)
- **API keys/tokens**: any string that looks like a key → `[REDACTED]`
- **SSH credentials**: `user@host` → `[USER]@[HOST]`
- **Git remotes with auth tokens**: mask the token portion
- **Internal project names**: if not open-source, generalize (e.g. "internal iOS app" instead of exact name)
- **Conversation content**: never include raw conversation text — only describe the pattern (e.g. "a conversation about deployment configuration" not the actual text)

**Test**: Before creating the issue, review the full body text and confirm no PII remains.

## Step 7: Submit GitHub Issue

Detect the repo from git remote, falling back to `auxten/clickmem`:
```bash
REPO=$(git remote get-url origin 2>/dev/null | sed -E 's|.*github\.com[:/]||;s|\.git$||' || echo "auxten/clickmem")
```

If there are findings worth reporting:
```bash
gh issue create --repo "$REPO" --title "Auto-Research Report: YYYY-MM-DD" --body "..."
```

Issue body format:
- **Summary**: pass/partial/fail counts, pass rate vs target
- **Health**: unprocessed count, data volume, server status
- **Failed Probes**: list with failure categories (use MASKED queries — describe the pattern, not literal content)
- **Systemic Issues**: patterns in failures (mechanism-level, no private details)
- **Suggestions**: parameter/mechanism improvements
- **Comparison**: delta from previous report if available

## Step 8: Record Quality Probes

Any new probes revealing interesting patterns should be appended to `docs/recall-test-cases.md` for regression tracking. These probes stay local (not submitted to GitHub) and CAN contain specific details since they're in the repo owner's control.
