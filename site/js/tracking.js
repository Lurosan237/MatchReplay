// Real player tracking (PFF FC, World Cup 2022), streamed one minute at a time
// (python -m matchreplay.tracking_web). Every frame, 29.97 per second, interpolated between.

export const MISSING = -32768;

export class Tracking {
  static async load(base) {
    const res = await fetch(`${base}/meta.json`);
    if (!res.ok) return null;
    return new Tracking(base, await res.json());
  }

  constructor(base, meta) {
    this.base = base;
    this.meta = meta;
    this.nh = meta.home_shirts.length;
    this.na = meta.away_shirts.length;
    this.byN = new Map(meta.chunks.map((c) => [c.n, c]));
    this.cache = new Map();       // n -> Promise<chunk>
  }

  chunkFor(t) { return this.byN.get(Math.floor(t / this.meta.chunk_ms)); }

  /** Fetch and decode a chunk (cached). */
  get(n) {
    if (!this.cache.has(n)) this.cache.set(n, this.#fetch(n));
    if (this.cache.size > 12) {   // keep memory bounded on long matches
      const far = [...this.cache.keys()].sort((a, b) => Math.abs(b - n) - Math.abs(a - n))[0];
      if (far !== n) this.cache.delete(far);
    }
    return this.cache.get(n);
  }

  async #fetch(n) {
    const res = await fetch(`${this.base}/${n}.bin.gz`);
    const stream = res.body.pipeThrough(new DecompressionStream("gzip"));
    const buf = await new Response(stream).arrayBuffer();
    const frames = new Int32Array(buf, 0, 1)[0];
    const time = new Int32Array(buf.slice(4, 4 + 4 * frames));
    const raw = new Int16Array(buf.slice(4 + 4 * frames));
    const count = raw.length / frames;
    const series = [];
    for (let s = 0; s < count; s++) {
      const out = new Int16Array(frames);         // running sum, wrapping in 16 bits like the encoder
      let acc = 0;
      for (let i = 0; i < frames; i++) { acc = (acc + raw[s * frames + i]) << 16 >> 16; out[i] = acc; }
      series.push(out);
    }
    return { n, frames, time, series, ready: true };
  }

  /** Start loading the chunks around t, so playback never waits. */
  prefetch(t, ahead = 2) {
    const n = Math.floor(t / this.meta.chunk_ms);
    for (let k = -1; k <= ahead; k++) if (this.byN.has(n + k)) this.get(n + k).then((c) => { this.loaded.set(n + k, c); });
  }

  loaded = new Map();

  /**
   * Positions at time t, in metres (home attacking +x), or null while the chunk is loading.
   * {ball: [x, y, z] | null, home: [[x, y] | null...], away: [...]}
   */
  frame(t) {
    const info = this.chunkFor(t);
    if (!info) return { gap: true };
    const c = this.loaded.get(info.n);
    if (!c) { this.prefetch(t); return null; }
    const T = c.time;
    let lo = 0, hi = c.frames - 1;
    if (t <= T[0]) hi = 0;
    else if (t >= T[hi]) lo = hi;
    else {
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (T[mid] <= t) lo = mid; else hi = mid; }
    }
    const f = hi > lo ? (t - T[lo]) / (T[hi] - T[lo]) : 0;
    const at = (s) => {
      const a = c.series[s][lo], b = c.series[s][hi];
      if (a === MISSING) return null;
      return (b === MISSING ? a : a + (b - a) * f) / 100;
    };
    const xy = (s) => { const x = at(s), y = at(s + 1); return x == null || y == null ? null : [x, y]; };
    const nb = this.meta.ball_series || 3;          // x, y, z (+ a "rebuilt" flag in newer files)
    const bx = at(0), by = at(1), bz = at(2);
    const rebuilt = nb > 3 ? c.series[3][lo] === 1 : false;
    const home = [], away = [];
    for (let j = 0; j < this.nh; j++) home.push(xy(nb + 2 * j));
    for (let j = 0; j < this.na; j++) away.push(xy(nb + 2 * this.nh + 2 * j));
    return { ball: bx == null || by == null ? null : [bx, by, bz ?? 0], rebuilt, home, away };
  }
}
