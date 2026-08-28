import { describe, expect, it } from "vitest";

import { BRANCH_GAP, LEAF_GAP, grow, type BranchInput } from "./tree";

function leaf(id: string, severity = "medium") {
  return {
    id,
    label: id,
    detail: "",
    severity,
    category: "quality",
    unread: false,
    closed: false,
  };
}

function branch(id: string, leaves: number, expanded = true): BranchInput {
  return {
    id,
    label: id,
    enabled: true,
    worst: "high",
    hidden: 0,
    expanded,
    leaves: Array.from({ length: leaves }, (_, index) => leaf(`${id}-${index}`)),
  };
}

describe("the tree", () => {
  it("gives every branch its own band, so two never draw over each other", () => {
    const tree = grow([branch("a", 4), branch("b", 3), branch("c", 1)]);
    const ys = tree.branches.map((one) => one.at.y);
    const sorted = [...ys].sort((first, second) => first - second);
    expect(ys).toEqual(sorted);
    for (let index = 1; index < ys.length; index += 1) {
      expect(ys[index] - ys[index - 1]).toBeGreaterThan(BRANCH_GAP / 2);
    }
  });

  it("keeps a branch's leaves inside its own band", () => {
    const tree = grow([branch("a", 6), branch("b", 6)]);
    const [first, second] = tree.branches;
    const lowest = Math.max(...first.leaves.map((one) => one.at.y));
    const highest = Math.min(...second.leaves.map((one) => one.at.y));
    expect(lowest).toBeLessThan(highest);
  });

  it("spreads leaves evenly, whatever the wobble does", () => {
    const [one] = grow([branch("a", 5)]).branches;
    const gaps = one.leaves
      .slice(1)
      .map((leafAt, index) => leafAt.at.y - one.leaves[index].at.y);
    for (const gap of gaps) {
      expect(gap).toBeGreaterThan(LEAF_GAP * 0.7);
      expect(gap).toBeLessThan(LEAF_GAP * 1.3);
    }
  });

  it("grows taller as it holds more", () => {
    const small = grow([branch("a", 2)]);
    const large = grow([branch("a", 20)]);
    expect(large.height).toBeGreaterThan(small.height);
  });

  it("draws a collapsed branch as one twig with no leaves", () => {
    const [one] = grow([branch("a", 9, false)]).branches;
    expect(one.leaves).toEqual([]);
    expect(one.count).toBe(9);
  });

  it("is the same tree every time, so it never moves under the pointer", () => {
    const input = [branch("a", 4), branch("b", 2)];
    expect(grow(input)).toEqual(grow(input));
  });

  it("starts every branch at the fork and ends it at the branch point", () => {
    const tree = grow([branch("a", 1), branch("b", 1)]);
    for (const one of tree.branches) {
      expect(one.path.startsWith(`M ${tree.fork.x} ${tree.fork.y}`)).toBe(true);
      expect(one.path.endsWith(`${one.at.x} ${one.at.y}`)).toBe(true);
    }
  });

  it("has room for a tree with nothing in it", () => {
    const tree = grow([]);
    expect(tree.height).toBeGreaterThan(0);
    expect(tree.branches).toEqual([]);
  });
});
