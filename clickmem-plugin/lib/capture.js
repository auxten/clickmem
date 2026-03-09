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

export function buildCaptureHandler(cfg, run) {
  return async (event, ctx) => {
    if (!event?.success || !event?.messages?.length) return;

    // Skip cron/exec events
    if (ctx?.messageProvider === "exec-event" || ctx?.messageProvider === "cron-event") return;

    const lastTurn = getLastTurn(event.messages);
    if (!lastTurn.length) return;

    const text = lastTurn.map(m => `${m.role}: ${m.content}`).join("\n");
    if (text.length < 20) {
      console.log("[clickmem] capture skipped: text too short (%d chars)", text.length);
      return;
    }

    const truncated = text.length > 2000 ? text.slice(0, 2000) + "..." : text;

    console.log("[clickmem] capture: extracting from %d chars", truncated.length);

    try {
      const result = await run(["extract", truncated, "--json"]);
      const ids = JSON.parse(result);
      console.log("[clickmem] capture: extracted %d memories", ids.length);
    } catch (extractErr) {
      console.error("[clickmem] capture: extract failed:", extractErr.message);
      try {
        await run([
          "remember", truncated,
          "--layer", "episodic",
          "--category", "event",
          "--json"
        ]);
        console.log("[clickmem] capture: stored raw (extract unavailable)");
      } catch (storeErr) {
        console.error("[clickmem] capture FAILED: could not store memory:", storeErr.message);
        console.error("[clickmem] Is the ClickMem API server running? (port 9527)");
      }
    }
  };
}
