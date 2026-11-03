// The replay model, run on real exported matches (python -m matchreplay.web_export --only afcon2023).
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { Match, lastAtOrBefore } from "../js/replay.js";

const DIR = new URL("../data/matches/", import.meta.url);
const files = fs.existsSync(DIR) ? fs.readdirSync(DIR).filter((f) => f.endsWith(".json")) : [];
const load = (f) => new Match(JSON.parse(fs.readFileSync(new URL(f, DIR))));
const has = files.length > 0;

test("binary search", () => {
  assert.equal(lastAtOrBefore([1, 3, 5], 0), -1);
  assert.equal(lastAtOrBefore([1, 3, 5], 3), 1);
  assert.equal(lastAtOrBefore([1, 3, 5], 4), 1);
  assert.equal(lastAtOrBefore([1, 3, 5], 9), 2);
});

test("every match: final snapshot has the real score, and the goals in the feed add up to it", { skip: !has }, () => {
  for (const f of files) {
    const m = load(f);
    const s = m.snapshot(m.end + 60000);
    assert.deepEqual(s.score, [m.meta.home_score, m.meta.away_score], `${f} ${m.meta.home} v ${m.meta.away}`);
    const goals = m.goals();
    // An own goal is credited to the other team
    const home = goals.filter((g) => (g.kind === "goal" && g.side === 0) || (g.kind === "owngoal" && g.side === 1)).length;
    const away = goals.length - home;
    assert.deepEqual([home, away], s.score, `${f} goals in the feed`);
  }
});

test("the timeline maps played time back and forth, skipping the breaks", { skip: !has }, () => {
  for (const f of files.slice(0, 10)) {
    const m = load(f);
    for (const x of [0, 1000, m.duration / 3, m.duration / 2, m.duration - 1]) {
      assert.ok(Math.abs(m.toPlayed(m.fromPlayed(x)) - x) < 1e-6, `${f} at ${x}`);
    }
    // Playing from kick-off reaches the end, passing through every period in order
    let t = m.start, periods = [m.segmentAt(t).period], steps = 0;
    for (;;) {
      const step = m.advance(t, 20000);
      t = step.t;
      if (step.jumped) periods.push(step.jumped.period);
      if (step.finished || ++steps > 100000) break;
    }
    assert.deepEqual(periods, m.segments.map((s) => s.period), f);
  }
});

test("the clock reads like a broadcast clock", { skip: !has }, () => {
  const m = load(files[0]);
  assert.equal(m.clock(m.segments[0].start), "00:00");
  const second = m.segments.find((s) => s.period === 2);
  assert.match(m.clock(second.start + 61000), /^4[56]:\d\d$/);
});

test("the ball never leaves the drawing", { skip: !has }, () => {
  for (const f of files.slice(0, 10)) {
    const m = load(f);
    for (let x = 0; x < m.duration; x += 7000) {
      const b = m.ball(m.fromPlayed(x));
      assert.ok(b.x >= -5 && b.x <= 125 && b.y >= -5 && b.y <= 85, `${f} ${JSON.stringify(b)}`);
    }
  }
});
