// Pure scoring functions, SPEC.md §3. No DOM, no network, no globals besides
// what's passed in -- these are unit tested directly under Node (tests/) and
// loaded as a plain <script> in the browser (see index.html).

function getMatchup(matchupMatrix, a, b) {
  const row = matchupMatrix[a];
  return row && row[b] !== undefined ? row[b] : 0;
}

function getSynergy(synergyMatrix, a, b) {
  const row = synergyMatrix[a];
  return row && row[b] !== undefined ? row[b] : 0.5; // neutral: no data = coinflip
}

// §3.1
function counterScore(candidateId, enemyIds, matchupMatrix, threatWeights) {
  let score = 0;
  for (const e of enemyIds) {
    score += getMatchup(matchupMatrix, candidateId, e) * (threatWeights[e] || 0);
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

// §3.5. counteredByEnemy: how hard the enemy team already counters this
// candidate (positive = enemy favored against candidate, i.e. bad for us).
function finalScore(parts, weights) {
  const w = weights.final_score;
  return (
    w.counter_score * parts.counterScore +
    w.synergy_score * parts.synergyScore +
    w.role_fit * parts.roleFit +
    w.hero_baseline * parts.heroBaseline +
    -w.countered_by_enemy_penalty * parts.counteredByEnemy
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

  const counterScoreVal = counterScore(candidateId, ctx.enemyTeamIds, data.matchupMatrix, threatWeights);
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
    return !best || v > best.value ? { heroId: e, value: v } : best;
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
  getSynergy,
  counterScore,
  synergyScore,
  avgMatchupAgainst,
  avgSynergyWithRest,
  pickOrderPriority,
  baseThreat,
  roleRelevance,
  computeThreatWeights,
  roleFit,
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
