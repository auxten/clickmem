/**
 * ClickMem HTTP client — talks to `memory serve` on localhost:9527.
 *
 * All calls are fail-open: on error, the caller gets null/empty
 * so Cursor is never blocked.
 */

import { request as httpRequest } from "http";

const PORT = parseInt(process.env.CLICKMEM_SERVER_PORT || "9527", 10);
const HOST = process.env.CLICKMEM_SERVER_HOST || "127.0.0.1";
const TIMEOUT_MS = 30_000;

function httpPost(path, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = httpRequest(
      {
        hostname: HOST,
        port: PORT,
        path,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
        },
        timeout: TIMEOUT_MS,
      },
      (res) => {
        let buf = "";
        res.on("data", (chunk) => (buf += chunk));
        res.on("end", () => {
          if (res.statusCode >= 200 && res.statusCode < 300) resolve(buf);
          else reject(new Error(`HTTP ${res.statusCode}: ${buf}`));
        });
      }
    );
    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("HTTP timeout"));
    });
    req.write(data);
    req.end();
  });
}

/**
 * Semantic recall — search for memories matching a query.
 * Returns array of memory objects or [] on failure.
 */
export async function recall(query, { topK = 5, minScore = 0.3 } = {}) {
  try {
    const resp = JSON.parse(
      await httpPost("/v1/recall", {
        query,
        top_k: topK,
        min_score: minScore,
      })
    );
    return resp.memories || [];
  } catch {
    return [];
  }
}

/**
 * Extract structured memories from conversation text.
 * Returns array of IDs or [] on failure.
 */
export async function extract(text, sessionId = "") {
  try {
    const resp = JSON.parse(
      await httpPost("/v1/extract", { text, session_id: sessionId })
    );
    return resp.ids || [];
  } catch {
    return [];
  }
}

/**
 * Store a raw memory as fallback when extract fails.
 */
export async function remember(content, { layer = "episodic", category = "event" } = {}) {
  try {
    await httpPost("/v1/remember", { content, layer, category, tags: ["cursor-auto"] });
    return true;
  } catch {
    return false;
  }
}

/**
 * Run lightweight maintenance (stale cleanup, promote, etc.).
 */
export async function maintain() {
  try {
    const resp = JSON.parse(await httpPost("/v1/maintain", { dry_run: false }));
    return resp;
  } catch {
    return null;
  }
}

/**
 * Quick health check — returns true if the server is reachable.
 */
export async function isAlive() {
  try {
    await httpPost("/v1/status", {});
    return true;
  } catch {
    return false;
  }
}
