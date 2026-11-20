"""Published cross-sections, linear mass and insulation for straight lengths."""
from html import escape
from math import isfinite
import catalog_phase_conductor_v8_11_10 as phase

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
                "Бесшовная компаундная, класс C", technical_ref, insulation_ref
            )
    for material, leaf, table in GRAZIADIO_TABLES:
        technical_ref = (f"Graziadio ISOLSBARRA, стр. {leaf} каталога", f"{GRAZIADIO_PDF}#page=3")
        insulation_ref = ("Graziadio ISOLSBARRA, стр. 64–65 каталога", f"{GRAZIADIO_PDF}#page=2")
        for rating, count, mass in table:
            entries = make_variant(
                GRAZIADIO, material, rating, GRAZIADIO_WIDTH[count], 120, mass, GRAZIADIO_VARIANT,
                "Индивидуальная оболочка из ПВХ", technical_ref, insulation_ref
            )
            if rating == 6300:
                entries["dimensions_mm"]["variant_note"] = "N = 3000 мм² по сноске"
            variants[(GRAZIADIO, material, rating)] = entries
    return variants


VARIANTS = build_variants()


def records(phase_variants):
    return [
        entry for catalogue in (VARIANTS, phase_variants)
        for variant in catalogue.values() for entry in variant.values()
    ]


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


def section_axes(entry):
    if not entry or entry["unit"] != "мм":
        return None, None
    width, _, height = str(entry["value"]).partition("×")
    return tuple({"value": int(axis), "unit": "мм"} if axis.strip().isdecimal() else None
                 for axis in (width, height))


def comparison_row(label, left, right, highlight=None):
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
            display[selected] = f'<span class="metric-highlight">{display[selected]}<small>{label_short}</small></span>'
    return f'<tr><th scope="row">{escape(label)}</th><td>{display[0]}</td><td>{display[1]}</td></tr>'


def comparison_markup(material, rating, phase_geometry):
    piton = VARIANTS.get((PITON, material, rating), {}) | phase_geometry.get("piton", {})
    graziadio = VARIANTS.get((GRAZIADIO, material, rating), {}) | phase_geometry.get("graziadio", {})
    if not (piton or graziadio):
        return ""
    built_rows = []
    for code, label in LABELS.items():
        left, right = piton.get(code), graziadio.get(code)
        if code == "dimensions_mm":
            for axis, piton_axis, graziadio_axis in zip(
                ("Ширина секции (Ш)", "Высота секции (В)"), section_axes(left), section_axes(right)
            ):
                built_rows.append(comparison_row(axis, piton_axis, graziadio_axis, ("меньше", "мм")))
        else:
            highlight = ("больше", "мм²") if code == "phase_area_mm2" else None
            built_rows.append(comparison_row(label, left, right, highlight))
    rows = "".join(built_rows)
    notes = "".join(
        f'<li><strong>{escape(entries["dimensions_mm"]["product"])}</strong> · '
        f'{escape(entries["dimensions_mm"]["variant"])}'
        f'{escape(", " + entries["dimensions_mm"]["variant_note"]) if "variant_note" in entries["dimensions_mm"] else ""}. '
        f'{evidence(entries["dimensions_mm"])} · {evidence(entries["insulation_type"])}</li>'
        for entries in (piton, graziadio) if "dimensions_mm" in entries
    )
    phase_sources = "".join(
        f'<li><strong>{escape(product)} · шина</strong> · '
        + " · ".join(dict.fromkeys(evidence(entry) for code, entry in entries.items() if code in phase.LABELS))
        + '</li>'
        for product, entries in ((PITON[1], piton), (GRAZIADIO[1], graziadio))
        if any(code in entries for code in phase.LABELS)
    )
    return (
        '<section class="construction-details"><h2>Габариты, масса и изоляция</h2>'
        '<div class="scroll"><table><thead><tr><th>Параметр</th><th>PitON E3</th>'
        f'<th>Graziadio ISOLSBARRA</th></tr></thead><tbody>{rows}</tbody></table></div>'
        f'<ul class="source">{notes}{phase_sources}</ul></section>'
    )
