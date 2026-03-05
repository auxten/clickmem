import { buildRecallHandler } from "./lib/recall.js";
import { buildCaptureHandler } from "./lib/capture.js";
import { runAsync } from "./lib/cli.js";

const DEFAULT_CONFIG = {
  autoRecall: true,
  autoCapture: true,
  maxRecallResults: 5,
  minScore: 0.3,
};

export default {
  id: "clickmem",
  name: "ClickMem",
  description: "Local semantic memory powered by chDB + Qwen3 embeddings",
  kind: "memory",

  register(api) {
    const raw = api.pluginConfig || {};
    const cfg = { ...DEFAULT_CONFIG, ...raw };
    const log = api.logger ?? console;

    // ── Recall: before every AI turn ──
    if (cfg.autoRecall) {
      api.on("before_agent_start", buildRecallHandler(cfg, runAsync));
    }

    // ── Capture: after every AI turn ──
    if (cfg.autoCapture) {
      api.on("agent_end", buildCaptureHandler(cfg, runAsync));
    }

    // ── Tools: agent can use directly ──
    api.registerTool({
      name: "clickmem_search",
      label: "Search Memory",
      description: "Semantic search over long-term memory",
      parameters: { type: "object", properties: {
        query: { type: "string", description: "Search query" },
        top_k: { type: "number", description: "Max results (default 5)" }
      }, required: ["query"] },
      async execute(id, params) {
        const args = ["recall", params.query, "--top-k", String(params.top_k || 5), "--json"];
        const result = await runAsync(args);
        return { content: [{ type: "text", text: result }] };
      }
    }, { name: "clickmem_search" });

    api.registerTool({
      name: "clickmem_store",
      label: "Store Memory",
      description: "Store important information to long-term memory",
      parameters: { type: "object", properties: {
        content: { type: "string", description: "Memory content" },
        layer: { type: "string", description: "working/episodic/semantic (default: semantic)" },
        category: { type: "string", description: "preference/decision/knowledge/person/project/workflow/insight/context" }
      }, required: ["content"] },
      async execute(id, params) {
        const args = ["remember", params.content,
          "--layer", params.layer || "semantic",
          "--category", params.category || "knowledge",
          "--json"];
        const result = await runAsync(args);
        return { content: [{ type: "text", text: result }] };
      }
    }, { name: "clickmem_store" });

    api.registerTool({
      name: "clickmem_forget",
      label: "Forget Memory",
      description: "Delete a memory by ID or content. Pass a UUID/prefix from recall context (e.g. '25fb9684'), or describe the memory content to search and delete.",
      parameters: { type: "object", properties: {
        memory_id: { type: "string", description: "Memory UUID or prefix (from recall context id:XXXXXXXX), OR text description of the memory to find and delete" }
      }, required: ["memory_id"] },
      async execute(id, params) {
        const result = await runAsync(["forget", params.memory_id, "--json"]);
        return { content: [{ type: "text", text: result }] };
      }
    }, { name: "clickmem_forget" });

    // ── Session reset: hook ──
    api.registerHook(["command:new", "command:reset"], (event) => {
      log.info("[clickmem] session reset, memory persisted");
    }, { name: "clickmem-session", description: "ClickMem session boundary handler" });

    log.info("[clickmem] plugin registered (recall=" + cfg.autoRecall + ", capture=" + cfg.autoCapture + ")");
  }
};
