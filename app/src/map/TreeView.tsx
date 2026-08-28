/** The map, drawn.
 *
 * One SVG. The view box comes from the size of what was grown, so the picture can never
 * open on an empty canvas, which is what the graph library did.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { categoryOf, severityOf } from "./palette";
import { CANVAS_WIDTH, type Branch, type Leaf, type Tree } from "./tree";

type View = { x: number; y: number; w: number; h: number };

const TRUNK_COLOUR = "#4c9df0";

/** How far out the first view may go. Past this the labels stop being words. */
const MIN_SCALE = 1.4;

function fit(tree: Tree, box: { width: number; height: number }): View {
  const margin = 40;
  const width = tree.width + margin * 2;
  const height = tree.height + margin * 2;
  const ratio = box.width > 0 && box.height > 0 ? box.width / box.height : width / height;
  // Grow the shorter side so nothing is cut off, whatever shape the window is.
  const whole = Math.max(width, height * ratio);
  // A hundred repositories would otherwise open zoomed so far out that every label is
  // a smudge. Stop at a readable scale and start at the top, where the worst grows.
  const w = Math.min(whole, width * MIN_SCALE);
  const h = w / ratio;
  const tall = h < height;
  return {
    x: -margin - (w - width) / 2,
    y: tall ? -margin : -margin - (h - height) / 2,
    w,
    h,
  };
}

function LeafMark({
  leaf,
  branch,
  chosen,
  onPick,
}: {
  leaf: Leaf;
  branch: Branch;
  chosen: boolean;
  onPick: (id: string) => void;
}) {
  const severity = severityOf(leaf.severity);
  const category = categoryOf(leaf.category);
  const from = severityOf(branch.worst).colour;
  return (
    <g
      className="cursor-pointer"
      opacity={leaf.closed ? 0.4 : 1}
      onClick={(event) => {
        event.stopPropagation();
        onPick(leaf.id);
      }}
    >
      <linearGradient
        id={`twig-${leaf.id}`}
        gradientUnits="userSpaceOnUse"
        x1={leaf.from.x}
        y1={leaf.from.y}
        x2={leaf.at.x}
        y2={leaf.at.y}
      >
        <stop offset="0%" stopColor={from} stopOpacity={0.55} />
        <stop offset="100%" stopColor={severity.colour} stopOpacity={0.95} />
      </linearGradient>
      <path
        d={leaf.path}
        fill="none"
        stroke={`url(#twig-${leaf.id})`}
        strokeWidth={chosen ? leaf.width + 1.4 : leaf.width}
        strokeLinecap="round"
      />
      {/* The leaf itself: a filled blob with a soft halo, brighter when it is new. */}
      <circle
        cx={leaf.at.x}
        cy={leaf.at.y}
        r={chosen ? 12 : 9}
        fill={severity.colour}
        opacity={leaf.unread ? 0.22 : 0.1}
        className={leaf.unread ? "rr-breathe" : undefined}
      />
      <circle
        cx={leaf.at.x}
        cy={leaf.at.y}
        r={chosen ? 6 : 4.5}
        fill={severity.colour}
        stroke={chosen ? "var(--color-text-primary)" : "none"}
        strokeWidth={1}
      />
      <text
        x={leaf.at.x + 14}
        y={leaf.at.y - 2}
        fontSize={11}
        fill="var(--color-text-primary)"
        className="select-none"
      >
        {leaf.label.length > 46 ? `${leaf.label.slice(0, 45)}…` : leaf.label}
      </text>
      <text
        x={leaf.at.x + 14}
        y={leaf.at.y + 11}
        fontSize={9}
        fill={category.colour}
        opacity={0.85}
        className="select-none font-mono"
      >
        {category.tag}
        {leaf.detail ? ` · ${leaf.detail}` : ""}
      </text>
    </g>
  );
}

function BranchMark({
  branch,
  chosen,
  onToggle,
  onPick,
}: {
  branch: Branch;
  chosen: string | null;
  onToggle: (id: string) => void;
  onPick: (id: string) => void;
}) {
  const worst = severityOf(branch.worst);
  const note = `${branch.count === 0 ? "clear" : `${branch.count} open`}${
    branch.hidden > 0 ? ` · ${branch.hidden} not drawn` : ""
  }`;
  return (
    <g opacity={branch.enabled ? 1 : 0.45}>
      <linearGradient
        id={`branch-${branch.id.replace(/\W/g, "")}`}
        gradientUnits="userSpaceOnUse"
        x1={0}
        y1={0}
        x2={branch.at.x}
        y2={branch.at.y}
      >
        <stop offset="0%" stopColor={TRUNK_COLOUR} stopOpacity={0.8} />
        <stop offset="100%" stopColor={branch.count ? worst.colour : "#3f5468"} stopOpacity={0.9} />
      </linearGradient>
      <path
        d={branch.path}
        fill="none"
        stroke={`url(#branch-${branch.id.replace(/\W/g, "")})`}
        strokeWidth={branch.width}
        strokeLinecap="round"
      />
      {branch.leaves.map((leaf) => (
        <LeafMark
          key={leaf.id}
          leaf={leaf}
          branch={branch}
          chosen={chosen === leaf.id}
          onPick={onPick}
        />
      ))}

      <g
        className="cursor-pointer"
        onClick={(event) => {
          event.stopPropagation();
          onToggle(branch.id);
        }}
      >
        <circle
          cx={branch.at.x}
          cy={branch.at.y}
          r={7}
          fill="var(--color-bg-elevated)"
          stroke={branch.count ? worst.colour : "var(--color-border)"}
          strokeWidth={1.5}
        />
        <text
          x={branch.at.x}
          y={branch.at.y + 3.5}
          fontSize={10}
          textAnchor="middle"
          fill="var(--color-text-secondary)"
          className="select-none"
        >
          {branch.expanded ? "−" : "+"}
        </text>
        {/* Above the branch, not across it. A panel behind the words would cut the
            branch in half, and the branch is the picture. */}
        <text
          x={branch.at.x - 12}
          y={branch.at.y - 26}
          fontSize={12}
          textAnchor="end"
          fill="var(--color-text-primary)"
          className="select-none"
        >
          {branch.label}
        </text>
        <text
          x={branch.at.x - 12}
          y={branch.at.y - 13}
          fontSize={10}
          textAnchor="end"
          fill="var(--color-text-tertiary)"
          className="select-none"
        >
          {note}
        </text>
        {branch.unread > 0 && (
          <circle
            cx={branch.at.x - 4}
            cy={branch.at.y - 30}
            r={3}
            fill={TRUNK_COLOUR}
            className="rr-breathe"
          />
        )}
      </g>
    </g>
  );
}

export default function TreeView({
  tree,
  chosen,
  onToggle,
  onPick,
}: {
  tree: Tree;
  chosen: string | null;
  onToggle: (id: string) => void;
  onPick: (id: string) => void;
}) {
  const holder = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [view, setView] = useState<View | null>(null);
  const [drag, setDrag] = useState<{ x: number; y: number; view: View } | null>(null);

  useEffect(() => {
    const element = holder.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setBox({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const home = useMemo(() => fit(tree, box), [tree, box]);
  const shape = `${tree.branches.length}:${tree.height}`;
  const previous = useRef(shape);

  useEffect(() => {
    // Frame the tree on the first draw, and again when it changes shape. A pan the
    // user made is kept while they are only reading.
    if (view === null || previous.current !== shape) {
      previous.current = shape;
      setView(home);
    }
  }, [home, shape, view]);

  const current = view ?? home;

  function zoom(factor: number, at?: { x: number; y: number }) {
    setView((now) => {
      const from = now ?? home;
      const w = Math.min(CANVAS_WIDTH * 6, Math.max(240, from.w * factor));
      const h = (w / from.w) * from.h;
      const anchor = at ?? { x: 0.5, y: 0.5 };
      return {
        x: from.x + (from.w - w) * anchor.x,
        y: from.y + (from.h - h) * anchor.y,
        w,
        h,
      };
    });
  }

  return (
    <div ref={holder} className="relative h-full w-full overflow-hidden">
      <svg
        width="100%"
        height="100%"
        viewBox={`${current.x} ${current.y} ${current.w} ${current.h}`}
        className={drag ? "cursor-grabbing" : "cursor-grab"}
        onWheel={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect();
          zoom(event.deltaY > 0 ? 1.12 : 0.89, {
            x: (event.clientX - bounds.left) / bounds.width,
            y: (event.clientY - bounds.top) / bounds.height,
          });
        }}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          setDrag({ x: event.clientX, y: event.clientY, view: current });
        }}
        onPointerMove={(event) => {
          if (!drag) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          const scale = current.w / bounds.width;
          setView({
            ...drag.view,
            x: drag.view.x - (event.clientX - drag.x) * scale,
            y: drag.view.y - (event.clientY - drag.y) * scale,
          });
        }}
        onPointerUp={() => setDrag(null)}
        onPointerCancel={() => setDrag(null)}
      >
        <defs>
          <filter id="rr-soft" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="7" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <path
          d={tree.trunk}
          fill="none"
          stroke={TRUNK_COLOUR}
          strokeOpacity={0.75}
          strokeWidth={13}
          strokeLinecap="round"
        />
        <circle cx={tree.root.x} cy={tree.root.y} r={9} fill={TRUNK_COLOUR} opacity={0.9} />

        {tree.branches.map((branch) => (
          <BranchMark
            key={branch.id}
            branch={branch}
            chosen={chosen}
            onToggle={onToggle}
            onPick={onPick}
          />
        ))}
      </svg>

      <div className="absolute bottom-3 right-3 flex flex-col gap-1">
        {[
          { label: "+", act: () => zoom(0.82) },
          { label: "−", act: () => zoom(1.22) },
          { label: "⤢", act: () => setView(home) },
        ].map((one) => (
          <button
            key={one.label}
            onClick={one.act}
            className="h-7 w-7 rounded-md border border-border-subtle bg-bg-card text-xs text-text-secondary transition-colors hover:text-text-primary"
          >
            {one.label}
          </button>
        ))}
      </div>
    </div>
  );
}
