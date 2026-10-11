// Display-only physics. Connections come from the saved evidence API.
const hash = key => [...key].reduce((n, c) => (Math.imul(n, 31) + c.charCodeAt(0)) >>> 0, 17);
const free = (node, held) => !node.pinned && node.account_key !== held;
// Normal display coordinates use a fast Euclidean length in the O(n^2) loops;
// hypot remains the overflow fallback for unusually large saved coordinates.
const length = (dx, dy) => { const squared = dx * dx + dy * dy; return Number.isFinite(squared) ? Math.sqrt(squared) : Math.hypot(dx, dy); };

export function createGraphLayout(data, positions = new Map(), width = 940, height = 600) {
  const degrees = new Map(data.nodes.map(n => [n.account_key, new Set()])), pairs = new Map();
  for (const edge of data.edges) {
    const a = edge.source_key, b = edge.target_key;
    if (a === b || !degrees.has(a) || !degrees.has(b)) continue;
    degrees.get(a).add(b); degrees.get(b).add(a); pairs.set([a, b].sort().join('|'), [a, b]);
  }
  const nodes = data.nodes.map((raw, i) => {
    const degree = degrees.get(raw.account_key).size, saved = positions.get(raw.account_key), angle = i * 2.3999632297, distance = 32 * Math.sqrt(i + 1);
    return { ...raw, degree, radius: 5 + Math.min(7, Math.sqrt(degree) * 1.5), mass: 1 + Math.sqrt(degree) * .25,
      x: Number.isFinite(saved?.x) ? saved.x : width / 2 + Math.cos(angle) * distance,
      y: Number.isFinite(saved?.y) ? saved.y : height / 2 + Math.sin(angle) * distance,
      vx: 0, vy: 0, pinned: Boolean(saved?.pinned) };
  });
  const byKey = new Map(nodes.map(n => [n.account_key, n]));
  for (const node of nodes) if (!positions.has(node.account_key)) {
    const neighbours = [...degrees.get(node.account_key)].map(key => byKey.get(key)).filter(n => positions.has(n.account_key));
    if (neighbours.length) {
      const angle = hash(node.account_key) / 0xffffffff * Math.PI * 2;
      node.x = neighbours.reduce((sum, n) => sum + n.x, 0) / neighbours.length + Math.cos(angle) * 85;
      node.y = neighbours.reduce((sum, n) => sum + n.y, 0) / neighbours.length + Math.sin(angle) * 85;
    }
  }
  return { nodes, byKey, springs: [...pairs.values()].map(([a,b]) => [byKey.get(a), byKey.get(b)]), width, height };
}

export function stepGraphLayout(layout, { held = '', alpha = 1 } = {}) {
  const { nodes, springs, width, height } = layout;
  const previous = nodes.map(node => [node.x, node.y]);
  for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = nodes[i], b = nodes[j];
    let dx = b.x - a.x, dy = b.y - a.y, distance = length(dx, dy);
    if (distance < .001) { const angle = hash(a.account_key + '|' + b.account_key) / 0xffffffff * Math.PI * 2; dx = Math.cos(angle); dy = Math.sin(angle); distance = 1; }
    const nx = dx / distance, ny = dy / distance, repel = Math.min(9, 9000 / Math.max(20, distance) ** 2) * alpha;
    if (free(a, held)) { a.vx -= nx * repel / a.mass; a.vy -= ny * repel / a.mass; }
    if (free(b, held)) { b.vx += nx * repel / b.mass; b.vy += ny * repel / b.mass; }
  }
  for (const [a,b] of springs) {
    const dx = b.x - a.x, dy = b.y - a.y, distance = Math.max(1, length(dx,dy));
    const pull = (distance - 155) * .012 / Math.sqrt(Math.max(1, Math.min(a.degree,b.degree))) * alpha;
    if (free(a, held)) { a.vx += dx / distance * pull / a.mass; a.vy += dy / distance * pull / a.mass; }
    if (free(b, held)) { b.vx -= dx / distance * pull / b.mass; b.vy -= dy / distance * pull / b.mass; }
  }
  for (const node of nodes) {
    if (!free(node, held)) { node.vx = node.vy = 0; continue; }
    const center = .001;
    node.vx = Math.max(-12,Math.min(12,(node.vx + (width / 2 - node.x) * center * alpha) * .74));
    node.vy = Math.max(-12,Math.min(12,(node.vy + (height / 2 - node.y) * center * alpha) * .74));
    node.x += node.vx; node.y += node.vy;
  }
  // Project collisions after integration so springs cannot immediately undo a
  // direct gesture. Multiple sweeps also separate a cluster around a held node.
  for (let pass = 0; pass < 3; pass++) {
    let projected = false;
    for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
    const a = nodes[i], b = nodes[j], movable = Number(free(a, held)) + Number(free(b, held));
    if (!movable) continue;
    let dx = b.x - a.x, dy = b.y - a.y, distance = length(dx, dy);
    const gap = a.radius + b.radius + 28;
    if (distance >= gap) continue;
    projected = true;
    if (distance < .001) { const angle = hash(a.account_key + '|' + b.account_key) / 0xffffffff * Math.PI * 2; dx = Math.cos(angle); dy = Math.sin(angle); distance = 0; }
    const normalLength = length(dx, dy), nx = dx / normalLength, ny = dy / normalLength, push = (gap - distance) / movable;
    if (free(a, held)) { a.x -= nx * push; a.y -= ny * push; const inward = Math.max(0, a.vx * nx + a.vy * ny); a.vx -= nx * inward; a.vy -= ny * inward; }
    if (free(b, held)) { b.x += nx * push; b.y += ny * push; const inward = Math.min(0, b.vx * nx + b.vy * ny); b.vx -= nx * inward; b.vy -= ny * inward; }
    }
    // With no projection the next sweep sees identical coordinates, so it is
    // guaranteed to do no work. Preserve all sweeps whenever a pair moved.
    if (!projected) break;
  }
  // Include collision projections in the measured movement. Velocity alone can
  // report zero while coincident nodes have moved by an entire node diameter.
  return Math.max(0, ...nodes.map((node, i) => length(node.x - previous[i][0], node.y - previous[i][1])));
}

export const GRAPH_STABLE_MOVEMENT = .035;
export const GRAPH_STABLE_QUIET_STEPS = 1800;
export const GRAPH_STABLE_DRIFT = .35;
// A short quiet interval starts confirmation; it does not stop the solver.
// Dense graphs can pass through a slow plateau and move again hundreds of
// physical steps later. Any renewed movement resets the whole quiet window.
export function createGraphMotion() { return { alpha: 1, quiet: 0, quietSteps: 0, quietMinimumMovement: Infinity, confirmationOrigin: null, confirmationSteps: 0, confirmationDisplacement: 0, confirmationResets: 0, iterations: 0, movement: Infinity, stable: false }; }
export function graphMotionStepLimit(motion, nodeCount) { return motion.quiet >= 30 ? 48 : nodeCount < 100 ? 3 : 24; }
export function graphConfirmationDisplacement(motion, nodes) { return motion.confirmationOrigin ? Math.max(0, ...nodes.map((node,i)=>length(node.x-motion.confirmationOrigin[i][0],node.y-motion.confirmationOrigin[i][1]))) : 0; }
export function advanceGraphMotion(motion, movement, steps = 1, nodes = null, maximumConfirmationDisplacement = null) {
  motion.iterations++; motion.movement = movement;
  motion.alpha = Math.max(.35, motion.alpha * .994);
  const quiet = Number.isFinite(movement) && movement <= GRAPH_STABLE_MOVEMENT;
  // A rising residual can remain below the displacement limit for a long
  // plateau. Restart confirmation before it grows into visible movement.
  const rising = quiet && motion.quietSteps > 0 && movement > motion.quietMinimumMovement * 1.15 + .0005;
  const stepCount = Math.max(1, Math.floor(Number(steps) || 1));
  motion.quiet = quiet ? rising ? 1 : motion.quiet + 1 : 0;
  motion.quietSteps = quiet ? rising ? stepCount : motion.quietSteps + stepCount : 0;
  motion.quietMinimumMovement = quiet ? rising ? movement : Math.min(motion.quietMinimumMovement, movement) : Infinity;
  if (nodes) {
    if (motion.quiet < 30) { motion.confirmationOrigin = null; motion.confirmationSteps = 0; motion.confirmationDisplacement = 0; }
    else {
      const displacement = maximumConfirmationDisplacement ?? graphConfirmationDisplacement(motion,nodes);
      if (!motion.confirmationOrigin || displacement > GRAPH_STABLE_DRIFT) {
        if (motion.confirmationOrigin) motion.confirmationResets++;
        motion.confirmationOrigin = nodes.map(node=>[node.x,node.y]); motion.confirmationSteps = 0; motion.confirmationDisplacement = 0;
      } else { motion.confirmationSteps += stepCount; motion.confirmationDisplacement = Math.max(motion.confirmationDisplacement,displacement); }
    }
  } else { motion.confirmationSteps = motion.quietSteps; motion.confirmationDisplacement = 0; }
  motion.stable = motion.quiet >= 30 && motion.confirmationSteps >= GRAPH_STABLE_QUIET_STEPS;
  return motion.stable;
}

export function settleGraphLayout(layout, steps = 70) {
  for(let i=0;i<steps;i++) stepGraphLayout(layout,{alpha:1-i/steps*.6});
  return layout;
}

export function graphLabelPlacements(nodes, scale, { selected = '', hovered = '' } = {}) {
  const labels = new Map(), occupied = [], budget = nodes.length < 12 ? nodes.length : scale < .7 ? 8 : scale < 1.2 ? 16 : 32;
  const overlaps = box => occupied.some(b => box.x < b.x+b.width+5 && box.x+box.width+5 > b.x && box.y < b.y+b.height+4 && box.y+box.height+4 > b.y);
  const coversNode = (box, own) => nodes.some(n => n !== own && box.x < n.x+n.radius+3 && box.x+box.width > n.x-n.radius-3 && box.y < n.y+n.radius+3 && box.y+box.height > n.y-n.radius-3);
  const priority = n => (n.account_key === hovered ? 10000 : n.account_key === selected ? 9000 : 0) + n.degree;
  for(const node of [...nodes].sort((a,b)=>priority(b)-priority(a))) {
    if(labels.size>=budget && ![selected,hovered].includes(node.account_key)) continue;
    const text = [...String(node.name || node.handle || node.account_key)].slice(0,node.account_key===selected ? 32 : 18).join(''), font = 13 / Math.max(.25,scale);
    const width = [...text].reduce((sum,c)=>sum+(c.charCodeAt(0)>255 ? font : font*.55),0);
    const right = {x:node.x+node.radius+8,y:node.y-font*.8,width,height:font*1.3}, left={...right,x:node.x-node.radius-8-width};
    const box = !overlaps(right) && !coversNode(right,node) ? right : !overlaps(left) && !coversNode(left,node) ? left : null;
    if(!box && ![selected,hovered].includes(node.account_key)) continue;
    occupied.push(box || right); labels.set(node.account_key,{text,x:box===left ? -node.radius-8 : node.radius+8,anchor:box===left ? 'end' : 'start',font,bounds:box || right});
  }
  return labels;
}
