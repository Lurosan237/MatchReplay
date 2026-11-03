// The streaming job's logic in miniature, for the Lab: a messy feed in, correct live numbers out.
// This mirrors LiveMatchState.java (the real job runs in Apache Flink and is tested in CI); here it
// runs in the browser so you can turn the knobs and watch what breaks.
//
//   chaos()   delivers a match's events the way a real feed does: some late, a few twice
//   run()     the job: drop duplicates by id, buffer events in 5-second windows of match time,
//             close a window once the match's clock (latest event time minus the allowed delay)
//             has passed it, apply its events in true order, emit a snapshot. Anything arriving
//             after its window closed goes to the side output instead of corrupting the numbers.

export const WINDOW_MS = 5000;

/** The match file's events as feed messages, in true order. */
export function messages(match) {
  const ev = match.ev, out = [];
  for (let i = 0; i < match.n; i++) {
    out.push({ id: i, t: ev.t[i], period: ev.period[i], type: match.types[ev.type[i]], side: ev.side[i],
      outcome: ev.outcome[i], xg: ev.xg[i], card: ev.card[i] });
  }
  return out;
}

/** Seeded, so a run can be repeated exactly. */
function rng(seed) {
  let x = seed >>> 0 || 1;
  return () => ((x = (x * 1664525 + 1013904223) >>> 0) / 4294967296);
}

/** Arrival order of a messy feed: each message at its event time plus a delay; some delayed, some twice. */
export function chaos(msgs, { lateShare = 0.05, maxDelayMs = 8000, duplicateShare = 0.01, seed = 7 } = {}) {
  const r = rng(seed), deliveries = [];
  msgs.forEach((m, i) => {
    const delay = r() < lateShare ? 1 + Math.floor(r() * maxDelayMs) : 0;
    deliveries.push({ arrival: m.t + delay, i, dup: false, delay, m });
    if (r() < duplicateShare) deliveries.push({ arrival: m.t + Math.floor(r() * maxDelayMs), i, dup: true, delay: 0, m });
  });
  deliveries.sort((a, b) => a.arrival - b.arrival || a.i - b.i || a.dup - b.dup);
  return deliveries;
}

function emptyState() {
  return { goals: [0, 0], shots: [0, 0], xg: [0, 0], passes: [0, 0], cards: [0, 0] };
}

function apply(s, m) {
  const k = m.side;
  if (k !== 0 && k !== 1) return;
  if (m.type === "Shot" && m.period !== 5) {
    s.shots[k]++;
    s.xg[k] += m.xg || 0;
    if (m.outcome === "Goal") s.goals[k]++;
  } else if (m.type === "Own Goal For" && m.period !== 5) s.goals[k]++;
  else if (m.type === "Pass") s.passes[k]++;
  if (m.card) s.cards[k]++;
}

const snapshot = (s, time) => ({ time, goals: [...s.goals], shots: [...s.shots], xg: s.xg.map((v) => Math.round(v * 1e4) / 1e4),
  passes: [...s.passes], cards: [...s.cards] });

/**
 * Run the job over deliveries ({m, dup}) with an allowed delay. Returns the snapshots, the late
 * events (side output), the duplicates dropped, and a log of when each message was handled.
 */
export function run(deliveries, allowedMs) {
  const seen = new Set(), pending = new Map(), state = emptyState();
  const snaps = [], late = [], log = [];
  let latest = -Infinity, duplicates = 0;
  const clock = () => latest - allowedMs;
  const flush = (upTo) => {
    const ready = [...pending.keys()].filter((end) => end <= upTo).sort((a, b) => a - b);
    for (const end of ready) {
      const bucket = pending.get(end).sort((a, b) => a.t - b.t || a.id - b.id);
      for (const m of bucket) apply(state, m);
      pending.delete(end);
      snaps.push(snapshot(state, end));
    }
  };
  for (const d of deliveries) {
    const m = d.m;
    if (seen.has(m.id)) { duplicates++; log.push({ ...d, fate: "duplicate" }); continue; }
    seen.add(m.id);
    const end = (Math.floor(m.t / WINDOW_MS) + 1) * WINDOW_MS;
    if (end <= clock()) { late.push(m); log.push({ ...d, fate: "late" }); continue; }
    if (!pending.has(end)) pending.set(end, []);
    pending.get(end).push(m);
    latest = Math.max(latest, m.t);
    log.push({ ...d, fate: d.delay > 0 ? "reordered" : "on time" });
    flush(clock());
  }
  flush(Infinity);                                   // the end-of-match marker
  return { snaps, late, duplicates, log };
}

/** The oracle: the same job on the events in perfect order, no duplicates. */
export function reference(msgs) {
  return run(msgs.map((m, i) => ({ arrival: m.t, i, dup: false, delay: 0, m })), 0).snaps;
}

/** How many snapshots differ from the reference (missing, extra or with other numbers). */
export function differences(snaps, ref) {
  const byTime = new Map(snaps.map((s) => [s.time, JSON.stringify(s)]));
  let diff = 0;
  for (const r of ref) if (byTime.get(r.time) !== JSON.stringify(r)) diff++;
  return diff + Math.max(0, snaps.length - ref.length);
}
