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
import json
import os
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

def build_hero_baseline():
    stats = fetch_json(f"{OPENDOTA}/heroStats")
    baseline = {}
    for h in stats:
        wins = sum(h.get(f"{b}_win", 0) or 0 for b in range(1, 9))
        picks = sum(h.get(f"{b}_pick", 0) or 0 for b in range(1, 9))
        baseline[str(h["id"])] = round(wins / picks, 4) if picks else 0.5
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
# ponytail: 30-day window is picked empirically to land under Explorer's
# ~15s read timeout on this self-join, not derived from anything -- if a
# patch just dropped and you want fresher data faster, or the query starts
# timing out again, shrink the window; OpenDota's paid tier lifts the
# timeout if this stays annoying.
SYNERGY_WINDOW_DAYS = 30

SYNERGY_SQL = f"""
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
WHERE pb1.is_pick AND pb2.is_pick
  AND m.start_time > extract(epoch from now() - interval '{SYNERGY_WINDOW_DAYS} days')
GROUP BY pb1.hero_id, pb2.hero_id
"""


def build_synergy_matrix():
    url = f"{OPENDOTA}/explorer?sql=" + urllib.parse.quote(SYNERGY_SQL)
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
# Confirmed against STRATZ's live schema via introspection: heroStats.stats
# with groupByPosition:true, grouped by (heroId, position). `position` is a
# string enum (POSITION_1..5), not a queryable field on winWeek like the
# original guess assumed.

STRATZ_QUERY = """
query HeroPositionStats($positionIds: [MatchPlayerPositionType]) {
  heroStats {
    stats(positionIds: $positionIds, groupByPosition: true) {
      heroId
      position
      matchCount
      winCount
    }
  }
}
"""

STRATZ_POSITION_MAP = {
    "POSITION_1": "1", "POSITION_2": "2", "POSITION_3": "3",
    "POSITION_4": "4", "POSITION_5": "5",
}


def build_role_stats(api_key):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API",
    }
    variables = {"positionIds": list(STRATZ_POSITION_MAP.keys())}
    body = json.dumps({"query": STRATZ_QUERY, "variables": variables}).encode()
    result = fetch_json(STRATZ_URL, headers=headers, data=body, timeout=60)
    if "errors" in result:
        raise RuntimeError(f"STRATZ GraphQL error: {result['errors']}")

    rows = result["data"]["heroStats"]["stats"]

    # pickrate = this hero's share of games played *at that position*, not
    # of all games everywhere -- that's what makes weights.json's
    # role_fit_pickrate_floor (0.01) a meaningful "near never played" cutoff.
    total_by_position = {}
    for row in rows:
        pos = STRATZ_POSITION_MAP.get(row["position"])
        if pos is None:
            continue
        total_by_position[pos] = total_by_position.get(pos, 0) + row["matchCount"]

    role_stats = {}
    for row in rows:
        pos = STRATZ_POSITION_MAP.get(row["position"])
        if pos is None:
            continue
        hid = str(row["heroId"])
        matches, wins = row["matchCount"], row["winCount"]
        total = total_by_position.get(pos, 0)
        role_stats.setdefault(hid, {})[pos] = {
            "winrate": round(wins / matches, 4) if matches else 0.0,
            "pickrate": round(matches / total, 6) if total else 0.0,
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
