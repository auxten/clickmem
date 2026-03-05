/**
 * Strip OpenClaw metadata prefix from event.prompt.
 * The prompt often arrives as:
 *   "Conversation info (untrusted metadata):\n```json\n{...}\n```\n\n<actual user message>"
 * We extract just the user message for semantic search.
 */
function extractUserQuery(rawPrompt) {
  // Remove leading metadata block (```json ... ```)
  let q = rawPrompt.replace(/^Conversation info[^\n]*\n```json\n[\s\S]*?```\n*/i, "").trim();
  // Truncate to reasonable length for search query
  if (q.length > 500) q = q.slice(0, 500);
  return q;
}

export function buildRecallHandler(cfg, run) {
  return async (event, ctx) => {
    if (!event?.prompt || event.prompt.length < 2) return;

    const query = extractUserQuery(event.prompt);
    if (query.length < 2) {
      console.log("[clickmem] recall skipped: query too short (%d chars)", query.length);
      return;
    }

    console.log("[clickmem] recall query: %s", query.slice(0, 80));

    let results;
    try {
      results = JSON.parse(await run([
        "recall", query,
        "--top-k", String(cfg.maxRecallResults),
        "--min-score", String(cfg.minScore),
        "--json"
      ]));
    } catch (err) {
      console.error("[clickmem] recall failed:", err.message);
      return;
    }

    if (!results.length) {
      console.log("[clickmem] recall: 0 results");
      return;
    }

    const lines = results.map(r => {
      const score = Math.round(r.final_score * 100);
      const shortId = r.id.slice(0, 8);
      return `- [id:${shortId}] [${r.layer}/${r.category}] ${r.content} (${score}%)`;
    });

    console.log("[clickmem] recall: %d results injected", results.length);

    const context = [
      "<clickmem-context>",
      "Background context from long-term memory. Use silently unless directly relevant.",
      "",
      ...lines,
      "</clickmem-context>"
    ].join("\n");

    return { prependContext: context };
  };
}
