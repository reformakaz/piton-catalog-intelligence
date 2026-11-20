"""Read technical PDF tables locally using their text layer or Tesseract."""
import concurrent.futures
from pathlib import Path
import re
import shutil
import subprocess

import spec_import

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
    if not isinstance(selection,str) or not re.fullmatch(r"[1-9][0-9]*(?:-[1-9][0-9]*)?",selection):
        raise ValueError("Укажите страницу или диапазон, например 4-9.")
    limits = [int(v) for v in selection.split("-")]
    first, last = limits[0], limits[-1]
    if not 1 <= first <= last <= count or last-first >= 12:
        raise ValueError("Выберите до 12 страниц в пределах документа.")
    return range(first-1,last)


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


def row_cells(line, code, unit, count):
    if EXCLUDED_ROW.search(line) or not LABEL_SIGNATURES[code].search(line):
        return ()
    if code == "icw_1s_ka" and not re.search(r"(?<![\d.,])1\s*(?:s|с|sec)\b",line,re.I):
        return ()
    if code == "r20_mohm_m" and not re.search(r"(?<!\d)20\s*°?\s*[cс]\b",line,re.I):
        return ()
    symbol = r"m\s*[ωΩ]\s*/\s*m" if code == "r20_mohm_m" else re.escape(unit)
    unit_match = re.search(r"(?<![a-z])" + symbol + r"(?![a-z])",line,re.I)
    if not unit_match:
        return ()
    tail = line[unit_match.end():]
    # Reject split ratings, text placeholders and missing columns. A dash is not a number.
    tokens = re.findall(r"\d+(?:[.,]\d+)?|[-–—]+", tail)
    if len(tokens)!=count:
        return ()
    return tuple(float(v.replace(",",".")) if v[0].isdigit() else None for v in tokens)


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
                unit,lo,hi=spec_import.PARAMS[code]
                cells = row_cells(line,code,unit,len(ratings))
                for rating,value in zip(ratings,cells):
                    if value is None or not lo <= value <= hi:
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
