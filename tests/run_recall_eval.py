#!/usr/bin/env python3
"""Run all 25 recall test cases from recall-test-cases.md against the live server.

Usage: python tests/run_recall_eval.py [--server URL]
Default server: http://100.86.126.80:9527
"""

from __future__ import annotations
import sys, os, json, time, argparse, requests

CASES = [
    # (id, category, query, probe_words, top_k)
    (1, "Infra", "OpenClaw 部署在哪台机器上，怎么 SSH 登录", ["tong", "mini", "100.86.126.80"], 3),
    (2, "Infra", "登录 mini 主机用什么用户名", ["tong", "tong@mini"], 3),
    (3, "Infra", "mini 的 Tailscale IP 地址", ["100.86.126.80"], 3),
    (4, "Infra", "ssh 到 mini 的命令", ["tong@mini", "100.86.126.80"], 3),
    (5, "Infra", "clickmem server 跑在哪个端口", ["9527"], 3),
    (6, "Infra", "ClickHouse Cloud 的端口号是多少", ["9440", "secure"], 3),
    (7, "Infra", "Chrome CDP 用的哪个端口", ["18800", "18801"], 3),
    (8, "Bug", "WhatsApp channel 之前崩溃是怎么修的", ["extraDirs", "openclaw.json"], 3),
    (9, "Bug", "botschat 发图片失败是什么原因", ["mediaUrls", "deliver"], 3),
    (10, "Bug", "chdb Connection 的 __del__ 之前出了什么问题", ["__del__", "lifecycle", "race condition"], 3),
    (11, "Bug", "AppsFlyer 收入没上报是怎么修的", ["trackSubscription", "never invoked"], 3),
    (12, "Bug", "BotChat 一直重启是怎么回事", ["state sync", "health monitor", "restart"], 3),
    (13, "Bug", "wrangler 部署后旧的 cron trigger 还在跑", ["crons = []", "commenting out does not delete"], 3),
    (14, "Decision", "X 上发帖的策略后来改成什么了", ["Reply Guy", "high-volume replies", "disable"], 3),
    (15, "Decision", "安装 plugin 时为什么不能删除 settings.json", ["coexist", "inline hooks", "current session"], 3),
    (16, "Decision", "clickmem Claude Code plugin 为什么加载失败", [".cursor-plugin", ".claude-plugin"], 3),
    (17, "Principle", "我们开发产品遇到 bug 应该怎么处理", ["product", "opportunity", "bug less"], 3),
    (18, "Principle", "我的 agent 开发原则", ["agent", "Decision Tree", "knowledge scope", "workflow"], 5),
    (19, "Principle", "配置应该用环境变量还是配置文件", ["environment variable", "defaults"], 3),
    (20, "Principle", "可以手动修改 clickmem 里的记忆数据吗", ["never", "禁止", "手动修改"], 3),
    (21, "Project", "clickmem 支持哪些 AI coding agent", ["Claude Code", "Cursor", "OpenClaw"], 3),
    (22, "Project", "改了 botschat 源码之后怎么 rebuild", ["dist", "channel.js", "rebuild"], 3),
    (23, "Project", "AiNote 录音按钮不够明显怎么优化的", ["animation", "glow", "orange"], 3),
    (24, "Project", "代码改完之后 deploy 到 mini 的完整流程", ["commit", "push", "ssh", "uv pip install"], 3),
    (25, "Identity", "我叫什么名字", ["Auxten", "Pengcheng"], 5),
]


def check_probes(results: list[dict], probes: list[str]) -> tuple[list[str], list[str]]:
    """Check which probe words appear in top-K results content."""
    combined = ""
    for r in results:
        combined += " " + r.get("content", "")
        meta = r.get("metadata", {})
        if isinstance(meta, dict):
            combined += " " + meta.get("reasoning", "")
            combined += " " + str(meta.get("category", ""))
    combined_lower = combined.lower()

    found, missing = [], []
    for p in probes:
        if p.lower() in combined_lower:
            found.append(p)
        else:
            missing.append(p)
    return found, missing


def run_recall(server: str, query: str, top_k: int, retries: int = 2) -> list[dict]:
    """Call /v1/recall on the remote server with retry."""
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{server}/v1/recall",
                json={"query": query, "top_k": top_k},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("memories", [])
        except Exception as e:
            if attempt < retries:
                time.sleep(3)
                continue
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default="http://100.86.126.80:9527")
    args = parser.parse_args()

    print(f"Server: {args.server}")
    health = requests.get(f"{args.server}/v1/health", timeout=5).json()
    print(f"Health: {health}\n")

    pass_count = partial_count = fail_count = 0
    results_log = []

    hdr = f"{'#':>3} {'Cat':<10} {'Query':<45} {'Status':<8} {'Found':<5} {'Details'}"
    print("=" * 120)
    print(hdr)
    print("=" * 120)

    for case_id, cat, query, probes, top_k in CASES:
        t0 = time.monotonic()
        try:
            results = run_recall(args.server, query, top_k)
        except Exception as e:
            results = []
            print(f"{case_id:>3} {cat:<10} {query:<45} ERROR    {e}")
            fail_count += 1
            continue
        elapsed = (time.monotonic() - t0) * 1000

        found, missing = check_probes(results, probes)
        ratio = len(found) / len(probes) if probes else 1.0

        if ratio >= 1.0:
            status = "✅ PASS"
            pass_count += 1
        elif ratio > 0:
            status = "⚠️ PART"
            partial_count += 1
        else:
            status = "❌ FAIL"
            fail_count += 1

        detail = f"missing: {missing}" if missing else ""
        print(f"{case_id:>3} {cat:<10} {query:<45} {status:<8} {len(found)}/{len(probes)}  {detail}")

        results_log.append({
            "id": case_id,
            "category": cat,
            "query": query,
            "top_k": top_k,
            "probes": probes,
            "found": found,
            "missing": missing,
            "status": "pass" if ratio >= 1.0 else ("partial" if ratio > 0 else "fail"),
            "result_count": len(results),
            "elapsed_ms": round(elapsed, 1),
            "top_results": [
                {
                    "entity_type": r.get("entity_type", r.get("layer", "")),
                    "score": round(r.get("final_score", r.get("score", 0)), 4),
                    "content": r.get("content", "")[:200],
                }
                for r in results[:top_k]
            ],
        })

    total = len(CASES)
    print("=" * 120)
    print(f"\nSummary: {pass_count}/{total} PASS, {partial_count}/{total} PARTIAL, {fail_count}/{total} FAIL")
    print(f"Pass rate: {pass_count/total*100:.0f}%  |  Pass+Partial: {(pass_count+partial_count)/total*100:.0f}%")

    out_path = os.path.join(os.path.dirname(__file__), "recall_eval_results.json")
    with open(out_path, "w") as f:
        json.dump(results_log, f, indent=2, ensure_ascii=False)
    print(f"\nDetailed results → {out_path}")


if __name__ == "__main__":
    main()
