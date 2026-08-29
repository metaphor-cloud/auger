/** The building blocks every settings page is made of.
 *
 * One row is a label, a sentence saying what it changes, and the control. Search runs
 * through here rather than through a registry: a row that does not match the query
 * renders nothing, and a group whose rows all vanish hides itself.
 */

import { Card, CardContent, CardHeader, CardTitle } from "@metaphor-cloud/ui";
import { createContext, useContext, type ReactNode } from "react";

import { visible } from "./search";

const Query = createContext("");

export function SearchProvider({ query, children }: { query: string; children: ReactNode }) {
  return <Query.Provider value={query.trim().toLowerCase()}>{children}</Query.Provider>;
}

export function Group({
  title,
  description,
  action,
  keywords,
  children,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  /** Extra words the group answers to, for search terms that are not on screen. */
  keywords?: string;
  children: ReactNode;
}) {
  const query = useContext(Query);
  const matched = visible(query, title, keywords, typeof description === "string" ? description : "");
  // A group whose own title matches shows everything inside it, so a search for the
  // group name is a way to jump to it rather than a way to empty it.
  return (
    <SearchProvider query={matched ? "" : query}>
      <Card className="mb-4" data-group={title}>
        <CardHeader className="flex flex-row items-start gap-3 space-y-0">
          <div className="flex-1">
            <CardTitle className="text-sm">{title}</CardTitle>
            {description && <p className="mt-1 text-xs text-text-secondary">{description}</p>}
          </div>
          {action}
        </CardHeader>
        <CardContent className="divide-y divide-border-subtle pt-0">{children}</CardContent>
      </Card>
    </SearchProvider>
  );
}

export function Row({
  label,
  help,
  keywords,
  children,
}: {
  label: string;
  /** One sentence on what changes. Say the consequence, not the mechanism. */
  help?: ReactNode;
  keywords?: string;
  children: ReactNode;
}) {
  const query = useContext(Query);
  const text = typeof help === "string" ? help : undefined;
  if (!visible(query, label, text, keywords)) return null;
  return (
    <div className="flex items-start justify-between gap-6 py-3 first:pt-0 last:pb-0">
      <div className="min-w-0 flex-1">
        <p className="text-xs text-text-primary">{label}</p>
        {help && <p className="mt-0.5 text-[11px] leading-snug text-text-secondary">{help}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}

/** A block that is not one setting: a table, a list, an editor. */
export function Block({
  label,
  help,
  keywords,
  children,
}: {
  label?: string;
  help?: ReactNode;
  keywords?: string;
  children: ReactNode;
}) {
  const query = useContext(Query);
  const text = typeof help === "string" ? help : undefined;
  if (!visible(query, label, text, keywords)) return null;
  return (
    <div className="py-3 first:pt-0 last:pb-0">
      {label && <p className="text-xs text-text-primary">{label}</p>}
      {help && <p className="mt-0.5 mb-2 text-[11px] leading-snug text-text-secondary">{help}</p>}
      {children}
    </div>
  );
}

/** The vertical nav down the side of the settings pages. */
export function SectionNav<T extends string>({
  sections,
  active,
  onPick,
}: {
  sections: readonly { id: T; label: string }[];
  active: T;
  onPick: (id: T) => void;
}) {
  return (
    <nav className="flex w-40 shrink-0 flex-col gap-0.5">
      {sections.map((one) => (
        <button
          key={one.id}
          onClick={() => onPick(one.id)}
          className={`rounded-md px-2.5 py-1.5 text-left text-xs transition-colors ${
            one.id === active
              ? "bg-bg-selected text-text-primary"
              : "text-text-secondary hover:bg-bg-hover hover:text-text-primary"
          }`}
        >
          {one.label}
        </button>
      ))}
    </nav>
  );
}
