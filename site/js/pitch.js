// Drawing the pitch (StatsBomb's 120 x 80 yards, home team attacking to the right) on a canvas.

const W = 120, H = 80, PAD = 4;

export function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export class Pitch {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.resize();
    addEventListener("resize", () => this.resize());
  }

  resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = this.canvas.clientWidth || 800;
    this.canvas.width = Math.round(w * dpr);
    this.canvas.height = Math.round((w * (H + 2 * PAD)) / (W + 2 * PAD) * dpr);
    this.scale = this.canvas.width / (W + 2 * PAD);
  }

  px(x) { return (x + PAD) * this.scale; }
  py(y) { return (y + PAD) * this.scale; }

  drawField() {
    const c = this.ctx, s = this.scale;
    c.fillStyle = css("--grass-1");
    c.fillRect(0, 0, this.canvas.width, this.canvas.height);
    c.fillStyle = css("--grass-2");
    for (let i = 0; i < 12; i += 2) c.fillRect(this.px(i * 10), this.py(0), 10 * s, H * s);
    c.strokeStyle = css("--chalk");
    c.lineWidth = Math.max(1, 0.35 * s);
    const rect = (x, y, w, h) => c.strokeRect(this.px(x), this.py(y), w * s, h * s);
    rect(0, 0, W, H);
    c.beginPath(); c.moveTo(this.px(60), this.py(0)); c.lineTo(this.px(60), this.py(H)); c.stroke();
    c.beginPath(); c.arc(this.px(60), this.py(40), 10 * s, 0, Math.PI * 2); c.stroke();
    rect(0, 18, 18, 44); rect(102, 18, 18, 44); rect(0, 30, 6, 20); rect(114, 30, 6, 20);
    rect(-2, 36, 2, 8); rect(120, 36, 2, 8);
    const dot = (x, y) => { c.beginPath(); c.arc(this.px(x), this.py(y), 0.5 * s, 0, Math.PI * 2); c.fillStyle = css("--chalk"); c.fill(); };
    dot(60, 40); dot(12, 40); dot(108, 40);
    const arc = (x, a0, a1) => { c.beginPath(); c.arc(this.px(x), this.py(40), 10 * s, a0, a1); c.stroke(); };
    arc(12, -0.93, 0.93); arc(108, Math.PI - 0.93, Math.PI + 0.93);
  }

  color(side) { return side === 0 ? css("--home") : side === 1 ? css("--away") : css("--muted"); }

  drawShots(shots, now) {
    const c = this.ctx, s = this.scale;
    for (const sh of shots) {
      const r = (0.8 + Math.sqrt(sh.xg) * 3.2) * s;
      const fresh = Math.max(0, 1 - (now - sh.t) / 4000);
      c.beginPath();
      c.arc(this.px(sh.x), this.py(sh.y), r + fresh * 2 * s, 0, Math.PI * 2);
      c.globalAlpha = 0.85;
      if (sh.goal) { c.fillStyle = css("--goal"); c.fill(); }
      c.strokeStyle = this.color(sh.side);
      c.lineWidth = Math.max(1, 0.4 * s);
      c.stroke();
      c.globalAlpha = 1;
    }
  }

  drawTrails(recent) {
    const c = this.ctx, s = this.scale;
    for (const a of recent) {
      if (a.ex == null) continue;
      const alpha = Math.max(0, 1 - a.age / 9000) * 0.8;
      c.globalAlpha = alpha;
      c.strokeStyle = this.color(a.side);
      c.lineWidth = Math.max(1, (a.type === "Shot" ? 0.6 : 0.35) * s);
      c.setLineDash(a.type === "Carry" ? [1.2 * s, 1.2 * s] : a.outcome && a.type === "Pass" ? [0.4 * s, 0.9 * s] : []);
      c.beginPath(); c.moveTo(this.px(a.x), this.py(a.y)); c.lineTo(this.px(a.ex), this.py(a.ey)); c.stroke();
    }
    c.setLineDash([]);
    c.globalAlpha = 1;
  }

  drawActor(a) {
    if (!a) return;
    const c = this.ctx, s = this.scale;
    const fade = Math.max(0, 1 - a.age / 3500);
    if (fade <= 0) return;
    c.globalAlpha = fade;
    c.beginPath(); c.arc(this.px(a.x), this.py(a.y), 1.6 * s, 0, Math.PI * 2);
    c.fillStyle = this.color(a.side); c.fill();
    c.strokeStyle = "#0b1210"; c.lineWidth = Math.max(1, 0.3 * s); c.stroke();
    if (a.player) {
      const name = a.player.split(" ").slice(-1)[0];
      c.font = `600 ${Math.max(11, 2.1 * s)}px Inter, system-ui, sans-serif`;
      const tw = c.measureText(name).width;
      const x = Math.min(Math.max(this.px(a.x) - tw / 2, 4), this.canvas.width - tw - 4);
      const y = this.py(a.y) - 2.6 * s;
      c.fillStyle = "rgba(11,18,16,0.75)";
      c.fillRect(x - 4, y - 2.4 * s, tw + 8, 3 * s);
      c.fillStyle = "#fff"; c.fillText(name, x, y);
    }
    c.globalAlpha = 1;
  }

  drawBall(b) {
    const c = this.ctx, s = this.scale;
    c.beginPath(); c.arc(this.px(b.x), this.py(b.y), 2.4 * s, 0, Math.PI * 2);
    c.fillStyle = "rgba(255,255,255,0.15)"; c.fill();
    c.beginPath(); c.arc(this.px(b.x), this.py(b.y), 0.95 * s, 0, Math.PI * 2);
    c.fillStyle = "#fff"; c.fill();
  }
}
