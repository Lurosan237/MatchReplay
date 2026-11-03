// The browser's win probability must equal the Python reference (and so the Flink job) on every
// snapshot of the two parity fixtures, and the what-if must flip what it says it flips.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import zlib from "node:zlib";
import { WinModel, whatIf } from "../js/winprob.js";
import { Match } from "../js/replay.js";

const model = new WinModel(JSON.parse(fs.readFileSync(new URL("../../models/winprob.json", import.meta.url))));
const FIX = new URL("../../pipeline/src/test/resources/fixtures/", import.meta.url);

const flat = (s) => ({ time: s.time, period: s.period, score: s.score, h_red: s.home_stats.red, a_red: s.away_stats.red,
  h_xg: s.home_stats.xg, a_xg: s.away_stats.xg });

for (const id of [3922238, 3922242]) {
  test(`win probability matches the Python reference on every snapshot of ${id}`, () => {
    const lines = zlib.gunzipSync(fs.readFileSync(new URL(`${id}.expected.jsonl.gz`, FIX))).toString().trim().split("\n");
    let checked = 0;
    for (const line of lines) {
      const s = JSON.parse(line);
      const p = model.forSnapshot(flat(s), false);
      if (s.win_prob === null) { assert.equal(p, null); continue; }
      for (let k = 0; k < 3; k++) assert.ok(Math.abs(p[k] - s.win_prob[k]) <= 1.5e-4, `${id} t=${s.time}: ${p} vs ${s.win_prob}`);
      checked++;
    }
    assert.ok(checked > 500);
  });
}

test("probabilities add up, and a two-goal lead late on is nearly safe", () => {
  const late = { time: 60 * 60000 + 40 * 60000, period: 2, score: [2, 0], h_red: 0, a_red: 0, h_xg: 1.2, a_xg: 1.2 };
  const p = model.forSnapshot(late, false);
  assert.ok(Math.abs(p[0] + p[1] + p[2] - 1) < 1e-3);
  assert.ok(p[0] > 0.95, String(p));
});

const DATA = new URL("../data/matches/3922238.json", import.meta.url);
test("what if: taking away a goal changes the score from then on, and the simulation agrees with the exact odds", { skip: !fs.existsSync(DATA) }, () => {
  const m = new Match(JSON.parse(fs.readFileSync(DATA)), model);
  const goal = m.highlights.find((h) => h.kind === "goal");
  const w = whatIf(m, model, goal);
  const exact = model.outcome(w.moment.diff, w.moment.remaining, w.moment.home, w.moment.away, false, false);
  for (let k = 0; k < 3; k++) assert.ok(Math.abs(w.sim.outcome[k] - exact[k]) < 0.03, `${w.sim.outcome} vs ${exact}`);
  const real = m.snapshot(w.alt.time[0]);
  assert.equal(w.moment.diff, real.score[0] - real.score[1] + (goal.side === 0 ? -1 : 1));
});
