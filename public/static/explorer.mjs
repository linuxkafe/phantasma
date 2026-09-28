/**
 * pHantasma · Explorador 3D da Memória
 *
 * Renders /api/memory/graph with WebGL (three.js) in a genuine 3D scene:
 * orbit / zoom / pan, click-to-focus, hover picking, search, degree and kind
 * filtering, plus a Mermaid panel whose diagrams can be folded back into the
 * 3D graph.
 *
 * The force layout is implemented here (repulsion + springs + centring) rather
 * than pulled from a library, so the whole explorer depends only on three.js.
 *
 * Honesty rules carried over from the backend:
 *   - stats come from the payload, never recomputed for display;
 *   - a reference the database does not resolve is drawn in red and counted,
 *     not quietly connected to something that looks plausible;
 *   - FlyBrain reinforcement is shown only when flybrain_state has rows.
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { mergeMermaidIntoGraph, parseMermaid } from './mermaid_graph.mjs';

const KIND_COLOR = {
  memory: 0x4aa3ff,
  concept: 0x00d4aa,
  mermaid: 0xb388ff,
};
const UNRESOLVED_COLOR = 0xff6b6b;
const BG = 0x06060c;

const $ = (id) => document.getElementById(id);
const el = {
  stage: $('stage'), loading: $('loading'), tooltip: $('tooltip'),
  details: $('details'), detailBody: $('detail-body'),
  search: $('search'), degree: $('degree'), degreeVal: $('degree-val'),
  kMemory: $('k-memory'), kConcept: $('k-concept'),
  kMermaid: $('k-mermaid'), kUnresolved: $('k-unresolved'),
  btnReset: $('btn-reset'), btnRotate: $('btn-rotate'),
  btnLabels: $('btn-labels'), btnPhysics: $('btn-physics'),
  btnClose: $('btn-close'), visible: $('visible-count'),
  sMem: $('s-mem'), sCon: $('s-con'), sLink: $('s-link'),
  sMmd: $('s-mmd'), sDangle: $('s-dangle'), sDangleBox: $('s-dangle-box'),
  sFly: $('s-fly'), sFlyBox: $('s-fly-box'),
};

/* ------------------------------------------------------------------ state */
const state = {
  payload: null,          // { nodes, links, stats }
  nodeById: new Map(),
  meshById: new Map(),
  labelById: new Map(),
  selected: null,
  hovered: null,
  matchIds: null,         // Set|null from the search box
  showLabels: true,
  autoRotate: true,
  physics: false,
  minDegree: 1,
  kinds: { memory: true, concept: true, mermaid: true },
  showUnresolved: true,
};

/* ------------------------------------------------------------ three setup */
const scene = new THREE.Scene();
scene.background = new THREE.Color(BG);
scene.fog = new THREE.FogExp2(BG, 0.0016);

const camera = new THREE.PerspectiveCamera(
  55, window.innerWidth / window.innerHeight, 0.5, 4000
);
camera.position.set(0, 40, 190);

const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
el.stage.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.autoRotateSpeed = 0.7;
controls.minDistance = 8;
controls.maxDistance = 900;

scene.add(new THREE.AmbientLight(0xffffff, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 0.75);
key.position.set(60, 90, 120);
scene.add(key);
const rim = new THREE.DirectionalLight(0x00d4aa, 0.35);
rim.position.set(-80, -40, -60);
scene.add(rim);

const nodeGroup = new THREE.Group();
const linkGroup = new THREE.Group();
const labelGroup = new THREE.Group();
scene.add(nodeGroup, linkGroup, labelGroup);

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

/* ------------------------------------------------------------------ utils */
function escapeHtml(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function makeLabelTexture(text, color) {
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const font = '600 34px Inter, system-ui, sans-serif';
  ctx.font = font;
  const width = Math.ceil(ctx.measureText(text).width) + 26;
  canvas.width = Math.max(width, 8);
  canvas.height = 52;
  const c = canvas.getContext('2d');
  c.font = font;
  c.fillStyle = 'rgba(6,6,12,0.72)';
  c.beginPath();
  const r = 10;
  const w = canvas.width;
  const h = canvas.height;
  c.moveTo(r, 0); c.lineTo(w - r, 0); c.quadraticCurveTo(w, 0, w, r);
  c.lineTo(w, h - r); c.quadraticCurveTo(w, h, w - r, h);
  c.lineTo(r, h); c.quadraticCurveTo(0, h, 0, h - r);
  c.lineTo(0, r); c.quadraticCurveTo(0, 0, r, 0);
  c.fill();
  c.strokeStyle = color; c.lineWidth = 2; c.stroke();
  c.fillStyle = '#e6e9ef';
  c.textBaseline = 'middle';
  c.fillText(text, 13, h / 2 + 1);
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  return texture;
}

/* ------------------------------------------------------------- force layout */
function layout(iterations = 320) {
  const nodes = state.payload.nodes;
  const n = nodes.length;
  if (!n) return;

  // Seed positions on a fibonacci sphere: deterministic, no overlap clumps.
  const golden = Math.PI * (3 - Math.sqrt(5));
  nodes.forEach((node, i) => {
    if (Number.isFinite(node.x)) return;
    const y = 1 - (i / Math.max(n - 1, 1)) * 2;
    const radius = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = golden * i;
    const spread = 26 + Math.sqrt(n) * 3.4;
    node.x = Math.cos(theta) * radius * spread;
    node.y = y * spread;
    node.z = Math.sin(theta) * radius * spread;
    node.vx = 0; node.vy = 0; node.vz = 0;
  });

  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const springs = [];
  for (const link of state.payload.links) {
    const a = index.get(link.source);
    const b = index.get(link.target);
    if (a === undefined || b === undefined || a === b) continue;
    springs.push([a, b, 0.55 + Math.min(Math.abs(link.affinity || 0), 1) * 0.4]);
  }

  const repulsion = 210;
  const springLength = 34;
  const damping = 0.86;

  for (let step = 0; step < iterations; step += 1) {
    const cooling = 1 - step / iterations;

    for (let i = 0; i < n; i += 1) {
      const a = nodes[i];
      for (let j = i + 1; j < n; j += 1) {
        const b = nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dz = a.z - b.z;
        let distSq = dx * dx + dy * dy + dz * dz;
        if (distSq < 0.01) {
          // deterministic nudge instead of random jitter
          dx = ((i % 7) - 3) * 0.1 || 0.1;
          dy = ((j % 5) - 2) * 0.1 || 0.1;
          dz = 0.05;
          distSq = dx * dx + dy * dy + dz * dz;
        }
        const dist = Math.sqrt(distSq);
        const force = repulsion / distSq;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        const fz = (dz / dist) * force;
        a.vx += fx; a.vy += fy; a.vz += fz;
        b.vx -= fx; b.vy -= fy; b.vz -= fz;
      }
    }

    for (const [ai, bi, stiffness] of springs) {
      const a = nodes[ai];
      const b = nodes[bi];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dz = b.z - a.z;
      const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
      const force = (dist - springLength) * stiffness;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      const fz = (dz / dist) * force;
      a.vx += fx; a.vy += fy; a.vz += fz;
      b.vx -= fx; b.vy -= fy; b.vz -= fz;
    }

    for (let i = 0; i < n; i += 1) {
      const node = nodes[i];
      node.vx -= node.x * 0.012;
      node.vy -= node.y * 0.012;
      node.vz -= node.z * 0.012;
      node.vx *= damping; node.vy *= damping; node.vz *= damping;
      const limit = 26 * cooling;
      node.x += THREE.MathUtils.clamp(node.vx, -limit, limit);
      node.y += THREE.MathUtils.clamp(node.vy, -limit, limit);
      node.z += THREE.MathUtils.clamp(node.vz, -limit, limit);
    }
  }
}

/* ------------------------------------------------------------ scene build */
function nodeRadius(node) {
  const degree = node.degree || 0;
  if (node.kind === 'memory') return 1.5 + Math.min(Math.log2(1 + degree), 3) * 0.55;
  return 1.1 + Math.min(Math.log2(1 + degree), 4) * 0.6;
}

function isVisibleNode(node) {
  if (node.unresolved && !state.showUnresolved) return false;
  if (!state.kinds[node.kind] && !(node.kind === 'mermaid' && state.kinds.mermaid)) return false;
  if (node.kind === 'mermaid' && !state.kinds.mermaid) return false;
  if (node.kind === 'memory' && !state.kinds.memory) return false;
  if (node.kind === 'concept' && !state.kinds.concept) return false;
  if ((node.degree || 0) < state.minDegree) return false;
  if (state.matchIds && !state.matchIds.has(node.id)) return false;
  return true;
}

function buildScene() {
  nodeGroup.clear();
  linkGroup.clear();
  labelGroup.clear();
  state.meshById.clear();
  state.labelById.clear();

  const sphere = new THREE.SphereGeometry(1, 18, 14);
  const visible = [];

  for (const node of state.payload.nodes) {
    state.nodeById.set(node.id, node);
    if (!isVisibleNode(node)) continue;
    visible.push(node);

    const material = new THREE.MeshStandardMaterial({
      color: node.unresolved ? UNRESOLVED_COLOR : (KIND_COLOR[node.kind] || KIND_COLOR.concept),
      roughness: 0.42,
      metalness: 0.12,
      emissive: node.unresolved ? 0x3a0d0d : 0x000000,
    });
    const mesh = new THREE.Mesh(sphere, material);
    const radius = nodeRadius(node);
    mesh.scale.setScalar(radius);
    mesh.position.set(node.x || 0, node.y || 0, node.z || 0);
    mesh.userData.nodeId = node.id;
    mesh.userData.baseRadius = radius;
    nodeGroup.add(mesh);
    state.meshById.set(node.id, mesh);

    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
      map: makeLabelTexture(
        node.label.length > 26 ? `${node.label.slice(0, 25)}…` : node.label,
        node.unresolved ? '#ff6b6b' : '#00d4aa'
      ),
      depthTest: false,
      transparent: true,
    }));
    const scale = 0.055;
    sprite.scale.set(sprite.material.map.image.width * scale, sprite.material.map.image.height * scale, 1);
    sprite.position.set(node.x || 0, (node.y || 0) + radius + 2.4, node.z || 0);
    sprite.renderOrder = 5;
    labelGroup.add(sprite);
    state.labelById.set(node.id, sprite);
  }

  // links as one LineSegments buffer
  const visibleIds = new Set(visible.map((n) => n.id));
  const positions = [];
  const colors = [];
  for (const link of state.payload.links) {
    if (!visibleIds.has(link.source) || !visibleIds.has(link.target)) continue;
    const a = state.nodeById.get(link.source);
    const b = state.nodeById.get(link.target);
    positions.push(a.x || 0, a.y || 0, a.z || 0, b.x || 0, b.y || 0, b.z || 0);
    const hex = link.kind === 'graph' ? 0x00d4aa : (link.kind === 'mermaid' ? 0xb388ff : 0x2f4a63);
    const c = new THREE.Color(hex);
    colors.push(c.r, c.g, c.b, c.r, c.g, c.b);
  }
  if (positions.length) {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    linkGroup.add(new THREE.LineSegments(
      geometry,
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.55 })
    ));
  }

  labelGroup.visible = state.showLabels;
  el.visible.textContent = `${visible.length} visíveis`;
  return visible.length;
}

/* ------------------------------------------------------------- interaction */
function focusNode(node, distance = 46) {
  const target = new THREE.Vector3(node.x || 0, node.y || 0, node.z || 0);
  const dir = new THREE.Vector3().subVectors(camera.position, target);
  if (dir.lengthSq() < 0.01) dir.set(0.4, 0.5, 1);
  dir.normalize().multiplyScalar(distance);
  const destination = target.clone().add(dir);
  animateCamera(destination, target);
}

let cameraTween = null;
function animateCamera(position, target, duration = 850) {
  cameraTween = {
    fromPos: camera.position.clone(),
    toPos: position.clone(),
    fromTarget: controls.target.clone(),
    toTarget: target.clone(),
    start: performance.now(),
    duration,
  };
}

function stepCameraTween() {
  if (!cameraTween) return;
  const t = Math.min(1, (performance.now() - cameraTween.start) / cameraTween.duration);
  const eased = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
  camera.position.lerpVectors(cameraTween.fromPos, cameraTween.toPos, eased);
  controls.target.lerpVectors(cameraTween.fromTarget, cameraTween.toTarget, eased);
  if (t >= 1) cameraTween = null;
}

function pickAt(clientX, clientY) {
  pointer.x = (clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(clientY / window.innerHeight) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(nodeGroup.children, false);
  return hits.length ? hits[0].object.userData.nodeId : null;
}

function highlight(nodeId, on) {
  const mesh = state.meshById.get(nodeId);
  if (!mesh) return;
  const node = state.nodeById.get(nodeId);
  const base = node.unresolved ? UNRESOLVED_COLOR : (KIND_COLOR[node.kind] || KIND_COLOR.concept);
  mesh.material.emissive.setHex(on ? base : 0x000000);
  mesh.material.emissiveIntensity = on ? 0.85 : 0;
  mesh.scale.setScalar(mesh.userData.baseRadius * (on ? 1.45 : 1));
}

function clearHighlight() {
  if (state.selected) highlight(state.selected, false);
  if (state.hovered) highlight(state.hovered, false);
  state.selected = null;
  state.hovered = null;
}

/* -------------------------------------------------------------- details UI */
function showDetails(node) {
  const parts = [];
  parts.push(`<span class="kind ${escapeHtml(node.kind)}">${escapeHtml(node.kind)}</span>`);

  if (node.unresolved) {
    parts.push(
      '<div class="warnbox">Referência guardada cujo alvo não existe na base. ' +
      'Está marcada a vermelho — não foi ligada a nada por semelhança.</div>'
    );
  }

  parts.push(`<p class="dlabel">${escapeHtml(node.label)}</p>`);

  const meta = [`<code>${escapeHtml(node.id)}</code>`];
  if (node.timestamp) meta.push(escapeHtml(node.timestamp));
  if (node.degree !== undefined) meta.push(`grau ${node.degree}`);
  if (node.touch_count) meta.push(`${node.touch_count} toques`);
  if (node.affinity) meta.push(`afinidade ${Number(node.affinity).toFixed(2)}`);
  if (node.graph_key) meta.push(`chave ${escapeHtml(node.graph_key)}`);
  parts.push(`<div class="dmeta">${meta.join(' · ')}</div>`);

  if (node.sources && node.sources.length) {
    parts.push(`<div class="dmeta">origem: ${node.sources.map(escapeHtml).join(', ')}</div>`);
  }

  if (node.tags && node.tags.length) {
    parts.push('<div class="dmeta">etiquetas</div><div id="d-tags"></div>');
  }

  if (node.facts && node.facts.length) {
    parts.push(
      `<div class="dmeta">factos (${node.facts.length})</div>` +
      `<ul class="facts">${node.facts.map((f) => `<li>${escapeHtml(f)}</li>`).join('')}</ul>`
    );
  }

  if (node.memory_ids && node.memory_ids.length) {
    parts.push(
      `<div class="dmeta">memórias com esta etiqueta: ${node.memory_ids.length}</div>`
    );
  }

  if (node.mermaid) {
    parts.push(
      '<div class="row" style="margin:12px 0 8px">' +
      '<button id="btn-render-mermaid">Ver Mermaid</button>' +
      '<button id="btn-fold-mermaid">Integrar no 3D</button>' +
      '</div>' +
      '<div id="mermaid-host"><div class="hint">diagrama não carregado</div></div>'
    );
  }

  if (node.raw) {
    parts.push(
      '<div class="dmeta" style="margin-top:14px">payload guardado</div>' +
      `<pre class="raw">${escapeHtml(node.raw)}</pre>`
    );
  }

  el.detailBody.innerHTML = parts.join('');
  el.details.classList.add('open');

  const tagHost = $('d-tags');
  if (tagHost && node.tags) {
    tagHost.innerHTML = node.tags
      .map((t) => `<span class="tag" data-tag="${escapeHtml(t)}">#${escapeHtml(t)}</span>`)
      .join('');
    tagHost.querySelectorAll('.tag').forEach((chip) => {
      chip.addEventListener('click', () => {
        el.search.value = chip.dataset.tag;
        runSearch();
      });
    });
  }

  const renderBtn = $('btn-render-mermaid');
  if (renderBtn) {
    renderBtn.addEventListener('click', () => renderMermaid(node.mermaid));
    $('btn-fold-mermaid').addEventListener('click', () => foldMermaid(node.mermaid, node));
  }

  // --- graph editing -------------------------------------------------
  // Weight and affinity are corrections to what was inferred, NOT training
  // events. Nothing here calls the FlyBrain: a drag must not move learned
  // state, because an accidental drag would train the wrong thing and be
  // indistinguishable from deliberate feedback. Training lives on the
  // reaction path (/api/reaction).
    appendEdgeEditor(node);
  }

  // ---------------------------------------------------------------------
  function postJSON(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    }).then(async r => ({status: r.status, data: await r.json().catch(() => ({}))}));
  }

  function editSection(title, inner) {
    const d = document.createElement('div');
    d.className = 'edit-section';
    d.innerHTML = '<h4>' + escapeHtml(title) + '</h4>' + inner;
    return d;
  }

  function numberField(id, label, value, step) {
    return '<label class="edit-row"><span>' + escapeHtml(label) + '</span>' +
      '<input id="' + id + '" type="number" value="' + Number(value || 0) +
      '" min="-1" max="1" step="' + (step || 0.05) + '"></label>';
  }

  function feedback(msg, isError) {
    const host = $('edit-feedback');
    if (!host) return;
    host.textContent = msg;
    host.className = 'edit-feedback' + (isError ? ' err' : ' ok');
  }

  function reloadGraph() { location.reload(); }

  function appendEdgeEditor(node) {
    const host = $('detail-body');
    if (!host) return;

    if (node.kind === 'edge') {
      host.appendChild(editSection('Aresta',
        numberField('edit-weight', 'peso', node.weight) +
        numberField('edit-affinity', 'afinidade', node.affinity) +
        '<div class="edit-row"><button id="edit-save-edge" class="btn">Guardar peso</button>' +
        '<button id="edit-del-edge" class="btn danger">Apagar aresta</button></div>'));
      $('edit-save-edge').addEventListener('click', async () => {
        feedback('A guardar...', false);
        const r = await postJSON('/api/graph/edge', {
          op: 'update', node_key: node.id,
          weight: parseFloat($('edit-weight').value),
          affinity: parseFloat($('edit-affinity').value),
        });
        if (r.data.ok) { feedback('Guardado', false); setTimeout(reloadGraph, 500); }
        else feedback(r.data.error || ('erro ' + r.status), true);
      });
      $('edit-del-edge').addEventListener('click', async () => {
        if (!confirm('Apagar esta aresta? A operação fica registada.')) return;
        const r = await postJSON('/api/graph/edge', {op: 'delete', node_key: node.id});
        if (r.data.ok) { feedback('Aresta apagada', false); setTimeout(reloadGraph, 500); }
        else feedback(r.data.error || 'erro', true);
      });
    }

    host.appendChild(editSection('Nó',
      '<label class="edit-row"><span>rótulo</span><input id="edit-label" value="' +
      escapeHtml(node.label || '') + '"></label>' +
      // The weight control only appears where there is a STORED row to write.
      // Measured: 239 of the 241 nodes in the payload have no `graph_key` --
      // their weight is an aggregate derived from memories, not a saved value.
      // Offering an editable field there produced a 422 on every save, and the
      // error was invisible (the feedback element did not exist), which is
      // exactly the "it does not save" report. Creating 239 nodes just to hold
      // a weight would change what the graph IS, and nobody asked for that.
      (node.graph_key
        ? numberField('edit-node-weight', 'peso do nó', node.weight)
        : '<p class="edit-note">Peso derivado: ' + Number(node.weight).toFixed(2) +
          '. Este conceito não tem linha guardada, por isso não há valor a ' +
          'editar aqui — o peso vem das memórias que o mencionam.</p>') +
      '<div class="edit-row"><button id="edit-save-node" class="btn">Guardar nó</button></div>'));
    $('edit-save-node').addEventListener('click', async () => {
      // `graph_key` is the memory_graph.node_key the backend edits; `id` is the
      // concept id the renderer uses, and for a stored concept the two differ
      // (id='tag:capitalismo-tardio', node_key='node:capitalismo tardio').
      // Sending `id` produced 422 "no such node", so node weight AND node label
      // were both unsaveable -- it only ever looked fine because the edge editor
      // sends node_key already and that path worked.
      const key = node.graph_key || node.id;
      const r = await postJSON('/api/graph/edge', {
        op: 'node', node_key: key, label: $('edit-label').value,
        ...(node.graph_key && $('edit-node-weight')
          ? {weight: parseFloat($('edit-node-weight').value)} : {}) });
      if (r.data.ok) { feedback('Guardado', false); setTimeout(reloadGraph, 500); }
      else feedback(r.data.error || 'erro', true);
    });

    // Resolve actions: ONLY on a dangling node (AC8). Offering them on a node
    // that resolves fine would be misleading -- there is nothing pending.
    if (node.unresolved && Array.isArray(node.dangling) && node.dangling.length) {
      const d = node.dangling[0];
      const others = (state.payload.nodes || [])
        // Only STORED nodes. `resolve_dangling` looks the label up in
        // memory_graph, so listing the 181 memory-derived concepts (which have no
        // row there) would fill the dropdown with choices that all return 422.
        .filter(n => !n.unresolved && n.kind === 'concept' && n.graph_key && n.label)
        .sort((a, b) => a.label.localeCompare(b.label))
        .slice(0, 200);
      const opts = others
        .map(n => '<option value="' + escapeHtml(n.label) + '">' + escapeHtml(n.label) + '</option>')
        .join('');
      host.appendChild(editSection('Referência por resolver',
        '<p class="edit-note">Aresta <code>' + escapeHtml(d.edge_key) + '</code> aponta para ' +
        '<code>' + escapeHtml(node.label) + '</code> (' + escapeHtml(d.side) + '), que não corresponde a nenhum ' +
        'conceito guardado. Escolhe o que é verdade:</p>' +
        '<label class="edit-row"><span>é o mesmo conceito que</span>' +
        '<select id="resolve-target"><option value="">— escolher —</option>' + opts +
        '</select></label>' +
        '<div class="edit-row"><button id="resolve-relink" class="btn">Religar a este</button>' +
        '<button id="resolve-promote" class="btn">É um conceito novo</button></div>'));

      const doResolve = async (action) => {
        const target = action === 'relink' ? $('resolve-target').value : '';
        if (action === 'relink' && !target) { feedback('Escolhe primeiro o conceito', true); return; }
        if (action === 'promote' && !confirm(
          'Criar "' + node.label + '" como conceito próprio? É uma escrita no grafo, registada na auditoria.')) return;
        feedback('A resolver...', false);
        const r = await postJSON('/api/graph/resolve', {
          edge_key: d.edge_key, side: d.side, action: action,
          target_label: target || null, actor: 'ui',
        });
        if (r.data.ok) {
          feedback('Resolvido: ' + (r.data.result.from || '') + ' → ' + (r.data.result.to || ''), false);
          setTimeout(reloadGraph, 600);
        } else feedback(r.data.error || ('erro ' + r.status), true);
      };
      $('resolve-relink').addEventListener('click', () => doResolve('relink'));
      $('resolve-promote').addEventListener('click', () => doResolve('promote'));
    }


    if (node.rag) {
      host.appendChild(editSection('RAG',
        '<label class="edit-row"><span>tags (uma por linha)</span>' +
        '<textarea id="edit-rag-tags" rows="3">' +
        escapeHtml((node.rag.tags || []).join('\n')) + '</textarea></label>' +
        '<div class="edit-row"><button id="edit-save-rag" class="btn">Guardar RAG</button></div>'));
      $('edit-save-rag').addEventListener('click', async () => {
        const tags = $('edit-rag-tags').value.split('\n').map(s => s.trim()).filter(Boolean);
        const r = await postJSON('/api/graph/rag', {key: node.ragKey, tags: tags});
        if (r.data.ok) { feedback('RAG guardado', false); setTimeout(reloadGraph, 500); }
        else feedback(r.data.error || 'erro', true);
      });
    }
  }

/** Load mermaid.min.js on first use so the 3.3MB bundle is not a startup cost. */
let mermaidPromise = null;
function ensureMermaid() {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  if (mermaidPromise) return mermaidPromise;
  mermaidPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-mermaid]');
    if (existing) {
      existing.addEventListener('load', () => resolve(window.mermaid));
      existing.addEventListener('error', reject);
      return;
    }
    const script = document.createElement('script');
    script.src = '/public/vendor/mermaid.min.js';
    script.dataset.mermaid = '1';
    script.onload = () => (window.mermaid ? resolve(window.mermaid) : reject(new Error('mermaid não expôs window.mermaid')));
    script.onerror = () => reject(new Error('falha ao carregar mermaid.min.js'));
    document.head.appendChild(script);
  });
  return mermaidPromise;
}

let mermaidCounter = 0;
async function renderMermaid(text) {
  const host = $('mermaid-host');
  if (!host) return;
  host.innerHTML = '<div class="hint">a desenhar…</div>';
  let mermaid;
  try {
    mermaid = await ensureMermaid();
  } catch (err) {
    host.innerHTML = `<div class="warnbox">${escapeHtml(err.message || err)}</div>`;
    return;
  }
  const parsed = parseMermaid(text);
  const summary =
    `<div class="hint" style="margin-bottom:6px">${parsed.nodes.length} nós · ` +
    `${parsed.links.length} ligações` +
    (parsed.warnings.length ? ` · ${parsed.warnings.length} aviso(s)` : '') + '</div>';
  mermaidCounter += 1;
  const id = `mmd-${mermaidCounter}`;
  try {
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', theme: 'dark' });
    const { svg } = await mermaid.render(id, text);
    host.innerHTML = summary + svg;
  } catch (err) {
    host.innerHTML =
      summary +
      `<div class="warnbox">Mermaid não conseguiu desenhar: ${escapeHtml(err.message || err)}</div>` +
      `<pre class="raw">${escapeHtml(text)}</pre>`;
  }
}

function foldMermaid(text, originNode) {
  const before = state.payload.links.length;
  const { payload, report } = mergeMermaidIntoGraph(state.payload, text);
  const addedLinks = payload.links.length - before;
  state.payload = payload;
  state.nodeById = new Map();
  buildScene();

  // Re-render the drawer *first*: showDetails() rebuilds #detail-body, which
  // destroys and recreates #mermaid-host. Writing the report before that would
  // silently discard it and leave the user with no feedback.
  if (originNode) {
    const refreshed = state.nodeById.get(originNode.id);
    if (refreshed) {
      state.selected = originNode.id;
      showDetails(refreshed);
      highlight(originNode.id, true);
    }
  }

  const host = $('mermaid-host');
  if (host) {
    host.innerHTML =
      '<div class="warnbox">' +
      `integrado no 3D: ${report.merged} nó(s) reaproveitado(s), ${report.created} novo(s), ` +
      `+${addedLinks} ligação(ões)` +
      (report.warnings.length ? `<br>${report.warnings.length} aviso(s) do parser` : '') +
      '</div>';
  }
}

/* ------------------------------------------------------------------ search */
function runSearch() {
  const term = el.search.value.trim().toLowerCase();
  if (!term) {
    state.matchIds = null;
  } else {
    const hits = new Set();
    for (const node of state.payload.nodes) {
      const haystack = `${node.label} ${node.id} ${(node.tags || []).join(' ')} ${
        (node.facts || []).join(' ')
      } ${node.raw || ''}`.toLowerCase();
      if (haystack.includes(term)) hits.add(node.id);
    }
    state.matchIds = hits;
    if (hits.size) {
      const first = state.payload.nodes.find((n) => hits.has(n.id));
      if (first) focusNode(first, 60);
    }
  }
  buildScene();
}

/* ------------------------------------------------------------------- stats */
function paintStats(stats) {
  el.sMem.textContent = stats.memories ?? '–';
  el.sCon.textContent = stats.concepts ?? '–';
  el.sLink.textContent = stats.links ?? '–';
  el.sMmd.textContent = stats.memories_with_mermaid ?? '–';
  if (stats.unresolved_edges) {
    el.sDangleBox.hidden = false;
    el.sDangle.textContent = stats.unresolved_edges;
  } else {
    el.sDangleBox.hidden = true;
  }
  if (stats.flybrain) {
    el.sFly.textContent = `passo ${stats.flybrain.steps ?? '?'}`;
    el.sFlyBox.title = 'estado real de flybrain_state';
  } else {
    el.sFly.textContent = 'sem estado';
    el.sFlyBox.title = 'flybrain_state está vazia — nenhum reforço persistido';
  }
}

/* -------------------------------------------------------------------- loop */
function animate() {
  requestAnimationFrame(animate);
  stepCameraTween();
  controls.autoRotate = state.autoRotate;
  controls.update();
  renderer.render(scene, camera);
}

/* ------------------------------------------------------------------- wiring */
function wireEvents() {
  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  let downAt = null;
  renderer.domElement.addEventListener('pointerdown', (e) => {
    downAt = { x: e.clientX, y: e.clientY, t: performance.now() };
  });
  renderer.domElement.addEventListener('pointerup', (e) => {
    if (!downAt) return;
    const moved = Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y);
    const quick = performance.now() - downAt.t < 400;
    downAt = null;
    if (moved > 5 || !quick) return; // it was a drag, not a click
    const id = pickAt(e.clientX, e.clientY);
    if (!id) { clearHighlight(); el.details.classList.remove('open'); return; }
    const node = state.nodeById.get(id);
    if (!node) return;
    clearHighlight();
    state.selected = id;
    highlight(id, true);
    focusNode(node);
    showDetails(node);
  });

  renderer.domElement.addEventListener('pointermove', (e) => {
    if (e.buttons) { el.tooltip.style.display = 'none'; return; }
    const id = pickAt(e.clientX, e.clientY);
    if (id !== state.hovered) {
      if (state.hovered && state.hovered !== state.selected) highlight(state.hovered, false);
      state.hovered = id;
      if (id && id !== state.selected) highlight(id, true);
    }
    if (id) {
      const node = state.nodeById.get(id);
      el.tooltip.textContent = node.label;
      el.tooltip.style.display = 'block';
      el.tooltip.style.left = `${e.clientX + 14}px`;
      el.tooltip.style.top = `${e.clientY + 14}px`;
    } else {
      el.tooltip.style.display = 'none';
    }
  });

  renderer.domElement.addEventListener('pointerleave', () => {
    el.tooltip.style.display = 'none';
  });

  el.btnClose.addEventListener('click', () => {
    el.details.classList.remove('open');
    if (state.selected) highlight(state.selected, false);
    state.selected = null;
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      el.details.classList.remove('open');
      if (state.selected) highlight(state.selected, false);
      state.selected = null;
    }
  });

  let searchTimer = null;
  el.search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 180);
  });

  el.degree.addEventListener('input', () => {
    state.minDegree = Number(el.degree.value);
    el.degreeVal.textContent = el.degree.value;
    buildScene();
  });

  const kindInputs = {
    memory: el.kMemory, concept: el.kConcept, mermaid: el.kMermaid,
  };
  for (const [kind, input] of Object.entries(kindInputs)) {
    input.addEventListener('change', () => {
      state.kinds[kind] = input.checked;
      buildScene();
    });
  }
  el.kUnresolved.addEventListener('change', () => {
    state.showUnresolved = el.kUnresolved.checked;
    buildScene();
  });

  el.btnReset.addEventListener('click', () => {
    const box = new THREE.Box3();
    for (const node of state.payload.nodes) {
      if (!Number.isFinite(node.x)) continue;
      box.expandByPoint(new THREE.Vector3(node.x, node.y, node.z));
    }
    if (box.isEmpty()) return;
    const centre = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length() || 120;
    animateCamera(centre.clone().add(new THREE.Vector3(0.2, 0.25, 1).normalize().multiplyScalar(size * 1.15)), centre);
  });

  el.btnRotate.addEventListener('click', () => {
    state.autoRotate = !state.autoRotate;
    el.btnRotate.setAttribute('aria-pressed', String(state.autoRotate));
  });

  el.btnLabels.addEventListener('click', () => {
    state.showLabels = !state.showLabels;
    labelGroup.visible = state.showLabels;
    el.btnLabels.setAttribute('aria-pressed', String(state.showLabels));
  });

  el.btnPhysics.addEventListener('click', () => {
    state.physics = !state.physics;
    el.btnPhysics.setAttribute('aria-pressed', String(state.physics));
    if (state.physics) layout(240);
  });
}

/* -------------------------------------------------------------------- boot */
async function boot() {
  try {
    const response = await fetch('/api/memory/graph', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.nodes)) throw new Error('payload inesperado');

    state.payload = payload;
    state.nodeById = new Map(payload.nodes.map((n) => [n.id, n]));
    paintStats(payload.stats || {});

    layout(payload.nodes.length > 600 ? 180 : 320);
    buildScene();
    wireEvents();
    animate();
    el.loading.style.display = 'none';
  } catch (err) {
    el.loading.innerHTML =
      `<div class="err"><strong>Não foi possível abrir o explorador.</strong><br>${escapeHtml(err.message || err)}</div>`;
  }
}

// Edge selection: nodes were the only selectable things, so the weight
// editor could not be reached from the graph at all.
// --- edge selection -------------------------------------------------
// Edges were not selectable at all: nodeIds() returned only nodes, so the
// weight editor could never be reached from the graph. This indexes the
// links the payload already carries -- including the node_key and weight the
// API now sends -- and lets the inspector open one.
function edgeIds() {
  return state.payload.links
    .filter(l => l.node_key)
    .map(l => l.node_key);
}

function selectEdge(nodeKey) {
  const link = state.payload.links.find(l => l.node_key === nodeKey);
  if (!link) return false;
  clearHighlight();
  highlight(link.source, true);
  highlight(link.target, true);
  state.selected = {kind: 'edge', id: nodeKey};
  showDetails({
    id: nodeKey,
    kind: 'edge',
    label: link.kind,
    weight: link.weight,
    affinity: link.affinity,
    degree: undefined,
  });
  return true;
}

/* Automated-UI hook: lets the headless check drive selection deterministically
   instead of clicking guessed screen coordinates. Read-only apart from
   select(); it adds no behaviour the user cannot already trigger. */
window.__explorer = {
  state,
  select(id) {
    const node = state.nodeById.get(id);
    if (!node) return false;
    clearHighlight();
    state.selected = id;
    highlight(id, true);
    focusNode(node);
    showDetails(node);
    return true;
  },
  search(term) {
    el.search.value = term;
    runSearch();
  },
  visibleCount: () => state.meshById.size,
  nodeIds: () => [...state.nodeById.keys()],
    edgeIds: () => edgeIds(),
    selectEdge: (k) => selectEdge(k),
};

boot();
