#!/usr/bin/env python3
"""Add printable comparison cards to the existing PitON catalogue matrix."""
import argparse
from datetime import datetime, timezone
import fcntl
from html import escape
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

from matrix_v7 import (
    METRIC_CSS, comparison_note_markup, electrical_badge, icw_conflict_links,
    metric_highlight, unconfirmed_ipk_page,
)

ROOT = Path("/opt/piton-catalog-intelligence")
WEB = ROOT / "web.py"
MODULE = ROOT / "matrix_comparison_card_v8.py"
BACKUPS = Path("/var/backups/piton-catalog-comparison-card-v8")
BASE_URL = "http://127.0.0.1:8137"
SERVICE = "piton-catalog-web"
V7_ROUTE = (
    "   if path=='/matrix-v7':return self.send(200,matrix_v7.page(data).encode('utf-8'),"
    "'text/html; charset=utf-8')\n"
)
V8_ROUTE = (
    "   if path=='/matrix-v7':\n"
    "    from matrix_comparison_card_v8 import matrix_view\n"
    "    status, page = matrix_view(data, self.params(), matrix_v7.page)\n"
    "    return self.send(status,page.encode('utf-8'),'text/html; charset=utf-8')\n"
)
MATERIALS = {"Al": "Алюминий", "Cu": "Медь"}
REVIEW_STATUSES = {"manual_review", "ocr", "unverified", "pending"}
LABELS = {
    "nominal_voltage_v": "Напряжение изоляции Ui",
    "icw_1s_ka": "Стойкость при КЗ между фазами Icw, 1 с",
    "ipk_ka": "Пиковый ток Ipk",
    "r20_mohm_m": "Фазное сопротивление R20 при 20 °C",
}
SUMMARY = re.compile(r"(<summary><b>(Al|Cu) · ([0-9]+) А</b>[^<]*</summary>)")
PITON_PDF = "https://pitonelectric.ru/uploads/shinoprovod_e3.pdf"
GRAZIADIO_PDF = "https://www.graziadio.it/wp-content/uploads/2017/12/ISOLSBARRA-Catalogo-2020-1.pdf"
MISSING_PAIR_NOTES = {
    ("Al", 400): (
        "Алюминиевый ряд Graziadio ISOLSBARRA начинается с 630 А.",
        f"{GRAZIADIO_PDF}#page=13", "Каталог Graziadio, стр. 86",
    ),
    ("Al", 6400): (
        "Алюминиевый ряд Graziadio ISOLSBARRA заканчивается на 5000 А.",
        f"{GRAZIADIO_PDF}#page=13", "Каталог Graziadio, стр. 86",
    ),
    ("Cu", 630): (
        "Медный ряд Graziadio ISOLSBARRA начинается с 800 А.",
        f"{GRAZIADIO_PDF}#page=13", "Каталог Graziadio, стр. 87",
    ),
    ("Cu", 6300): (
        "В медном ряду PitON E3 за номиналом 5000 А следует 6400 А.",
        f"{PITON_PDF}#page=63", "Каталог PitON, стр. 63 PDF",
    ),
    ("Cu", 6400): (
        "Медный ряд Graziadio ISOLSBARRA заканчивается на 6300 А.",
        f"{GRAZIADIO_PDF}#page=13", "Каталог Graziadio, стр. 87",
    ),
}
CSS = """
* { box-sizing: border-box; }
html { color: #1b2d26; background: #eef3ef; font: 15px/1.48 system-ui, "DejaVu Sans", sans-serif; }
body { margin: 0; padding: 24px 16px; }
main { max-width: 880px; margin: auto; background: #fff; padding: 32px; border: 1px solid #d7e4dc; border-radius: 12px; }
h1, h2, p { margin: 0; }
h1 { margin-top: 5px; font-size: 29px; line-height: 1.17; }
h2 { margin-bottom: 7px; font-size: 16px; }
.eyebrow { color: #205940; font-weight: 700; font-size: 13px; letter-spacing: .02em; }
.variant { color: #435a4e; margin-top: 9px; font-size: 17px; }
nav { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 18px; flex-wrap: wrap; }
nav span { color: #435a4e; font-size: 13px; }
a { color: #095a41; text-underline-offset: 3px; }
.notice { margin: 17px 0; padding: 9px 12px; border-left: 3px solid #b17518; background: #fcf4e7; color: #624617; }
.scroll { overflow-x: auto; margin-top: 19px; }
table { border: 1px solid #ccdad1; border-collapse: collapse; table-layout: fixed; width: 100%; min-width: 570px; }
th, td { padding: 10px; border-bottom: 1px solid #d8e3db; text-align: left; vertical-align: top; }
thead th { background: #e5f0e8; color: #143e2f; font-size: 13px; }
thead th:first-child { width: 34%; }
thead th:nth-child(2), thead th:nth-child(3) { width: 20%; }
tbody th { font-weight: 600; }
tbody td { font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
section { margin-top: 18px; }
.comparison-summary ul { margin: 5px 0 0; padding-left: 20px; }
.comparison-summary li + li { margin-top: 3px; }
.comparison-summary ul + p { margin-top: 6px; }
.source { font-size: 13px; overflow-wrap: anywhere; }
.source + .source { margin-top: 7px; }
@media (max-width: 620px) {
  body { padding: 8px; }
  main { padding: 16px; }
  h1 { font-size: 23px; }
  table { min-width: 0; font-size: 13px; }
  th, td { padding: 7px 5px; overflow-wrap: anywhere; }
  thead th { font-size: 12px; }
}
@page { size: A4 portrait; margin: 14mm; }
@media print {
  html { background: white; font-size: 10.5pt; }
  body, main { margin: 0; padding: 0; }
  main { border: 0; max-width: none; }
  nav { display: none; }
  .scroll { overflow: visible; }
  table { min-width: 0; }
  th, td { padding: 7px; }
  tr, section { break-inside: avoid; }
}
"""


CSS += METRIC_CSS


def paired(row):
    return all(any(detail.get(vendor) for detail in row["details"]) for vendor in ("piton", "graziadio"))


def card_url(material, rating):
    return f"?material={material}&amp;rating={rating}&amp;view=card"


def annotate_matrix(document, rows):
    comparisons = {(row["material"], row["rating_a"]): paired(row) for row in rows}

    def add(match):
        material, rating = match[2], int(match[3])
        key = (material, rating)
        if comparisons.get(key):
            return (
                f'{match[1]}<p style="padding:0 10px"><a class="download" '
                f'href="{card_url(material, rating)}">Карточка сравнения</a></p>'
            )
        if key in comparisons and key in MISSING_PAIR_NOTES:
            reason, source_url, source_label = MISSING_PAIR_NOTES[key]
            summary = match[1].removesuffix("</summary>")
            return (
                f'{summary}<small style="display:block;margin-top:.3em">{escape(reason)} '
                f'<a href="{escape(source_url)}">{escape(source_label)}</a></small></summary>'
            )
        return match[1]

    return SUMMARY.sub(add, document)


def display_value(entry):
    if not entry:
        return "Нет данных"
    formatted = f"{entry['value']:g}".replace(".", ",")
    unit = {"V": "В", "kA": "кА", "mΩ/m": "мОм/м"}.get(entry["unit"], entry["unit"])
    review = '<sup aria-label="Требует сверки">*</sup>' if needs_review(entry) else ""
    return f"{formatted} {escape(unit)}{review}"


def needs_review(entry):
    return bool(entry and str(entry.get("status") or "").lower() in REVIEW_STATUSES)


def card_parameter_row(detail):
    page = unconfirmed_ipk_page(detail)
    piton = metric_highlight(display_value(detail["piton"]), electrical_badge(detail, "piton"))
    graziadio = metric_highlight(display_value(detail["graziadio"]), electrical_badge(detail, "graziadio"))
    qualifier = ""
    if page:
        value = f'{detail["piton"]["value"]:g}'.replace(".", ",")
        piton = f'{value} · <a href="{PITON_PDF}#page={page}">единица требует сверки</a>'
        qualifier = ' data-ipk-unit="unconfirmed"'
    if detail.get("comparison_source"):
        piton += f' · <a href="{escape(detail["comparison_source"])}">Каталог EN</a>'
    if detail.get("comparison_conflict"):
        piton = icw_conflict_links(detail["comparison_conflict"])
        qualifier = ' data-icw-conflict="true"'
    return (
        f'<tr{qualifier}><th scope="row">{LABELS[detail["code"]]}{comparison_note_markup(detail)}</th>'
        f'<td>{piton}</td><td>{graziadio}</td></tr>'
    )


def comparison_summary(row):
    vendor_names = {"piton": "PitON", "graziadio": "Graziadio"}
    labels = {"icw_1s_ka": "Icw (1 с)", "ipk_ka": "Ipk", "r20_mohm_m": "R20"}
    details = row["details"]
    comparable = [detail for detail in details if not unconfirmed_ipk_page(detail)]
    matches = sum(detail["verdict"] == "equal" for detail in comparable)
    unresolved = len(details) - len(comparable)
    unresolved += sum(detail["verdict"] in ("missing", "different") for detail in comparable)
    statements = []
    for detail in comparable:
        vendor = detail["verdict"]
        code = detail["code"]
        if vendor not in vendor_names or code not in labels:
            continue
        other_vendor = "graziadio" if vendor == "piton" else "piton"
        entry, other_entry = detail[vendor], detail[other_vendor]
        delta = abs(entry["value"] - other_entry["value"])
        difference = display_value({"value": delta, "unit": entry["unit"]})
        direction = "ниже" if code == "r20_mohm_m" else "выше"
        comparison = f"{labels[code]} у {vendor_names[vendor]} {direction} на {difference}"
        if code == "r20_mohm_m" and other_entry["value"] > 0:
            percent = f'{100 * delta / other_entry["value"]:.1f}'.replace(".", ",")
            if percent != "0,0":
                comparison += f" ({percent}% от значения {vendor_names[other_vendor]})"
        statements.append(f"<li>{comparison}.</li>")

    items = f'<ul>{"".join(statements)}</ul>' if statements else ""
    remaining = f"<p>Требуют уточнения или данных для сравнения: {unresolved}.</p>" if unresolved else ""
    note = (
        '<p class="source">Часть исходных значений требует инженерной сверки.</p>'
        if any(needs_review(detail[vendor]) for detail in details for vendor in vendor_names)
        else ""
    )
    return (
        '<section class="comparison-summary"><h2>Краткий вывод</h2>'
        f"<p>Совпало параметров: {matches} из {len(details)}.</p>{items}{remaining}{note}</section>"
    )


def sources(row, vendor):
    entries = [d[vendor] for d in row["details"] if d.get(vendor)]
    if not entries:
        return ""
    name = "PitON E3" if vendor == "piton" else "Graziadio ISOLSBARRA"
    originals = list(dict.fromkeys(item.get("source") or "Источник в базе не указан" for item in entries))
    links = []
    note = ""
    if vendor == "piton":
        pattern = r"\bPDF стр\. (6[0-3])\b"
        pages = sorted({int(m) for ref in originals for m in re.findall(pattern, ref)})
        originals = [ref for ref in originals if not re.search(pattern, ref)]
        links = [f'<a href="{PITON_PDF}#page={page}">PDF, стр. {page}</a>' for page in pages]
        if any(unconfirmed_ipk_page(detail) for detail in row["details"]):
            note = '<br>В PDF PitON в строке Ipk напечатано «мОм/м». Для сравнения сверяйте Ipk с протоколом испытаний, где указана единица тока.'
    else:
        table = "левая таблица" if row["material"] == "Al" else "правая таблица"
        unmapped = [ref for ref in originals if f"PDF стр. 14, {table}" not in ref]
        if unmapped != originals:
            sheet = 86 if row["material"] == "Al" else 87
            links = [f'<a href="{GRAZIADIO_PDF}#page=13">Каталог, лист {sheet} (стр. 13 PDF)</a>']
        originals = unmapped
    heading = " · ".join([f"<strong>{name}</strong>", *links])
    citations = "".join("<br>" + escape(ref) for ref in originals)
    return f'<p class="source">{heading}{citations}{note}</p>'


def electrical_table(row):
    table = "".join(card_parameter_row(detail) for detail in row["details"])
    note = ""
    if any(needs_review(d[vendor]) for d in row["details"] for vendor in ("piton", "graziadio")):
        note = '<p class="notice">Звёздочкой отмечены значения, которые требуют инженерной сверки.</p>'
    return (
        note + '<div class="scroll"><table class="catalog-comparison-table">'
        '<thead><tr><th>Параметр</th><th>PitON E3</th><th>Graziadio ISOLSBARRA</th></tr></thead>'
        f'<tbody>{table}</tbody></table></div>'
    )


def technical_comparison(row):
    from catalog_physical_v8_11_9 import comparison_markup
    material, rating = row["material"], row["rating_a"]
    full_card = card_url(material, rating)
    return (
        electrical_table(row) + comparison_markup(material, rating, row.get("phase_geometry", {}))
        + f'<p class="quote-tech-source"><a href="{full_card}">Карточка сравнения и источники</a></p>'
    )


def card(row):
    from catalog_physical_v8_11_9 import comparison_markup
    material = row["material"]
    rating = f'{row["rating_a"]:,}'.replace(",", " ")
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Карточка сравнения · {material} {rating} А</title><style>{CSS}</style></head>
<body><main><nav><a href="?material={material}">← К матрице</a><span>Печать или PDF · Ctrl+P</span></nav>
<header><p class="eyebrow">Карточка сравнения шинопроводов</p>
<h1>PitON E3 / Graziadio ISOLSBARRA</h1><p class="variant">{MATERIALS[material]} ({material}), {rating} А</p></header>
{electrical_table(row)}
{comparison_summary(row)}
{comparison_markup(material, row['rating_a'], row.get('phase_geometry', {}))}
<section><h2>Что сверить с проектировщиком</h2>
<p>Исполнение нейтрали и PE, степень IP, условия монтажа и расчётные токи КЗ в точке установки.</p></section>
<section><h2>Источники данных</h2>{sources(row, "piton")}{sources(row, "graziadio")}</section>
</main></body></html>"""


QUOTE_COMPARISON_HTML = '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Сравнение КП · PitON Catalog Intelligence</title>\n<style>\n*{box-sizing:border-box}body{margin:0;padding:20px 16px;background:#eff3f0;color:#1b2d26;font:15px/1.5 system-ui,"DejaVu Sans",sans-serif}\nmain{max-width:1120px;margin:auto;padding:26px;background:#fff;border:1px solid #d9e4dd;border-radius:12px}a{color:#126348}h1,h2,p{margin:0}h1{margin:9px 0 4px;font-size:clamp(24px,4vw,34px);line-height:1.2}h2{font-size:18px;margin:16px 0 8px}.muted{color:#54675d}.offers,.summary-cards{display:flex;gap:14px;flex-wrap:wrap;margin-top:16px}.offer,.total-card{min-width:0;flex:1 1 44%;margin:0;padding:14px;border:1px solid #d9e4dd;border-radius:8px}.offer legend{padding:0 5px;font-weight:700}.fields{display:flex;gap:10px 14px;flex-wrap:wrap}.field{display:flex;flex-direction:column;gap:4px;flex:1 1 42%;min-width:0;font-size:13px;font-weight:600}.wide{flex-basis:100%}input,select,textarea,button{font:inherit;color:inherit}input,select,textarea{max-width:100%;min-width:0;padding:7px 9px;border:1px solid #aabdb1;border-radius:5px;background:#fff}input,select{min-height:36px;width:11em}.field.wide input:not([type=file]){width:100%}textarea{width:100%;resize:vertical;min-height:100px}.offer details{margin-top:10px}.offer summary{cursor:pointer;color:#126348;font-weight:650}.offer details .fields{margin-top:10px}.actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:16px}button{border:1px solid #1b5842;border-radius:6px;background:#1b5842;color:white;padding:8px 14px;cursor:pointer;min-height:38px;line-height:1.4;appearance:none}.secondary{background:#fff;color:#1b5842}.error{color:#8b382c}.total-card{font-size:17px}.total-card strong{display:block;font-size:21px;overflow-wrap:anywhere}.notice{margin:9px 0;padding:8px 11px;border-left:3px solid #a07826;background:#fbf5e8}.table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:7px 9px;border-bottom:1px solid #d9e4dd;text-align:left;vertical-align:top;overflow-wrap:anywhere}thead{background:#edf5ef}tbody th{font-weight:600}.positions{min-width:670px}.positions > tbody > tr > td:nth-child(2),.positions > tbody > tr > td:nth-child(3){white-space:pre-line}.different{color:#85511e}.positive{color:#215636}\n@media(max-width:650px){body{padding:7px}main{padding:13px}.offers,.summary-cards{gap:8px}.offer,.total-card{flex-basis:100%}.offer{padding:11px}.field{flex-basis:100%}input,select{width:100%}th,td{padding:6px}.positions,.terms{min-width:0;display:block}.positions > thead,.terms thead{display:none}.positions > tbody,.positions > tbody > tr,.positions > tbody > tr > th,.positions > tbody > tr > td,.terms tbody,.terms tr,.terms th,.terms td{display:block}.positions > tbody > tr,.terms tr{margin-bottom:10px;border:1px solid #d9e4dd;border-radius:6px}.positions > tbody > tr > td,.terms td{border:0;padding-block:4px}.positions > tbody > tr > td::before,.terms td::before{content:attr(data-title);display:block;color:#54675d;font-size:12px;font-weight:650}}\n@media print{body,main{padding:0;background:#fff;border:0}.screen,.offers{display:none!important}.positions{min-width:0;font-size:10pt}thead{display:table-header-group}tr,.total-card{break-inside:avoid}}\n.positions {table-layout:fixed}\n.positions > thead th:nth-child(1) {width:22%}\n.positions > thead th:nth-child(2) {width:25%}\n.positions > thead th:nth-child(3) {width:30%}\n.positions > thead th:nth-child(4) {width:23%}\n.position-catalogue {margin-top:8px;white-space:normal}\n.position-catalogue summary {color:#126348;cursor:pointer;font-size:13px;font-weight:650}\n.catalogue-picker {max-width:100%;display:flex;flex-direction:column;gap:7px;margin-top:6px;padding:9px;border:1px solid #d9e4dd;border-radius:6px;background:#f7faf8}\n.catalogue-field {min-width:0;display:flex;flex-direction:column;gap:3px;color:#395347;font-size:12px;font-weight:650}\n.catalogue-field select,.catalogue-field input {width:100%;min-height:32px;padding:4px 6px;font-size:13px;font-weight:400}\n.catalogue-hint {color:#54675d;font-size:12px}\n.catalogue-link {align-self:flex-start;font-size:13px;font-weight:650;text-underline-offset:2px}\n.catalogue-link[hidden] {display:none}\n/* QUOTE_TECHNICAL_CSS */\n.quote-tech-row > td{padding:9px 12px 13px;background:#f6faf7}\n.quote-tech-summary{color:#126348;cursor:pointer;font-weight:650}\n.quote-tech-row .scroll{overflow-x:auto;margin-top:8px}\n.quote-tech-row .catalog-comparison-table{background:white;table-layout:fixed;min-width:300px}\n.quote-tech-row .source,.quote-tech-source{font-size:13px}\n.quote-tech-source{margin:8px 0 0}\n@media(max-width:650px){.positions > tbody > .quote-tech-row > td{padding:8px}.positions > tbody > .quote-tech-row > td::before{display:none}.quote-tech-row .catalog-comparison-table{font-size:12px}}\n</style></head><body><main>\n<nav class="screen"><a href="?material=all">← К матрице</a></nav>\n<h1>Сравнение КП</h1>\n<p class="screen muted">Вставьте спецификации из Excel или откройте CSV. Столбцы «Материал» и «Номинал, А» могут заполнить поля каталожной карточки.</p>\n<div class="offers"><fieldset class="offer" id="piton-offer"><legend>PitON</legend><div class="fields"><label class="field">Тип оборудования<select data-field="catalog-type"><option value="">Выберите тип</option><option value="shp">Шинопровод</option><option value="tp">Токопровод</option></select></label>\n\n<label class="field wide">Название в отчёте<input data-field="name" value="PitON" maxlength="90"></label>\n<label class="field">Итого по КП<input data-field="total" inputmode="decimal" maxlength="30"></label>\n<label class="field">Валюта<select data-field="currency"><option value="KZT">KZT</option><option value="RUB">RUB</option><option value="USD">USD</option><option value="EUR">EUR</option></select></label>\n<label class="field">База НДС<select data-field="vat"><option value="">Не указана</option><option value="gross">С НДС</option><option value="net">Без НДС</option><option value="not_due">НДС не начисляется</option></select></label>\n<label class="field">Общая длина, м<input data-field="length" inputmode="decimal" maxlength="18"></label>\n<label class="field wide">Таблица позиций<textarea data-field="items" rows="4" maxlength="200000" aria-describedby="piton-offer-format"></textarea></label>\n<p class="muted" id="piton-offer-format">Столбцы: «Наименование», «Ед. изм.», «Количество». Если название отличается, добавьте в обе таблицы одинаковый «Ключ».</p>\n<label class="field wide">Открыть CSV или TSV<input type="file" data-field="file" accept=".csv,.tsv,text/csv,text/tab-separated-values"></label>\n</div><details><summary>Комплектность и условия</summary><div class="fields"><label class="field">Крепления<select data-field="mounts"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Гибкие связи<select data-field="flexible"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Компенсаторы<select data-field="compensators"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Доставка<select data-field="delivery"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Шеф-монтаж<select data-field="supervision"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Срок поставки<input data-field="lead" maxlength="150"></label><label class="field">Оплата<input data-field="payment" maxlength="150"></label><label class="field">Гарантия<input data-field="warranty" maxlength="150"></label></div></details></fieldset>\n<fieldset class="offer" id="competitor-offer"><legend>Конкурент</legend><div class="fields"><label class="field">Тип оборудования<select data-field="catalog-type"><option value="">Выберите тип</option><option value="shp">Шинопровод</option><option value="tp">Токопровод</option></select></label>\n\n<label class="field wide">Название в отчёте<input data-field="name" value="Конкурент" maxlength="90"></label>\n<label class="field">Итого по КП<input data-field="total" inputmode="decimal" maxlength="30"></label>\n<label class="field">Валюта<select data-field="currency"><option value="KZT">KZT</option><option value="RUB">RUB</option><option value="USD">USD</option><option value="EUR">EUR</option></select></label>\n<label class="field">База НДС<select data-field="vat"><option value="">Не указана</option><option value="gross">С НДС</option><option value="net">Без НДС</option><option value="not_due">НДС не начисляется</option></select></label>\n<label class="field">Общая длина, м<input data-field="length" inputmode="decimal" maxlength="18"></label>\n<label class="field wide">Таблица позиций<textarea data-field="items" rows="4" maxlength="200000" aria-describedby="competitor-offer-format"></textarea></label>\n<p class="muted" id="competitor-offer-format">Столбцы: «Наименование», «Ед. изм.», «Количество». Если название отличается, добавьте в обе таблицы одинаковый «Ключ».</p>\n<label class="field wide">Открыть CSV или TSV<input type="file" data-field="file" accept=".csv,.tsv,text/csv,text/tab-separated-values"></label>\n</div><details><summary>Комплектность и условия</summary><div class="fields"><label class="field">Крепления<select data-field="mounts"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Гибкие связи<select data-field="flexible"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Компенсаторы<select data-field="compensators"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Доставка<select data-field="delivery"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Шеф-монтаж<select data-field="supervision"><option value="">Не указано</option><option value="included">В цене</option><option value="extra">Отдельно</option><option value="excluded">Не входит</option><option value="unnecessary">Не требуется</option></select></label><label class="field">Срок поставки<input data-field="lead" maxlength="150"></label><label class="field">Оплата<input data-field="payment" maxlength="150"></label><label class="field">Гарантия<input data-field="warranty" maxlength="150"></label></div></details></fieldset>\n</div>\n<div class="actions screen"><button type="button" id="compare-offers">Сравнить КП</button><button type="button" id="print-offers" class="secondary" hidden>Печать / PDF</button><span id="quote-error" class="error" role="alert"></span></div>\n<section id="comparison-results" aria-live="polite"></section>\n</main><select id="quote-catalogues" hidden></select>\n<div id="quote-technical-templates" hidden></div>\n<script>\n\'use strict\';\n\nconst SCOPE_FIELDS = {\n  mounts: \'Крепления\', flexible: \'Гибкие связи\', compensators: \'Компенсаторы\',\n  delivery: \'Доставка\', supervision: \'Шеф-монтаж\'\n};\nconst SCOPE_LABELS = {included: \'В цене\', extra: \'Отдельно\', excluded: \'Не входит\', unnecessary: \'Не требуется\'};\nconst TAX_LABELS = {gross: \'С НДС\', net: \'Без НДС\', not_due: \'НДС не начисляется\'};\nconst TERMS = {lead: \'Срок поставки\', payment: \'Оплата\', warranty: \'Гарантия\'};\nconst HEADER_ALIASES = {\n  name: [\'наименование\',\'наименование товара\',\'наименование позиции\',\'описание\',\'позиция\',\'description\',\'item\'],\n  unit: [\'ед изм\',\'единица измерения\',\'единица\',\'ед\',\'unit\'],\n  qty: [\'количество\',\'кол во\',\'кол\',\'qty\',\'quantity\'],\n  key: [\'ключ\',\'ключ сопоставления\',\'код сопоставления\',\'match key\'],\n  line: [\'линия\',\'трасса\',\'line\'],\n  catalogue: [\'каталог\',\'название каталога\',\'catalogue\',\'catalog\',\'серия\',\'модель\',\'серия конкурента\',\'series\',\'model\'],\n  material: [\'материал\',\'материал проводника\',\'материал шин\',\'material\'],\n  rating: [\'номинал\',\'номинал а\',\'номинальный ток\',\'номинальный ток а\',\'ток а\',\'rated current\',\'rated current a\'],\n  price: [\'цена\',\'цена за единицу\',\'цена ед\',\'unit price\',\'price\'],\n  amount: [\'итого после скидки\',\'сумма после скидки\',\'итого\',\'сумма\',\'стоимость\',\'сумма позиции\',\'стоимость позиции\',\'стоимость линии\',\'amount\',\'total\']\n};\nconst UNIT_NAMES = new Map([\n  [\'м\',\'м\'],[\'метр\',\'м\'],[\'метров\',\'м\'],[\'пог м\',\'м\'],[\'m\',\'м\'],[\'шт\',\'шт\'],[\'штука\',\'шт\'],[\'pcs\',\'шт\'],\n  [\'компл\',\'компл\'],[\'комплект\',\'компл\'],[\'set\',\'компл\'],[\'кг\',\'кг\'],[\'kg\',\'кг\']\n]);\n\nfunction canonical(value) {\n  return String(value || \'\').trim().normalize(\'NFKC\').toLocaleLowerCase(\'ru\').replace(/ё/g, \'е\').replace(/\\s+/g, \' \');\n}\n\nfunction heading(value) {\n  return canonical(value).replace(/₸|₽|\\b(?:kzt|rub|usd|eur)\\b/g, \' \').replace(/[^\\p{L}\\p{N}]+/gu, \' \').trim();\n}\n\nfunction decimal(raw, scale, label, currency = \'\') {\n  let value = String(raw ?? \'\').normalize(\'NFKC\').trim();\n  if (!value || value === \'—\' || value === \'-\') return null;\n  if (currency) {\n    const suffix = {KZT: /[ ]*(?:₸|KZT|тенге)$/i, RUB: /[ ]*(?:₽|RUB|руб\\.?)$/i,\n      USD: /[ ]*(?:\\$|USD)$/i, EUR: /[ ]*(?:€|EUR)$/i}[currency];\n    value = value.replace(suffix, \'\').trim();\n  }\n  const lastComma = value.lastIndexOf(\',\');\n  const lastDot = value.lastIndexOf(\'.\');\n  if (lastComma !== -1 && lastDot !== -1) {\n    const group = lastComma > lastDot ? \'.\' : \',\';\n    const boundary = Math.max(lastComma, lastDot);\n    const whole = value.slice(0,boundary);\n    const blocks = whole.split(group);\n    if (!/^\\d{1,3}$/.test(blocks[0]) || blocks.slice(1).some(block => !/^\\d{3}$/.test(block))) {\n      throw new Error(`${label} — проверьте разделители числа.`);\n    }\n    value = blocks.join(\'\') + \'.\' + value.slice(boundary + 1);\n  }\n  if (!new RegExp(`^(?:[0-9]+|[0-9]{1,3}(?: [0-9]{3})+)(?:[.,][0-9]{1,${scale}})?$`).test(value)) {\n    throw new Error(`${label} — введите число, например 1234,50.`);\n  }\n  const [whole, fraction = \'\'] = value.replace(/ /g,\'\').replace(\',\',\'.\').split(\'.\');\n  if (whole.length > 15) throw new Error(`${label} — слишком большое число.`);\n  return BigInt(whole + fraction.padEnd(scale,\'0\'));\n}\n\nfunction formatNumber(value, scale, fixed = false) {\n  if (value === null) return \'Не указано\';\n  const unit = 10n ** BigInt(scale);\n  const suffix = (value % unit).toString().padStart(scale, \'0\');\n  const fraction = fixed ? suffix : suffix.replace(/0+$/, \'\');\n  return (value / unit).toLocaleString(\'ru-RU\') + (fraction ? \',\' + fraction : \'\');\n}\n\nfunction parsedRows(source, separator) {\n  const rows = [];\n  let row = [], field = \'\', quoted = false;\n  const pushField = () => { row.push(field.trim()); field = \'\'; };\n  const pushRow = () => { pushField(); rows.push(row); row = []; };\n  const text = source.replace(/^\\uFEFF/, \'\').replace(/\\r\\n?/g, \'\\n\');\n  for (let i = 0; i < text.length; i++) {\n    const char = text[i];\n    if (quoted) {\n      if (char !== \'"\') field += char;\n      else if (text[i + 1] === \'"\') { field += \'"\'; i++; }\n      else quoted = false;\n    } else if (char === \'"\' && !field) quoted = true;\n    else if (char === separator) pushField();\n    else if (char === \'\\n\') pushRow();\n    else field += char;\n  }\n  if (quoted) throw new Error(\'В таблице есть незакрытая кавычка.\');\n  if (field || row.length) pushRow();\n  return rows;\n}\n\nfunction parseItems(source, title, currency) {\n  if (!source.trim()) return [];\n  if (source.length > 200000) throw new Error(`${title} — таблица превышает 200 000 знаков.`);\n  let parsed;\n  for (const separator of [\'\\t\',\';\',\',\']) {\n    let rows;\n    try { rows = parsedRows(source,separator); } catch (error) { continue; }\n    const index = rows.slice(0,25).findIndex(row => {\n      const heads = row.map(heading);\n      return [\'name\',\'unit\',\'qty\'].every(key => HEADER_ALIASES[key].some(alias => heads.includes(alias)));\n    });\n    if (index !== -1) { parsed = {rows,index}; break; }\n  }\n  if (!parsed) throw new Error(`${title} — не найдены столбцы «Наименование», «Ед. изм.» и «Количество».`);\n  const {rows,index} = parsed;\n  if (rows.length - index - 1 > 1500) throw new Error(`${title} — поддерживается до 1500 строк.`);\n  const heads = rows[index].map(heading);\n  const cols = Object.fromEntries(Object.entries(HEADER_ALIASES).map(([key,aliases]) =>\n    [key, aliases.map(alias => heads.indexOf(alias)).find(index => index >= 0) ?? -1]));\n  const items = [];\n  rows.slice(index+1).forEach((row, offset) => {\n    if (!row.some(Boolean)) return;\n    const cell = key => row[cols[key]] || \'\';\n    const name = cell(\'name\');\n    if (row.map(heading).join(\'|\') === heads.join(\'|\') ||\n        /^(итого|всего|ндс)(\\b|\\s|:|$)/iu.test(heading(name))) return;\n    const at = `${title}, строка ${index + offset + 2}`;\n    const unit = canonical(cell(\'unit\'));\n    if (!name || !unit) throw new Error(`${at} — заполните наименование и единицу измерения.`);\n    const qty = decimal(cell(\'qty\'),3,at+\' количество\');\n    if (qty === null || qty <= 0n) throw new Error(`${at} — количество должно быть положительным.`);\n    const price = decimal(cell(\'price\'),2,at+\' цена\',currency);\n    const stated = decimal(cell(\'amount\'),2,at+\' сумма\',currency);\n    const amount = stated !== null ? stated : price === null ? null : (price * qty + 500n) / 1000n;\n    const explicit = canonical(cell(\'key\'));\n    const line = cell(\'line\');\n    const key = explicit ? \'key:\' + explicit : \'auto:\' + JSON.stringify([canonical(line),canonical(name)]);\n    items.push({\n      key,name,line,unit:UNIT_NAMES.get(heading(unit)) || unit,qty,price,amount,\n      catalogue:cell(\'catalogue\'),material:cell(\'material\'),rating:cell(\'rating\')\n    });\n  });\n  return items;\n}\n\nfunction group(items) {\n  const result = new Map();\n  for (const item of items) {\n    if (!result.has(item.key)) result.set(item.key,[]);\n    result.get(item.key).push(item);\n  }\n  return result;\n}\n\nfunction alignItems(left, right) {\n  const a = group(left), b = group(right);\n  return [...new Set([...a.keys(),...b.keys()])].map(key => {\n    const l = a.get(key) || [], r = b.get(key) || [];\n    if (l.length > 1 || r.length > 1) return {l,r,state:\'Дубли ключа · сверить\',different:true};\n    if (!l.length || !r.length) return {l,r,state:l.length?\'Только в КП PitON\':\'Только в КП конкурента\',different:true};\n    if (l[0].unit !== r[0].unit) return {l,r,state:\'Разные единицы измерения\',different:true};\n    if (l[0].qty !== r[0].qty) return {l,r,state:\'Разное количество\',different:true};\n    return {l,r,state:\'Объём совпадает\',different:false};\n  });\n}\n\nfunction readOffer(id, title) {\n  const input = key => String(document.querySelector(`#${id} [data-field="${key}"]`).value ?? \'\').trim();\n  const name = input(\'name\') || title;\n  const currency = input(\'currency\');\n  return {\n    name,catalogType:input(\'catalog-type\'),currency,vat:input(\'vat\'),total:decimal(input(\'total\'),2,`${title}, итог`,currency),\n    length:decimal(input(\'length\'),3,`${title}, длина`),items:parseItems(input(\'items\'),title,currency),\n    scopes:Object.fromEntries(Object.keys(SCOPE_FIELDS).map(key => [key,input(key)])),\n    terms:Object.fromEntries(Object.keys(TERMS).map(key => [key,input(key)]))\n  };\n}\n\nfunction element(tag, text = \'\', css = \'\') {\n  const item = document.createElement(tag);\n  item.textContent = text;\n  if (css) item.className = css;\n  return item;\n}\n\nconst CATALOG_MATERIALS = new Map([\n  [\'al\',\'Al\'], [\'ал\',\'Al\'], [\'алюминий\',\'Al\'], [\'aluminium\',\'Al\'], [\'aluminum\',\'Al\'],\n  [\'cu\',\'Cu\'], [\'медь\',\'Cu\'], [\'copper\',\'Cu\']\n]);\n\nfunction isLegrandRcp(value) {\n  return /^(?:legrand )?rcp[-‐‑–— ]?4c(?: \\(3l\\+n\\))?$/i.test(canonical(value));\n}\n\nfunction ratingFrom(value) {\n  const raw = canonical(value).replace(/ ?[aа]$/, \'\');\n  if (!/^(?:[1-9][0-9]{0,5}|[1-9][0-9]{0,2}(?: [0-9]{3})+)$/.test(raw)) return \'\';\n  const digits = raw.replace(/ /g, \'\');\n  return digits.length <= 6 ? digits : \'\';\n}\n\nfunction selectField(label, options, selected) {\n  const select = element(\'select\');\n  select.setAttribute(\'data-catalogue-field\', label);\n  for (const [value, text] of options) {\n    const option = element(\'option\', text);\n    option.value = value;\n    if (value === selected) option.setAttribute(\'selected\', \'\');\n    select.append(option);\n  }\n  return select;\n}\n\nfunction catalogueField(label, input) {\n  const field = element(\'label\', label, \'catalogue-field\');\n  field.append(input);\n  return field;\n}\n\nfunction positionCatalogue(item, type) {\n  const details = element(\'details\', \'\', \'position-catalogue screen\');\n  details.append(element(\'summary\', \'Каталожная карточка\'));\n  details.addEventListener(\'toggle\', () => {\n    if (!details.hasAttribute(\'open\') || details.querySelector(\'.catalogue-picker\')) return;\n    const picker = element(\'div\', \'\', \'catalogue-picker\');\n    const options = [...document.getElementById(\'quote-catalogues\').options]\n      .filter(option => option.dataset.type === type);\n    if (!options.length) {\n      picker.append(element(\'p\', \'В библиотеке пока нет каталогов этого типа.\', \'catalogue-hint\'));\n      details.append(picker);\n      return;\n    }\n\n    const references = [item.catalogue, item.name].map(canonical).filter(Boolean);\n    const matching = options.map((option, index) => {\n      const reference = canonical(option.dataset.reference);\n      const aliases = [canonical(option.textContent), reference].filter(Boolean);\n      return references.some(value => aliases.includes(value) || isLegrandRcp(value) && isLegrandRcp(reference))\n        ? String(index) : null;\n    }).filter(index => index !== null);\n    const catalogue = selectField(\'catalogue\', [\n      [\'\', \'Выберите каталог\'], ...options.map((option, index) => [String(index), option.textContent])\n    ], matching.length === 1 ? matching[0] : \'\');\n    const material = selectField(\'material\', [\n      [\'\', \'Выбрать\'], [\'Al\',\'Алюминий (Al)\'], [\'Cu\',\'Медь (Cu)\']\n    ], CATALOG_MATERIALS.get(canonical(item.material)) || \'\');\n    const rating = element(\'input\');\n    rating.type = \'text\';\n    rating.inputMode = \'numeric\';\n    rating.maxLength = 14;\n    rating.placeholder = \'1 600\';\n    rating.value = ratingFrom(item.rating);\n    rating.setAttribute(\'data-catalogue-field\',\'rating\');\n    const link = element(\'a\', \'Открыть карточку ↗\', \'catalogue-link\');\n    link.target = \'_blank\';\n    link.rel = \'noopener noreferrer\';\n    picker.append(catalogueField(\'Каталог конкурента\', catalogue), catalogueField(\'Материал\', material),\n      catalogueField(\'Номинал, А\', rating), link);\n    details.append(picker);\n\n    const update = () => {\n      const amps = ratingFrom(rating.value);\n      const selected = catalogue.value === \'\' ? null : options[Number(catalogue.value)];\n      link.hidden = !selected || ![\'Al\',\'Cu\'].includes(material.value) || !amps;\n      if (link.hidden) {\n        link.removeAttribute(\'href\');\n        return;\n      }\n      const params = new URLSearchParams({view:\'catalog\',variant:material.value + \'-\' + amps});\n      if (selected.value) params.set(\'document\', selected.value);\n      link.setAttribute(\'href\', \'?\' + params);\n    };\n    catalogue.addEventListener(\'change\', update);\n    material.addEventListener(\'change\', update);\n    rating.addEventListener(\'input\', update);\n    update();\n  });\n  return details;\n}\n\nfunction positionTechnical(left, right, catalogType) {\n  if (catalogType !== \'shp\' || left.length !== 1 || right.length !== 1) return null;\n  const [piton] = left;\n  const [competitor] = right;\n  if (!/^(?:piton(?: electric)? )?e3$/.test(canonical(piton.catalogue || piton.name)) ||\n      !/^(?:graziadio )?isolsbarra(?: 630[-‑–— ]+6300(?: [aа])?)?$/.test(canonical(competitor.catalogue || competitor.name))) return null;\n  const material = CATALOG_MATERIALS.get(canonical(piton.material));\n  const amps = ratingFrom(piton.rating);\n  if (!material || !amps || material !== CATALOG_MATERIALS.get(canonical(competitor.material)) ||\n      amps !== ratingFrom(competitor.rating)) return null;\n  return document.getElementById(\'quote-tech-\' + material + \'-\' + amps);\n}\n\nfunction reportTable(parent, title, headers, rows, css) {\n  if (!rows.length) return;\n  const table = element(\'table\',\'\',css);\n  const thead = element(\'thead\'), head = element(\'tr\'), body = element(\'tbody\');\n  for (const name of headers) { const cell = element(\'th\',name); cell.scope=\'col\'; head.append(cell); }\n  thead.append(head); table.append(thead);\n  for (const {cells,different,competitorItem,catalogType,technical} of rows) {\n    const row = element(\'tr\');\n    cells.forEach((value,index) => {\n      const cell = element(index===0?\'th\':\'td\',value,different && index===cells.length-1?\'different\':\'\');\n      if (index===0) cell.scope=\'row\';\n      else cell.dataset.title = headers[index];\n      if (index === 2 && competitorItem) cell.append(positionCatalogue(competitorItem,catalogType));\n      row.append(cell);\n    });\n    body.append(row);\n    if (technical) {\n      const technicalRow = element(\'tr\',\'\',\'quote-tech-row\');\n      const cell = element(\'td\');\n      cell.setAttribute(\'colspan\', String(headers.length));\n      const details = element(\'details\');\n      details.append(element(\'summary\',\'Техническое сравнение\',\'quote-tech-summary\'),\n        technical.content.cloneNode(true));\n      cell.append(details);\n      technicalRow.append(cell);\n      body.append(technicalRow);\n    }\n  }\n  table.append(body);\n  const scroll = element(\'div\',\'\',\'table-scroll\');\n  scroll.append(table);\n  parent.append(element(\'h2\',title),scroll);\n}\n\nfunction offerNumber(value, currency) {\n  return value === null ? \'Не указано\' : formatNumber(value,2,true)+\' \'+currency;\n}\n\nfunction positionText(items,currency) {\n  if (!items.length) return \'—\';\n  if (items.length > 1) return `${items.length} строк: ${items.map(item=>item.name).join(\', \')}`;\n  const item = items[0];\n  const quantity = formatNumber(item.qty,3)+\' \'+item.unit;\n  const price = item.price === null ? \'\' : \'Цена за ед. \'+offerNumber(item.price,currency);\n  const amount = item.amount === null ? \'\' : \'Сумма \'+offerNumber(item.amount,currency);\n  return [item.name,item.line,quantity,price,amount].filter(Boolean).join(\'\\n\');\n}\n\nfunction renderComparison(left,right) {\n  const root = element(\'div\');\n  const summary = element(\'div\',\'\',\'summary-cards\');\n  for (const offer of [left,right]) {\n    const tile = element(\'div\',offer.name,\'total-card\');\n    tile.append(element(\'strong\',offerNumber(offer.total,offer.currency)),element(\'p\',TAX_LABELS[offer.vat] || \'База НДС не указана\',\'muted\'));\n    summary.append(tile);\n  }\n  root.append(element(\'p\',left.catalogType === \'shp\' ? \'Шинопроводы\' : \'Токопроводы\',\'muted\'),summary);\n  const sameBasis = left.currency && left.currency === right.currency && left.vat && left.vat === right.vat;\n  if (left.total !== null && right.total !== null) {\n    if (sameBasis) {\n      const delta = right.total-left.total;\n      const absolute = delta < 0n ? -delta : delta;\n      const percent = right.total>0n ? formatNumber((absolute*1000n+right.total/2n)/right.total,1,true) : null;\n      let amount = \'Заявленные итоги совпадают.\';\n      if (delta) amount = `Заявленный итог PitON ${delta>0n?\'ниже\':\'выше\'} на ${offerNumber(absolute,left.currency)}${percent === null ? \'\' : ` (${percent}% от итога конкурента)`}.`;\n      root.append(element(\'p\',amount,\'notice\'));\n    } else {\n      root.append(element(\'p\',\'Для расчёта разницы укажите одинаковую валюту и базу НДС.\',\'notice\'));\n    }\n  }\n  const conditions = [];\n  const add = (name,a,b,agreed) => conditions.push({cells:[name,a,b,agreed?\'Совпадает\':a===\'Не указано\'||b===\'Не указано\'?\'Уточнить\':\'Различается\'],different:!agreed});\n  if (left.length !== null || right.length !== null) {\n    const show = length => length === null ? \'Не указано\' : formatNumber(length,3)+\' м\';\n    add(\'Общая длина\',show(left.length),show(right.length),left.length === right.length);\n  }\n  for (const [key,title] of Object.entries(SCOPE_FIELDS)) {\n    const a = left.scopes[key], b = right.scopes[key];\n    add(title,SCOPE_LABELS[a]||\'Не указано\',SCOPE_LABELS[b]||\'Не указано\',Boolean(a && a===b));\n  }\n  for (const [key,title] of Object.entries(TERMS)) {\n    const a = left.terms[key], b = right.terms[key];\n    if (a || b) add(title,a||\'Не указано\',b||\'Не указано\',Boolean(a && b && canonical(a)===canonical(b)));\n  }\n  const aligned = alignItems(left.items,right.items);\n  if (conditions.some(row=>row.different) || !aligned.length || aligned.some(row=>row.different)) {\n    root.append(element(\'p\',\'Проверьте отмеченные расхождения и пропуски в объёме, комплектности и условиях.\',\'notice\'));\n  }\n  reportTable(root,\'Комплектность и условия\',[\'Параметр\',left.name,right.name,\'Сравнение\'],conditions,\'terms\');\n  const positions = aligned.map(row => {\n    let state = row.state;\n    const leftAmount = row.l[0]?.amount, rightAmount = row.r[0]?.amount;\n    if (!row.different && sameBasis && leftAmount != null && rightAmount != null) {\n      const delta = rightAmount - leftAmount;\n      if (delta) state += ` · PitON ${delta > 0n ? \'ниже\' : \'выше\'} на ${offerNumber(delta > 0n ? delta : -delta,left.currency)}`;\n    }\n    return {\n      cells:[(row.l[0] || row.r[0]).name,positionText(row.l,left.currency),positionText(row.r,right.currency),state],\n      different:row.different,competitorItem:row.r.length === 1 ? row.r[0] : null,catalogType:right.catalogType,\n      technical:positionTechnical(row.l,row.r,right.catalogType)\n    };\n  });\n  reportTable(root,\'Позиции\',[\'Позиция\',left.name,right.name,\'Результат\'],positions,\'positions\');\n  return root;\n}\n\nfunction compareOffers() {\n  const output = document.getElementById(\'comparison-results\');\n  const error = document.getElementById(\'quote-error\');\n  const print = document.getElementById(\'print-offers\');\n  output.replaceChildren();\n  error.textContent = \'\';\n  print.hidden = true;\n  try {\n    const left = readOffer(\'piton-offer\',\'PitON\'), right = readOffer(\'competitor-offer\',\'Конкурент\');\n    if (![left.catalogType,right.catalogType].every(type=>[\'shp\',\'tp\'].includes(type))) {\n      throw new Error(\'Выберите тип оборудования в обоих КП.\');\n    }\n    if (left.catalogType !== right.catalogType) {\n      throw new Error(\'Сравнение доступно для двух КП одного типа.\');\n    }\n    for (const offer of [left,right]) {\n      for (const item of offer.items) {\n        const name = canonical(item.name), model = canonical(item.catalogue);\n        const rcp = isLegrandRcp(model) || isLegrandRcp(name) ||\n          /(?:^|[^a-z0-9])legrand rcp[-‑–— ]?4c(?:$|[^a-z0-9])/i.test(name);\n        const e3 = /^(?:piton )?e3$/.test(model) || /(?:^|[^a-z0-9])piton e3(?:$|[^a-z0-9])/i.test(name);\n        if ((rcp && offer.catalogType !== \'tp\') || (e3 && offer.catalogType !== \'shp\')) {\n          throw new Error(offer.name+\' — выбранный тип противоречит оборудованию в спецификации.\');\n        }\n      }\n      if (offer.total === null && !offer.items.length) throw new Error(`${offer.name} — укажите итог или вставьте таблицу позиций.`);\n    }\n    output.append(renderComparison(left,right));\n    print.hidden = false;\n  } catch (problem) {\n    error.textContent = problem.message;\n  }\n}\n\ndocument.getElementById(\'compare-offers\').addEventListener(\'click\',compareOffers);\ndocument.getElementById(\'print-offers\').addEventListener(\'click\',()=>window.print());\nfor (const id of [\'piton-offer\',\'competitor-offer\']) {\n  const panel = document.getElementById(id);\n  panel.querySelector(\'[data-field="file"]\').addEventListener(\'change\',async event => {\n    const file = event.target.files[0];\n    if (!file) return;\n    const report = document.getElementById(\'quote-error\');\n    report.textContent = \'\';\n    try {\n      if (!/\\.(csv|tsv)$/i.test(file.name) || file.size > 300000) throw new Error(\'Откройте CSV или TSV размером до 300 КБ.\');\n      const bytes = await file.arrayBuffer();\n      let value;\n      try { value = new TextDecoder(\'utf-8\',{fatal:true}).decode(bytes); }\n      catch (error) { value = new TextDecoder(\'windows-1251\').decode(bytes); }\n      if (value.length > 200000 || value.includes(\'\\0\')) throw new Error(\'Таблица слишком большая или файл не в текстовом формате.\');\n      panel.querySelector(\'[data-field="items"]\').value = value;\n    } catch (error) {\n      report.textContent = error.message;\n    } finally {\n      event.target.value = \'\';\n    }\n  });\n}\n\n</script></body></html>\n'


def matrix_view(data, params, base_page):
    view = (params.get("view") or [""])[0]
    if view == "quote-comparison":
        return 200, QUOTE_COMPARISON_HTML
    if view != "card":
        page = annotate_matrix(base_page(data), data["rows"])
        button = '<p><a class="download" id="offer-comparison-entry" href="?view=quote-comparison">Сравнение КП</a></p>'
        return 200, page.replace("</h1>", "</h1>" + button, 1)
    material, raw_rating = (params.get("material") or [""])[0], (params.get("rating") or [""])[0]
    if material not in MATERIALS or not re.fullmatch(r"[1-9][0-9]{0,5}", raw_rating):
        return error_page(400, "Выберите материал и номинал в матрице.")
    rating = int(raw_rating)
    for row in data["rows"]:
        if row["material"] == material and row["rating_a"] == rating and paired(row):
            return 200, card(row)
    return error_page(404, "Для этого номинала нет пары для сравнения.")


def error_page(status, message):
    return status, (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<title>Карточка сравнения</title>'
        f'<h1>{message}</h1><p><a href="?material=all">К матрице</a></p></html>'
    )


def fetch(path):
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(BASE_URL + path, timeout=5) as response:
            return response.status, response.read(5_000_000)
    except HTTPError as error:
        return error.code, error.read(100_000)


def fetch_ok(path):
    status, body = fetch(path)
    if status != 200:
        raise RuntimeError(f"{path}: ожидался HTTP 200, получен {status}.")
    return body


def baseline():
    body = fetch_ok("/api/matrix-v7.json?material=Al")
    data = json.loads(body)
    rows = [row for row in data["rows"] if row["material"] == "Al"]
    if not any(row["rating_a"] == 1600 and paired(row) for row in rows):
        raise RuntimeError("В рабочей матрице отсутствует контрольная пара Al 1600 А.")
    document = fetch_ok("/matrix-v7?material=Al").decode("utf-8")
    if len(SUMMARY.findall(document)) != len(rows):
        raise RuntimeError("Разметка рабочей матрицы отличается от версии v7.")
    if not fetch_ok("/api/matrix-v7.csv?material=Al").startswith(b"\xef\xbb\xbf"):
        raise RuntimeError("Формат CSV рабочей матрицы отличается от версии v7.")
    return data


def verify(data):
    status, page = fetch("/matrix-v7?material=Al")
    if status != 200:
        raise RuntimeError(f"Матрица после установки вернула HTTP {status}.")
    table = page.decode("utf-8")
    if table.count("Карточка сравнения") != sum(paired(row) for row in data["rows"]):
        raise RuntimeError("Ссылки на карточки присутствуют не у всех сопоставимых строк.")
    text = fetch_ok("/matrix-v7?material=Al&rating=1600&view=card").decode("utf-8")
    if (
        "<title>Карточка сравнения" not in text
        or "Источники данных" not in text or "PDF, стр. 60" not in text
    ):
        raise RuntimeError("Карточка Al 1600 А не прошла проверку.")
    if fetch("/matrix-v7?material=Al&rating=1&view=card")[0] != 404:
        raise RuntimeError("Запрос отсутствующего номинала должен возвращать HTTP 404.")
    if fetch("/matrix-v7?material=Al&rating=x&view=card")[0] != 400:
        raise RuntimeError("Запрос некорректного номинала должен возвращать HTTP 400.")
    if json.loads(fetch_ok("/api/matrix-v7.json?material=Al")) != data:
        raise RuntimeError("Выгрузка JSON отличается от проверенной перед установкой.")
    if not fetch_ok("/api/matrix-v7.csv?material=Al").startswith(b"\xef\xbb\xbf"):
        raise RuntimeError("Выгрузка CSV не прошла проверку.")


def restart():
    subprocess.run(["systemctl", "restart", SERVICE], check=True, timeout=35)
    for _ in range(10):
        try:
            fetch_ok("/api/health")
            return
        except (OSError, RuntimeError):
            time.sleep(0.5)
    raise RuntimeError("Сервис каталога не ответил после перезапуска.")


def write_atomic(path, content):
    metadata = WEB.stat()
    with tempfile.NamedTemporaryFile(dir=ROOT, prefix=".matrix-card-", delete=False) as temp:
        temporary = Path(temp.name)
        try:
            temp.write(content)
            temp.flush()
            os.fchown(temp.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(temp.fileno(), metadata.st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def backup():
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = BACKUPS / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    folder.mkdir(mode=0o700)
    shutil.copy2(WEB, folder / WEB.name)
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "apply", "rollback"))
    action = parser.parse_args().action
    if os.geteuid() != 0:
        raise RuntimeError("Запустите через sudo.")
    with open("/run/piton-catalog-comparison-card-v8.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not WEB.is_file() or WEB.is_symlink() or MODULE.is_symlink():
            raise RuntimeError("Рабочие файлы отличаются от ожидаемых путей.")
        original = WEB.read_bytes()
        current = original.decode("utf-8")
        source = Path(__file__).read_bytes()
        if action == "rollback":
            if current.count(V8_ROUTE) != 1 or not MODULE.is_file() or MODULE.read_bytes() != source:
                raise RuntimeError("Версия установленной карточки отличается от этого обновления.")
            restored = current.replace(V8_ROUTE, V7_ROUTE, 1)
            compile(restored, str(WEB), "exec")
            backup()
            write_atomic(WEB, restored.encode("utf-8"))
            try:
                restart()
            except BaseException:
                write_atomic(WEB, original)
                restart()
                raise
            MODULE.unlink()
            print("Карточка убрана. Матрица v7 и API работают.")
            return

        if current.count(V8_ROUTE) == 1 and MODULE.is_file() and MODULE.read_bytes() == source:
            verify(baseline())
            print("Карточка уже установлена и проверена.")
            return
        if current.count(V7_ROUTE) != 1 or MODULE.exists():
            raise RuntimeError("Рабочая версия web.py или имя модуля отличаются от ожидаемых.")
        updated = current.replace(V7_ROUTE, V8_ROUTE, 1)
        compile(updated, str(WEB), "exec")
        compile(source, str(MODULE), "exec")
        data = baseline()
        if action == "check":
            print("Проверка пройдена. Рабочая матрица совместима с карточкой.")
            return

        saved = backup()
        if WEB.read_bytes() != original:
            raise RuntimeError("Рабочий web.py изменился во время проверки.")
        write_atomic(MODULE, source)
        try:
            write_atomic(WEB, updated.encode("utf-8"))
            restart()
            verify(data)
        except BaseException:
            write_atomic(WEB, original)
            MODULE.unlink(missing_ok=True)
            restart()
            raise
        print("Готово. Карточка Al 1600 А, ссылки, фильтр и выгрузки проверены.")
        print(f"Резервная копия сохранена в {saved}")
        print("https://ai.reforma.kz/catalog/?material=Al&rating=1600&view=card")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"Ошибка: {error}")
