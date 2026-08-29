/** Which findings the worklist filters let through.
 *
 * It is here, away from React, because the rule that matters is easy to get backwards:
 * a filter with nothing selected restricts nothing. Reading an empty set as "match
 * nothing" empties the list, and an empty list reads as a broken view, not as a filter.
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
    (filters.categories.size === 0 || filters.categories.has(finding.category)) &&
    (filters.states.size === 0 || filters.states.has(finding.status)) &&
    (filters.dismissed || finding.triage !== "false") &&
    (wanted === "" || hit(finding, wanted))
  );
}

/** What a click on one pill does to a filter set.
 *
 * Every pill starts on, so a plain toggle means the only thing a first click can do is
 * subtract: clicking "Open" hides every open finding, which is the opposite of what the
 * word says. A click narrows to what was clicked instead, and clicking the one that is
 * already alone widens back out. Holding a modifier keeps the old add-and-remove.
 */
export function clicked(current: Set<string>, name: string, add: boolean): Set<string> {
  const next = new Set(current);
  if (add) {
    if (next.has(name)) next.delete(name);
    else next.add(name);
    return next;
  }
  // Already the only one on: widen back out. An empty set restricts nothing.
  if (next.size === 1 && next.has(name)) return new Set();
  return new Set([name]);
}
