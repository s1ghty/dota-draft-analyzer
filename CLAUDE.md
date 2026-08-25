# Dota 2 Draft Analyzer

Personal, offline-after-build draft assistant focused on pos-1-relevant (but
role-selectable) pick advice. Full spec: `SPEC.md`. This file is the terse
summary — read `SPEC.md` for exact formulas.

## What it is

A single static HTML/JS/CSS page (no backend, no build step to run it) that
scores candidate heroes live in the browser against the current draft state,
using JSON data baked ahead of time by a one-time Python pipeline. The app
itself never makes a network call — all internet access happens in
`pipeline/build_data.py`, run manually and only after major patches.

## Layout

- `pipeline/build_data.py` — the one-time data pipeline (§1). Pulls from
  dotaconstants (GitHub raw JSON), OpenDota REST API, OpenDota Explorer (SQL),
  and STRATZ GraphQL. Writes everything into `data/`.
- `data/` — all pipeline output + hand-curated files, checked into the repo:
  - `heroes.json`, `items.json` — from dotaconstants, plus hand-added tags
    (§2) and threat-profile tags (§3.3b).
  - `matchup_matrix.json` — OpenDota per-hero matchup win-rate deltas.
  - `synergy_matrix.json` — OpenDota Explorer SQL: pair win rate when picked
    together on the same team.
  - `hero_baseline.json` — OpenDota `/heroStats` overall win rate.
  - `hero_role_stats.json` — STRATZ per-position (1–5) win rate / pick rate.
  - `item_counters.json` — hand-curated tag → item rules (§4).
  - `weights.json` — every tunable number in the scoring system (§3): final
    score weights, base-threat weights, role-relevance table. Edit this, not
    the code, when tuning.
- `app/` — the static page: `index.html`, `scoring.js` (pure scoring
  functions, §3), `app.js` (UI wiring), `style.css`.
- `tests/` — a small `unittest` script for the scoring functions.

## Data model (§2)

Each hero has:
- `tags`: kit-shape tags (`physical-burst`, `magic-burst`, `disable-heavy`,
  `silence`, `illusion-based`, `invisibility`, `summons`, `healing-heavy`,
  `high-mobility`, `global`, `channeled-ultimate`, `lockdown-combo`) — drives
  item suggestions via `item_counters.json`.
- `threat_profile`: who the hero threatens by role (`ganker`, `lane-bully`,
  `split-pusher`, `teamfight-nuker`, `late-game-scaler`, `pick-off`,
  `pusher`) — drives role-relative threat weighting (§3.3b).

Both are hand-curated (game-sense call, not stat-derived) — Claude drafts,
user reviews.

## Scoring (§3) — all pure arithmetic, computed client-side

- **Counter score**: candidate's matchup edge vs. each enemy, weighted by how
  much that enemy actually threatens *this role* (§3.3).
- **Synergy score**: average pair win rate with current teammates.
- **Threat score**: `base_threat` (baseline strength, team synergy enabling
  them, how well your team already answers them, pick-order priority) ×
  `role_relevance` multiplier from `weights.json`'s role-relevance table.
  Normalized across the enemy team → "biggest threat to you, in this role."
- **Role fit**: STRATZ position win rate/pick rate for the candidate at role
  `R`. Near-zero pickrate at `R` → **excluded from candidates entirely**, not
  just penalized.
- **Final score**: weighted sum of counter/synergy/role-fit/baseline, minus
  how hard the candidate gets countered by the enemy team. Ranked, top 5–8
  shown with a one-line reason.

All weights live in `data/weights.json` — never hardcode a scoring constant.

## Item counters (§4)

Rule-based (curated, not stat-derived): hero threat tags → item list with a
budget `tier` (`cheap`/`mid`/`luxury`). pos 4/5 sees cheap-first; pos 1/2/3
sees mid/luxury-first. Extend `item_counters.json` directly as patches change
the meta — don't build tooling around it.

## UI (§5)

Role selector (1–5, changeable live) at top. Hero grid with search. Your
Team / Enemy Team panels. Optional ban/lock list. Right panel: biggest
threat + why, item suggestions for that threat, ranked candidate list with
one-line reasons. Everything re-scores instantly on any change — no
spinners, no network.

Implemented in `app/`. One deviation from "just open the file": Chrome
blocks `fetch()` of local JSON from a `file://` page, so `app/index.html`
needs to be served, not double-clicked. Run `./serve.sh` (stdlib
`python3 -m http.server`, no install) and open
`http://localhost:8000/app/index.html`. Editing `data/weights.json` and
refreshing picks it up immediately — no rebuild step.

## Build order (§6) — follow in sequence

1. `CLAUDE.md` (this file). ✅
2. Data pipeline (`pipeline/build_data.py`) — verify `matchup_matrix.json`,
   `synergy_matrix.json`, `hero_role_stats.json` sanity-check against known
   truths (e.g. Anti-Mage bad vs. heavy disable, near-never played pos 4/5).
3. Hand-tag heroes with §2 tags — Claude drafts, user reviews.
4. Build `item_counters.json` — same review step.
5. Implement scoring (§3) as pure, unit-tested functions — confirm pos 1 vs.
   pos 5 recommendations diverge sensibly against the same enemy draft
   *before* touching UI.
6. Build the UI, wire to scoring functions.
7. Dry run against a real remembered draft, sanity-check the output.

## Notes for whoever (Claude) touches this next

- STRATZ requires a free-tier API key (GraphQL, bearer token) — read from
  `STRATZ_API_KEY` env var, never hardcode it. Get one at
  https://stratz.com/api.
- OpenDota's public REST + Explorer endpoints don't require a key but are
  rate-limited — the pipeline should be polite (small delay between the
  per-hero matchup calls) since it's a one-shot job, not something run often.
- `urllib`'s default User-Agent gets a 403 from OpenDota's edge — every
  `fetch_json` call in `build_data.py` sets a real UA. If you add a new raw
  request path, keep using `fetch_json`, don't call `urlopen` directly.
- OpenDota's `/heroes/{id}/matchups` endpoint samples from their fully-parsed
  match pool, not their full pick pool (roughly ~1% of picks for a popular
  hero) — per-pair sample sizes are thin (tens of games, sometimes single
  digits). `build_matchup_matrix`/`build_synergy_matrix` shrink each
  win-rate delta toward the neutral midpoint by sample size
  (`SHRINKAGE_K = 30`) so a 1-game fluke doesn't swing scores. If matchup
  data still looks noisy in practice, this constant is the first knob, and a
  paid OpenDota tier is the real fix (removes the sampling entirely).
- Synergy has no prebuilt endpoint (§1) — it's a hand-written Explorer SQL
  query in `build_data.py`. `public_matches` looked like the right join
  target (it carries `avg_rank_tier`) but is a small persistent *sample*
  table; switched to the full `matches` table with a 30-day window
  (`SYNERGY_WINDOW_DAYS`) tuned empirically to land under Explorer's ~15s
  read timeout on this self-join. Shrink the window if it starts timing out
  again (e.g. right after a patch when you'd want fresher data anyway).
- `pipeline/tag_heroes.py` holds the hand-curated `tags`/`threat_profile`
  per hero (§2/§3.3b) — Claude-drafted, needs your review, re-run any time
  you edit it. `build_data.py`'s `build_heroes`/`build_items` carry forward
  existing `tags`/`threat_profile`/`tier` from the current `data/*.json` on
  every re-run specifically so re-running the pipeline after a patch doesn't
  wipe hand curation — don't remove that merge-in-existing behavior.
- Three heroes in `tag_heroes.py` are marked "REVIEW" (Ring Master, Largo,
  and lower-confidence on Kez/Muerta) — recent enough releases that the tags
  are low-confidence guesses, not researched kit knowledge.
- `data/weights.json`'s `role_relevance_table` has no `pusher` row in
  SPEC.md §3.3b's table even though §2 lists `pusher` as a valid
  `threat_profile` tag — added a neutral (all 1.0) row as a placeholder,
  worth tuning once you have a feel for it.
- Don't scrape Dotabuff. STRATZ + OpenDota cover the same ground officially.
- This is a personal tool for one user (erikjan1207@gmail.com) — no auth, no
  multi-user concerns, no deployment target beyond "serve locally with
  `serve.sh`." Don't add infrastructure the spec doesn't ask for.
