#!/usr/bin/env python3
"""PitON Catalog Intelligence v1 — deterministic offline pilot, no CRM writes."""
import argparse, csv, datetime, html, json, pathlib, re, shutil, sqlite3, sys
ROOT=pathlib.Path(__file__).resolve().parent
DB=ROOT/'catalogs.sqlite3'
DOCS=ROOT/'catalogs'
BASE=[
 ('piton','PitON Electric E3','Cu',4000,'nominal_voltage_v',1000,'V','PitON E3, printed p. 61 (PDF p. 63)','manual_review'),
 ('graziadio','Graziadio ISOLSBARRA','Cu',4000,'nominal_voltage_v',1000,'V','Graziadio ISOLSBARRA, technical tables, PDF p. 14','text_verified'),
 ('piton','PitON Electric E3','Cu',4000,'icw_1s_ka',120,'kA','PitON E3, printed p. 61 (PDF p. 63)','manual_review'),
 ('graziadio','Graziadio ISOLSBARRA','Cu',4000,'icw_1s_ka',156,'kA','Graziadio ISOLSBARRA, technical tables, PDF p. 14','text_verified'),
 ('piton','PitON Electric E3','Cu',4000,'ipk_ka',264,'kA','PitON E3, printed p. 61 (PDF p. 63)','manual_review'),
 ('graziadio','Graziadio ISOLSBARRA','Cu',4000,'ipk_ka',348,'kA','Graziadio ISOLSBARRA, technical tables, PDF p. 14','text_verified'),
 ('piton','PitON Electric E3','Cu',4000,'r20_mohm_m',0.008,'mΩ/m','PitON E3, printed p. 61 (PDF p. 63)','manual_review'),
 ('graziadio','Graziadio ISOLSBARRA','Cu',4000,'r20_mohm_m',0.0094,'mΩ/m','Graziadio ISOLSBARRA, technical tables, PDF p. 14','text_verified'),
]
META={'nominal_voltage_v':('Напряжение изоляции','equal'),'icw_1s_ka':('Icw, 1 с','higher'),'ipk_ka':('Ipk','higher'),'r20_mohm_m':('Активное сопротивление фазы при 20 °C','lower')}
SCHEMA='''CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,title TEXT,filename TEXT,pages INTEGER,text_pages INTEGER,sha256 TEXT,created_at TEXT);
CREATE TABLE IF NOT EXISTS specs(vendor TEXT,product TEXT,material TEXT,rating_a INTEGER,parameter TEXT,value REAL,unit TEXT,source TEXT,status TEXT,PRIMARY KEY(vendor,material,rating_a,parameter));'''
def conn():
 c=sqlite3.connect(DB);c.executescript(SCHEMA);return c
def init():
 c=conn();c.executemany('INSERT OR IGNORE INTO specs VALUES(?,?,?,?,?,?,?,?,?)',BASE);c.commit();c.close();print('База готова:',DB)
def ingest(pdf,vendor):
 import hashlib
 try:import fitz
 except ImportError:sys.exit('Нужен PyMuPDF: python3 -m pip install pymupdf')
 p=pathlib.Path(pdf);data=p.read_bytes();sha=hashlib.sha256(data).hexdigest();DOCS.mkdir(exist_ok=True)
 dest=DOCS/(vendor+'.pdf');shutil.copy2(p,dest) if p.resolve()!=dest.resolve() else None
 d=fitz.open(dest);n=sum(bool(pg.get_text().strip()) for pg in d);c=conn()
 c.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?,?,?)',(vendor,p.stem,dest.name,len(d),n,sha,datetime.datetime.now().isoformat(timespec='seconds')));c.commit();c.close()
 print(f'{vendor}: {len(d)} PDF-страниц, текстовый слой на {n}; сохранено {dest.name}')
 if n<len(d)//2:print('ВНИМАНИЕ: сканированный PDF; значения требуют визуальной проверки; автоматическое извлечение не заявляется.')
def verdict(a,b,key):
 if a is None or b is None:return 'Недостаточно данных'
 if abs(a-b)<1e-12:return 'Сопоставимо'
 order=META[key][1]
 winner=('PitON' if a>b else 'Graziadio') if order=='higher' else ('PitON' if a<b else 'Graziadio')
 return 'Преимущество '+winner if order!='equal' else 'Разные значения'
def render(material,rating,out):
 c=conn();rows=c.execute('SELECT vendor,parameter,value,unit,source,status FROM specs WHERE material=? AND rating_a=?',(material,rating)).fetchall();docs=c.execute('SELECT id,filename,pages,text_pages FROM documents').fetchall();c.close()
 index={(r[0],r[1]):r for r in rows};items=[]
 for key,(label,_) in META.items():
  p=index.get(('piton',key));g=index.get(('graziadio',key));a=p[2] if p else None;b=g[2] if g else None
  def val(r):return (f'{r[2]:g} {r[3]}' if r else 'Нет данных')
  def provenance(r):return (r[4]+' · '+('Проверить по скану' if r[5]=='manual_review' else 'Извлечено из текста') if r else '—')
  items.append({'parameter':key,'name':label,'piton':a,'graziadio':b,'piton_label':val(p),'graziadio_label':val(g),'piton_source':provenance(p),'graziadio_source':provenance(g),'verdict':verdict(a,b,key)})
 out=pathlib.Path(out);out.parent.mkdir(parents=True,exist_ok=True)
 payload={'scope':f'{material} {rating} A','documents':[dict(zip(['id','filename','pages','text_pages'],d)) for d in docs],'items':items,'limitations':['Pilot values for PitON E3 were transcribed from image-only pages and require engineer approval','Missing data are never interpreted as disadvantages','Not an engineering conformity certification']}
 out.with_suffix('.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
 with out.with_suffix('.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.writer(f,delimiter=';');w.writerow(['Параметр','PitON','Источник PitON','Graziadio','Источник Graziadio','Оценка'])
  for x in items:w.writerow([x['name'],x['piton_label'],x['piton_source'],x['graziadio_label'],x['graziadio_source'],x['verdict']])
 esc=html.escape
 trs=''.join(f'<tr><th>{esc(i["name"])}</th><td>{esc(i["piton_label"])}<small>{esc(i["piton_source"])}</small></td><td>{esc(i["graziadio_label"])}<small>{esc(i["graziadio_source"])}</small></td><td>{esc(i["verdict"])}</td></tr>' for i in items)
 benefits=[x['name'] for x in items if x['verdict']=='Преимущество PitON'];weak=[x['name'] for x in items if x['verdict']=='Преимущество Graziadio']
 def lis(a):return ''.join('<li>'+esc(v)+'</li>' for v in a) or '<li>Не установлены на этой выборке</li>'
 page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>PitON — Сравнение каталогов</title><style>body{font-family:Arial,sans-serif;background:#f4f6fa;color:#182a43;margin:0;padding:32px}main{max-width:1200px;margin:auto;background:white;padding:28px;border-radius:14px;box-shadow:0 8px 24px #142b4218}h1{margin-top:0}table{border-collapse:collapse;width:100%}th,td{padding:14px;border-bottom:1px solid #dfe5ed;text-align:left;vertical-align:top}thead{background:#edf3fa}small{display:block;font-size:11px;color:#64748b;margin-top:8px}section{background:#f6f8fb;padding:18px;margin-top:24px;border-radius:10px}.warn{border-left:4px solid #d59a15;background:#fffaed;padding:12px}p{line-height:1.6}@media(max-width:850px){body{padding:8px}main{padding:10px}table{font-size:12px}th,td{padding:7px}}</style><main><h1>Сравнение каталогов: PitON E3 и Graziadio ISOLSBARRA</h1><p>Сопоставление: <strong>'''+esc(material)+' · '+str(rating)+''' A</strong> · Пилот Catalog Intelligence v1 · Внутренний рабочий материал</p><div class="warn">Числа PitON из графического каталога требуют проверки ответственным инженером перед передачей клиенту. Разные исполнения IP и специальные условия эксплуатации здесь не сопоставлялись.</div><div style="overflow-x:auto"><table><thead><tr><th>Параметр</th><th>PitON E3</th><th>Graziadio ISOLSBARRA</th><th>Оценка</th></tr></thead><tbody>'''+trs+'''</tbody></table></div><section><h2>Сильные стороны PitON в данном примере</h2><ul>'''+lis(benefits)+'''</ul><h2>Сильные стороны конкурента</h2><ul>'''+lis(weak)+'''</ul></section><section><h2>Рекомендации менеджеру</h2><ol><li>Уточнить у заказчика расчётный Icw и Ipk и необходимый запас относительно расчётных токов КЗ.</li><li>Для оценки энергопотерь запросить профиль нагрузок, длину трассы, cos φ и условия температуры; не рассчитывать TCO только из R20.</li><li>Проверить параметры на одинаковом материале, номинале и конфигурации проводников (N/PE).</li><li>До внешней отправки подтвердить значения по паспорту исполнения и испытательным протоколам, а не только по каталогу.</li></ol></section><section><h2>Проверяемость</h2><p>Каждое значение сопровождается указанием каталога и страницы. «Нет данных» не означает отсутствие характеристики у производителя.</p></section></main></html>'''
 out.write_text(page,encoding='utf-8');print('Отчёты:',out,out.with_suffix('.csv'),out.with_suffix('.json'))
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='cmd',required=True);sub.add_parser('init');i=sub.add_parser('ingest');i.add_argument('--file',required=True);i.add_argument('--vendor',required=True,choices=['piton','graziadio']);r=sub.add_parser('report');r.add_argument('--material',default='Cu');r.add_argument('--rating',type=int,default=4000);r.add_argument('--out',default=str(ROOT/'reports'/'comparison_4000_cu.html'))
 a=p.parse_args();{'init':lambda:init(),'ingest':lambda:ingest(a.file,a.vendor),'report':lambda:render(a.material,a.rating,a.out)}[a.cmd]()
if __name__=='__main__':main()
