import { MemoryKind, MemoryStatus } from "../api";

export const MEMORY_KIND_OPTIONS: MemoryKind[] = [
  "principle",
  "decision",
  "fact",
  "doc",
  "free",
];

export const MEMORY_STATUS_OPTIONS: MemoryStatus[] = [
  "active",
  "conflicted",
  "contracted",
];

const KIND_LABELS: Record<MemoryKind, string> = {
  principle: "Rule",
  decision: "Decision",
  fact: "Fact",
  doc: "Document",
  free: "Note",
};

const STATUS_LABELS: Record<MemoryStatus, string> = {
  active: "Active",
  conflicted: "Needs review",
  contracted: "Archived",
};

export function memoryKindLabel(kind: string): string {
  return KIND_LABELS[kind as MemoryKind] ?? kind;
}

export function memoryStatusLabel(status: string): string {
  return STATUS_LABELS[status as MemoryStatus] ?? status;
}
