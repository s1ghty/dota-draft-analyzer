#!/usr/bin/env python3
"""One-time data pipeline (SPEC.md §1). Run manually, only after major
patches. Pulls dotaconstants + OpenDota + STRATZ into data/*.json. The app
itself never touches the network -- this script is the only thing that does.

Usage:
    python3 pipeline/build_data.py            # build everything
    python3 pipeline/build_data.py --skip-stratz   # skip role stats (needs STRATZ_API_KEY)
    python3 pipeline/build_data.py --skip-synergy  # skip the slow Explorer SQL query

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


def build_hero_baseline():
    stats = fetch_json(f"{OPENDOTA}/heroStats")
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
        matrix.setdefault(a, {})[b] = shrunk_wr
        matrix.setdefault(b, {})[a] = shrunk_wr
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


def build_role_stats(api_key):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API",
    }
    # one HTTP round-trip for all 5 positions via aliases, each pulling
    # every hero x every patch version STRATZ has data for.
    position_fields = "\n".join(
        f"    {stratz_pos}: winGameVersion(positionIds: [{stratz_pos}], take: 20000) "
        f"{{ gameVersionId heroId matchCount winCount }}"
        for stratz_pos in STRATZ_POSITION_MAP
    )
    query = f"query {{\n  heroStats {{\n{position_fields}\n  }}\n}}"
    body = json.dumps({"query": query}).encode()
    result = fetch_json(STRATZ_URL, headers=headers, data=body, timeout=60)
    if "errors" in result:
        raise RuntimeError(f"STRATZ GraphQL error: {result['errors']}")
    hero_stats = result["data"]["heroStats"]

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

    role_stats = {}
    for (hid, pos), e in per_hero.items():
        since_wr = e["since_w"] / e["since_m"] if e["since_m"] else 0.5
        broad_wr = e["broad_w"] / e["broad_m"] if e["broad_m"] else 0.5
        winrate = blend_by_sample_size(since_wr, e["since_m"], broad_wr, e["broad_m"], ROLE_STATS_PATCH_K)
        total = total_by_position.get(pos, 0)
        role_stats.setdefault(hid, {})[pos] = {
            "winrate": round(winrate, 4),
            "pickrate": round(e["total_m"] / total, 6) if total else 0.0,
        }
    return role_stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-stratz", action="store_true")
    parser.add_argument("--skip-synergy", action="store_true")
    parser.add_argument("--skip-matchups", action="store_true")
    parser.add_argument("--skip-builds", action="store_true")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    print("dotaconstants: heroes + items")
    heroes = build_heroes()
    save("heroes.json", heroes)
    items = build_items()
    save("items.json", items)

    print("OpenDota: hero_baseline")
    save("hero_baseline.json", build_hero_baseline())

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
            print("STRATZ_API_KEY not set -- skipping hero_role_stats.json. "
                  "Get a free key at https://stratz.com/api and re-run without --skip-stratz.")
        else:
            print("STRATZ: hero_role_stats")
            try:
                save("hero_role_stats.json", build_role_stats(api_key))
            except (urllib.error.URLError, RuntimeError) as e:
                print(f"  hero_role_stats FAILED ({e})")
    else:
        print("skipping hero_role_stats")

    print("done")


if __name__ == "__main__":
    main()
