/**
 * Mermaid -> graph parser for the pHantasma 3D memory explorer.
 *
 * Pure, dependency-free and DOM-free so it can be unit-tested under node
 * (see mermaid_graph.test.mjs). It handles the subset of Mermaid that the
 * assistant actually stores in `memories.mermaid`:
 *
 *   graph TD;                                  graph/flowchart header
 *   A[label] -->|edge label| B[label]          dominant stored form
 *   A[X]-->B[Y]                                no spaces
 *   "quoted node" --> "other"                   quoted ids, no brackets
 *   a [label="long text"]                      label= attribute, no edge
 *   A((x)) A{x} A[[x]] A[(x)]                  other node shapes
 *   -.->  ---  ==>  A -- text --> B             edge variants
 *   %% comment                                 ignored
 *   subgraph/end/classDef/class/style/click    ignored (not node data)
 *
 * Nothing here invents nodes: every returned node comes from a token in the
 * source text, and every edge connects two parsed node ids.
 */

const PAIRS = { '[': ']', '(': ')', '{': '}' };
const CLOSERS = new Set([']', ')', '}']);

/** Lines that carry no node/edge information for our purposes. */
const IGNORED_DIRECTIVES = [
  'subgraph',
  'end',
  'classdef',
  'class',
  'style',
  'click',
  'linkstyle',
  'direction',
  'callback',
  'href',
];

function stripQuotes(value) {
  const v = String(value == null ? '' : value).trim();
  if (v.length >= 2) {
    const first = v[0];
    const last = v[v.length - 1];
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return v.slice(1, -1).trim();
    }
  }
  return v;
}

/** Remove one balanced bracket wrapper, e.g. `[sub]` -> `sub`. */
function stripBalancedWrapper(value) {
  let out = String(value == null ? '' : value).trim();
  if (out.length < 2) return out;
  const first = out[0];
  const last = out[out.length - 1];
  if (PAIRS[first] && PAIRS[first] === last) {
    const inner = out.slice(1, -1).trim();
    if (inner) return inner;
  }
  return out;
}

/** `A -- text --> B` becomes `A -->|text| B` so one edge rule covers both. */
function normaliseInlineEdgeLabel(line) {
  return line.replace(
    /(-{2,}|={2,}|-\.-+)\s*([^->|=]+?)\s*(-+>|={2,}>|-\.-+>)/g,
    (_m, open, label, close) => `${open}${close}|${label.trim()}|`
  );
}

const EDGE_RE = /(-{2,}>|-\.-+>|={2,}>|-{3,}|={3,}|-\.-)/g;

/**
 * Split one statement into node specs separated by edges.
 * Returns [{ spec, edgeLabel, arrow }] where edgeLabel belongs to the edge
 * that FOLLOWS that spec.
 */
function splitOnEdges(statement) {
  const parts = [];
  const edges = [];
  let last = 0;
  let match;
  EDGE_RE.lastIndex = 0;
  while ((match = EDGE_RE.exec(statement)) !== null) {
    const before = statement.slice(last, match.index);
    // A pipe label may sit before the arrow (A -->|lbl| B is the common form,
    // but `A -- lbl --> B` was already normalised, so only handle `|lbl|-->`
    // and `-->|lbl|`).
    let spec = before;
    let pendingLabel = null;
    const trailingPipe = spec.match(/\|\s*([^|]*)\|\s*$/);
    if (trailingPipe) {
      pendingLabel = trailingPipe[1];
      spec = spec.slice(0, trailingPipe.index);
    }
    parts.push({ spec, pendingLabel });
    edges.push({ arrow: match[0] });
    last = match.index + match[0].length;
  }
  parts.push({ spec: statement.slice(last), pendingLabel: null });

  // Attach a label that follows the arrow: `-->` then `|lbl|`
  for (let i = 0; i < edges.length; i += 1) {
    const after = parts[i + 1];
    if (after && after.pendingLabel !== null) {
      edges[i].label = after.pendingLabel;
      after.pendingLabel = null;
    } else if (after) {
      const lead = after.spec.match(/^\s*\|\s*([^|]*)\|\s*/);
      if (lead) {
        edges[i].label = lead[1];
        after.spec = after.spec.slice(lead[0].length);
      }
    }
  }
  return { parts, edges };
}

/** Extract `{ id, label }` from a node spec such as `A[Text]`. */
function parseNodeSpec(rawSpec) {
  const spec = String(rawSpec || '').trim();
  if (!spec) return null;

  let openIdx = -1;
  for (let i = 0; i < spec.length; i += 1) {
    if (PAIRS[spec[i]]) {
      openIdx = i;
      break;
    }
  }

  if (openIdx === -1) {
    const bare = stripQuotes(spec);
    if (!bare) return null;
    return { id: bare, label: bare };
  }

  const id = stripQuotes(spec.slice(0, openIdx));
  const stack = [];
  let closeIdx = -1;
  let innerStart = -1;
  for (let i = openIdx; i < spec.length; i += 1) {
    const c = spec[i];
    if (PAIRS[c]) {
      if (stack.length === 0) innerStart = i + 1;
      stack.push(PAIRS[c]);
    } else if (CLOSERS.has(c)) {
      const want = stack.pop();
      if (want === c && stack.length === 0) {
        closeIdx = i;
        break;
      }
    }
  }

  let label = closeIdx === -1 ? spec.slice(innerStart) : spec.slice(innerStart, closeIdx);
  label = label.replace(/^\s*label\s*=\s*/i, '');
  label = stripQuotes(label);
  // Double shapes carry their own wrapper inside the first bracket pair:
  // `A[[sub]]`, `B((dbl))`, `D[(stadium)]` -> strip the inner wrapper.
  label = stripBalancedWrapper(label);
  if (!label) label = id;
  return { id: id || label, label };
}

function isIgnored(line) {
  const lower = line.toLowerCase();
  return IGNORED_DIRECTIVES.some((d) => lower === d || lower.startsWith(`${d} `));
}

/**
 * Parse a Mermaid diagram into `{ nodes, links, header }`.
 *
 * @param {string} text raw Mermaid source
 * @returns {{nodes: Array<{id:string,label:string}>, links: Array<{source:string,target:string,label:string|null}>, header: string|null, warnings: string[]}}
 */
export function parseMermaid(text) {
  const warnings = [];
  const nodeMap = new Map();
  const links = [];
  let header = null;

  const addNode = (id, label) => {
    if (!nodeMap.has(id)) {
      nodeMap.set(id, { id, label: label || id });
      return nodeMap.get(id);
    }
    const existing = nodeMap.get(id);
    // A later, richer label (a real text label) wins over a bare id echo.
    if (existing.label === existing.id && label && label !== id) {
      existing.label = label;
    }
    return existing;
  };

  const source = String(text || '');
  const lines = source.split(/\r?\n/);

  for (const rawLine of lines) {
    const withoutComment = rawLine.split('%%')[0];
    let line = withoutComment.trim();
    if (!line) continue;

    if (/^(graph|flowchart)\b/i.test(line)) {
      if (!header) header = line.replace(/;+\s*$/, '');
      // The header may share the line with a statement:
      //   `graph TD; A[X] -->|lbl| B[Y]`
      const rest = line
        .replace(/^(graph|flowchart)\b[^;]*/i, '')
        .replace(/^;+/, '')
        .trim();
      if (!rest) continue;
      line = rest;
    }
    if (isIgnored(line)) continue;

    // strip statement separators at the edges of the line
    line = line.replace(/^;+|;+$/g, '').trim();
    if (!line) continue;

    // A statement may hold several edges: A --> B --> C
    const normalised = normaliseInlineEdgeLabel(line);
    const { parts, edges } = splitOnEdges(normalised);

    if (edges.length === 0) {
      const node = parseNodeSpec(parts[0].spec);
      if (node) addNode(node.id, node.label);
      else warnings.push(`could not parse node: ${line}`);
      continue;
    }

    const resolved = parts.map((p) => {
      const specText = p.spec != null ? p.spec : '';
      return parseNodeSpec(specText);
    });

    for (let i = 0; i < edges.length; i += 1) {
      const from = resolved[i];
      const to = resolved[i + 1];
      if (!from || !to) {
        warnings.push(`incomplete edge near: ${line}`);
        continue;
      }
      addNode(from.id, from.label);
      addNode(to.id, to.label);
      const label = edges[i].label != null ? String(edges[i].label).trim() : null;
      const key = `${from.id}\u0000${to.id}\u0000${label || ''}`;
      if (!links.some((l) => l.key === key)) {
        links.push({ key, source: from.id, target: to.id, label: label || null });
      }
    }
  }

  return {
    header,
    nodes: [...nodeMap.values()],
    links: links.map(({ key, ...rest }) => rest),
    warnings,
  };
}

/**
 * Fold a parsed Mermaid graph into an existing explorer payload.
 *
 * Nodes whose normalised label already exists as a concept are merged into
 * that concept instead of creating a duplicate; genuinely new labels are
 * added. Returns a new payload plus a merge report -- it never mutates the
 * input.
 */
export function mergeMermaidIntoGraph(payload, mermaidText, { prefix = 'mmd' } = {}) {
  const parsed = parseMermaid(mermaidText);
  const byLabel = new Map();
  for (const node of payload.nodes) {
    if (node.kind === 'concept') byLabel.set(node.label.toLowerCase(), node);
  }

  const nodes = payload.nodes.map((n) => ({ ...n }));
  const links = payload.links.map((l) => ({ ...l }));
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const counted = new Set();
  let merged = 0;
  let created = 0;

  // Idempotent: the same label may be visited once per edge endpoint and
  // again in the standalone sweep. It must be counted exactly once.
  const ensure = (id, label) => {
    const key = String(label).toLowerCase();
    const existing = byLabel.get(key);
    if (existing) {
      if (!counted.has(key)) {
        counted.add(key);
        merged += 1;
      }
      return existing.id;
    }
    const newId = `${prefix}:${id}`;
    if (index.has(newId)) return newId;
    const node = {
      id: newId,
      kind: 'mermaid',
      label: String(label).slice(0, 60),
      sources: ['mermaid'],
      degree: 0,
      unresolved: false,
    };
    nodes.push(node);
    index.set(newId, nodes.length - 1);
    byLabel.set(key, node);
    counted.add(key);
    created += 1;
    return newId;
  };

  for (const link of parsed.links) {
    const sourceLabel = parsed.nodes.find((n) => n.id === link.source);
    const targetLabel = parsed.nodes.find((n) => n.id === link.target);
    if (!sourceLabel || !targetLabel) continue;
    const s = ensure(link.source, sourceLabel.label);
    const t = ensure(link.target, targetLabel.label);
    if (s === t) continue;
    if (!links.some((l) => l.source === s && l.target === t && l.kind === 'mermaid')) {
      links.push({ source: s, target: t, kind: 'mermaid', affinity: 0, label: link.label });
    }
  }

  // Standalone node declarations carry no edge but are still real content.
  for (const node of parsed.nodes) {
    ensure(node.id, node.label);
  }

  const stats = refreshDerivedFields(nodes, links, payload.stats);

  return {
    payload: { ...payload, nodes, links, stats },
    report: { merged, created, parsed: parsed.nodes.length, warnings: parsed.warnings },
  };
}

/**
 * Re-derive everything the renderer trusts but this module just invalidated.
 *
 * Why this is not optional: new nodes are born with `degree: 0` and no
 * coordinates, and the explorer's default `minDegree` filter is 1 while
 * `buildScene()` never re-runs `layout()`. Before this existed, folding a
 * Mermaid diagram produced a report saying "ok" and a graph where every
 * integrated node was invisible and stacked at the origin.
 */
function refreshDerivedFields(nodes, links, prevStats = {}) {
  // Degree is undirected and must match src/api/memory_graph.py: each link
  // contributes +1 to both endpoints. Recomputing for *all* nodes (not just the
  // new ones) keeps the invariant true if a caller passes in a stale payload.
  const degree = new Map();
  for (const link of links) {
    degree.set(link.source, (degree.get(link.source) || 0) + 1);
    degree.set(link.target, (degree.get(link.target) || 0) + 1);
  }
  for (const node of nodes) {
    node.degree = degree.get(node.id) || 0;
  }

  // Deterministic fibonacci-sphere seeding, identical to explorer.mjs layout(),
  // so newly folded nodes get a stable non-overlapping position.
  const missing = nodes.filter((n) => !Number.isFinite(n.x) || !Number.isFinite(n.y) || !Number.isFinite(n.z));
  if (missing.length) {
    const total = nodes.length;
    const golden = Math.PI * (3 - Math.sqrt(5));
    const spread = 26 + Math.sqrt(total) * 3.4;
    for (const node of missing) {
      const i = nodes.indexOf(node);
      const y = 1 - (i / Math.max(total - 1, 1)) * 2;
      const radius = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = golden * i;
      node.x = Math.cos(theta) * radius * spread;
      node.y = y * spread;
      node.z = Math.sin(theta) * radius * spread;
    }
  }

  const stats = {
    ...prevStats,
    nodes: nodes.length,
    links: links.length,
  };
  if (typeof stats.unresolved_edges === 'number') {
    stats.unresolved_edges = links.filter((l) => l.unresolved).length;
  }
  return stats;
}

export default { parseMermaid, mergeMermaidIntoGraph };
