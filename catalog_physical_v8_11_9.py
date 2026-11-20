"""Published cross-sections, linear mass and insulation for straight lengths."""
from html import escape
from math import isfinite
import re
import catalog_phase_conductor_v8_11_10 as phase
from matrix_v7 import metric_highlight

PITON = ("piton", "PitON E3")
GRAZIADIO = ("graziadio", "Graziadio ISOLSBARRA")
PITON_PDF = "https://pitonelectric.ru/uploads/shinoprovod_e3.pdf"
GRAZIADIO_PDF = "https://www.graziadio.it/wp-content/uploads/2017/12/ISOLSBARRA-Catalogo-2020-1.pdf"

LABELS = {
    "dimensions_mm": "Габариты секции, Ш × В",
    **phase.LABELS,
    "mass_kg_m": "Масса 1 пог. м",
    "insulation_type": "Тип изоляции",
}

# PitON: I, H, kg/m. Width is W=125 mm throughout the four tables.
PITON_TABLES = (
    ("Al", 60, ((400,109,7.2),(630,109,7.2),(800,124,8.7),(1000,149,10.7),(1250,179,13.2),(1600,229,17.2))),
    ("Al", 61, ((2000,269,20.2),(2500,269,24.6),(3200,395,32.9),(4000,475,38.9),(5000,475,47.7),(6400,681,70.8))),
    ("Cu", 62, ((630,109,12),(800,109,12),(1000,124,16.7),(1250,149,22.3),(1600,179,29.2),(2000,229,40.8))),
    ("Cu", 63, ((2500,269,52.6),(3200,295,56.9),(4000,395,80.1),(5000,475,102.5),(6400,681,120.2))),
)

# Graziadio catalogue leaves 66–67: I, conductor count, kg/m for 3P+N(P)+PE.
# Width is taken from the printed L table on the same spread, height is 120 mm.
GRAZIADIO_WIDTH = {4:190, 8:358, 11:484, 12:526, 15:652, 16:694}
GRAZIADIO_TABLES = (
    ("Al", 66, ((630,4,13.4),(800,4,13.6),(1000,4,16.8),(1250,4,16.8),(1600,4,19.2),
                (2000,8,27.4),(2500,8,34.6),(3200,12,45.2),(4000,12,53),(5000,16,70))),
    ("Cu", 67, ((800,4,18.5),(1000,4,20.5),(1250,4,23),(1600,4,28),(2000,8,51),
                (2500,8,61),(3200,11,67),(4000,11,81),(5000,15,104),(6300,15,156))),
)
PITON_VARIANT = "Прямая секция, 3L+N+PE"
GRAZIADIO_VARIANT = "Прямая секция, 3P+N(P)+PE, стандартное исполнение без дополнительного кожуха"
COMPOUND_INSULATION = "Бесшовная компаундная, класс C"
PVC_INSULATION = "Индивидуальная оболочка из ПВХ"

IMPORT_UNITS = {"dimensions_mm": "мм", "mass_kg_m": "кг/м", "insulation_type": ""}
DIMENSIONS = re.compile(r"([1-9]\d{0,4})\s*[×xх]\s*([1-9]\d{0,4})", re.I)


def import_value(code, value):
    text = str(value).strip()
    if code == "mass_kg_m":
        return phase.number(text)
    if code == "dimensions_mm":
        result = DIMENSIONS.fullmatch(text)
        if not result:
            raise ValueError("Укажите два габарита прямой секции в мм, например 125 × 395.")
        return " × ".join(result.groups())
    if not isinstance(value, str) or not 1 <= len(text) <= 140 or any(ord(c) < 32 for c in text):
        raise ValueError("Укажите тип изоляции как в каталоге.")
    return text


def stored_variants(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catalog_physical_specs'").fetchone()
    variants = {}
    if exists:
        for vendor, product, material, amps, code, value, unit, source, status in conn.execute(
            "SELECT vendor,product,material,rating_a,parameter,value,unit,source,status FROM catalog_physical_specs"
        ):
            if code in IMPORT_UNITS:
                variants.setdefault(((vendor, product), material, amps), {})[code] = dict(
                    vendor=vendor,product=product,material=material,rating_a=amps,parameter=code,
                    value=import_value(code,value),unit=unit,source=source,status=status,
                )
    return variants


def geometry(conn):
    variants = phase.geometry(conn)
    for (identity, material, amps), values in stored_variants(conn).items():
        variants.setdefault((*identity, material, amps), {}).update(values)
    return variants


def pairs(conn):
    grouped = {}
    for (vendor, product, material, rating), entries in geometry(conn).items():
        if (vendor, product) in {PITON, GRAZIADIO}:
            grouped.setdefault((material, rating), {})[vendor] = entries
    return grouped



def make_variant(identity, material, rating, width, height, mass, variant, insulation, technical_source, insulation_source):
    result = {}
    for code, value, unit, (source, source_url) in (
        ("dimensions_mm", f"{width} × {height}", "мм", technical_source),
        ("mass_kg_m", mass, "кг/м", technical_source),
        ("insulation_type", insulation, "", insulation_source),
    ):
        result[code] = {
            "vendor": identity[0], "product": identity[1], "material": material, "rating_a": rating,
            "parameter": code, "value": value, "unit": unit, "variant": variant,
            "source": source, "source_url": source_url, "status": "catalog",
        }
    return result


def build_variants():
    variants = {}
    for material, pdf_page, table in PITON_TABLES:
        technical_ref = (f"PitON E3, стр. {pdf_page - 2} каталога", f"{PITON_PDF}#page={pdf_page}")
        insulation_ref = ("PitON E3, стр. 7 каталога", f"{PITON_PDF}#page=9")
        for rating, height, mass in table:
            variants[(PITON, material, rating)] = make_variant(
                PITON, material, rating, 125, height, mass, PITON_VARIANT,
                COMPOUND_INSULATION, technical_ref, insulation_ref
            )
    for material, leaf, table in GRAZIADIO_TABLES:
        technical_ref = (f"Graziadio ISOLSBARRA, стр. {leaf} каталога", f"{GRAZIADIO_PDF}#page=3")
        insulation_ref = ("Graziadio ISOLSBARRA, стр. 64–65 каталога", f"{GRAZIADIO_PDF}#page=2")
        for rating, count, mass in table:
            entries = make_variant(
                GRAZIADIO, material, rating, GRAZIADIO_WIDTH[count], 120, mass, GRAZIADIO_VARIANT,
                PVC_INSULATION, technical_ref, insulation_ref
            )
            if rating == 6300:
                entries["dimensions_mm"]["variant_note"] = "N = 3000 мм² по сноске"
            variants[(GRAZIADIO, material, rating)] = entries
    return variants


VARIANTS = build_variants()


def records(variants):
    combined = {(*identity, material, rating): dict(entries)
                for (identity, material, rating), entries in VARIANTS.items()}
    for key, entries in variants.items():
        combined.setdefault(key, {}).update(entries)
    return [entry for variant in combined.values() for entry in variant.values()]


def render_value(entry):
    if not entry:
        return "—"
    raw = entry["value"]
    value = f"{raw:g}".replace(".", ",") if isinstance(raw, (int, float)) else str(raw)
    return escape(" ".join(filter(None, (value, entry["unit"]))))


def evidence(entry):
    if not entry:
        return ""
    label = escape(entry["source"])
    if not entry.get("source_url"):
        return label
    return f'<a href="{escape(entry["source_url"], quote=True)}">{label}</a>'


def section_area(entry):
    if not entry or entry["unit"] != "мм":
        return None
    width, _, height = (part.strip() for part in str(entry["value"]).partition("×"))
    if not width.isdecimal() or not height.isdecimal():
        return None
    area = int(width) * int(height)
    return {"value": area / 100, "unit": "см²"} if area > 0 else None


def insulation_family(entry):
    if not entry:
        return None
    value = entry["value"]
    compound = bool(re.search(r"компаунд|cast resin|epoxy resin|эпоксид",value,re.I))
    pvc = bool(re.search(r"\bpvc\b|пвх|поливинилхлорид",value,re.I))
    if compound == pvc:
        return None
    return "compound" if compound else "pvc"


def comparison_row(label, left, right, highlight=None, prefer_compound=False):
    def valid(entry, unit):
        return (
            entry and entry["unit"] == unit and type(entry["value"]) in (int, float)
            and isfinite(entry["value"]) and entry["value"] > 0
        )

    display = [render_value(left), render_value(right)]
    if highlight:
        label_short, unit = highlight
        if valid(left, unit) and valid(right, unit) and left["value"] != right["value"]:
            first = left["value"] > right["value"] if label_short == "больше" else left["value"] < right["value"]
            selected = 0 if first else 1
            display[selected] = metric_highlight(display[selected], label_short)
    if prefer_compound:
        families = [insulation_family(entry) for entry in (left,right)]
        if set(families) == {"compound","pvc"}:
            selected = families.index("compound")
            display[selected] = metric_highlight(display[selected], "предпочтительнее")
    return f'<tr><th scope="row">{escape(label)}</th><td>{display[0]}</td><td>{display[1]}</td></tr>'


def comparison_parts(material, rating, phase_geometry):
    piton = VARIANTS.get((PITON, material, rating), {}) | phase_geometry.get("piton", {})
    graziadio = VARIANTS.get((GRAZIADIO, material, rating), {}) | phase_geometry.get("graziadio", {})
    if not (piton or graziadio):
        return "", ""
    built_rows = []
    for code, label in LABELS.items():
        if code in ("phase_width_mm", "phase_height_mm"):
            continue
        left, right = piton.get(code), graziadio.get(code)
        if code == "dimensions_mm":
            built_rows.append(comparison_row(
                "Площадь габаритного сечения секции (Ш × В)",
                section_area(left), section_area(right), ("меньше", "см²"),
            ))
        else:
            highlight = {"phase_area_mm2": ("больше", "мм²"), "mass_kg_m": ("меньше", "кг/м")}.get(code)
            built_rows.append(comparison_row(label, left, right, highlight, prefer_compound=code == "insulation_type"))
    rows = "".join(built_rows)
    notes = []
    for entries in (piton, graziadio):
        construction = [entry for code, entry in entries.items() if code in IMPORT_UNITS]
        if not construction:
            continue
        product = escape(construction[0]["product"])
        variant = entries.get("dimensions_mm", {})
        description = ", ".join(filter(None, (variant.get("variant"), variant.get("variant_note"))))
        references = " · ".join(dict.fromkeys(evidence(entry) for entry in construction))
        notes.append(f'<li><strong>{product}</strong> · {escape(description) + ". " if description else ""}{references}</li>')
    notes = "".join(notes)
    phase_sources = "".join(
        f'<li><strong>{escape(product)} · шина</strong> · '
        + " · ".join(dict.fromkeys(evidence(entry) for code, entry in entries.items() if code in phase.LABELS))
        + '</li>'
        for product, entries in ((PITON[1], piton), (GRAZIADIO[1], graziadio))
        if any(code in entries for code in phase.LABELS)
    )
    return rows, f'<ul class="source">{notes}{phase_sources}</ul>' if notes or phase_sources else ""


def comparison_markup(material, rating, phase_geometry):
    rows, references = comparison_parts(material, rating, phase_geometry)
    if not rows:
        return ""
    return (
        '<section class="construction-details"><h2>Габариты, масса и изоляция</h2>'
        '<div class="scroll"><table class="catalog-comparison-table"><thead><tr><th>Параметр</th><th>PitON E3</th>'
        f'<th>Graziadio ISOLSBARRA</th></tr></thead><tbody>{rows}</tbody></table></div>'
        f'{references}</section>'
    )
