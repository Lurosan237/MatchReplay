// A footballer for the 3D view: a jointed body in the team's kit (shirt, shorts, socks, boots,
// number on the back), animated from the real tracking: how fast the player is actually moving
// decides between standing, jogging and sprinting, stride and arm swing follow the distance covered,
// and the body leans into the run and turns the way the player really moves.
import * as THREE from "three";

const SKIN = 0x8d6a52;      // one neutral tone for everyone: the data says nothing about the players' looks
const HAIR = 0x1f1a17;
const BOOT = 0x111111;

const geo = {
  torso: new THREE.CapsuleGeometry(0.16, 0.36, 6, 14),
  pelvis: new THREE.CylinderGeometry(0.165, 0.15, 0.2, 14),
  shortsLeg: new THREE.CylinderGeometry(0.085, 0.08, 0.2, 10),
  thigh: new THREE.CapsuleGeometry(0.07, 0.3, 4, 10),
  shin: new THREE.CapsuleGeometry(0.056, 0.32, 4, 10),
  boot: new THREE.BoxGeometry(0.1, 0.07, 0.24),
  sleeve: new THREE.CapsuleGeometry(0.058, 0.14, 4, 10),
  upperArm: new THREE.CapsuleGeometry(0.048, 0.2, 4, 8),
  forearm: new THREE.CapsuleGeometry(0.042, 0.22, 4, 8),
  neck: new THREE.CylinderGeometry(0.05, 0.055, 0.1, 10),
  head: new THREE.SphereGeometry(0.105, 18, 14),
  hair: new THREE.SphereGeometry(0.11, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2.1),
  number: new THREE.PlaneGeometry(0.24, 0.24),
};

const materials = new Map();
function mat(color, rough = 0.75) {
  const key = `${color}-${rough}`;
  if (!materials.has(key)) materials.set(key, new THREE.MeshStandardMaterial({ color, roughness: rough }));
  return materials.get(key);
}

function numberTexture(text, color) {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  g.font = "800 92px Inter, system-ui, sans-serif";
  g.textAlign = "center"; g.textBaseline = "middle";
  g.fillStyle = color; g.fillText(text, 64, 70);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function part(geometry, material, y = 0, parent = null) {
  const m = new THREE.Mesh(geometry, material);
  m.position.y = y;
  m.castShadow = true;
  if (parent) parent.add(m);
  return m;
}

/** A joint: a group that rotates, with the limb hanging below it. */
function joint(parent, x, y, z = 0) {
  const j = new THREE.Group();
  j.position.set(x, y, z);
  parent.add(j);
  return j;
}

export class Footballer {
  /** kit: {shirt, shorts, socks, number} colours */
  constructor(scene, kit, shirtNumber) {
    this.root = new THREE.Group();
    this.body = new THREE.Group();            // everything that bobs and leans
    this.root.add(this.body);
    const shirt = mat(kit.shirt), shorts = mat(kit.shorts), socks = mat(kit.socks), skin = mat(SKIN, 0.85);

    this.hips = joint(this.body, 0, 0.94);
    part(geo.pelvis, shorts, -0.02, this.hips);
    this.spine = joint(this.hips, 0, 0.06);
    part(geo.torso, shirt, 0.3, this.spine).scale.set(1.25, 1, 0.78);
    part(geo.neck, skin, 0.6, this.spine);
    this.head = joint(this.spine, 0, 0.72);
    part(geo.head, skin, 0, this.head);
    part(geo.hair, mat(HAIR, 0.9), 0.015, this.head);
    const back = new THREE.Mesh(geo.number, new THREE.MeshBasicMaterial({ map: numberTexture(shirtNumber, kit.number), transparent: true }));
    back.position.set(0, 0.36, -0.13);
    back.rotation.y = Math.PI;
    this.spine.add(back);

    this.legs = [-1, 1].map((side) => {
      const hip = joint(this.hips, side * 0.095, -0.06);
      part(geo.shortsLeg, shorts, -0.08, hip);
      part(geo.thigh, skin, -0.22, hip);
      const knee = joint(hip, 0, -0.42);
      part(geo.shin, socks, -0.2, knee);
      const foot = joint(knee, 0, -0.42);
      const boot = part(geo.boot, mat(BOOT, 0.5), 0.0, foot);
      boot.position.z = 0.05;
      return { hip, knee, side };
    });
    this.arms = [-1, 1].map((side) => {
      const shoulder = joint(this.spine, side * 0.235, 0.5);
      part(geo.sleeve, shirt, -0.08, shoulder);
      part(geo.upperArm, skin, -0.17, shoulder);
      const elbow = joint(shoulder, 0, -0.3);
      part(geo.forearm, skin, -0.13, elbow);
      return { shoulder, elbow, side };
    });

    this.phase = Math.random() * Math.PI * 2;
    this.speed = 0;
    this.heading = 0;
    this.last = null;
    this.root.visible = false;
    scene.add(this.root);
  }

  /** Forget the motion state (after a jump in time). */
  reset() { this.speed = 0; this.ready = false; }

  /**
   * Place the player at (x, z), moving at (vx, vz) metres per second as measured in the tracking
   * data itself; dtMatch is the match time since the last update (ms), so the animation is the
   * same at any playback speed. When nearly still, the player turns to face the ball.
   */
  update(x, z, vx, vz, dtMatch, lookX, lookZ) {
    this.root.visible = true;
    this.root.position.set(x, 0, z);
    const raw = Math.hypot(vx, vz);
    const v = Math.min(raw, 9);                                   // tracker estimates off camera can glide too fast
    const k = this.ready ? 1 - Math.exp(-dtMatch / 180) : 1;
    this.speed += (v - this.speed) * k;

    const want = this.speed > 1.0 ? Math.atan2(vx, vz) : Math.atan2(lookX - x, lookZ - z);
    if (!this.ready) this.heading = want;
    let d = want - this.heading;
    d = Math.atan2(Math.sin(d), Math.cos(d));
    this.heading += d * (1 - Math.exp(-dtMatch / (this.speed > 1.0 ? 140 : 450)));
    this.root.rotation.y = this.heading;
    this.ready = true;

    // Gait: the cycle advances with the ground covered (one cycle = two steps); steps lengthen with speed
    const effort = Math.min(this.speed / 7, 1);
    const step = 0.75 + 0.95 * effort;
    this.phase += (this.speed * dtMatch / 1000 / step) * Math.PI;
    const moving = Math.min(1, Math.max(0, (this.speed - 0.35) / 0.6)); // fade the gait in and out
    const s = Math.sin(this.phase), c = Math.cos(this.phase);
    const swing = (0.22 + 0.7 * effort) * moving;

    for (const leg of this.legs) {
      const p = leg.side < 0 ? s : -s, forward = leg.side < 0 ? c : -c;
      leg.hip.rotation.x = -p * swing;
      leg.knee.rotation.x = 0.06 + Math.max(0, forward) * (0.35 + 1.2 * effort) * moving;  // folds on the swing forward
    }
    for (const arm of this.arms) {
      const p = arm.side < 0 ? -s : s;
      arm.shoulder.rotation.x = -p * (0.12 + 0.7 * effort) * moving;
      arm.shoulder.rotation.z = arm.side * (0.1 + 0.05 * effort);
      arm.elbow.rotation.x = -(0.3 + 1.0 * effort * moving);
    }
    this.spine.rotation.x = 0.03 + 0.2 * effort * moving;        // lean into the run
    this.head.rotation.x = -0.12 * effort * moving;               // eyes stay up
    this.body.position.y = Math.abs(c) * 0.05 * (0.3 + effort) * moving;
  }

  hide() { this.root.visible = false; this.reset(); }
}

/** A ball with panels, rolling with the distance it travels. */
export function makeBall() {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 256;
  const g = c.getContext("2d");
  g.fillStyle = "#f4f4f2"; g.fillRect(0, 0, 512, 256);
  g.fillStyle = "#1b1b1b";
  for (let i = 0; i < 12; i++) {
    const x = (i % 6) * 85 + (i < 6 ? 20 : 62), y = i < 6 ? 70 : 186;
    g.beginPath();
    for (let k = 0; k < 5; k++) {
      const a = (k / 5) * Math.PI * 2 - Math.PI / 2;
      g.lineTo(x + Math.cos(a) * 22, y + Math.sin(a) * 26);
    }
    g.fill();
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const ball = new THREE.Mesh(new THREE.SphereGeometry(0.16, 24, 16), new THREE.MeshStandardMaterial({ map: tex, roughness: 0.35 }));
  ball.castShadow = true;
  ball.userData.last = null;
  ball.roll = (x, y, z) => {
    const last = ball.userData.last;
    if (last) {
      const dx = x - last.x, dz = z - last.z, d = Math.hypot(dx, dz);
      if (d > 0 && d < 3) {
        const axis = new THREE.Vector3(dz, 0, -dx).normalize();
        ball.rotateOnWorldAxis(axis, d / 0.16);
      }
    }
    ball.userData.last = { x, z };
    ball.position.set(x, y, z);
  };
  return ball;
}

/** Kit colours from PFF's metadata; keepers get a colour that clashes with neither team. */
export function kits(homeKit, awayKit) {
  const hex = (h, fallback) => (h && /^#[0-9a-f]{6}$/i.test(h) ? h : fallback);
  const team = (k, fb) => ({
    shirt: hex(k.primary, fb), shorts: hex(k.secondary, fb), socks: hex(k.primary, fb), number: hex(k.text, "#111111"),
  });
  const home = team(homeKit, "#dddddd"), away = team(awayKit, "#3366cc");
  const dist = (a, b) => {
    const p = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
    const [x, y] = [p(a), p(b)];
    return Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
  };
  const options = ["#c6f000", "#ff7a00", "#8a3ffc", "#00c2b2", "#ff3fa4", "#222222"];
  const keeper = (avoid) => options.slice().sort((a, b) =>
    Math.min(...avoid.map((k) => dist(b, k))) - Math.min(...avoid.map((k) => dist(a, k))));
  const [gk1, gk2] = keeper([home.shirt, away.shirt]);
  return {
    home, away,
    homeKeeper: { shirt: gk1, shorts: gk1, socks: gk1, number: "#111111" },
    awayKeeper: { shirt: gk2, shorts: gk2, socks: gk2, number: "#111111" },
  };
}
