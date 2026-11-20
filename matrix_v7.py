"""PitON Catalog Intelligence v7: read-only product comparison matrix."""
import csv,html,io,json,re,sqlite3
from collections import defaultdict
from catalog_engine import COMPARISON_FILTER, COMPARISON_FILTER_PARAMS, PITON_PRODUCTS

PARAMS={
 'nominal_voltage_v':('Напряжение изоляции','equal','V'),
 'icw_1s_ka':('Icw, 1 с','high','kA'),
 'ipk_ka':('Ipk','high','kA'),
 'r20_mohm_m':('R20 при 20 °C','low','mΩ/m'),
}
METRIC_CSS = """
.catalog-comparison-table thead th{background:#e5f0e8;color:#143e2f}
.catalog-comparison-table thead th:first-child{width:46%}
.catalog-comparison-table thead th:nth-child(n+2){width:27%;overflow-wrap:anywhere}
.catalog-comparison-table tbody th{font-weight:600}
.catalog-comparison-table .metric-highlight{display:inline-block;max-width:100%;padding:1px 5px;border-radius:4px;background:#e5f1e8;color:#174c35;font-weight:700}
.catalog-comparison-table .metric-badge{display:inline-block;max-width:100%;margin-top:3px;padding:2px 6px;border-radius:4px;background:#145943;color:#fff;font-size:.82em;font-weight:700;line-height:1.25;overflow-wrap:anywhere}
.catalog-comparison-table .comparison-note{display:block;margin-top:4px;color:#624617}
"""


def metric_highlight(value, badge):
 if not badge:
  return value
 return (
  f'<span class="metric-highlight">{value}</span> '
  f'<span class="metric-badge">{html.escape(badge)}</span>'
 )


def electrical_badge(detail, vendor):
 if detail['verdict'] != vendor or detail.get('comparison_note'):
  return ''
 return {'high': 'выше', 'low': 'ниже'}.get(PARAMS[detail['code']][1], '')


def comparison_note_markup(detail):
 note = detail.get('comparison_note')
 if not note and detail['verdict'] == 'different':
  note = 'Требуется уточнение'
 return f'<small class="comparison-note">{html.escape(note)}</small>' if note else ''


SUSPECT=('manual_review','ocr','unverified','pending')

IPK_UNIT_NOTE = 'Единица Ipk PitON требует сверки с протоколом испытаний'
IPK_EN_PDF = 'https://pitonelectric.ru/uploads/catalogE3en.pdf'
ICW_RU_SOURCE = 'https://pitonelectric.ru/uploads/shinoprovod_e3.pdf#page=62'
ICW_EN_SOURCE = f'{IPK_EN_PDF}#page=52'
ICW_CU_EDITIONS = {800: (25, 35), 1000: (35, 50), 1250: (50, 60), 1600: (60, 80)}
ICW_CONFLICT_NOTE = 'Icw (1 с): значения в каталогах PitON различаются'
IPK_EN_TABLES = {
    ('Al', '60'): (50, {400: 53, 630: 53, 800: 73.5, 1000: 105, 1250: 132, 1600: 176}),
    ('Al', '61'): (51, {2000: 176, 2500: 220, 3200: 264, 4000: 264, 5000: 264, 6400: 264}),
    ('Cu', '62'): (52, {630: 53, 800: 73.5, 1000: 105, 1250: 132, 1600: 176, 2000: 176}),
    ('Cu', '63'): (53, {2500: 220, 3200: 264, 4000: 264, 5000: 264, 6400: 264}),
}


def piton_ipk_page(detail):
    if detail["code"] != "ipk_ka" or not detail.get("piton"):
        return None
    source = detail["piton"].get("source") or ""
    match = re.search(r"\bPDF стр\. (6[0-3])\b", source)
    return match[1] if match else None


def unconfirmed_ipk_page(detail):
    return None if detail.get("comparison_source") else piton_ipk_page(detail)


def confirmed_ipk_source(detail):
    entry = detail.get("piton")
    page = piton_ipk_page(detail)
    if not page or entry.get("product") not in PITON_PRODUCTS:
        return None
    table = IPK_EN_TABLES.get((entry["material"], page))
    if not table:
        return None
    en_page, declared = table
    if declared.get(entry["rating_a"]) == entry["value"]:
        return f"{IPK_EN_PDF}#page={en_page}"
    return None


def icw_catalog_conflict(detail):
    entry = detail.get("piton")
    if (detail["code"] != "icw_1s_ka" or not entry
            or entry["product"] not in PITON_PRODUCTS or entry["material"] != "Cu"):
        return None
    values = ICW_CU_EDITIONS.get(entry["rating_a"])
    source = entry.get("source") or ""
    if not values or not (re.search(r"\bPDF стр\. 62\b", source) or ICW_EN_SOURCE in source):
        return None
    return {
        "unit": "kA",
        "ru": {"value": values[0], "source": ICW_RU_SOURCE},
        "en": {"value": values[1], "source": ICW_EN_SOURCE},
    }


def icw_conflict_links(conflict):
    return ' · '.join(
        f'<a href="{text(conflict[edition]["source"])}">{edition.upper()}: {text(conflict[edition]["value"])} кА</a>'
        for edition in ("ru", "en")
    )


def matrix(db,material='all'):
 from catalog_phase_conductor_v8_11_10 import pairs
 if material not in ('all','Cu','Al'):raise ValueError('Неверный материал')
 with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=10) as c:
  c.row_factory=sqlite3.Row
  raw=[dict(r) for r in c.execute(
   f"SELECT vendor,product,material,rating_a,parameter,value,unit,source,status FROM specs "
   f"WHERE {COMPARISON_FILTER} AND material IN ('Cu','Al') ORDER BY material,rating_a,vendor,parameter",
   COMPARISON_FILTER_PARAMS,
  )]
  phase_geometry=pairs(c)
 by=defaultdict(dict)
 for r in raw:
  if r['parameter'] not in PARAMS or r['value'] is None:continue
  by[(r['material'],r['rating_a'],r['parameter'])][r['vendor']]=r
 ratings=sorted({(m,a) for m,a,k in by if material=='all' or material==m},key=lambda t:(t[0],t[1]))
 rows=[];totals={'piton':0,'graziadio':0,'equal':0,'missing':0,'different':0}
 for mat,amp in ratings:
  details=[];t={x:0 for x in totals}
  for code,(name,direction,unit) in PARAMS.items():
   pair=by.get((mat,amp,code),{});p=pair.get('piton');g=pair.get('graziadio')
   detail = {'code': code, 'label': name, 'piton': p, 'graziadio': g}
   confirmation = confirmed_ipk_source(detail)
   if confirmation:
    p = dict(p, unit='kA')
    detail.update(piton=p, comparison_source=confirmation)
   elif unconfirmed_ipk_page(detail):
    detail['piton'] = dict(p, unit='')
    detail['comparison_note'] = IPK_UNIT_NOTE
   conflict = icw_catalog_conflict(detail)
   if conflict:
    detail.update(comparison_conflict=conflict, comparison_note=ICW_CONFLICT_NOTE)
   if not p or not g:state='missing'
   elif detail.get('comparison_note'):state='different'
   elif p['unit']!=g['unit'] or p['unit']!=unit:state='different'
   elif abs(p['value']-g['value'])<=1e-9:state='equal'
   elif direction=='equal':state='different'
   elif (p['value']>g['value'])==(direction=='high'):state='piton'
   else:state='graziadio'
   t[state]+=1;totals[state]+=1
   detail['verdict'] = state
   details.append(detail)
  caution = any(
   detail.get('comparison_note') or any(
    str(record.get('status') or '').lower() in SUSPECT
    for record in (detail['piton'], detail['graziadio']) if record
   )
   for detail in details
  )
  rows.append({'material':mat,'rating_a':amp,'counts':t,'details':details,'complete':t['missing']==0,'caution':caution,'phase_geometry':phase_geometry.get((mat,amp),{})})
 return {'rows':rows,'totals':totals,'scope':material,'parameters':list(PARAMS)}

def text(v):return html.escape(str(v),quote=True)
def cell(r, comparison_source=None, conflict=None, badge=''):
 if not r:return '<span class="missing">Нет данных</span>'
 status=str(r.get('status') or '')
 review=' ⚠' if status.lower() in SUSPECT else ''
 link = f' · <a href="{text(comparison_source)}">Ipk: каталог EN</a>' if comparison_source else ''
 value = icw_conflict_links(conflict) if conflict else f'{r["value"]:g} {text(r["unit"])}'
 return f'{metric_highlight(value, badge)}{review}<small>{text(r["source"] or "Источник не указан")}; статус: {text(status or "не указан")}{link}</small>'

def page(data):
 from catalog_physical_v8_11_9 import comparison_markup
 rows=data['rows'];rows_html=[]
 for entry in rows:
  detail=''.join(
   f'<tr><th scope="row">{text(d["label"])}{comparison_note_markup(d)}</th>'
   f'<td>{cell(d["piton"], d.get("comparison_source"), d.get("comparison_conflict"), electrical_badge(d,"piton"))}</td>'
   f'<td>{cell(d["graziadio"], badge=electrical_badge(d,"graziadio"))}</td></tr>'
   for d in entry['details']
  )
  ct=entry['counts']
  rows_html.append(f'<details><summary><b>{text(entry["material"])} · {entry["rating_a"]} А</b> — PitON: {ct["piton"]}; Graziadio: {ct["graziadio"]}; совпадений: {ct["equal"]}; нет данных: {ct["missing"]}; уточнить: {ct["different"]}{" · ⚠ есть непроверенные значения" if entry["caution"] else ""}</summary><table class="catalog-comparison-table"><thead><tr><th>Параметр</th><th>PitON E3</th><th>Graziadio ISOLSBARRA</th></tr></thead><tbody>{detail}</tbody></table>{comparison_markup(entry["material"], entry["rating_a"], entry.get("phase_geometry", {}))}</details>')
 opts=''.join(f'<option value="{v}" {"selected" if v==data["scope"] else ""}>{label}</option>' for v,label in [('all','Cu и Al'),('Cu','Медь (Cu)'),('Al','Алюминий (Al)')])
 t=data['totals']
 return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Catalog Intelligence — матрица сравнения</title><style>
 body{{font:14px/1.5 system-ui,Arial;background:#f1f5f9;color:#17243a;margin:0;padding:25px}}main{{max-width:1250px;margin:auto;background:white;padding:25px;border-radius:12px}}h1{{margin:0 0 7px}}.muted,small{{color:#60748c}}.note{{padding:12px;border-left:4px solid #e8a121;background:#fff8e8;margin:16px 0}}.stats{{display:flex;gap:12px;flex-wrap:wrap}}.stats span{{padding:11px;background:#eef4f8;border-radius:7px}}details{{border:1px solid #d5dfe8;border-radius:8px;margin:12px 0}}summary{{padding:14px;cursor:pointer;background:#f8fafc}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:10px;border-top:1px solid #e5e9ee;vertical-align:top}}td small{{display:block;font-size:11px;max-width:300px;overflow-wrap:anywhere;margin-top:5px}}.missing{{color:#9b5b1b}}select,button,a.download{{padding:9px;border:1px solid #b6c9d7;border-radius:6px;background:#fff;color:#135b88}}a.download{{display:inline-block;text-decoration:none}}@media(max-width:750px){{body{{padding:4px}}main{{padding:10px}}th,td{{font-size:11px;padding:4px}}}}
 {METRIC_CSS}</style></head><body><main><p><a href="/">← Вернуться в Catalog Intelligence</a></p><h1>Матрица сравнения PitON E3 / Graziadio</h1><p class="muted">Только сведения из локальной базы, без AI-догадок. Сравниваются одинаковые материалы и номинальные токи.</p><form method="get" action="/matrix-v7"><label>Материал: <select name="material">{opts}</select></label> <button type="submit">Показать</button> <a class="download" href="/api/matrix-v7.csv?material={text(data['scope'])}">CSV ↓</a> <a class="download" href="/api/matrix-v7.json?material={text(data['scope'])}">JSON ↓</a></form><div class="note">Отсутствие данных не означает недостаток изделия. «Преимущество» относится только к конкретному числовому параметру, а не ко всему оборудованию. Проверяйте исполнение, конфигурацию N/PE и испытательные протоколы. ⚠ — характеристика требует проверки.</div><div class="stats"><span><b>{len(rows)}</b> сочетаний материал/номинал</span><span><b>{t['piton']}</b> преимуществ PitON</span><span><b>{t['graziadio']}</b> преимуществ Graziadio</span><span><b>{t['missing']}</b> сравнений без данных</span><span><b>{t['different']}</b> требуют уточнения</span></div>{''.join(rows_html) if rows_html else '<p>Нет характеристик для выбранного материала.</p>'}<h2>Рекомендации менеджеру</h2><ol><li>Не сравнивать несопоставимые исполнения (нейтраль, PE, IP, стандарт испытания).</li><li>При преимуществе по R20 предлагать расчёт потерь только с учётом длины трассы, профиля нагрузки и температуры.</li><li>При сравнении Icw/Ipk запросить расчётные токи КЗ и требования проекта.</li><li>Проверить все строки с ⚠ по оригинальному PDF перед отправкой заказчику.</li></ol><p class="muted">Внутренний аналитический материал; не является сертификатом соответствия или расчётом проекта.</p></main></body></html>'''

def as_csv(data):
 s=io.StringIO();w=csv.writer(s,delimiter=';');w.writerow(['Материал','Ток, А','Параметр','PitON','Ед. PitON','Источник PitON','Статус PitON','Graziadio','Ед. Graziadio','Источник Graziadio','Статус Graziadio','Итог'])
 for x in data['rows']:
  for d in x['details']:
   p=d['piton'] or {};g=d['graziadio'] or {}
   source = p.get('source','')
   source = ' · '.join(filter(None, (source, d.get('comparison_source'), d.get('comparison_note'))))
   conflict = d.get('comparison_conflict')
   if conflict:
    source += ' · ' + ' · '.join(
     f'{edition.upper()}: {conflict[edition]["value"]} кА {conflict[edition]["source"]}'
     for edition in ('ru','en')
    )
   w.writerow([x['material'],x['rating_a'],d['label'],p.get('value',''),p.get('unit',''),source,p.get('status',''),g.get('value',''),g.get('unit',''),g.get('source',''),g.get('status',''),d['verdict']])
 return ('\ufeff'+s.getvalue()).encode('utf-8')
