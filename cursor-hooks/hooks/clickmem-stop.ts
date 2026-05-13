/// <reference types="bun-types-no-globals/lib/index.d.ts" />

/**
 * ClickMem Cursor stop-hook — fire-and-forget POST /v1/raw.
 *
 * Design constraints (Phase 6):
 *   - No dedup-via-mtime; the server hashes (session_id, sha256(text)).
 *   - No LLM, no maintenance work, no Dream sync, no replay logic.
 *   - Target: hook returns in <50 ms with `{}` regardless of upstream latency.
 *   - On failure: log to stderr, drop the event, do NOT retry inline.
 *
 * Cursor reports a `transcript_path` JSONL file on stdin; we read it lazily
 * (only the tail of the file, capped at 16 KB) and hand it off to a
 * detached `curl` process so this script can exit immediately.
 */

import { existsSync, readFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { stdin } from "bun";

const MAX_TAIL = 16_000;

interface StopInput {
  conversation_id?: string;
  generation_id?: string;
  status?: string;
  loop_count?: number;
  transcript_path?: string | null;
}

function getBaseUrl(): string {
  const remote = process.env.CLICKMEM_REMOTE;
  if (remote) return remote.replace(/\/$/, "");
  const host = process.env.CLICKMEM_SERVER_HOST || "127.0.0.1";
  const port = process.env.CLICKMEM_SERVER_PORT || "9527";
  return `http://${host}:${port}`;
}

function tailTranscript(path: string): string {
  try {
    const raw = readFileSync(path, "utf-8");
    const lines: string[] = [];
    for (const line of raw.split("\n")) {
      if (!line.trim()) continue;
      try {
        const entry = JSON.parse(line);
        const role = entry.role;
        if (role !== "user" && role !== "assistant") continue;
        const parts = (entry.message?.content || [])
          .filter((c: any) => c && c.type === "text" && c.text)
          .map((c: any) => (typeof c.text === "string" ? c.text : ""));
        if (!parts.length) continue;
        let text = parts.join("\n");
        if (role === "user") {
          const m = text.match(/<user_query>\n?([\s\S]*?)\n?<\/user_query>/);
          if (m) text = m[1];
        }
        lines.push(`${role}: ${text}`);
      } catch {
        continue;
      }
    }
    const full = lines.join("\n\n");
    return full.length > MAX_TAIL ? full.slice(-MAX_TAIL) : full;
  } catch {
    return "";
  }
}

function fireAndForget(text: string, sessionId: string): void {
  const url = `${getBaseUrl()}/v1/raw`;
  const body = JSON.stringify({ text, session_id: sessionId, agent: "cursor" });
  const args = ["-s", "-m", "5", "-X", "POST", url, "-H", "Content-Type: application/json"];
  const apiKey = process.env.CLICKMEM_API_KEY;
  if (apiKey) args.push("-H", `Authorization: Bearer ${apiKey}`);
  args.push("-d", body);
  try {
    const child = spawn("curl", args, { detached: true, stdio: "ignore" });
    child.unref();
  } catch (e: any) {
    process.stderr.write(`[clickmem] stop-hook spawn failed: ${e?.message || e}\n`);
  }
}

async function main(): Promise<number> {
  try {
    const stdinRaw = await stdin.text();
    let input: StopInput = {};
    try {
      input = JSON.parse(stdinRaw || "{}");
    } catch {
      // hook may be invoked without a JSON payload during smoke tests
    }
    if (!input.transcript_path || !existsSync(input.transcript_path)) {
      process.stdout.write("{}");
      return 0;
    }
    const text = tailTranscript(input.transcript_path);
    if (text.length < 50) {
      process.stdout.write("{}");
      return 0;
    }
    fireAndForget(text, input.conversation_id || input.generation_id || "");
    process.stdout.write("{}");
    return 0;
  } catch (e: any) {
    process.stderr.write(`[clickmem] stop-hook error: ${e?.message || e}\n`);
    process.stdout.write("{}");
    return 0;
  }
}

const code = await main();
process.exit(code);
