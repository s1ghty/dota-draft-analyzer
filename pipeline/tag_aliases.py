#!/usr/bin/env python3
"""Alias draft for search matching (English nicknames/abbreviations + old
hero names + Russian names/nicknames), on top of SPEC.md's original scope.
Writes data/aliases.json: hero_id -> [alias strings], matched
case-insensitively against whatever the user types, in addition to the
hero's official localized_name.

Source, v2: rebuilt from Valve's own official alternate hero-search names
for the Russian client's "Arsenal" hero picker, as reported by cybersport.ru
(https://www.cybersport.ru/tags/dota-2/drova-padzhero-i-vodichka-valve-dobavila-novyye-alternativnyye-nazvaniya-geroyev),
cross-checked against a 3-part community glossary (dota-blog.ru, 2018) for
older heroes. This is a meaningfully stronger source than v1's guesses --
Valve's list is official game data, not community slang that could be
regional/dated/wrong. v1 had at least one confirmed error this caught:
Earth Spirit was tagged with "Пандарин"/"стон панда", which are actually
Brewmaster's (the panda hero) -- fixed here.

Confidence: entries below are from the two sourced lists above, not
invented. Still worth a skim -- I did not play every hero to confirm a
nickname "feels" current, and the Valve list mixes official lore character
names (e.g. "Traxex" for Drow Ranger) in with slang. Nothing here is
flagged REVIEW individually since both sources are attested, but this is a
draft pass, not a guarantee -- correct anything that's wrong or stale.

Re-run after editing ALIASES below; safe to run multiple times.
"""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# hero_id: [alias, alias, ...]
ALIASES = {
    1: ["AM", "ам", "антимаг", "вэй"],  # Anti-Mage
    2: ["акс", "топор", "могул-хан"],  # Axe
    3: ["бейн", "бэйн", "атропос"],  # Bane
    4: ["BS", "бладсикер", "блудсикер", "стригвир"],  # Bloodseeker
    5: ["CM", "кристал", "мэйденка", "мейденка", "цмка", "рилай"],  # Crystal Maiden
    6: ["Traxex", "дрова", "траксекс", "тракса", "дроу", "дровка"],  # Drow Ranger
    7: ["ES", "Raigor", "эрсшейкер", "рейгор", "шейкер"],  # Earthshaker
    8: ["Yurnero", "джаггернаут", "джага", "юрнеро", "джагга"],  # Juggernaut
    9: ["Princess", "Moon", "Potm", "мирана", "потма"],  # Mirana
    10: ["морфлинг", "водичка"],  # Morphling
    11: ["SF", "Nevermore", "шадоу", "финд", "невермор"],  # Shadow Fiend
    12: ["PL", "Azwraith", "фантом", "лансер", "лэнсер", "азраф"],  # Phantom Lancer
    13: ["Faerie Dragon", "FD", "пак"],  # Puck
    14: ["Toy", "Butcher", "падж", "пудж", "мясник", "плюшевый", "бучка", "бачер", "паджеро", "пудге"],  # Pudge
    15: ["Lightning Revenant", "рейзор", "рэйзор", "разор"],  # Razor
    16: ["SK", "Crixalis", "сэнд кинг", "ск", "криксалис"],  # Sand King
    17: ["SS", "Raijin", "Thunderkeg", "шторм", "сторм", "райдзин", "громокег"],  # Storm Spirit
    18: ["Rogue Knight", "свен", "мятежный рыцарь"],  # Sven
    19: ["Stone Giant", "тини", "тайни", "камень"],  # Tiny
    20: ["VS", "Shendelzare", "венджфул спирит", "венга", "шенделезара"],  # Vengeful Spirit
    21: ["WR", "Lyralei", "виндрейнджер", "врка", "лиралей", "виндра", "алерия"],  # Windranger
    22: ["Lord of Heaven", "зевс", "зюс"],  # Zeus
    23: ["Admiral", "кункка", "адмирал"],  # Kunkka
    25: ["Slayer", "лина"],  # Lina
    26: ["Demon Witch", "лайон", "лион"],  # Lion
    27: ["SS", "Rhasta", "шедоу", "шаман", "раста"],  # Shadow Shaman
    28: ["Slithereen Guard", "слардар", "селёдка"],  # Slardar
    29: ["TH", "Leviathan", "тайдхантер", "левиафан", "тх", "арбуз"],  # Tidehunter
    30: ["WD", "Zharvakko", "витч", "вич", "доктор", "жарвакко"],  # Witch Doctor
    31: ["Ethreain", "лич", "этриан"],  # Lich
    32: ["Stealth Assassin", "SA", "рикимару"],  # Riki
    33: ["энигма"],  # Enigma
    34: ["Boush", "тинкер", "боуш", "механик"],  # Tinker
    35: ["Kardel Sharpeye", "снайпер", "кардел остроглаз"],  # Sniper
    36: ["Rotundjere", "некрофос", "ротундйер", "некр", "некролит"],  # Necrophos
    37: ["WL", "Demnok Lannik", "варлок", "демнок лэнник"],  # Warlock
    38: ["BM", "бистмастер", "каррох"],  # Beastmaster
    39: ["QOP", "Akasha", "квин оф пейн", "квопа", "акаша"],  # Queen of Pain
    40: ["Lesale", "веномансер", "веномант", "веник", "лисайл"],  # Venomancer
    41: ["FV", "фейслесс", "фэйслесс", "войд", "дарктеррор"],  # Faceless Void
    42: ["SK", "Ostarion", "врейс кинг", "вк", "скелет", "остарион"],  # Wraith King
    43: ["DP", "Krobelus", "дэс", "дэф", "профетка", "кроба", "кробелус", "банша"],  # Death Prophet
    44: ["PA", "Mortred", "фантомка", "мортред", "мортра", "морта"],  # Phantom Assassin
    45: ["пугна", "пагна"],  # Pugna
    46: ["TA", "Lanaya", "темплар", "ланайя", "темпларка"],  # Templar Assassin
    47: ["Netherdrake", "вайпер"],  # Viper
    48: ["Moon Rider", "луна"],  # Luna
    49: ["DK", "Davion", "дрэгон", "драгон", "дэвион"],  # Dragon Knight
    50: ["даззл"],  # Dazzle
    51: ["CW", "клокверк", "болтозвяк", "клок"],  # Clockwerk
    52: ["TS", "лешрак", "леший"],  # Leshrac
    53: ["NP", "нейчурс", "нейчерс", "профет", "фура", "фурион"],  # Nature's Prophet
    54: ["LS", "Naix", "лайфстилер", "найкс", "нэйкс", "гуля"],  # Lifestealer
    55: ["DS", "Ishkafel", "дарк сир", "ишкафэль"],  # Dark Seer
    56: ["клинкз", "боник", "боня"],  # Clinkz
    57: ["Purist Thunderwrath", "омнинайт", "омник", "ревнитель громобой"],  # Omniknight
    58: ["Aiushtha", "энчантресс", "аюшта", "коза", "энча"],  # Enchantress
    59: ["хускар", "хусик"],  # Huskar
    60: ["NS", "Balanar", "найт сталкер", "баланар"],  # Night Stalker
    61: ["BM", "Spider", "брудмазер", "бруда", "чёрная арахния", "мать", "паучиха"],  # Broodmother
    62: ["BH", "баунти хантер", "бх", "гондар"],  # Bounty Hunter
    63: ["NW", "Skitskurr", "вивер", "скитскурр", "жук"],  # Weaver
    64: ["THD", "Twin Headed Dragon", "джакиро", "тхд"],  # Jakiro
    65: ["BR", "батрайдер", "бэтрайдер", "бэтик"],  # Batrider
    66: ["Holy Knight", "чен", "рыцарь веры"],  # Chen
    67: ["Mercurial", "спектра", "меркуриал"],  # Spectre
    68: ["AA", "Эншент апаришн", "аппарат", "калдр"],  # Ancient Apparition
    69: ["DB", "дум", "люцифер"],  # Doom
    70: ["Ulfsaar", "урса", "медведь", "ульфсаар", "мишка"],  # Ursa
    71: ["SB", "Bara", "Barathrum", "спирит брейкер", "бара", "баратрум", "корова"],  # Spirit Breaker
    72: ["Aurel", "джайрокоптер", "гирокоптер", "аурел", "гиро"],  # Gyrocopter
    73: ["Razzil Darkbrew", "алкемист", "алхимик", "раззил темновар", "химик"],  # Alchemist
    74: ["Kid", "инвокер", "карл"],  # Invoker
    75: ["Nortrom", "сайленсер", "нортром", "сало"],  # Silencer
    76: ["OD", "Harbinger", "Obsidian Destroyer", "Outworld Destroyer", "од", "аутворлд дестроер", "предвестник"],  # Outworld Devourer
    77: ["Banehallow", "лайкан", "волк", "ликантроп", "бейнхаллоу"],  # Lycan
    78: ["Mangix", "брюмастер", "пиво", "мангикс", "панда", "пивовар"],  # Brewmaster
    79: ["SD", "шадоу демон", "шд"],  # Shadow Demon
    80: ["LD", "Sylla", "лоун друид", "силла"],  # Lone Druid
    81: ["CK", "хаос", "цк"],  # Chaos Knight
    82: ["Meepwn", "мипо", "геомансер", "геомант", "мипарь"],  # Meepo
    83: ["трент", "трэнт", "руфтреллен"],  # Treant Protector
    84: ["огр", "аггрон камнелом"],  # Ogre Magi
    85: ["Dirge", "андаинг", "зомби"],  # Undying
    86: ["рубик", "рубен"],  # Rubick
    87: ["дизраптор", "дисраптор"],  # Disruptor
    88: ["NA", "никс", "нюкс"],  # Nyx Assassin
    89: ["Slithice", "нага", "сайрен", "сирена", "слизис"],  # Naga Siren
    90: ["Keeper", "Ezalor", "KOTL", "кипер оф зе лайт", "котл", "котёл", "эзалор"],  # Keeper of the Light
    91: ["Wisp", "ио", "висп"],  # Io
    92: ["Necrolic", "визедж", "визаж", "некролик"],  # Visage
    93: ["сларк", "марлок"],  # Slark
    94: ["Gorgon", "медуза", "горгона", "дуза"],  # Medusa
    95: ["Jahrakal", "тролль", "варлорд", "джаракал"],  # Troll Warlord
    96: ["кентавр-вождь", "брэдводен", "кентавр"],  # Centaur Warrunner
    97: ["Magnataur", "магнус", "автобус", "магнотавр"],  # Magnus
    98: ["Rizzrack", "Shredder", "тимберсо", "риззрак", "древопил", "шредер"],  # Timbersaw
    99: ["BB", "бристлбэк", "брислбэк", "ёжик", "ригварл"],  # Bristleback
    100: ["Ymir", "туск", "таск", "тусик", "имир", "бивень"],  # Tusk
    101: ["SM", "Dragonus", "скайрас", "драгонус", "петух", "скай"],  # Skywrath Mage
    102: ["абаддон", "аббадон", "абба"],  # Abaddon
    103: ["TC", "Cairne", "ET", "элдер", "тайтан", "титан"],  # Elder Titan
    104: ["Tresdin", "LC", "лиджен", "лега", "лц", "тресдин", "легионка"],  # Legion Commander
    105: ["Squee", "Spleen", "Spoon", "текиз", "течис", "течка", "скви", "сплин", "спун"],  # Techies
    106: ["Xin", "ES", "эмбер спирит", "син"],  # Ember Spirit
    107: ["Kaolin", "ES", "эрс спирит", "каолин", "земеля"],  # Earth Spirit
    108: ["PitLord", "Pit Lord", "Azgalor", "UL", "андерлорд", "питлорд", "врогрош"],  # Underlord
    109: ["TB", "террорблейд", "тб"],  # Terrorblade
    110: ["PH", "феникс"],  # Phoenix
    111: ["Nerif", "оракл", "оракул", "нериф"],  # Oracle
    112: ["Auroth", "WW", "винтер вайверн", "аурос", "виверна"],  # Winter Wyvern
    113: ["Зет", "AW", "арк", "варден", "ворден", "самость"],  # Arc Warden
    114: ["MK", "Sun Wukong", "манки кинг", "мк", "сунь укун"],  # Monkey King
    119: ["Mireska", "DW", "дарк виллоу", "вилка", "миреска солнечная", "фея"],  # Dark Willow
    120: ["AR", "пангольер", "донте панлин", "панго"],  # Pangolier
    121: ["GS", "гримстроук"],  # Grimstroke
    123: ["Squirrel", "HW", "худвинк", "белка"],  # Hoodwink
    126: ["Inai", "войд спирит", "инай"],  # Void Spirit
    128: ["Mortimer", "снэпфайр", "снэпфаер", "снэпка", "бабка", "мортимер", "беатрикс"],  # Snapfire
    129: ["марс"],  # Mars
    131: ["RM", "Marionetto", "Cogliostro", "рингмастер", "марионетто", "колиостро кеттл"],  # Ring Master
    135: ["Valora", "донбрейкер", "валора"],  # Dawnbreaker
    136: ["марси"],  # Marci
    137: ["PB", "праймал бист", "динозавр"],  # Primal Beast
    138: ["муэрта"],  # Muerta
    145: ["Bird Samurai", "Kestrel", "кез", "самурай", "кестрель", "казурай", "попугай"],  # Kez
    155: ["Bard", "Frog", "ларго", "бард", "лягушка", "жаба", "лягух"],  # Largo
}


def main():
    heroes_path = os.path.join(DATA_DIR, "heroes.json")
    with open(heroes_path) as f:
        heroes = json.load(f)

    missing = set(heroes) - {str(k) for k in ALIASES}
    aliases = {str(hid): al for hid, al in ALIASES.items() if str(hid) in heroes}

    out_path = os.path.join(DATA_DIR, "aliases.json")
    with open(out_path, "w") as f:
        json.dump(aliases, f, indent=2, sort_keys=True, ensure_ascii=False)

    print(f"wrote {out_path} ({len(aliases)} heroes)")
    if missing:
        print(f"WARNING: heroes.json has ids with no alias entry: {sorted(missing, key=int)}")


if __name__ == "__main__":
    main()
