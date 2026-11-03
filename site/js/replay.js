// The replay model: a match file (python -m matchreplay.web_export) and a clock.
// Pure functions, no DOM: the page draws whatever these return, and node tests run them.

export const PERIOD_OFFSET_MIN = { 1: 0, 2: 60, 3: 120, 4: 140, 5: 160 };
const PERIOD_BASE_MIN = { 1: 0, 2: 45, 3: 90, 4: 105, 5: 120 };
const SHOT_OUTCOME = { "Off T": "off target", Wayward: "wide", Blocked: "blocked", Saved: "saved", Post: "hits the post",
  "Saved to Post": "saved onto the post", "Saved Off Target": "saved" };
export const PERIOD_NAME = { 1: "1st half", 2: "2nd half", 3: "Extra time, 1st", 4: "Extra time, 2nd", 5: "Penalties" };

/** Last index i with arr[i] <= x, or -1. */
export function lastAtOrBefore(arr, x) {
  let lo = 0, hi = arr.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= x) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

export class Match {
  /** model: a WinModel (winprob.js), for the live win probability. */
  constructor(data, model = null) {
    this.model = model;
    this.meta = data.meta;
    this.types = data.types;
    this.players = data.players;
    this.ev = data.events;
    this.snaps = data.snaps;
    this.n = this.ev.t.length;
    this.typeIndex = Object.fromEntries(this.types.map((t, i) => [t, i]));
    this.segments = this.#segments();
    this.keys = this.#ballKeyframes();
    this.highlights = this.#highlights();
  }

  type(i) { return this.types[this.ev.type[i]]; }
  player(i) { const p = this.ev.player[i]; return p >= 0 ? this.players[p] : null; }
  team(side) { return side === 0 ? this.meta.home : side === 1 ? this.meta.away : ""; }

  /** The periods actually played, each a stretch of event time. */
  #segments() {
    const by = new Map();
    for (let i = 0; i < this.n; i++) {
      const p = this.ev.period[i], t = this.ev.t[i];
      const s = by.get(p) || { period: p, start: t, end: t };
      s.start = Math.min(s.start, t);
      s.end = Math.max(s.end, t + (this.ev.dur[i] || 0) * 1000);
      by.set(p, s);
    }
    return [...by.values()].sort((a, b) => a.period - b.period);
  }

  get start() { return this.segments[0].start; }
  get end() { return this.segments[this.segments.length - 1].end; }
  /** Played time only, without the gaps between periods: what the timeline shows. */
  get duration() { return this.segments.reduce((s, g) => s + g.end - g.start, 0); }

  segmentAt(t) {
    for (const s of this.segments) if (t <= s.end) return s;
    return this.segments[this.segments.length - 1];
  }

  /** Event time -> position on the timeline (0..duration), and back. */
  toPlayed(t) {
    let acc = 0;
    for (const s of this.segments) {
      if (t < s.start) return acc;
      if (t <= s.end) return acc + t - s.start;
      acc += s.end - s.start;
    }
    return acc;
  }
  fromPlayed(x) {
    let acc = 0;
    for (const s of this.segments) {
      const len = s.end - s.start;
      if (x <= acc + len) return s.start + Math.max(0, x - acc);
      acc += len;
    }
    return this.end;
  }

  /** Move the clock forward by dt ms of match time, jumping over the breaks between periods. */
  advance(t, dt) {
    let next = t + dt;
    const seg = this.segmentAt(t);
    if (next > seg.end) {
      const i = this.segments.indexOf(seg);
      if (i < this.segments.length - 1) return { t: this.segments[i + 1].start, jumped: this.segments[i + 1] };
      return { t: this.end, finished: true };
    }
    return { t: next };
  }

  /** "67:12" as on a broadcast clock. */
  clock(t) {
    const seg = this.segmentAt(t);
    if (seg.period === 5) return "PENS";
    const s = Math.max(0, Math.floor(PERIOD_BASE_MIN[seg.period] * 60 + (t - PERIOD_OFFSET_MIN[seg.period] * 60000) / 1000));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  /** The live numbers at time t: the last snapshot emitted at or before it (as the pipeline would have). */
  snapshot(t) {
    const i = lastAtOrBefore(this.snaps.time, t);
    return i < 0 ? null : this.snapshotAt(i);
  }

  /** Win probability after every snapshot: from the pipeline when it sent it (live), else computed here. */
  get winProbs() {
    if (!this._wp) {
      const S = this.snaps, n = S.time.length;
      this._wp = S.win_prob && S.win_prob.length === n ? S.win_prob
        : this.model ? Array.from({ length: n }, (_, i) => this.model.forSnapshot(this.snapshotAt(i), !!this.meta.women))
        : new Array(n).fill(null);
    }
    return this._wp;
  }

  /** [P(home wins), P(draw), P(away wins)] as of time t, or null (shoot-out, or no model). */
  winProb(t) {
    const i = lastAtOrBefore(this.snaps.time, t);
    return i < 0 ? (this.model ? this.model.forSnapshot(this.#kickoff(), !!this.meta.women) : null) : this.winProbs[i];
  }

  #kickoff() {
    return { time: this.start, period: 1, score: [0, 0], h_red: 0, a_red: 0, h_xg: 0, a_xg: 0 };
  }

  snapshotAt(i) {
    const out = {};
    for (const k in this.snaps) out[k] = this.snaps[k][i];
    return out;
  }

  /** Index of the last event at or before t. */
  eventAt(t) { return lastAtOrBefore(this.ev.t, t); }

  /** Ball path: every event's start, and where passes, carries and shots end. */
  #ballKeyframes() {
    const kt = [], kx = [], ky = [];
    const push = (t, x, y) => {
      if (kt.length && t < kt[kt.length - 1]) t = kt[kt.length - 1];
      kt.push(t); kx.push(x); ky.push(y);
    };
    for (let i = 0; i < this.n; i++) {
      const { x, y, ex, ey, dur, t } = { x: this.ev.x[i], y: this.ev.y[i], ex: this.ev.ex[i], ey: this.ev.ey[i], dur: this.ev.dur[i], t: this.ev.t[i] };
      if (x == null || y == null) continue;
      push(t, x, y);
      if (ex != null && ey != null) {
        const next = i + 1 < this.n ? this.ev.t[i + 1] : t + 2000;
        const travel = Math.min(Math.max((dur || 0.4) * 1000, 250), Math.max(next - t, 250), 6000);
        push(t + travel, ex, ey);
      }
    }
    return { t: kt, x: kx, y: ky };
  }

  ball(t) {
    const k = this.keys, i = lastAtOrBefore(k.t, t);
    if (i < 0) return { x: 60, y: 40 };
    if (i === k.t.length - 1) return { x: k.x[i], y: k.y[i] };
    const span = k.t[i + 1] - k.t[i];
    // Don't drift across the pitch during long stoppages: hold still, then move in the last 1.5 s
    const g = span > 4000
      ? Math.min(1, Math.max(0, (t - (k.t[i + 1] - 1500)) / 1500))
      : span > 0 ? Math.min(1, (t - k.t[i]) / span) : 1;
    return { x: k.x[i] + (k.x[i + 1] - k.x[i]) * g, y: k.y[i] + (k.y[i + 1] - k.y[i]) * g };
  }

  /** The moments the feed and the timeline show: goals, shots, cards, subs, period starts and ends. */
  #highlights() {
    const out = [];
    for (let i = 0; i < this.n; i++) {
      const type = this.type(i), side = this.ev.side[i], who = this.player(i), team = this.team(side);
      const outcome = this.ev.outcome[i], card = this.ev.card[i], p = this.ev.period[i];
      let text = null, kind = "info";
      if (type === "Shot") {
        const xg = this.ev.xg[i] != null ? ` (xG ${this.ev.xg[i].toFixed(2)})` : "";
        if (p === 5) { text = `${who} (${team}): penalty ${outcome === "Goal" ? "scored" : "missed"}`; kind = outcome === "Goal" ? "pen" : "penmiss"; }
        else if (outcome === "Goal") { text = `GOAL! ${who}, ${team}${xg}`; kind = "goal"; }
        else { text = `${who} shoots: ${SHOT_OUTCOME[outcome] || (outcome || "").toLowerCase()}${xg}`; kind = "shot"; }
      } else if (type === "Own Goal Against") { text = `Own goal by ${who} (${team})`; kind = "owngoal"; }
      else if (card) { text = `${card}: ${who} (${team})`; kind = card.includes("Red") || card.includes("Second") ? "red" : "yellow"; }
      else if (type === "Substitution") { text = `Substitution, ${team}: ${who} off`; kind = "sub"; }
      else if (type === "Half Start" && side === 0) { text = `${PERIOD_NAME[p]} begins`; }
      else if (type === "Half End" && side === 0) { text = `End of the ${PERIOD_NAME[p].toLowerCase()}`; }
      if (text) out.push({ i, t: this.ev.t[i], minute: this.ev.minute[i], period: p, kind, text, side });
    }
    return out;
  }

  /** Goals that count (not the shoot-out), for the timeline markers. */
  goals() {
    return this.highlights.filter((h) => h.kind === "goal" || h.kind === "owngoal");
  }

  /** Shots taken so far (shoot-out excluded): where from, xG, and whether they went in. */
  shotsUntil(t) {
    const out = [];
    for (let i = 0; i < this.n && this.ev.t[i] <= t; i++) {
      if (this.ev.type[i] === this.typeIndex.Shot && this.ev.period[i] !== 5 && this.ev.x[i] != null) {
        out.push({ x: this.ev.x[i], y: this.ev.y[i], xg: this.ev.xg[i] || 0.02, goal: this.ev.outcome[i] === "Goal", side: this.ev.side[i], t: this.ev.t[i] });
      }
    }
    return out;
  }

  /** The last few actions before t, newest last: what the pitch draws as trails. */
  recent(t, ms = 9000) {
    const out = [];
    for (let i = this.eventAt(t); i >= 0 && this.ev.t[i] > t - ms; i--) {
      if (this.ev.x[i] == null) continue;
      out.push({ i, age: t - this.ev.t[i], type: this.type(i), side: this.ev.side[i], x: this.ev.x[i], y: this.ev.y[i], ex: this.ev.ex[i], ey: this.ev.ey[i], outcome: this.ev.outcome[i], player: this.player(i) });
    }
    return out.reverse();
  }
}
