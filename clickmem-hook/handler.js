/**
 * ClickMem Hook Handler for OpenClaw
 *
 * Bridges OpenClaw events to the clickmem CLI:
 * - On bootstrap/new/reset: exports memory context to the workspace
 */

import { execFileSync } from "child_process";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const CLICKMEM_ROOT = resolve(__dirname, "..");
const MEMORY_BIN = join(CLICKMEM_ROOT, ".venv", "bin", "memory");

function run(args, options = {}) {
  try {
    const result = execFileSync(MEMORY_BIN, args, {
      encoding: "utf-8",
      timeout: 30000,
      ...options,
    });
    return result.trim();
  } catch (err) {
    console.error(`[clickmem] Error running: memory ${args.join(" ")}`);
    console.error(`[clickmem] ${err.message}`);
    return null;
  }
}

/**
 * Handle OpenClaw hook events.
 * @param {object} event - Hook event: {type, action, sessionKey, context, timestamp, messages}
 */
export default function handle(event) {
  const eventKey = `${event.type}:${event.action}`;
  const workspacePath =
    event.context?.workspacePath || event.context?.workspace_path || "";

  switch (eventKey) {
    case "agent:bootstrap":
    case "command:new":
    case "command:reset":
      if (workspacePath) {
        run(["export-context", workspacePath, "--json"]);
      }
      break;

    default:
      break;
  }
}
