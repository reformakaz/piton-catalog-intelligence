"""Conservative OCR proof of concept for PitON E3 Cu high-current technical table, PDF page 63.
All outputs are unverified proposals, never database writes. Tesseract invoked only on fixed small cells.
"""
import io,pathlib,re,shutil,sqlite3,subprocess,hashlib
import pymupdf
from PIL import Image
ROOT=pathlib.Path(__file__).resolve().parent
DB=ROOT/'catalogs.sqlite3'
PAGE=63
KNOWN_PDF_SHA256="4c08dfb2c281f4029a4b253a89f73594cdb9ce2f4d47f555d90943275eaf9d91"
RATINGS=[2500,3200,4000,5000,6400]
# Coordinates calibrated on 2x raster of original 623.6 x 870.2 PDF page.
X=[650,755,858,960,1060]
ROWS=[('nominal_voltage_v','V',530),('icw_1s_ka','kA',634),('ipk_ka','kA',690),('r20_mohm_m','mΩ/m',738)]

def cell(im,x,y):
 crop=im.crop((x-44,y-18,x+44,y+18)).resize((370,180))
 buf=io.BytesIO();crop.save(buf,format='PNG')
 cmd=['tesseract','stdin','stdout','--psm','7','-l','eng','-c','tessedit_char_whitelist=0123456789,.']
 try:r=subprocess.run(cmd,input=buf.getvalue(),capture_output=True,timeout=8,check=False)
 except subprocess.TimeoutExpired:return None
 if r.returncode:return None
 txt=r.stdout.decode('utf-8','replace').strip().replace(' ','').replace(',','.')
 if not re.fullmatch(r'\d+(?:\.\d+)?',txt):return None
 try:return float(txt)
 except ValueError:return None

def candidates(docid):
 if not shutil.which('tesseract'):raise RuntimeError('На сервере не установлен tesseract-ocr')
 with sqlite3.connect(DB) as c:
  doc=c.execute('SELECT filename,title,pages FROM documents WHERE id=?',(docid,)).fetchone()
 if not doc:raise ValueError('Документ не найден')
 filename,title,pages=doc
 if docid!='piton' or filename!='piton.pdf':raise ValueError('OCR-шаблон разрешён только для исходного каталога PitON E3 (id=piton)')
 path=(ROOT/'catalogs'/filename).resolve()
 if path.parent!=(ROOT/'catalogs').resolve() or not path.is_file():raise ValueError('Документ недоступен')
 with pymupdf.open(path) as pdf:
  if len(pdf)<PAGE:raise ValueError('Для пилота требуется PDF со страницей 63')
  page=pdf[PAGE-1]
  if abs(page.rect.width-623.622)>15 or abs(page.rect.height-870.236)>15:raise ValueError('Неожиданный размер страницы; OCR-шаблон неприменим')
  pix=page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
  im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
  if abs(im.width-1248)>5 or abs(im.height-1741)>5:raise ValueError('Изменилась сетка изображения')
  # PITON_CATALOG_OCR_SHA_GUARD_V5_1: Header OCR is diagnostic only.
  # Exact PDF bytes plus page size/raster geometry are the layout identity guard.
  # OCR header recognition depends on local Tesseract data and must not decide layout.
  header=[cell(im,x,465) for x in X]
  expected=[float(v) for v in RATINGS]
  if header!=expected:
   matched=[(got is not None and got==want) for got,want in zip(header,expected)]
   if sum(matched)<5:
    # Never trust fixed table coordinates for a different source revision.
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    if digest!=KNOWN_PDF_SHA256:
     return {'document':{'id':docid,'title':title},'page':PAGE,'proposals':[],
      'note':'Макет PDF отличается от проверенного эталона; OCR остановлен.',
      'header_recognized':header,'pdf_sha256':digest,'expected_sha256':KNOWN_PDF_SHA256}
  proposals=[];rejected=[]
  for key,unit,y in ROWS:
   values=[cell(im,x,y) for x in X]
   for rating,v in zip(RATINGS,values):
    if v is None or (key=='nominal_voltage_v' and v!=1000) or (key=='icw_1s_ka' and not (10<=v<=500)) or (key=='ipk_ka' and not (20<=v<=1000)) or (key=='r20_mohm_m' and not (.0001<=v<=.1)):
     rejected.append({'parameter':key,'rating_a':rating,'ocr':v});continue
    proposals.append({'vendor':'piton','product':'PitON E3','material':'Cu','rating_a':rating,
    'parameter':key,'value':v,'unit':unit,'source':f'{title}, PDF стр. {PAGE}, таблица Cu 2500–6400 А, OCR — требует сверки','status':'manual_review','page':PAGE})
  return {'document':{'id':docid,'title':title},'page':PAGE,'proposals':proposals,
   'rejected':rejected,'header_recognized':header,'layout_guard':'known_sha256' if header!=expected else 'exact_header',
   'note':'Экспериментальный шаблон только для таблицы Cu 2500–6400 А на стр. 63 PDF. Все результаты OCR требуют визуальной проверки по оригиналу, даже при отсутствии пропусков.'}
