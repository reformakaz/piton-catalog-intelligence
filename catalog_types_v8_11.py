"""Classify catalogue documents and enforce like-for-like comparison."""
from contextlib import closing
from html import escape
import json
from pathlib import Path
import re
import sqlite3
from unicodedata import normalize
from urllib.parse import urlsplit

import catalog_series_v8_9 as series
from catalog_engine import COMPARISON_PRODUCTS, GRAZIADIO_PRODUCT, PITON_PRODUCTS
import matrix_comparison_card_v8 as card
import matrix_v7
import catalog_physical_v8_11_9 as physical
import catalog_phase_conductor_v8_11_10 as phase

LABELS = {"shp": "Шинопровод", "tp": "Токопровод"}
PITON = ("piton", PITON_PRODUCTS[0])
GRAZIADIO = ("graziadio", GRAZIADIO_PRODUCT)
LEGRAND = ("legrand", series.PRODUCT)
FIXED = {PITON: "shp", LEGRAND: "tp"}
PDF_IDENTITIES = {
    "05db1cd3654a50f498974428a06fbcd61024a0a41d1d8fa59b340fb566ff398f": PITON,
    "b8a50e38a4b7c7a1b463d32cf5527e460c23faf7b2ab2b5f3dd840f894e657cc": GRAZIADIO,
    "1c8745f59d3f815bbe1629dd35b4d39ddd4020ddfa2201e3859699e71f1cc17c": GRAZIADIO,
}
TITLE_IDENTITIES = {
    "piton e3": {PITON},
    "piton e 3": {PITON},
    "graziadio isolsbarra": {GRAZIADIO},
    "isolsbarra": {GRAZIADIO},
    "legrand rcp 4c": {LEGRAND},
    "legrand rcp 4c 3l n": {LEGRAND},
    "rcp 4c": {LEGRAND},
    "rcp 4c 3l n": {LEGRAND},
}
TABLE = "catalog_document_types"
MATRIX_PATHS = {"/matrix-v7", "/api/matrix-v7.json", "/api/matrix-v7.csv"}


def db_connect(db, writable=False):
    uri = Path(db).resolve().as_uri() + ("?mode=rw" if writable else "?mode=ro")
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def catalogue_rows(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
        return []
    return [dict(row) for row in conn.execute(
        "SELECT t.document_id, t.vendor, t.product, t.catalog_type FROM catalog_document_types t "
        "JOIN documents d ON CAST(d.id AS TEXT) = t.document_id ORDER BY t.document_id"
    )]


def product_type(db, identity):
    if identity in FIXED:
        return FIXED[identity]
    with closing(db_connect(db)) as conn:
        types = {
            row["catalog_type"] for row in library_catalogues(conn)
            if (row["spec_vendor"], row["spec_product"]) == identity and row["catalog_type"]
        }
        return next(iter(types)) if len(types) == 1 else None


def title_key(title):
    return " ".join(re.findall(r"[^\W_]+", normalize("NFKC", title or "").casefold()))


def catalogue_titles(conn):
    titles = {name: set(identities) for name, identities in TITLE_IDENTITIES.items()}
    for vendor, product in conn.execute("SELECT DISTINCT vendor,product FROM specs"):
        if vendor and product:
            titles.setdefault(title_key(product), set()).add((vendor, product))
    for vendor, product in {(key[0], key[1]) for key in phase.stored_rows(conn)}:
        titles.setdefault(title_key(product), set()).add((vendor, product))
    return titles


def title_identity(titles, title, vendor=""):
    if is_isolsbarra_range(title, vendor):
        return GRAZIADIO
    matches = [pair for pair in titles.get(title_key(title), ()) if not vendor or pair[0] == vendor]
    return matches[0] if len(matches) == 1 else None


def is_isolsbarra_range(title, vendor):
    return (
        vendor in {"", "competitor", "graziadio"}
        and re.fullmatch(r"(?:graziadio )?isolsbarra 630 6300(?: ?[aа])?", title_key(title)) is not None
    )


def library_catalogues(conn):
    stored = {row["document_id"]: row for row in catalogue_rows(conn)}
    pinned = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalogue_pdf_links'").fetchone():
        pinned = {row[0]: (row[1], row[2]) for row in conn.execute(
            "SELECT document_id,vendor,product FROM catalogue_pdf_links"
        )}
    titles = catalogue_titles(conn)
    result = []
    for doc in conn.execute("SELECT id,title,sha256 FROM documents ORDER BY created_at DESC"):
        docid = str(doc["id"])
        metadata = stored.get(docid, {})
        vendor = metadata.get("vendor") or ""
        product = metadata.get("product") or ""
        identity = PDF_IDENTITIES.get(doc["sha256"])
        if not identity and is_isolsbarra_range(doc["title"], vendor):
            identity = GRAZIADIO
        fallback = pinned.get(docid)
        if not identity and fallback and vendor in {"", "competitor", fallback[0]}:
            if not product or (vendor, product) not in titles.get(title_key(product), ()):
                identity = fallback
        if not identity and vendor and product:
            identity = (vendor, product)
        if not identity:
            identity = title_identity(titles, doc["title"], vendor)
        spec_vendor, spec_product = identity or ("", "")
        catalog_type = metadata.get("catalog_type") or ""
        expected_type = FIXED.get(identity, catalog_type)
        comparison_type = expected_type if not catalog_type or catalog_type == expected_type else ""
        result.append({"document_id": docid, "title": doc["title"] or "", "vendor": vendor,
                       "catalog_type": catalog_type, "comparison_type": comparison_type,
                       "spec_vendor": spec_vendor, "spec_product": spec_product})
    return result


def classification(vendor, product, catalog_type):
    if catalog_type not in LABELS:
        raise ValueError("Укажите тип каталога.")
    if product and not vendor:
        raise ValueError("Укажите код производителя для каталога.")
    if (catalog_type == "shp" and (vendor, product) == LEGRAND
            or catalog_type == "tp" and (vendor, product) == PITON):
        raise ValueError("Выбранный тип противоречит типу каталога.")
    if len(vendor) > 100 or len(product) > 200:
        raise ValueError("Проверьте длину кода производителя и названия каталога.")
    return vendor, product, catalog_type


def record_classification(conn, document_id, vendor, product, catalog_type):
    """Write through the caller's transaction, alongside the PDF row during upload."""
    doc = conn.execute("SELECT sha256 FROM documents WHERE id=?", (document_id,)).fetchone()
    verified = PDF_IDENTITIES.get(doc[0]) if doc else None
    if verified in FIXED:
        classification(*verified, catalog_type)
    conn.execute("""CREATE TABLE IF NOT EXISTS catalog_document_types (
        document_id TEXT PRIMARY KEY,
        vendor TEXT NOT NULL,
        product TEXT NOT NULL,
        catalog_type TEXT NOT NULL CHECK(catalog_type IN ('shp','tp'))
    )""")
    if product:
        conflicting = conn.execute(
            "SELECT 1 FROM catalog_document_types t JOIN documents d ON CAST(d.id AS TEXT)=t.document_id "
            "WHERE t.vendor=? AND t.product=? AND t.catalog_type<>? AND t.document_id<>?",
            (vendor, product, catalog_type, document_id),
        ).fetchone()
        if conflicting:
            raise ValueError("Для этого каталога уже указан другой тип. Сверьте связанные PDF.")
    conn.execute(
        "INSERT INTO catalog_document_types(document_id,vendor,product,catalog_type) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(document_id) DO UPDATE SET vendor=excluded.vendor, product=excluded.product, "
        "catalog_type=excluded.catalog_type",
        (document_id, vendor, product, catalog_type),
    )


def send_json(handler, status, data):
    handler.send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")


def error(handler, message, code=400):
    handler.close_connection = True
    send_json(handler, code, {"ok": False, "error": message})


def param(handler, key):
    values = handler.params().get(key) or []
    return str(values[0]).strip() if len(values) == 1 else ""


def upload_fields(handler, vendor):
    identity = title_identity(TITLE_IDENTITIES, param(handler, "title"), vendor)
    product = param(handler, "product") if "product" in handler.params() else (
        identity[1] if identity and identity[0] == vendor else ""
    )
    return classification(vendor, product, param(handler, "catalog_type"))


def set_existing_type(handler, db):
    document_id = param(handler, "document")
    if not document_id or len(document_id) > 255:
        raise ValueError("Выберите PDF из библиотеки.")
    title = param(handler, "title") if "title" in handler.params() else None
    if title is not None and not 1 <= len(title) <= 140:
        raise ValueError("Название каталога должно содержать от 1 до 140 символов.")
    params = handler.params()
    with closing(db_connect(db, writable=True)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        current = next((row for row in library_catalogues(conn) if row["document_id"] == document_id), None)
        if current is None:
            raise ValueError("PDF с таким идентификатором отсутствует в библиотеке.")
        old_pair = (current["spec_vendor"], current["spec_product"])
        retained = old_pair
        if "catalog_type" in params or "vendor" in params or "product" in params or title is None:
            vendor = param(handler, "vendor").lower() if "vendor" in params else current["vendor"]
            catalog_type = param(handler, "catalog_type") if "catalog_type" in params else current["catalog_type"]
            target = old_pair if vendor == old_pair[0] and old_pair[1] else title_identity(
                catalogue_titles(conn), title or current["title"], vendor
            )
            product = param(handler, "product") if "product" in params else (
                target[1] if target and vendor == target[0] else ""
            )
            validation_pair = target if target in FIXED else (vendor, product)
            classification(*validation_pair, catalog_type)
            record_classification(conn, document_id, *classification(vendor, product, catalog_type))
            if product:
                retained = (vendor, product)
        if title is not None:
            if retained[1]:
                conn.execute("""CREATE TABLE IF NOT EXISTS catalogue_pdf_links (
                    document_id TEXT PRIMARY KEY, vendor TEXT NOT NULL, product TEXT NOT NULL
                )""")
                conn.execute("INSERT INTO catalogue_pdf_links VALUES (?,?,?) "
                             "ON CONFLICT(document_id) DO UPDATE SET vendor=excluded.vendor, product=excluded.product",
                             (document_id, *retained))
            conn.execute("UPDATE documents SET title=? WHERE id=?", (title, document_id))
    send_json(handler, 200, {"ok": True})


def legacy_pair(db):
    return product_type(db, PITON) == product_type(db, GRAZIADIO) == "shp"


def report_pair(db):
    if not legacy_pair(db):
        return False
    with closing(db_connect(db)) as conn:
        products = {(row[0], row[1]) for row in conn.execute(
            "SELECT DISTINCT vendor,product FROM specs WHERE vendor IN ('piton','graziadio')"
        )}
    return products <= COMPARISON_PRODUCTS


def page(handler, status, title, body, css=""):
    markup = (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{escape(title)}</title><style>{card.CSS}{css}</style><body><main>{body}</main></body></html>'
    )
    handler.send(status, markup.encode("utf-8"), "text/html; charset=utf-8")


def legrand_page(handler, db):
    variant = series.VARIANT.fullmatch(param(handler, "variant") or "Al-1600")
    if not variant:
        page(handler, 400, "Каталог Legrand", '<p>Укажите материал Al или Cu и целый номинал.</p>')
        return
    key = (variant[1], int(variant[2]))
    available = {}
    for combination, entries in series.records(db).items():
        legrand = {code: record for (vendor, code), record in entries.items() if vendor == "legrand"}
        if legrand:
            available[combination] = legrand
    options = "".join(
        f'<option value="{metal}-{amps}"{" selected" if (metal, amps) == key else ""}>{series.variant_name(metal, amps)}</option>'
        for metal, amps in sorted(available.keys() | {key})
    )
    entries = available.get(key, {})
    rows = "".join(
        f'<tr><th scope="row">{escape(card.LABELS[code])}</th><td>{card.display_value(entries.get(code))}'
        f'{series.source_markup(entries.get(code), "legrand")}</td></tr>'
        for code in matrix_v7.PARAMS
    )
    note = ('<p class="notice">Звёздочкой отмечены значения, которые требуют сверки.</p>'
            if any(card.needs_review(record) for record in entries.values()) else "")
    if not entries:
        note = '<p class="notice">В этом каталоге нет импортированных характеристик для выбранного исполнения.</p>'
    page(handler, 200, series.TITLE, f"""
<nav><a href="?material=all">← К каталогам</a></nav>
<header><p class="eyebrow">Токопровод · Каталожная карточка</p><h1>{series.TITLE}</h1>
<p class="variant">{series.variant_name(*key)}</p></header>
<form method="get" class="series-form"><input type="hidden" name="view" value="catalog">
<label for="standalone-variant">Материал и номинал</label><select id="standalone-variant" name="variant">{options}</select>
<button type="submit">Показать</button></form>
{note}<div class="scroll"><table style="min-width:0"><thead><tr><th style="width:60%">Характеристика</th>
<th>Legrand RCP‑4C</th></tr></thead><tbody>{rows}</tbody></table></div>
<section><h2>Источник</h2><a href="{series.PDF}">Legrand F04447EN-00 · официальный техлист</a></section>
""", series.CSS)


def quote_page(handler, db):
    with closing(db_connect(db)) as conn:
        catalogues = [row for row in library_catalogues(conn) if row["comparison_type"]]
    options = [
        f'<option value="{escape(row["document_id"], quote=True)}" data-type="{row["comparison_type"]}"'
        f' data-reference="{escape(row["spec_product"], quote=True)}">'
        f'{escape(row["title"] or row["document_id"])}</option>'
        for row in catalogues
    ]
    if not any(row["comparison_type"] == "tp" and (row["spec_vendor"], row["spec_product"]) == LEGRAND
               for row in catalogues):
        options.append(
            f'<option value="" data-type="tp" data-reference="{escape(LEGRAND[1], quote=True)}">'
            f'{escape(series.TITLE)}</option>'
        )
    picker = '<select id="quote-catalogues" hidden>'
    html = card.QUOTE_COMPARISON_HTML.replace(picker + "</select>", picker + "".join(options) + "</select>", 1)
    templates = ""
    if legacy_pair(db):
        templates = "".join(
            f'<template id="quote-tech-{row["material"]}-{row["rating_a"]}">'
            f'{card.technical_comparison(row)}</template>'
            for row in matrix_v7.matrix(db)["rows"] if card.paired(row)
        )
    html = html.replace('<div id="quote-technical-templates" hidden></div>',
                        '<div id="quote-technical-templates" hidden>' + templates + '</div>', 1)
    html = html.replace("/* QUOTE_TECHNICAL_CSS */", matrix_v7.METRIC_CSS, 1)
    handler.send(200, html.encode("utf-8"), "text/html; charset=utf-8")


def document_page(handler, db, document_id):
    variant = series.VARIANT.fullmatch(param(handler, "variant"))
    if not variant:
        page(handler, 400, "Каталог", '<p>Укажите материал Al или Cu и целый номинал.</p>')
        return
    with closing(db_connect(db)) as conn:
        selected = next((row for row in library_catalogues(conn) if row["document_id"] == document_id), None)
        if not selected or not selected["comparison_type"]:
            page(handler, 404, "Каталог", '<p>Каталог недоступен для сравнения.</p>')
            return
        rows = [dict(row) for row in conn.execute(
            "SELECT parameter,value,unit,source,status FROM specs "
            "WHERE vendor=? AND product=? AND material=? AND rating_a=? ORDER BY parameter",
            (selected["spec_vendor"], selected["spec_product"], variant[1], int(variant[2])),
        )]
        phase_key = (selected["spec_vendor"], selected["spec_product"], variant[1], int(variant[2]))
        conductor = phase.geometry(conn).get(phase_key, {})
    key = (phase_key[:2], *phase_key[2:])
    existing = {row["parameter"] for row in rows if row["value"] is not None}
    supplemental = physical.VARIANTS.get(key, {}) | conductor
    additional = [row for code, row in supplemental.items() if code not in existing]
    rows = [row for row in rows if row["value"] is not None] + additional
    content = "".join(
        f'<tr><th scope="row">{escape(physical.LABELS.get(row["parameter"], card.LABELS.get(row["parameter"], row["parameter"])))}</th>'
        f'<td>{physical.render_value(row) if row["parameter"] in physical.LABELS else card.display_value(row)}'
        f'{("<small>" + escape(row["variant_note"]) + "</small>") if row.get("variant_note") else ""}</td></tr>'
        for row in rows
    )
    evidence = "".join(
        f'<li>{physical.evidence(row) if row.get("source_url") else escape(row["source"] or "")}'
        f' · {escape(row["status"] or "")}</li>'
        for row in {((item["source"] or ""), (item["status"] or "")): item for item in rows}.values()
        if row["source"] or row["status"]
    )
    details = (
        '<div class="scroll"><table><thead><tr><th>Характеристика</th><th>Значение</th>'
        '</tr></thead><tbody>' + content + '</tbody></table></div>'
        if rows else '<p class="notice">Для выбранного исполнения характеристики ещё не импортированы.</p>'
    )
    if evidence:
        details += '<details class="catalog-evidence"><summary>Источники и статусы</summary><ul>' + evidence + '</ul></details>'
    construction = next((row["variant"] for row in additional if row.get("variant")), "")
    if construction:
        details = f'<p class="variant">{escape(construction)}.</p>' + details
    title = selected["title"] or document_id
    equipment = LABELS[selected["comparison_type"]]
    page(handler, 200, title,
         '<nav><a href="?view=quote-comparison">← Сравнение КП</a></nav>'
         f'<header><p class="eyebrow">{equipment} · Каталожная карточка</p><h1>{escape(title)}</h1>'
         f'<p class="variant">{series.variant_name(variant[1], int(variant[2]))}</p></header>{details}',
         '.catalog-evidence{margin-top:16px}td small{display:block;color:#54675d;margin-top:3px}')


def get(handler, db):
    path = urlsplit(handler.path).path
    if path == "/api/catalog-types":
        with closing(db_connect(db)) as conn:
            send_json(handler, 200, {"documents": catalogue_rows(conn), "catalogues": library_catalogues(conn)})
        return True
    if path.startswith("/api/reports/") and not report_pair(db):
        error(handler, "Для отчёта подтвердите один тип у обоих каталогов.", 409)
        return True
    if path not in MATRIX_PATHS:
        return False
    view = param(handler, "view")
    if path == "/matrix-v7" and view == "quote-comparison":
        quote_page(handler, db)
        return True
    if path == "/matrix-v7" and view in {"catalog", "series"}:
        document_id = param(handler, "document")
        if document_id:
            document_page(handler, db, document_id)
        else:
            legrand_page(handler, db)
        return True
    if legacy_pair(db):
        if path != "/matrix-v7":
            return False
        material = param(handler, "material") or "all"
        if material not in ("all", "Al", "Cu"):
            page(handler, 400, "Каталоги PitON", "<p>Укажите материал Al или Cu.</p>")
            return True
        data = matrix_v7.matrix(db, material)
        status, html = card.matrix_view(data, handler.params(), matrix_v7.page)
        if not view:
            html = html.replace("</h1>", '</h1><p><a class="download" href="?view=catalog">'
                                'Токопроводы · каталог Legrand</a></p>', 1)
        handler.send(status, html.encode("utf-8"), "text/html; charset=utf-8")
        return True
    if path != "/matrix-v7":
        error(handler, "Для выгрузки сравнения подтвердите тип каталога Graziadio ISOLSBARRA.", 409)
    else:
        page(handler, 200, "Каталоги PitON", """
<h1>Каталоги PitON</h1>
<section><h2>Шинопроводы</h2><p>Для сравнения PitON E3 и Graziadio ISOLSBARRA
укажите тип каталога Graziadio в библиотеке.</p></section>
<section><h2>Токопроводы</h2><a href="?view=catalog">Каталог Legrand RCP‑4C (3L+N)</a></section>
<section><a href="?view=quote-comparison">Сравнение КП</a></section>
""")
    return True
