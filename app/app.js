// UI wiring for the draft analyzer (SPEC.md §5). All the actual math lives
// in scoring.js -- this file just tracks draft state, renders it, and
// re-scores on every change. No network calls except the one-time load of
// the local data/*.json files below.

const ICON_BASE = "https://cdn.cloudflare.steamstatic.com";

const DATA_FILES = {
  heroes: "../data/heroes.json",
  items: "../data/items.json",
  matchupMatrix: "../data/matchup_matrix.json",
  synergyMatrix: "../data/synergy_matrix.json",
  heroBaseline: "../data/hero_baseline.json",
  heroRoleStats: "../data/hero_role_stats.json",
  itemCounters: "../data/item_counters.json",
  weights: "../data/weights.json",
  aliases: "../data/aliases.json",
  itemBuilds: "../data/item_builds.json",
  heroCounters: "../data/hero_specific_counters.json",
};

const BUILD_PHASES = [
  ["start_game_items", "Start"],
  ["early_game_items", "Early"],
  ["mid_game_items", "Mid"],
  ["late_game_items", "Late"],
];

const state = {
  role: "1",
  addMode: "yours",
  yourTeam: [],
  enemyTeam: [],
  banned: [],
  pickOrder: [], // hero ids, in the order they were picked (yours + enemy only)
  myHeroId: null, // which Your Team hero the Suggested Build panel targets; null = most recent pick
  search: "",
};

let data = null; // filled in by loadData()

async function loadData() {
  const entries = await Promise.all(
    Object.entries(DATA_FILES).map(async ([key, url]) => {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`failed to load ${url}: ${res.status}`);
      return [key, await res.json()];
    })
  );
  data = Object.fromEntries(entries);

  // reverse lookup for item icons: item_counters.json refers to items by
  // display name (curated text, sometimes not a real item at all -- e.g.
  // "Armor items (Vanguard/Solar Crest)") -- match against items.json's
  // localized_name where possible, fall back to a text label otherwise.
  data.itemsByName = {};
  for (const item of Object.values(data.items)) {
    data.itemsByName[item.localized_name] = item;
  }
}

function heroName(id) {
  return data.heroes[id] ? data.heroes[id].localized_name : id;
}

function heroIconUrl(id) {
  // "img" (256x144 bust portrait) reads far better at grid size than
  // "icon" (32x32 minimap icon, blurry once scaled up).
  const img = data.heroes[id] && data.heroes[id].img;
  return img ? ICON_BASE + img : "";
}

function itemIconUrl(itemDisplayName) {
  const item = data.itemsByName[itemDisplayName];
  return item && item.icon ? ICON_BASE + item.icon : null;
}

// search matches the hero's official name OR any curated alias (English
// nicknames/abbreviations, Russian names/nicknames -- data/aliases.json)
function heroSearchText(id) {
  const aliases = data.aliases[id] || [];
  return [heroName(id), ...aliases].join("   ").toLowerCase();
}

function isUsed(id) {
  return state.yourTeam.includes(id) || state.enemyTeam.includes(id) || state.banned.includes(id);
}

// which list a hero is already in, for a distinct visual per list (§5)
function heroStatus(id) {
  if (state.yourTeam.includes(id)) return "yours";
  if (state.enemyTeam.includes(id)) return "enemy";
  if (state.banned.includes(id)) return "banned";
  return null;
}

// which Your Team hero to show the build for: your explicit pick if it's
// still on the team, otherwise the most recent addition.
function effectiveMyHeroId() {
  if (state.myHeroId && state.yourTeam.includes(state.myHeroId)) return state.myHeroId;
  return state.yourTeam[state.yourTeam.length - 1] || null;
}

function ctxForScoring() {
  const pickOrderIndexByHero = {};
  state.pickOrder.forEach((id, i) => (pickOrderIndexByHero[id] = i));
  return {
    role: state.role,
    yourTeamIds: state.yourTeam,
    enemyTeamIds: state.enemyTeam,
    bannedIds: state.banned,
    pickOrderIndexByHero,
    totalPicks: state.pickOrder.length,
  };
}

// ---- actions ----

const MAX_TEAM_SIZE = 5; // a Dota side has 5 players -- yours/enemy can't exceed that

function addHero(id) {
  if (isUsed(id)) return;
  if (state.addMode === "yours") {
    if (state.yourTeam.length >= MAX_TEAM_SIZE) return;
    state.yourTeam.push(id);
  } else if (state.addMode === "enemy") {
    if (state.enemyTeam.length >= MAX_TEAM_SIZE) return;
    state.enemyTeam.push(id);
  } else {
    state.banned.push(id); // no cap -- any number of heroes can be banned/excluded
  }
  if (state.addMode !== "banned") state.pickOrder.push(id);
  renderAll();
}

function removeHero(id, listName) {
  state[listName] = state[listName].filter((h) => h !== id);
  state.pickOrder = state.pickOrder.filter((h) => h !== id);
  if (state.myHeroId === id) state.myHeroId = null;
  renderAll();
}

function selectMyHero(id) {
  state.myHeroId = state.myHeroId === id ? null : id; // click again to go back to "most recent pick"
  renderAll();
}

function resetTeams() {
  state.yourTeam = [];
  state.enemyTeam = [];
  state.banned = [];
  state.pickOrder = [];
  state.myHeroId = null;
  renderAll();
}

// ---- rendering ----

function renderTopBar() {
  document.querySelectorAll(".role-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.role === state.role);
  });
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === state.addMode);
  });
}

function renderGrid() {
  const grid = document.getElementById("hero-grid");
  grid.innerHTML = "";
  const heroIds = Object.keys(data.heroes).sort((a, b) =>
    heroName(a).localeCompare(heroName(b))
  );
  const term = state.search.trim().toLowerCase();

  for (const id of heroIds) {
    const el = document.createElement("div");
    el.className = "hero-icon";
    const status = heroStatus(id);
    if (status) el.classList.add("status-" + status);
    // every non-matching hero dims during a search, picked or not. A picked
    // hero that *does* match stays at its plain status brightness rather
    // than also getting the "matched" glow -- it's already spoken for, so
    // it doesn't need the extra highlight that free heroes get.
    if (term) {
      if (heroSearchText(id).includes(term)) {
        if (!status) el.classList.add("matched");
      } else {
        el.classList.add("dimmed");
      }
    }
    const badge = { yours: "Y", enemy: "E", banned: "B" }[status];
    el.innerHTML = `
      <img src="${heroIconUrl(id)}" alt="${heroName(id)}" loading="lazy" />
      ${badge ? `<span class="status-badge">${badge}</span>` : ""}
      <span class="name">${heroName(id)}</span>`;
    el.addEventListener("click", () => addHero(id));
    grid.appendChild(el);
  }
}

function renderChips(listName, containerId, countId) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  if (countId) {
    const full = state[listName].length >= MAX_TEAM_SIZE;
    document.getElementById(countId).textContent = `(${state[listName].length}/${MAX_TEAM_SIZE})`;
    document.getElementById(countId).classList.toggle("count-full", full);
  }
  const effectiveMyHero = listName === "yourTeam" ? effectiveMyHeroId() : null;
  for (const id of state[listName]) {
    const chip = document.createElement("div");
    chip.className = "chip";
    if (listName === "yourTeam") {
      chip.classList.add("chip-selectable");
      if (id === effectiveMyHero) chip.classList.add("chip-selected");
      chip.title = "Click to show this hero's build in Suggested Build";
      chip.addEventListener("click", () => selectMyHero(id));
    }
    chip.innerHTML = `<img src="${heroIconUrl(id)}" alt="" /><span>${heroName(id)}</span>`;
    const removeBtn = document.createElement("button");
    removeBtn.textContent = "×";
    removeBtn.setAttribute("aria-label", `Remove ${heroName(id)}`);
    removeBtn.addEventListener("click", (e) => {
      e.stopPropagation(); // don't also trigger the chip's own select-click
      removeHero(id, listName);
    });
    chip.appendChild(removeBtn);
    container.appendChild(chip);
  }
}

const THREAT_REASONS = [
  ["baseline", "high current win rate"],
  ["enabledBySynergy", "enabled by their team's synergy"],
  ["alreadyCountered", "not well covered by your picks yet"],
  ["pickOrder", "picked early -- likely their game plan's anchor"],
];

function whyText(breakdown) {
  let best = THREAT_REASONS[0];
  for (const entry of THREAT_REASONS) {
    if (Math.abs(breakdown[entry[0]]) > Math.abs(breakdown[best[0]])) best = entry;
  }
  return best[1];
}

function itemsForThreat(heroId, role) {
  return Scoring.suggestItems(heroId, role, data.heroes, data.itemCounters).slice(0, 5);
}

// Curated, paraphrased hero-vs-hero wiki notes (data/hero_specific_counters.json)
// -- supplementary context only, never fed into scoring.js's math. A matchup
// may be filed under either hero's own list (whichever wiki page covered it),
// so check both before giving up.
function findMatchupNote(a, b) {
  const fromA = (data.heroCounters[a] || []).find((e) => e.vs === b);
  if (fromA) return fromA;
  const fromB = (data.heroCounters[b] || []).find((e) => e.vs === a);
  if (fromB) return fromB;
  return null;
}

function matchupNoteHtml(note, opponentId) {
  if (!note) return "";
  const itemsSuffix = note.items && note.items.length ? ` (${note.items.join(", ")})` : "";
  return `<div class="matchup-note"><span class="matchup-note-label">vs ${heroName(opponentId)}:</span> ${note.note}${itemsSuffix}</div>`;
}

// shared item-pill renderer: icon when items.json has a matching entry,
// text-only pill otherwise (item_counters.json sometimes names a category
// like "Armor items (Vanguard/Solar Crest)" rather than a real item).
function itemPillHtml(displayName, titleSuffix) {
  const icon = itemIconUrl(displayName);
  const title = titleSuffix ? `${displayName} (${titleSuffix})` : displayName;
  return icon
    ? `<span class="item-pill" title="${title}"><img src="${icon}" alt="${displayName}" />${displayName}</span>`
    : `<span class="item-pill item-pill-text" title="${title}">${displayName}</span>`;
}

function renderThreats(threat) {
  const el = document.getElementById("threat-content");
  if (state.enemyTeam.length === 0) {
    el.className = "empty";
    el.textContent = "Add enemy heroes to see threats.";
    return;
  }
  el.className = "";
  el.innerHTML = "";

  const ranked = [...state.enemyTeam].sort(
    (a, b) => threat.normalized[b] - threat.normalized[a]
  );
  for (const id of ranked.slice(0, 2)) {
    const items = itemsForThreat(id, state.role);
    const itemsHtml = items.length
      ? items.map((i) => itemPillHtml(i.item, i.tier)).join("")
      : `<span class="empty">no item rules for this hero's tags</span>`;

    // curated note: how this threat matches up against a hero already on
    // Your Team, if any such wiki-sourced pair exists (first match wins).
    let noteHtml = "";
    for (const allyId of state.yourTeam) {
      const note = findMatchupNote(id, allyId);
      if (note) {
        noteHtml = matchupNoteHtml(note, allyId);
        break;
      }
    }

    const div = document.createElement("div");
    div.className = "threat-hero";
    div.innerHTML = `
      <img src="${heroIconUrl(id)}" alt="" />
      <div class="threat-hero-info">
        <div class="name">${heroName(id)} <span class="threat-pct">(${Math.round(threat.normalized[id] * 100)}% of enemy threat)</span></div>
        <div class="why">${whyText(threat.breakdowns[id])}</div>
        <div class="threat-items">${itemsHtml}</div>
        ${noteHtml}
      </div>`;
    el.appendChild(div);
  }
}

// build order for whichever Your Team hero is selected -- click a chip in
// Your Team to pick it explicitly, otherwise defaults to the most recent
// pick (effectiveMyHeroId). Real per-phase item pick rates from OpenDota,
// not curated/AI-generated.
function renderBuild() {
  const el = document.getElementById("build-content");
  const heroId = effectiveMyHeroId();
  if (!heroId) {
    el.className = "empty";
    el.textContent = "Add your own hero to Your Team to see its build.";
    return;
  }
  const build = data.itemBuilds[heroId];
  if (!build) {
    el.className = "empty";
    el.textContent = `No build data for ${heroName(heroId)}.`;
    return;
  }

  el.className = "";
  const hint = state.yourTeam.length > 1
    ? `<span class="build-hint">click a hero in Your Team to switch</span>`
    : "";
  el.innerHTML = `<div class="build-hero-name">${heroName(heroId)} ${hint}</div>`;
  for (const [key, label] of BUILD_PHASES) {
    const items = build[key] || [];
    const row = document.createElement("div");
    row.className = "build-row";
    row.innerHTML = `
      <span class="build-phase-label">${label}</span>
      <span class="build-items">${
        items.length ? items.map((name) => itemPillHtml(name)).join("") : `<span class="empty">no data</span>`
      }</span>`;
    el.appendChild(row);
  }
}

function renderCandidates(candidates, threat) {
  const list = document.getElementById("candidates-list");
  if (state.yourTeam.length === 0 && state.enemyTeam.length === 0) {
    list.className = "empty";
    list.textContent = "Pick your role and add heroes to see suggestions.";
    return;
  }
  if (Object.keys(data.heroRoleStats).length === 0) {
    list.className = "empty";
    list.textContent = "hero_role_stats.json is empty (needs a STRATZ API key in the pipeline) -- role fit can't be computed, so no candidates pass the filter yet.";
    return;
  }
  list.className = "";
  list.innerHTML = "";

  const shown = candidates.slice(0, 8);
  // the raw finalScore is an unbounded weighted sum (SPEC.md §3.5) -- not a
  // percentage or a 0-1 scale, only meaningful *relative to the rest of this
  // list*. So show rank + a relative bar instead of the bare number; the
  // number itself is still available on hover for anyone who wants it.
  const maxScore = shown.length ? shown[0].finalScore : 0;
  const minScore = shown.length ? shown[shown.length - 1].finalScore : 0;
  const range = maxScore - minScore;

  // enemy heroes ordered biggest-threat-first, so the note shown next to a
  // candidate (if any exist) is against the enemy that matters most.
  const enemiesByThreat = [...state.enemyTeam].sort(
    (a, b) => (threat.normalized[b] || 0) - (threat.normalized[a] || 0)
  );

  shown.forEach((c, i) => {
    const parts = [];
    if (c.bestCounter && c.bestCounter.value > 0) {
      parts.push(`${c.bestCounter.value >= 0 ? "+" : ""}${Math.round(c.bestCounter.value * 100)}% vs their ${heroName(c.bestCounter.heroId)}`);
    }
    if (c.bestSynergy && c.bestSynergy.value > 0.5) {
      parts.push(`strong synergy with your ${heroName(c.bestSynergy.heroId)}`);
    }
    const reason = parts.join(", ") || "solid all-round pick for this role";

    // curated note: how this candidate matches up against the biggest
    // matching enemy threat, if any such wiki-sourced pair exists.
    let noteHtml = "";
    for (const enemyId of enemiesByThreat) {
      const note = findMatchupNote(c.heroId, enemyId);
      if (note) {
        noteHtml = matchupNoteHtml(note, enemyId);
        break;
      }
    }

    // min bar width 12% so even the last-place shown candidate reads as a bar, not a sliver
    const barPct = range > 0 ? 12 + ((c.finalScore - minScore) / range) * 88 : 100;
    const scoreTitle =
      `Overall pick score: ${c.finalScore.toFixed(3)} (raw, not a percentage -- ` +
      `only meaningful relative to others in this list). Combines counter (35%), ` +
      `synergy (25%), role fit (20%), baseline strength (10%), minus how hard ` +
      `the enemy counters this pick (10%).`;

    const li = document.createElement("li");
    li.className = "candidate";
    li.title = scoreTitle;
    li.innerHTML = `
      <span class="rank">#${i + 1}</span>
      <img src="${heroIconUrl(c.heroId)}" alt="" />
      <div class="info">
        <div class="name">${heroName(c.heroId)}</div>
        <div class="reason">${reason}</div>
        <div class="score-bar"><div class="score-bar-fill" style="width:${barPct}%"></div></div>
        ${noteHtml}
      </div>`;
    list.appendChild(li);
  });
}

function renderAll() {
  renderTopBar();
  renderGrid();
  renderChips("yourTeam", "yours-chips", "yours-count");
  renderChips("enemyTeam", "enemy-chips", "enemy-count");
  renderChips("banned", "banned-chips");

  renderBuild();

  const { candidates, threat } = Scoring.rankCandidates(ctxForScoring(), data);
  renderThreats(threat);
  renderCandidates(candidates, threat);
}

function wireControls() {
  document.querySelectorAll(".role-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.role = btn.dataset.role;
      renderAll();
    });
  });
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.addMode = btn.dataset.mode;
      renderTopBar();
    });
  });
  document.getElementById("reset-btn").addEventListener("click", resetTeams);
  const searchBox = document.getElementById("search");

  // Dota client hero-picker behavior: keystrokes within SEARCH_IDLE_MS of
  // each other accumulate into one search term; once you go quiet for that
  // long, the term is dropped, so the next keystroke starts a fresh search
  // instead of resuming the stale one.
  const SEARCH_IDLE_MS = 1500;
  let idleTimer = null;
  function touchSearch() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => setSearch(""), SEARCH_IDLE_MS);
  }
  function setSearch(value) {
    state.search = value;
    searchBox.value = value;
    renderGrid();
  }

  searchBox.addEventListener("input", (e) => {
    setSearch(e.target.value);
    if (e.target.value) touchSearch();
    else clearTimeout(idleTimer);
  });
  searchBox.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      clearTimeout(idleTimer);
      setSearch("");
      searchBox.blur();
    }
  });

  // type-to-filter without clicking the search box first (like the Dota
  // client's hero picker) -- only when focus isn't already in a text field,
  // so it doesn't fight with normal typing there.
  document.addEventListener("keydown", (e) => {
    const active = document.activeElement;
    if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.key === "Escape") {
      clearTimeout(idleTimer);
      setSearch("");
      return;
    } else if (e.key === "Backspace") {
      setSearch(state.search.slice(0, -1));
    } else if (e.key.length === 1) {
      setSearch(state.search + e.key);
    } else {
      return;
    }
    e.preventDefault();
    touchSearch();
  });
}

async function main() {
  wireControls();
  try {
    await loadData();
  } catch (err) {
    document.getElementById("hero-grid").textContent =
      "Failed to load data/*.json -- serve this folder over http (see README/CLAUDE.md), don't open index.html directly. " + err.message;
    return;
  }
  renderAll();
}

main();
