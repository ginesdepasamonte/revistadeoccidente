#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_index.py
==============

Construye el índice bibliográfico de la primera época de *Revista de Occidente*
(1923–1936, números 1–157).

Fuentes:
  - Fundación Ortega-Marañón (FOM): enumeración de ejemplares y enlace al facsímil
    oficial (visor). Fuente autoritativa para número, mes/año y enlace oficial.
  - Dialnet (código de revista 1203, ISSN 0034-8635): metadatos bibliográficos
    (título, autor, páginas, tipo de documento) obtenidos de las páginas de año.

Genera:
  - data/indice.json   (datos estructurados, reproducibilidad)
  - indice.md          (índice legible en español)
  - README.md          (introducción del proyecto)

No descarga los PDF. Solo enlaza al visor oficial:
  https://ortegaygasset.edu/visor-pdfro/?pdf={PDFID}

Uso:
  python3 scripts/build_index.py            # extrae (con caché), construye JSON y Markdown
  python3 scripts/build_index.py --stats    # solo extrae y muestra estadísticas de validación
  python3 scripts/build_index.py --offline  # falla si algo no está en caché (no red)
"""

from __future__ import annotations

import argparse
import html
import http.cookiejar
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"
DATA_DIR = ROOT / "data"
COVERS_DIR = ROOT / "covers"          # miniaturas de portada (versionadas en el repo)

USER_AGENT = (
    "RevistaOccidenteIndex/1.0 (indice bibliografico no comercial; "
    "contacto via GitHub issues)"
)
REQUEST_DELAY_S = 3.0          # cortesía con los servidores
COVER_DELAY_S = 1.0            # descarga de portadas (imágenes estáticas de FOM)
REQUEST_TIMEOUT_S = 60
MAX_RETRIES = 5
THROTTLE_COOLDOWN_S = 90       # espera larga si el servidor responde 503 (anti-bot)

FOM_ARCHIVE_PAGES = [
    "https://ortegaygasset.edu/publicaciones/revista-de-occidente/archivo-ro/numero/",
    "https://ortegaygasset.edu/publicaciones/revista-de-occidente/archivo-ro/numero-2/",
    "https://ortegaygasset.edu/publicaciones/revista-de-occidente/archivo-ro/numero-3/",
    "https://ortegaygasset.edu/publicaciones/revista-de-occidente/archivo-ro/numero-4/",
    "https://ortegaygasset.edu/publicaciones/revista-de-occidente/archivo-ro/numero-5/",
    "https://ortegaygasset.edu/publicaciones/revista-de-occidente/archivo-ro/numero-6/",
]
FOM_VIEWER = "https://ortegaygasset.edu/visor-pdfro/?pdf={pdfid}"

DIALNET_JOURNAL_CODE = "1203"
DIALNET_ISSN = "0034-8635"
DIALNET_YEARS = list(range(1923, 1937))  # 1923..1936
DIALNET_YEAR_URL = "https://dialnet.unirioja.es/revista/1203/A/{year}"

EXPECTED_ISSUE_COUNT = 157

MONTHS = {
    "ENERO": ("enero", 1), "FEBRERO": ("febrero", 2), "MARZO": ("marzo", 3),
    "ABRIL": ("abril", 4), "MAYO": ("mayo", 5), "JUNIO": ("junio", 6),
    "JULIO": ("julio", 7), "AGOSTO": ("agosto", 8), "SEPTIEMBRE": ("septiembre", 9),
    "OCTUBRE": ("octubre", 10), "NOVIEMBRE": ("noviembre", 11), "DICIEMBRE": ("diciembre", 12),
}

# --------------------------------------------------------------------------- #
# Cliente HTTP con caché
# --------------------------------------------------------------------------- #

_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookie_jar))
_opener.addheaders = [("User-Agent", USER_AGENT), ("Accept-Language", "es")]
_last_request_time = [0.0]


def _cache_path(url: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", url).strip("_")
    return CACHE_DIR / (safe[:150] + ".html")


def fetch(url: str, offline: bool = False) -> str:
    """Descarga una URL usando caché en disco. Respeta un retardo entre peticiones."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cp = _cache_path(url)
    if cp.exists():
        return cp.read_text(encoding="utf-8", errors="replace")
    if offline:
        raise RuntimeError(f"[offline] no está en caché: {url}")

    delay = REQUEST_DELAY_S - (time.time() - _last_request_time[0])
    if delay > 0:
        time.sleep(delay)

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with _opener.open(url, timeout=REQUEST_TIMEOUT_S) as resp:
                data = resp.read()
            text = data.decode("utf-8", errors="replace")
            _last_request_time[0] = time.time()
            cp.write_text(text, encoding="utf-8")
            print(f"  [descargado] {url}")
            return text
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 503:  # reto anti-bot por ritmo: enfriar y reintentar (sin eludirlo)
                wait = THROTTLE_COOLDOWN_S * attempt
                print(f"  [503 ritmo {attempt}/{MAX_RETRIES}] {url}: enfriando {wait}s",
                      file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"  [reintento {attempt}/{MAX_RETRIES}] {url} -> {e}", file=sys.stderr)
                time.sleep(REQUEST_DELAY_S * attempt * 2)
        except Exception as e:  # noqa: BLE001
            last_err = e
            print(f"  [reintento {attempt}/{MAX_RETRIES}] {url} -> {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY_S * attempt * 2)
    raise RuntimeError(f"No se pudo descargar {url}: {last_err}")


def fetch_binary(url: str, dest: Path, offline: bool = False) -> bool:
    """Descarga un binario (imagen) a `dest`. La propia carpeta actúa de caché:
    si el fichero ya existe, no se vuelve a pedir. Devuelve True si el fichero existe."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return True
    if offline:
        return False
    delay = COVER_DELAY_S - (time.time() - _last_request_time[0])
    if delay > 0:
        time.sleep(delay)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with _opener.open(url, timeout=REQUEST_TIMEOUT_S) as resp:
                data = resp.read()
            _last_request_time[0] = time.time()
            dest.write_bytes(data)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"  [reintento img {attempt}/{MAX_RETRIES}] {url} -> {e}", file=sys.stderr)
            time.sleep(COVER_DELAY_S * attempt * 2)
    return False


# --------------------------------------------------------------------------- #
# Utilidades de texto
# --------------------------------------------------------------------------- #

def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def clean(s: str) -> str:
    s = html.unescape(strip_tags(s))
    return re.sub(r"\s+", " ", s).strip()


def slugify_ascii(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def sort_key(s: str) -> str:
    """Clave de ordenación insensible a acentos y mayúsculas (no altera el texto mostrado)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


# --------------------------------------------------------------------------- #
# Paso 1 — FOM: enumerar ejemplares
# --------------------------------------------------------------------------- #

_BUTTON_RE = re.compile(
    r'href="[^"]*visor-pdfro/\?pdf=(\d+)"[^>]*>\s*Revista de Occidente\s*-\s*N[ºo]\s*0*(\d+)\s*</a>'
)
_COVER_RE = re.compile(r"RDO_N0*(\d+)_([A-ZÑ_]+?)_(\d{4})\.jpg")
# URL completa de la portada; la parte numérica procede del propio nombre de fichero,
# de modo que la imagen siempre corresponde a su número (evita la errata del Nº 145).
_COVER_SRC_RE = re.compile(
    r'src="([^"]*/RDO_N0*(\d+)_[A-ZÑ_]+?_\d{4}\.(?:jpe?g|png))"'
)

# La primera época es estrictamente mensual: de julio de 1923 a julio de 1936 hay
# exactamente 157 meses = 157 números. El número de ejemplar determina el mes.
_BASE_ABS = 1923 * 12 + (7 - 1)  # julio de 1923 (índice absoluto de mes)


def expected_date(issue_number: int) -> tuple[str, int, int]:
    """(month_name, month_num, year) esperados para un número, por la cadencia mensual."""
    absidx = _BASE_ABS + (issue_number - 1)
    year = absidx // 12
    month_num = absidx % 12 + 1
    name = {v[1]: v[0] for v in MONTHS.values()}[month_num]
    return name, month_num, year


def parse_fom_page(text: str) -> dict[int, dict]:
    """Empareja cada botón (número + PDFID) con la portada de su misma tarjeta.

    El botón es la fuente autoritativa del número y del PDFID. La portada aporta
    mes/año, pero puede contener erratas (imagen equivocada); por eso se registra
    el número que declara la portada para poder detectar discrepancias.
    """
    buttons = [(m.start(), int(m.group(2)), m.group(1)) for m in _BUTTON_RE.finditer(text)]
    covers = sorted(
        (m.start(), int(m.group(1)), m.group(2), int(m.group(3)))
        for m in _COVER_RE.finditer(text)
    )
    issues: dict[int, dict] = {}
    for pos, num, pdfid in buttons:
        preceding = [c for c in covers if c[0] < pos]
        cov = preceding[-1] if preceding else None
        issues.setdefault(num, {
            "pdfid": pdfid,
            "cover_number": cov[1] if cov else None,
            "cover_month_raw": cov[2] if cov else None,
            "cover_year": cov[3] if cov else None,
        })
    return issues


def collect_fom(offline: bool = False) -> tuple[dict[int, dict], list[str]]:
    print("Paso 1 — FOM: enumerando los 157 ejemplares…")
    raw: dict[int, dict] = {}
    cover_urls: dict[int, str] = {}   # clave = número del nombre de fichero
    for url in FOM_ARCHIVE_PAGES:
        text = fetch(url, offline=offline)
        page_issues = parse_fom_page(text)
        for num, data in page_issues.items():
            raw.setdefault(num, data)
        for m in _COVER_SRC_RE.finditer(text):
            cover_urls.setdefault(int(m.group(2)), m.group(1))
        print(f"  {url.rsplit('/', 2)[-2]}: {len(page_issues)} ejemplares")

    notes: list[str] = []
    # Detectar portadas reutilizadas (misma imagen para dos tarjetas)
    cover_usage: dict[tuple, list[int]] = {}
    for num, d in raw.items():
        if d["cover_number"] is not None:
            key = (d["cover_number"], d["cover_month_raw"], d["cover_year"])
            cover_usage.setdefault(key, []).append(num)
    for (cn, cm, cy), nums in cover_usage.items():
        if len(nums) > 1:
            notes.append(
                f"FOM: la portada «RDO_N{cn:03d}_{cm}_{cy}.jpg» se reutiliza en los "
                f"números {sorted(nums)} (errata de imagen en el archivo)."
            )

    all_issues: dict[int, dict] = {}
    for num in sorted(raw):
        d = raw[num]
        exp_name, exp_num, exp_year = expected_date(num)
        cover_ok = (
            d["cover_number"] == num
            and d["cover_month_raw"] in MONTHS
            and d["cover_year"] == exp_year
            and MONTHS[d["cover_month_raw"]][1] == exp_num
        )
        if cover_ok:
            month_name, month_num = MONTHS[d["cover_month_raw"]]
            year = d["cover_year"]
        else:
            # La portada de esta tarjeta no corresponde al número: usar la fecha que
            # impone la cadencia mensual (evidencia interna de la propia FOM) y anotarlo.
            month_name, month_num, year = exp_name, exp_num, exp_year
            shown = (
                f"portada del Nº {d['cover_number']} "
                f"({d['cover_month_raw']} {d['cover_year']})"
                if d["cover_number"] is not None else "sin portada propia"
            )
            notes.append(
                f"FOM: la tarjeta del Nº {num} muestra {shown}; mes/año "
                f"({month_name} de {year}) derivado de la secuencia mensual y "
                f"contrastado con el año de Dialnet."
            )
        all_issues[num] = {
            "pdfid": d["pdfid"],
            "month_name": month_name,
            "month_num": month_num,
            "year": year,
            "cover_url": cover_urls.get(num),
            "cover_local": None,
        }
    return all_issues, notes


def download_covers(fom: dict[int, dict], offline: bool = False) -> list[str]:
    """Descarga las miniaturas de portada a covers/N{NNN}.jpg (no descarga los PDF)."""
    print("Paso 1b — FOM: descargando miniaturas de portada…")
    notes: list[str] = []
    got = 0
    missing_cover = []
    for num in sorted(fom):
        url = fom[num].get("cover_url")
        if not url:
            missing_cover.append(num)
            continue
        ext = url.rsplit(".", 1)[-1].lower()
        rel = f"covers/N{num:03d}.{ext}"
        dest = ROOT / rel
        if fetch_binary(url, dest, offline=offline):
            fom[num]["cover_local"] = rel
            got += 1
    if missing_cover:
        notes.append(
            "FOM: sin miniatura de portada para el/los número(s) "
            f"{missing_cover} (la página de archivo no publica su imagen; "
            "véase la errata del Nº 145)."
        )
    print(f"  portadas disponibles: {got}/{len(fom)}")
    return notes


# --------------------------------------------------------------------------- #
# Paso 2 — Dialnet: extraer contribuciones (páginas de año, con paginación)
# --------------------------------------------------------------------------- #

def parse_dialnet_year(text: str, year: int) -> list[dict]:
    """Extrae [{issue_number, ejemplar_id, contributions:[...]}] de una página de año."""
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)

    header_re = re.compile(
        r"<h3>\s*A[nñ]o\s+%d,\s*N[uú]mero\s*<a href=\"/ejemplar/(\d+)\">\s*(\d+)\s*</a>\s*</h3>"
        % year,
        re.S,
    )
    headers = list(header_re.finditer(text))
    footer = text.find('id="pieDeListadoDeArticulosDeRevistas"')
    if footer == -1:
        footer = len(text)

    results = []
    for i, hm in enumerate(headers):
        ejemplar_id = hm.group(1)
        issue_number = int(hm.group(2))
        start = hm.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else footer
        block = text[start:end]

        contributions = []
        for li in re.finditer(r'<li id="articulo(\d+)"[^>]*>(.*?)</li>', block, re.S):
            articulo_id = li.group(1)
            body = li.group(2)

            tm = re.search(r'/servlet/articulo\?codigo=\d+">(.*?)</a>', body, re.S)
            title = clean(tm.group(1)) if tm else ""

            authors = []
            for am in re.finditer(r'/servlet/autor\?codigo=(\d+)">(.*?)</a>', body, re.S):
                authors.append({"autor_id": am.group(1), "name": clean(am.group(2))})

            pm = re.search(r"p[aá]gs?\.\s*</abbr>\s*(\d+)(?:\s*-\s*(\d+))?", body)
            page_start = int(pm.group(1)) if pm else None
            page_end = int(pm.group(2)) if (pm and pm.group(2)) else page_start

            dm = re.search(r'iconoTipoDocumento"><img alt="([^"]+)"', body)
            doc_type = dm.group(1) if dm else ""

            contributions.append({
                "articulo_id": articulo_id,
                "title": title,
                "authors": authors,
                "page_start": page_start,
                "page_end": page_end,
                "doc_type": doc_type,
            })

        results.append({
            "issue_number": issue_number,
            "ejemplar_id": ejemplar_id,
            "contributions": contributions,
        })
    return results


def total_pages(text: str) -> int:
    m = re.search(r'numeroTotalDePaginas">(\d+)<', text)
    return int(m.group(1)) if m else 1


def collect_dialnet(offline: bool = False) -> dict[int, dict]:
    """Devuelve {issue_number: {ejemplar_id, year, contributions:[...]}} para 1923–1936."""
    print("Paso 2 — Dialnet: extrayendo contribuciones por año (con paginación)…")
    issues: dict[int, dict] = {}
    for year in DIALNET_YEARS:
        base = DIALNET_YEAR_URL.format(year=year)
        first = fetch(base, offline=offline)
        n_pages = total_pages(first)
        pages_text = [first]
        for p in range(2, n_pages + 1):
            inicio = (p - 1) * 30 + 1
            pages_text.append(fetch(f"{base}?inicio={inicio}", offline=offline))

        year_issue_count = 0
        for text in pages_text:
            for issue in parse_dialnet_year(text, year):
                num = issue["issue_number"]
                if num not in issues:
                    issues[num] = {
                        "ejemplar_id": issue["ejemplar_id"],
                        "year": year,
                        "_seen_articulo": set(),
                        "contributions": [],
                    }
                    year_issue_count += 1
                bucket = issues[num]
                for c in issue["contributions"]:
                    if c["articulo_id"] in bucket["_seen_articulo"]:
                        continue  # dedup: un ejemplar puede repetirse entre páginas
                    bucket["_seen_articulo"].add(c["articulo_id"])
                    bucket["contributions"].append(c)
        print(f"  {year}: {n_pages} página(s), {year_issue_count} ejemplares")

    for v in issues.values():
        v.pop("_seen_articulo", None)
    return issues


# --------------------------------------------------------------------------- #
# Paso 4 — Dialnet: forma normalizada del nombre de autor (DC.creator)
# --------------------------------------------------------------------------- #

_ART_URL = "https://dialnet.unirioja.es/servlet/articulo?codigo={aid}"


def extract_dc_creator(text: str) -> str | None:
    for m in re.finditer(r"<meta\b[^>]*>", text):
        tag = m.group(0)
        n = re.search(r'name="([^"]*)"', tag)
        c = re.search(r'content="([^"]*)"', tag)
        if n and c and n.group(1).lower() == "dc.creator":
            return html.unescape(c.group(1)).strip()
    return None


def collect_author_names(dataset: dict, offline: bool = False) -> tuple[dict, list[str]]:
    """Obtiene la forma catalogada del nombre («Apellidos, Nombre») desde el campo
    DC.creator de Dialnet, una sola petición por autor único. No adivina: si no puede
    obtenerla, conserva la forma tal como aparece en las páginas de año.
    """
    # autor_id -> (recorded_name, un articulo_id de muestra)  [todas las piezas son de un solo autor]
    sample: dict[str, tuple[str, str]] = {}
    for iss in dataset["issues"]:
        for c in iss["contributions"]:
            for a in c["authors"]:
                if a["autor_id"] and a["autor_id"] not in sample:
                    sample[a["autor_id"]] = (a["name"], c["articulo_id"])

    print(f"Paso 4 — Dialnet: nombre normalizado (DC.creator) de {len(sample)} autores…")
    authors: dict[str, dict] = {}
    notes: list[str] = []
    fallbacks = 0
    for i, aid in enumerate(sorted(sample, key=int), 1):
        recorded, art_id = sample[aid]
        canonical = None
        try:
            text = fetch(_ART_URL.format(aid=art_id), offline=offline)
            canonical = extract_dc_creator(text)
        except Exception as e:  # noqa: BLE001
            notes.append(f"Dialnet: sin DC.creator para el autor {aid} ({recorded}): {e}")
        if not canonical:
            canonical = recorded
            fallbacks += 1
        authors[aid] = {
            "autor_id": aid,
            "name_recorded": recorded,
            "name_index": canonical,
            "dialnet_author_url": f"https://dialnet.unirioja.es/servlet/autor?codigo={aid}",
        }
        if i % 40 == 0:
            print(f"    {i}/{len(sample)}…")
    if fallbacks:
        notes.append(
            f"Índice de autores: {fallbacks} autor(es) sin forma normalizada de Dialnet; "
            "se conserva el nombre tal como aparece en las páginas de año."
        )
    return authors, notes


# --------------------------------------------------------------------------- #
# Paso 3 — Cruce y validación
# --------------------------------------------------------------------------- #

def build_dataset(fom: dict[int, dict], dialnet: dict[int, dict],
                  fom_notes: list[str] | None = None) -> dict:
    print("Paso 3 — Cruzando FOM y Dialnet (número + año) y validando…")
    problems: list[str] = list(fom_notes or [])

    fom_nums = sorted(fom)
    if fom_nums != list(range(1, EXPECTED_ISSUE_COUNT + 1)):
        missing = sorted(set(range(1, EXPECTED_ISSUE_COUNT + 1)) - set(fom_nums))
        extra = sorted(set(fom_nums) - set(range(1, EXPECTED_ISSUE_COUNT + 1)))
        raise SystemExit(
            f"Secuencia FOM incompleta. Faltan: {missing}. Sobran: {extra}. Se detiene."
        )

    issues = []
    for num in fom_nums:
        f = fom[num]
        d = dialnet.get(num)
        year = f["year"]
        if d and d["year"] != year:
            problems.append(
                f"Nº {num}: año FOM={year} difiere de año Dialnet={d['year']}"
            )
        contributions = d["contributions"] if d else []
        if not d:
            problems.append(f"Nº {num} ({year}): sin ejemplar/contribuciones en Dialnet")
        elif not contributions:
            problems.append(f"Nº {num} ({year}): ejemplar Dialnet sin contribuciones")

        issues.append({
            "issue_number": num,
            "year": year,
            "month_name": f["month_name"],
            "month_num": f["month_num"],
            "fom_pdfid": f["pdfid"],
            "fom_viewer_url": FOM_VIEWER.format(pdfid=f["pdfid"]),
            "cover_source_url": f.get("cover_url"),
            "cover_local": f.get("cover_local"),
            "dialnet_ejemplar_id": d["ejemplar_id"] if d else None,
            "dialnet_year_url": DIALNET_YEAR_URL.format(year=year),
            "contributions": [
                {
                    "articulo_id": c["articulo_id"],
                    "title": c["title"],
                    "authors": c["authors"],
                    "page_start": c["page_start"],
                    "page_end": c["page_end"],
                    "doc_type": c["doc_type"],
                    "dialnet_article_url":
                        f"https://dialnet.unirioja.es/servlet/articulo?codigo={c['articulo_id']}",
                }
                for c in contributions
            ],
        })

    # Detección de huecos de paginación (posible contenido no indexado por Dialnet)
    for i, iss in enumerate(issues):
        pages = [c["page_start"] for c in iss["contributions"] if c["page_start"]]
        ends = [c["page_end"] for c in iss["contributions"] if c["page_end"]]
        if len(iss["contributions"]) >= 2 and ends and pages:
            last_end = max(ends)
            # comparar con el primer folio del siguiente ejemplar del mismo tomo
            if i + 1 < len(issues):
                nxt = issues[i + 1]
                nxt_starts = [c["page_start"] for c in nxt["contributions"] if c["page_start"]]
                if nxt_starts and min(nxt_starts) > 1:  # mismo tomo (paginación continua)
                    gap = min(nxt_starts) - last_end - 1
                    if gap >= 5:
                        problems.append(
                            f"Nº {iss['issue_number']}: posible contenido no indexado "
                            f"(termina en pág. {last_end}; el Nº {nxt['issue_number']} "
                            f"empieza en pág. {min(nxt_starts)})"
                        )

    # Duplicados de contribución (no debería haber)
    seen = set()
    for iss in issues:
        for c in iss["contributions"]:
            key = c["articulo_id"]
            if key in seen:
                problems.append(f"Contribución duplicada: articulo {key}")
            seen.add(key)

    total_contribs = sum(len(i["contributions"]) for i in issues)
    print(f"  Ejemplares: {len(issues)} · contribuciones: {total_contribs}")
    print(f"  Incidencias registradas: {len(problems)}")

    return {
        "meta": {
            "titulo": "Revista de Occidente — Primera época (1923–1936)",
            "issues_total": len(issues),
            "contribuciones_total": total_contribs,
            "dialnet_journal_code": DIALNET_JOURNAL_CODE,
            "issn": DIALNET_ISSN,
            "fom_viewer_pattern": FOM_VIEWER,
            "fuentes": {
                "fom": "Fundación Ortega-Marañón (enumeración de ejemplares y facsímil oficial)",
                "dialnet": "Dialnet (metadatos bibliográficos)",
            },
            "validacion": problems,
        },
        "issues": issues,
    }


# --------------------------------------------------------------------------- #
# Formato legible
# --------------------------------------------------------------------------- #

def fmt_pages(c: dict) -> str:
    ps, pe = c["page_start"], c["page_end"]
    if ps is None:
        return ""
    if pe is None or pe == ps:
        return f"p. {ps}"
    return f"pp. {ps}\u2013{pe}"


def authors_display(c: dict) -> str:
    return ", ".join(a["name"] for a in c["authors"])


def cap_month(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name


# --------------------------------------------------------------------------- #
# Pasos 5–8 y 13 — indice.md
# --------------------------------------------------------------------------- #

def generate_indice_md(dataset: dict) -> str:
    issues = dataset["issues"]
    years = sorted({i["year"] for i in issues})
    L: list[str] = []

    L.append("# Revista de Occidente — Primera época (1923–1936)")
    L.append("")
    L.append(
        "Índice bibliográfico de la **primera época** de *Revista de Occidente*, "
        "la revista fundada y dirigida por José Ortega y Gasset. Abarca los "
        f"**{len(issues)} números** publicados entre **julio de 1923** y "
        "**julio de 1936**."
    )
    L.append("")
    L.append(
        "- Los **ejemplares originales digitalizados** están alojados por la "
        "**Fundación Ortega-Marañón (FOM)**. Cada número enlaza a su facsímil en el "
        "visor oficial. Este proyecto **no aloja ni redistribuye** los PDF."
    )
    L.append(
        "- Los **metadatos bibliográficos** (autores, títulos, páginas) proceden "
        "principalmente de **Dialnet** (ISSN 0034-8635)."
    )
    L.append(
        "- **Limitación importante:** Dialnet no siempre indexa la totalidad del "
        "contenido de cada número (puede omitir notas, reseñas, textos preliminares o "
        "secciones menores). Por tanto, este índice es una **herramienta de consulta "
        "bibliográfica (finding aid)**, no una transcripción exhaustiva página a página. "
        "Véase la [nota de metodología y cobertura](#metodologia-y-cobertura)."
    )
    L.append("")
    L.append("---")
    L.append("")

    # Años (navegación)
    L.append('<a id="anios"></a>')
    L.append("## Años")
    L.append("")
    L.append(" · ".join(f"[{y}](#anio-{y})" for y in years))
    L.append("")
    L.append("Otros índices: [Índice de autores](#indice-de-autores) · "
             "[Índice de títulos](#indice-de-titulos)")
    L.append("")
    L.append("---")
    L.append("")

    # Cuerpo por años
    by_year: dict[int, list[dict]] = {}
    for iss in issues:
        by_year.setdefault(iss["year"], []).append(iss)

    for year in years:
        L.append(f'<a id="anio-{year}"></a>')
        L.append(f"## {year}")
        L.append("")
        for iss in sorted(by_year[year], key=lambda x: x["issue_number"]):
            num = iss["issue_number"]
            L.append(f'<a id="ejemplar-{num}"></a>')
            L.append(f"### Nº {num} — {cap_month(iss['month_name'])} de {year}")
            L.append("")
            if iss.get("cover_local"):
                alt = f"Portada del Nº {num} — {cap_month(iss['month_name'])} de {year}"
                L.append(
                    f'[<img src="{iss["cover_local"]}" alt="{alt}" width="150">]'
                    f'({iss["fom_viewer_url"]})'
                )
                L.append("")
            L.append(
                f"[📖 Leer el ejemplar digitalizado en la Fundación Ortega-Marañón]"
                f"({iss['fom_viewer_url']})"
            )
            L.append("")
            if not iss["contributions"]:
                L.append(
                    "> *Sin contribuciones indexadas en Dialnet para este número. "
                    "El facsímil completo está disponible en el enlace anterior.*"
                )
                L.append("")
                continue
            for c in iss["contributions"]:
                au = authors_display(c)
                pages = fmt_pages(c)
                tipo = "" if c["doc_type"] in ("", "Artículo") else f" · {c['doc_type']}"
                bits = []
                if au:
                    bits.append(f"**{au}** — ")
                bits.append(f"*{c['title']}*")
                if pages:
                    bits.append(f", {pages}")
                bits.append(tipo)
                L.append("* " + "".join(bits))
            L.append("")
        L.append("[↑ Años](#anios)")
        L.append("")
        L.append("---")
        L.append("")

    # Índice de autores
    L.append('<a id="indice-de-autores"></a>')
    L.append("# Índice de autores")
    L.append("")
    L.append(
        "Ordenado alfabéticamente por el **nombre tal como lo registra Dialnet** en las "
        "páginas de año (forma «Nombre Apellidos»). Para no introducir errores, los nombres "
        "**no se han invertido ni corregido por conjetura**; puedes usar la búsqueda del "
        "navegador (Ctrl/Cmd-F) para localizar a un autor por cualquier parte del nombre. "
        "Las 180 contribuciones sin autor identificado en Dialnet no aparecen en este "
        "índice, pero sí en la lista por años."
    )
    L.append("")
    authors_meta = dataset.get("authors", {})
    # agrupar por autor (clave = autor_id si existe, si no el nombre)
    authors_map: dict[str, dict] = {}
    for iss in issues:
        for c in iss["contributions"]:
            for a in c["authors"]:
                key = a["autor_id"] or ("name:" + a["name"])
                meta = authors_meta.get(a["autor_id"] or "", {})
                display = meta.get("name_index") or a["name"]  # inverso solo si --author-canonical
                entry = authors_map.setdefault(
                    key, {"name": display, "items": []}
                )
                entry["items"].append((iss, c))
    for key in sorted(authors_map, key=lambda k: sort_key(authors_map[k]["name"])):
        entry = authors_map[key]
        L.append(f"## {entry['name']}")
        L.append("")
        items = sorted(entry["items"], key=lambda ic: ic[0]["issue_number"])
        for iss, c in items:
            pages = fmt_pages(c)
            pg = f", {pages}" if pages else ""
            L.append(
                f"* [Nº {iss['issue_number']}](#ejemplar-{iss['issue_number']}) — "
                f"{iss['month_name']} de {iss['year']} — *{c['title']}*{pg}"
            )
        L.append("")
    L.append("[↑ Años](#anios)")
    L.append("")
    L.append("---")
    L.append("")

    # Índice de títulos
    L.append('<a id="indice-de-titulos"></a>')
    L.append("# Índice de títulos")
    L.append("")
    L.append("Ordenado alfabéticamente por título.")
    L.append("")
    title_rows = []
    for iss in issues:
        for c in iss["contributions"]:
            title_rows.append((iss, c))
    for iss, c in sorted(title_rows, key=lambda ic: sort_key(ic[1]["title"])):
        au = authors_display(c)
        au = f" — {au}" if au else ""
        pages = fmt_pages(c)
        pg = f" — {pages}" if pages else ""
        L.append(
            f"* *{c['title']}*{au} — "
            f"[Nº {iss['issue_number']}](#ejemplar-{iss['issue_number']}) — "
            f"{iss['month_name']} de {iss['year']}{pg}"
        )
    L.append("")
    L.append("[↑ Años](#anios)")
    L.append("")
    L.append("---")
    L.append("")

    # Metodología y cobertura
    L.append('<a id="metodologia-y-cobertura"></a>')
    L.append("## Metodología y cobertura")
    L.append("")
    L.append(
        f"- **Cobertura de ejemplares:** {len(issues)}/157 números de la primera época "
        "(julio de 1923 – julio de 1936)."
    )
    L.append(
        "- **Enlaces al original:** todos los enlaces «📖 Leer el ejemplar digitalizado» "
        "apuntan al **visor oficial de la Fundación Ortega-Marañón**. El proyecto no "
        "aloja, descarga ni redistribuye los facsímiles PDF."
    )
    L.append(
        "- **Origen de los datos bibliográficos:** las contribuciones (autor, título, "
        "páginas, tipo) proceden principalmente de **Dialnet** (código de revista "
        f"{DIALNET_JOURNAL_CODE}, ISSN {DIALNET_ISSN})."
    )
    L.append(
        "- **Cobertura bibliográfica (no exhaustiva):** Dialnet no indexa "
        "necesariamente todo el contenido de cada número. Es habitual que falten "
        "notas, reseñas, textos preliminares y secciones menores. Cuando la paginación "
        "sugiere que faltan páginas entre contribuciones, puede tratarse de material no "
        "indexado; **no se ha rellenado por conjetura**."
    )
    L.append(
        "- **Fidelidad:** se preservan los títulos y nombres de autor tal como los "
        "registra la fuente. No se moderniza la ortografía, no se expanden iniciales, "
        "no se resuelven seudónimos ni se traducen los títulos."
    )
    L.append(
        "- **Nombres de autor:** en el índice de autores se muestran tal como aparecen en "
        "Dialnet («Nombre Apellidos») y se ordenan por esa forma. No se invierten a «Apellidos, "
        "Nombre» para no adivinar el apellido en nombres compuestos (p. ej. «Ortega y Gasset», "
        "«Gómez de la Serna»). El script incluye una opción (`--author-canonical`) que obtiene la "
        "forma catalogada `DC.creator` de Dialnet, pero Dialnet limita el ritmo de peticiones y "
        "por defecto no se utiliza."
    )
    L.append(
        "- **Portadas:** las miniaturas (carpeta `covers/`) se han descargado del archivo "
        "de la Fundación Ortega-Marañón y enlazan al visor oficial. No se descargan ni "
        "redistribuyen los PDF. El Nº 145 no tiene miniatura porque su ficha en el archivo "
        "muestra una imagen equivocada (la del Nº 148)."
    )
    L.append(
        "- **En resumen:** este proyecto debe entenderse como una **herramienta de "
        "consulta bibliográfica (finding aid)**, no como un análisis exhaustivo página "
        "a página de cada ejemplar."
    )
    L.append("")
    problems = dataset["meta"]["validacion"]
    if problems:
        L.append("### Incidencias detectadas automáticamente")
        L.append("")
        L.append(
            "Se registran (sin resolverlas por conjetura) para transparencia y futuras "
            "correcciones:"
        )
        L.append("")
        for p in problems:
            L.append(f"* {p}")
        L.append("")
    L.append(
        "*Índice generado automáticamente a partir de `data/indice.json` mediante "
        "`scripts/build_index.py`.*"
    )
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# Paso 9 — README.md
# --------------------------------------------------------------------------- #

def generate_readme(dataset: dict) -> str:
    n = dataset["meta"]["issues_total"]
    total_c = dataset["meta"]["contribuciones_total"]
    return f"""# Revista de Occidente — Primera época (1923–1936)

Índice bibliográfico de los **{n} números** de la primera época (julio de 1923 –
julio de 1936), con la portada y el enlace al facsímil oficial de cada ejemplar.

## 📖 → [Abrir el índice](indice.md)

Números por año, **índice de autores** e **índice de títulos**.

---

- **Originales digitalizados:** [Fundación Ortega-Marañón](https://ortegaygasset.edu/publicaciones/revista-de-occidente/archivo-ro/numero/).
  Cada número enlaza a su visor oficial. Este repositorio **no aloja los PDF**.
- **Metadatos bibliográficos:** Dialnet (ISSN 0034-8635). Puede omitir notas y reseñas
  menores: es una **herramienta de consulta**, no una transcripción exhaustiva.
- **Reproducible:** `data/indice.json` + `python3 scripts/build_index.py`.
"""


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def print_validation(dataset: dict) -> None:
    print("\n=== Validación (Paso 12) ===")
    issues = dataset["issues"]
    nums = [i["issue_number"] for i in issues]
    print(f"  Ejemplares FOM: {len(nums)} (esperado {EXPECTED_ISSUE_COUNT})")
    print(f"  Secuencia 1..157 sin huecos: {nums == list(range(1, 158))}")
    print(f"  Duplicados de número: {len(nums) - len(set(nums))}")
    no_month = [i['issue_number'] for i in issues if not i['month_name']]
    print(f"  Sin mes/año: {no_month or 'ninguno'}")
    no_link = [i['issue_number'] for i in issues if not i['fom_viewer_url']]
    print(f"  Sin enlace FOM: {no_link or 'ninguno'}")
    no_dialnet = [i['issue_number'] for i in issues if not i['dialnet_ejemplar_id']]
    print(f"  Sin ejemplar Dialnet: {no_dialnet or 'ninguno'}")
    no_contrib = [i['issue_number'] for i in issues if not i['contributions']]
    print(f"  Sin contribuciones Dialnet: {no_contrib or 'ninguno'}")
    for p in dataset["meta"]["validacion"]:
        print(f"   - {p}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Construye el índice de Revista de Occidente (1923–1936).")
    ap.add_argument("--stats", action="store_true", help="solo extraer y mostrar validación")
    ap.add_argument("--offline", action="store_true", help="usar solo la caché (sin red)")
    ap.add_argument("--author-canonical", action="store_true",
                    help="(lento; Dialnet limita el ritmo) descargar DC.creator por autor "
                         "para ordenar el índice por apellido")
    ap.add_argument("--no-covers", action="store_true",
                    help="no descargar las miniaturas de portada")
    args = ap.parse_args()

    fom, fom_notes = collect_fom(offline=args.offline)
    if not args.no_covers:
        fom_notes += download_covers(fom, offline=args.offline)
    dialnet = collect_dialnet(offline=args.offline)
    dataset = build_dataset(fom, dialnet, fom_notes)

    if args.author_canonical:
        # Opcional y LENTO: Dialnet limita el ritmo con retos 503, por lo que esta
        # normalización puede tardar mucho. Por defecto no se usa (respeto al servidor).
        authors, author_notes = collect_author_names(dataset, offline=args.offline)
        dataset["authors"] = authors
        dataset["meta"]["validacion"].extend(author_notes)
    else:
        dataset["authors"] = {}

    print_validation(dataset)

    if args.stats:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "indice.json").write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (ROOT / "indice.md").write_text(generate_indice_md(dataset), encoding="utf-8")
    (ROOT / "README.md").write_text(generate_readme(dataset), encoding="utf-8")
    print("\nGenerado: data/indice.json, indice.md, README.md")


if __name__ == "__main__":
    main()
