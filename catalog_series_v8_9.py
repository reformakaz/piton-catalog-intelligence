"""Read-only catalogue card for PitON E3 and Legrand RCP-4C."""
from collections import defaultdict
from contextlib import closing
from html import escape
from math import isclose, isfinite
from pathlib import Path
import re
import sqlite3

import matrix_comparison_card_v8 as legacy_card
import matrix_v7

PRODUCT = "Legrand RCP-4C (3L+N)"
TITLE = "Токопровод Legrand RCP‑4C (3L+N)"
PDF = "https://assets.legrand.com/pim/NP-FT-GT/F04447EN_00.pdf"
VARIANT = re.compile(r"(Al|Cu)-([1-9][0-9]{0,5})")
CSS = """
.series-form {display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;margin:16px 0 8px}
.series-form input[type="hidden"] {display:none}
.series-form label {font-weight:600}
.series-form select,.series-form button {font:inherit;line-height:1.25;min-height:34px;padding:6px 8px;max-width:100%;border:1px solid #9ead9f;border-radius:5px;background:white;color:#1b2d26}
.series-form button {background:#215940;color:white}
.series-switch {display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}
.series-switch span {font-weight:700}
.series-source-cell small {display:block;margin-top:4px}
@media (max-width:450px) {
  .series-form label {flex-basis:100%}
  .series-card table,.series-card tbody {display:block;border:0}
  .series-card thead {position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
  .series-card tbody tr {display:flex;flex-wrap:wrap;min-width:0;border:1px solid #d8e3db;border-radius:6px;margin-bottom:8px}
  .series-card tbody th,.series-card tbody td {display:block;flex:1 1 44%}
  .series-card tbody th,.series-card tbody td:last-child {flex:1 0 94%}
  .series-card tbody th {background:#e5f0e8}
  .series-card tbody td {border:0;min-width:0}
  .series-card tbody td::before {display:block;content:attr(data-label);font-weight:650;color:#435a4e}
}
@media print {.series-form,.series-switch {display:none}}
"""


def records(db):
    statement = """
        SELECT vendor, product, material, rating_a, parameter, value, unit, source, status
        FROM specs
        WHERE ((vendor = 'piton' AND product = 'PitON E3') OR (vendor = 'legrand' AND product = ?))
          AND material IN ('Al', 'Cu')
        ORDER BY material, rating_a, vendor, parameter
    """
    uri = Path(db).resolve().as_uri() + "?mode=ro"
    variants = defaultdict(dict)
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(statement, (PRODUCT,)):
            entry = dict(row)
            material, rating, parameter = entry["material"], entry["rating_a"], entry["parameter"]
            value = entry["value"]
            if (parameter not in matrix_v7.PARAMS or type(rating) is not int or not 1 <= rating <= 999999
                    or type(value) not in (int, float) or not isfinite(value) or value <= 0):
                continue
            entries = variants[(material, rating)]
            key = (entry["vendor"], parameter)
            if key in entries and entries[key] != entry:
                entries[key] = None
            else:
                entries[key] = entry
    return dict(variants)


def card_link(material, rating):
    return f"?view=series&amp;variant={material}-{rating}"


def variant_name(material, rating):
    number = f"{rating:,}".replace(",", " ")
    return f"{legacy_card.MATERIALS[material]} ({material}), {number} А"


def source_markup(entry, vendor):
    if not entry:
        return ""
    reference = entry.get("source") or ""
    if vendor == "legrand":
        match = re.search(re.escape(PDF) + r"#page=(4|9)\s*$", reference)
        if match:
            page = match[1]
            return f'<small><a href="{PDF}#page={page}">Техлист, стр. {page}</a></small>'
    elif match := re.search(r"\bPDF стр\. (6[0-3])\b", reference):
        page = match[1]
        return f'<small><a href="{legacy_card.PITON_PDF}#page={page}">Каталог, стр. {page}</a></small>'
    return f"<small>{escape(reference)}</small>" if reference else ""


def piton_value(code, entry):
    shown = legacy_card.display_value(entry) + source_markup(entry, "piton")
    if not entry or code not in ("icw_1s_ka", "ipk_ka"):
        return shown, entry, False
    detail = {"code": code, "piton": entry}
    if code == "icw_1s_ka":
        conflict_lookup = getattr(matrix_v7, "icw_catalog_conflict", None)
        if conflict_lookup is None:
            in_old_cu_table = entry["material"] == "Cu" and "PDF стр. 62" in (entry.get("source") or "")
            return shown, entry, in_old_cu_table or legacy_card.needs_review(entry)
        conflict = conflict_lookup(detail)
        if conflict:
            return matrix_v7.icw_conflict_links(conflict) + "<small>Разные редакции</small>", entry, True
        return shown, entry, False
    confirm = getattr(matrix_v7, "confirmed_ipk_source", None)
    source = confirm(detail) if confirm else None
    if source:
        confirmed = dict(entry, unit="kA")
        return legacy_card.display_value(confirmed) + f'<small><a href="{escape(source)}">Каталог EN</a></small>', confirmed, False
    ambiguous_page = re.search(r"\bPDF стр\. 6[0-3]\b", entry.get("source") or "")
    if entry["unit"] != "kA" or ambiguous_page or legacy_card.needs_review(entry):
        number = f'{entry["value"]:g}'.replace(".", ",")
        return f"{number}<small>Единица требует сверки</small>" + source_markup(entry, "piton"), entry, True
    return shown, entry, False


def outcome(piton, legrand, direction, unit, requires_review):
    if not (piton and legrand):
        return "Нет пары"
    if requires_review or piton["unit"] != unit or legrand["unit"] != unit:
        return "Сверка"
    if isclose(piton["value"], legrand["value"], abs_tol=1e-9, rel_tol=0):
        return "Совпадает"
    if direction == "equal":
        return "Различается"
    side = "PitON" if (piton["value"] > legrand["value"]) == (direction == "high") else "Legrand"
    return f'{"Ниже" if direction == "low" else "Выше"} у {side}'


def parameter_rows(entries):
    rows = []
    for code, (label, direction, unit) in matrix_v7.PARAMS.items():
        piton = entries.get(("piton", code))
        other_key = ("legrand", code)
        legrand = entries.get(other_key)
        left, piton, disputed = piton_value(code, piton)
        right = legacy_card.display_value(legrand) + source_markup(legrand, "legrand")
        if legrand is None and other_key in entries:
            right = "Несколько версий · сверить источник"
        if piton is None and ("piton", code) in entries:
            left = "Несколько версий · сверить источник"
        verdict = outcome(piton, legrand, direction, unit, disputed)
        rows.append(
            f'<tr data-parameter="{code}"><th scope="row">{escape(legacy_card.LABELS.get(code, label))}</th>'
            f'<td class="series-source-cell" data-label="PitON E3">{left}</td>'
            f'<td class="series-source-cell" data-label="Legrand RCP‑4C">{right}</td>'
            f'<td data-label="Сравнение">{verdict}</td></tr>'
        )
    return "".join(rows)


def render(data, variants, material, rating):
    key = (material, rating)
    entries = variants.get(key, {})
    choices = sorted(variants.keys() | {key})
    options = "".join(
        f'<option value="{metal}-{amps}"{" selected" if key == (metal, amps) else ""}>'
        f'{variant_name(metal, amps)}</option>' for metal, amps in choices
    )
    has_graziadio = any(
        row["material"] == material and row["rating_a"] == rating and legacy_card.paired(row)
        for row in data["rows"]
    )
    old_link = (
        f'<a href="{legacy_card.card_url(material, rating)}">Graziadio ISOLSBARRA</a>'
        if has_graziadio else ""
    )
    missing = ""
    if not any(entry for (vendor, _), entry in entries.items() if vendor == "legrand"):
        missing = '<p class="notice">В базе пока нет характеристик Legrand для этого материала и номинала.</p>'
    note = (
        '<p class="notice">Звёздочкой отмечены значения, которые требуют инженерной сверки.</p>'
        if any(legacy_card.needs_review(record) for record in entries.values()) else ""
    )
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Карточка сравнения · {TITLE}</title><style>{legacy_card.CSS}{CSS}</style></head>
<body><main class="series-card"><nav><a href="?material={material}">← К матрице</a><span>Печать или PDF · Ctrl+P</span></nav>
<header><p class="eyebrow">Сравнение серий</p><h1>{TITLE}</h1>
<p class="variant">PitON E3 и Legrand RCP‑4C · {variant_name(material, rating)}</p></header>
<div class="series-switch" aria-label="Серии конкурентов">{old_link}<span aria-current="page">Legrand RCP‑4C</span></div>
<form class="series-form" method="get"><input type="hidden" name="view" value="series">
<label for="series-variant">Материал и номинал</label><select id="series-variant" name="variant">{options}</select>
<button type="submit">Показать</button></form>
{missing}{note}<div class="scroll"><table><thead><tr><th>Параметр</th><th>PitON E3</th>
<th>Legrand RCP‑4C</th><th>Сравнение</th></tr></thead><tbody>{parameter_rows(entries)}</tbody></table></div>
<section><h2>Исполнение</h2><p>В карточке использованы данные Legrand RCP‑4C (3L+N). Для сравнения КП
проверьте серию и исполнение в предложении поставщика.</p></section>
<section><h2>Источник</h2><p class="source"><a href="{PDF}">Legrand F04447EN-00 · официальный техлист (PDF)</a></p></section>
</main></body></html>"""


def matrix_view(db, data, params, base_page):
    view = (params.get("view") or [""])[0]
    if view == "series":
        variant = VARIANT.fullmatch((params.get("variant") or ["Al-1600"])[0])
        if not variant:
            return legacy_card.error_page(400, "Выберите материал и номинал.")
        material, rating = variant[1], int(variant[2])
        # The default matrix may be filtered. Read its full set if needed for the Graziadio link.
        if data["scope"] not in ("all", material):
            data = matrix_v7.matrix(db, material)
        return 200, render(data, records(db), material, rating)
    status, page = legacy_card.matrix_view(data, params, base_page)
    if status != 200:
        return status, page
    if view == "card":
        material = params["material"][0]
        rating = int(params["rating"][0])
        link = f'<a href="{card_link(material, rating)}">Сравнить с Legrand RCP‑4C</a>'
        return status, page.replace("</nav>", link + "</nav>", 1)
    if view == "":
        link = f'<p><a class="download" id="legrand-series-entry" href="{card_link("Al", 1600)}">Сравнить серии · Legrand RCP‑4C</a></p>'
        page = page.replace("</h1>", "</h1>" + link, 1)
    return status, page
