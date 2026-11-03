import { Match, PERIOD_NAME } from "./replay.js";
import { Pitch, css } from "./pitch.js";
import { LiveFeed } from "./live.js";
import { WinModel, whatIf } from "./winprob.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const id = params.get("id") || "3922238";
const liveUrl = params.get("live");          // e.g. ws://localhost:8765: follow the pipeline live
let feedSource = null;

const state = { t: 0, playing: false, speed: 20, lastFrame: 0, feedCount: -1, score: "", banner: 0 };
let match, pitch, model = null;

async function load() {
  try {
    const r = await fetch("data/winprob.json");
    if (r.ok) model = new WinModel(await r.json());
  } catch { /* no model: the page still replays, without win probability */ }
  if (liveUrl) {
    feedSource = new LiveFeed(liveUrl, id);
    $("subtitle").textContent = `Connecting to ${liveUrl}…`;
    while (!feedSource.ready) {
      if (feedSource.status === "error" || feedSource.status === "closed") throw new Error(`the gateway at ${liveUrl} is not reachable`);
      await new Promise((r) => setTimeout(r, 200));
    }
    match = feedSource.build(model);
    state.speed = 60;
  } else {
    const res = await fetch(`data/matches/${id}.json`);
    if (!res.ok) throw new Error(`No match ${id}`);
    match = new Match(await res.json(), model);
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
  $("feed").onclick = (e) => {
    const b = e.target.closest("button.wi");
    if (b) showWhatIf(match.highlights[Number(b.dataset.h)]);
  };
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
    if (feedSource.dirty) { match = feedSource.build(model); state.feedCount = -1; }
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
  winbar(t, seg.period);
  wpChart(t);
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
  const flippable = new Set(["goal", "owngoal", "shot", "red"]);
  $("feed").innerHTML = shown.slice(-40).reverse().map((h) => {
    const minute = h.period === 5 ? "PEN" : `${h.minute + 1}'`;
    const wi = model && flippable.has(h.kind) && h.period !== 5
      ? `<button class="wi" data-h="${match.highlights.indexOf(h)}" title="Replay the rest of the match as if this had gone the other way">What if?</button>` : "";
    return `<div><span class="m">${minute}</span><span class="${big.has(h.kind) ? "big " + h.kind : ""}">${h.text}${wi}</span></div>`;
  }).join("") || `<div><span class="m"></span><span>Kick-off is coming up. Press play.</span></div>`;
}

const pct = (v) => `${Math.round(v * 100)}%`;

function winbar(t, period) {
  const p = model ? match.winProb(t) : null;
  $("winbar").style.visibility = p ? "visible" : "hidden";
  if (!p) {
    $("wl-home").textContent = ""; $("wl-away").textContent = "";
    $("wl-draw").textContent = period === 5 ? "Penalty shoot-out" : "";
    return;
  }
  $("wb-home").style.flexBasis = pct(p[0]);
  $("wb-draw").style.flexBasis = pct(p[1]);
  $("wb-away").style.flexBasis = pct(p[2]);
  $("wl-home").textContent = `${match.meta.home} ${pct(p[0])}`;
  $("wl-draw").textContent = `${period >= 3 ? "Penalties" : "Draw"} ${pct(p[1])}`;
  $("wl-away").textContent = `${pct(p[2])} ${match.meta.away}`;
}

function wpChart(t) {
  const { c, w, h } = setup($("wp"));
  if (!model) return;
  const S = match.snaps, P = match.winProbs, end = match.duration;
  const X = (time) => (match.toPlayed(time) / end) * w;
  c.strokeStyle = css("--line"); c.beginPath(); c.moveTo(0, h / 2); c.lineTo(w, h / 2); c.stroke();
  // home win probability (up) and away win probability (down from the top)
  const line = (times, probs, k, color, dash, until = t) => {
    c.beginPath(); c.strokeStyle = color; c.lineWidth = 2; c.setLineDash(dash);
    let started = false;
    for (let i = 0; i < times.length && times[i] <= until; i++) {
      if (!probs[i]) { started = false; continue; }
      const x = X(times[i]), y = h - probs[i][k] * (h - 4) - 2;
      started ? c.lineTo(x, y) : c.moveTo(x, y);
      started = true;
    }
    c.stroke(); c.setLineDash([]);
  };
  line(S.time, P, 0, css("--home"), []);
  line(S.time, P, 2, css("--away"), []);
  if (state.whatif) {
    const a = state.whatif.alt;
    line(a.time, a.prob, 0, css("--home"), [4, 3], Infinity);
    line(a.time, a.prob, 2, css("--away"), [4, 3], Infinity);
  }
  $("wp-legend").innerHTML = `<span>${match.meta.home} win</span><span>${state.whatif ? "dashed: what if" : ""}</span><span>${match.meta.away} win</span>`;
}

function showWhatIf(h) {
  const w = whatIf(match, model, h);
  if (!w) return;
  state.whatif = w;
  const m = match.meta, team = (side) => (side === 0 ? m.home : m.away);
  const who = match.player(h.i) || team(h.side);
  const what = { goal: `${who}'s goal`, owngoal: "the own goal", shot: `${who}'s shot`, red: `${who}'s red card` }[h.kind];
  const [hw, d, aw] = w.sim.outcome;
  const real = w.finalReal.score;
  const story = w.sim.story.goals.length
    ? w.sim.story.goals.map((g) => `${g.minute}' ${team(g.side)}`).join(", ") + `, final score ${w.sim.story.final.join(" - ")}`
    : `no more goals, final score ${w.sim.story.final.join(" - ")}`;
  const drawLabel = w.moment.extraTime ? "Penalties" : "Draw";
  $("whatif").innerHTML = `
    <button class="close" title="Close">×</button>
    <h3>What if</h3>
    <div class="q">What if ${what} (${h.minute + 1}') ${w.label}?</div>
    <p>It really ended <b>${real[0]} - ${real[1]}</b>. Changing that one moment, ${w.sim.n.toLocaleString()} simulated
      endings from there give:</p>
    <table>
      <tr><td>${m.home} win</td><td>${pct(hw)}</td></tr>
      <tr><td>${drawLabel}</td><td>${pct(d)}</td></tr>
      <tr><td>${m.away} win</td><td>${pct(aw)}</td></tr>
    </table>
    <p>Most likely final scores: ${w.sim.scorelines.slice(0, 4).map((s) => `<b>${s.score.join("-")}</b> ${pct(s.share)}`).join(" · ")}</p>
    <p class="story">One way it could have gone: ${story}.</p>
    <p>The dashed lines on the win-probability chart show how the odds would have read from then on, with the
      changed score and everything else as it really happened.</p>`;
  $("whatif").hidden = false;
  $("whatif").querySelector(".close").onclick = () => { state.whatif = null; $("whatif").hidden = true; };
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
