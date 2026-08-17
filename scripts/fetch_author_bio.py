#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_author_bio.py
===================

Enriquece el índice de autores con datos de Wikidata: fechas de nacimiento y
defunción, una breve descripción y el enlace directo a la Wikipedia en español.

Estrategia conservadora (evita fechas erróneas en un índice bibliográfico):
  - Solo se acepta una coincidencia si la entidad es un ser humano (P31 = Q5),
    tiene enlace a la Wikipedia (es o en) y el nombre normalizado coincide de
    forma exacta con la etiqueta, un alias o el título del artículo.
  - Si hay más de una entidad candidata que cumple lo anterior, se descarta
    (nombre ambiguo): es preferible no mostrar fecha a mostrar una incorrecta.

Fuente: Wikidata API (wbsearchentities + wbgetentities). Respuestas cacheadas en
cache/ para reproducibilidad y para no repetir peticiones.

Uso:
  python3 scripts/fetch_author_bio.py            # consulta (con caché) y escribe data/authors_bio.json
  python3 scripts/fetch_author_bio.py --offline  # solo usa la caché; no accede a la red
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache" / "wikidata"
DATA_DIR = ROOT / "data"

API = "https://www.wikidata.org/w/api.php"
USER_AGENT = (
    "RevistaOccidenteIndex/1.0 (indice bibliografico no comercial; "
    "contacto via GitHub issues)"
)
REQUEST_DELAY_S = 1.0
REQUEST_TIMEOUT_S = 60
MAX_RETRIES = 6
SEARCH_LIMIT = 12

MONTHS_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

_last_request = [0.0]


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = value.replace(".", " ").replace("-", " ")
    return re.sub(r"\s+", " ", value).strip()


def _cache_path(key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", key).strip("_")
    return CACHE_DIR / (safe[:150] + ".json")


def _get(params: dict, cache_key: str, offline: bool) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = _cache_path(cache_key)
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if offline:
        raise RuntimeError(f"[offline] no está en caché: {cache_key}")

    delay = REQUEST_DELAY_S - (time.time() - _last_request[0])
    if delay > 0:
        time.sleep(delay)

    query = {**params, "format": "json", "maxlag": "5"}
    url = f"{API}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "es"})

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("error", {}).get("code") == "maxlag":
                raise urllib.error.HTTPError(url, 503, "maxlag", None, None)
            _last_request[0] = time.time()
            cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code in (429, 503):
                retry_after = error.headers.get("Retry-After") if error.headers else None
                wait = int(retry_after) if retry_after and retry_after.isdigit() else min(60, 5 * attempt)
                print(f"  [{error.code} {attempt}/{MAX_RETRIES}] {cache_key}: espera {wait}s", file=sys.stderr)
                time.sleep(wait)
            else:
                raise
        except Exception as error:  # noqa: BLE001
            last_error = error
            print(f"  [reintento {attempt}/{MAX_RETRIES}] {cache_key}: {error}", file=sys.stderr)
            time.sleep(REQUEST_DELAY_S * attempt * 2)
    raise RuntimeError(f"No se pudo consultar {cache_key}: {last_error}")


def author_names(dataset: dict) -> list[str]:
    """Nombres de autor tal como se muestran en el índice (artículos y «Notas»)."""
    seen: dict[str, None] = {}
    for issue in dataset["issues"]:
        for contribution in issue["contributions"]:
            for author in contribution["authors"]:
                seen.setdefault(author["name"], None)
        for note_author in issue.get("note_authors") or []:
            seen.setdefault(note_author["name"], None)
    return list(seen)


def search_candidates(name: str, offline: bool) -> list[str]:
    data = _get(
        {
            "action": "wbsearchentities",
            "search": name,
            "language": "es",
            "uselang": "es",
            "type": "item",
            "limit": str(SEARCH_LIMIT),
        },
        f"search_{name}",
        offline,
    )
    return [hit["id"] for hit in data.get("search", [])]


def load_entities(qids: list[str], offline: bool) -> dict:
    if not qids:
        return {}
    data = _get(
        {
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "labels|aliases|descriptions|claims|sitelinks/urls",
            "languages": "es|en",
            "sitefilter": "eswiki|enwiki",
        },
        "entities_" + "_".join(qids),
        offline,
    )
    return data.get("entities", {})


def _time_claim(claims: dict, prop: str) -> dict | None:
    statements = claims.get(prop) or []
    for statement in statements:
        snak = statement.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        value = snak["datavalue"]["value"]
        iso = value.get("time", "")  # e.g. +1892-02-24T00:00:00Z
        precision = value.get("precision", 11)
        match = re.match(r"[+-](\d+)-(\d{2})-(\d{2})", iso)
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups())
        return {"year": year, "month": month, "day": day, "precision": precision}
    return None


def _is_human(claims: dict) -> bool:
    for statement in claims.get("P31") or []:
        snak = statement.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        if snak["datavalue"]["value"].get("id") == "Q5":
            return True
    return False


def _names_for(entity: dict) -> set[str]:
    names: set[str] = set()
    for lang in ("es", "en"):
        label = entity.get("labels", {}).get(lang, {}).get("value")
        if label:
            names.add(_normalise(label))
        for alias in entity.get("aliases", {}).get(lang, []):
            names.add(_normalise(alias.get("value", "")))
    for site in ("eswiki", "enwiki"):
        title = entity.get("sitelinks", {}).get(site, {}).get("title")
        if title:
            names.add(_normalise(re.sub(r"\s*\(.*\)$", "", title)))
    names.discard("")
    return names


def match_author(name: str, entities: dict) -> dict | None:
    target = _normalise(name)
    matches: list[dict] = []

    for qid, entity in entities.items():
        claims = entity.get("claims", {})
        if not _is_human(claims):
            continue
        sitelinks = entity.get("sitelinks", {})
        es_url = sitelinks.get("eswiki", {}).get("url")
        en_url = sitelinks.get("enwiki", {}).get("url")
        if not es_url and not en_url:
            continue
        if target not in _names_for(entity):
            continue

        birth = _time_claim(claims, "P569")
        death = _time_claim(claims, "P570")
        if not birth and not death:
            continue

        matches.append(
            {
                "qid": qid,
                "description": entity.get("descriptions", {}).get("es", {}).get("value")
                or entity.get("descriptions", {}).get("en", {}).get("value"),
                "wikipedia_es_url": es_url,
                "wikipedia_en_url": en_url,
                "birth": birth,
                "death": death,
            }
        )

    unique_qids = {match["qid"] for match in matches}
    if len(unique_qids) != 1:
        return None  # sin coincidencia o nombre ambiguo: no se arriesga una fecha errónea
    return matches[0]


def _fmt_date(value: dict | None) -> str | None:
    if not value:
        return None
    year, month, day, precision = value["year"], value["month"], value["day"], value["precision"]
    if precision >= 11 and 1 <= day <= 31 and 1 <= month <= 12:
        return f"{day} de {MONTHS_ES[month]} de {year}"
    if precision == 10 and 1 <= month <= 12:
        return f"{MONTHS_ES[month]} de {year}"
    return str(year)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="usa solo la caché; no accede a la red")
    args = parser.parse_args()

    dataset = json.loads((DATA_DIR / "indice.json").read_text(encoding="utf-8"))
    names = author_names(dataset)
    print(f"Autores a consultar: {len(names)}")

    result: dict[str, dict] = {}
    matched = 0

    def write_output(final: bool) -> None:
        payload = {
            "meta": {
                "source": "Wikidata (P569 nacimiento, P570 defunción)",
                "authors_total": len(names),
                "authors_matched": len(result),
                "complete": final,
                "note": (
                    "Solo se incluyen autores con coincidencia única de nombre humano con "
                    "enlace a Wikipedia. Los nombres ambiguos o sin correspondencia se omiten."
                ),
            },
            "authors": dict(sorted(result.items())),
        }
        (DATA_DIR / "authors_bio.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    for index, name in enumerate(names, 1):
        try:
            candidates = search_candidates(name, args.offline)
            entities = load_entities(candidates, args.offline)
            match = match_author(name, entities)
        except RuntimeError as error:
            print(f"  [omitido] {name}: {error}")
            continue

        if match:
            matched += 1
            birth_label = _fmt_date(match["birth"])
            death_label = _fmt_date(match["death"])
            lifespan = None
            if birth_label or death_label:
                lifespan = f"{birth_label or '?'} – {death_label or '?'}"
            result[name] = {
                "qid": match["qid"],
                "description": match["description"],
                "wikipedia_url": match["wikipedia_es_url"] or match["wikipedia_en_url"],
                "birth_year": match["birth"]["year"] if match["birth"] else None,
                "death_year": match["death"]["year"] if match["death"] else None,
                "birth_label": birth_label,
                "death_label": death_label,
                "lifespan": lifespan,
            }
        if index % 20 == 0:
            write_output(final=False)
            print(f"    {index}/{len(names)} · coincidencias: {matched}")

    write_output(final=True)
    print(f"\nGenerado: data/authors_bio.json · {matched}/{len(names)} autores con datos")


if __name__ == "__main__":
    main()
