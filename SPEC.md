# Dota 2 Draft Analyzer — Technical Spec

Personal tool. Free, offline after build, fast, pos-1-focused draft assistant.
Give this whole file to Claude Code as-is. It should write CLAUDE.md itself
after reading this.

---

## 1. Data Pipeline (build once, re-run only after major patches)

Run a one-time Python script that pulls and caches everything into local JSON.
Nothing below runs live during an actual draft — that's the whole point.

**Sources:**
- `dotaconstants` (GitHub, npm/pip package) → hero list, item list, icons, hero
  roles/attributes, ability behaviors.
- OpenDota API `/heroes/{id}/matchups` → for every hero, win rate **against**
  every other hero when they're on opposing teams. This is your raw counter data.
- OpenDota Explorer (SQL endpoint) → custom query joining `picks_bans` for
  hero pairs **on the same team** in wins, to build a synergy table (win rate
  when two heroes are picked together). This isn't a prebuilt endpoint — it's
  a SQL query against their public match dataset. Cache the result.
- OpenDota `/heroStats` → overall hero win rate / pick rate, useful as a
  baseline "how strong is this hero right now" signal.
- **STRATZ API (GraphQL, free tier)** → the key addition for role support.
  STRATZ breaks hero performance down **by position (1–5)**, which OpenDota
  mostly doesn't do well. Pull per-position win rate / pick rate / pick order
  for every hero here — this is what makes "is this hero even good at pos 4
  right now" possible, instead of just "is this hero good in general."
  Also has facet-level stats (heroes have multiple facets now that meaningfully
  change matchups) — worth pulling if the query supports it, skip if it adds
  too much complexity for v1.

  Not recommending Dotabuff as a data source — pulling from it means scraping
  their site, which isn't something to build against for a personal tool
  even at small scale; STRATZ + OpenDota cover the same ground officially.

**Output of the pipeline (all local files, checked into the repo):**
- `heroes.json` — id, name, icon, tags (see §4), threat-profile tags (see §3.3b)
- `items.json` — id, name, icon, tags, **budget tier** (cheap/mid/luxury — see §4)
- `matchup_matrix.json` — `matchup[heroA][heroB] = winrate_delta` (positive =
  heroA favored vs heroB)
- `synergy_matrix.json` — `synergy[heroA][heroB] = winrate_when_paired`
- `hero_baseline.json` — `baseline[hero] = overall_winrate`
- `hero_role_stats.json` — from STRATZ: `role_stats[hero][position] =
  {winrate, pickrate}` for positions 1–5. This drives role-aware suggestions.
- `item_counters.json` — curated rules (see §4)

Re-running this script is the *only* time the internet is touched. The app
itself never makes a network call.

---

## 2. Data Model

Each hero gets tagged (manually curated, ~15–20 min of work, reusable forever
until Valen adds a hero that breaks the pattern):

```
tags: [
  "physical-burst", "magic-burst", "disable-heavy", "silence",
  "illusion-based", "invisibility", "summons", "healing-heavy",
  "high-mobility", "global", "channeled-ultimate", "lockdown-combo"
]
```

These tags drive item recommendations — they're the bridge between "this
hero is a threat" and "buy this."

Each hero also gets **threat-profile tags** — these drive who a hero is
dangerous *to*, which differs by role (a lane bully threatens whoever's
laning against them; a split-pusher mostly threatens the pos 1/pos 5 who
have to answer it):

```
threat_profile: [
  "ganker", "lane-bully", "split-pusher", "teamfight-nuker",
  "late-game-scaler", "pick-off", "pusher"
]
```

---

## 3. Scoring System

All scores are computed **live in the browser** from the local JSON — this is
just arithmetic, so it's instant even on a phone-tier CPU.

**Role is a required input, selected before you start filling in the draft**
(1/2/3/4/5). Every score below either takes the role directly or takes it
through the role-relevance step in §3.3b. This is what makes the same enemy
draft produce different advice depending on whether you're the pos 1 farming
carry or the pos 5 babysitting them.

### 3.1 Counter Score
For candidate hero `C`, role `R`, against enemy team `E = [e1, e2, ...]`:

```
counter_score(C, R) = Σ over ei in E of:
    matchup[C][ei] * threat_weight(ei, R)
```

`threat_weight(ei, R)` comes from §3.3 — this is what makes the score
prioritize countering their *real, role-relevant* threat instead of
weighting every enemy hero equally regardless of who's picking.

### 3.2 Synergy Score
For candidate hero `C` with your current team `T = [t1, t2, ...]`:

```
synergy_score(C) = average over ti in T of synergy[C][ti]
```

Not role-dependent — synergy between two heroes' kits doesn't change based
on which of the two you personally play.

### 3.3 Threat Score (per enemy hero, role-relative)

**3.3a — base threat** (how dangerous is this hero, period):

```
base_threat(ei) = 0.4 * hero_baseline[ei]                      // raw current strength
                + 0.3 * avg_synergy_with_rest_of_their_team(ei) // are they enabled?
                + 0.2 * countered_score_against_your_current_team(ei)
                        // negative if YOUR picks already answer them
                + 0.1 * pick_order_priority(ei)
                        // earlier pick = more likely their gameplan's anchor
```

**3.3b — role relevance multiplier** (who this hero actually threatens):

```
role_relevance(ei, R) = lookup in role_relevance_table[threat_profile(ei)][R]
```

`role_relevance_table` is a small config, roughly:

| threat_profile   | pos 1 | pos 2 | pos 3 | pos 4 | pos 5 |
|------------------|-------|-------|-------|-------|-------|
| ganker           | 0.8   | 1.0   | 0.9   | 1.2   | 1.2   |
| lane-bully       | 1.0   | 1.2   | 1.0   | 0.7   | 0.7   |
| split-pusher     | 1.3   | 0.8   | 0.9   | 0.9   | 1.1   |
| teamfight-nuker  | 1.0   | 1.0   | 1.0   | 1.1   | 1.1   |
| late-game-scaler | 1.3   | 0.9   | 0.8   | 0.8   | 0.9   |
| pick-off         | 0.8   | 0.9   | 0.9   | 1.2   | 1.3   |

(Ship these as starting values in `weights.json` — they're informed
estimates, not measured, so treat them as the first thing to adjust once you
notice the tool over- or under-rating a hero type for your role.)

```
threat(ei, R) = base_threat(ei) * role_relevance(ei, R)
```

Normalize all `threat(ei, R)` to sum to 1 across their team → that's
`threat_weight()` in §3.1. The highest-scoring enemy hero is your
"biggest threat **to you, in this role**" callout in the UI.

### 3.4 Role Fit Score
For candidate hero `C` in role `R`, pulled straight from STRATZ position data:

```
role_fit(C, R) = normalized(role_stats[C][R].winrate, role_stats[C][R].pickrate)
```

This keeps the tool from recommending a hero that counters everything on
paper but is never actually played at your role (e.g. don't suggest a
hard-carry-only hero as a pos 5 pick just because the matchup numbers favor it).
Heroes with near-zero pickrate at role `R` should be excluded from the
candidate list entirely, not just scored low — filter, don't just penalize.

### 3.5 Final Recommendation Score
For each candidate hero still available and viable at role `R`:

```
final_score(C, R) = 0.35 * counter_score(C, R)
                   + 0.25 * synergy_score(C)
                   + 0.20 * role_fit(C, R)
                   + 0.10 * hero_baseline[C]
                   - 0.10 * how_hard_C_gets_countered_by_their_team
```

Sort descending → that's your ranked pick list. Show top 5–8, not just #1 —
you want options in case of a snipe or a role conflict on your own team.

**Every weight above — the final-score weights, the base-threat weights, and
the whole role-relevance table — must live in one editable `weights.json`**,
not hardcoded. You'll want to tune these after a few real drafts across
different roles, and pos 1 vs pos 5 tuning will likely diverge over time.
That's expected and fine — it's why this is a config file, not a constant.

---

## 4. Item Counter Layer (rule-based, not stat-derived)

This is curated knowledge, not something OpenDota gives you. Structure it as
tag → item mappings so it's easy to extend:

```json
{
  "disable-heavy": [
    {"item": "Black King Bar", "tier": "mid"},
    {"item": "Lotus Orb", "tier": "mid"},
    {"item": "Force Staff", "tier": "cheap"},
    {"item": "Glimmer Cape", "tier": "cheap"}
  ],
  "silence": [{"item": "Black King Bar", "tier": "mid"}],
  "illusion-based": [{"item": "Manta Style", "tier": "mid"}],
  "physical-burst": [
    {"item": "Aeon Disk", "tier": "luxury"},
    {"item": "Ghost Scepter", "tier": "cheap"},
    {"item": "Armor items (Vanguard/Solar Crest)", "tier": "cheap"}
  ],
  "magic-burst": [
    {"item": "Black King Bar", "tier": "mid"},
    {"item": "Aeon Disk", "tier": "luxury"},
    {"item": "Pipe of Insight", "tier": "mid"}
  ],
  "invisibility": [
    {"item": "Sentry Wards", "tier": "cheap"},
    {"item": "Gem of True Sight", "tier": "cheap"},
    {"item": "Dust of Appearance", "tier": "cheap"}
  ],
  "summons": [{"item": "AoE/splash damage items", "tier": "mid"}],
  "healing-heavy": [{"item": "Nullifier", "tier": "mid"}],
  "channeled-ultimate": [
    {"item": "Silver Edge", "tier": "mid"},
    {"item": "Nullifier", "tier": "mid"}
  ],
  "global": [{"item": "Aeon Disk", "tier": "luxury"}]
}
```

`tier` (`cheap`/`mid`/`luxury`) matters because your gold budget depends
entirely on role. When suggesting items:
- **pos 4/5**: show `cheap` tier first, `mid` only if the game's gone long.
- **pos 1/2/3**: show `mid`/`luxury` first, `cheap` items only as early filler.

When you output "biggest threat: Hero X," pull X's tags, resolve to items via
this table, filter/order by role budget, and show them next to the hero
suggestions. This file is yours to correct/expand as patches change items —
don't over-engineer it at first.

---

## 5. UI Requirements

- **Role selector (1/2/3/4/5) at the top, set before or during the draft,
  changeable at any point** (you might queue as your usual role but end up
  filling a different one that game) — changing it live re-scores everything
  instantly since it's all local arithmetic.
- Hero grid with icons, click to pick; text search box with live filtering
  that highlights matching heroes in the grid as you type.
- Two panels: **Your Team** / **Enemy Team**, each holds picked heroes.
- A locked/banned list (optional but easy — just excludes from candidate pool).
- Right-side results panel, live-updating on every pick:
  - "Biggest Threat" — top 1–2 enemy heroes by threat score, with why
    (baseline strength / enabled by synergy / uncountered by your picks).
  - Item suggestions tied to that threat.
  - Ranked candidate list (top 5–8) with a one-line reason per hero
    (e.g. "+14% vs their Storm Spirit, strong synergy with your Ogre Magi").
- Must load and respond instantly — no spinners, no network calls once the
  JSON is loaded on page open.
- Single static HTML/JS/CSS page. No backend, no build step for you to run
  day-to-day — you open the file in a browser.

---

## 6. Build Order (tell Claude Code to do this in order)

1. Write `CLAUDE.md` summarizing this spec.
2. Build the data pipeline script (OpenDota + STRATZ), verify
   `matchup_matrix.json`, `synergy_matrix.json`, and `hero_role_stats.json`
   actually populate correctly for a few known hero/position pairs you can
   sanity-check by eye (e.g. does the tool agree Anti-Mage is bad against a
   heavy-disable lineup, and does it correctly show him as near-never played
   pos 4/5?).
3. Hand-tag the hero list with §2's tags and threat-profile tags (Claude Code
   can draft these, you review since this is the part that most benefits
   from your own game sense).
4. Build `item_counters.json` from §4, same review step.
5. Implement scoring (§3) as pure functions with unit-testable inputs — test
   with a fake draft at two or three different roles (e.g. pos 1 vs pos 5
   against the same enemy draft) and confirm the recommendations actually
   diverge sensibly before touching UI.
6. Build the UI last, wire it to the scoring functions, add the role selector.
7. Do a dry run with a real past draft you remember, at the role you actually
   played that game, and sanity-check the output against what actually
   happened / what you wish you'd picked.
