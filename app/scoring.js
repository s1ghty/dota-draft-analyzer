// Pure scoring functions, SPEC.md §3. No DOM, no network, no globals besides
// what's passed in -- these are unit tested directly under Node (tests/) and
// loaded as a plain <script> in the browser (see index.html).

function getMatchup(matchupMatrix, a, b) {
  const row = matchupMatrix[a];
  const cell = row && row[b] !== undefined ? row[b] : 0;
  // matchup_matrix.json entries are either a bare shrunk-delta number (old
  // pipeline output, still committed) or {delta, games} (new -- games kept
  // for judging sample-size confidence, not used in scoring math itself).
  return typeof cell === "object" ? cell.delta : cell;
}

function getMatchupGames(matchupMatrix, a, b) {
  const row = matchupMatrix[a];
  const cell = row && row[b] !== undefined ? row[b] : 0;
  return typeof cell === "object" ? cell.games : null; // null = old-format data, sample size unknown
}

function getSynergy(synergyMatrix, a, b) {
  const row = synergyMatrix[a];
  return row && row[b] !== undefined ? row[b] : 0.5; // neutral: no data = coinflip
}

// Does a curated, wiki-sourced counters_enemy relationship exist for
// candidate vs enemy, in either direction it could be filed under
// (hero_specific_counters.json's third data layer)? Mirrors app.js's
// findCounterNote() but as a boolean, since counterScore() only needs to
// know whether to apply the bonus, not the note text.
function hasKitCounter(heroCounters, candidateId, enemyId) {
  if (!heroCounters) return false;
  const ownEntry = (heroCounters[candidateId] || []).some(
    (e) => e.vs === enemyId && e.direction === "counters_enemy"
  );
  if (ownEntry) return true;
  return (heroCounters[enemyId] || []).some(
    (e) => e.vs === candidateId && e.direction === "countered_by"
  );
}

// §3.1. heroCounters/kitCounterBonus are optional: when given, a known kit
// counter the raw matchup stats haven't caught up to yet (thin/noisy
// sample) gets a modest per-enemy nudge on top of the real delta -- not an
// override, since it's added before the threat-weighting and the final
// -1..1 normalization, so it can't swing further than a single strong real
// matchup would. See weights.json's kit_counter_bonus for the magnitude,
// and CLAUDE.md/this session's Anti-Mage vs Morphling case for why this
// exists: a real, curated counter interaction the stats currently disagree
// with (added 2026-08-26).
function counterScore(candidateId, enemyIds, matchupMatrix, threatWeights, heroCounters, kitCounterBonus) {
  let score = 0;
  for (const e of enemyIds) {
    let delta = getMatchup(matchupMatrix, candidateId, e);
    if (kitCounterBonus && hasKitCounter(heroCounters, candidateId, e)) {
      delta += kitCounterBonus;
    }
    score += delta * (threatWeights[e] || 0);
  }
  return score;
}

// §3.2
function synergyScore(candidateId, teamIds, synergyMatrix) {
  if (teamIds.length === 0) return 0.5;
  const sum = teamIds.reduce((acc, t) => acc + getSynergy(synergyMatrix, candidateId, t), 0);
  return sum / teamIds.length;
}

// average matchup of `heroIds` against `against` -- positive = heroIds favored
function avgMatchupAgainst(matchupMatrix, heroIds, against) {
  if (heroIds.length === 0) return 0;
  const sum = heroIds.reduce((acc, h) => acc + getMatchup(matchupMatrix, h, against), 0);
  return sum / heroIds.length;
}

function avgSynergyWithRest(synergyMatrix, enemyId, restOfEnemyTeam) {
  if (restOfEnemyTeam.length === 0) return 0.5;
  const sum = restOfEnemyTeam.reduce((acc, o) => acc + getSynergy(synergyMatrix, enemyId, o), 0);
  return sum / restOfEnemyTeam.length;
}

// §3.3a. pickOrderIndex: 0-based position this hero was picked in the draft
// (0 = first pick). totalPicks: how many heroes have been picked so far.
// Earlier pick -> higher priority. No picks yet -> neutral 0.5.
function pickOrderPriority(pickOrderIndex, totalPicks) {
  if (totalPicks <= 1) return 0.5;
  return (totalPicks - 1 - pickOrderIndex) / (totalPicks - 1);
}

// §3.3a broken into named components so the UI can say *why* a hero is the
// biggest threat (baseline strength / enabled by synergy / uncountered).
function baseThreatBreakdown(enemyId, ctx, weights) {
  const { enemyTeamIds, yourTeamIds, heroBaseline, synergyMatrix, matchupMatrix, pickOrderIndex, totalPicks } = ctx;
  const restOfTheirTeam = enemyTeamIds.filter((h) => h !== enemyId);

  const w = weights.base_threat;
  const baseline = w.hero_baseline * (heroBaseline[enemyId] !== undefined ? heroBaseline[enemyId] : 0.5);
  const enabledBySynergy = w.synergy_with_their_team * avgSynergyWithRest(synergyMatrix, enemyId, restOfTheirTeam);
  // negative when your picks already favor you against this enemy (spec §3.3a)
  const alreadyCountered = w.countered_by_your_team * -avgMatchupAgainst(matchupMatrix, yourTeamIds, enemyId);
  const pickOrder = w.pick_order_priority * pickOrderPriority(pickOrderIndex, totalPicks);

  return {
    baseline,
    enabledBySynergy,
    alreadyCountered,
    pickOrder,
    total: baseline + enabledBySynergy + alreadyCountered + pickOrder,
  };
}

function baseThreat(enemyId, ctx, weights) {
  return baseThreatBreakdown(enemyId, ctx, weights).total;
}

// §3.3b. A hero can carry multiple threat_profile tags; average their
// relevance for this role. No tags -> neutral 1.0 (spec doesn't define this
// case; untagged heroes shouldn't be silently zeroed out of threat scoring).
function roleRelevance(threatProfileTags, role, roleRelevanceTable) {
  if (!threatProfileTags || threatProfileTags.length === 0) return 1.0;
  const values = threatProfileTags
    .map((tag) => roleRelevanceTable[tag] && roleRelevanceTable[tag][role])
    .filter((v) => v !== undefined);
  if (values.length === 0) return 1.0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

// §3.3 end-to-end: base_threat * role_relevance for every enemy, then
// normalized so weights sum to 1 across the enemy team. Returns
// { [enemyId]: normalizedWeight }, plus the raw threat components are
// available via computeThreatBreakdown for the UI's "why" callout.
function computeThreatWeights(ctx, heroThreatProfiles, weights) {
  const raw = {};
  const breakdowns = {};
  for (let i = 0; i < ctx.enemyTeamIds.length; i++) {
    const enemyId = ctx.enemyTeamIds[i];
    const enemyCtx = { ...ctx, pickOrderIndex: ctx.pickOrderIndexByHero[enemyId] };
    const breakdown = baseThreatBreakdown(enemyId, enemyCtx, weights);
    const rel = roleRelevance(heroThreatProfiles[enemyId], ctx.role, weights.role_relevance_table);
    raw[enemyId] = breakdown.total * rel;
    breakdowns[enemyId] = breakdown;
  }
  const total = Object.values(raw).reduce((a, b) => a + b, 0);
  const normalized = {};
  for (const enemyId of ctx.enemyTeamIds) {
    normalized[enemyId] = total > 0 ? raw[enemyId] / total : 1 / ctx.enemyTeamIds.length;
  }
  return { raw, normalized, breakdowns };
}

// §3.4. Returns null when the hero is filtered out for near-zero pickrate at
// this role -- caller must exclude these from the candidate list entirely.
function roleFit(candidateId, role, roleStatsMap, pickrateFloor, maxPickrateAtRole) {
  const stats = roleStatsMap[candidateId] && roleStatsMap[candidateId][role];
  if (!stats || stats.pickrate < pickrateFloor) return null;

  const winrateComponent = Math.max(-1, Math.min(1, (stats.winrate - 0.5) * 2));
  const pickrateComponent = maxPickrateAtRole > 0
    ? Math.max(0, Math.min(1, stats.pickrate / maxPickrateAtRole))
    : 0;
  // ponytail: 0.7/0.3 split is a reasonable-looking default, not derived --
  // spec only says "normalized(winrate, pickrate)" without a formula. Tune
  // once real STRATZ data shows this over/under-valuing pick rate.
  return 0.7 * winrateComponent + 0.3 * pickrateComponent;
}

// Every §3.5 component is centered at a neutral value and expressed as "how
// far from neutral" before it reaches here: counter_score/counteredByEnemy
// are already win-rate deltas (neutral 0), synergy_score/hero_baseline are
// raw win rates (neutral 0.5). Map all of them onto the same -1..1 scale
// with the same *2-from-neutral transform roleFit already uses for its own
// winrate term, so a 0.35 weight and a 0.20 weight are comparing like
// quantities instead of a fractional win-rate delta against a term that
// natively swings close to +-1 -- role_fit was swamping the matchup signal
// before this normalization (found 2026-08-26: Sniper's much higher
// unnormalized role_fit outranked Puck despite a much weaker counter into
// the same enemy).
function normalizeFromNeutral(value, neutral) {
  return Math.max(-1, Math.min(1, (value - neutral) * 2));
}

function finalScore(parts, weights) {
  const w = weights.final_score;
  const counterNorm = normalizeFromNeutral(parts.counterScore, 0);
  const synergyNorm = normalizeFromNeutral(parts.synergyScore, 0.5);
  const roleFitNorm = Math.max(-1, Math.min(1, parts.roleFit)); // already -1..1-shaped
  const baselineNorm = normalizeFromNeutral(parts.heroBaseline, 0.5);
  const counteredByNorm = normalizeFromNeutral(parts.counteredByEnemy, 0);
  return (
    w.counter_score * counterNorm +
    w.synergy_score * synergyNorm +
    w.role_fit * roleFitNorm +
    w.hero_baseline * baselineNorm +
    -w.countered_by_enemy_penalty * counteredByNorm
  );
}

// Ties everything together for one candidate hero. ctx carries the draft
// state (see computeThreatWeights); heroes/data are the loaded JSON files.
function scoreCandidate(candidateId, ctx, threatWeights, data) {
  const roleFitVal = roleFit(
    candidateId,
    ctx.role,
    data.heroRoleStats,
    data.weights.role_fit_pickrate_floor,
    ctx.maxPickrateAtRole
  );
  if (roleFitVal === null) return null; // excluded, not just penalized (§3.4)

  const counterScoreVal = counterScore(
    candidateId,
    ctx.enemyTeamIds,
    data.matchupMatrix,
    threatWeights,
    data.heroCounters,
    data.weights.kit_counter_bonus
  );
  const synergyScoreVal = synergyScore(candidateId, ctx.yourTeamIds, data.synergyMatrix);
  const heroBaselineVal = data.heroBaseline[candidateId] !== undefined ? data.heroBaseline[candidateId] : 0.5;
  const counteredByEnemyVal = avgMatchupAgainst(data.matchupMatrix, ctx.enemyTeamIds, candidateId);

  const final = finalScore(
    {
      counterScore: counterScoreVal,
      synergyScore: synergyScoreVal,
      roleFit: roleFitVal,
      heroBaseline: heroBaselineVal,
      counteredByEnemy: counteredByEnemyVal,
    },
    data.weights
  );

  // for the UI's one-line reason ("+14% vs their Storm Spirit, strong synergy
  // with your Ogre Magi", spec §5) -- the single best contributor of each kind.
  const bestCounter = ctx.enemyTeamIds.reduce((best, e) => {
    const v = getMatchup(data.matchupMatrix, candidateId, e);
    if (!best || v > best.value) {
      return { heroId: e, value: v, games: getMatchupGames(data.matchupMatrix, candidateId, e) };
    }
    return best;
  }, null);
  const bestSynergy = ctx.yourTeamIds.reduce((best, t) => {
    const v = getSynergy(data.synergyMatrix, candidateId, t);
    return !best || v > best.value ? { heroId: t, value: v } : best;
  }, null);

  return {
    heroId: candidateId,
    finalScore: final,
    counterScore: counterScoreVal,
    synergyScore: synergyScoreVal,
    roleFit: roleFitVal,
    heroBaseline: heroBaselineVal,
    counteredByEnemy: counteredByEnemyVal,
    bestCounter,
    bestSynergy,
  };
}

// Full ranked candidate list for the UI (§3.5, §5). Excludes picked/banned
// heroes and anyone filtered out by role_fit. `data` = the loaded JSON files
// (heroes, matchupMatrix, synergyMatrix, heroBaseline, heroRoleStats,
// weights) plus itemCounters for suggestions elsewhere.
function rankCandidates(ctx, data) {
  const excluded = new Set([...ctx.yourTeamIds, ...ctx.enemyTeamIds, ...(ctx.bannedIds || [])]);
  const allHeroIds = Object.keys(data.heroes);

  const heroThreatProfiles = {};
  for (const hid of allHeroIds) heroThreatProfiles[hid] = data.heroes[hid].threat_profile;

  const threatCtx = {
    ...ctx,
    heroBaseline: data.heroBaseline,
    synergyMatrix: data.synergyMatrix,
    matchupMatrix: data.matchupMatrix,
  };
  const threat = computeThreatWeights(threatCtx, heroThreatProfiles, data.weights);

  const maxPickrateAtRole = allHeroIds.reduce((max, hid) => {
    const stats = data.heroRoleStats[hid] && data.heroRoleStats[hid][ctx.role];
    return stats ? Math.max(max, stats.pickrate) : max;
  }, 0);

  const scored = [];
  for (const hid of allHeroIds) {
    if (excluded.has(hid)) continue;
    const result = scoreCandidate(hid, { ...ctx, maxPickrateAtRole }, threat.normalized, data);
    if (result) scored.push(result);
  }

  scored.sort((a, b) => b.finalScore - a.finalScore);
  return { candidates: scored, threat };
}

// §4: hero tags -> item rules, deduped, ordered by budget tier for the role
// (pos 4/5: cheap first; pos 1/2/3: mid/luxury first, cheap as filler).
function suggestItems(heroId, role, heroes, itemCounters) {
  const tags = (heroes[heroId] && heroes[heroId].tags) || [];
  const seen = new Set();
  const items = [];
  for (const tag of tags) {
    for (const entry of itemCounters[tag] || []) {
      if (seen.has(entry.item)) continue;
      seen.add(entry.item);
      items.push(entry);
    }
  }
  const tierOrder = ["4", "5"].includes(role)
    ? { cheap: 0, mid: 1, luxury: 2 }
    : { mid: 0, luxury: 1, cheap: 2 };
  items.sort((a, b) => tierOrder[a.tier] - tierOrder[b.tier]);
  return items;
}

const api = {
  getMatchup,
  getMatchupGames,
  getSynergy,
  hasKitCounter,
  counterScore,
  synergyScore,
  avgMatchupAgainst,
  avgSynergyWithRest,
  pickOrderPriority,
  baseThreat,
  roleRelevance,
  computeThreatWeights,
  roleFit,
  normalizeFromNeutral,
  finalScore,
  scoreCandidate,
  rankCandidates,
  suggestItems,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
} else {
  window.Scoring = api;
}
