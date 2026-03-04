/**
 * ClickMem Hook Handler for OpenClaw
 *
 * Bridges OpenClaw events to the clickmem CLI:
 * - On bootstrap/new: exports memory context to the workspace
 * - On session end: stores session summary as episodic memory
 */

const { execFileSync } = require("child_process");
const path = require("path");

// Resolve the memory CLI from the clickmem venv
const CLICKMEM_ROOT = path.resolve(__dirname, "..");
const MEMORY_BIN = path.join(CLICKMEM_ROOT, ".venv", "bin", "memory");

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
 * Handle OpenClaw events.
 * @param {string} event - Event name (e.g. "agent:bootstrap")
 * @param {object} context - Event context from OpenClaw
 */
function handle(event, context) {
  const workspacePath = context.workspacePath || context.workspace_path || "";

  switch (event) {
    case "agent:bootstrap":
    case "command:new":
    case "command:reset":
      if (workspacePath) {
        run(["export-context", workspacePath, "--json"]);
      }
      break;

    default:
      // Unknown event — ignore silently
      break;
  }
}

module.exports = { handle };
