/// <reference types="bun-types-no-globals/lib/index.d.ts" />

/**
 * ClickMem stop hook — ingests conversation transcripts into ClickMem.
 *
 * On conversation completion, parses the JSONL transcript, extracts
 * user/assistant messages, and fires a background ingest request.
 * The hook returns immediately so Cursor is never blocked.
 */

import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { stdin } from "bun";

const STATE_PATH = resolve(".cursor/hooks/state/clickmem.json");
const MAX_INGEST_CHARS = 16_000;

interface StopInput {
  conversation_id: string;
  generation_id?: string;
  status: "completed" | "aborted" | "error" | string;
  loop_count: number;
  transcript_path?: string | null;
}

interface ClickMemState {
  version: 1;
  lastProcessedGenerationId: string | null;
  lastTranscriptMtimeMs: number | null;
}

function getBaseUrl(): string {
  if (process.env.CLICKMEM_REMOTE) {
    return process.env.CLICKMEM_REMOTE.replace(/\/$/, "");
  }
  const host = process.env.CLICKMEM_SERVER_HOST || "127.0.0.1";
  const port = process.env.CLICKMEM_SERVER_PORT || "9527";
  return `http://${host}:${port}`;
}

function loadState(): ClickMemState {
  const fallback: ClickMemState = {
    version: 1,
    lastProcessedGenerationId: null,
    lastTranscriptMtimeMs: null,
  };
  if (!existsSync(STATE_PATH)) return fallback;
  try {
    const parsed = JSON.parse(readFileSync(STATE_PATH, "utf-8"));
    if (parsed.version !== 1) return fallback;
    return { ...fallback, ...parsed };
  } catch {
    return fallback;
  }
}

function saveState(state: ClickMemState): void {
  const dir = dirname(STATE_PATH);
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  writeFileSync(STATE_PATH, JSON.stringify(state, null, 2) + "\n", "utf-8");
}

function parseTranscript(path: string): string {
  const raw = readFileSync(path, "utf-8");
  const lines: string[] = [];

  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    try {
      const entry = JSON.parse(line);
      const role = entry.role;
      if (role !== "user" && role !== "assistant") continue;

      const contents = entry.message?.content;
      if (!Array.isArray(contents)) continue;

      const textParts = contents
        .filter((c: any) => c.type === "text" && c.text)
        .map((c: any) => typeof c.text === "string" ? c.text : JSON.stringify(c.text));
      if (textParts.length === 0) continue;

      let text = textParts.join("\n");
      if (role === "user") {
        const match = text.match(/<user_query>\n?([\s\S]*?)\n?<\/user_query>/);
        if (match) text = match[1];
      }
      lines.push(`${role}: ${text}`);
    } catch {
      continue;
    }
  }

  const full = lines.join("\n\n");
  if (full.length <= MAX_INGEST_CHARS) return full;
  return full.slice(-MAX_INGEST_CHARS);
}

function getTranscriptMtimeMs(path: string | null | undefined): number | null {
  if (!path) return null;
  try {
    return statSync(path).mtimeMs;
  } catch {
    return null;
  }
}

function fireAndForgetIngest(text: string, sessionId: string): void {
  const baseUrl = getBaseUrl();
  const apiKey = process.env.CLICKMEM_API_KEY;

  const args = [
    "-s", "-m", "180",
    "-X", "POST",
    `${baseUrl}/v1/ingest`,
    "-H", "Content-Type: application/json",
  ];
  if (apiKey) args.push("-H", `Authorization: Bearer ${apiKey}`);
  args.push("-d", JSON.stringify({ text, session_id: sessionId, source: "cursor" }));

  const child = spawn("curl", args, { detached: true, stdio: "ignore" });
  child.unref();
  process.stderr.write(`[clickmem] background ingest started (pid=${child.pid})\n`);
}

async function main(): Promise<number> {
  try {
    const input: StopInput = JSON.parse(await stdin.text());
    const state = loadState();

    if (input.generation_id && input.generation_id === state.lastProcessedGenerationId) {
      console.log(JSON.stringify({}));
      return 0;
    }

    state.lastProcessedGenerationId = input.generation_id ?? null;

    if (input.status !== "completed" || !input.transcript_path || !existsSync(input.transcript_path)) {
      saveState(state);
      console.log(JSON.stringify({}));
      return 0;
    }

    const mtimeMs = getTranscriptMtimeMs(input.transcript_path);
    if (mtimeMs !== null && state.lastTranscriptMtimeMs !== null && mtimeMs <= state.lastTranscriptMtimeMs) {
      saveState(state);
      console.log(JSON.stringify({}));
      return 0;
    }

    const text = parseTranscript(input.transcript_path);
    if (text.length < 50) {
      saveState(state);
      console.log(JSON.stringify({}));
      return 0;
    }

    fireAndForgetIngest(text, input.conversation_id || "");

    state.lastTranscriptMtimeMs = mtimeMs;
    saveState(state);
    console.log(JSON.stringify({}));
    return 0;
  } catch (error: any) {
    process.stderr.write(`[clickmem] stop hook error: ${error?.message || error}\n`);
    console.log(JSON.stringify({}));
    return 0;
  }
}

const exitCode = await main();
process.exit(exitCode);
