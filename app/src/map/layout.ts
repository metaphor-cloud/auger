/** Where every node sits.
 *
 * dagre lays the tree out from left to right. A title is wider than it is tall, so a
 * tree that grows sideways fits the window that a person actually has.
 */

import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";

export const REPO_SIZE = { width: 260, height: 76 };
export const FINDING_SIZE = { width: 320, height: 64 };

export function layout(nodes: Node[], edges: Edge[]): Node[] {
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "LR", nodesep: 14, ranksep: 90, marginx: 24, marginy: 24 });

  for (const node of nodes) {
    const size = node.type === "repo" ? REPO_SIZE : FINDING_SIZE;
    graph.setNode(node.id, size);
  }
  for (const edge of edges) graph.setEdge(edge.source, edge.target);
  dagre.layout(graph);

  return nodes.map((node) => {
    const placed = graph.node(node.id);
    const size = node.type === "repo" ? REPO_SIZE : FINDING_SIZE;
    return {
      ...node,
      position: { x: placed.x - size.width / 2, y: placed.y - size.height / 2 },
    };
  });
}
