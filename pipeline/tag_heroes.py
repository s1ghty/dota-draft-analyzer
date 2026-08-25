#!/usr/bin/env python3
"""One-time hand-tag draft for SPEC.md §2/§3.3b. Merges `tags` and
`threat_profile` into data/heroes.json by hero id. Claude drafted this from
game knowledge -- review it, this is the part that most benefits from your
own game sense (SPEC.md §6 step 3). Ring Master, Largo, Kez, and Muerta were
originally low-confidence guesses; re-tagged after pulling their actual
ability text (see inline comments on each) -- still worth a sanity read
since kit text was summarized by a search, not read first-hand from the game.

Re-run after editing TAGS below; safe to run multiple times.
"""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# hero_id: (tags, threat_profile)
TAGS = {
    1: (["high-mobility"], ["late-game-scaler", "split-pusher"]),  # Anti-Mage
    2: (["disable-heavy", "lockdown-combo"], ["lane-bully", "ganker"]),  # Axe
    3: (["disable-heavy", "channeled-ultimate", "lockdown-combo"], ["pick-off", "teamfight-nuker"]),  # Bane
    4: (["physical-burst", "high-mobility"], ["pick-off", "ganker"]),  # Bloodseeker
    5: (["disable-heavy", "magic-burst"], ["teamfight-nuker", "lane-bully"]),  # Crystal Maiden
    6: (["physical-burst"], ["late-game-scaler"]),  # Drow Ranger
    7: (["disable-heavy", "magic-burst", "lockdown-combo"], ["teamfight-nuker", "ganker"]),  # Earthshaker
    8: (["physical-burst", "healing-heavy"], ["late-game-scaler", "split-pusher"]),  # Juggernaut
    9: (["physical-burst", "disable-heavy", "invisibility"], ["ganker", "pick-off"]),  # Mirana
    10: (["high-mobility"], ["late-game-scaler"]),  # Morphling
    11: (["magic-burst"], ["ganker", "teamfight-nuker"]),  # Shadow Fiend
    12: (["illusion-based", "high-mobility"], ["split-pusher", "late-game-scaler"]),  # Phantom Lancer
    13: (["high-mobility", "disable-heavy"], ["ganker", "pick-off"]),  # Puck
    14: (["disable-heavy", "physical-burst", "lockdown-combo"], ["ganker", "pick-off"]),  # Pudge
    15: (["physical-burst"], ["lane-bully", "teamfight-nuker"]),  # Razor
    16: (["disable-heavy", "magic-burst", "lockdown-combo"], ["ganker", "teamfight-nuker"]),  # Sand King
    17: (["high-mobility", "magic-burst"], ["ganker", "pick-off"]),  # Storm Spirit
    18: (["physical-burst", "disable-heavy"], ["lane-bully", "teamfight-nuker"]),  # Sven
    19: (["physical-burst", "disable-heavy"], ["ganker", "teamfight-nuker"]),  # Tiny
    20: (["disable-heavy"], ["lane-bully", "pick-off"]),  # Vengeful Spirit
    21: (["disable-heavy", "magic-burst"], ["ganker", "lane-bully"]),  # Windranger
    22: (["magic-burst", "global"], ["teamfight-nuker", "ganker"]),  # Zeus
    23: (["physical-burst", "disable-heavy", "lockdown-combo"], ["teamfight-nuker", "lane-bully"]),  # Kunkka
    25: (["magic-burst", "disable-heavy"], ["ganker", "teamfight-nuker"]),  # Lina
    26: (["disable-heavy", "magic-burst", "lockdown-combo"], ["pick-off", "ganker"]),  # Lion
    27: (["disable-heavy", "summons", "lockdown-combo"], ["pick-off", "teamfight-nuker"]),  # Shadow Shaman
    28: (["physical-burst", "disable-heavy"], ["lane-bully", "ganker"]),  # Slardar
    29: (["disable-heavy"], ["teamfight-nuker"]),  # Tidehunter
    30: (["disable-heavy", "channeled-ultimate", "magic-burst"], ["teamfight-nuker", "pick-off"]),  # Witch Doctor
    31: (["magic-burst", "disable-heavy"], ["lane-bully", "teamfight-nuker"]),  # Lich
    32: (["invisibility", "physical-burst"], ["pick-off", "ganker"]),  # Riki
    33: (["disable-heavy", "channeled-ultimate", "summons", "lockdown-combo"], ["teamfight-nuker", "ganker"]),  # Enigma
    34: (["magic-burst"], ["split-pusher", "pusher"]),  # Tinker
    35: (["physical-burst"], ["late-game-scaler"]),  # Sniper
    36: (["magic-burst", "healing-heavy"], ["teamfight-nuker", "late-game-scaler"]),  # Necrophos
    37: (["summons", "disable-heavy", "channeled-ultimate"], ["teamfight-nuker"]),  # Warlock
    38: (["disable-heavy", "summons", "lockdown-combo"], ["ganker", "lane-bully"]),  # Beastmaster
    39: (["high-mobility", "magic-burst"], ["ganker", "pick-off"]),  # Queen of Pain
    40: (["disable-heavy", "summons"], ["lane-bully", "teamfight-nuker"]),  # Venomancer
    41: (["disable-heavy", "channeled-ultimate", "physical-burst", "lockdown-combo"], ["late-game-scaler", "teamfight-nuker"]),  # Faceless Void
    42: (["physical-burst", "disable-heavy"], ["late-game-scaler", "teamfight-nuker"]),  # Wraith King
    43: (["magic-burst", "summons", "silence"], ["split-pusher", "teamfight-nuker"]),  # Death Prophet
    44: (["physical-burst"], ["late-game-scaler", "pick-off"]),  # Phantom Assassin
    45: (["magic-burst", "healing-heavy"], ["teamfight-nuker", "split-pusher"]),  # Pugna
    46: (["physical-burst"], ["pick-off", "late-game-scaler"]),  # Templar Assassin
    47: (["physical-burst", "magic-burst"], ["lane-bully", "late-game-scaler"]),  # Viper
    48: (["physical-burst"], ["late-game-scaler", "teamfight-nuker"]),  # Luna
    49: (["physical-burst", "disable-heavy"], ["lane-bully", "late-game-scaler"]),  # Dragon Knight
    50: (["healing-heavy", "disable-heavy"], ["lane-bully", "teamfight-nuker"]),  # Dazzle
    51: (["disable-heavy", "lockdown-combo"], ["ganker", "pick-off"]),  # Clockwerk
    52: (["magic-burst", "disable-heavy"], ["teamfight-nuker", "lane-bully"]),  # Leshrac
    53: (["summons", "global"], ["split-pusher", "pusher"]),  # Nature's Prophet
    54: (["physical-burst", "healing-heavy"], ["late-game-scaler"]),  # Lifestealer
    55: (["disable-heavy"], ["teamfight-nuker", "lane-bully"]),  # Dark Seer
    56: (["invisibility", "physical-burst", "high-mobility"], ["pick-off", "split-pusher"]),  # Clinkz
    57: (["healing-heavy", "disable-heavy"], ["teamfight-nuker"]),  # Omniknight
    58: (["healing-heavy", "summons"], ["split-pusher", "pusher"]),  # Enchantress
    59: (["physical-burst"], ["lane-bully", "late-game-scaler"]),  # Huskar
    60: (["disable-heavy", "high-mobility"], ["ganker", "pick-off"]),  # Night Stalker
    61: (["summons", "high-mobility"], ["split-pusher", "ganker"]),  # Broodmother
    62: (["invisibility", "physical-burst"], ["pick-off", "ganker"]),  # Bounty Hunter
    63: (["high-mobility", "physical-burst"], ["pick-off", "late-game-scaler"]),  # Weaver
    64: (["magic-burst", "disable-heavy"], ["lane-bully", "teamfight-nuker"]),  # Jakiro
    65: (["disable-heavy", "lockdown-combo", "high-mobility"], ["ganker", "pick-off"]),  # Batrider
    66: (["summons", "healing-heavy"], ["ganker"]),  # Chen
    67: (["illusion-based", "physical-burst", "global"], ["late-game-scaler", "teamfight-nuker"]),  # Spectre
    68: (["magic-burst", "disable-heavy"], ["teamfight-nuker", "pick-off"]),  # Ancient Apparition
    69: (["disable-heavy", "channeled-ultimate", "physical-burst"], ["ganker", "pick-off"]),  # Doom
    70: (["physical-burst", "high-mobility"], ["pick-off", "lane-bully"]),  # Ursa
    71: (["disable-heavy", "high-mobility", "lockdown-combo"], ["ganker", "pick-off"]),  # Spirit Breaker
    72: (["physical-burst", "magic-burst"], ["teamfight-nuker", "late-game-scaler"]),  # Gyrocopter
    73: (["physical-burst"], ["late-game-scaler"]),  # Alchemist
    74: (["magic-burst", "disable-heavy", "high-mobility"], ["ganker", "teamfight-nuker"]),  # Invoker
    75: (["silence", "magic-burst"], ["lane-bully", "teamfight-nuker"]),  # Silencer
    76: (["magic-burst", "silence"], ["ganker", "late-game-scaler"]),  # Outworld Devourer
    77: (["summons", "high-mobility"], ["split-pusher", "late-game-scaler"]),  # Lycan
    78: (["disable-heavy", "channeled-ultimate", "summons"], ["teamfight-nuker", "ganker"]),  # Brewmaster
    79: (["disable-heavy", "magic-burst"], ["pick-off", "teamfight-nuker"]),  # Shadow Demon
    80: (["summons", "high-mobility"], ["split-pusher", "late-game-scaler"]),  # Lone Druid
    81: (["illusion-based", "disable-heavy", "physical-burst"], ["late-game-scaler", "teamfight-nuker"]),  # Chaos Knight
    82: (["summons", "physical-burst", "high-mobility"], ["late-game-scaler", "split-pusher"]),  # Meepo
    83: (["healing-heavy", "disable-heavy"], ["lane-bully"]),  # Treant Protector
    84: (["disable-heavy", "magic-burst"], ["lane-bully", "teamfight-nuker"]),  # Ogre Magi
    85: (["disable-heavy", "summons"], ["lane-bully"]),  # Undying
    86: (["disable-heavy", "magic-burst"], ["teamfight-nuker", "pick-off"]),  # Rubick
    87: (["disable-heavy", "magic-burst"], ["teamfight-nuker", "lane-bully"]),  # Disruptor
    88: (["disable-heavy", "invisibility", "lockdown-combo"], ["pick-off", "ganker"]),  # Nyx Assassin
    89: (["illusion-based", "channeled-ultimate"], ["split-pusher", "late-game-scaler"]),  # Naga Siren
    90: (["magic-burst"], ["teamfight-nuker", "lane-bully"]),  # Keeper of the Light
    91: (["healing-heavy", "high-mobility"], ["late-game-scaler"]),  # Io
    92: (["summons", "physical-burst"], ["ganker", "teamfight-nuker"]),  # Visage
    93: (["high-mobility", "physical-burst"], ["pick-off", "late-game-scaler"]),  # Slark
    94: (["physical-burst", "disable-heavy"], ["late-game-scaler", "teamfight-nuker"]),  # Medusa
    95: (["physical-burst"], ["late-game-scaler", "lane-bully"]),  # Troll Warlord
    96: (["disable-heavy", "physical-burst"], ["lane-bully", "teamfight-nuker"]),  # Centaur Warrunner
    97: (["disable-heavy", "lockdown-combo", "physical-burst"], ["teamfight-nuker", "ganker"]),  # Magnus
    98: (["physical-burst", "disable-heavy"], ["lane-bully", "teamfight-nuker"]),  # Timbersaw
    99: (["physical-burst"], ["lane-bully", "teamfight-nuker"]),  # Bristleback
    100: (["disable-heavy", "lockdown-combo", "high-mobility"], ["ganker", "pick-off"]),  # Tusk
    101: (["magic-burst", "disable-heavy", "silence"], ["ganker", "teamfight-nuker"]),  # Skywrath Mage
    102: (["healing-heavy", "disable-heavy"], ["teamfight-nuker"]),  # Abaddon
    103: (["disable-heavy"], ["ganker", "teamfight-nuker"]),  # Elder Titan
    104: (["disable-heavy", "physical-burst", "lockdown-combo"], ["pick-off", "ganker"]),  # Legion Commander
    105: (["magic-burst", "disable-heavy"], ["split-pusher", "pick-off"]),  # Techies
    106: (["high-mobility", "physical-burst", "disable-heavy"], ["ganker", "pick-off"]),  # Ember Spirit
    107: (["disable-heavy", "lockdown-combo", "high-mobility"], ["ganker", "pick-off"]),  # Earth Spirit
    108: (["disable-heavy", "summons", "global"], ["teamfight-nuker", "lane-bully"]),  # Underlord
    109: (["illusion-based", "physical-burst"], ["late-game-scaler", "split-pusher"]),  # Terrorblade
    110: (["disable-heavy", "channeled-ultimate", "healing-heavy"], ["teamfight-nuker"]),  # Phoenix
    111: (["healing-heavy", "disable-heavy"], ["lane-bully"]),  # Oracle
    112: (["disable-heavy", "magic-burst"], ["teamfight-nuker", "pick-off"]),  # Winter Wyvern
    113: (["illusion-based", "high-mobility"], ["late-game-scaler", "split-pusher"]),  # Arc Warden
    114: (["physical-burst", "high-mobility", "disable-heavy"], ["ganker", "lane-bully"]),  # Monkey King
    119: (["disable-heavy", "magic-burst", "invisibility"], ["pick-off", "ganker"]),  # Dark Willow
    120: (["disable-heavy", "physical-burst", "high-mobility", "lockdown-combo"], ["ganker", "teamfight-nuker"]),  # Pangolier
    121: (["disable-heavy", "magic-burst"], ["teamfight-nuker", "pick-off"]),  # Grimstroke
    123: (["disable-heavy", "physical-burst", "high-mobility"], ["ganker", "pick-off"]),  # Hoodwink
    126: (["high-mobility", "magic-burst", "disable-heavy"], ["ganker", "pick-off"]),  # Void Spirit
    128: (["disable-heavy", "physical-burst"], ["lane-bully", "teamfight-nuker"]),  # Snapfire
    129: (["disable-heavy", "physical-burst", "lockdown-combo"], ["lane-bully", "teamfight-nuker"]),  # Mars
    131: (["disable-heavy", "magic-burst"], ["ganker", "teamfight-nuker"]),  # Ring Master -- fear (Q), %hp dagger + slow (E), AoE knockback/slow ult (R)
    135: (["physical-burst", "healing-heavy", "disable-heavy"], ["lane-bully", "teamfight-nuker"]),  # Dawnbreaker
    136: (["physical-burst", "disable-heavy", "high-mobility"], ["ganker", "pick-off"]),  # Marci
    137: (["disable-heavy", "physical-burst", "lockdown-combo"], ["lane-bully", "teamfight-nuker"]),  # Primal Beast
    138: (["physical-burst", "magic-burst", "disable-heavy", "silence"], ["late-game-scaler", "teamfight-nuker"]),  # Muerta -- fear/slow bounce nuke (Q), AoE silence zone (W), physical<->magic dmg ult
    145: (["physical-burst", "high-mobility", "disable-heavy"], ["ganker", "late-game-scaler"]),  # Kez -- line-AoE burst + gap closer (both stances), parry-stun (Sai E)
    155: (["disable-heavy", "magic-burst", "healing-heavy"], ["lane-bully", "teamfight-nuker"]),  # Largo -- drag+AoE ministun/slow kit, ult songs buff/heal/damage-amp allies
}


def main():
    path = os.path.join(DATA_DIR, "heroes.json")
    with open(path) as f:
        heroes = json.load(f)

    missing = set(heroes) - {str(k) for k in TAGS}
    for hid, (tags, threat_profile) in TAGS.items():
        key = str(hid)
        if key in heroes:
            heroes[key]["tags"] = tags
            heroes[key]["threat_profile"] = threat_profile

    with open(path, "w") as f:
        json.dump(heroes, f, indent=2, sort_keys=True)

    print(f"tagged {len(TAGS)} heroes")
    if missing:
        print(f"WARNING: heroes.json has ids with no tag entry (untagged): {sorted(missing, key=int)}")


if __name__ == "__main__":
    main()
