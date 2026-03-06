import { execFile, execFileSync } from "child_process";
import { request as httpRequest } from "http";
import { readFileSync } from "fs";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const LIB_DIR = dirname(__filename);
const PLUGIN_DIR = dirname(LIB_DIR);
const CLICKMEM_ROOT = resolve(PLUGIN_DIR, "..");
const MEMORY_BIN = join(CLICKMEM_ROOT, ".venv", "bin", "memory");

let _llmEnv = {};

try {
  const raw = readFileSync(join(CLICKMEM_ROOT, "llm.json"), "utf-8");
  const cfg = JSON.parse(raw);
  if (cfg.baseUrl)    _llmEnv.OPENAI_API_BASE = cfg.baseUrl;
  if (cfg.apiKey)     _llmEnv.OPENAI_API_KEY = cfg.apiKey;
  if (cfg.model)      _llmEnv.CLICKMEM_LLM_MODEL = cfg.model;
  if (cfg.mode)       _llmEnv.CLICKMEM_LLM_MODE = cfg.mode;
  if (cfg.localModel) _llmEnv.CLICKMEM_LOCAL_MODEL = cfg.localModel;
  if (cfg.logLevel)   _llmEnv.CLICKMEM_LOG_LEVEL = cfg.logLevel;
} catch { /* no llm.json — rely on process.env */ }

function execEnv() {
  return { ...process.env, ..._llmEnv };
}

// ---------------------------------------------------------------------------
// Server configuration
// ---------------------------------------------------------------------------

const SERVER_PORT = parseInt(process.env.CLICKMEM_SERVER_PORT || _llmEnv.CLICKMEM_SERVER_PORT || "9527", 10);
const SERVER_HOST = "127.0.0.1";

// ---------------------------------------------------------------------------
// HTTP client — talk to `memory serve` directly (no Python startup)
// ---------------------------------------------------------------------------

function httpPost(path, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = httpRequest({
      hostname: SERVER_HOST, port: SERVER_PORT,
      path, method: "POST",
      headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) },
      timeout: 60_000,
    }, (res) => {
      let buf = "";
      res.on("data", (chunk) => buf += chunk);
      res.on("end", () => {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(buf);
        else reject(new Error(`HTTP ${res.statusCode}: ${buf}`));
      });
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("HTTP timeout")); });
    req.write(data);
    req.end();
  });
}

function httpDelete(path) {
  return new Promise((resolve, reject) => {
    const req = httpRequest({
      hostname: SERVER_HOST, port: SERVER_PORT,
      path, method: "DELETE",
      timeout: 60_000,
    }, (res) => {
      let buf = "";
      res.on("data", (chunk) => buf += chunk);
      res.on("end", () => {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(buf);
        else reject(new Error(`HTTP ${res.statusCode}: ${buf}`));
      });
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("HTTP timeout")); });
    req.end();
  });
}

// ---------------------------------------------------------------------------
// CLI args → HTTP API translation
// ---------------------------------------------------------------------------

function parseFlag(args, flag, defaultVal) {
  const idx = args.indexOf(flag);
  if (idx === -1 || idx + 1 >= args.length) return defaultVal;
  return args[idx + 1];
}

function hasFlag(args, flag) {
  return args.includes(flag);
}

async function tryServerCall(args) {
  const cmd = args[0];

  if (cmd === "recall") {
    const query = args[1] || "";
    const body = {
      query,
      top_k: parseInt(parseFlag(args, "--top-k", "10"), 10),
      min_score: parseFloat(parseFlag(args, "--min-score", "0")),
      layer: parseFlag(args, "--layer", null),
      category: parseFlag(args, "--category", null),
    };
    const resp = JSON.parse(await httpPost("/v1/recall", body));
    return JSON.stringify(resp.memories || []);
  }

  if (cmd === "extract") {
    const text = args[1] || "";
    const body = { text, session_id: parseFlag(args, "--session-id", "") };
    const resp = JSON.parse(await httpPost("/v1/extract", body));
    return JSON.stringify(resp.ids || []);
  }

  if (cmd === "remember") {
    const content = args[1] || "";
    const body = {
      content,
      layer: parseFlag(args, "--layer", "semantic"),
      category: parseFlag(args, "--category", "knowledge"),
      tags: [],
      no_upsert: hasFlag(args, "--no-upsert"),
    };
    const tagsRaw = parseFlag(args, "--tags", "");
    if (tagsRaw) body.tags = tagsRaw.split(",").map(t => t.trim()).filter(Boolean);
    const resp = await httpPost("/v1/remember", body);
    return resp;
  }

  if (cmd === "forget") {
    const memoryId = args[1] || "";
    const resp = await httpDelete(`/v1/forget/${encodeURIComponent(memoryId)}`);
    return resp;
  }

  if (cmd === "maintain") {
    const body = { dry_run: hasFlag(args, "--dry-run") };
    const resp = await httpPost("/v1/maintain", body);
    return resp;
  }

  if (cmd === "status") {
    const resp = await httpPost("/v1/status", {});
    return resp;
  }

  return null;
}

// ---------------------------------------------------------------------------
// CLI fallback — spawn Python process (original behavior)
// ---------------------------------------------------------------------------

const TIMEOUT_FAST = 30_000;
const TIMEOUT_LLM  = 120_000;

function timeoutFor(args) {
  const cmd = args[0];
  return (cmd === "extract" || cmd === "maintain" || cmd === "remember") ? TIMEOUT_LLM : TIMEOUT_FAST;
}

function cliExec(args) {
  return new Promise((resolve, reject) => {
    execFile(MEMORY_BIN, args, { timeout: timeoutFor(args), env: execEnv() }, (err, stdout) => {
      if (err) reject(err);
      else resolve(stdout.trim());
    });
  });
}

// ---------------------------------------------------------------------------
// Public API — server-first, CLI fallback
// ---------------------------------------------------------------------------

export function runSync(args) {
  return execFileSync(MEMORY_BIN, args, { encoding: "utf-8", timeout: timeoutFor(args), env: execEnv() }).trim();
}

let _queue = Promise.resolve();

export function runAsync(args) {
  const wantsJson = hasFlag(args, "--json");
  const job = _queue.then(async () => {
    // Try the HTTP server first (instant, no Python startup)
    if (wantsJson) {
      try {
        return await tryServerCall(args);
      } catch {
        // Server not running or call failed — fall through to CLI
      }
    }
    return cliExec(args);
  });
  _queue = job.catch(() => {});
  return job;
}
