"""Read technical PDF tables locally using their text layer or Tesseract."""
import concurrent.futures
from pathlib import Path
import re
import shutil
import subprocess

import spec_import
import catalog_physical_v8_11_9 as physical
import catalog_phase_conductor_v8_11_10 as phase

TABLE_TERMS = re.compile(
    r"\b(?:icw|ipk|r20|ui|impedance|withstand|insulation voltage|technical characteristics|short.circuit)\b"
    r"|термическ|изоляци|сопротивлен|технические характеристики", re.I
)
VARIANT = re.compile(r"\b(rcp)[\s\-‐‑–—]*(\d+c)\b", re.I)
EXCLUDED_ROW = re.compile(r"neutral|protective|zero.sequence|earth|нейтрал|защитн|нулев", re.I)
LABEL_SIGNATURES = {
    "nominal_voltage_v": re.compile(r"\bui\b|insulation voltage|напряжение изоляции", re.I),
    "icw_1s_ka": re.compile(r"\bicw\b|short.time|термическ", re.I),
    "ipk_ka": re.compile(r"\bipk\b|peak current|пиков|динамическ", re.I),
    "r20_mohm_m": re.compile(r"\br20\b|phase resistance|сопротивлен", re.I),
    "dimensions_mm": re.compile(r"overall dimensions?.*busbar|busway dimensions?|габарит.*секци", re.I),
    "phase_area_mm2": re.compile(r"(?:cross.section.*(?:phase|conductor)|(?:phase|conductor).*cross.section|сечение.*фаз)", re.I),
    "mass_kg_m": re.compile(r"\bweight\b|linear mass|масса|вес", re.I),
    "insulation_type": re.compile(r"insulation material|type of insulation|тип изоляции|материал изоляции", re.I),
}
NUM_TOKEN = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?(?![\d.,])")
def raster(page, pixels):
    import pymupdf
    scale = min(2.6, pixels / max(page.rect.width, page.rect.height))
    return page.get_pixmap(matrix=pymupdf.Matrix(scale,scale), colorspace=pymupdf.csGRAY,
                           alpha=False).tobytes("png")


def ocr_image(png):
    if not shutil.which("tesseract"):
        return ""
    try:
        result = subprocess.run(
            ["tesseract","stdin","stdout","--psm","6"], input=png,
            capture_output=True, timeout=12, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.decode("utf-8",errors="replace")


def page_indices(count, selection):
    if not selection:
        return range(count)
    if not isinstance(selection, str) or not re.fullmatch(r"[1-9][0-9]*(?:-[1-9][0-9]*)?(?:\s*,\s*[1-9][0-9]*(?:-[1-9][0-9]*)?)*", selection):
        raise ValueError("Укажите страницы PDF, например 4, 9 или 4-6.")
    indices = set()
    for item in selection.split(","):
        limits = [int(value) for value in item.strip().split("-")]
        first, last = limits[0], limits[-1]
        if not 1 <= first <= last <= count or last-first >= 12:
            raise ValueError("Выберите до 12 страниц в пределах документа.")
        indices.update(range(first-1,last))
        if len(indices) > 12:
            raise ValueError("За один запуск можно выбрать до 12 страниц.")
    return sorted(indices)


def read_pages(doc, selection):
    indices = page_indices(len(doc),selection)
    if not selection and len(indices) > 80:
        texts = [(i, doc[i].get_text(sort=True)) for i in indices]
        hits = [(i,t) for i,t in texts if TABLE_TERMS.search(t)]
        if not hits:
            raise ValueError("Укажите страницы с техническими таблицами: этот PDF длинный и не имеет доступного текстового оглавления.")
        return choose_pages(doc,hits,False)
    texts = [(i,doc[i].get_text(sort=True)) for i in indices]
    short = [i for i,t in texts if len(t.strip()) < 120]
    # Scan all image-only pages in a small catalog. Larger scans can use the same page-range control.
    if not selection and len(short) > 20:
        hits = [(i,t) for i,t in texts if TABLE_TERMS.search(t)]
        if not hits:
            raise ValueError("В PDF много сканированных страниц. Укажите диапазон технических таблиц и повторите распознавание.")
        texts = hits
        short = []
    elif short and not selection:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            rasters = [raster(doc[i],1550) for i in short]
            recognized = dict(zip(short,pool.map(ocr_image,rasters)))
        texts = [(i, (t+"\n"+recognized.get(i,"")).strip()) for i,t in texts]
    return choose_pages(doc,texts,bool(selection))


def choose_pages(doc, texts, selected):
    hits = [(i,t) for i,t in texts if TABLE_TERMS.search(t)]
    if not selected and len(hits) > 12:
        raise ValueError("Нашлось больше 12 страниц с техническими таблицами. Укажите диапазон страниц нужного исполнения.")
    if not selected and not hits and len(texts) > 12:
        raise ValueError("Не удалось выделить страницы с таблицами. Укажите диапазон страниц для распознавания.")
    picks = texts if selected or not hits else hits
    pages = []
    for i,t in picks:
        # Use text-layer plus local OCR for grounding. Do not OCR text PDFs again.
        if len(t.strip()) < 120:
            t += "\n" + ocr_image(raster(doc[i],2200))
        pages.append((i+1,t[:24000]))
    return pages


def numeric_cells(text):
    return [v.replace(",", ".") for v in NUM_TOKEN.findall(text)]


def table_header(text):
    headers = set()
    for line in text.splitlines():
        rate = re.search(r"(?:\bin\b|rated current|номинальный ток)\s*(?:\[|\()?\s*[aа]\b\s*(?:\]|\))?", line,re.I)
        if rate:
            values = tuple(int(item) for item in numeric_cells(line[rate.end():])
                           if item.isdecimal() and 400 <= int(item) <= 7500)
            if len(values) >= 2 and len(values) == len(set(values)):
                headers.add(values)
    return next(iter(headers)) if len(headers)==1 else ()


def material_on_page(text):
    copper = bool(re.search(r"\bcopper\b|\bcu\b|медь|медн",text,re.I))
    aluminium = bool(re.search(r"\balumin(?:i)?um\b|\bal\b|алюмини",text,re.I))
    if copper == aluminium:
        return None
    return "Cu" if copper else "Al"


PARAMETER_UNITS = {code: envelope[0] for code, envelope in spec_import.PARAMS.items()} | physical.IMPORT_UNITS | {"phase_area_mm2":"мм²"}
UNIT_PATTERN = {
    "r20_mohm_m": r"m\s*[ωΩ]\s*/\s*m|мОм\s*/\s*м",
    "dimensions_mm": r"mm|мм",
    "phase_area_mm2": r"(?:mm|мм)\s*(?:²|2|\^2)",
    "mass_kg_m": r"(?:kg|кг)\s*/\s*(?:m|м)",
}
INSULATION = re.compile(r"\b(?:PVC|ПВХ|cast resin|epoxy resin|polyester|polyethylene|mica|компаунд\w*|эпоксидная смола|поливинилхлорид)\b|[-–—]+",re.I)


def literal_value(code, raw):
    if code in physical.IMPORT_UNITS:
        return physical.import_value(code,raw)
    if code == "phase_area_mm2":
        return phase.number(raw)
    if not re.fullmatch(r"\d+(?:[.,]\d+)?",raw.strip()):
        raise ValueError("Ожидалось число из таблицы.")
    value=float(raw.replace(",","."))
    _,lo,hi=spec_import.PARAMS[code]
    if not lo <= value <= hi:
        raise ValueError("Числовое значение вне допустимого диапазона.")
    return value


def literal_unit(code, unit):
    unit = unit.strip()
    if code == "insulation_type":
        return not unit
    expres = UNIT_PATTERN.get(code,re.escape(PARAMETER_UNITS[code]))
    suffix = r"(?:\s*rms)?" if code=="icw_1s_ka" else ""
    return re.fullmatch(r"(?:"+expres+r")"+suffix,unit,re.I) is not None


def row_cells(line, code, unit, count):
    if EXCLUDED_ROW.search(line) or not LABEL_SIGNATURES[code].search(line):
        return ()
    if code == "icw_1s_ka" and not re.search(r"(?<![\d.,])1\s*(?:s|с|sec)\b",line,re.I):
        return ()
    if code == "r20_mohm_m" and not re.search(r"(?<!\d)20\s*°?\s*[cс]\b",line,re.I):
        return ()
    if code == "insulation_type":
        if re.search(r"thermal resistance|insulation class|класс",line,re.I):
            return ()
        tail = line[LABEL_SIGNATURES[code].search(line).end():]
        tokens = INSULATION.findall(tail)
    else:
        symbol = UNIT_PATTERN.get(code,re.escape(unit))
        unit_match = re.search(r"(?<![a-z])(?:"+symbol+r")(?![a-z])",line,re.I)
        if not unit_match:
            return ()
        tail = line[unit_match.end():]
        if code == "dimensions_mm":
            tokens = re.findall(r"\d+\s*[×xх]\s*\([^)]*\)|\d+\s*[×xх]\s*\d+|[-–—]+",tail)
        else:
            tokens = re.findall(r"\d+\s*[×xх]\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?|[-–—]+",tail)
    if len(tokens)!=count:
        return ()
    cells=[]
    for token in tokens:
        try:
            cells.append(None if re.fullmatch(r"[-–—]+",token) else literal_value(code,token))
        except ValueError:
            cells.append(None)
    return tuple(cells)


def known_variants(text):
    return {"-".join(match.groups()).lower() for match in VARIANT.finditer(text)}


def table_drafts(pages, vendor, product, title):
    chosen = known_variants(product)
    all_variants = set().union(*(known_variants(text) for _,text in pages))
    if not chosen and len(all_variants)>1:
        raise ValueError("В PDF несколько исполнений RCP. Укажите диапазон страниц одного исполнения.")
    drafts, conflicts = {},set()
    for page,text in pages:
        variants = known_variants(text)
        if len(variants)>1 or (chosen and variants != chosen):
            continue
        ratings, material = table_header(text),material_on_page(text)
        if not ratings or not material:
            continue
        for line in text.splitlines():
            for code in LABEL_SIGNATURES:
                unit=PARAMETER_UNITS[code]
                cells = row_cells(line,code,unit,len(ratings))
                for rating,value in zip(ratings,cells):
                    if value is None:
                        continue
                    key=(material,rating,code)
                    if key in drafts:
                        if drafts[key]["value"]!=value:
                            conflicts.add(key)
                        continue
                    drafts[key]=dict(vendor=vendor,product=product,material=material,rating_a=rating,
                                     parameter=code,value=value,unit=unit,status="manual_review",page=page,
                                     source=f"{title[:200]}, PDF стр. {page}")
    return [row for key,row in drafts.items() if key not in conflicts]


def extract(root, filename, title, vendor, product, selection=""):
    import pymupdf
    folder=(Path(root)/"catalogs").resolve()
    file=(folder/filename).resolve()
    if file.parent!=folder or not file.is_file():
        raise ValueError("PDF отсутствует в библиотеке.")
    with pymupdf.open(file) as doc:
        return table_drafts(read_pages(doc,selection),vendor,product,title)
