/* The shape language in the browser: the same text, the same mesh, at any slider value.
 *
 * A line-for-line port of codenodes/shapes (expr.py, language.py, solids.py), so a page
 * can re-evaluate a shape as a slider moves instead of switching between baked copies.
 * tests/test_shape_js.py builds the same shapes with both and compares every vertex.
 *
 *   const shape = CodeShapes.parse(source);
 *   const parts = shape.buildParts({ height: 0.4 });   // [{name, mode, solid}]
 *   const solid = shape.build({ height: 0.4 });        // every part joined
 *   // solid.verts: Float64Array xyz (Z up), solid.quads / solid.tris: Int32Array
 *
 * Like the Python, it never runs code: expressions are parsed by hand and only numbers,
 * the names in scope and the functions below can appear. Booleans and bevels need
 * Blender, so a page shows parts as they are and says so.
 */
(function (root) {
"use strict";

class ExprError extends Error {}
class ShapeError extends ExprError {}

// ---- expressions --------------------------------------------------------------------------
// Values are numbers or Float64Arrays (a whole curve at once); booleans are 1 and 0.

const isArr = (v) => v instanceof Float64Array;
function map1(a, f) {
  if (!isArr(a)) return f(a);
  const out = new Float64Array(a.length);
  for (let i = 0; i < a.length; i++) out[i] = f(a[i]);
  return out;
}
function mapN(args, f) {
  let n = -1;
  for (const a of args) if (isArr(a)) {
    if (n >= 0 && a.length !== n) throw new ExprError(`values of different lengths (${n} and ${a.length})`);
    n = a.length;
  }
  if (n < 0) return f(...args);
  const out = new Float64Array(n), row = new Array(args.length);
  for (let i = 0; i < n; i++) {
    for (let k = 0; k < args.length; k++) row[k] = isArr(args[k]) ? args[k][i] : args[k];
    out[i] = f(...row);
  }
  return out;
}
const pymod = (a, b) => { const r = a - b * Math.floor(a / b); return b === 0 ? NaN : r; };
function roundHalfEven(x) {
  if (!isFinite(x)) return x;
  const f = Math.floor(x), d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}
const clip = (x, lo, hi) => Math.min(Math.max(x, lo), hi);

const FN = {
  sin: [1, Math.sin], cos: [1, Math.cos], tan: [1, Math.tan],
  asin: [1, Math.asin], acos: [1, Math.acos], atan: [1, Math.atan], atan2: [2, Math.atan2],
  sqrt: [1, Math.sqrt], abs: [1, Math.abs], sign: [1, Math.sign],
  floor: [1, Math.floor], ceil: [1, Math.ceil], round: [1, roundHalfEven],
  exp: [1, Math.exp], log: [1, Math.log], pow: [2, Math.pow],
  min: [2, Math.min], max: [2, Math.max], mod: [2, pymod], hypot: [2, Math.hypot],
  clamp: [[1, 3], (x, lo = 0, hi = 1) => clip(x, lo, hi)],
  mix: [3, (a, b, t) => a * (1 - t) + b * t], lerp: [3, (a, b, t) => a * (1 - t) + b * t],
  smoothstep: [3, (e0, e1, x) => {
    const t = clip((x - e0) / (e1 - e0 === 0 ? 1e-12 : e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  }],
  radians: [1, (d) => d * Math.PI / 180], degrees: [1, (r) => r * 180 / Math.PI],
};
const CONSTANTS = { pi: Math.PI, tau: 2 * Math.PI, e: Math.E };
const WORDS = new Set(["if", "else", "and", "or", "not", "in", "is", "lambda", "for"]);

function tokens(text) {
  const out = [];
  const re = /\s*(?:(\d[\d_]*\.?[\d_]*(?:[eE][+-]?\d+)?|\.\d[\d_]*(?:[eE][+-]?\d+)?)|([A-Za-z_]\w*)|(\*\*|\/\/|<=|>=|==|!=|[-+*\/%<>(),]))/y;
  let at = 0;
  while (at < text.length) {
    if (/^\s*$/.test(text.slice(at))) break;
    re.lastIndex = at;
    const m = re.exec(text);
    if (!m) throw new ExprError(`'${text}' is not a valid expression: unexpected '${text.slice(at).trim()[0]}'`);
    if (m[1] !== undefined) out.push({ k: "num", v: parseFloat(m[1].replace(/_/g, "")) });
    else if (m[2] !== undefined) out.push({ k: WORDS.has(m[2]) ? "kw" : "name", v: m[2] });
    else out.push({ k: "op", v: m[3] });
    at = re.lastIndex;
  }
  return out;
}

function compile(text) {
  const toks = tokens(text);
  let i = 0;
  const peek = () => toks[i], bad = (why) => { throw new ExprError(`'${text}' is not a valid expression: ${why}`); };
  const isOp = (v) => toks[i] && toks[i].k === "op" && toks[i].v === v;
  const isKw = (v) => toks[i] && toks[i].k === "kw" && toks[i].v === v;

  function expr() {
    const body = orTest();
    if (isKw("if")) {
      i++;
      const test = orTest();
      if (!isKw("else")) bad("expected 'else'");
      i++;
      const other = expr();
      return (s) => { const c = test(s), a = body(s), b = other(s); return mapN([c, a, b], (c, a, b) => (c ? a : b)); };
    }
    return body;
  }
  function orTest() {
    let left = andTest();
    while (isKw("or")) { i++; const l = left, r = andTest(); left = (s) => mapN([l(s), r(s)], (a, b) => (a || b ? 1 : 0)); }
    return left;
  }
  function andTest() {
    let left = notTest();
    while (isKw("and")) { i++; const l = left, r = notTest(); left = (s) => mapN([l(s), r(s)], (a, b) => (a && b ? 1 : 0)); }
    return left;
  }
  function notTest() {
    if (isKw("not")) { i++; const v = notTest(); return (s) => map1(v(s), (a) => (a ? 0 : 1)); }
    return comparison();
  }
  const CMP = { "<": (a, b) => a < b, "<=": (a, b) => a <= b, ">": (a, b) => a > b, ">=": (a, b) => a >= b,
                "==": (a, b) => a === b, "!=": (a, b) => a !== b };
  function comparison() {
    const left = arith();
    const t = peek();
    if (t && t.k === "op" && CMP[t.v]) {
      i++;
      const right = arith(), f = CMP[t.v];
      const n = peek();
      if (n && n.k === "op" && CMP[n.v]) throw new ExprError("chained comparisons aren't supported");
      return (s) => mapN([left(s), right(s)], (a, b) => (f(a, b) ? 1 : 0));
    }
    return left;
  }
  function arith() {
    let left = term();
    while (isOp("+") || isOp("-")) {
      const op = toks[i++].v, l = left, r = term();
      left = op === "+" ? (s) => mapN([l(s), r(s)], (a, b) => a + b) : (s) => mapN([l(s), r(s)], (a, b) => a - b);
    }
    return left;
  }
  const MUL = { "*": (a, b) => a * b, "/": (a, b) => a / b, "//": (a, b) => Math.floor(a / b), "%": pymod };
  function term() {
    let left = factor();
    while (toks[i] && toks[i].k === "op" && MUL[toks[i].v]) {
      const f = MUL[toks[i++].v], l = left, r = factor();
      left = (s) => mapN([l(s), r(s)], f);
    }
    return left;
  }
  function factor() {
    if (isOp("-")) { i++; const v = factor(); return (s) => map1(v(s), (a) => -a); }
    if (isOp("+")) { i++; return factor(); }
    return power();
  }
  function power() {
    const base = atom();
    if (isOp("**")) { i++; const ex = factor(); return (s) => mapN([base(s), ex(s)], Math.pow); }
    return base;
  }
  function atom() {
    const t = toks[i++];
    if (!t) bad("it ends too soon");
    if (t.k === "num") { const v = t.v; return () => v; }
    if (t.k === "op" && t.v === "(") {
      const inner = expr();
      if (!isOp(")")) bad("a bracket is not closed");
      i++;
      return inner;
    }
    if (t.k === "name") {
      if (t.v === "True" || t.v === "False" || t.v === "None") throw new ExprError(`${t.v} is not a number`);
      if (isOp("(")) {
        i++;
        const args = [];
        if (!isOp(")")) {
          for (;;) {
            args.push(expr());
            if (isOp(",")) { i++; continue; }
            break;
          }
        }
        if (!isOp(")")) bad("a function call is not closed");
        i++;
        const entry = FN[t.v];
        if (!entry) throw new ExprError(`unknown function '${t.v}'. Available: ${Object.keys(FN).sort().join(", ")}`);
        const [arity, f] = entry;
        const [lo, hi] = Array.isArray(arity) ? arity : [arity, arity];
        if (args.length < lo || args.length > hi) throw new ExprError(`${t.v}: takes ${lo === hi ? lo : lo + " to " + hi} arguments, got ${args.length}`);
        return (s) => mapN(args.map((a) => a(s)), f);
      }
      const name = t.v;
      return (s) => {
        if (Object.prototype.hasOwnProperty.call(s, name)) return s[name];
        const known = Object.keys(s).filter((k) => !k.startsWith("_")).sort().join(", ") || "nothing";
        throw new ExprError(`unknown name '${name}' in '${text}'. In scope: ${known}`);
      };
    }
    bad(`unexpected '${t.v}'`);
  }
  const fn = expr();
  if (i < toks.length) bad(`unexpected '${toks[i].v}'`);
  return fn;
}

const compiled = new Map();
function evaluate(source, scope) {
  if (typeof source === "number" || isArr(source)) return source;
  const text = String(source).trim();
  if (!text) throw new ExprError("the expression is empty");
  let fn = compiled.get(text);
  if (!fn) { fn = compile(text); compiled.set(text, fn); }
  return fn(Object.assign({}, CONSTANTS, scope || {}));
}
function number(source, scope, name = "value") {
  const v = evaluate(source, scope);
  if (isArr(v)) throw new ExprError(`${name} must be a single number, got ${v.length} values`);
  if (!isFinite(v)) throw new ExprError(`${name} came out as ${v} — check the maths`);
  return v;
}
function integer(source, scope, name = "value", low = null, high = null) {
  const r = roundHalfEven(number(source, scope, name));
  if (low !== null && r < low) throw new ExprError(`${name} must be at least ${low} (got ${r})`);
  if (high !== null && r > high) throw new ExprError(`${name} must be at most ${high} (got ${r})`);
  return r;
}
function linspace(a, b, n, endpoint = true) {
  const out = new Float64Array(n);
  const div = endpoint ? n - 1 : n, step = div > 0 ? (b - a) / div : 0;
  for (let k = 0; k < n; k++) out[k] = a + k * step;
  if (endpoint && n > 1) out[n - 1] = b;
  return out;
}
const broadcast = (v, n) => (isArr(v) ? v : new Float64Array(n).fill(v));

// ---- solids -------------------------------------------------------------------------------

const MAX_POINTS = 100000, MAX_FACES = 2000000;
const dist2 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const dist3 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const f32 = (arr) => { for (let i = 0; i < arr.length; i++) arr[i] = Math.fround(arr[i]); return arr; };

class Profile {
  constructor() { this.points = []; this.hard = []; this.closed = false; }
  move(x, y) {
    if (this.points.length) throw new ExprError("'move' can only start a profile; use line, arc or curve after that");
    this.points.push([x, y]); this.hard.push(true); return this;
  }
  _here(what) {
    if (!this.points.length) throw new ExprError(`'${what}' needs a starting point: put a 'move' first`);
    return this.points[this.points.length - 1];
  }
  line(x, y) { this._here("line"); this.points.push([x, y]); this.hard.push(true); return this; }
  arc(x, y, radius, steps = 16, clockwise = false) {
    const [x0, y0] = this._here("arc");
    const dx = x - x0, dy = y - y0, span = Math.hypot(dx, dy);
    if (span < 1e-12) throw new ExprError("an arc needs to end somewhere else than it starts");
    if (Math.abs(radius) < span / 2 - 1e-9)
      throw new ExprError(`radius ${radius} is too small to reach that point (needs at least ${span / 2})`);
    const height = Math.sqrt(Math.max(radius * radius - (span / 2) ** 2, 0));
    const mx = (x0 + x) / 2, my = (y0 + y) / 2, nx = -dy / span, ny = dx / span;
    const side = clockwise ? -1 : 1;
    const cx = mx + nx * height * side, cy = my + ny * height * side;
    const a0 = Math.atan2(y0 - cy, x0 - cx);
    let a1 = Math.atan2(y - cy, x - cx);
    if (clockwise && a1 > a0) a1 -= 2 * Math.PI;
    if (!clockwise && a1 < a0) a1 += 2 * Math.PI;
    steps = Math.max(2, Math.trunc(steps));
    const r = Math.abs(radius);
    for (let k = 1; k <= steps; k++) {
      const a = a0 + (a1 - a0) * k / steps;
      this.points.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
      this.hard.push(k === steps);
    }
    return this;
  }
  curve(xExpr, yExpr, steps = 24, scope = null) {
    steps = Math.max(1, Math.trunc(steps));
    const t = linspace(0, 1, steps + 1);
    const names = Object.assign({}, scope || {}, { t });
    const xs = broadcast(evaluate(xExpr, names), t.length), ys = broadcast(evaluate(yExpr, names), t.length);
    for (let k = 0; k < t.length; k++)
      if (!isFinite(xs[k]) || !isFinite(ys[k])) throw new ExprError("the curve produced values that are not finite — check the maths");
    if (!this.points.length) { this.points.push([xs[0], ys[0]]); this.hard.push(true); }
    for (let k = 1; k < t.length; k++) { this.points.push([xs[k], ys[k]]); this.hard.push(k === t.length - 1); }
    return this;
  }
  close() { this.closed = true; return this; }
  finish() {
    if (this.points.length < 2) throw new ExprError("a profile needs at least two points");
    const pts = [this.points[0]], hard = [this.hard[0]];
    for (let i = 1; i < this.points.length; i++) {
      if (dist2(this.points[i], pts[pts.length - 1]) > 1e-9) { pts.push(this.points[i]); hard.push(this.hard[i]); }
      else if (this.hard[i]) hard[hard.length - 1] = true;
    }
    if (this.closed && dist2(pts[0], pts[pts.length - 1]) < 1e-9) { pts.pop(); hard.pop(); }
    if (pts.length < 2) throw new ExprError("a profile needs at least two points that aren't in the same place");
    if (pts.length > MAX_POINTS) throw new ExprError(`that profile has ${pts.length} points; keep it under ${MAX_POINTS}`);
    return pts;
  }
}

class Path {
  constructor() { this.points = []; }
  move(x, y, z) {
    if (this.points.length) throw new ExprError("'move' can only start a path");
    this.points.push([x, y, z]); return this;
  }
  line(x, y, z, steps = 1) {
    if (!this.points.length) throw new ExprError("a path needs a starting point: put a 'move' first");
    const [x0, y0, z0] = this.points[this.points.length - 1];
    steps = Math.max(1, Math.trunc(steps));
    for (let k = 1; k <= steps; k++) {
      const f = k / steps;
      this.points.push([x0 + (x - x0) * f, y0 + (y - y0) * f, z0 + (z - z0) * f]);
    }
    return this;
  }
  curve(xe, ye, ze, steps = 32, scope = null) {
    steps = Math.max(1, Math.trunc(steps));
    const t = linspace(0, 1, steps + 1);
    const names = Object.assign({}, scope || {}, { t });
    const cols = [xe, ye, ze].map((e) => broadcast(evaluate(e, names), t.length));
    for (let k = 0; k < t.length; k++)
      if (!isFinite(cols[0][k]) || !isFinite(cols[1][k]) || !isFinite(cols[2][k]))
        throw new ExprError("the path produced values that are not finite — check the maths");
    for (let k = this.points.length ? 1 : 0; k < t.length; k++) this.points.push([cols[0][k], cols[1][k], cols[2][k]]);
    return this;
  }
  helix(radius, pitch, turns, steps = null, start = 0) {
    if (Math.abs(turns) < 1e-9) throw new ExprError("a helix needs at least a fraction of a turn");
    steps = steps ? Math.trunc(steps) : Math.max(8, Math.trunc(Math.abs(turns) * 32));
    const t = linspace(0, turns, steps + 1);
    for (let k = this.points.length ? 1 : 0; k < t.length; k++) {
      const a = 2 * Math.PI * t[k] + start * Math.PI / 180;
      this.points.push([radius * Math.cos(a), radius * Math.sin(a), pitch * t[k]]);
    }
    return this;
  }
  finish() {
    if (this.points.length < 2) throw new ExprError("a path needs at least two points");
    const pts = [this.points[0]];
    for (let i = 1; i < this.points.length; i++) if (dist3(this.points[i], pts[pts.length - 1]) > 1e-9) pts.push(this.points[i]);
    if (pts.length < 2) throw new ExprError("a path needs at least two points that aren't in the same place");
    if (pts.length > MAX_POINTS) throw new ExprError(`that path has ${pts.length} points; keep it under ${MAX_POINTS}`);
    return pts;
  }
}

// A mesh: verts as flat xyz, quads (4 per face) and tris (3 per face), kept apart like the
// Python so the polygon order — quads, then caps — is the same.
class Solid {
  constructor(verts, quads, tris) {
    this.verts = f32(Float64Array.from(verts));
    this.quads = Int32Array.from(quads || []);
    this.tris = Int32Array.from(tris || []);
  }
  get empty() { return this.quads.length === 0 && this.tris.length === 0; }
  polygons() {
    const out = [];
    for (let i = 0; i < this.quads.length; i += 4) out.push([this.quads[i], this.quads[i + 1], this.quads[i + 2], this.quads[i + 3]]);
    for (let i = 0; i < this.tris.length; i += 3) out.push([this.tris[i], this.tris[i + 1], this.tris[i + 2]]);
    return out;
  }
}

function checkSize(rings, perRing) {
  if (rings * perRing > MAX_FACES)
    throw new ExprError(`that would make about ${rings * perRing} faces; lower the segments or steps (limit ${MAX_FACES})`);
}

function orient(verts, axis) {
  axis = String(axis).toLowerCase();
  if (axis === "z" || axis === "+z") return verts;
  const out = new Float64Array(verts.length);
  for (let i = 0; i < verts.length; i += 3) {
    const x = verts[i], y = verts[i + 1], z = verts[i + 2];
    if (axis === "y" || axis === "+y") { out[i] = x; out[i + 1] = -z; out[i + 2] = y; }
    else if (axis === "x" || axis === "+x") { out[i] = z; out[i + 1] = y; out[i + 2] = -x; }
    else throw new ExprError(`axis must be x, y or z (got '${axis}')`);
  }
  return out;
}

function stitch(rings, count, closeRings, closeProfile) {
  const ringPairs = closeRings ? rings : rings - 1, profPairs = closeProfile ? count : count - 1;
  const quads = [];
  for (let r = 0; r < ringPairs; r++) {
    const r1 = (r + 1) % rings;
    for (let p = 0; p < profPairs; p++) {
      const p1 = (p + 1) % count;
      quads.push(r * count + p, r1 * count + p, r1 * count + p1, r * count + p1);
    }
  }
  return quads;
}

function fan(solid, ring, wrap) {
  const centre = solid.verts.length / 3;
  let cx = 0, cy = 0, cz = 0;
  for (const v of ring) { cx += solid.verts[3 * v]; cy += solid.verts[3 * v + 1]; cz += solid.verts[3 * v + 2]; }
  const verts = new Float64Array(solid.verts.length + 3);
  verts.set(solid.verts);
  verts.set([Math.fround(cx / ring.length), Math.fround(cy / ring.length), Math.fround(cz / ring.length)], solid.verts.length);
  const tris = Array.from(solid.tris);
  const n = wrap ? ring.length : ring.length - 1;
  for (let i = 0; i < n; i++) tris.push(centre, ring[i], ring[(i + 1) % ring.length]);
  solid.verts = verts;
  solid.tris = Int32Array.from(tris);
}
const range = (n, offset = 0) => Array.from({ length: n }, (_, k) => k + offset);
function addFlatCaps(solid, rings, count) {
  fan(solid, range(count), false);
  fan(solid, range(count, (rings - 1) * count).reverse(), false);
}
function addFanCaps(solid, rings, count) {
  fan(solid, range(count), true);
  fan(solid, range(count, (rings - 1) * count).reverse(), true);
}

function isClosed(solid) {
  const counts = new Map();
  for (const f of solid.polygons())
    for (let i = 0; i < f.length; i++) {
      const a = f[i], b = f[(i + 1) % f.length], key = a < b ? a * 4294967296 + b : b * 4294967296 + a;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
  if (!counts.size) return false;
  for (const c of counts.values()) if (c !== 2) return false;
  return true;
}
function signedVolume(solid) {
  let total = 0;
  const v = solid.verts;
  for (const f of solid.polygons()) {
    const a = 3 * f[0];
    for (let i = 1; i < f.length - 1; i++) {
      const b = 3 * f[i], c = 3 * f[i + 1];
      const cx = v[b + 1] * v[c + 2] - v[b + 2] * v[c + 1];
      const cy = v[b + 2] * v[c] - v[b] * v[c + 2];
      const cz = v[b] * v[c + 1] - v[b + 1] * v[c];
      total += v[a] * cx + v[a + 1] * cy + v[a + 2] * cz;
    }
  }
  return total;
}
function reversed(flat, size) {
  const out = new Int32Array(flat.length);
  for (let i = 0; i < flat.length; i += size) for (let k = 0; k < size; k++) out[i + k] = flat[i + size - 1 - k];
  return out;
}
function orientOutward(solid) {
  if (!isClosed(solid) || signedVolume(solid) >= 0) return solid;
  return new Solid(solid.verts, reversed(solid.quads, 4), reversed(solid.tris, 3));
}

function weldAxis(solid, rings, count, onAxis) {
  const nv = solid.verts.length / 3;
  const remap = Int32Array.from(range(nv));
  onAxis.forEach((on, j) => { if (on) for (let r = 1; r < rings; r++) remap[r * count + j] = j; });
  const quads = [], tris = [];
  for (let i = 0; i < solid.quads.length; i += 4) {
    const idx = [0, 1, 2, 3].map((k) => remap[solid.quads[i + k]]);
    const collapsed = idx.filter((v, k) => v !== idx[(k + 3) % 4]);
    if (collapsed.length === 4) quads.push(collapsed);
    else if (collapsed.length === 3) tris.push(collapsed);
  }
  for (let i = 0; i < solid.tris.length; i += 3) {
    const idx = [0, 1, 2].map((k) => remap[solid.tris[i + k]]);
    if (new Set(idx).size === 3) tris.push(idx);
  }
  const used = Array.from(new Set([...quads.flat(), ...tris.flat()])).sort((a, b) => a - b);
  const compact = new Int32Array(nv).fill(-1);
  used.forEach((v, k) => { compact[v] = k; });
  const verts = new Float64Array(used.length * 3);
  used.forEach((v, k) => { verts[3 * k] = solid.verts[3 * v]; verts[3 * k + 1] = solid.verts[3 * v + 1]; verts[3 * k + 2] = solid.verts[3 * v + 2]; });
  return new Solid(verts, quads.flat().map((v) => compact[v]), tris.flat().map((v) => compact[v]));
}

function cumulative(points) {
  const along = [0];
  for (let i = 1; i < points.length; i++) along.push(along[i - 1] + Math.hypot(...points[i].map((c, k) => c - points[i - 1][k])));
  return along;
}

function revolve(profile, segments = 48, degrees = 360, axis = "z", cap = true) {
  const points = profile.finish();
  segments = Math.max(3, Math.trunc(segments));
  if (Math.abs(degrees) < 1e-6) throw new ExprError("revolve needs an angle to sweep through");
  const full = Math.abs(degrees) >= 359.999;
  if (points.some((p) => p[0] < -1e-9)) throw new ExprError("a revolved profile can't have negative x: x is the distance from the axis");
  const rings = full ? segments : segments + 1;
  checkSize(rings, points.length);
  const angles = linspace(0, degrees, rings, !full).map((a) => a * Math.PI / 180);
  const n = points.length, raw = new Float64Array(rings * n * 3);
  for (let r = 0; r < rings; r++) {
    const ca = Math.cos(angles[r]), sa = Math.sin(angles[r]);
    for (let j = 0; j < n; j++) {
      const o = 3 * (r * n + j);
      raw[o] = points[j][0] * ca; raw[o + 1] = points[j][0] * sa; raw[o + 2] = points[j][1];
    }
  }
  let solid = new Solid(orient(raw, axis), stitch(rings, n, full, profile.closed), []);
  const onAxis = points.map((p) => p[0] < 1e-9);
  if (onAxis.some(Boolean)) solid = weldAxis(solid, rings, n, onAxis);
  if (cap && !profile.closed && !full) addFlatCaps(solid, rings, n);
  return orientOutward(solid);
}

function extrude(profile, depth = 1, steps = 1, axis = "z", cap = true, taper = 1) {
  const points = profile.finish();
  steps = Math.max(1, Math.trunc(steps));
  const rings = steps + 1, n = points.length;
  checkSize(rings, n);
  const t = linspace(0, 1, rings), raw = new Float64Array(rings * n * 3);
  for (let r = 0; r < rings; r++) {
    const s = 1 + (taper - 1) * t[r];
    for (let j = 0; j < n; j++) {
      const o = 3 * (r * n + j);
      raw[o] = points[j][0] * s; raw[o + 1] = points[j][1] * s; raw[o + 2] = t[r] * depth;
    }
  }
  const solid = new Solid(orient(raw, axis), stitch(rings, n, false, profile.closed), []);
  if (cap && profile.closed) addFanCaps(solid, rings, n);
  return orientOutward(solid);
}

const sub3 = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const cross3 = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const scale3 = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
const norm3 = (a) => Math.hypot(a[0], a[1], a[2]);
function rotateAbout(v, axis, angle) {
  const c = Math.cos(angle), s = Math.sin(angle), cr = cross3(axis, v), d = dot3(axis, v) * (1 - c);
  return [v[0] * c + cr[0] * s + axis[0] * d, v[1] * c + cr[1] * s + axis[1] * d, v[2] * c + cr[2] * s + axis[2] * d];
}
// Parallel-transport frames; on a closed path the turn left over after going round is
// spread evenly so the ends meet (as solids.py does).
function frames(points, closed = false) {
  const n = points.length, tan = new Array(n);
  if (closed) {
    for (let i = 0; i < n; i++) tan[i] = sub3(points[(i + 1) % n], points[(i - 1 + n) % n]);
  } else {
    tan[0] = sub3(points[1], points[0]);
    tan[n - 1] = sub3(points[n - 1], points[n - 2]);
    for (let i = 1; i < n - 1; i++) tan[i] = sub3(points[i + 1], points[i - 1]);
  }
  for (let i = 0; i < n; i++) tan[i] = scale3(tan[i], 1 / Math.max(norm3(tan[i]), 1e-12));
  let seed = [0, 0, 1];
  if (Math.abs(dot3(seed, tan[0])) > 0.9) seed = [1, 0, 0];
  const normal = new Array(n), first = cross3(seed, tan[0]);
  normal[0] = scale3(first, 1 / Math.max(norm3(first), 1e-12));
  const carry = (prev, t0, t1) => {
    const cr = cross3(t0, t1), len = norm3(cr);
    let nrm = len < 1e-9 ? prev.slice() : rotateAbout(prev, scale3(cr, 1 / len), Math.atan2(len, dot3(t0, t1)));
    nrm = sub3(nrm, scale3(t1, dot3(nrm, t1)));
    return scale3(nrm, 1 / Math.max(norm3(nrm), 1e-12));
  };
  for (let i = 1; i < n; i++) normal[i] = carry(normal[i - 1], tan[i - 1], tan[i]);
  if (closed) {
    const back = carry(normal[n - 1], tan[n - 1], tan[0]);
    const phi = Math.atan2(dot3(cross3(back, normal[0]), tan[0]), dot3(back, normal[0]));
    for (let i = 1; i < n; i++) normal[i] = rotateAbout(normal[i], tan[i], phi * i / n);
  }
  return [tan, normal, tan.map((t, i) => cross3(t, normal[i]))];
}

function isClosedPath(spine) {
  if (spine.length < 4) return false;
  let size = 0;
  for (let k = 0; k < 3; k++) {
    let lo = Infinity, hi = -Infinity;
    for (const p of spine) { lo = Math.min(lo, p[k]); hi = Math.max(hi, p[k]); }
    size = Math.max(size, hi - lo);
  }
  return norm3(sub3(spine[spine.length - 1], spine[0])) <= 1e-6 * Math.max(size, 1e-3);
}

// A path that ends where it starts becomes a closed loop with no end caps.
function sweep(profile, path, twist = 0, scaleEnd = 1, cap = true) {
  const points = profile.finish();
  let spine = path.finish();
  const loop = isClosedPath(spine) && Math.abs(scaleEnd - 1) < 1e-9;
  if (loop) spine = spine.slice(0, -1);
  const rings = spine.length, n = points.length;
  checkSize(rings, n);
  const [, normal, binormal] = frames(spine, loop);
  const steps = loop ? Array.from({ length: rings }, (_, r) => r / rings) : linspace(0, 1, rings);
  const raw = new Float64Array(rings * n * 3);
  for (let r = 0; r < rings; r++) {
    const a = twist * Math.PI / 180 * steps[r], s = 1 + (scaleEnd - 1) * steps[r];
    const ca = Math.cos(a), sa = Math.sin(a);
    for (let j = 0; j < n; j++) {
      const px = points[j][0], py = points[j][1];
      const rx = (px * ca - py * sa) * s, ry = (px * sa + py * ca) * s;
      const o = 3 * (r * n + j);
      for (let k = 0; k < 3; k++) raw[o + k] = spine[r][k] + rx * normal[r][k] + ry * binormal[r][k];
    }
  }
  const solid = new Solid(raw, stitch(rings, n, loop, profile.closed), []);
  if (cap && profile.closed && !loop) addFanCaps(solid, rings, n);
  return orientOutward(solid);
}

function interp(x, xp, fp) {
  if (x <= xp[0]) return fp[0];
  if (x >= xp[xp.length - 1]) return fp[fp.length - 1];
  let lo = 0, hi = xp.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xp[mid] <= x) lo = mid; else hi = mid; }
  const span = xp[hi] - xp[lo];
  return span === 0 ? fp[hi] : fp[lo] + (fp[hi] - fp[lo]) * (x - xp[lo]) / span;
}
function resample(points, count, closed) {
  const loop = closed ? points.concat([points[0]]) : points;
  const along = cumulative(loop), total = along[along.length - 1];
  if (total < 1e-12) throw new ExprError("a profile in a loft has no length");
  const want = linspace(0, total, count, !closed), xs = loop.map((p) => p[0]), ys = loop.map((p) => p[1]);
  return Array.from(want, (w) => [interp(w, along, xs), interp(w, along, ys)]);
}
function loft(profiles, steps = 1, cap = true, axis = "z") {
  if (profiles.length < 2) throw new ExprError("a loft needs at least two profiles");
  const closed = profiles[0].closed;
  if (profiles.some((p) => p.closed !== closed)) throw new ExprError("every profile in a loft must be open, or every one closed");
  const shapes = profiles.map((p) => p.finish());
  const count = Math.max(...shapes.map((s) => s.length));
  const rows = shapes.map((s) => resample(s, count, closed));
  const heights = Array.from(linspace(0, 1, rows.length));
  steps = Math.max(1, Math.trunc(steps));
  const rings = [], levels = [];
  for (let i = 0; i < rows.length - 1; i++)
    for (let k = 0; k < (i < rows.length - 2 ? steps : steps + 1); k++) {
      const f = k / steps;
      rings.push(rows[i].map((p, j) => [p[0] * (1 - f) + rows[i + 1][j][0] * f, p[1] * (1 - f) + rows[i + 1][j][1] * f]));
      levels.push(heights[i] * (1 - f) + heights[i + 1] * f);
    }
  checkSize(rings.length, count);
  const raw = new Float64Array(rings.length * count * 3);
  rings.forEach((ring, r) => ring.forEach((p, j) => { const o = 3 * (r * count + j); raw[o] = p[0]; raw[o + 1] = p[1]; raw[o + 2] = levels[r]; }));
  const solid = new Solid(orient(raw, axis), stitch(rings.length, count, false, closed), []);
  if (cap && closed) addFanCaps(solid, rings.length, count);
  return orientOutward(solid);
}

function shell(profile, thickness) {
  const points = profile.finish();
  if (profile.closed) throw new ExprError("shell works on an open profile; a closed one is already a solid");
  if (Math.abs(thickness) < 1e-9) throw new ExprError("shell needs a thickness");
  const n = points.length, normals = points.map(() => [0, 0]);
  for (let i = 0; i < n - 1; i++) {
    let sx = points[i + 1][0] - points[i][0], sy = points[i + 1][1] - points[i][1];
    const len = Math.max(Math.hypot(sx, sy), 1e-12);
    sx /= len; sy /= len;
    normals[i][0] += -sy; normals[i][1] += sx;
    normals[i + 1][0] += -sy; normals[i + 1][1] += sx;
  }
  const outer = points.map((p, i) => {
    const len = Math.max(Math.hypot(normals[i][0], normals[i][1]), 1e-12);
    return [p[0] + normals[i][0] / len * thickness, p[1] + normals[i][1] / len * thickness];
  });
  const shelled = new Profile();
  shelled.points = points.map((p) => p.slice()).concat(outer.reverse());
  shelled.hard = shelled.points.map(() => false);
  shelled.hard[0] = shelled.hard[n - 1] = shelled.hard[n] = shelled.hard[shelled.hard.length - 1] = true;
  shelled.closed = true;
  return shelled;
}

function join(solids) {
  solids = solids.filter((s) => s && !s.empty);
  if (!solids.length) return new Solid([], [], []);
  const verts = [], quads = [], tris = [];
  let offset = 0;
  for (const s of solids) {
    for (const v of s.verts) verts.push(v);
    for (const q of s.quads) quads.push(q + offset);
    for (const t of s.tris) tris.push(t + offset);
    offset += s.verts.length / 3;
  }
  return new Solid(verts, quads, tris);
}

function transform(solid, move = [0, 0, 0], rotate = [0, 0, 0], scale = [1, 1, 1]) {
  const v = Float64Array.from(solid.verts);
  for (let i = 0; i < v.length; i += 3) for (let k = 0; k < 3; k++) v[i + k] *= scale[k];
  const angles = rotate.map((a) => number(a, null, "rotation") * Math.PI / 180);
  [[angles[0], 1, 2], [angles[1], 2, 0], [angles[2], 0, 1]].forEach(([angle, a, b]) => {
    if (Math.abs(angle) < 1e-12) return;
    const c = Math.cos(angle), s = Math.sin(angle);
    for (let i = 0; i < v.length; i += 3) { const x = v[i + a], y = v[i + b]; v[i + a] = x * c - y * s; v[i + b] = x * s + y * c; }
  });
  for (let i = 0; i < v.length; i += 3) for (let k = 0; k < 3; k++) v[i + k] += move[k];
  const flipped = scale[0] * scale[1] * scale[2] < 0;
  return new Solid(v, flipped ? reversed(solid.quads, 4) : solid.quads, flipped ? reversed(solid.tris, 3) : solid.tris);
}

function array(solid, count, move = [0, 0, 0], around = null) {
  count = Math.trunc(count);
  if (count < 1) throw new ExprError("an array needs a count of at least 1");
  const each = solid.quads.length / 4 + solid.tris.length / 3;
  if (count * Math.max(each, 1) > MAX_FACES) throw new ExprError(`${count} copies would be about ${count * each} faces`);
  const copies = [];
  for (let i = 0; i < count; i++) {
    if (around !== null && around !== undefined) {
      const a = String(around).toLowerCase(), turn = 360 * i / count;
      copies.push(transform(solid, [0, 0, 0], a === "z" ? [0, 0, turn] : a === "x" ? [turn, 0, 0] : [0, turn, 0]));
    } else {
      copies.push(transform(solid, move.map((m) => m * i), [0, 0, 0], [1, 1, 1]));
    }
  }
  return join(copies);
}

// ---- the language -------------------------------------------------------------------------

const SINGLE_WORD = new Set(["axis", "around", "clockwise", "cap"]);
const KEYWORDS = {
  move: [], line: [], arc: ["radius", "steps", "clockwise"], curve: ["x", "y", "z", "steps"],
  helix: ["radius", "pitch", "turns", "steps", "start"],
  revolve: ["segments", "degrees", "angle", "axis", "cap"], extrude: ["depth", "steps", "axis", "taper", "cap"],
  sweep: ["twist", "scale", "cap"], loft: ["steps", "axis", "cap"],
  translate: ["x", "y", "z"], rotate: ["x", "y", "z"], scale: ["x", "y", "z"],
  array: ["count", "around", "x", "y", "z"], shell: ["thickness"], smooth: [], close: [],
  bevel: ["width", "segments", "shape", "angle"],
};
const PART_MODES = ["add", "subtract", "intersect"];
const PROFILE_WORDS = ["move", "line", "arc", "curve", "close", "shell"];
const PATH_WORDS = ["move", "line", "curve", "helix"];
const SOLID_WORDS = ["revolve", "extrude", "sweep", "loft", "translate", "rotate", "scale", "array", "smooth", "bevel"];

const fail = (line, message) => { throw new ShapeError(`line ${line}: ${message}`); };
const commas = (text) => text.split(",").map((p) => p.trim()).filter(Boolean);
const yes = (v, dflt) => !["0", "no", "false"].includes(String(v === undefined ? dflt : v).toLowerCase());

function splitArgs(text, keywords = []) {
  text = text.trim();
  if (!text) return [[], {}];
  const wanted = new Set(keywords.map((k) => k.toLowerCase()));
  if (!wanted.size) return [commas(text), {}];
  const found = [];
  let depth = 0, skipTo = 0, ignoreAt = -1;
  const re = /[()]|[A-Za-z_]\w*/g;
  let m;
  while ((m = re.exec(text))) {
    const token = m[0], start = m.index, end = start + token.length;
    if (token === "(") { depth++; continue; }
    if (token === ")") { depth--; continue; }
    if (start === ignoreAt) { ignoreAt = -1; continue; }
    const before = text.slice(0, start).trimEnd();
    if (depth || start < skipTo || !wanted.has(token.toLowerCase()) || (before && "+-*/%,=(".includes(before[before.length - 1]))) continue;
    const word = token.toLowerCase();
    const equals = /^\s*=\s*/.exec(text.slice(end));
    const valueStart = end + (equals ? equals[0].length : 0);
    if (SINGLE_WORD.has(word)) {
      const single = /^\s*(\S+)/.exec(text.slice(valueStart));
      if (!single) continue;
      found.push([start, word, single[1].replace(/^,+|,+$/g, "")]);
      skipTo = valueStart + single[0].length;
    } else {
      found.push([start, word, valueStart]);
      ignoreAt = valueStart + /^\s*/.exec(text.slice(valueStart))[0].length;
    }
  }
  const named = {}, ends = found.slice(1).map((f) => f[0]).concat([text.length]);
  found.forEach(([start, word, value], k) => {
    named[word] = typeof value === "string" ? value : text.slice(value, ends[k]).trim().replace(/,+$/, "").trim();
  });
  const head = found.length ? text.slice(0, found[0][0]).trim().replace(/,+$/, "") : text;
  return [head ? commas(head) : [], named];
}

// Three numbers from `x, y, z` (or one positional number meaning all three, as in
// `scale 2`), or from named components (`rotate x 90`, `translate y 1 z 2`), where the
// ones left out keep the default (0 for moves and turns, 1 for scale).
function vector(positional, named, scope, dflt, line) {
  if (positional.length) {
    if (positional.length === 1) { const v = number(positional[0], scope, "value"); return [v, v, v]; }
    if (positional.length !== 3) fail(line, `expected three numbers, got ${positional.length}`);
    return positional.map((b) => number(b, scope, "value"));
  }
  const keys = ["x", "y", "z"];
  if (!keys.some((k) => named[k] !== undefined && named[k] !== null)) return dflt.slice();
  return keys.map((k, i) => (named[k] !== undefined && named[k] !== null ? number(named[k], scope, k) : dflt[i]));
}

function wrapLine(line, fn) {
  try { return fn(); } catch (e) {
    if (e instanceof ShapeError) throw e;
    if (e instanceof ExprError) fail(line, e.message);
    throw e;
  }
}

function buildProfile(ops, scope) {
  let p = new Profile();
  for (const [word, rest, line] of ops) {
    const [pos, named] = splitArgs(rest, KEYWORDS[word] || []);
    wrapLine(line, () => {
      if (word === "shell") {
        const value = pos.length ? pos[0] : named.thickness;
        if (value === undefined) fail(line, "shell needs a thickness, e.g. `shell 0.004`");
        p = shell(p, number(value, scope, "thickness"));
      } else if (word === "close") p.close();
      else if (word === "move" || word === "line") {
        if (pos.length !== 2) fail(line, `'${word}' needs two numbers: \`${word} x, y\``);
        const [x, y] = pos.map((v) => number(v, scope, word));
        word === "move" ? p.move(x, y) : p.line(x, y);
      } else if (word === "arc") {
        if (pos.length !== 2) fail(line, "'arc' needs `arc x, y radius r`");
        if (named.radius === undefined) fail(line, "'arc' needs a radius, e.g. `arc 0.1, 0.02 radius 0.014`");
        const [x, y] = pos.map((v) => number(v, scope, "arc"));
        p.arc(x, y, number(named.radius, scope, "radius"), integer(named.steps ?? 12, scope, "steps", 2, 4096),
              ["1", "true", "yes"].includes(String(named.clockwise ?? "").toLowerCase()));
      } else if (word === "curve") {
        if (named.x === undefined || named.y === undefined) fail(line, "'curve' needs x and y, e.g. `curve x = r*t  y = h*t  steps 24`");
        p.curve(named.x, named.y, integer(named.steps ?? 24, scope, "steps", 1, 4096), scope);
      }
    });
  }
  return p;
}

const PATH_KEYWORDS = Object.assign({}, KEYWORDS, { line: ["steps"] });   // a path's straight run can be divided

function buildPath(ops, scope) {
  const path = new Path();
  for (const [word, rest, line] of ops) {
    const [pos, named] = splitArgs(rest, PATH_KEYWORDS[word] || []);
    wrapLine(line, () => {
      if (word === "move" || word === "line") {
        if (pos.length !== 3) fail(line, `inside a path, '${word}' needs three numbers: \`${word} x, y, z\``);
        const [x, y, z] = pos.map((v) => number(v, scope, word));
        word === "move" ? path.move(x, y, z) : path.line(x, y, z, integer(named.steps ?? 1, scope, "steps", 1, 4096));
      } else if (word === "curve") {
        const missing = ["x", "y", "z"].filter((k) => named[k] === undefined);
        if (missing.length) fail(line, "a path curve needs x, y and z, e.g. `curve x = cos(t*tau)  y = sin(t*tau)  z = t  steps 64`");
        path.curve(named.x, named.y, named.z, integer(named.steps ?? 32, scope, "steps", 1, 8192), scope);
      } else if (word === "helix") {
        for (const need of ["radius", "pitch", "turns"])
          if (named[need] === undefined) fail(line, "helix needs radius, pitch and turns, e.g. `helix radius 0.02 pitch 0.006 turns 5`");
        path.helix(number(named.radius, scope, "radius"), number(named.pitch, scope, "pitch"), number(named.turns, scope, "turns"),
                   named.steps !== undefined ? integer(named.steps, scope, "steps", 2, 20000) : null,
                   number(named.start ?? 0, scope, "start"));
      }
    });
  }
  return path;
}

function buildPart(name, ops, scope) {
  let profiles = [], path = null, solid = null, bevel = false;
  for (const [word, rest, line] of ops) {
    if (word === "profile") { profiles.push(buildProfile(rest, scope)); continue; }
    if (word === "path") { path = buildPath(rest, scope); continue; }
    const profile = profiles.length ? profiles[profiles.length - 1] : null;
    const [pos, named] = splitArgs(rest, KEYWORDS[word] || []);
    wrapLine(line, () => {
      if (word === "revolve") {
        if (!profile) fail(line, "revolve needs a profile above it");
        solid = revolve(profile, integer(named.segments ?? 48, scope, "segments", 3, 4096),
                        number(named.degrees ?? named.angle ?? 360, scope, "degrees"), named.axis ?? "z", yes(named.cap, "yes"));
      } else if (word === "extrude") {
        if (!profile) fail(line, "extrude needs a profile above it");
        solid = extrude(profile, number(pos.length ? pos[0] : named.depth ?? 1, scope, "depth"),
                        integer(named.steps ?? 1, scope, "steps", 1, 4096), named.axis ?? "z", yes(named.cap, "yes"),
                        number(named.taper ?? 1, scope, "taper"));
        profiles = [];
      } else if (word === "sweep") {
        if (!profile) fail(line, "sweep needs a profile above it");
        if (!path) fail(line, "sweep needs a `path` block saying where to carry the profile");
        solid = sweep(profile, path, number(named.twist ?? 0, scope, "twist"), number(named.scale ?? 1, scope, "scale"), yes(named.cap, "yes"));
        profiles = []; path = null;
      } else if (word === "loft") {
        if (profiles.length < 2) fail(line, `loft needs at least two profiles above it (found ${profiles.length})`);
        solid = loft(profiles, integer(named.steps ?? 1, scope, "steps", 1, 512), yes(named.cap, "yes"), named.axis ?? "z");
        profiles = [];
      } else if (word === "bevel") {
        bevel = true;
      } else if (word === "translate" || word === "rotate" || word === "scale") {
        if (!solid) fail(line, `${word} needs something to act on`);
        const vec = vector(pos, named, scope, word === "scale" ? [1, 1, 1] : [0, 0, 0], line);
        solid = word === "translate" ? transform(solid, vec) : word === "rotate" ? transform(solid, [0, 0, 0], vec) : transform(solid, [0, 0, 0], [0, 0, 0], vec);
      } else if (word === "array") {
        if (!solid) fail(line, "array needs something to repeat");
        const count = integer(pos.length ? pos[0] : named.count ?? 2, scope, "count", 1, 4096);
        const around = named.around;
        let step = [0, 0, 0];
        if (around === undefined) {
          const only = {};
          for (const k of ["x", "y", "z"]) if (named[k] !== undefined) only[k] = named[k];
          step = vector([], only, scope, [0, 0, 0], line);
          if (step.every((v) => v === 0)) fail(line, "array needs a direction (x, y or z) or `around z`");
        }
        solid = array(solid, count, step, around === undefined ? null : around);
      }
    });
  }
  if (!solid) throw new ShapeError(`part '${name}' never becomes a solid: add a revolve, extrude, sweep or loft`);
  return { solid, bevel };
}

class Shape {
  constructor(params, parts, finish) { this.params = params; this.parts = parts; this.finish = finish; }
  values(overrides) {
    const scope = {};
    for (const p of this.params) scope[p.name] = p.default;
    for (const [k, v] of Object.entries(overrides || {})) if (k in scope) scope[k] = Number(v);
    return scope;
  }
  buildParts(overrides) {
    const scope = this.values(overrides);
    return this.parts.map(([name, mode, ops]) => Object.assign({ name, mode }, buildPart(name, ops, scope)));
  }
  build(overrides) { return join(this.buildParts(overrides).map((p) => p.solid)); }
}

function parse(source) {
  const params = [], parts = [], finish = [], seen = new Set();
  let current = null, block = null, inFinish = false;
  String(source).split(/\r?\n/).forEach((raw, k) => {
    const line = k + 1, text = raw.split("#")[0].trimEnd();
    if (!text.trim()) return;
    const bits0 = text.trim().split(/\s+/);
    const word = bits0[0].toLowerCase(), rest = text.trim().slice(bits0[0].length).trim();
    if (word === "finish") { inFinish = true; block = null; return; }
    if (inFinish) {
      if (word === "bevel" || word === "smooth") { finish.push([word, rest, line]); return; }
      fail(line, `after \`finish\` only bevel and smooth make sense, not '${word}'`);
    }
    if (word === "param") {
      const bits = rest.split(/\s+/).filter(Boolean);
      if (!bits.length) fail(line, "param needs a name and a default, e.g. `param height 0.3`");
      const name = bits[0];
      if (!/^[a-zA-Z_]\w*$/.test(name)) fail(line, `'${name}' is not a usable name`);
      if (seen.has(name)) fail(line, `'${name}' is declared twice`);
      if (["t", "pi", "tau", "e"].includes(name)) fail(line, `'${name}' is reserved`);
      seen.add(name);
      wrapLine(line, () => {
        const dflt = bits.length > 1 ? number(bits[1], null, name) : 1;
        const lo = bits.length > 3 ? number(bits[2], null, name) : Math.min(0, dflt);
        const hi = bits.length > 3 ? number(bits[3], null, name) : Math.max(1, dflt * 2 || 1);
        params.push({ name, default: dflt, min: lo, max: hi });
      });
      return;
    }
    if (word === "part") {
      const bits = rest.split(/\s+/).filter(Boolean);
      let mode = "add";
      if (bits.length && PART_MODES.includes(bits[bits.length - 1].toLowerCase())) mode = bits.pop().toLowerCase();
      current = [bits.join(" ") || `part${parts.length + 1}`, mode, []];
      parts.push(current);
      block = null;
      return;
    }
    if (!current) { current = ["shape", "add", []]; parts.push(current); }
    if (word === "profile" || word === "path") { block = [word, []]; current[2].push([word, block[1], line]); return; }
    if (PROFILE_WORDS.includes(word) || PATH_WORDS.includes(word)) {
      if (!block) fail(line, `'${word}' belongs inside a \`profile\` or \`path\` block`);
      const allowed = block[0] === "profile" ? PROFILE_WORDS : PATH_WORDS;
      if (!allowed.includes(word)) fail(line, `'${word}' can't be used inside a \`${block[0]}\` block`);
      block[1].push([word, rest, line]);
      return;
    }
    if (SOLID_WORDS.includes(word)) { current[2].push([word, rest, line]); block = null; return; }
    fail(line, `'${word}' is not a shape command. Try: param, part, finish, profile, path, ` +
      Array.from(new Set(PROFILE_WORDS.concat(PATH_WORDS, SOLID_WORDS))).sort().join(", "));
  });
  if (!parts.length) throw new ShapeError("nothing to build: describe a profile and a revolve or extrude");
  return new Shape(params, parts, finish);
}

const CodeShapes = { parse, evaluate, splitArgs, ExprError, ShapeError, Solid, join };
if (typeof module !== "undefined" && module.exports) module.exports = CodeShapes;
else root.CodeShapes = CodeShapes;
})(typeof self !== "undefined" ? self : this);
