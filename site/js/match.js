import { Match, PERIOD_NAME } from "./replay.js";
import { Pitch, css } from "./pitch.js";
import { LiveFeed } from "./live.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const id = params.get("id") || "3922238";
const liveUrl = params.get("live");          // e.g. ws://localhost:8765: follow the pipeline live
let feedSource = null;

const state = { t: 0, playing: false, speed: 20, lastFrame: 0, feedCount: -1, score: "", banner: 0 };
let match, pitch;

async function load() {
  if (liveUrl) {
    feedSource = new LiveFeed(liveUrl, id);
    $("subtitle").textContent = `Connecting to ${liveUrl}…`;
    while (!feedSource.ready) {
      if (feedSource.status === "error" || feedSource.status === "closed") throw new Error(`the gateway at ${liveUrl} is not reachable`);
      await new Promise((r) => setTimeout(r, 200));
    }
    match = feedSource.build();
    state.speed = 60;
  } else {
    const res = await fetch(`data/matches/${id}.json`);
    if (!res.ok) throw new Error(`No match ${id}`);
    match = new Match(await res.json());
  }
  const m = match.meta;
  document.title = `${m.home} v ${m.away} · MatchReplay`;
  $("home").textContent = m.home;
  $("away").textContent = m.away;
  $("subtitle").textContent = [m.competition_name, m.stage, m.date, m.stadium].filter(Boolean).join(" · ");
  pitch = new Pitch($("pitch"));
  const start = Number(params.get("t"));
  state.t = Number.isFinite(start) && start > 0 ? match.fromPlayed(start * 60000) : match.start;
  wire();
  if (feedSource) {
    for (const o of $("speeds").querySelectorAll("button")) o.setAttribute("aria-pressed", o.dataset.speed === "60" ? "true" : "false");
    setPlaying(true);
  }
  requestAnimationFrame(frame);
}

function wire() {
  $("play").onclick = () => setPlaying(!state.playing);
  for (const b of $("speeds").querySelectorAll("button")) {
    b.onclick = () => {
      state.speed = Number(b.dataset.speed);
      for (const o of $("speeds").querySelectorAll("button")) o.setAttribute("aria-pressed", o === b ? "true" : "false");
    };
  }
  $("next-goal").onclick = () => {
    const g = match.goals().find((h) => h.t > state.t + 500);
    if (g) { seek(Math.max(match.segmentAt(g.t).start, g.t - 12000)); setPlaying(true); }
    else flash("No more goals in this match");
  };
  addEventListener("keydown", (e) => {
    if (e.code === "Space" && e.target === document.body) { e.preventDefault(); setPlaying(!state.playing); }
    if (e.code === "ArrowRight") seek(match.fromPlayed(match.toPlayed(state.t) + 30000));
    if (e.code === "ArrowLeft") seek(match.fromPlayed(match.toPlayed(state.t) - 30000));
  });
  const tl = $("timeline");
  const scrub = (e) => {
    const r = tl.getBoundingClientRect();
    seek(match.fromPlayed(Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * match.duration));
  };
  tl.onpointerdown = (e) => { tl.setPointerCapture(e.pointerId); scrub(e); tl.onpointermove = scrub; };
  tl.onpointerup = () => { tl.onpointermove = null; };
}

function setPlaying(on) {
  if (on && state.t >= match.end) state.t = match.start;
  state.playing = on;
  $("play").textContent = on ? "❚❚ Pause" : "▶ Play";
}

function seek(t) {
  state.t = t;
  state.feedCount = -1;
}

function flash(text, kind = "") {
  const b = $("banner");
  b.textContent = text;
  b.className = `banner show ${kind}`;
  clearTimeout(state.banner);
  state.banner = setTimeout(() => (b.className = "banner"), kind === "goal" ? 3500 : 2200);
}

function frame(now) {
  const dt = state.lastFrame ? Math.min(100, now - state.lastFrame) : 0;
  state.lastFrame = now;
  if (feedSource) {
    if (feedSource.dirty) { match = feedSource.build(); state.feedCount = -1; }
    $("subtitle").textContent = `Live from the pipeline · ${feedSource.status} · ${feedSource.events.length} events, ${feedSource.snaps.length} snapshots`;
  }
  if (state.playing) {
    const step = match.advance(state.t, dt * state.speed);
    if (feedSource) {                    // live: never run ahead of what the feed has delivered
      const edge = feedSource.edge - 1500;
      if (step.t > edge) { step.t = Math.max(state.t, edge); step.finished = false; }
    }
    const before = state.t;
    state.t = step.t;
    if (step.jumped) flash(`${PERIOD_NAME[step.jumped.period]}`);
    if (step.finished) { setPlaying(false); flash("Full time"); }
    announce(before, state.t);
  }
  draw();
  requestAnimationFrame(frame);
}

/** Banners for goals and red cards that happened between two frames. */
function announce(from, to) {
  for (const h of match.highlights) {
    if (h.t > from && h.t <= to) {
      if (h.kind === "goal" || h.kind === "owngoal") flash(`⚽ ${h.text.replace("GOAL! ", "")}`, "goal");
      else if (h.kind === "red") flash(`🟥 ${h.text}`);
      else if (h.kind === "pen" || h.kind === "penmiss") flash(h.text);
    }
  }
}

function draw() {
  const t = state.t;
  pitch.drawField();
  if ($("show-shots").checked) pitch.drawShots(match.shotsUntil(t), t);
  const recent = match.recent(t);
  if ($("show-trails").checked) pitch.drawTrails(recent);
  pitch.drawActor(recent[recent.length - 1]);
  pitch.drawBall(match.ball(t));

  const snap = match.snapshot(t);
  const seg = match.segmentAt(t);
  $("clock").textContent = match.clock(t);
  $("period").textContent = PERIOD_NAME[seg.period];
  const score = snap ? `${snap.score[0]} - ${snap.score[1]}` : "0 - 0";
  if (score !== state.score) {
    $("score").textContent = score;
    if (state.score) { $("score").classList.remove("flash"); void $("score").offsetWidth; $("score").classList.add("flash"); }
    state.score = score;
  }
  $("pens").textContent = snap && seg.period === 5 ? `Penalties ${snap.shootout[0]} - ${snap.shootout[1]}` : "";
  stats(snap);
  feed(t);
  xgChart(t);
  timeline(t);
}

function stats(s) {
  const z = (k) => (s ? [s["h_" + k], s["a_" + k]] : [0, 0]);
  const row = (label, [h, a], fmt = (v) => v) => {
    const share = h + a > 0 ? h / (h + a) : 0.5;
    return `<div class="stat"><span class="v">${fmt(h)}</span><span class="bar" style="--h:${share}">${label}</span><span class="v a">${fmt(a)}</span></div>`;
  };
  const poss = s ? s.possession_home : 0.5;
  const html = [
    row("Expected goals", z("xg"), (v) => v.toFixed(2)),
    row("Shots", z("shots")),
    row("On target", z("on_target")),
    `<div class="stat"><span class="v">${Math.round(poss * 100)}%</span><span class="bar" style="--h:${poss}">Share of passes</span><span class="v a">${Math.round((1 - poss) * 100)}%</span></div>`,
    row("Passes completed", z("passes_completed")),
    row("Box entries", z("box_entries")),
    row("Fouls", z("fouls")),
    row("Cards", s ? [s.h_yellow + s.h_red, s.a_yellow + s.a_red] : [0, 0]),
  ].join("");
  if ($("stats").innerHTML !== html) $("stats").innerHTML = html;
}

function feed(t) {
  const shown = match.highlights.filter((h) => h.t <= t);
  if (shown.length === state.feedCount) return;
  state.feedCount = shown.length;
  const big = new Set(["goal", "owngoal", "red", "pen", "penmiss"]);
  $("feed").innerHTML = shown.slice(-40).reverse().map((h) => {
    const minute = h.period === 5 ? "PEN" : `${h.minute + 1}'`;
    return `<div><span class="m">${minute}</span><span class="${big.has(h.kind) ? "big " + h.kind : ""}">${h.text}</span></div>`;
  }).join("") || `<div><span class="m"></span><span>Kick-off is coming up. Press play.</span></div>`;
}

function setup(canvas) {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== Math.round(w * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); }
  const c = canvas.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);
  return { c, w, h };
}

function xgChart(t) {
  const { c, w, h } = setup($("xg"));
  const S = match.snaps, end = match.duration;
  if (!S.h_xg || !S.h_xg.length) return;
  const max = Math.max(0.5, S.h_xg[S.h_xg.length - 1], S.a_xg[S.a_xg.length - 1]) * 1.1;
  for (const [key, color] of [["h_xg", css("--home")], ["a_xg", css("--away")]]) {
    c.beginPath();
    c.strokeStyle = color; c.lineWidth = 2;
    let last = 0;
    for (let i = 0; i < S.time.length && S.time[i] <= t; i++) {
      const x = (match.toPlayed(S.time[i]) / end) * w, y = h - (S[key][i] / max) * (h - 4);
      i === 0 ? c.moveTo(x, h) : c.lineTo(x, h - (last / max) * (h - 4));
      c.lineTo(x, y);
      last = S[key][i];
    }
    c.stroke();
  }
  const s = match.snapshot(t);
  $("xg-home").textContent = `${match.meta.home} ${s ? s.h_xg.toFixed(2) : "0.00"}`;
  $("xg-away").textContent = `${s ? s.a_xg.toFixed(2) : "0.00"} ${match.meta.away}`;
}

function timeline(t) {
  const { c, w, h } = setup($("timeline-canvas"));
  const end = match.duration, mid = h / 2;
  const X = (time) => (match.toPlayed(time) / end) * w;
  c.fillStyle = css("--panel"); c.fillRect(0, 0, w, h);
  // period boundaries
  c.strokeStyle = css("--line");
  for (const s of match.segments.slice(1)) { c.beginPath(); c.moveTo(X(s.start), 0); c.lineTo(X(s.start), h); c.stroke(); }
  // momentum: home above, away below
  const S = match.snaps;
  if (!S.momentum) S.momentum = [];
  const peak = Math.max(0.3, ...S.momentum.map(Math.abs));
  for (const [sign, color] of [[1, css("--home")], [-1, css("--away")]]) {
    c.beginPath(); c.moveTo(0, mid);
    for (let i = 0; i < S.time.length; i++) {
      const v = Math.max(0, sign * S.momentum[i]) / peak;
      c.lineTo(X(S.time[i]), mid - sign * v * (mid - 6));
    }
    c.lineTo(w, mid); c.closePath();
    c.globalAlpha = 0.35; c.fillStyle = color; c.fill(); c.globalAlpha = 1;
  }
  c.strokeStyle = css("--line"); c.beginPath(); c.moveTo(0, mid); c.lineTo(w, mid); c.stroke();
  // goals
  for (const g of match.goals()) {
    const x = X(g.t);
    c.fillStyle = css("--goal");
    c.beginPath(); c.arc(x, g.side === 0 ? 8 : h - 8, 4.5, 0, Math.PI * 2); c.fill();
  }
  // played part and playhead
  const x = X(t);
  c.fillStyle = "rgba(255,255,255,0.05)"; c.fillRect(0, 0, x, h);
  c.fillStyle = css("--accent"); c.fillRect(x - 1, 0, 2, h);
}

load().catch((e) => {
  document.querySelector(".stage").innerHTML = `<div class="panel">Could not load this match: ${e.message}. <a href="./">Pick another one</a>.</div>`;
});
