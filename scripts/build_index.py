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
COVERS_DIR = ROOT / "covers"          # miniaturas opcionales (--covers); no versionadas

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

# Archivo por autor de la FOM (índice inverso). Recupera los autores de la sección
# «Notas», que Dialnet no indexa.
FOM_AUTHOR_LETTERS = [
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l",
    "m", "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "y-z",
]
FOM_AUTHOR_URL = (
    "https://ortegaygasset.edu/publicaciones/revista-de-occidente/"
    "archivo-ro/autor/autor-{letter}/"
)
_NAME_PARTICLES = {
    "de", "del", "la", "las", "los", "y", "e", "da", "do", "dos",
    "van", "von", "di", "du", "le", "des", "della",
}

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


def merge_key(name: str) -> str:
    """Clave para unir el mismo autor entre fuentes (sin acentos, guiones ni mayúsculas)."""
    s = sort_key(name).replace("-", " ")
    return re.sub(r"\s+", " ", s).strip()


def _case_word(w: str) -> str:
    return "-".join(p[:1].upper() + p[1:].lower() for p in w.split("-"))


def reverse_name(fom_name: str) -> str:
    """Convierte la forma de la FOM «APELLIDOS, Nombre» a «Nombre Apellidos».

    La coma marca de forma inequívoca el corte entre apellidos y nombre, por lo que
    la inversión es determinista (no se adivina el apellido). Solo se ajusta el uso
    de mayúsculas y se conservan partículas (de, la, y…) en minúscula.
    """
    if "," not in fom_name:
        return fom_name
    surnames, given = fom_name.split(",", 1)
    given = given.split("(")[0].strip()  # descarta el nombre real entre paréntesis
    fixed = [
        w.lower() if w.lower() in _NAME_PARTICLES else _case_word(w)
        for w in surnames.strip().split()
    ]
    surnames_disp = " ".join(fixed)
    return f"{given} {surnames_disp}".strip() if given else surnames_disp


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


_TITLE_SPAN_RE = re.compile(r'<span class="vc_tta-title-text">(.*?)</span>', re.S)
_NOTE_BTN_RE = re.compile(r'href="[^"]*visor-pdfro/\?pdf=(\d+)"[^>]*>(.*?)</a>', re.S)
_NOTE_LABEL_RE = re.compile(r"(\d{4})-(\d{2})\s+Notas", re.I)


def collect_fom_notes(fom: dict[int, dict], offline: bool = False) -> dict[int, list]:
    """Índice inverso: para cada ejemplar, los autores que firman en la sección «Notas».

    Se obtiene del archivo por autor de la FOM (24 páginas por letra). Dialnet no
    indexa las notas, por lo que esta es la única fuente estructurada de esos autores.
    """
    print("Paso 1c — FOM: índice inverso de autores de la sección «Notas…»")
    by_ym = {(v["year"], v["month_num"]): num for num, v in fom.items()}
    per_issue: dict[int, dict[str, str]] = {}
    for letter in FOM_AUTHOR_LETTERS:
        text = fetch(FOM_AUTHOR_URL.format(letter=letter), offline=offline)
        titles = list(_TITLE_SPAN_RE.finditer(text))
        for i, tm in enumerate(titles):
            author = clean(tm.group(1))
            start = tm.end()
            end = titles[i + 1].start() if i + 1 < len(titles) else len(text)
            body = text[start:end]
            for bm in _NOTE_BTN_RE.finditer(body):
                label = clean(bm.group(2))
                nm = _NOTE_LABEL_RE.search(label)
                if not nm:
                    continue
                num = by_ym.get((int(nm.group(1)), int(nm.group(2))))
                if num is None:
                    continue
                per_issue.setdefault(num, {}).setdefault(author, reverse_name(author))
    result: dict[int, list] = {}
    for num, mapping in per_issue.items():
        items = [{"name": disp, "name_fom": fom_n} for fom_n, disp in mapping.items()]
        items.sort(key=lambda x: sort_key(x["name_fom"]))
        result[num] = items
    total = sum(len(v) for v in result.values())
    print(f"  autores de notas: {total} en {len(result)} ejemplares")
    return result


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
                  fom_notes: list[str] | None = None,
                  note_authors: dict[int, list] | None = None) -> dict:
    print("Paso 3 — Cruzando FOM y Dialnet (número + año) y validando…")
    problems: list[str] = list(fom_notes or [])
    note_authors = note_authors or {}

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
            "note_authors": note_authors.get(num, []),
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
    total_note_authors = sum(len(i["note_authors"]) for i in issues)
    # Ejemplares con autores de notas (FOM) pero sin línea «Notas» en Dialnet
    notas_only_fom = [
        i["issue_number"] for i in issues
        if i["note_authors"] and not any(
            c["title"].strip().lower() == "notas" for c in i["contributions"]
        )
    ]
    if notas_only_fom:
        problems.append(
            "Notas presentes en el archivo por autor de la FOM pero sin entrada «Notas» "
            f"en Dialnet en el/los número(s) {notas_only_fom} (se añade la sección con "
            "sus autores)."
        )
    print(f"  Ejemplares: {len(issues)} · contribuciones: {total_contribs}"
          f" · autores de notas: {total_note_authors}")
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
        "- Los **autores de la sección «Notas»** (que Dialnet no indexa) se recuperan del "
        "**archivo por autor de la Fundación Ortega-Marañón** y se listan bajo cada «Notas»."
    )
    L.append(
        "- **Limitación importante:** aun así, el detalle por contribución depende de Dialnet, "
        "que no siempre indexa la totalidad del contenido (puede omitir reseñas, textos "
        "preliminares o secciones menores). Por tanto, este índice es una **herramienta de "
        "consulta bibliográfica (finding aid)**, no una transcripción exhaustiva página a "
        "página. Véase la [nota de metodología y cobertura](#metodologia-y-cobertura)."
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
    L.append("Otros índices: **[Índice de autores](autores.md)** · "
             "**[Índice de títulos](titulos.md)**")
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
            note_list = iss.get("note_authors") or []
            notas_done = False
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
                if note_list and not notas_done and c["title"].strip().lower() == "notas":
                    for a in note_list:
                        L.append(f"  * {a['name']}")
                    notas_done = True
            if note_list and not notas_done:
                L.append("* *Notas*")
                for a in note_list:
                    L.append(f"  * {a['name']}")
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
        "reseñas, textos preliminares y secciones menores. Cuando la paginación "
        "sugiere que faltan páginas entre contribuciones, puede tratarse de material no "
        "indexado; **no se ha rellenado por conjetura**."
    )
    L.append(
        "- **Autores de «Notas» (índice inverso):** Dialnet suele registrar la sección de "
        "notas como una sola entrada «Notas» sin autor. Los nombres que aparecen bajo cada "
        "«Notas» se han recuperado del **archivo por autor de la FOM** cruzando por año y mes. "
        "Se listan **solo los autores** (sin título ni páginas de cada nota). El nombre se "
        "invierte de «Apellidos, Nombre» a «Nombre Apellidos» usando la coma como separador "
        "(sin adivinar el apellido). Estos autores también aparecen en el "
        "[índice de autores](autores.md), con sus entradas marcadas como *Notas*."
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
# Pasos 6 y 7 — índices separados (autores.md, titulos.md)
# --------------------------------------------------------------------------- #

_NAV = "[← Índice por años](indice.md) · [Índice de autores](autores.md) · [Índice de títulos](titulos.md)"


def generate_autores_md(dataset: dict) -> str:
    issues = dataset["issues"]
    authors_meta = dataset.get("authors", {})
    L: list[str] = []
    L.append("# Índice de autores")
    L.append("")
    L.append(_NAV)
    L.append("")
    L.append(
        "Incluye tanto los **artículos** (fuente: Dialnet) como los autores de la sección "
        "**«Notas»** (fuente: archivo por autor de la FOM). Las entradas de notas se marcan "
        "como *Notas* y no llevan título ni páginas. Los nombres se muestran tal como los "
        "registran las fuentes; **no se corrigen por conjetura**. Usa la búsqueda del "
        "navegador (Ctrl/Cmd-F) para localizar a un autor por cualquier parte del nombre."
    )
    L.append("")

    groups: dict[str, dict] = {}         # gid -> {name, items:[(num, iss, kind, c)]}
    norm_to_gids: dict[str, set] = {}    # merge_key -> {gid}

    def ensure_group(gid: str, display: str) -> dict:
        g = groups.get(gid)
        if g is None:
            g = groups[gid] = {"name": display, "items": []}
            norm_to_gids.setdefault(merge_key(display), set()).add(gid)
        return g

    # 1) Artículos de Dialnet, agrupados por autor_id
    for iss in issues:
        for c in iss["contributions"]:
            for a in c["authors"]:
                gid = a["autor_id"] or ("name:" + a["name"])
                meta = authors_meta.get(a["autor_id"] or "", {})
                display = meta.get("name_index") or a["name"]
                ensure_group(gid, display)["items"].append(
                    (iss["issue_number"], iss, "art", c)
                )

    # 2) Autores de «Notas» (FOM): unir con el autor de Dialnet si el nombre coincide
    for iss in issues:
        for na in iss.get("note_authors") or []:
            nk = merge_key(na["name"])
            gids = norm_to_gids.get(nk)
            if gids and len(gids) == 1:
                gid = next(iter(gids))
            else:
                gid = "nota:" + nk
                ensure_group(gid, na["name"])
            groups[gid]["items"].append((iss["issue_number"], iss, "nota", None))

    for gid in sorted(groups, key=lambda k: sort_key(groups[k]["name"])):
        g = groups[gid]
        L.append(f"## {g['name']}")
        L.append("")
        seen = set()
        for num, iss, kind, c in sorted(g["items"], key=lambda t: (t[0], t[2])):
            if kind == "art":
                pages = fmt_pages(c)
                pg = f", {pages}" if pages else ""
                line = (f"* [Nº {num}](indice.md#ejemplar-{num}) — "
                        f"{iss['month_name']} de {iss['year']} — *{c['title']}*{pg}")
            else:
                key = (num, "nota")
                if key in seen:
                    continue
                seen.add(key)
                line = (f"* [Nº {num}](indice.md#ejemplar-{num}) — "
                        f"{iss['month_name']} de {iss['year']} — *Notas*")
            L.append(line)
        L.append("")
    L.append(_NAV)
    L.append("")
    return "\n".join(L)


def generate_titulos_md(dataset: dict) -> str:
    issues = dataset["issues"]
    L: list[str] = []
    L.append("# Índice de títulos")
    L.append("")
    L.append(_NAV)
    L.append("")
    L.append("Ordenado alfabéticamente por título.")
    L.append("")
    rows = [(iss, c) for iss in issues for c in iss["contributions"]]
    for iss, c in sorted(rows, key=lambda ic: sort_key(ic[1]["title"])):
        au = authors_display(c)
        au = f" — {au}" if au else ""
        pages = fmt_pages(c)
        pg = f" — {pages}" if pages else ""
        L.append(
            f"* *{c['title']}*{au} — "
            f"[Nº {iss['issue_number']}](indice.md#ejemplar-{iss['issue_number']}) — "
            f"{iss['month_name']} de {iss['year']}{pg}"
        )
    L.append("")
    L.append(_NAV)
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
julio de 1936), con enlace al facsímil oficial de cada ejemplar.

## 📖 → [Abrir el índice](indice.md)

Números por año, **[índice de autores](autores.md)** e **[índice de títulos](titulos.md)**.

---

- **Originales digitalizados:** [Fundación Ortega-Marañón](https://ortegaygasset.edu/publicaciones/revista-de-occidente/archivo-ro/numero/).
  Cada número enlaza a su visor oficial. Este repositorio **no aloja los PDF**.
- **Metadatos bibliográficos:** Dialnet (ISSN 0034-8635). Puede omitir reseñas y
  secciones menores: es una **herramienta de consulta**, no una transcripción exhaustiva.
- **Autores de la sección «Notas»:** recuperados del archivo por autor de la FOM
  (Dialnet no los indexa) y listados bajo cada «Notas».
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
    ap.add_argument("--covers", action="store_true",
                    help="(opcional) descargar las miniaturas de portada a covers/ "
                         "(no se muestran en el índice ni se versionan)")
    ap.add_argument("--no-notes", action="store_true",
                    help="no construir el índice inverso de autores de «Notas» (FOM)")
    args = ap.parse_args()

    fom, fom_notes = collect_fom(offline=args.offline)
    if args.covers:
        fom_notes += download_covers(fom, offline=args.offline)
    note_authors = {} if args.no_notes else collect_fom_notes(fom, offline=args.offline)
    dialnet = collect_dialnet(offline=args.offline)
    dataset = build_dataset(fom, dialnet, fom_notes, note_authors)

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
    (ROOT / "autores.md").write_text(generate_autores_md(dataset), encoding="utf-8")
    (ROOT / "titulos.md").write_text(generate_titulos_md(dataset), encoding="utf-8")
    (ROOT / "README.md").write_text(generate_readme(dataset), encoding="utf-8")
    print("\nGenerado: data/indice.json, indice.md, autores.md, titulos.md, README.md")


if __name__ == "__main__":
    main()
