"""Build the static Pokemon Champions database from public reference sites."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "champions-db.js"
CACHE = ROOT / "data" / ".cache" / "learnsets-v3"
SMOGON = "https://www.smogon.com/dex/champions/pokemon/"
SMOGON_RPC = "https://www.smogon.com/dex/_rpc/dump-pokemon"
WIKI = "https://wiki.52poke.com/zh-hans/%E5%AE%9D%E5%8F%AF%E6%A2%A6%E5%88%97%E8%A1%A8%EF%BC%88Champions%EF%BC%89"
PIKALYTICS = "https://pikalytics.com/champions"
LOCALES = "https://raw.githubusercontent.com/radiantwf/vgc-damage-calc/main/public/locales/zh/pokemon"

session = requests.Session()
session.headers["User-Agent"] = "pokemon-champions-tactics data updater"


def to_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def to_slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")


def get(url: str) -> requests.Response:
    response = session.get(url, timeout=45)
    response.raise_for_status()
    return response


def smogon_basics() -> dict:
    html = get(SMOGON).text
    start = html.index("dexSettings = ") + len("dexSettings = ")
    end = html.index("</script>", start)
    settings = json.loads(html[start:end])
    return settings["injectRpcs"][1][1]


def locale(name: str) -> dict:
    return get(f"{LOCALES}/{name}.zh.json").json()


def pikalytics_ranks() -> dict[str, int]:
    soup = BeautifulSoup(get(PIKALYTICS).text, "html.parser")
    cards = soup.select("section[aria-label='Top Pokemon usage'] [data-name]")
    return {to_id(card.get("data-name", "")): index + 1 for index, card in enumerate(cards)}


def wiki_versions() -> tuple[dict[tuple[int, bool], str], int]:
    response = get(WIKI)
    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.select("table.eplist tr")[1:]
    versions: dict[tuple[int, bool], str] = {}
    for row in rows:
        cells = row.select("th,td")
        if len(cells) < 6:
            continue
        dex_match = re.search(r"\d+", cells[0].get_text())
        if not dex_match:
            continue
        name = cells[3].get_text(" ", strip=True)
        version = cells[-1].get_text(" ", strip=True)
        versions[(int(dex_match.group()), "超级" in name)] = version
    return versions, len(rows)


def fetch_learnset(name: str) -> tuple[str, dict]:
    cache_file = CACHE / f"{to_id(name)}.json"
    if cache_file.exists():
        return name, json.loads(cache_file.read_text(encoding="utf-8")) or {"learnset": []}
    query_name = re.sub(r"-Mega(?:-[XY])?$", "", name)
    query_name = {
        "Morpeko-Hangry": "Morpeko",
        "Castform-Snowy": "Castform",
        "Castform-Sunny": "Castform",
        "Castform-Rainy": "Castform",
        "Mimikyu-Busted": "Mimikyu",
        "Aegislash-Blade": "Aegislash",
        "Palafin-Hero": "Palafin",
        "Sinistcha-Masterpiece": "Sinistcha",
        "Vivillon-Fancy": "Vivillon",
        "Vivillon-Pokeball": "Vivillon",
        "Gourgeist-Large": "Gourgeist",
        "Gourgeist-Small": "Gourgeist",
        "Gourgeist-Super": "Gourgeist",
        "Polteageist-Antique": "Polteageist",
        "Maushold-Four": "Maushold",
        "Floette-Mega": "Floette-Eternal",
    }.get(name, query_name)
    payload = {"alias": to_slug(query_name), "gen": "champions", "language": "en"}
    for attempt in range(3):
        try:
            response = session.post(SMOGON_RPC, json=payload, timeout=45)
            response.raise_for_status()
            result = response.json() or {"learnset": [], "strategies": [], "formeStrategies": {}}
            CACHE.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(result), encoding="utf-8")
            return name, result
        except requests.RequestException:
            if attempt == 2:
                return name, {"learnset": [], "strategies": [], "formeStrategies": {}}
            time.sleep(1 + attempt)
    return name, {"learnset": []}


def translated(mapping: dict, english: str) -> str:
    return mapping.get(english.lower()) or mapping.get(to_id(english)) or english


def main() -> None:
    basics = smogon_basics()
    zh_pokemon = locale("pokemon")
    zh_move = locale("move")
    zh_ability = locale("ability")
    zh_item = locale("item")
    zh_type = locale("type")
    ranks = pikalytics_ranks()
    versions, wiki_rows = wiki_versions()

    pokemon_source = basics["pokemon"]
    dex_by_name = {
        source["name"]: source["oob"]["dex_number"]
        for source in pokemon_source
        if source.get("oob") and source["oob"].get("dex_number")
    }
    form_bases = {
        "Morpeko-Hangry": "Morpeko",
        "Castform-Snowy": "Castform",
        "Castform-Sunny": "Castform",
        "Castform-Rainy": "Castform",
        "Mimikyu-Busted": "Mimikyu",
        "Aegislash-Blade": "Aegislash",
        "Palafin-Hero": "Palafin",
    }
    fixed_dex = {"Floette-Mega": 670}

    def inherited_dex(name: str) -> int:
        if name in fixed_dex:
            return fixed_dex[name]
        if name in dex_by_name:
            return dex_by_name[name]
        base = form_bases.get(name, re.sub(r"-Mega(?:-[XY])?$", "", name))
        if base not in dex_by_name and base.endswith("-M"):
            base = base[:-2]
        if base not in dex_by_name:
            raise ValueError(f"No National Dex number for {name} (base {base})")
        return dex_by_name[base]
    learnsets: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(fetch_learnset, p["name"]) for p in pokemon_source]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            name, result = future.result()
            learnsets[name] = result
            if index % 40 == 0:
                print(f"learnsets {index}/{len(futures)}")

    moves = []
    moves_by_name = {}
    for move in basics["moves"]:
        move_id = to_id(move["name"])
        item = {
            "id": move_id,
            "name": translated(zh_move, move["name"]),
            "en": move["name"],
            "type": translated(zh_type, move["type"]),
            "category": move["category"],
            "power": move.get("power") or 0,
            "accuracy": move.get("accuracy") or 0,
            "priority": move.get("priority") or 0,
        }
        moves.append(item)
        moves_by_name[move["name"]] = item

    items = [
        {"id": to_id(item["name"]), "name": translated(zh_item, item["name"]), "en": item["name"]}
        for item in basics["items"]
    ]

    missing_names = {
        "Vivillon-Fancy": "彩粉蝶·幻彩花纹",
        "Vivillon-Pokeball": "彩粉蝶·球球花纹",
        "Meowstic-M": "超能妙喵♂",
        "Polteageist-Antique": "怖思壶·真品",
        "Mr. Rime": "踏冰人偶",
        "Morpeko-Hangry": "莫鲁贝可·空腹花纹",
        "Castform-Snowy": "飘浮泡泡·雪云",
        "Castform-Sunny": "飘浮泡泡·太阳",
        "Castform-Rainy": "飘浮泡泡·雨水",
        "Mimikyu-Busted": "谜拟丘·现形",
        "Palafin-Hero": "海豚侠·全能形态",
    }

    pokemon = []
    for source in pokemon_source:
        english = source["name"]
        pokemon_id = english.lower().replace(" ", "-").replace(".", "")
        result = learnsets.get(english) or {}
        legal_moves = [to_id(name) for name in result.get("learnset", []) if name in moves_by_name]
        source_types = source["types"]
        damaging = [
            moves_by_name[name]
            for name in result.get("learnset", [])
            if name in moves_by_name and moves_by_name[name]["power"] > 0
        ]
        stab = sorted(
            [move for move in damaging if move["en"] and next((m for m in basics["moves"] if m["name"] == move["en"]), {}).get("type") in source_types],
            key=lambda move: move["power"],
            reverse=True,
        )
        defaults = []
        if "protect" in legal_moves:
            defaults.append("protect")
        for move in stab + sorted(damaging, key=lambda move: move["power"], reverse=True):
            if move["id"] not in defaults:
                defaults.append(move["id"])
            if len(defaults) == 4:
                break
        for move_id in legal_moves:
            if len(defaults) == 4:
                break
            if move_id not in defaults:
                defaults.append(move_id)

        is_mega = "-Mega" in english
        dex = inherited_dex(english)
        pokemon.append(
            {
                "id": pokemon_id,
                "name": zh_pokemon.get(english.lower()) or missing_names.get(english) or english,
                "en": english,
                "dex": dex,
                "types": [translated(zh_type, value) for value in source_types],
                "abilities": [translated(zh_ability, value) for value in source["abilities"]],
                "stats": [source[key] for key in ("hp", "atk", "def", "spa", "spd", "spe")],
                "mega": is_mega,
                "version": versions.get((dex, is_mega), ""),
                "metaRank": ranks.get(to_id(english)),
                "learnset": legal_moves,
                "defaultMoves": defaults,
                "sprite": f"https://play.pokemonshowdown.com/sprites/gen5/{pokemon_id}.png",
            }
        )

    database = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "regulation": "M-B",
        "sources": {
            "smogon": SMOGON,
            "wiki52poke": WIKI,
            "pokestats": "https://pokestats.top/calc/?bo1=true",
            "pikalytics": PIKALYTICS,
            "pokechampdb": "https://pokechampdb.com/?view=pokemon",
        },
        "counts": {
            "pokemon": len(pokemon),
            "moves": len(moves),
            "items": len(items),
            "abilities": len(basics["abilities"]),
            "wikiRows": wiki_rows,
        },
        "pokemon": pokemon,
        "moves": moves,
        "items": items,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(database, ensure_ascii=False, separators=(",", ":"))
    OUTPUT.write_text(f"window.CHAMPIONS_DB={payload};\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    print(database["counts"])


if __name__ == "__main__":
    main()
