#!/usr/bin/env python3
"""One-time data pipeline (SPEC.md §1). Run manually, only after major
patches. Pulls dotaconstants + OpenDota + STRATZ into data/*.json. The app
itself never touches the network -- this script is the only thing that does.

Usage:
    python3 pipeline/build_data.py            # build everything
    python3 pipeline/build_data.py --skip-stratz   # skip role stats (needs STRATZ_API_KEY)
    python3 pipeline/build_data.py --skip-synergy  # skip the slow Explorer SQL query
    python3 pipeline/build_data.py --skip-matchups-stratz  # skip the ~10-12 min STRATZ matchup pull

STRATZ needs a free-tier API key: https://stratz.com/api -> set STRATZ_API_KEY.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DOTACONSTANTS = "https://raw.githubusercontent.com/odota/dotaconstants/master/build"
OPENDOTA = "https://api.opendota.com/api"
STRATZ_URL = "https://api.stratz.com/graphql"


def fetch_json(url, headers=None, data=None, timeout=30):
    headers = {"User-Agent": "dota-draft-analyzer/1.0 (personal tool)", **(headers or {})}
    req = urllib.request.Request(url, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def save(name, obj):
    path = os.path.join(DATA_DIR, name)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    print(f"wrote {path} ({len(obj)} entries)")


# ---- heroes.json / items.json (dotaconstants + hand-tag scaffolding) ----

def build_heroes():
    raw = fetch_json(f"{DOTACONSTANTS}/heroes.json")
    existing_path = os.path.join(DATA_DIR, "heroes.json")
    existing = {}
    if os.path.exists(existing_path):
        with open(existing_path) as f:
            existing = json.load(f)

    heroes = {}
    for hid, h in raw.items():
        old = existing.get(hid, {})
        heroes[hid] = {
            "id": int(hid),
            "name": h["name"],
            "localized_name": h["localized_name"],
            "icon": h.get("icon"),  # 32x32 minimap icon
            "img": h.get("img"),  # 256x144 bust portrait -- what the UI actually renders
            "attribute": h.get("primary_attr"),
            # hand-curated (SPEC.md §2) -- carried over from the existing file
            # so re-running the pipeline after a patch doesn't wipe manual tags.
            "tags": old.get("tags", []),
            "threat_profile": old.get("threat_profile", []),
        }
    return heroes


def item_tier(cost):
    if cost is None:
        return "cheap"
    if cost < 2000:
        return "cheap"
    if cost < 4500:
        return "mid"
    return "luxury"


def build_items():
    raw = fetch_json(f"{DOTACONSTANTS}/items.json")
    existing_path = os.path.join(DATA_DIR, "items.json")
    existing = {}
    if os.path.exists(existing_path):
        with open(existing_path) as f:
            existing = json.load(f)

    items = {}
    for key, it in raw.items():
        old = existing.get(key, {})
        cost = it.get("cost")
        items[key] = {
            "id": it.get("id"),
            "name": key,
            "localized_name": it.get("dname", key),
            "icon": it.get("img"),
            "cost": cost,
            # heuristic default from cost; keep a hand-adjusted tier across re-runs
            "tier": old.get("tier", item_tier(cost)),
            "tags": old.get("tags", []),
        }
    return items


# ---- hero_baseline.json (OpenDota /heroStats) ----

# A hero_baseline that's stale right after a patch flips the meta is the
# actual motivating problem here -- but OpenDota's /heroStats fields aren't
# "overall win rate" like their names suggest: per OpenDota's own source
# (svc/api/spec.ts), every {bracket}_pick/{bracket}_win value (including the
# 1-8 skill-bracket fields this function used to sum) is already a rolling
# *7-day* sum from daily Redis counters, not an all-time total. Confirmed
# empirically too: data/hero_baseline.json's values shift slightly between
# pipeline runs on different days -- an all-time average over millions of
# historical games couldn't move that fast.
#
# So the 7-day window already IS "since roughly last week," which covers
# "since this patch" fine once a patch is more than ~7 days old. The one gap
# is the first few days after a fresh patch, when that rolling window still
# has some pre-patch days mixed in. OpenDota's pub_pick_trend/pub_win_trend
# expose the same 7-day window as daily buckets (oldest-to-newest, verified
# live), which lets us split it precisely: buckets from on/after the patch
# release date are the "since-patch" pool, the remaining (pre-patch) buckets
# are the fallback pool, blended by the same shrinkage-by-sample-size shape
# already used for matchup/synergy shrinkage. When the patch is >=7 days
# old this naturally degrades to "since_games = all 7 days, weight ~= 1" --
# no separate branch needed, no separate all-time query needed either.
#
# ponytail: an OpenDota Explorer SQL query (like synergy_matrix's) would let
# this reach further back than 7 days for a true "since an old patch, vs a
# longer prior-patch baseline" comparison. Tried it first -- as of this
# session, OpenDota's Explorer times out (>15s) on *any* picks_bans join
# regardless of window size, even the pre-existing 30-day synergy_matrix
# self-join that used to work. That's an OpenDota-side infrastructure
# problem (data/synergy_matrix.json can't currently be refreshed either),
# not something tunable away here -- this REST-only approach sidesteps it
# entirely. Revisit Explorer once/if it's healthy again.
BASELINE_PATCH_TREND_K = 500


def get_current_patch():
    """{'id', 'name', 'date' (epoch seconds)} for the most recent patch
    whose release date is not in the future."""
    patches = fetch_json(f"{OPENDOTA}/constants/patch")
    now = time.time()

    def parse_iso(s):
        s = re.sub(r"\.\d+Z$", "Z", s).replace("Z", "+00:00")
        return dt.datetime.fromisoformat(s).timestamp()

    current = None
    for p in patches:
        ts = parse_iso(p["date"])
        if ts <= now and (current is None or ts > current["date"]):
            current = {"id": p["id"], "name": p["name"], "date": ts}
    return current


# Shrinks a "since" estimate toward a "broad/fallback" estimate by sample
# size, same shape as SHRINKAGE_K: weight rises toward 1 as since_games grows
# past k, so a thin since-patch sample doesn't swing the number on noise, but
# a well-supported one is trusted close to fully. Reused for hero_role_stats.
def blend_by_sample_size(since_value, since_games, broad_value, broad_games, k):
    if since_games == 0:
        return broad_value if broad_games else 0.5
    if broad_games == 0:
        return since_value
    weight = since_games / (since_games + k)
    return since_value * weight + broad_value * (1 - weight)


# Bracket toggle (All / 4 rank-bracket groups / Pro, hero_baseline + role
# stats only -- the rest of the app stays bracket-agnostic). OpenDota's
# /heroStats groups its own 1-8 numeric brackets (Herald..Immortal) in pairs
# that line up exactly with STRATZ's RankBracket enum groupings below, so
# both data sources use the same 4 group keys.
BRACKET_GROUPS_OPENDOTA = {
    "herald_guardian": [1, 2],
    "crusader_archon": [3, 4],
    "legend_ancient": [5, 6],
    "divine_immortal": [7, 8],
}


def build_hero_baseline(stats):
    patch = get_current_patch()
    patch_age_days = (time.time() - patch["date"]) / 86400 if patch else 7
    # buckets are oldest(6 days ago)->newest(today); how many trailing
    # buckets fall on/after the patch release (clamped into 0..7).
    n_since = min(7, max(0, int(patch_age_days) + 1))

    baseline = {}
    for h in stats:
        pick_trend = h.get("pub_pick_trend") or [0] * 7
        win_trend = h.get("pub_win_trend") or [0] * 7
        since_games = sum(pick_trend[7 - n_since:]) if n_since else 0
        since_wins = sum(win_trend[7 - n_since:]) if n_since else 0
        broad_games = sum(pick_trend[: 7 - n_since])
        broad_wins = sum(win_trend[: 7 - n_since])

        since_rate = since_wins / since_games if since_games else 0.5
        broad_rate = broad_wins / broad_games if broad_games else 0.5
        blended = blend_by_sample_size(since_rate, since_games, broad_rate, broad_games, BASELINE_PATCH_TREND_K)
        baseline[str(h["id"])] = round(blended, 4) if (since_games or broad_games) else 0.5
    return baseline


# Separate file, not a change to hero_baseline.json's existing shape --
# hero_baseline.json's per-hero value is a bare float (scoring.js reads it
# directly as a number), and "All" must keep reproducing it exactly. This
# holds the 4 bracket groups + Pro, all pulled from the same /heroStats
# response build_hero_baseline() already fetched -- no extra API call.
# Raw all-time bucket totals (not patch-blended like "All" is): OpenDota's
# per-bracket N_pick/N_win fields aren't broken out by time trend the way
# pub_pick_trend/pub_win_trend are, so there's no "since-patch" number to
# blend toward here.
def build_hero_baseline_brackets(stats):
    brackets = {}
    for h in stats:
        entry = {}
        for group, indices in BRACKET_GROUPS_OPENDOTA.items():
            games = sum(h.get(f"{i}_pick", 0) or 0 for i in indices)
            wins = sum(h.get(f"{i}_win", 0) or 0 for i in indices)
            entry[group] = {"winrate": round(wins / games, 4) if games else 0.5, "games": games}
        pro_games = h.get("pro_pick", 0) or 0
        pro_wins = h.get("pro_win", 0) or 0
        entry["pro"] = {"winrate": round(pro_wins / pro_games, 4) if pro_games else 0.5, "games": pro_games}
        brackets[str(h["id"])] = entry
    return brackets


# ---- item_builds.json (OpenDota /heroes/{id}/itemPopularity) ----
# Answers "what do I actually buy on this hero, and when" from real match
# data -- no AI, no curation, just the most-picked items per game phase.
# Not in SPEC.md's original scope; added on request.

ITEM_BUILD_PHASES = ["start_game_items", "early_game_items", "mid_game_items", "late_game_items"]
ITEMS_PER_PHASE = 5


def build_item_builds(hero_ids, items_by_id, delay=0.6):
    # Same reasoning as build_matchup_matrix: start from the existing file so
    # a timed-out hero keeps its last-known build instead of its row being
    # deleted outright when save() overwrites the file.
    existing_path = os.path.join(DATA_DIR, "item_builds.json")
    builds = {}
    if os.path.exists(existing_path):
        with open(existing_path) as f:
            builds = json.load(f)
    total = len(hero_ids)
    failed = []
    for i, hid in enumerate(hero_ids, 1):
        try:
            data = fetch_json(f"{OPENDOTA}/heroes/{hid}/itemPopularity")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            failed.append(hid)
            print(f"\n  hero {hid} FAILED ({e}), keeping its existing build, skipping")
            continue
        phases = {}
        for phase in ITEM_BUILD_PHASES:
            counts = data.get(phase, {})
            ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:ITEMS_PER_PHASE]
            phases[phase] = [items_by_id[int(iid)] for iid, _ in ranked if int(iid) in items_by_id]
        builds[str(hid)] = phases
        print(f"  item builds {i}/{total} (hero {hid})", end="\r")
        time.sleep(delay)
    print()
    if failed:
        print(f"  {len(failed)} heroes failed and kept their existing data: {failed} -- re-run to refresh them")
    return builds


# ---- matchup_matrix.json (OpenDota /heroes/{id}/matchups) ----
#
# This endpoint's per-pair sample sizes are thin (tens of games for common
# pairs, sometimes single digits) -- it draws from OpenDota's fully-parsed
# match pool, not their full pick count. A raw wins/games delta on an n=1
# sample would swing the counter score on pure noise, so shrink each delta
# toward 0 by its sample size (more games -> trust the observed delta more).
# ponytail: fixed shrinkage constant (K=30), not fit to real variance. If
# scores still look noisy in practice, replace with a Wilson interval or
# swap to a paid OpenDota tier for bigger samples.
SHRINKAGE_K = 30


def build_matchup_matrix(hero_ids, delay=0.6):
    # Start from whatever's already on disk so a hero that times out today
    # keeps its last-known row instead of vanishing outright -- otherwise a
    # partial run (OpenDota having a bad day, see CLAUDE.md) permanently
    # deletes that hero's matchup data the moment save() writes this dict,
    # rather than actually "filling in" on the next re-run like the skip
    # message below promises. Found 2026-08-26: a run that timed out on 45/127
    # heroes silently dropped their rows from data/matchup_matrix.json entirely.
    existing_path = os.path.join(DATA_DIR, "matchup_matrix.json")
    matrix = {}
    if os.path.exists(existing_path):
        with open(existing_path) as f:
            matrix = json.load(f)
    total = len(hero_ids)
    failed = []
    for i, hid in enumerate(hero_ids, 1):
        try:
            data = fetch_json(f"{OPENDOTA}/heroes/{hid}/matchups")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            failed.append(hid)
            print(f"\n  hero {hid} FAILED ({e}), keeping its existing row, skipping")
            continue
        row = {}
        for entry in data:
            games = entry.get("games_played", 0)
            if games > 0:
                raw_delta = entry["wins"] / games - 0.5
                shrunk = raw_delta * games / (games + SHRINKAGE_K)
                row[str(entry["hero_id"])] = {"delta": round(shrunk, 4), "games": games}
        matrix[str(hid)] = row
        print(f"  matchups {i}/{total} (hero {hid})", end="\r")
        time.sleep(delay)
    print()
    if failed:
        print(f"  {len(failed)} heroes failed and kept their existing data: {failed} -- re-run to refresh them")
    return matrix


# ---- matchup_matrix_stratz_<bracket>.json (STRATZ heroVsHeroMatchup, primary
# matchup source as of 2026-11 -- see CLAUDE.md) ----
#
# Why this exists alongside matchup_matrix.json rather than replacing it:
# OpenDota's per-hero /matchups endpoint samples from a small fully-parsed
# pool (tens of games/pair, sometimes single digits -- see SHRINKAGE_K above).
# STRATZ's heroVsHeroMatchup, pulled explicitly across every week of the
# current patch, gives 185-2.1M+ games per pair (median 14.3k-105k depending
# on bracket) -- evaluated against real match outcomes before switching, but
# the clean (non-leaky) test sample only ran to 77 matches, not the 10k+
# originally wanted: OpenDota's parsed-match pool is too sparse to reach 10k
# within any window that doesn't also predate the STRATZ training window
# (confirmed live -- reaching 10k+ complete-roster matches needs ~230+ days
# back, i.e. this patch's entire lifetime). At n=77 OpenDota scored higher
# on point estimate but the confidence intervals overlapped heavily with
# STRATZ's -- not a clean win either way, see git log/session notes for the
# full numbers. Kept OpenDota as a per-pair fallback (weights.json's
# matchup_source / matchup_stratz_games_floor), not deleted outright, given
# that genuine uncertainty.
#
# week:null was tested and rejected: it does NOT reproduce "the current
# week" as STRATZ's own schema docs claim (confirmed live -- null returned
# ~83k games for a test hero's matchups at Divine/Immortal, while every
# explicit single-week timestamp tested, including the literal current week,
# returned 103k-231k -- a genuine doc/behavior mismatch, not a timezone
# artifact, see the 7-offset test in git history/session notes). Every week
# here is an explicit Monday-aligned epoch timestamp instead.
MATCHUP_STRATZ_SHRINKAGE_K = 10  # see weights.json comment-equivalent: at
# these sample sizes (hundreds to millions of games/pair) shrinkage barely
# moves the number either way -- K=10 (vs the OpenDota-tuned K=30) mainly
# matters for whichever pair sits just above the games-floor fallback cutoff,
# or if a future pipeline run narrows the window (e.g. last-N-weeks instead
# of whole-patch) and samples shrink back down.

MATCHUP_STRATZ_BRACKETS = {
    "herald_guardian": "HERALD_GUARDIAN",
    "crusader_archon": "CRUSADER_ARCHON",
    "legend_ancient": "LEGEND_ANCIENT",
    "divine_immortal": "DIVINE_IMMORTAL",
}
MATCHUP_STRATZ_BATCH = 25


def _patch_week_timestamps(patch):
    """Monday-aligned epoch timestamps from the current patch's release week
    through this week, inclusive -- explicit, not week:null (see above)."""
    patch_start = dt.datetime.fromtimestamp(patch["date"], dt.timezone.utc)
    patch_monday = (patch_start - dt.timedelta(days=patch_start.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    now = dt.datetime.now(dt.timezone.utc)
    this_monday = (now - dt.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    weeks = []
    w = patch_monday
    while w <= this_monday:
        weeks.append(int(w.timestamp()))
        w += dt.timedelta(weeks=1)
    return weeks


def _fetch_stratz_matchup_week(headers, hero_ids, bracket_enum, week_ts, retries=5):
    fields = "\n".join(
        f"  h{hid}: matchUp(heroId: {hid}, week: {week_ts}, bracketBasicIds: [{bracket_enum}], take: 200) "
        f"{{ vs {{ heroId2 matchCount winCount }} }}"
        for hid in hero_ids
    )
    query = "query {\n  heroStats {\n" + fields + "\n  }\n}"
    body = json.dumps({"query": query}).encode()
    headers = {**headers, "User-Agent": "STRATZ_API"}
    for attempt in range(retries):
        result = fetch_json(STRATZ_URL, headers=headers, data=body, timeout=60)
        if "errors" not in result:
            return result["data"]["heroStats"]
        codes = {e.get("extensions", {}).get("code") for e in result["errors"]}
        if codes == {"SERVER_BUSY"} and attempt < retries - 1:
            wait = 2 ** (attempt + 1)
            print(f"\n    SERVER_BUSY, retrying in {wait}s...")
            time.sleep(wait)
            continue
        raise RuntimeError(result["errors"])
    raise RuntimeError("exhausted retries on SERVER_BUSY")


def build_matchup_matrix_stratz(api_key, hero_ids):
    """Returns {bracket_key: matrix} for the 4 bracket groups + "all" (summed
    across brackets), matrix shaped like matchup_matrix.json ({a: {b: {delta,
    games}}}). ~792 requests (33 weeks x 4 brackets x 6 hero-batches at the
    time this was written, scales with patch age) -- comfortably inside
    STRATZ's rate limit (8/s, 150/min, 1500/hr, confirmed live via response
    headers) but takes ~10-12 minutes wall-clock, dominated by request
    latency, not throttling."""
    hero_ids = sorted(int(h) for h in hero_ids)  # normalize to int -- callers pass
    # strings (sorted(heroes, key=int) sorts numerically but returns strings), and
    # the pair-dedup comparison below needs real int comparison, not lexicographic
    # string comparison ("6" >= "14" is True as strings, wrong -- confirmed live,
    # it silently dropped real pairs like Drow Ranger vs Pudge from the "all" matrix).
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    patch = get_current_patch()
    weeks = _patch_week_timestamps(patch)
    print(f"  STRATZ matchup pull: {len(weeks)} weeks (patch {patch['name']} start) x "
          f"{len(MATCHUP_STRATZ_BRACKETS)} brackets x "
          f"{-(-len(hero_ids) // MATCHUP_STRATZ_BATCH)} hero-batches")

    # bracket -> hero_a -> hero_b -> {games, wins_lo}
    totals = {bk: {} for bk in MATCHUP_STRATZ_BRACKETS}
    request_count = 0
    t0 = time.time()
    for bracket_key, bracket_enum in MATCHUP_STRATZ_BRACKETS.items():
        for week_ts in weeks:
            for i in range(0, len(hero_ids), MATCHUP_STRATZ_BATCH):
                batch = hero_ids[i:i + MATCHUP_STRATZ_BATCH]
                result = _fetch_stratz_matchup_week(headers, batch, bracket_enum, week_ts)
                request_count += 1
                for hid in batch:
                    entries = result.get(f"h{hid}")
                    if not entries or not entries[0]:
                        continue
                    a = hid
                    for e in entries[0]["vs"]:
                        b = e["heroId2"]
                        if a >= b:
                            continue  # each pair taken from the lower heroId's own query
                            # only -- confirmed live the two directions report slightly
                            # different matchCount for the same pair/week/bracket (308 vs
                            # 298), so using both would double-count on non-identical samples
                        row = totals[bracket_key].setdefault(str(a), {})
                        cell = row.setdefault(str(b), {"games": 0, "wins_lo": 0})
                        cell["games"] += e["matchCount"]
                        cell["wins_lo"] += e["winCount"]
            print(f"  {bracket_key}: week {week_ts} done ({request_count} requests, {time.time()-t0:.0f}s)", end="\r")
        print(f"  {bracket_key}: all weeks done" + " " * 30)

    def shrink(wins, games, k=MATCHUP_STRATZ_SHRINKAGE_K):
        if games <= 0:
            return 0.0
        raw_delta = wins / games - 0.5
        return raw_delta * games / (games + k)

    def to_matrix(bracket_data):
        matrix = {}
        for a, row in bracket_data.items():
            for b, cell in row.items():
                d = shrink(cell["wins_lo"], cell["games"])
                matrix.setdefault(a, {})[b] = {"delta": round(d, 4), "games": cell["games"]}
                matrix.setdefault(b, {})[a] = {"delta": round(-d, 4), "games": cell["games"]}
        return matrix

    matrices = {bk: to_matrix(bracket_data) for bk, bracket_data in totals.items()}

    all_data = {}
    for bracket_data in totals.values():
        for a, row in bracket_data.items():
            for b, cell in row.items():
                if int(a) >= int(b):  # dict keys here are strings -- see the int()
                    continue          # note above the hero_ids normalization
                dst = all_data.setdefault(a, {}).setdefault(b, {"games": 0, "wins_lo": 0})
                dst["games"] += cell["games"]
                dst["wins_lo"] += cell["wins_lo"]
    matrices["all"] = to_matrix(all_data)

    print(f"  STRATZ matchup pull done: {request_count} requests, {time.time()-t0:.0f}s total")
    return matrices


# ---- synergy_matrix.json (OpenDota Explorer SQL) ----
#
# No prebuilt endpoint for this (SPEC.md §1) -- has to be a raw query.
# `public_matches` looked like the natural join target (it carries
# avg_rank_tier) but it's a small persistent *sample* table, not the full
# match history, so it starved this self-join of rows. `matches` is
# OpenDota's full parsed-match table (same data backing /heroes/{id}/matchups)
# and has far more rows per pair, at the cost of no rank-tier filter.
# 60 days, not 30 -- deliberate choice (2026-08-27): unlike hero_baseline's
# win rate, a hero-pair *kit* synergy doesn't really go stale the way an
# individual hero's power level does after a patch, so more sample size at
# the cost of some recency is the right trade here. At 30 days, 41% of all
# pairs were single-game samples (median 2 games/pair) -- see git history
# for the actual before/after distribution this traded against.
SYNERGY_WINDOW_DAYS = 60

# This self-join used to filter on `m.start_time > now() - interval` and
# timed out unpredictably (sometimes <1s, sometimes the full ~15s Explorer
# read timeout, on the *identical* query). Root-caused 2026-08-27:
# matches.start_time isn't usably indexed for a join into picks_bans (a much
# bigger table), so that filter forces a load-dependent scan of a large
# chunk of picks_bans regardless of window size -- confirmed live that even
# a 1-day window timed out the same way a 30-day one did. picks_bans.match_id
# filters reliably fast instead (confirmed: 0.3-0.6s across repeated runs,
# vs. either <1s or >15s before). Since match_id increases monotonically
# with time, get_recent_match_id_threshold() calibrates a match_id cutoff
# for "N days ago" using two match_id-filtered (never start_time-filtered)
# lookups, and the query below filters on that instead.
SYNERGY_SQL_TEMPLATE = """
SELECT pb1.hero_id AS hero_a, pb2.hero_id AS hero_b,
       COUNT(*) AS games,
       SUM(CASE WHEN (pb1.team = 0 AND m.radiant_win)
                  OR (pb1.team = 1 AND NOT m.radiant_win)
                THEN 1 ELSE 0 END) AS wins
FROM picks_bans pb1
JOIN picks_bans pb2
  ON pb1.match_id = pb2.match_id
 AND pb1.team = pb2.team
 AND pb1.hero_id < pb2.hero_id
JOIN matches m ON m.match_id = pb1.match_id
WHERE pb1.is_pick AND pb2.is_pick AND pb1.match_id > {lo_match_id}
GROUP BY pb1.hero_id, pb2.hero_id
"""


def get_recent_match_id_threshold(days, tolerance_days=1, max_iterations=5):
    """match_id for "days ago". A single rate extrapolated from a short local
    sample isn't good enough -- confirmed live (2026-08-27) that a rate
    calibrated from a ~1.4-day window and extrapolated 20x out to 30 days
    landed at an *actual* 40.5 days back, because match volume isn't constant
    across that longer span. Instead, iteratively refine the guess against
    the real measured elapsed time at that match_id until it's within
    tolerance_days -- each check is one fast match_id-filtered lookup
    (confirmed reliably <1s), so a few extra rounds cost nothing."""
    def match_id_and_time(sql):
        url = f"{OPENDOTA}/explorer?sql=" + urllib.parse.quote(sql)
        return fetch_json(url, timeout=15)["rows"][0]

    newest = match_id_and_time("SELECT match_id, start_time FROM matches ORDER BY match_id DESC LIMIT 1")
    probe = match_id_and_time(
        f"SELECT match_id, start_time FROM matches WHERE match_id < {newest['match_id'] - 3_000_000} "
        "ORDER BY match_id DESC LIMIT 1"
    )
    ids_per_second = (newest["match_id"] - probe["match_id"]) / (newest["start_time"] - probe["start_time"])
    id_delta = ids_per_second * days * 86400

    for _ in range(max_iterations):
        lo_id = int(newest["match_id"] - id_delta)
        at_lo = match_id_and_time(f"SELECT match_id, start_time FROM matches WHERE match_id > {lo_id} ORDER BY match_id ASC LIMIT 1")
        actual_days = (newest["start_time"] - at_lo["start_time"]) / 86400
        if abs(actual_days - days) <= tolerance_days:
            break
        id_delta = id_delta * days / actual_days  # rescale toward the target
    return lo_id


def build_synergy_matrix():
    lo_match_id = get_recent_match_id_threshold(SYNERGY_WINDOW_DAYS)
    sql = SYNERGY_SQL_TEMPLATE.format(lo_match_id=lo_match_id)
    url = f"{OPENDOTA}/explorer?sql=" + urllib.parse.quote(sql)
    result = fetch_json(url, timeout=30)
    rows = result.get("rows", [])
    matrix = {}
    for r in rows:
        a, b, games, wins = str(r["hero_a"]), str(r["hero_b"]), r["games"], r["wins"]
        if games <= 0:
            continue
        raw_wr = wins / games
        # same small-sample shrinkage as matchup_matrix, toward the neutral 0.5 midpoint
        shrunk_wr = round(0.5 + (raw_wr - 0.5) * games / (games + SHRINKAGE_K), 4)
        # {winrate, games} -- mirrors matchup_matrix.json's {delta, games} shape
        # (scoring.js's getSynergy/getSynergyGames read either this or the old
        # bare-number shape, same backward-compat pattern as getMatchup).
        cell = {"winrate": shrunk_wr, "games": games}
        matrix.setdefault(a, {})[b] = cell
        matrix.setdefault(b, {})[a] = cell
    return matrix


# ---- hero_role_stats.json (STRATZ GraphQL) ----
# Was heroStats.stats(positionIds:, groupByPosition:true) -- confirmed via
# live schema introspection that this defaults to STRATZ's *current calendar
# week only* when its optional `week` arg is omitted (their own schema doc:
# "Leaving null gives the current week"), same "already recent, just fixed-
# window" situation as hero_baseline's old bracket-sum. Switched to
# heroStats.winGameVersion, which buckets matchCount/winCount by patch
# (gameVersionId) directly -- confirmed live (2026-08-26, real API key) this
# is patch-aware, not week-aware, and doesn't need any date math at all.
#
# One real surface mismatch found only by running this live: third-party
# schema dumps (and STRATZ's own docs elsewhere) show a `gameVersionIds`
# filter argument on winGameVersion -- the live schema (checked via
# __type introspection) does NOT have it. Worked around by pulling all
# versions in one call (take: 20000 comfortably covers every hero x every
# patch STRATZ has ever tracked -- confirmed live, <4s for all 5 positions
# in a single request) and filtering by gameVersionId client-side instead.
#
# "Current patch" is taken as the newest gameVersionId actually present in
# the returned data, not the newest entry in constants.gameVersions -- a
# patch can be registered there before STRATZ has bucketed any real match
# data under it yet (confirmed live: id 182/"7.40b" existed in constants but
# had zero winGameVersion rows for any hero; id 181/"7.40" was the newest
# with real data).
STRATZ_POSITION_MAP = {
    "POSITION_1": "1", "POSITION_2": "2", "POSITION_3": "3",
    "POSITION_4": "4", "POSITION_5": "5",
}

# games needed at this hero+position, in the current patch alone, before its
# winrate is trusted close to fully over the broader fallback window (same
# shrinkage shape as BASELINE_PATCH_TREND_K/SHRINKAGE_K).
ROLE_STATS_PATCH_K = 1000
# how many patches before the current one make up the fallback pool. Also
# bounds *pickrate*'s window (unlike winrate, pickrate isn't blended by
# recency -- how often a hero is played at a role is structural, not
# something that needs to be this patch-fresh -- but it's still capped to
# this window rather than a hero's entire multi-year history, so a hero's
# pickrate at a role it hasn't been played at in years doesn't linger).
ROLE_STATS_BROAD_LOOKBACK_VERSIONS = 3


# STRATZ's own RankBracket enum (8 tiers, Herald..Immortal) grouped to match
# BRACKET_GROUPS_OPENDOTA's 4 groups -- confirmed live via introspection that
# winGameVersion's `bracketIds` arg takes this finer enum (not the 4-value
# RankBracketBasicEnum some other STRATZ fields use), and that it composes
# cleanly with positionIds/gameVersionId in one query (tested live).
BRACKET_GROUPS_STRATZ = {
    "herald_guardian": ["HERALD", "GUARDIAN"],
    "crusader_archon": ["CRUSADER", "ARCHON"],
    "legend_ancient": ["LEGEND", "ANCIENT"],
    "divine_immortal": ["DIVINE", "IMMORTAL"],
}


def _fetch_role_stats_heroStats(headers, bracket_ids):
    bracket_arg = f", bracketIds: [{', '.join(bracket_ids)}]" if bracket_ids else ""
    # one HTTP round-trip for all 5 positions via aliases, each pulling
    # every hero x every patch version STRATZ has data for (optionally
    # restricted to bracket_ids).
    position_fields = "\n".join(
        f"    {stratz_pos}: winGameVersion(positionIds: [{stratz_pos}], take: 20000{bracket_arg}) "
        f"{{ gameVersionId heroId matchCount winCount }}"
        for stratz_pos in STRATZ_POSITION_MAP
    )
    query = f"query {{\n  heroStats {{\n{position_fields}\n  }}\n}}"
    body = json.dumps({"query": query}).encode()
    result = fetch_json(STRATZ_URL, headers=headers, data=body, timeout=60)
    if "errors" in result:
        raise RuntimeError(f"STRATZ GraphQL error: {result['errors']}")
    return result["data"]["heroStats"]


# Factored out of build_role_stats() so the exact same since-patch/broad-
# lookback blend can run once unfiltered ("All" -- byte-identical to before
# this bracket toggle existed) and again per bracket group.
def _aggregate_role_stats(hero_stats):
    all_version_ids = {
        row["gameVersionId"]
        for stratz_pos in STRATZ_POSITION_MAP
        for row in hero_stats[stratz_pos]
    }
    if not all_version_ids:
        return {}
    current_version = max(all_version_ids)
    broad_versions = set(
        sorted((v for v in all_version_ids if v < current_version), reverse=True)[
            :ROLE_STATS_BROAD_LOOKBACK_VERSIONS
        ]
    )

    # pickrate = this hero's share of games played *at that position*, not
    # of all games everywhere -- that's what makes weights.json's
    # role_fit_pickrate_floor (0.01) a meaningful "near never played" cutoff.
    total_by_position = {}
    per_hero = {}  # (hero_id, position) -> since/broad matches+wins, total matches
    for stratz_pos, pos in STRATZ_POSITION_MAP.items():
        for row in hero_stats[stratz_pos]:
            gv = row["gameVersionId"]
            if gv != current_version and gv not in broad_versions:
                continue  # outside the current-patch + fallback window entirely
            hid = str(row["heroId"])
            matches, wins = row["matchCount"], row["winCount"]
            entry = per_hero.setdefault((hid, pos), {"since_m": 0, "since_w": 0, "broad_m": 0, "broad_w": 0, "total_m": 0})
            entry["total_m"] += matches
            if gv == current_version:
                entry["since_m"] += matches
                entry["since_w"] += wins
            else:
                entry["broad_m"] += matches
                entry["broad_w"] += wins
            total_by_position[pos] = total_by_position.get(pos, 0) + matches

    stats = {}
    for (hid, pos), e in per_hero.items():
        since_wr = e["since_w"] / e["since_m"] if e["since_m"] else 0.5
        broad_wr = e["broad_w"] / e["broad_m"] if e["broad_m"] else 0.5
        winrate = blend_by_sample_size(since_wr, e["since_m"], broad_wr, e["broad_m"], ROLE_STATS_PATCH_K)
        total = total_by_position.get(pos, 0)
        stats.setdefault(hid, {})[pos] = {
            "winrate": round(winrate, 4),
            "pickrate": round(e["total_m"] / total, 6) if total else 0.0,
            "games": e["total_m"],
        }
    return stats


def build_role_stats(api_key):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API",
    }
    # "All" -- identical query/aggregation to before the bracket toggle
    # existed, so its winrate/pickrate values reproduce today's numbers
    # exactly. `games` is a new, purely additive field alongside them.
    role_stats = _aggregate_role_stats(_fetch_role_stats_heroStats(headers, None))

    for bracket_key, bracket_ids in BRACKET_GROUPS_STRATZ.items():
        try:
            bracket_stats = _aggregate_role_stats(_fetch_role_stats_heroStats(headers, bracket_ids))
        except (urllib.error.URLError, RuntimeError) as e:
            print(f"  hero_role_stats bracket {bracket_key} FAILED ({e}) -- skipping, \"All\" still has today's data")
            continue
        for hid, positions in bracket_stats.items():
            for pos, entry in positions.items():
                role_stats.setdefault(hid, {}).setdefault(pos, {"winrate": 0.5, "pickrate": 0.0, "games": 0})
                role_stats[hid][pos].setdefault("byBracket", {})[bracket_key] = entry

    return role_stats


# ---- player_hero_stats.json (OpenDota /players/{account_id}/heroes) ----
# Optional, personal, single-user feature (SPEC.md's tool is explicitly for
# one user -- CLAUDE.md): your own per-hero games/wins, so scoring.js can
# give a small nudge toward heroes you actually play. Public endpoint, no
# auth needed -- confirmed live 2026-09-16. Baked into the pipeline rather
# than fetched live by the browser: this repo is public (GitHub Pages), and
# a live per-page-load browser fetch would need OpenDota to send
# Access-Control-Allow-Origin (unconfirmed -- their API was down, HTTP 521,
# while checking this), on top of adding a live network dependency to an
# app whose whole point is "never touches the network" (CLAUDE.md). Server-
# side via the same fetch_json() every other source already uses sidesteps
# both problems.
def build_player_hero_stats(account_id):
    data = fetch_json(f"{OPENDOTA}/players/{account_id}/heroes")
    if not isinstance(data, list):
        print(f"  WARNING: unexpected response for account {account_id} (private profile or bad id?) -- writing empty player_hero_stats.json")
        return {}
    return {str(h["hero_id"]): {"games": h["games"], "win": h["win"]} for h in data if h.get("games", 0) > 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-stratz", action="store_true")
    parser.add_argument("--skip-synergy", action="store_true")
    parser.add_argument("--skip-matchups", action="store_true")
    parser.add_argument("--skip-matchups-stratz", action="store_true",
                         help="skip the ~10-12 min STRATZ matchup pull (792 requests, scales with patch age)")
    parser.add_argument("--skip-builds", action="store_true")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    print("dotaconstants: heroes + items")
    heroes = build_heroes()
    save("heroes.json", heroes)
    items = build_items()
    save("items.json", items)

    print("OpenDota: hero_baseline")
    hero_stats_raw = fetch_json(f"{OPENDOTA}/heroStats")
    save("hero_baseline.json", build_hero_baseline(hero_stats_raw))
    save("hero_baseline_brackets.json", build_hero_baseline_brackets(hero_stats_raw))

    if not args.skip_matchups:
        print("OpenDota: matchup_matrix (one call per hero, be polite)")
        save("matchup_matrix.json", build_matchup_matrix(sorted(heroes, key=int)))
    else:
        print("skipping matchup_matrix")

    if not args.skip_builds:
        print("OpenDota: item_builds (one call per hero, be polite)")
        items_by_id = {it["id"]: it["localized_name"] for it in items.values() if it.get("id") is not None}
        save("item_builds.json", build_item_builds(sorted(heroes, key=int), items_by_id))
    else:
        print("skipping item_builds")

    if not args.skip_synergy:
        print("OpenDota Explorer: synergy_matrix (SQL, can take a while)")
        try:
            save("synergy_matrix.json", build_synergy_matrix())
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"  synergy_matrix FAILED ({e}) -- leaving unset, re-run with --skip-synergy once other data is good")
    else:
        print("skipping synergy_matrix")

    if not args.skip_stratz:
        api_key = os.environ.get("STRATZ_API_KEY")
        if not api_key:
            print("STRATZ_API_KEY not set -- skipping hero_role_stats.json and the STRATZ "
                  "matchup matrices. Get a free key at https://stratz.com/api and re-run without --skip-stratz.")
        else:
            print("STRATZ: hero_role_stats")
            try:
                save("hero_role_stats.json", build_role_stats(api_key))
            except (urllib.error.URLError, RuntimeError) as e:
                print(f"  hero_role_stats FAILED ({e})")

            if not args.skip_matchups_stratz:
                print("STRATZ: matchup_matrix_stratz_<bracket> (792 requests, ~10-12 min)")
                try:
                    matrices = build_matchup_matrix_stratz(api_key, sorted(heroes, key=int))
                    for bracket_key, matrix in matrices.items():
                        save(f"matchup_matrix_stratz_{bracket_key}.json", matrix)
                except (urllib.error.URLError, RuntimeError) as e:
                    print(f"  matchup_matrix_stratz FAILED ({e}) -- leaving existing files as-is")
            else:
                print("skipping matchup_matrix_stratz")
    else:
        print("skipping hero_role_stats and matchup_matrix_stratz")

    account_id = os.environ.get("DOTA_ACCOUNT_ID")
    if account_id:
        print("OpenDota: player_hero_stats (personal affinity)")
        try:
            save("player_hero_stats.json", build_player_hero_stats(account_id))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"  player_hero_stats FAILED ({e})")
    else:
        print("DOTA_ACCOUNT_ID not set -- skipping player_hero_stats.json (optional personal-affinity feature)")

    # last thing written: the app's "data freshness" footer reads this
    # instead of a hardcoded date/patch string in the HTML, so it's always
    # accurate as of whenever this script last actually ran.
    try:
        patch = get_current_patch()
        meta = {
            "last_refreshed": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
            "patch_name": patch["name"] if patch else None,
        }
        path = os.path.join(DATA_DIR, "meta.json")
        with open(path, "w") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
        print(f"wrote {path} ({meta['last_refreshed']}, patch {meta['patch_name']})")
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  meta.json FAILED to fetch current patch ({e}) -- leaving unset")

    print("done")


if __name__ == "__main__":
    main()
