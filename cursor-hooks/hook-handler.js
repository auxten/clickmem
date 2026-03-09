#!/usr/bin/env node

/**
 * ClickMem Cursor Hooks — Main entry point.
 *
 * Reads hook data from stdin, routes to the appropriate handler.
 * On `beforeSubmitPrompt`: recalls memories and injects context.
 * On `afterAgentResponse`: extracts memories from the conversation.
 * On `stop`: runs lightweight maintenance.
 *
 * All errors are swallowed — we never block Cursor.
 */

import { readStdin } from "./lib/utils.js";
import { routeHookHandler } from "./lib/handlers.js";

async function main() {
  try {
    const input = await readStdin();
    const hookName = input.hook_event_name;
    const response = await routeHookHandler(hookName, input);

    if (response !== null && response !== undefined) {
      console.log(JSON.stringify(response));
    }
  } catch (error) {
    process.stderr.write(`[clickmem-hook] error: ${error.message}\n`);
    console.log(JSON.stringify({ continue: true, permission: "allow" }));
    process.exit(1);
  }
}

main();
