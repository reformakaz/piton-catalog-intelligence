#!/usr/bin/env python3
"""Catalog Intelligence v2: localhost-only library and evidence-backed demo reports."""
import hashlib,html,json,mimetypes,os,pathlib,re,sqlite3,subprocess,sys,urllib.parse,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import catalog_workspace
ROOT=pathlib.Path(__file__).resolve().parent
DB=ROOT/'catalogs.sqlite3'; DOCS=ROOT/'catalogs'; REPORTS=ROOT/'reports'
UI=(ROOT/'ui.html').read_bytes() if (ROOT/'ui.html').exists() else b''
MAX_UPLOAD=80*1024*1024
PARAMS={'nominal_voltage_v': 'Напряжение изоляции','icw_1s_ka':'Термическая стойкость Icw, 1 с','ipk_ka':'Пиковая стойкость Ipk','r20_mohm_m':'Активное сопротивление фазы при 20 °C'}

def connection():
 c=sqlite3.connect(f'file:{DB}?mode=rw',uri=True,timeout=10)
 c.row_factory=sqlite3.Row
 return c

def jdata(obj):return json.dumps(obj,ensure_ascii=False).encode()

class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):sys.stderr.write('[catalog] '+fmt%args+'\n')
 def send(self,code,body=b'',ctype='application/json; charset=utf-8',headers=None):
  if code==200 and ctype.startswith('text/html'):
   if getattr(self,'catalog_public',False):
    body=catalog_workspace.public_html(self,body)
   path=urllib.parse.urlsplit(self.path).path
   body=catalog_workspace.themed_html(body,path,(self.params().get('view') or [''])[0])
  self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)))
  # PITON_CATALOG_REPORT_PREVIEW_FIX_V1: only same-origin report HTML may be framed.
  report_preview = (urllib.parse.urlsplit(self.path).path.startswith('/api/reports/')
                    and ctype.startswith('text/html') and code == 200)
  self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
  self.send_header('X-Frame-Options','SAMEORIGIN' if report_preview else 'DENY')
  csp="default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'self'" if report_preview else "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
  if code==200 and ctype.startswith('text/html') and urllib.parse.urlsplit(self.path).path=='/matrix-v7' and self.params().get('view')==['quote-comparison']:
   csp=csp.replace("script-src 'self';","script-src 'self' 'sha256-PlD5WQ6XH04PGemdLFxnT3JrblwKELrol77KscyGIV4=';",1)
  self.send_header('Content-Security-Policy',csp)
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.end_headers();self.wfile.write(body)
 def fail(self,code,msg):self.send(code,jdata({'error':msg}))
 def params(self):return urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
 def readbody(self):
  size=int(self.headers.get('Content-Length','0'))
  if not 0<size<=MAX_UPLOAD:raise ValueError('Недопустимый размер файла (максимум 80 МБ)')
  return self.rfile.read(size)
 def do_GET(self):
  try:
   if catalog_workspace.public_route(self):self.get()
  except (ValueError,KeyError) as e:self.fail(400,str(e))
  except Exception as e:self.fail(500,'Ошибка сервера: '+type(e).__name__)
 def get(self):
  path=urllib.parse.urlsplit(self.path).path
  from catalog_types_v8_11 import get as catalogue_get
  if catalogue_get(self,DB):return
  if path=='/':return self.send(200,UI,'text/html; charset=utf-8')
  # PITON_CATALOG_MATRIX_V7: strictly read-only SQLite aggregation.
  if path in ('/matrix-v7','/api/matrix-v7.json','/api/matrix-v7.csv'):
   import matrix_v7
   material=(self.params().get('material') or ['all'])[0]
   data=matrix_v7.matrix(DB,material)
   if path=='/matrix-v7':
    from catalog_series_v8_9 import matrix_view
    status, page = matrix_view(DB, data, self.params(), matrix_v7.page)
    return self.send(status,page.encode('utf-8'),'text/html; charset=utf-8')
   if path.endswith('.json'):return self.send(200,jdata(data))
   return self.send(200,matrix_v7.as_csv(data),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="catalog_matrix_v7.csv"'})
  if path=='/api/health':return self.send(200,jdata({'ok':True,'version':'5.0','bind':'loopback'}))
  if path=='/api/characteristics-template':
   from catalog_characteristics import template
   return template(self,DB)
  if path=='/api/spec-template':
   if self.params().get('phase')==['1']:
    from catalog_phase_conductor_v8_11_10 import CSV_FIELDS
    template=';'.join(CSV_FIELDS)+'\n'
    return self.send(200,template.encode('utf-8-sig'),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="phase_conductor_template.csv"'})
   template='vendor;product;material;rating_a;parameter;value;unit;source;status\n'
   return self.send(200,template.encode('utf-8-sig'),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="spec_import_template.csv"'})
  # PITON_CATALOG_EXTRACT_V4: candidate-only PDF analysis; never writes to DB.
  # PITON_CATALOG_SCAN_V5 — experimental OCR preview of known table only.
  # PITON_CATALOG_SCAN_V6: OCR four SHA-guarded E3 technical tables.
  if path=='/api/scan-v6':
   import scan_v6
   doc=(self.params().get('document') or [''])[0]
   if not doc:raise ValueError('Выберите каталог')
   return self.send(200,jdata(scan_v6.candidates(doc)))
  if path=='/api/scan-v5':
   import scan_v5
   doc=(self.params().get('document') or [''])[0]
   if not doc:raise ValueError('Выберите каталог')
   return self.send(200,jdata(scan_v5.candidates(doc)))
  if path=='/api/extract-v4':
   import extract_v4
   doc=(self.params().get('document') or [''])[0]
   if not doc:raise ValueError('Выберите каталог')
   return self.send(200,jdata(extract_v4.candidates(doc)))
  if path=='/api/library':
   import catalog_physical_v8_11_9 as physical
   from catalog_characteristics import variant_specs
   from catalog_types_v8_11 import archived_documents
   with connection() as c:
    docs=[dict(x) for x in c.execute('SELECT * FROM documents ORDER BY created_at DESC')]
    specs=[dict(x) for x in c.execute('SELECT vendor,product,material,rating_a,parameter,value,unit,source,status FROM specs ORDER BY vendor,material,rating_a')]
    construction=physical.records(physical.geometry(c))
    variants=variant_specs(c)
    archived=archived_documents(c)
   return self.send(200,jdata({'documents':docs,'specs':specs,'physical':construction,'variants':variants,'archived_documents':archived,'parameters':{**PARAMS,**physical.LABELS},'note':'Новые PDF индексируются, но их спецификации не распознаются автоматически; у v1 только пилот Cu 4000 A.'}))
  if path.startswith('/api/pdf/'):
   docid=path.split('/')[-1]
   with connection() as c:r=c.execute('SELECT filename FROM documents WHERE id=?',(docid,)).fetchone()
   if not r:return self.fail(404,'Документ не найден')
   file=(DOCS/r['filename']).resolve()
   if file.parent!=DOCS.resolve() or not file.is_file():return self.fail(404,'PDF отсутствует')
   return self.send(200,file.read_bytes(),'application/pdf',{'Content-Disposition':"inline; filename=\"catalog.pdf\""})
  if path.startswith('/api/reports/'):
   name=path.rsplit('/',1)[-1]
   if not re.fullmatch(r'comparison_(?:Cu|Al)_[0-9]{3,5}\.(?:html|csv|json)',name):return self.fail(400,'Неверное имя отчёта')
   file=REPORTS/name
   if not file.is_file():return self.fail(404,'Отчёт ещё не создан')
   types={'.html':'text/html; charset=utf-8','.csv':'text/csv; charset=utf-8','.json':'application/json; charset=utf-8'}
   return self.send(200,file.read_bytes(),types[file.suffix],{'Content-Disposition':'attachment; filename="'+name+'"'} if file.suffix!='.html' else None)
  if path=='/ui.js':return self.send(200,(ROOT/'ui.js').read_bytes(),'text/javascript; charset=utf-8')
  self.fail(404,'Не найдено')
 def do_POST(self):
  try:
   if catalog_workspace.public_route(self):self.post()
  except (ValueError,KeyError) as e:self.fail(400,str(e))
  except Exception as e:
   self.log_message('error: %s',repr(e));self.fail(500,'Ошибка сервера: '+type(e).__name__)
 def post(self):
  path=urllib.parse.urlsplit(self.path).path
  # Loopback only; require same-origin browser requests for modifications.
  origin=self.headers.get('Origin','')
  if not getattr(self,'catalog_public',False) and origin and origin not in ('http://127.0.0.1:8137','http://localhost:8137'):
   return self.fail(403,'Недопустимый Origin')
  if path=='/api/characteristics':
   from catalog_characteristics import review
   return review(self,DB)
  if path=='/api/catalog-presence':
   from catalog_types_v8_11 import set_document_presence
   return set_document_presence(self,DB)
  if path=='/api/catalog-type':
   from catalog_types_v8_11 import set_existing_type
   return set_existing_type(self,DB)
  # PITON_CATALOG_SPEC_IMPORT_V3: validated CSV specifications; no OCR claims.
  if path=='/api/spec-import':
   from catalog_phase_conductor_v8_11_10 import import_csv
   q=self.params();apply=(q.get('apply') or ['0'])[0]=='1'
   content=self.readbody()
   result=import_csv(DB,content,apply)
   if result is None:
    import spec_import
    result=spec_import.process(content,apply)
   return self.send(200,jdata(result))
  if path=='/api/upload':
   q=self.params();vendor=(q.get('vendor') or [''])[0].strip().lower();title=(q.get('title') or [''])[0].strip()[:140]
   if not re.fullmatch(r'[a-z][a-z0-9_-]{1,35}',vendor):raise ValueError('Код производителя: латиница, цифры и дефис')
   if not title:raise ValueError('Нужно название каталога')
   from catalog_types_v8_11 import upload_fields,record_classification
   catalogue_fields=upload_fields(self,vendor)
   content=self.readbody()
   if not content.startswith(b'%PDF-'):raise ValueError('Ожидается PDF')
   try:import pymupdf
   except ImportError:raise ValueError('Не установлен pymupdf в виртуальном окружении')
   # Check before storing file. Limit is intentionally small for pilot.
   import io
   pdf=pymupdf.open(stream=content,filetype='pdf')
   try:
    if pdf.needs_pass:raise ValueError('PDF защищён паролем')
    pages=len(pdf)
    if pages>500:raise ValueError('Не более 500 страниц')
    text_pages=sum(bool(p.get_text().strip()) for p in pdf)
   finally:pdf.close()
   digest=hashlib.sha256(content).hexdigest();id='doc_'+uuid.uuid4().hex[:16];fname=id+'.pdf';DOCS.mkdir(parents=True,exist_ok=True)
   file=DOCS/fname;file.write_bytes(content)
   try:
    with connection() as c:
     c.execute('INSERT INTO documents(id,title,filename,pages,text_pages,sha256,created_at) VALUES(?,?,?,?,?,?,datetime("now"))',(id,title,fname,pages,text_pages,digest))
     record_classification(c,id,*catalogue_fields)
   except Exception:file.unlink(missing_ok=True);raise
   return self.send(200,jdata({'ok':True,'id':id,'pages':pages,'text_pages':text_pages,'vendor_label':vendor,'note':'Характеристики не извлекаются автоматически'}))
  if path=='/api/report':
   from catalog_types_v8_11 import report_pair
   if not report_pair(DB):return self.fail(409,'Сравнение доступно после подтверждения одного типа для обоих каталогов.')
   data=json.loads(self.readbody().decode());material=data.get('material','Cu');rating=int(data.get('rating',4000))
   if material not in ('Cu','Al') or not 400<=rating<=7500:raise ValueError('Недопустимый материал или номинал')
   target=REPORTS/f'comparison_{material}_{rating}.html';REPORTS.mkdir(exist_ok=True)
   subprocess.run([sys.executable,str(ROOT/'catalog_engine.py'),'report','--material',material,'--rating',str(rating),'--out',str(target)],check=True,timeout=20,capture_output=True)
   prefix=catalog_workspace.MOUNT.rstrip('/') if getattr(self,'catalog_public',False) else ''
   return self.send(200,jdata({'ok':True,'basename':target.stem,'links':{ext:prefix+'/api/reports/'+target.stem+'.'+ext for ext in ('html','csv','json')}}))
  self.fail(404,'Не найдено')

def main():
 if not DB.is_file():sys.exit('Не найдена существующая база v1: '+str(DB))
 server=ThreadingHTTPServer(('127.0.0.1',8137),Handler)
 print('Catalog Intelligence v2: http://127.0.0.1:8137 (SSH tunnel only)',flush=True)
 server.serve_forever()
if __name__=='__main__':main()
