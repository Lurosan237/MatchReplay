// Live win probability in the browser: a line-by-line port of matchreplay/winprob.py (see there for
// the model). The Flink job runs the Java port of the same lines; tests check all three agree.
// Also the "what if" simulation: the same goal rates, sampled forward to see how a match could end.

import { PERIOD_OFFSET_MIN } from "./replay.js";

const PERIOD_START_MIN = { 1: 0, 2: 45, 3: 90, 4: 105 };
const round4 = (v) => Math.round(v * 10000) / 10000;

export class WinModel {
  constructor(m) {
    this.beta = m.beta;
    this.periodLength = Object.fromEntries(Object.entries(m.period_length).map(([k, v]) => [Number(k), v]));
    this.maxDiff = m.max_diff;
    this.minRemaining = m.min_remaining;
    this.xgPrior = m.xg_prior_per_90;
    this.xgPriorMinutes = m.xg_prior_minutes;
  }

  elapsed(t, period) { return (t - PERIOD_OFFSET_MIN[period] * 60000) / 60000; }

  remaining(t, period) {
    let left = Math.max(this.periodLength[period] - this.elapsed(t, period), this.minRemaining);
    if (period === 1 || period === 3) left += this.periodLength[period + 1];
    return left;
  }

  played(t, period) { return PERIOD_START_MIN[period] + Math.max(this.elapsed(t, period), 0); }

  shrunk(xg, played) { return (xg + this.xgPrior / 90 * this.xgPriorMinutes) / (played + this.xgPriorMinutes) * 90; }

  features(home, diff, ownRed, oppRed, xgFor, xgAgainst, women, extraTime) {
    return [1, home ? 1 : 0, diff === 1 ? 1 : 0, diff >= 2 ? 1 : 0, diff === -1 ? 1 : 0, diff <= -2 ? 1 : 0,
      Math.min(ownRed, 2), Math.min(oppRed, 2), xgFor - this.xgPrior, xgAgainst - this.xgPrior, women ? 1 : 0, extraTime ? 1 : 0];
  }

  rate(x) {
    let s = 0;
    for (let i = 0; i < this.beta.length; i++) s += this.beta[i] * x[i];
    return Math.exp(s);
  }

  /** Goal rates per minute, for each team, at every score difference i - maxDiff + diff. */
  rates(diff, home, away, women, extraTime) {
    const n = 2 * this.maxDiff + 1, rh = new Array(n), ra = new Array(n);
    for (let i = 0; i < n; i++) {
      rh[i] = this.rate(this.features(true, diff - this.maxDiff + i, home.red, away.red, home.xg, away.xg, women, extraTime));
      ra[i] = this.rate(this.features(false, this.maxDiff - diff - i, away.red, home.red, away.xg, home.xg, women, extraTime));
    }
    return { rh, ra };
  }

  /** [P(home wins), P(draw), P(away wins)] at the end of the phase. */
  outcome(diff, remaining, home, away, women, extraTime) {
    const n = 2 * this.maxDiff + 1;
    const { rh, ra } = this.rates(diff, home, away, women, extraTime);
    let p = new Array(n).fill(0);
    p[this.maxDiff] = 1;
    const steps = Math.max(1, Math.ceil(remaining));
    for (let step = 0; step < steps; step++) {
      const dt = step === steps - 1 ? remaining - (steps - 1) : 1;
      const q = new Array(n).fill(0);
      for (let i = 0; i < n; i++) {
        if (p[i] === 0) continue;
        const gh = 1 - Math.exp(-rh[i] * dt), ga = 1 - Math.exp(-ra[i] * dt);
        q[i] += p[i] * ((1 - gh) * (1 - ga) + gh * ga);
        q[Math.min(i + 1, n - 1)] += p[i] * gh * (1 - ga);
        q[Math.max(i - 1, 0)] += p[i] * ga * (1 - gh);
      }
      p = q;
    }
    let win = 0, draw = 0;
    for (let i = 0; i < n; i++) {
      const d = diff - this.maxDiff + i;
      if (d > 0) win += p[i]; else if (d === 0) draw += p[i];
    }
    return [round4(win), round4(draw), round4(1 - win - draw)];
  }

  /** The inputs for one moment: from a snapshot (flattened as in the match files). */
  moment(s, women) {
    const period = s.period, played = this.played(s.time, period);
    return {
      period, t: s.time, diff: s.score[0] - s.score[1], score: s.score, women, extraTime: period >= 3,
      remaining: this.remaining(s.time, period),
      home: { red: s.h_red, xg: this.shrunk(s.h_xg, played) },
      away: { red: s.a_red, xg: this.shrunk(s.a_xg, played) },
    };
  }

  /** Win probability for a snapshot; null during the penalty shoot-out. */
  forSnapshot(s, women) {
    if (!(s.period in this.periodLength)) return null;
    const m = this.moment(s, women);
    return this.outcome(m.diff, m.remaining, m.home, m.away, women, m.extraTime);
  }

  /**
   * "How could it end from here?": n continuations sampled minute by minute with the same goal
   * rates. Returns the final scorelines, most likely first, and one sampled story (the goals, with
   * their minute) to tell.
   */
  simulate(m, n = 5000, seed = 1) {
    let x = seed >>> 0;
    const rand = () => ((x = (x * 1664525 + 1013904223) >>> 0) / 4294967296);
    const { rh, ra } = this.rates(m.diff, m.home, m.away, m.women, m.extraTime);
    const steps = Math.max(1, Math.ceil(m.remaining));
    const counts = new Map();
    let story = null;
    const start = this.played(m.t, m.period);
    for (let k = 0; k < n; k++) {
      let h = m.score[0], a = m.score[1];
      const goals = [];
      for (let step = 0; step < steps; step++) {
        const dt = step === steps - 1 ? m.remaining - (steps - 1) : 1;
        const i = Math.min(Math.max(h - a - m.diff + this.maxDiff, 0), 2 * this.maxDiff);
        const gh = 1 - Math.exp(-rh[i] * dt), ga = 1 - Math.exp(-ra[i] * dt);
        const minute = Math.floor(start + step) + 1;
        if (rand() < gh) { h++; if (k === 0) goals.push({ minute, side: 0 }); }
        if (rand() < ga) { a++; if (k === 0) goals.push({ minute, side: 1 }); }
      }
      const key = `${h}-${a}`;
      counts.set(key, (counts.get(key) || 0) + 1);
      if (k === 0) story = { goals, final: [h, a] };
    }
    const scorelines = [...counts.entries()].sort((p, q) => q[1] - p[1])
      .map(([score, c]) => ({ score: score.split("-").map(Number), share: c / n }));
    let win = 0, draw = 0;
    for (const s of scorelines) { if (s.score[0] > s.score[1]) win += s.share; else if (s.score[0] === s.score[1]) draw += s.share; }
    return { scorelines, story, outcome: [win, draw, 1 - win - draw], n };
  }
}

/**
 * What if one moment had gone the other way? A goal that is not scored, a shot that goes in, a red
 * card never shown. Everything after it is replayed with the changed score (or cards): the win
 * probability as it would have read, and how the match could have ended from there.
 */
export function whatIf(match, model, h) {
  const flip = {
    goal: { field: "score", side: h.side, delta: -1, label: "had not gone in" },
    owngoal: { field: "score", side: 1 - h.side, delta: -1, label: "had not happened" },
    shot: { field: "score", side: h.side, delta: +1, label: "had gone in" },
    red: { field: "red", side: h.side, delta: -1, label: "had not been shown" },
  }[h.kind];
  if (!flip) return null;
  const S = match.snaps, women = !!match.meta.women;
  const alt = { time: [], prob: [] };
  let first = null;
  for (let i = 0; i < S.time.length; i++) {
    if (S.time[i] <= h.t || S.period[i] === 5) continue;
    const s = match.snapshot(S.time[i]);
    if (flip.field === "score") {
      s.score = [...s.score];
      s.score[flip.side] = Math.max(0, s.score[flip.side] + flip.delta);
    } else {
      const key = flip.side === 0 ? "h_red" : "a_red";
      s[key] = Math.max(0, s[key] + flip.delta);
    }
    alt.time.push(s.time);
    alt.prob.push(model.forSnapshot(s, women));
    if (!first) first = s;
  }
  if (!first) return null;
  const moment = model.moment(first, women);
  const real = match.snapshot(first.time);
  return { h, label: flip.label, alt, moment, sim: model.simulate(moment), real: model.forSnapshot(real, women),
    finalReal: match.snapshot(match.end + 60000) };
}
