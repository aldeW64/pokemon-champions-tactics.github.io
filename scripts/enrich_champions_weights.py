"""Backfill Pokemon weights in an already-generated Champions database."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "champions-db.js"
SMOGON = "https://www.smogon.com/dex/champions/pokemon/"


def base_form(name: str) -> str:
    return re.sub(r"-Mega(?:-[XY])?$", "", name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Add Champions Pokemon weights to the static database.")
    parser.add_argument("--proxy", default="", help="Optional HTTP(S) proxy, e.g. http://127.0.0.1:7890")
    args = parser.parse_args()

    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    response = requests.get(SMOGON, proxies=proxies, timeout=60)
    response.raise_for_status()
    start = response.text.index("dexSettings = ") + len("dexSettings = ")
    end = response.text.index("</script>", start)
    basics = json.loads(response.text[start:end])["injectRpcs"][1][1]
    weights = {entry["name"]: entry.get("weight") for entry in basics["pokemon"] if entry.get("weight")}

    text = DATABASE.read_text(encoding="utf-8")
    prefix = "window.CHAMPIONS_DB="
    payload = json.loads(text[len(prefix):].rstrip(";\n"))
    missing: list[str] = []
    for pokemon in payload["pokemon"]:
        weight = weights.get(pokemon["en"]) or weights.get(base_form(pokemon["en"]))
        if weight:
            pokemon["weight"] = weight
        else:
            missing.append(pokemon["en"])

    DATABASE.write_text(prefix + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(f"Updated {len(payload['pokemon']) - len(missing)} weights; unavailable: {len(missing)}")
    if missing:
        print(", ".join(missing))


if __name__ == "__main__":
    main()
