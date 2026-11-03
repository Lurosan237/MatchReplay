// The Lab's miniature of the streaming job: the same guarantees the Flink job is tested for.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { Match } from "../js/replay.js";
import { messages, chaos, run, reference, differences } from "../js/streamlab.js";

const DIR = new URL("../data/matches/", import.meta.url);
const files = fs.existsSync(DIR) ? fs.readdirSync(DIR).filter((f) => f.endsWith(".json")).slice(0, 8) : [];
const load = (f) => new Match(JSON.parse(fs.readFileSync(new URL(f, DIR))));

test("a messy feed gives exactly the perfect-order numbers when the allowed delay covers it", { skip: !files.length }, () => {
  for (const f of files) {
    const m = load(f), msgs = messages(m), ref = reference(msgs);
    const out = run(chaos(msgs, { lateShare: 0.2, maxDelayMs: 8000, duplicateShare: 0.05, seed: 3 }), 8000);
    assert.equal(out.late.length, 0, f);
    assert.ok(out.duplicates > 0, f);
    assert.equal(differences(out.snaps, ref), 0, f);
    // and the final score is the real one
    assert.deepEqual(ref[ref.length - 1].goals, [m.meta.home_score, m.meta.away_score], f);
  }
});

test("too small an allowance sends late events to the side output, and the numbers drift", { skip: !files.length }, () => {
  const m = load(files[0]), msgs = messages(m);
  const out = run(chaos(msgs, { lateShare: 0.2, maxDelayMs: 8000, duplicateShare: 0, seed: 3 }), 1000);
  assert.ok(out.late.length > 0);
  assert.ok(differences(out.snaps, reference(msgs)) > 0);
});
