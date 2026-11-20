"""Published phase cross-sections and user-supplied conductor geometry."""
from contextlib import closing
import csv
from decimal import Decimal, InvalidOperation
import io
from pathlib import Path
import re
import sqlite3

PITON = ("piton", "PitON E3")
GRAZIADIO = ("graziadio", "Graziadio ISOLSBARRA")
PITON_PDF = "https://pitonelectric.ru/uploads/shinoprovod_e3.pdf"
GRAZIADIO_PDF = "https://www.graziadio.it/wp-content/uploads/2017/12/ISOLSBARRA-Catalogo-2020-1.pdf"

PARAMETERS = {
    "phase_area_mm2": ("Сечение фазного проводника", "мм²"),
    "phase_width_mm": ("Ширина фазной шины (Ш)", "мм"),
    "phase_height_mm": ("Высота фазной шины (В)", "мм"),
}
LABELS = {code: label for code, (label, _) in PARAMETERS.items()}
CSV_FIELDS = ("vendor", "product", "material", "rating_a", *PARAMETERS, "source")

# Each pair is (nominal current, phase conductor area in square millimetres).
PITON_SECTIONS = (
    ("Al", 60, ((400,240),(630,240),(800,330),(1000,480),(1250,660),(1600,960))),
    ("Al", 61, ((2000,1200),(2500,1600),(3200,1920),(4000,2400),(5000,3200),(6400,4800))),
    ("Cu", 62, ((630,240),(800,240),(1000,330),(1250,480),(1600,660),(2000,960))),
    ("Cu", 63, ((2500,1200),(3200,1320),(4000,1920),(5000,2400),(6400,3600))),
)
GRAZIADIO_SECTIONS = (
    ("Al", 86, ((630,450),(800,500),(1000,690),(1250,800),(1600,1000),(2000,1380),
                (2500,2000),(3200,2400),(4000,3000),(5000,4000))),
    ("Cu", 87, ((800,400),(1000,450),(1250,500),(1600,600),(2000,1000),(2500,1200),
                (3200,1500),(4000,1800),(5000,2400),(6300,4000))),
)


def build_published():
    output = {}
    for identity, tables in ((PITON, PITON_SECTIONS), (GRAZIADIO, GRAZIADIO_SECTIONS)):
        for material, page, table in tables:
            if identity == PITON:
                source, url = f"PitON E3, стр. {page - 2} каталога, Sn", f"{PITON_PDF}#page={page}"
            else:
                source, url = f"Graziadio ISOLSBARRA, стр. {page} каталога, Sf", f"{GRAZIADIO_PDF}#page=13"
            for amps, area in table:
                output[(*identity, material, amps, "phase_area_mm2")] = (area, source, url)
    return output


PUBLISHED = build_published()


def stored_rows(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_phase_specs'").fetchone()
    if not exists:
        return {}
    return {
        (vendor, product, material, rating, parameter): (value, source, "")
        for vendor, product, material, rating, parameter, value, source in conn.execute(
            "SELECT vendor,product,material,rating_a,parameter,value,source FROM catalog_phase_specs"
        )
    }


def geometry(conn):
    variants = {}
    for key, (value, source, url) in (PUBLISHED | stored_rows(conn)).items():
        vendor, product, material, rating, code = key
        if code not in PARAMETERS:
            continue
        variants.setdefault(key[:-1], {})[code] = {
            "vendor": vendor, "product": product, "material": material, "rating_a": rating,
            "parameter": code, "value": value, "unit": PARAMETERS[code][1], "source": source,
            "source_url": url, "status": "catalog" if url else "source_document",
        }
    return variants


def pairs(conn):
    by_variant = {}
    for (vendor, product, material, rating), entries in geometry(conn).items():
        if (vendor, product) in {PITON, GRAZIADIO}:
            by_variant.setdefault((material, rating), {})[vendor] = entries
    return by_variant


def number(text):
    if not re.fullmatch(r"[0-9]{1,7}(?:[.,][0-9]{1,6})?", text):
        raise ValueError("ожидается положительное число")
    try:
        value = Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("ожидается положительное число") from exc
    if value <= 0:
        raise ValueError("значение должно быть больше нуля")
    return float(value)


def parse_csv(content):
    if not content:
        return None
    marker = content.split(b"\n", 1)[0]
    if not any(code.encode() in marker for code in PARAMETERS):
        return None
    if len(content) > 2_000_000:
        raise ValueError("CSV с сечением и размерами превышает 2 МБ.")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), delimiter=";", strict=True)
        if reader.fieldnames is None or len(reader.fieldnames) != len(CSV_FIELDS) or set(reader.fieldnames) != set(CSV_FIELDS):
            raise ValueError("Скачайте шаблон «Сечение и размеры шины» и сохраните все заголовки.")
        records = {}
        for row in reader:
            number_in_csv = reader.line_num
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Строка {number_in_csv}. Проверьте количество столбцов.")
            if not any(value.strip() for value in row.values()):
                continue
            vendor, product, material, amps, source = (row[key].strip() for key in ("vendor","product","material","rating_a","source"))
            vendor = vendor.lower()
            if (not re.fullmatch(r"[a-z][a-z0-9_-]{1,35}", vendor) or not 1 <= len(product) <= 200
                    or material not in {"Al", "Cu"} or not re.fullmatch(r"[1-9][0-9]{0,5}", amps)
                    or not 1 <= len(source) <= 500 or "\x00" in product + source):
                raise ValueError(f"Строка {number_in_csv}. Проверьте производителя, каталог, Al/Cu, номинал и источник.")
            values = {code: row[code].strip() for code in PARAMETERS if row[code].strip()}
            if not values:
                raise ValueError(f"Строка {number_in_csv}. Укажите хотя бы одно значение.")
            for code, value in values.items():
                key = (vendor, product, material, int(amps), code)
                if key in records:
                    raise ValueError(f"Строка {number_in_csv}. Повтор характеристики «{LABELS[code]}».")
                try:
                    records[key] = (number(value), source, "")
                except ValueError as exc:
                    raise ValueError(f"Строка {number_in_csv}. {LABELS[code]}: {exc}.") from exc
        if not records:
            raise ValueError("В CSV нет значений для импорта.")
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("Ожидается CSV UTF-8 с разделителем «;».") from exc
    return records


def import_csv(db, content, apply):
    records = parse_csv(content)
    if records is None:
        return None
    uri = Path(db).resolve().as_uri() + ("?mode=rw" if apply else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as conn, conn:
        if apply:
            conn.execute("BEGIN IMMEDIATE")
        existing = stored_rows(conn)
        changes = {key: record for key, record in records.items() if existing.get(key) != record}
        if apply and changes:
            conn.execute("""CREATE TABLE IF NOT EXISTS catalog_phase_specs (
                vendor TEXT NOT NULL, product TEXT NOT NULL, material TEXT NOT NULL, rating_a INTEGER NOT NULL,
                parameter TEXT NOT NULL, value REAL NOT NULL, source TEXT NOT NULL,
                PRIMARY KEY(vendor,product,material,rating_a,parameter)
            )""")
            conn.executemany(
                """INSERT INTO catalog_phase_specs(vendor,product,material,rating_a,parameter,value,source)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(vendor,product,material,rating_a,parameter)
                DO UPDATE SET value=excluded.value, source=excluded.source""",
                ((*key, record[0], record[1]) for key, record in changes.items()),
            )
        new = sum(key not in existing for key in changes)
    return {"ok": True, "kind": "phase", "rows": len(records), "new": new, "same": len(records)-len(changes),
            "changed": len(changes)-new, "warning": "Характеристики фазного проводника."}
