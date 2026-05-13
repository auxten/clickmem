/**
 * Typed REST helpers for the ClickMem FastAPI server.
 *
 * Endpoints match `src/clickmem/server.py` 1:1. Bearer auth is added when an
 * API key is present in `localStorage.CLICKMEM_API_KEY`; the server treats
 * loopback connections as open so dev usually works without one.
 */

export type MemoryKind = "principle" | "decision" | "fact" | "doc" | "free";
export type MemoryPrivacy = "public" | "private" | "confidential";
export type MemoryStatus = "active" | "contracted" | "conflicted";

export interface Memory {
  id: string;
  content: string;
  kind: MemoryKind;
  source: string;
  source_ref: string;
  project_id: string;
  privacy: MemoryPrivacy;
  tags: string[];
  status: MemoryStatus;
  pinned: boolean;
  contract_reason: string;
  revises_id: string;
  conflict_with: string[];
  content_hash: string;
  recall_hits: number;
  created_at: string;
  updated_at: string;
}

export interface MemoryListResponse {
  total: number;
  offset: number;
  limit: number;
  items: Memory[];
}

export interface MemoryMutationResult {
  status: string;
  id: string;
  peer_ids?: string[];
  message?: string;
}

export interface StatsOverview {
  total: number;
  active: number;
  pinned: number;
  conflicted: number;
  contracted: number;
  last7: number;
  prev7: number;
  raw_transcripts: number;
  events_24h: number;
}

export interface ProjectStat {
  project_id: string;
  memories: number;
  pinned: number;
  conflicts: number;
  last_updated: string;
}

export interface KindStat {
  kind: string;
  c: number;
}

export interface PrivacyMixRow {
  project_id: string;
  privacy: string;
  c: number;
}

export interface EventRow {
  id: string;
  kind: string;
  agent: string;
  project_id: string;
  memory_id: string;
  message: string;
  payload?: Record<string, unknown>;
  created_at: string;
}

export interface ConflictRow {
  id: string;
  content: string;
  kind: string;
  project_id: string;
  privacy: string;
  conflict_with: string[];
  updated_at: string;
}

export interface AgentRow {
  name: string;
  label: string;
  experimental: boolean;
  discovered: boolean;
  installed: boolean;
  session_count_24h: number;
  last_event: string;
}

export interface ActivityBucket {
  bucket: string;
  count: number;
}

export interface BlacklistRow {
  id: string;
  pattern: string;
  scope: string;
  reason: string;
  hit_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProjectRow {
  id: string;
  name: string;
  repo_url: string;
  kind: string;
  allowed_cross_refs: string[];
  created_at: string;
  updated_at: string;
}

export interface RawRow {
  id: string;
  session_id: string;
  agent: string;
  project_id: string;
  role: string;
  text: string;
  created_at: string;
  meta?: Record<string, unknown>;
}

export interface RecallHit {
  id: string;
  content: string;
  kind: string;
  project_id: string;
  privacy: string;
  status: string;
  pinned: boolean;
  cosine_sim: number;
  score: number;
  project_boost: number;
  source: string;
  tags: string[];
  updated_at: string;
}

export interface RecallTraceCandidate {
  id: string;
  content_preview: string;
  kind: string;
  project_id: string;
  privacy: string;
  pinned: boolean;
  cosine_sim: number;
  project_boost: number;
  blacklisted: boolean;
  privacy_blocked: boolean;
  score: number;
  kept: boolean;
}

export interface RecallTrace {
  query: string;
  filters: {
    project_id: string;
    include_confidential: boolean;
    cross_project: boolean;
    kind: string | null;
  };
  hits: RecallHit[];
  candidates: RecallTraceCandidate[];
}

export interface MemoryHistoryEntry {
  memory_id: string;
  version: number;
  op: string;
  content: string;
  edited_by: string;
  edited_at: string;
  prev_id: string;
  note: string;
  diff: string[];
}

export interface HealthInfo {
  ok: boolean;
  version: string;
  backend: string;
  embedding_model: string;
  embedding_dim: number;
}

const API_KEY_STORAGE = "CLICKMEM_API_KEY";

export function getApiKey(): string {
  try {
    return window.localStorage.getItem(API_KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setApiKey(value: string): void {
  try {
    if (value) {
      window.localStorage.setItem(API_KEY_STORAGE, value);
    } else {
      window.localStorage.removeItem(API_KEY_STORAGE);
    }
  } catch {
    /* ignore */
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message?: string) {
    super(message || `API error ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

function buildHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(extra || {}),
  };
  const key = getApiKey();
  if (key) headers["Authorization"] = `Bearer ${key}`;
  return headers;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  query?: Record<string, unknown>,
): Promise<T> {
  let url = path;
  if (query && Object.keys(query).length > 0) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      if (Array.isArray(v)) {
        v.forEach((entry) => qs.append(k, String(entry)));
      } else {
        qs.append(k, String(v));
      }
    }
    const s = qs.toString();
    if (s) url += `?${s}`;
  }

  const init: RequestInit = {
    method,
    headers: buildHeaders(
      body !== undefined ? { "Content-Type": "application/json" } : undefined,
    ),
    credentials: "same-origin",
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }

  const res = await fetch(url, init);
  const text = await res.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, parsed, `HTTP ${res.status} ${res.statusText}`);
  }
  return parsed as T;
}

export const api = {
  health: () => request<HealthInfo>("GET", "/v1/health"),

  // ---- Stats ----------------------------------------------------------
  statsOverview: () => request<StatsOverview>("GET", "/v1/stats/overview"),
  statsProjects: () => request<ProjectStat[]>("GET", "/v1/stats/projects"),
  statsKinds: () => request<KindStat[]>("GET", "/v1/stats/kinds"),
  statsPrivacyMix: () => request<PrivacyMixRow[]>("GET", "/v1/stats/privacy_mix"),

  // ---- Events ---------------------------------------------------------
  events: (params: {
    since?: string;
    kind?: string;
    agent?: string;
    limit?: number;
  } = {}) =>
    request<EventRow[]>("GET", "/v1/events", undefined, {
      since: params.since,
      kind: params.kind,
      agent: params.agent,
      limit: params.limit ?? 50,
    }),

  // ---- Memories -------------------------------------------------------
  listMemories: (params: {
    project_id?: string;
    privacy?: string;
    kind?: string;
    status?: string;
    pinned?: boolean;
    source?: string;
    search?: string;
    offset?: number;
    limit?: number;
  } = {}) =>
    request<MemoryListResponse>("GET", "/v1/memories", undefined, params as Record<string, unknown>),

  getMemory: (id: string) => request<Memory>("GET", `/v1/memories/${encodeURIComponent(id)}`),

  createMemory: (body: {
    content: string;
    kind?: MemoryKind;
    source?: string;
    source_ref?: string;
    project_id?: string;
    privacy?: MemoryPrivacy;
    tags?: string[];
    pinned?: boolean;
    revises_id?: string;
    agent?: string;
  }) => request<MemoryMutationResult>("POST", "/v1/memories", body),

  updateMemory: (id: string, body: {
    content?: string;
    kind?: MemoryKind;
    privacy?: MemoryPrivacy;
    project_id?: string;
    tags?: string[];
    pinned?: boolean;
    revises_id?: string;
    agent?: string;
  }) =>
    request<MemoryMutationResult>(
      "PATCH",
      `/v1/memories/${encodeURIComponent(id)}`,
      body,
    ),

  forgetMemory: (id: string, reason: string, agent?: string) =>
    request<MemoryMutationResult>(
      "DELETE",
      `/v1/memories/${encodeURIComponent(id)}`,
      undefined,
      { reason, agent },
    ),

  bulkMemories: (body: {
    ids: string[];
    op: string;
    payload?: Record<string, unknown>;
    agent?: string;
  }) => request<{ op: string; count: number; results: unknown[] }>(
    "POST",
    "/v1/memories/bulk",
    body,
  ),

  memoryHistory: (id: string) =>
    request<MemoryHistoryEntry[]>(
      "GET",
      `/v1/memories/${encodeURIComponent(id)}/history`,
    ),

  memoryNeighbors: (id: string, limit = 8) =>
    request<Array<{
      id: string;
      content: string;
      kind: string;
      project_id: string;
      privacy: string;
      status: string;
      pinned: boolean;
      cosine_sim?: number;
    }>>(
      "GET",
      `/v1/memories/${encodeURIComponent(id)}/neighbors`,
      undefined,
      { limit },
    ),

  // ---- Conflicts ------------------------------------------------------
  listConflicts: (project_id?: string, limit = 200) =>
    request<ConflictRow[]>("GET", "/v1/conflicts", undefined, {
      project_id,
      limit,
    }),

  resolveConflict: (id: string, op: string, peer_id?: string) =>
    request<{ status: string; op: string; id: string; peer_id?: string }>(
      "POST",
      `/v1/conflicts/${encodeURIComponent(id)}/resolve`,
      { op, peer_id: peer_id || "" },
    ),

  // ---- Recall ---------------------------------------------------------
  recall: (body: {
    query: string;
    project_id?: string;
    limit?: number;
    include_confidential?: boolean;
    cross_project?: boolean;
    kind?: string | null;
  }) =>
    request<{ hits: RecallHit[] }>("POST", "/v1/recall", body),

  recallTrace: (body: {
    query: string;
    project_id?: string;
    limit?: number;
    include_confidential?: boolean;
    cross_project?: boolean;
    kind?: string | null;
  }) => request<RecallTrace>("POST", "/v1/recall/trace", body),

  // ---- Raw ------------------------------------------------------------
  getRaw: (params: { session_id?: string; agent?: string; last?: number }) =>
    request<RawRow[]>("GET", "/v1/get-raw", undefined, params as Record<string, unknown>),

  // ---- Agents ---------------------------------------------------------
  listAgents: () => request<AgentRow[]>("GET", "/v1/agents"),

  agentActivity: (name: string, hours = 24) =>
    request<ActivityBucket[]>(
      "GET",
      `/v1/agents/${encodeURIComponent(name)}/activity`,
      undefined,
      { hours },
    ),

  installAgent: (name: string) =>
    request<{ name: string; installed: boolean; message: string }>(
      "POST",
      `/v1/agents/${encodeURIComponent(name)}/install`,
    ),

  uninstallAgent: (name: string) =>
    request<{ name: string; installed: boolean; message: string }>(
      "POST",
      `/v1/agents/${encodeURIComponent(name)}/uninstall`,
    ),

  testAgent: (name: string) =>
    request<{ name: string; ok: boolean; message: string }>(
      "POST",
      `/v1/agents/${encodeURIComponent(name)}/test`,
    ),

  // ---- Blacklist ------------------------------------------------------
  listBlacklist: () => request<BlacklistRow[]>("GET", "/v1/blacklist"),
  addBlacklist: (body: { pattern: string; scope?: string; reason?: string }) =>
    request<BlacklistRow>("POST", "/v1/blacklist", body),
  removeBlacklist: (id: string) =>
    request<{ ok: boolean; id: string }>("DELETE", `/v1/blacklist/${encodeURIComponent(id)}`),

  // ---- Projects -------------------------------------------------------
  listProjects: () => request<ProjectRow[]>("GET", "/v1/projects"),
  linkProjects: (body: { a: string; b: string; reason?: string }) =>
    request<{ a: ProjectRow; b: ProjectRow }>("POST", "/v1/projects/link", body),
};

export type Api = typeof api;
