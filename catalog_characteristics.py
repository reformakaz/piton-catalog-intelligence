"""Catalog-scoped review and transactional import of technical characteristics."""
import csv
import hashlib
import io
import json
import re
import sqlite3
from contextlib import closing

import catalog_phase_conductor_v8_11_10 as phase
import catalog_types_v8_11 as types
import spec_import
from catalog_engine import PITON_PRODUCTS

FIELDS = spec_import.FIELDS
PHASE_FIELDS = set(phase.PARAMETERS)
MISSING = (None, "")


def identity(vendor, product):
    return types.PITON if vendor == "piton" and product in PITON_PRODUCTS else (vendor, product)


def target(conn, document):
    catalogue = next((row for row in types.library_catalogues(conn) if row["document_id"] == document), None)
    if not catalogue or not catalogue["comparison_type"] or not catalogue["spec_vendor"] or not catalogue["spec_product"]:
        raise ValueError("Укажите производителя и тип каталога в библиотеке.")
    return catalogue


def rows_from_csv(content, vendor, product):
    geometry = phase.parse_csv(content)
    if geometry is not None:
        rows = [
            dict(vendor=v, product=p, material=m, rating_a=a, parameter=k, value=value,
                 unit=phase.PARAMETERS[k][1], source=source, status="source_document")
            for (v, p, m, a, k), (value, source, _) in geometry.items()
        ]
    else:
        rows, errors = spec_import.validate(content)
        if errors:
            raise ValueError(" ".join(f'Строка {row["line"]}: {row["error"]}.' for row in errors[:5]))
        rows = [dict(zip(FIELDS, row)) for row in rows]
    if any(identity(row["vendor"], row["product"]) != identity(vendor, product) for row in rows):
        raise ValueError("В CSV есть характеристики другого каталога. Проверьте производителя и продукт.")
    return rows


def rows_from_pdf(doc, vendor, product):
    if doc == "piton" and identity(vendor, product) == types.PITON:
        import scan_v6
        result = scan_v6.candidates(doc)
        rows = result["proposals"]
        seen = {(r["material"], r["rating_a"], r["parameter"]) for r in rows}
        for miss in result["rejected"]:
            key = (miss["material"], miss["rating_a"], miss["parameter"])
            if key in seen:
                continue
            seen.add(key)
            page = miss["page"]
            rows.append(dict(vendor=vendor, product=product, material=key[0], rating_a=key[1],
                             parameter=key[2], value=None, unit=spec_import.PARAMS[key[2]][0],
                             source=f'{result["document"]["title"]}, PDF стр. {page}',
                             status="manual_review", page=page))
    else:
        import extract_v4
        result = extract_v4.candidates(doc)
        rows = result["proposals"]
    if not rows:
        raise ValueError("В PDF не удалось извлечь технические таблицы. Попробуйте импорт из CSV.")
    if any(identity(r["vendor"], r["product"]) != identity(vendor, product) for r in rows):
        raise ValueError("Найденные в PDF таблицы не соответствуют выбранному каталогу.")
    return rows


def normalize(raw, vendor, product, allow_missing):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 1000:
        raise ValueError("Выберите от 1 до 1000 характеристик.")
    result, seen = [], set()
    for index, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Строка {index}: некорректная характеристика.")
        material, param = row.get("material"), row.get("parameter")
        rating = str(row.get("rating_a", ""))
        source, unit = row.get("source"), row.get("unit")
        if (material not in ("Al", "Cu") or not re.fullmatch(r"[1-9][0-9]{0,5}", rating)
                or not isinstance(source, str) or not 1 <= len(source.strip()) <= 500
                or "\0" in source or not isinstance(param, str)):
            raise ValueError(f"Строка {index}: проверьте материал, номинал и источник.")
        key = (material, int(rating), param)
        if key in seen:
            raise ValueError(f"Строка {index}: характеристика указана дважды.")
        seen.add(key)
        value = row.get("value")
        status = row.get("status", "manual_review")
        if param in PHASE_FIELDS:
            if unit != phase.PARAMETERS[param][1]:
                raise ValueError(f"Строка {index}: неверная единица измерения.")
            value = phase.number(str(value))
            status = "source_document"
        elif param in spec_import.PARAMS:
            expected, lo, hi = spec_import.PARAMS[param]
            if (unit != expected or not 400 <= key[1] <= 7500
                    or status not in ("manual_review", "engineer_verified", "text_verified")
                    or not 8 <= len(source.strip()) <= 350 or not re.search(r"(?:стр\.?|page|p\.|PDF|лист)", source, re.I)):
                raise ValueError(f"Строка {index}: проверьте единицу, номинал и ссылку на страницу каталога.")
            if value in MISSING and allow_missing:
                value = None
            else:
                try:
                    value = float(str(value).replace(",", "."))
                except ValueError:
                    raise ValueError(f"Строка {index}: укажите числовое значение.") from None
                if not lo <= value <= hi:
                    raise ValueError(f"Строка {index}: значение вне допустимого диапазона.")
        else:
            raise ValueError(f"Строка {index}: параметр не поддерживается.")
        page = row.get("page")
        result.append(dict(zip(FIELDS, (vendor, product, *key, value, unit, source.strip(), status))) |
                      {"page": page if type(page) is int and page > 0 else None})
    return result


def reviewed(conn, rows):
    vendor, product = rows[0]["vendor"], rows[0]["product"]
    existing = {
        (m, a, k): (p, value, unit, source, status)
        for p, m, a, k, value, unit, source, status in conn.execute(
            "SELECT product,material,rating_a,parameter,value,unit,source,status FROM specs WHERE vendor=?", (vendor,)
        )
    }
    phase_existing = phase.PUBLISHED | phase.stored_rows(conn)
    result = []
    for row in rows:
        material, rating, code = (row[k] for k in ("material", "rating_a", "parameter"))
        other = existing.get((material, rating, code))
        if code in PHASE_FIELDS:
            saved = phase_existing.get((vendor, product, material, rating, code))
            other = (product, saved[0], row["unit"], saved[1], "") if saved else None
        foreign = other and identity(vendor, other[0]) != identity(vendor, product)
        if foreign:
            state = "conflict"
        elif row["value"] is None:
            state = "missing"
        elif not other:
            state = "new"
        else:
            state = "same" if (row["value"], row["unit"]) == other[1:3] else "changed"
        result.append(row | {"state": state, "current": other[1] if other and not foreign else None,
                             "snapshot": other})
    return result


def write(conn, rows):
    electrical = [r for r in rows if r["parameter"] not in PHASE_FIELDS]
    conn.executemany(
        """INSERT INTO specs(vendor,product,material,rating_a,parameter,value,unit,source,status)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(vendor,material,rating_a,parameter)
        DO UPDATE SET value=excluded.value,unit=excluded.unit,source=excluded.source,status=excluded.status""",
        (tuple(row[key] for key in FIELDS) for row in electrical),
    )
    dimensions = [r for r in rows if r["parameter"] in PHASE_FIELDS]
    if dimensions:
        conn.execute("""CREATE TABLE IF NOT EXISTS catalog_phase_specs (
            vendor TEXT NOT NULL,product TEXT NOT NULL,material TEXT NOT NULL,rating_a INTEGER NOT NULL,
            parameter TEXT NOT NULL,value REAL NOT NULL,source TEXT NOT NULL,
            PRIMARY KEY(vendor,product,material,rating_a,parameter))""")
        conn.executemany(
            """INSERT INTO catalog_phase_specs(vendor,product,material,rating_a,parameter,value,source)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(vendor,product,material,rating_a,parameter)
            DO UPDATE SET value=excluded.value,source=excluded.source""",
            (tuple(row[key] for key in ("vendor","product","material","rating_a","parameter","value","source"))
             for row in dimensions),
        )


def review(handler, db):
    document = types.param(handler, "document")
    action = types.param(handler, "action")
    if action not in ("pdf", "csv", "confirm", "save"):
        raise ValueError("Выберите этап импорта.")
    with closing(types.db_connect(db, writable=action == "save")) as conn, conn:
        if action == "save":
            conn.execute("BEGIN IMMEDIATE")
        catalogue = target(conn, document)
        vendor, product = catalogue["spec_vendor"], catalogue["spec_product"]
        payload = {}
        if action == "pdf":
            rows = rows_from_pdf(document, vendor, product)
        elif action == "csv":
            rows = rows_from_csv(handler.readbody(), vendor, product)
        else:
            payload = json.loads(handler.readbody())
            if not isinstance(payload, dict):
                raise ValueError("Ожидались строки характеристик.")
            rows = payload.get("rows")
        rows = reviewed(conn, normalize(rows, vendor, product, action == "pdf"))
        if action in ("pdf", "csv"):
            return types.send_json(handler, 200, {"rows": [{k:v for k,v in row.items() if k != "snapshot"} for row in rows]})
        if any(row["state"] == "conflict" for row in rows):
            raise ValueError("Часть ключей занята другим каталогом. Снимите выбор с этих строк.")
        changes = [row for row in rows if row["state"] in ("new", "changed")]
        digest = hashlib.sha256(json.dumps([document, rows], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if action == "save":
            if digest != payload.get("digest"):
                return types.error(handler, "Данные изменились после проверки. Вернитесь к проверке строк.", 409)
            write(conn, changes)
        counts = {key:sum(row["state"] == key for row in rows) for key in ("new", "changed", "same")}
    return types.send_json(handler, 200, counts | {"digest": digest})


def template(handler, db):
    with closing(types.db_connect(db)) as conn:
        catalogue = target(conn, types.param(handler, "document"))
    kind = types.param(handler, "kind")
    fields = phase.CSV_FIELDS if kind == "phase" else FIELDS
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, delimiter=";")
    writer.writeheader()
    writer.writerow({"vendor": catalogue["spec_vendor"], "product": catalogue["spec_product"],
                     "status": "manual_review"} if kind != "phase" else
                    {"vendor": catalogue["spec_vendor"], "product": catalogue["spec_product"]})
    handler.send(200, buffer.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
                 {"Content-Disposition": 'attachment; filename="catalog_characteristics.csv"'})
