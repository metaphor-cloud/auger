/** Which findings the worklist filters let through.
 *
 * It is here, away from React, because the rule is the whole behaviour of the pills:
 * a pill that is on shows its kind, a pill that is off hides it, and one click changes
 * one pill. Nothing a click does to a pill may change another pill.
 */

import type { Finding } from "../types";

export type Filters = {
  categories: Set<string>;
  states: Set<string>;
  /** Keep the ones a model judged false. Off by default. */
  dismissed: boolean;
  search: string;
};

function hit(finding: Finding, wanted: string) {
  return (
    finding.title.toLowerCase().includes(wanted) ||
    finding.detail.toLowerCase().includes(wanted) ||
    finding.file.toLowerCase().includes(wanted)
  );
}

export function matches(finding: Finding, filters: Filters): boolean {
  const wanted = filters.search.trim().toLowerCase();
  return (
    filters.categories.has(finding.category) &&
    filters.states.has(finding.status) &&
    (filters.dismissed || finding.triage !== "false") &&
    (wanted === "" || hit(finding, wanted))
  );
}

/** What a click on one pill does to a filter set: it turns that one pill over.
 *
 * Turning every pill off shows nothing, which is what the pills then say. The list
 * says so in words rather than reading the empty set as "no filter at all", because a
 * pill that is off must never show its kind.
 */
export function clicked(current: Set<string>, name: string): Set<string> {
  const next = new Set(current);
  if (next.has(name)) next.delete(name);
  else next.add(name);
  return next;
}
