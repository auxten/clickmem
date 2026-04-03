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

## Step 6: Submit GitHub Issue

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
- **Failed Probes**: list with failure categories
- **Systemic Issues**: patterns in failures
- **Suggestions**: parameter/mechanism improvements
- **Comparison**: delta from previous report if available

## Step 7: Record Quality Probes

Any new probes revealing interesting patterns should be appended to `docs/recall-test-cases.md` for regression tracking.
