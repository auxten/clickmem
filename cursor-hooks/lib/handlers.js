/**
 * Hook handlers for ClickMem Cursor integration.
 *
 * Uses the actual Cursor hooks API:
 *   sessionStart         — recall memories, inject via additional_context
 *   beforeSubmitPrompt   — buffer the user prompt for later capture
 *   afterAgentResponse   — extract memories from the conversation turn
 *   sessionEnd / stop    — run maintenance
 */

import * as client from "./clickmem-client.js";
import { truncate, getSessionId } from "./utils.js";

const _conversationBuffers = new Map();

function _bufKey(conversationId) {
  return conversationId || "__default__";
}

function _log(msg) {
  process.stderr.write(`[clickmem-hook] ${msg}\n`);
}

// ─── sessionStart ────────────────────────────────────────────────────
// Recall relevant memories based on workspace, inject as initial context.

export async function handleSessionStart(input) {
  const sessionId = getSessionId(input.workspace_roots);
  const query = sessionId + " recent work context";
  const memories = await client.recall(query, { topK: 5, minScore: 0.25 });

  if (!memories.length) return {};

  const lines = memories.map((r) => {
    const score = Math.round((r.final_score || 0) * 100);
    const shortId = (r.id || "").slice(0, 8);
    return `- [id:${shortId}] [${r.layer}/${r.category || ""}] ${r.content} (${score}%)`;
  });

  const context = [
    "<clickmem-context>",
    "Background from long-term memory. Use silently unless directly relevant.",
    "",
    ...lines,
    "</clickmem-context>",
  ].join("\n");

  _log(`sessionStart: injected ${memories.length} memories`);
  return { additional_context: context };
}

// ─── beforeSubmitPrompt ──────────────────────────────────────────────
// Buffer the user prompt so afterAgentResponse can build the full turn.

export async function handleBeforeSubmitPrompt(input) {
  const key = _bufKey(input.conversation_id);
  _conversationBuffers.set(key, {
    prompt: input.prompt || "",
    model: input.model,
    ts: Date.now(),
  });
  return { continue: true };
}

// ─── afterAgentResponse ──────────────────────────────────────────────
// Combine buffered user prompt + agent response, extract memories.

export async function handleAfterAgentResponse(input) {
  const key = _bufKey(input.conversation_id);
  const buf = _conversationBuffers.get(key);
  const responseText = input.text || "";

  if (!responseText || responseText.length < 20) return null;

  const userPart = buf?.prompt || "";
  const turnText = `user: ${userPart}\nassistant: ${responseText}`;
  const truncated = truncate(turnText, 4000);

  if (truncated.length < 40) return null;

  const sessionId = getSessionId(input.workspace_roots);

  const ids = await client.extract(truncated, sessionId);
  if (ids.length > 0) {
    _log(`extracted ${ids.length} memories from ${truncated.length} chars`);
  } else if (turnText.length > 200) {
    const stored = await client.remember(truncate(turnText, 2000), {
      layer: "episodic",
      category: "event",
    });
    if (stored) _log("stored raw memory (extract returned 0)");
  }

  _conversationBuffers.delete(key);
  return null;
}

// ─── stop ────────────────────────────────────────────────────────────

export async function handleStop(input) {
  const result = await client.maintain();
  if (result) {
    const parts = [];
    if (result.stale_cleaned) parts.push("stale=" + result.stale_cleaned);
    if (result.deleted_purged) parts.push("purged=" + result.deleted_purged);
    if (result.promoted) parts.push("promoted=" + result.promoted);
    if (parts.length) _log("maintenance: " + parts.join(", "));
  }
  return {};
}

// ─── sessionEnd ──────────────────────────────────────────────────────

export async function handleSessionEnd(input) {
  _conversationBuffers.delete(_bufKey(input.session_id));
  await client.maintain();
  return {};
}

// ─── Permission hooks (pass-through) ────────────────────────────────

export function handleBeforeShellExecution() {
  return { permission: "allow" };
}

export function handleBeforeMCPExecution() {
  return { permission: "allow" };
}

export function handleBeforeReadFile() {
  return { permission: "allow" };
}

export function handleBeforeTabFileRead() {
  return { permission: "allow" };
}

// ─── Passthrough hooks (no-op) ──────────────────────────────────────

export function handleAfterAgentThought() { return null; }
export function handleAfterShellExecution() { return null; }
export function handleAfterMCPExecution() { return null; }
export function handleAfterFileEdit() { return null; }
export function handleAfterTabFileEdit() { return null; }

// ─── Router ─────────────────────────────────────────────────────────

const HANDLERS = {
  sessionStart: handleSessionStart,
  sessionEnd: handleSessionEnd,
  beforeSubmitPrompt: handleBeforeSubmitPrompt,
  afterAgentResponse: handleAfterAgentResponse,
  afterAgentThought: handleAfterAgentThought,
  beforeShellExecution: handleBeforeShellExecution,
  afterShellExecution: handleAfterShellExecution,
  beforeMCPExecution: handleBeforeMCPExecution,
  afterMCPExecution: handleAfterMCPExecution,
  beforeReadFile: handleBeforeReadFile,
  afterFileEdit: handleAfterFileEdit,
  stop: handleStop,
  beforeTabFileRead: handleBeforeTabFileRead,
  afterTabFileEdit: handleAfterTabFileEdit,
};

export function routeHookHandler(hookName, input) {
  const handler = HANDLERS[hookName];
  if (!handler) {
    _log(`unknown hook: ${hookName}`);
    return null;
  }
  return handler(input);
}
