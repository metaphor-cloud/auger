/** Where every branch, twig, and leaf of the map sits.
 *
 * The drawing grows left to right, the way a plant does: one trunk, a branch per
 * repository, a twig per finding, and a leaf at the end of each twig. A branch is a
 * cubic curve rather than a line, and its thickness falls as it divides, which is what
 * makes the picture read as a tree and not as a chart.
 *
 * This file holds no React and no colour. It is pure geometry, so the shape can be
 * tested without a window.
 */

export type Leaf = {
  id: string;
  label: string;
  detail: string;
  severity: string;
  category: string;
  unread: boolean;
  closed: boolean;
  /** Where the twig leaves its branch. */
  from: Point;
  /** Where the leaf sits. */
  at: Point;
  path: string;
  width: number;
};

export type Branch = {
  id: string;
  label: string;
  count: number;
  hidden: number;
  worst: string;
  unread: number;
  expanded: boolean;
  enabled: boolean;
  at: Point;
  path: string;
  width: number;
  leaves: Leaf[];
};

export type Point = { x: number; y: number };

export type Tree = {
  trunk: string;
  root: Point;
  fork: Point;
  branches: Branch[];
  width: number;
  height: number;
};

export type LeafInput = {
  id: string;
  label: string;
  detail: string;
  severity: string;
  category: string;
  unread: boolean;
  closed: boolean;
};

export type BranchInput = {
  id: string;
  label: string;
  enabled: boolean;
  worst: string;
  leaves: LeafInput[];
  hidden: number;
  expanded: boolean;
};

/** Vertical room for one leaf. Everything else follows from this. */
export const LEAF_GAP = 30;
export const BRANCH_GAP = 54;
/** A branch nobody has opened takes less room, so a long list still fits the window. */
export const CLOSED_BAND = 64;
const TRUNK_X = 40;
const FORK_X = 150;
const BRANCH_X = 430;
const LEAF_X = 720;
export const CANVAS_WIDTH = 1180;
const TOP = 60;

/** A stable wobble per id, so the tree looks grown rather than drawn, and never moves. */
function wobble(id: string, spread: number): number {
  let hash = 0;
  for (let index = 0; index < id.length; index += 1) {
    hash = (hash * 31 + id.charCodeAt(index)) | 0;
  }
  return ((Math.abs(hash) % 1000) / 1000 - 0.5) * spread;
}

/** A cubic that leaves its parent flat and arrives flat, so joins never look welded. */
function curve(from: Point, to: Point, lean = 0.55, sag = 0): string {
  const span = to.x - from.x;
  const c1 = { x: from.x + span * lean, y: from.y };
  const c2 = { x: to.x - span * lean, y: to.y + sag };
  return `M ${from.x} ${from.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${to.x} ${to.y}`;
}

export function grow(branches: BranchInput[]): Tree {
  const heights = branches.map((branch) =>
    branch.expanded
      ? Math.max(1, branch.leaves.length) * LEAF_GAP + BRANCH_GAP
      : CLOSED_BAND,
  );
  const height = Math.max(
    240,
    heights.reduce((total, one) => total + one, 0) + TOP,
  );
  const fork = { x: FORK_X, y: height / 2 };
  const root = { x: TRUNK_X, y: height / 2 };

  let cursor = TOP;
  const grown: Branch[] = branches.map((branch, index) => {
    const band = heights[index];
    const centre = cursor + band / 2;
    cursor += band;

    const at = { x: BRANCH_X + wobble(branch.id, 40), y: centre };
    const thickness = Math.min(11, 3.5 + Math.sqrt(branch.leaves.length) * 2.2);

    const leaves: Leaf[] = branch.expanded
      ? branch.leaves.map((leaf, position) => {
          const y =
            centre -
            ((branch.leaves.length - 1) * LEAF_GAP) / 2 +
            position * LEAF_GAP +
            wobble(leaf.id, 6);
          // A twig leaves its branch a little before the tip, not from one point, so
          // the branch keeps its taper instead of ending in a starburst.
          const from = {
            x: at.x - (branch.leaves.length > 1 ? 34 * (1 - position / branch.leaves.length) : 0),
            y: centre + (y - centre) * 0.12,
          };
          const tip = { x: LEAF_X + wobble(leaf.id, 26), y };
          return {
            id: leaf.id,
            label: leaf.label,
            detail: leaf.detail,
            severity: leaf.severity,
            category: leaf.category,
            unread: leaf.unread,
            closed: leaf.closed,
            from,
            at: tip,
            path: curve(from, tip, 0.5, wobble(leaf.id, 10)),
            width: 1.8,
          };
        })
      : [];

    return {
      id: branch.id,
      label: branch.label,
      count: branch.leaves.length,
      hidden: branch.hidden,
      worst: branch.worst,
      unread: branch.leaves.filter((leaf) => leaf.unread).length,
      expanded: branch.expanded,
      enabled: branch.enabled,
      at,
      path: curve(fork, at, 0.6, wobble(branch.id, 18)),
      width: thickness,
      leaves,
    };
  });

  return {
    trunk: `M ${root.x} ${root.y} C ${root.x + 50} ${root.y}, ${fork.x - 50} ${fork.y}, ${fork.x} ${fork.y}`,
    root,
    fork,
    branches: grown,
    width: CANVAS_WIDTH,
    height,
  };
}
