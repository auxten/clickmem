/**
 * Utility functions for ClickMem Cursor hooks.
 */

export async function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => { data += chunk; });
    process.stdin.on("end", () => {
      try {
        resolve(JSON.parse(data));
      } catch (e) {
        reject(new Error(`Failed to parse stdin JSON: ${e.message}`));
      }
    });
    process.stdin.on("error", reject);
  });
}

export function truncate(text, maxLen = 2000) {
  if (!text || text.length <= maxLen) return text || "";
  return text.slice(0, maxLen) + "…";
}

export function getSessionId(workspaceRoots) {
  if (!workspaceRoots || workspaceRoots.length === 0) return "cursor-default";
  const root = workspaceRoots[0];
  return root.split("/").pop() || root;
}
