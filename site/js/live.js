// Live mode: the match arrives over a WebSocket from the gateway (python -m matchreplay.gateway)
// while the pipeline runs. Events are what the feed delivered; snapshots are what the Flink job
// committed. They are gathered into the same shape as an exported match file, so the page draws
// a live match exactly like a recorded one.
import { Match } from "./replay.js";

const EVENT_COLS = ["t", "period", "minute", "second", "type", "side", "player", "x", "y", "ex", "ey", "outcome", "xg", "card", "dur"];

export class LiveFeed {
  constructor(url, id) {
    this.meta = { match_id: Number(id), home: "Home", away: "Away", competition_name: "Live from the pipeline" };
    this.events = [];
    this.snaps = [];
    this.dirty = false;
    this.status = "connecting";
    this.ws = new WebSocket(`${url.replace(/\/$/, "")}/match/${id}`);
    this.ws.onopen = () => (this.status = "live");
    this.ws.onclose = () => (this.status = "closed");
    this.ws.onerror = () => (this.status = "error");
    this.ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.kind === "meta") Object.assign(this.meta, { home: m.home, away: m.away });
      else if (m.kind === "event") this.events.push(m);
      else if (m.kind === "snap") this.snaps.push(m);
      this.dirty = true;
    };
  }

  get ready() { return this.events.length > 0; }

  /** The latest event time the feed has delivered: live play can't go past it. */
  get edge() { return this.events.reduce((m, e) => Math.max(m, e.t), 0); }

  /** A Match from everything received so far (late events land in their right place). */
  build() {
    this.dirty = false;
    const ev = [...this.events].sort((a, b) => a.t - b.t || a.index - b.index);
    const types = [...new Set(ev.map((e) => e.type))];
    const players = [...new Set(ev.map((e) => e.player).filter(Boolean))];
    const cols = Object.fromEntries(EVENT_COLS.map((k) => [k, []]));
    for (const e of ev) {
      for (const k of EVENT_COLS) cols[k].push(e[k] ?? null);
      cols.type[cols.type.length - 1] = types.indexOf(e.type);
      cols.player[cols.player.length - 1] = e.player ? players.indexOf(e.player) : -1;
    }
    const snaps = [...this.snaps].sort((a, b) => a.time - b.time);
    const keys = snaps.length ? Object.keys(snaps[0]).filter((k) => k !== "kind") : ["time"];
    const scols = Object.fromEntries(keys.map((k) => [k, snaps.map((s) => s[k])]));
    return new Match({ meta: this.meta, types, players, events: cols, snaps: scols });
  }
}
