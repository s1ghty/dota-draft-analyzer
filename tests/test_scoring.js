// Minimal self-check for app/scoring.js (SPEC.md §6 step 5): no framework,
// just assert. Run with: node tests/test_scoring.js
const assert = require("assert");
const path = require("path");
const fs = require("fs");
const Scoring = require("../app/scoring.js");

const weights = JSON.parse(fs.readFileSync(path.join(__dirname, "../data/weights.json")));

// --- Fixture: a fake draft where two candidates should swap rank between
// pos 1 and pos 5 against the SAME enemy team, driven purely by role
// relevance + role fit (this is the exact check SPEC.md §6 step 5 asks for).
//
// E1 is a "ganker" (role_relevance favors pos 4/5 threat weighting),
// E2 is a "lane-bully" (role_relevance favors pos 1/2 threat weighting).
// C1 counters E1 and is only viable at pos 1; C2 counters E2 and is only
// viable at pos 5 -- so C1 should win at pos 1, C2 should win at pos 5.
const heroes = {
  E1: { threat_profile: ["ganker"] },
  E2: { threat_profile: ["lane-bully"] },
  C1: { threat_profile: [] },
  C2: { threat_profile: [] },
  T1: { threat_profile: [] },
};

const matchupMatrix = {
  C1: { E1: 0.10 },
  C2: { E2: 0.10 },
};

const synergyMatrix = {}; // no data -> neutral 0.5 for everyone, doesn't bias the comparison

const heroBaseline = { E1: 0.55, E2: 0.55, C1: 0.5, C2: 0.5, T1: 0.5 };

const heroRoleStats = {
  C1: { "1": { winrate: 0.55, pickrate: 0.20 }, "5": { winrate: 0.45, pickrate: 0.02 } },
  C2: { "1": { winrate: 0.45, pickrate: 0.02 }, "5": { winrate: 0.55, pickrate: 0.20 } },
};

const data = { heroes, matchupMatrix, synergyMatrix, heroBaseline, heroRoleStats, weights };

function baseCtx(role) {
  return {
    role,
    yourTeamIds: ["T1"],
    enemyTeamIds: ["E1", "E2"],
    bannedIds: [],
    pickOrderIndexByHero: { E1: 0, E2: 1 },
  };
}

const pos1Ranked = Scoring.rankCandidates(baseCtx("1"), data).candidates;
const pos5Ranked = Scoring.rankCandidates(baseCtx("5"), data).candidates;

const pos1Top = pos1Ranked[0].heroId;
const pos5Top = pos5Ranked[0].heroId;

assert.strictEqual(pos1Top, "C1", `expected C1 to top pos1 ranking, got ${pos1Top}`);
assert.strictEqual(pos5Top, "C2", `expected C2 to top pos5 ranking, got ${pos5Top}`);
assert.notStrictEqual(pos1Top, pos5Top, "pos1 and pos5 recommendations should diverge for the same enemy draft");

// --- §3.4: near-zero pickrate at a role must exclude the hero entirely,
// not just score it low.
const dataWithDeadPick = {
  ...data,
  heroRoleStats: {
    ...heroRoleStats,
    C2: { ...heroRoleStats.C2, "1": { winrate: 0.9, pickrate: 0.0001 } }, // great winrate, never played pos1
  },
};
const pos1WithDeadPick = Scoring.rankCandidates(baseCtx("1"), dataWithDeadPick).candidates;
assert.ok(
  !pos1WithDeadPick.some((r) => r.heroId === "C2"),
  "hero with near-zero pickrate at this role must be filtered out, not just penalized"
);

// --- §4: item tier ordering flips by role, and items are deduped across tags.
const testHeroes = { X: { tags: ["disable-heavy", "magic-burst"] } };
const testItemCounters = {
  "disable-heavy": [
    { item: "Black King Bar", tier: "mid" },
    { item: "Force Staff", tier: "cheap" },
  ],
  "magic-burst": [
    { item: "Black King Bar", tier: "mid" }, // duplicate, should be deduped
    { item: "Aeon Disk", tier: "luxury" },
  ],
};

const pos5Items = Scoring.suggestItems("X", "5", testHeroes, testItemCounters);
assert.deepStrictEqual(
  pos5Items.map((i) => i.item),
  ["Force Staff", "Black King Bar", "Aeon Disk"],
  "pos4/5 should show cheap items first"
);

const pos1Items = Scoring.suggestItems("X", "1", testHeroes, testItemCounters);
assert.deepStrictEqual(
  pos1Items.map((i) => i.item),
  ["Black King Bar", "Aeon Disk", "Force Staff"],
  "pos1/2/3 should show mid/luxury first, cheap as filler"
);
assert.strictEqual(pos1Items.length, 3, "duplicate item across tags should be deduped, not listed twice");

// --- matchup_matrix.json cells can be a bare shrunk-delta number (old
// pipeline output, still committed) or {delta, games} (new) -- getMatchup
// must read the delta out of either shape, getMatchupGames only knows the
// sample size for the new shape.
const mixedMatrix = { A: { B: 0.12, C: { delta: 0.08, games: 40 } } };
assert.strictEqual(Scoring.getMatchup(mixedMatrix, "A", "B"), 0.12, "getMatchup should read old bare-number shape");
assert.strictEqual(Scoring.getMatchup(mixedMatrix, "A", "C"), 0.08, "getMatchup should read new {delta,games} shape");
assert.strictEqual(Scoring.getMatchupGames(mixedMatrix, "A", "B"), null, "old bare-number shape has no known sample size");
assert.strictEqual(Scoring.getMatchupGames(mixedMatrix, "A", "C"), 40, "getMatchupGames should read games from new shape");

// --- §3.5 normalization: components must be on a comparable scale before
// weighting, so a big counter_score edge isn't swamped by a role_fit value
// that natively swings much closer to +-1 than a shrunk matchup delta does.
// This is the exact Sniper-vs-Puck-at-pos-2-vs-Invoker case that surfaced
// the bug: Puck has the much stronger matchup into the same enemy but a
// far weaker role_fit -- after normalization, its counter edge should win.
assert.strictEqual(Scoring.normalizeFromNeutral(0.25, 0), 0.5, "delta 0.25 from neutral 0 should map to 0.5 on the -1..1 scale");
assert.strictEqual(Scoring.normalizeFromNeutral(0.75, 0.5), 0.5, "winrate 0.75 vs neutral 0.5 should map to 0.5 on the -1..1 scale");
assert.strictEqual(Scoring.normalizeFromNeutral(10, 0), 1, "normalization must clamp, not just scale");

const sniperCounterHeavy = Scoring.finalScore(
  { counterScore: 0.035, synergyScore: 0.5, roleFit: 0.1935, heroBaseline: 0.5034, counteredByEnemy: -0.035 },
  weights
);
const puckCounterHeavy = Scoring.finalScore(
  { counterScore: 0.0867, synergyScore: 0.5, roleFit: 0.025, heroBaseline: 0.4614, counteredByEnemy: -0.0867 },
  weights
);
assert.ok(puckCounterHeavy > sniperCounterHeavy, "a much stronger counter into the same enemy should win once role_fit is normalized onto a comparable scale");

console.log("all scoring tests passed");
