import { Match } from "./replay.js";
import { WinModel } from "./winprob.js";
import { messages, chaos, run, reference, differences } from "./streamlab.js";

const $ = (id) => document.getElementById(id);
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const pct = (v) => `${Math.round(v * 100)}%`;

function canvas2d(cv) {
  const dpr = Math.min(devicePixelRatio || 1, 2), w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const c = cv.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);
  return { c, w, h };
}

// ---------------- 1. win probability ----------------
let model;
const counts = { hg: 0, ag: 0, hr: 0, ar: 0 };

/** A snapshot as the pipeline would emit it at this minute. */
function snapAt(minute, hx, ax) {
  const period = minute < 45 ? 1 : 2;
  const time = (period === 1 ? minute : 60 + minute - 45) * 60000 + 1;
  return { period, time, score: [counts.hg, counts.ag], h_red: counts.hr, a_red: counts.ar, h_xg: hx, a_xg: ax };
}

function updateModel() {
  const minute = Number($("minute").value), hx = Number($("hx").value), ax = Number($("ax").value), women = $("women").checked;
  $("o-minute").textContent = `${minute}'`;
  $("o-hx").textContent = hx.toFixed(2);
  $("o-ax").textContent = ax.toFixed(2);
  for (const k in counts) $(k).textContent = counts[k];
  const s = snapAt(minute, hx, ax);
  const p = model.forSnapshot(s, women);
  const segs = $("bar").children;
  p.forEach((v, i) => (segs[i].style.flexBasis = pct(v)));
  $("bar-labels").innerHTML = `<span>Home ${pct(p[0])}</span><span>Draw ${pct(p[1])}</span><span>Away ${pct(p[2])}</span>`;

  // The odds minute by minute if the score and cards stay, chances keep coming at the same rate
  const { c, w, h } = canvas2d($("curve"));
  const series = [[], [], []];
  for (let m = minute; m <= 94; m++) {
    const scale = minute > 0 ? m / minute : 1;
    const q = model.forSnapshot(snapAt(m, hx * scale, ax * scale), women);
    q.forEach((v, i) => series[i].push([m, v]));
  }
  c.strokeStyle = css("--line");
  for (const y of [0.25, 0.5, 0.75]) { c.beginPath(); c.moveTo(0, h - y * h); c.lineTo(w, h - y * h); c.stroke(); }
  [css("--home"), "#8ea39a", css("--away")].forEach((color, i) => {
    c.beginPath(); c.strokeStyle = color; c.lineWidth = 2;
    series[i].forEach(([m, v], k) => { const x = (m / 94) * w, y = h - v * (h - 4) - 2; k ? c.lineTo(x, y) : c.moveTo(x, y); });
    c.stroke();
  });
  c.fillStyle = css("--muted"); c.font = "11px ui-monospace, monospace";
  c.fillText(`${minute}'`, Math.min(w - 30, (minute / 94) * w + 4), 12); c.fillText("90'", w - 26, h - 4);

  const sim = model.simulate(model.moment(s, women), 5000, 11);
  const max = sim.scorelines[0].share;
  $("scorelines").innerHTML = sim.scorelines.slice(0, 6).map((r) =>
    `<div><span>${r.score[0]} - ${r.score[1]}</span><i style="width:${(r.share / max) * 100}%"></i><span>${pct(r.share)}</span></div>`).join("");
}

const NAMES = { home: "Playing at home", lead1: "Leading by one", lead2: "Leading by two or more", trail1: "Behind by one",
  trail2: "Behind by two or more", own_red: "Each own red card", opp_red: "Each opponent red card",
  xg_for: "Per extra xG per 90 created", xg_against: "Per extra xG per 90 conceded", women: "Women's football",
  extra_time: "In extra time" };

async function initModel() {
  const json = await (await fetch("data/winprob.json")).json();
  model = new WinModel(json);
  $("weights").innerHTML = json.features.map((f, i) => [f, Math.exp(json.beta[i])]).filter(([f]) => f !== "intercept")
    .map(([f, x]) => `<div><span>${NAMES[f] || f}</span><b class="${x > 1.02 ? "up" : x < 0.98 ? "down" : ""}">×${x.toFixed(2)}</b></div>`).join("");
  for (const id of ["minute", "hx", "ax", "women"]) $(id).addEventListener("input", updateModel);
  for (const b of document.querySelectorAll(".stepper button")) b.onclick = (e) => {
    e.preventDefault();
    const k = b.dataset.for, limit = k.endsWith("r") ? 3 : 9;
    counts[k] = Math.min(limit, Math.max(0, counts[k] + Number(b.dataset.d)));
    updateModel();
  };
  updateModel();
}

// ---------------- 2. streaming ----------------
const cache = new Map();
let anim = 0;

async function loadMatch(id) {
  if (!cache.has(id)) {
    const m = new Match(await (await fetch(`data/matches/${id}.json`)).json());
    const msgs = messages(m);
    cache.set(id, { m, msgs, ref: reference(msgs) });
  }
  return cache.get(id);
}

function labels() {
  $("o-late").textContent = pct(Number($("late").value));
  $("o-maxd").textContent = `${(Number($("maxd").value) / 1000).toFixed(1)} s`;
  $("o-dup").textContent = `${(Number($("dup").value) * 100).toFixed(1)}%`;
  $("o-allow").textContent = `${(Number($("allow").value) / 1000).toFixed(1)} s`;
}

const COLORS = () => ({ "on time": "#6f8a80", reordered: css("--away"), duplicate: "#8a6cff", late: css("--goal") });

async function runStream() {
  cancelAnimationFrame(anim);
  const { m, msgs, ref } = await loadMatch($("s-match").value);
  const deliveries = chaos(msgs, { lateShare: Number($("late").value), maxDelayMs: Number($("maxd").value),
    duplicateShare: Number($("dup").value), seed: 7 });
  const allowed = Number($("allow").value);
  const out = run(deliveries, allowed);
  const log = out.log, maxDelay = Math.max(1000, Number($("maxd").value));
  const t0 = log[0].arrival, t1 = log[log.length - 1].arrival;
  const colors = COLORS();
  $("verdict").className = "verdict";
  const started = performance.now(), duration = 4000;
  const counters = { delivered: 0, "on time": 0, reordered: 0, duplicate: 0, late: 0 };

  const frame = (now) => {
    const k = Math.min(log.length, Math.ceil(((now - started) / duration) * log.length));
    const { c, w, h } = canvas2d($("feed"));
    // the allowance: anything above this line is too late
    const yOf = (d) => h - 8 - (Math.min(d, maxDelay * 1.05) / (maxDelay * 1.05)) * (h - 20);
    c.fillStyle = "rgba(255,90,78,0.07)"; c.fillRect(0, 0, w, yOf(allowed));
    c.strokeStyle = css("--goal"); c.setLineDash([4, 4]); c.beginPath(); c.moveTo(0, yOf(allowed)); c.lineTo(w, yOf(allowed)); c.stroke(); c.setLineDash([]);
    c.fillStyle = css("--muted"); c.font = "11px ui-monospace, monospace";
    c.fillText(`waits ${(allowed / 1000).toFixed(1)} s`, 6, Math.max(12, yOf(allowed) - 5));
    c.fillText("delay ↑   arrival time →", 6, h - 2 > 12 ? h - 2 : 12);
    for (const key in counters) counters[key] = 0;
    for (let i = 0; i < k; i++) {
      const d = log[i], x = ((d.arrival - t0) / Math.max(1, t1 - t0)) * (w - 8) + 4;
      const delay = d.fate === "duplicate" ? d.arrival - d.m.t : d.delay;
      c.fillStyle = colors[d.fate];
      const r = d.fate === "on time" ? 1.3 : 2.6;
      c.beginPath(); c.arc(x, yOf(Math.max(0, delay)), r, 0, Math.PI * 2); c.fill();
      counters[d.fate]++;
    }
    counters.delivered = k;
    $("counters").innerHTML = [
      ["Messages delivered", counters.delivered], ["Late, put back in place", counters.reordered],
      ["Duplicates dropped", counters.duplicate], ["Too late: side output", counters.late],
      ["Snapshots emitted", k === log.length ? out.snaps.length : "…"],
    ].map(([l, v]) => `<div>${l}<b>${v}</b></div>`).join("");
    if (k < log.length) { anim = requestAnimationFrame(frame); return; }
    const diff = differences(out.snaps, ref), final = out.snaps[out.snaps.length - 1];
    const real = [m.meta.home_score, m.meta.away_score];
    if (diff === 0) {
      $("verdict").className = "verdict ok";
      $("verdict").innerHTML = `<b>Identical to a perfect feed.</b> All ${ref.length} snapshots match, final score
        ${final.goals[0]}-${final.goals[1]} (the real one: ${real[0]}-${real[1]}). The price: each snapshot comes out
        ${(allowed / 1000).toFixed(1)} s of match time after its window closes.`;
    } else {
      $("verdict").className = "verdict bad";
      $("verdict").innerHTML = `<b>${diff} of ${ref.length} snapshots differ from a perfect feed.</b> ${out.late.length}
        events arrived after their window had closed and went to the side output instead of being counted, so the
        live numbers drifted${final.goals.join() !== real.join() ? ` (final score shown: ${final.goals[0]}-${final.goals[1]}, real: ${real[0]}-${real[1]})` : ""}.
        Let the pipeline wait at least as long as events can be late.`;
    }
  };
  anim = requestAnimationFrame(frame);
}

function initStream() {
  for (const id of ["late", "maxd", "dup", "allow"]) $(id).addEventListener("input", labels);
  labels();
  $("run").onclick = runStream;
  runStream();
}

initModel().catch((e) => { $("scorelines").textContent = `Could not load the model: ${e.message}`; });
initStream();
