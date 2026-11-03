import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { Match, PERIOD_NAME } from "./replay.js";
import { WinModel } from "./winprob.js";
import { Tracking } from "./tracking.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const id = params.get("id") || "3857280";
const state = { t: 0, playing: false, speed: 1, last: 0, cam: "broadcast", score: "", banner: 0, trail: [] };
let match, track, model = null;

// ---------- scene ----------
const canvas = $("scene");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1210);
scene.fog = new THREE.Fog(0x0b1210, 140, 260);
const camera = new THREE.PerspectiveCamera(32, 16 / 9, 0.5, 500);
const controls = new OrbitControls(camera, canvas);
controls.enabled = false;
controls.maxPolarAngle = Math.PI / 2.05;

scene.add(new THREE.HemisphereLight(0xdfefff, 0x1b3326, 1.1));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(-30, 70, 40);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -65, right: 65, top: 45, bottom: -45, near: 10, far: 200 });
scene.add(sun);

function pitchTexture(L, W) {
  const s = 20, pad = 5;                       // pixels per metre, metres of grass around the lines
  const c = document.createElement("canvas");
  c.width = (L + 2 * pad) * s; c.height = (W + 2 * pad) * s;
  const g = c.getContext("2d");
  for (let i = 0; i < 20; i++) { g.fillStyle = i % 2 ? "#2f7a43" : "#2a703d"; g.fillRect(i * c.width / 20, 0, c.width / 20 + 1, c.height); }
  g.strokeStyle = "rgba(255,255,255,0.9)"; g.lineWidth = 0.12 * s;
  const X = (x) => (x + L / 2 + pad) * s, Y = (y) => (y + W / 2 + pad) * s;
  const rect = (x0, y0, x1, y1) => g.strokeRect(X(x0), Y(y0), (x1 - x0) * s, (y1 - y0) * s);
  rect(-L / 2, -W / 2, L / 2, W / 2);
  g.beginPath(); g.moveTo(X(0), Y(-W / 2)); g.lineTo(X(0), Y(W / 2)); g.stroke();
  g.beginPath(); g.arc(X(0), Y(0), 9.15 * s, 0, Math.PI * 2); g.stroke();
  for (const side of [-1, 1]) {
    const gx = side * L / 2;
    rect(Math.min(gx, gx - side * 16.5), -20.16, Math.max(gx, gx - side * 16.5), 20.16);
    rect(Math.min(gx, gx - side * 5.5), -9.16, Math.max(gx, gx - side * 5.5), 9.16);
    g.beginPath(); g.arc(X(gx - side * 11), Y(0), 0.2 * s, 0, Math.PI * 2); g.fillStyle = "#fff"; g.fill();
    g.beginPath();
    const a = Math.acos(5.5 / 9.15);
    side > 0 ? g.arc(X(gx - 11), Y(0), 9.15 * s, Math.PI - a, Math.PI + a) : g.arc(X(gx + 11), Y(0), 9.15 * s, -a, a);
    g.stroke();
  }
  g.beginPath(); g.arc(X(0), Y(0), 0.2 * s, 0, Math.PI * 2); g.fill();
  const tex = new THREE.CanvasTexture(c);
  tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
  tex.colorSpace = THREE.SRGBColorSpace;
  return { tex, w: L + 2 * pad, h: W + 2 * pad };
}

function buildPitch(L, W) {
  const { tex, w, h } = pitchTexture(L, W);
  const grass = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshLambertMaterial({ map: tex }));
  grass.rotation.x = -Math.PI / 2;
  grass.receiveShadow = true;
  scene.add(grass);
  const surround = new THREE.Mesh(new THREE.PlaneGeometry(w + 60, h + 50), new THREE.MeshLambertMaterial({ color: 0x173826 }));
  surround.rotation.x = -Math.PI / 2; surround.position.y = -0.02;
  scene.add(surround);
  const post = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.4 });
  const net = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.18, side: THREE.DoubleSide, wireframe: true });
  for (const side of [-1, 1]) {
    const gx = side * L / 2, goal = new THREE.Group();
    const bar = (len, rot) => { const m = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, len, 10), post); m.castShadow = true; Object.assign(m.rotation, rot); return m; };
    const p1 = bar(2.44, {}); p1.position.set(gx, 1.22, -3.66);
    const p2 = bar(2.44, {}); p2.position.set(gx, 1.22, 3.66);
    const cross = bar(7.32, { x: Math.PI / 2 }); cross.position.set(gx, 2.44, 0);
    const back = new THREE.Mesh(new THREE.PlaneGeometry(7.32, 2.44, 14, 5), net);
    back.rotation.y = Math.PI / 2; back.position.set(gx + side * 1.8, 1.22, 0);
    const top = new THREE.Mesh(new THREE.PlaneGeometry(1.8, 7.32, 4, 14), net);
    top.rotation.x = -Math.PI / 2; top.position.set(gx + side * 0.9, 2.44, 0);
    goal.add(p1, p2, cross, back, top);
    scene.add(goal);
  }
}

function stands(L, W) {
  // Low, dark terraces around the pitch, so the horizon isn't empty
  const mat = new THREE.MeshLambertMaterial({ color: 0x18241f });
  const tier = (w, d, x, z, ry) => {
    for (let k = 0; k < 3; k++) {
      const m = new THREE.Mesh(new THREE.BoxGeometry(w, 2.5 + k * 2.5, 6), mat);
      m.position.set(x, (2.5 + k * 2.5) / 2, z);
      m.rotation.y = ry;
      m.translateZ(-(d + k * 6));
      scene.add(m);
    }
  };
  tier(L + 30, W / 2 + 12, 0, 0, 0);
  tier(L + 30, W / 2 + 12, 0, 0, Math.PI);
  tier(W + 30, L / 2 + 14, 0, 0, Math.PI / 2);
  tier(W + 30, L / 2 + 14, 0, 0, -Math.PI / 2);
}

function label(text, color = "#ffffff", bg = "rgba(11,18,16,0.75)") {
  const c = document.createElement("canvas"), g = c.getContext("2d");
  g.font = "600 44px Inter, system-ui, sans-serif";
  const w = Math.ceil(g.measureText(text).width) + 28;
  c.width = w; c.height = 64;
  g.font = "600 44px Inter, system-ui, sans-serif";
  g.fillStyle = bg; g.beginPath(); g.roundRect(0, 4, w, 56, 14); g.fill();
  g.fillStyle = color; g.textBaseline = "middle"; g.fillText(text, 14, 34);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
  sprite.scale.set(w / 64 * 0.9, 0.9, 1);
  sprite.renderOrder = 10;
  return sprite;
}

function player(kit, shirt, name) {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.32, 1.05, 6, 14),
    new THREE.MeshStandardMaterial({ color: kit.primary || "#cccccc", roughness: 0.6 }));
  body.position.y = 0.85; body.castShadow = true;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.17, 16, 12), new THREE.MeshStandardMaterial({ color: 0x5a3a28, roughness: 0.8 }));
  head.position.y = 1.72; head.castShadow = true;
  const num = label(shirt, kit.text || "#000", kit.primary || "#ccc");
  num.position.y = 2.45;
  const tag = label(name || shirt);
  tag.position.y = 3.3; tag.visible = false;
  g.add(body, head, num, tag);
  g.userData = { tag };
  g.visible = false;
  scene.add(g);
  return g;
}

const ball = new THREE.Mesh(new THREE.SphereGeometry(0.22, 20, 14), new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.3, emissive: 0x333333 }));
ball.castShadow = true;
scene.add(ball);
const trailGeo = new THREE.BufferGeometry();
const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({ color: 0xfff3b0, transparent: true, opacity: 0.6 }));
scene.add(trail);

let homeMeshes = [], awayMeshes = [];

// ---------- loading ----------
async function load() {
  try { const r = await fetch("data/winprob.json"); if (r.ok) model = new WinModel(await r.json()); } catch { /* optional */ }
  const res = await fetch(`data/matches/${id}.json`);
  if (!res.ok) throw new Error(`no match ${id}`);
  match = new Match(await res.json(), model);
  track = await Tracking.load(`tracking/${id}`);
  if (!track) throw new Error("this match has no tracking data (only the World Cup 2022 does)");
  const m = match.meta, tm = track.meta;
  document.title = `${m.home} v ${m.away} in 3D · MatchReplay`;
  $("home").textContent = m.home; $("away").textContent = m.away;
  $("sw-home").style.background = tm.home_kit.primary; $("sw-away").style.background = tm.away_kit.primary;
  $("subtitle").textContent = [m.competition_name, m.stage, m.date, m.stadium].filter(Boolean).join(" · ");
  $("to2d").href = `match.html?id=${id}`;
  buildPitch(tm.pitch.length, tm.pitch.width);
  stands(tm.pitch.length, tm.pitch.width);
  homeMeshes = tm.home_shirts.map((s) => player(tm.home_kit, s, tm.home_names[s]));
  awayMeshes = tm.away_shirts.map((s) => player(tm.away_kit, s, tm.away_names[s]));
  const start = Number(params.get("t"));
  state.t = Number.isFinite(start) && start > 0 ? match.fromPlayed(start * 60000) : Math.max(match.start, tm.chunks[0].start);
  track.prefetch(state.t, 3);
  wire();
  resize();
  requestAnimationFrame(frame);
}

function wire() {
  $("play").onclick = () => setPlaying(!state.playing);
  for (const b of $("speeds").querySelectorAll("button")) b.onclick = () => {
    state.speed = Number(b.dataset.speed);
    for (const o of $("speeds").querySelectorAll("button")) o.setAttribute("aria-pressed", o === b ? "true" : "false");
  };
  for (const b of $("cams").querySelectorAll("button")) b.onclick = () => {
    state.cam = b.dataset.cam;
    controls.enabled = state.cam === "free";
    for (const o of $("cams").querySelectorAll("button")) o.setAttribute("aria-pressed", o === b ? "true" : "false");
  };
  $("next-goal").onclick = () => {
    const g = match.goals().find((h) => h.t > state.t + 500);
    if (g) { seek(g.t - 15000); setPlaying(true); } else flash("No more goals in this match");
  };
  $("show-names").onchange = () => { for (const p of [...homeMeshes, ...awayMeshes]) p.userData.tag.visible = $("show-names").checked; };
  addEventListener("keydown", (e) => {
    if (e.code === "Space" && e.target === document.body) { e.preventDefault(); setPlaying(!state.playing); }
    if (e.code === "ArrowRight") seek(match.fromPlayed(match.toPlayed(state.t) + 10000));
    if (e.code === "ArrowLeft") seek(match.fromPlayed(match.toPlayed(state.t) - 10000));
  });
  const tl = $("timeline");
  const scrub = (e) => { const r = tl.getBoundingClientRect(); seek(match.fromPlayed(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * match.duration)); };
  tl.onpointerdown = (e) => { tl.setPointerCapture(e.pointerId); scrub(e); tl.onpointermove = scrub; };
  tl.onpointerup = () => { tl.onpointermove = null; };
  addEventListener("resize", resize);
}

function resize() {
  const w = canvas.clientWidth, h = Math.round(w * 9 / 16);
  canvas.style.height = `${h}px`;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}

function setPlaying(on) { state.playing = on; $("play").textContent = on ? "❚❚ Pause" : "▶ Play"; }
function seek(t) { state.t = t; state.trail = []; track.prefetch(t, 3); }

function flash(text, kind = "") {
  const b = $("banner");
  b.textContent = text; b.className = `banner show ${kind}`;
  clearTimeout(state.banner);
  state.banner = setTimeout(() => (b.className = "banner"), 3500);
}

// ---------- every frame ----------
const camTarget = new THREE.Vector3(), camPos = new THREE.Vector3();

function frame(now) {
  const dt = state.last ? Math.min(100, now - state.last) : 0;
  state.last = now;
  const f = track.frame(state.t);
  $("loading").style.display = f === null ? "grid" : "none";
  if (state.playing && f !== null) {
    const before = state.t;
    const step = match.advance(state.t, dt * state.speed);
    state.t = step.t;
    if (step.jumped) { flash(PERIOD_NAME[step.jumped.period]); state.trail = []; }
    if (step.finished) { setPlaying(false); flash("Full time"); }
    for (const h of match.highlights) if (h.t > before && h.t <= state.t && (h.kind === "goal" || h.kind === "owngoal")) flash(`⚽ ${h.text.replace("GOAL! ", "")}`, "goal");
    track.prefetch(state.t);
  }
  if (f && !f.gap) place(f);
  for (const p of [...homeMeshes, ...awayMeshes]) if (f && f.gap) p.visible = false;
  moveCamera(f && !f.gap ? f : null, dt);
  hud();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

// Data: x along the pitch (home attacks +x), y across. Scene: x, z = -y, up is y.
function place(f) {
  const set = (meshes, pts) => meshes.forEach((m, j) => {
    const p = pts[j];
    m.visible = !!p;
    if (p) {
      const nx = p[0], nz = -p[1];
      const dx = nx - m.position.x, dz = nz - m.position.z;
      if (dx * dx + dz * dz > 0.0004) m.rotation.y = Math.atan2(dx, dz);
      m.position.set(nx, 0, nz);
    }
  });
  set(homeMeshes, f.home);
  set(awayMeshes, f.away);
  if (f.ball) {
    ball.visible = true;
    ball.position.set(f.ball[0], Math.max(0.22, f.ball[2] || 0), -f.ball[1]);
    if ($("show-trails").checked) {
      state.trail.push(ball.position.clone());
      if (state.trail.length > 45) state.trail.shift();
      trailGeo.setFromPoints(state.trail);
      trail.visible = true;
    } else trail.visible = false;
  } else { ball.visible = false; }
}

function moveCamera(f, dt) {
  if (state.cam === "free") { controls.update(); return; }
  const b = f && f.ball ? ball.position : camTarget;
  const L = track.meta.pitch.length;
  if (state.cam === "broadcast") {
    camTarget.lerp(new THREE.Vector3(Math.max(-L / 2 + 14, Math.min(L / 2 - 14, b.x)), 0, b.z * 0.35), 1 - Math.exp(-dt / 400));
    camPos.set(camTarget.x * 0.8, 40, 64);
  } else if (state.cam === "tactical") {
    camTarget.set(0, 0, 4);
    camPos.set(0, 112, 52);
  } else {
    camTarget.lerp(b, 1 - Math.exp(-dt / 250));
    camPos.set(camTarget.x - 16, 9, camTarget.z + 14);
  }
  camera.position.lerp(camPos, 1 - Math.exp(-dt / 300));
  camera.lookAt(camTarget);
  controls.target.copy(camTarget);
}

const pct = (v) => `${Math.round(v * 100)}%`;

function hud() {
  const t = state.t, snap = match.snapshot(t), seg = match.segmentAt(t);
  $("clock").textContent = match.clock(t);
  $("period").textContent = PERIOD_NAME[seg.period];
  const score = snap ? `${snap.score[0]} - ${snap.score[1]}` : "0 - 0";
  if (score !== state.score) { $("score").textContent = score; state.score = score; }
  $("pens").textContent = snap && seg.period === 5 ? `Penalties ${snap.shootout[0]} - ${snap.shootout[1]}` : "";
  const p = model ? match.winProb(t) : null;
  $("winbar").style.visibility = p ? "visible" : "hidden";
  if (p) {
    $("wb-home").style.flexBasis = pct(p[0]); $("wb-draw").style.flexBasis = pct(p[1]); $("wb-away").style.flexBasis = pct(p[2]);
    $("wl-home").textContent = `${match.meta.home} ${pct(p[0])}`;
    $("wl-draw").textContent = `${seg.period >= 3 ? "Penalties" : "Draw"} ${pct(p[1])}`;
    $("wl-away").textContent = `${pct(p[2])} ${match.meta.away}`;
  }
  timeline(t);
}

function timeline(t) {
  const cv = $("timeline-canvas"), dpr = Math.min(devicePixelRatio || 1, 2);
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const c = cv.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.fillStyle = "#111b18"; c.fillRect(0, 0, w, h);
  const X = (time) => (match.toPlayed(time) / match.duration) * w;
  c.strokeStyle = "#22332d";
  for (const s of match.segments.slice(1)) { c.beginPath(); c.moveTo(X(s.start), 0); c.lineTo(X(s.start), h); c.stroke(); }
  const wp = model ? match.winProbs : null;
  if (wp) {
    c.beginPath();
    match.snaps.time.forEach((st, i) => { const x = X(st), y = h - wp[i][0] * h; i ? c.lineTo(x, y) : c.moveTo(x, y); });
    c.strokeStyle = "rgba(242,245,243,0.7)"; c.lineWidth = 1.5; c.stroke();
  }
  for (const g of match.goals()) { c.fillStyle = "#ff5a4e"; c.beginPath(); c.arc(X(g.t), g.side === 0 ? 8 : h - 8, 4.5, 0, Math.PI * 2); c.fill(); }
  const x = X(t);
  c.fillStyle = "rgba(255,255,255,0.05)"; c.fillRect(0, 0, x, h);
  c.fillStyle = "#3ddc84"; c.fillRect(x - 1, 0, 2, h);
}

load().catch((e) => {
  $("loading").textContent = `Could not load: ${e.message}.`;
  $("loading").style.display = "grid";
});
