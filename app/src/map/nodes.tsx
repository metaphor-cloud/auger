/** The two kinds of node the map draws. */

import { Handle, Position, type NodeProps } from "@xyflow/react";

import { categoryOf, severityOf } from "./palette";

export type RepoNodeData = {
  name: string;
  path: string;
  open: number;
  worst: string;
  expanded: boolean;
  enabled: boolean;
  unread: number;
};

export type FindingNodeData = {
  title: string;
  severity: string;
  category: string;
  status: string;
  file: string;
  unread: boolean;
  notes: number;
};

export function RepoNode({ data, selected }: NodeProps) {
  const one = data as unknown as RepoNodeData;
  const severity = severityOf(one.worst);
  return (
    <div
      className="group relative flex h-[76px] w-[260px] flex-col justify-center rounded-xl border px-4 transition-all"
      style={{
        borderColor: selected ? severity.colour : "var(--color-border)",
        background:
          "linear-gradient(140deg, var(--color-bg-elevated) 0%, var(--color-bg-card) 100%)",
        boxShadow: selected
          ? `0 0 0 1px ${severity.colour}, 0 12px 34px -12px rgba(${severity.glow}, 0.55)`
          : "0 8px 24px -18px rgba(0, 0, 0, 0.9)",
        opacity: one.enabled ? 1 : 0.55,
      }}
    >
      <div className="flex items-baseline gap-2">
        <span className="truncate text-sm font-medium text-text-primary">{one.name}</span>
        {one.unread > 0 && (
          <span className="rr-twinkle h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />
        )}
      </div>
      <div className="mt-1 flex items-center gap-2 text-[11px] text-text-tertiary">
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: one.open ? severity.colour : "var(--color-text-tertiary)" }}
        />
        <span>{one.open === 0 ? "nothing open" : `${one.open} open`}</span>
        <span className="ml-auto font-mono">{one.expanded ? "−" : "+"}</span>
      </div>
      <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !border-0 !bg-border" />
    </div>
  );
}

export function FindingNode({ data, selected }: NodeProps) {
  const one = data as unknown as FindingNodeData;
  const severity = severityOf(one.severity);
  const category = categoryOf(one.category);
  const closed = one.status === "resolved" || one.status === "suppressed";
  return (
    <div
      className="relative flex h-[64px] w-[320px] items-center gap-3 rounded-lg border px-3 transition-all"
      style={{
        borderColor: selected ? severity.colour : "var(--color-border-subtle)",
        background: "var(--color-bg-card)",
        boxShadow: selected
          ? `0 0 0 1px ${severity.colour}, 0 10px 30px -14px rgba(${severity.glow}, 0.6)`
          : "none",
        opacity: closed ? 0.5 : 1,
      }}
    >
      <span
        className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full"
        style={{ background: severity.colour }}
      />
      <span
        className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[9px] tracking-wider"
        style={{ background: `${category.colour}22`, color: category.colour }}
        title={category.label}
      >
        {category.tag}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-xs text-text-primary">{one.title}</span>
          {one.unread && <span className="rr-twinkle h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />}
        </div>
        <div className="truncate font-mono text-[10px] text-text-tertiary">
          {one.file || "no file"}
          {one.status === "doing" ? " · doing" : ""}
          {one.notes > 0 ? ` · ${one.notes} notes` : ""}
        </div>
      </div>
      <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !border-0 !bg-border" />
    </div>
  );
}

export const NODE_TYPES = { repo: RepoNode, finding: FindingNode };
