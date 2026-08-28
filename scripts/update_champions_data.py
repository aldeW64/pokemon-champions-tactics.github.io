"""Build the static Pokemon Champions database from public reference sites."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "champions-db.js"
CACHE = ROOT / "data" / ".cache" / "learnsets-v3"
PIKALYTICS_FORMAT = "battledataregmbs3"
META_CACHE = ROOT / "data" / ".cache" / PIKALYTICS_FORMAT
ABILITY_CACHE = ROOT / "data" / ".cache" / "ability-descriptions"
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
    api_url = "https://pokekipe.com/api/v1/meta/gen9championsvgc2026regmb?limit=2000&offset=0&elo_cutoff=1760"
    try:
        payload = get(api_url).json()
        rows = payload.get("pokemon", []) if isinstance(payload, dict) else []
        ranks = {to_id(row.get("showdown_id") or row.get("pokemon_name") or row.get("name", "")): row.get("rank") for row in rows}
        ranks = {name: int(rank) for name, rank in ranks.items() if name and rank is not None}
        if ranks:
            return ranks
    except (requests.RequestException, ValueError, TypeError):
        pass
    soup = BeautifulSoup(get(PIKALYTICS).text, "html.parser")
    cards = soup.select("[data-name]")
    ranks: dict[str, int] = {}
    for card in cards:
        name = to_id(card.get("data-name", ""))
        if name and name not in ranks:
            ranks[name] = len(ranks) + 1
    return ranks


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


def first_entry(soup: BeautifulSoup, wrapper: str) -> str:
    entry = soup.select_one(f"#{wrapper} .pokedex-move-entry-new")
    if not entry:
        return ""
    value = entry.select_one(".pokedex-inline-text-offset, .pokedex-inline-text")
    return value.get_text(" ", strip=True) if value else ""


def ranked_entries(soup: BeautifulSoup, wrapper: str, limit: int = 10) -> tuple[list[str], dict[str, float]]:
    names: list[str] = []
    usage: dict[str, float] = {}
    for entry in soup.select(f"#{wrapper} .pokedex-move-entry-new")[:limit]:
        value = entry.select_one(".pokedex-inline-text-offset, .pokedex-inline-text")
        if not value:
            continue
        name = value.get_text(" ", strip=True)
        names.append(name)
        rate = entry.select_one(".pokedex-inline-right")
        if rate:
            match = re.search(r"[0-9]+(?:\.[0-9]+)?", rate.get_text(" ", strip=True))
            if match:
                usage[name] = float(match.group(0))
    return names, usage


def fetch_pikalytics(name: str) -> tuple[str, dict]:
    cache_file = META_CACHE / f"{to_id(name)}.html"
    try:
        if cache_file.exists():
            html = cache_file.read_text(encoding="utf-8")
        else:
            url = f"https://www.pikalytics.com/pokedex/{PIKALYTICS_FORMAT}/{quote(name)}?l=en"
            html = ""
            for attempt in range(3):
                try:
                    response = requests.get(url, headers=session.headers, timeout=60)
                    response.raise_for_status()
                    html = response.text
                    break
                except requests.RequestException:
                    if attempt == 2:
                        raise
                    time.sleep(2 + attempt * 2)
            META_CACHE.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(html, encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")
        moves, move_usage = ranked_entries(soup, "moves_wrapper")
        items, item_usage = ranked_entries(soup, "items_wrapper")
        natures, nature_usage = ranked_entries(soup, "dex_natures_wrapper")
        spread_entry = soup.select_one("#dex_spreads_wrapper .pokedex-move-entry-new")
        spread = []
        if spread_entry:
            spread = [
                int(re.sub(r"\D", "", value.get_text()) or 0)
                for value in spread_entry.select(".pokedex-inline-text")[:6]
            ]
        return name, {
            "moves": moves,
            "moveUsage": move_usage,
            "items": items,
            "itemUsage": item_usage,
            "ability": first_entry(soup, "abilities_wrapper"),
            "nature": first_entry(soup, "dex_natures_wrapper"),
            "natures": natures,
            "natureUsage": nature_usage,
            "points": spread if len(spread) == 6 else [],
        }
    except (requests.RequestException, OSError, ValueError):
        return name, {}


def fetch_ability_description(name: str, fallback: str) -> tuple[str, str]:
    cache_file = ABILITY_CACHE / f"{to_id(name)}.json"
    try:
        if cache_file.exists():
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
        else:
            response = requests.get(
                f"https://pokeapi.co/api/v2/ability/{to_slug(name)}",
                headers=session.headers,
                timeout=45,
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            payload = response.json()
            ABILITY_CACHE.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        entries = [
            value["flavor_text"] for value in payload.get("flavor_text_entries", [])
            if value.get("language", {}).get("name") == "zh-hans"
        ]
        description = re.sub(r"\s+", "", entries[-1]) if entries else fallback
        return name, description
    except (requests.RequestException, OSError, ValueError, KeyError):
        return name, fallback


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

    ability_descriptions: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(fetch_ability_description, ability["name"], ability.get("description", ""))
            for ability in basics["abilities"]
        ]
        for future in concurrent.futures.as_completed(futures):
            name, description = future.result()
            ability_descriptions[name] = description

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

    popular: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fetch_pikalytics, p["name"]) for p in pokemon_source]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            name, result = future.result()
            popular[name] = result
            if index % 40 == 0:
                print(f"popular sets {index}/{len(futures)}")

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
            "flags": move.get("flags") or [],
        }
        moves.append(item)
        moves_by_name[move["name"]] = item

    items = [
        {"id": to_id(item["name"]), "name": translated(zh_item, item["name"]), "en": item["name"]}
        for item in basics["items"]
    ]
    items_by_name = {item["en"]: item for item in items}
    abilities = [
        {
            "id": to_id(ability["name"]),
            "name": translated(zh_ability, ability["name"]),
            "en": ability["name"],
            "description": ability_descriptions.get(ability["name"], ability.get("description", "")),
        }
        for ability in basics["abilities"]
    ]
    ability_by_name = {ability["en"]: ability["name"] for ability in abilities}
    nature_zh = {
        "Hardy": "勤奋", "Lonely": "怕寂寞", "Brave": "勇敢", "Adamant": "固执", "Naughty": "顽皮",
        "Bold": "大胆", "Docile": "坦率", "Relaxed": "悠闲", "Impish": "淘气", "Lax": "乐天",
        "Timid": "胆小", "Hasty": "急躁", "Serious": "认真", "Jolly": "爽朗", "Naive": "天真",
        "Modest": "内敛", "Mild": "慢吞吞", "Quiet": "冷静", "Bashful": "害羞", "Rash": "马虎",
        "Calm": "温和", "Gentle": "温顺", "Sassy": "自大", "Careful": "慎重", "Quirky": "浮躁",
    }

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

        usage = popular.get(english, {})
        usage_base_name = {
            **form_bases,
            "Floette-Mega": "Floette-Eternal",
            "Sinistcha-Masterpiece": "Sinistcha",
            "Vivillon-Fancy": "Vivillon",
            "Vivillon-Pokeball": "Vivillon",
            "Gourgeist-Large": "Gourgeist",
            "Gourgeist-Small": "Gourgeist",
            "Gourgeist-Super": "Gourgeist",
            "Polteageist-Antique": "Polteageist",
            "Maushold-Four": "Maushold",
        }.get(english, re.sub(r"-Mega(?:-[XY])?$", "", english))
        base_usage = popular.get(usage_base_name, {}) if usage_base_name != english else {}
        usage = {
            key: usage.get(key) or base_usage.get(key) or ({ } if key.endswith("Usage") else ([] if key in ("moves", "items", "natures", "points") else ""))
            for key in ("moves", "items", "natures", "points", "moveUsage", "itemUsage", "natureUsage", "item", "ability", "nature")
        }
        popular_moves = [
            to_id(name) for name in usage.get("moves", [])
            if name in moves_by_name and to_id(name) in legal_moves
        ][:10]
        popular_items = [items_by_name[name]["name"] for name in usage.get("items", []) if name in items_by_name][:10]
        popular_item = popular_items[0] if popular_items else items_by_name.get(usage.get("item", ""), {}).get("name", "")
        popular_ability = ability_by_name.get(usage.get("ability", ""), "")
        popular_nature = nature_zh.get(usage.get("nature", ""), "")
        popular_points = usage.get("points", [])
        if len(popular_points) != 6 or max(popular_points, default=0) > 32 or sum(popular_points) > 66:
            popular_points = []

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
                "popularMoves": popular_moves,
                "moveUsage": {to_id(name): rate for name, rate in usage.get("moveUsage", {}).items() if name in moves_by_name},
                "popularItems": popular_items,
                "itemUsage": {items_by_name[name]["name"]: rate for name, rate in usage.get("itemUsage", {}).items() if name in items_by_name},
                "popularItem": popular_item,
                "popularAbility": popular_ability,
                "popularNature": popular_nature,
                "popularNatures": [nature_zh.get(name, name) for name in usage.get("natures", [])][:10],
                "natureUsage": {nature_zh.get(name, name): rate for name, rate in usage.get("natureUsage", {}).items()},
                "popularPoints": popular_points,
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
            "popularSets": sum(bool(value.get("moves")) for value in popular.values()),
        },
        "pokemon": pokemon,
        "moves": moves,
        "items": items,
        "abilities": abilities,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(database, ensure_ascii=False, separators=(",", ":"))
    OUTPUT.write_text(f"window.CHAMPIONS_DB={payload};\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    print(database["counts"])


if __name__ == "__main__":
    main()
