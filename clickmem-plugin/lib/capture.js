function getLastTurn(messages) {
  if (!messages.length) return [];

  const last = [];
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    last.unshift(m);
    if (m.role === "user") break;
  }
  return last;
}

function contentToString(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const texts = content
      .filter(c => c && c.type === "text" && c.text)
      .map(c => String(c.text));
    if (texts.length > 0) return texts.join("\n");
    try { return JSON.stringify(content); } catch { return ""; }
  }
  if (content && typeof content === "object") {
    if (typeof content.text === "string") return content.text;
    try { return JSON.stringify(content); } catch { return ""; }
  }
  return content != null ? String(content) : "";
}

export function buildCaptureHandler(cfg, run) {
  return async (event, ctx) => {
    if (!event?.success || !event?.messages?.length) return;

    // Skip cron/exec events
    if (ctx?.messageProvider === "exec-event" || ctx?.messageProvider === "cron-event") return;

    const lastTurn = getLastTurn(event.messages);
    if (!lastTurn.length) return;

    const text = lastTurn.map(m => `${m.role}: ${contentToString(m.content)}`).join("\n");
    if (text.length < 20) {
      console.log("[clickmem] capture skipped: text too short (%d chars)", text.length);
      return;
    }

    const truncated = text.length > 4000 ? text.slice(0, 4000) + "..." : text;

    console.log("[clickmem] capture: ingesting %d chars", truncated.length);

    try {
      const result = await run(["ingest", truncated, "--source", "openclaw", "--json"]);
      const parsed = JSON.parse(result);
      const n = parsed.extracted_ids?.length || 0;
      console.log("[clickmem] capture: ingested raw_id=%s, extracted %d memories",
        (parsed.raw_id || "").slice(0, 8), n);
    } catch (ingestErr) {
      console.error("[clickmem] capture: ingest failed:", ingestErr.message);
      try {
        await run([
          "remember", truncated,
          "--layer", "episodic",
          "--category", "event",
          "--json"
        ]);
        console.log("[clickmem] capture: stored raw (ingest unavailable)");
      } catch (storeErr) {
        console.error("[clickmem] capture FAILED: could not store memory:", storeErr.message);
        const port = process.env.CLICKMEM_SERVER_PORT || "9527";
        console.error(`[clickmem] Is the ClickMem API server running? (port ${port})`);
      }
    }
  };
}
