/** Small pieces every view uses, so the views stay about their own subject. */

import { Badge, Card, CardContent, CardHeader, CardTitle } from "@metaphor-cloud/ui";
import type { ReactNode } from "react";

type Variant = "default" | "success" | "warning" | "danger" | "outline";

const SEVERITY_VARIANT: Record<string, Variant> = {
  critical: "danger",
  high: "warning",
  medium: "default",
  low: "outline",
  info: "outline",
};

const STATUS_VARIANT: Record<string, Variant> = {
  ok: "success",
  failed: "danger",
  skipped: "outline",
  running: "default",
};

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge variant={SEVERITY_VARIANT[severity] ?? "outline"}>{severity}</Badge>;
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge variant={STATUS_VARIANT[status] ?? "outline"}>{status}</Badge>;
}

export function Section({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="mb-4">
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="flex-1">
          <CardTitle className="text-sm">{title}</CardTitle>
          {description && (
            <p className="mt-1 text-xs text-text-secondary">{description}</p>
          )}
        </div>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export function Facts({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-x-4 gap-y-2 text-xs">{children}</dl>;
}

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-text-secondary">{label}</dt>
      <dd className="text-text-primary">{children}</dd>
    </>
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="font-mono text-[11px] text-text-secondary" title={title}>
      {children}
    </span>
  );
}

export function PageTitle({
  title,
  description,
  children,
}: {
  title: string;
  description?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <header className="mb-4 flex items-start gap-3">
      <div className="flex-1">
        <h2 className="text-base font-semibold tracking-tight">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-text-secondary">{description}</p>}
      </div>
      {children}
    </header>
  );
}
