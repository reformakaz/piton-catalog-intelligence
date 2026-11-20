#!/usr/bin/env python3
"""Validated engineering CSV specifications import. No OCR or invented data."""
import csv,io,json,pathlib,re,sqlite3,sys
ROOT=pathlib.Path(__file__).resolve().parent
DB=ROOT/'catalogs.sqlite3'
PARAMS={'nominal_voltage_v':('V',100,20000),'icw_1s_ka':('kA',1,1000),'ipk_ka':('kA',1,2000),'r20_mohm_m':('mΩ/m',0.00001,5)}
FIELDS=['vendor','product','material','rating_a','parameter','value','unit','source','status']
def validate(data):
 if len(data)>1024*1024:raise ValueError('CSV не более 1 МБ')
 text=data.decode('utf-8-sig'); reader=csv.DictReader(io.StringIO(text),delimiter=';')
 if reader.fieldnames!=FIELDS:raise ValueError('Точные колонки CSV: '+';'.join(FIELDS))
 rows=[];seen=set();errors=[]
 for n,row in enumerate(reader,2):
  if n>1001:raise ValueError('Максимум 1000 строк за импорт')
  try:
   if None in row or any(v is None for v in row.values()):raise ValueError('Лишний или отсутствующий столбец')
   r={k:v.strip() for k,v in row.items()}
   if not re.fullmatch(r'[a-z][a-z0-9_-]{1,35}',r['vendor']):raise ValueError('Некорректный код производителя')
   if not (2<=len(r['product'])<=140):raise ValueError('Название продукта: 2–140 знаков')
   if r['material'] not in ('Cu','Al'):raise ValueError('Материал должен быть Cu или Al')
   rating=int(r['rating_a']);assert 400<=rating<=7500
   par=r['parameter'];assert par in PARAMS
   unit,lo,hi=PARAMS[par]
   if r['unit']!=unit:raise ValueError('Единица для '+par+': '+unit)
   value=float(r['value'].replace(',','.'));assert lo<=value<=hi
   if not (8<=len(r['source'])<=350) or not re.search(r'(?:стр\.?|page|p\.|PDF|лист)',r['source'],re.I):raise ValueError('Источник должен содержать название документа и номер страницы')
   if r['status'] not in ('manual_review','engineer_verified','text_verified'):raise ValueError('Недопустимый статус проверки')
   k=(r['vendor'],r['material'],rating,par)
   if k in seen:raise ValueError('Повтор той же характеристики в файле')
   seen.add(k)
   rows.append((r['vendor'],r['product'],r['material'],rating,par,value,unit,r['source'],r['status']))
  except (AssertionError,ValueError) as e:errors.append({'line':n,'error':str(e) or 'Недопустимое число, параметр или номинал'})
 if not rows and not errors:raise ValueError('CSV пуст')
 return rows,errors
def process(data,apply=False):
 rows,errors=validate(data)
 if errors:return {'ok':False,'errors':errors[:50],'total_errors':len(errors),'accepted':len(rows)}
 with sqlite3.connect(DB) as c:
  current={(x[0],x[2],x[3],x[4]):x for x in c.execute('SELECT vendor,product,material,rating_a,parameter,value,unit,source,status FROM specs')}
  counts={'new':0,'same':0,'changed':0}
  for x in rows:
   k=(x[0],x[2],x[3],x[4]);old=current.get(k)
   counts['new' if old is None else 'same' if tuple(old)==tuple(x) else 'changed']+=1
  if apply:
   c.executemany('INSERT INTO specs(vendor,product,material,rating_a,parameter,value,unit,source,status) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(vendor,material,rating_a,parameter) DO UPDATE SET product=excluded.product,value=excluded.value,unit=excluded.unit,source=excluded.source,status=excluded.status',rows)
 return {'ok':True,'applied':apply,'rows':len(rows),**counts,'warning':'Статус engineer_verified в CSV — заявление импортирующего специалиста, не автоматическая проверка.'}
if __name__=='__main__':
 content=pathlib.Path(sys.argv[1]).read_bytes();print(json.dumps(process(content,len(sys.argv)>2 and sys.argv[2]=='apply'),ensure_ascii=False,indent=2))
