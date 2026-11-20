"""Catalog Intelligence v6: fixed-layout, SHA-guarded OCR of four known E3 pages.
All results are unverified drafts. No DB writes. Python 3.10 compatible.
"""
import hashlib,pathlib,sqlite3,shutil
import pymupdf
from PIL import Image
from scan_v5 import cell, KNOWN_PDF_SHA256
ROOT=pathlib.Path(__file__).resolve().parent
TABLES={
 60:('Al',[400,630,800,1000,1250,1600],[613,708,803,898,992,1087],468),
 61:('Al',[2000,2500,3200,4000,5000,6400],[613,708,803,898,992,1087],468),
 62:('Cu',[630,800,1000,1250,1600,2000],[613,708,803,898,992,1087],468),
 63:('Cu',[2500,3200,4000,5000,6400],[650,755,858,960,1060],465),
}
ROWS=[('nominal_voltage_v','V',530),('icw_1s_ka','kA',634),('ipk_ka','kA',690),('r20_mohm_m','mΩ/m',738)]
def valid(k,v):
 return v is not None and ((k=='nominal_voltage_v' and v==1000) or (k=='icw_1s_ka' and 10<=v<=500) or (k=='ipk_ka' and 20<=v<=1000) or (k=='r20_mohm_m' and .0001<=v<=.5))
def candidates(docid):
 if not shutil.which('tesseract'):raise RuntimeError('Установите tesseract-ocr')
 with sqlite3.connect(ROOT/'catalogs.sqlite3') as cn:
  row=cn.execute('SELECT filename,title,pages FROM documents WHERE id=?',(docid,)).fetchone()
 if not row:raise ValueError('Каталог не найден')
 filename,title,pages=row
 if docid!='piton' or filename!='piton.pdf':raise ValueError('OCR v6 поддерживает только исходный PitON E3')
 path=(ROOT/'catalogs'/filename).resolve()
 if not path.is_file() or path.parent!=(ROOT/'catalogs').resolve():raise ValueError('PDF не найден')
 digest=hashlib.sha256(path.read_bytes()).hexdigest()
 if digest!=KNOWN_PDF_SHA256:raise ValueError('SHA-256 каталога не совпадает с эталоном: распознавание остановлено. Требуется калибровка нового PDF.')
 proposals=[];rejected=[];diagnostics=[]
 with pymupdf.open(path) as pdf:
  if len(pdf)<63:raise ValueError('В PDF менее 63 страниц')
  for page_no,(material,ratings,xs,header_y) in TABLES.items():
   page=pdf[page_no-1]
   if abs(page.rect.width-623.622)>1 or abs(page.rect.height-870.236)>1:raise ValueError('Изменилась геометрия страницы '+str(page_no))
   pix=page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False)
   if abs(pix.width-1248)>5 or abs(pix.height-1741)>5:raise ValueError('Неожиданный размер растра страницы '+str(page_no))
   im=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
   headers=[cell(im,x,header_y) for x in xs]
   if headers!=[float(r) for r in ratings]:
    diagnostics.append({'page':page_no,'error':'Заголовки номиналов не совпали','recognized':headers,'expected':ratings})
    for key,unit,y in ROWS:
     for rating in ratings:rejected.append({'page':page_no,'material':material,'rating_a':rating,'parameter':key,'ocr':None,'reason':'header_mismatch'})
    continue
   diagnostics.append({'page':page_no,'material':material,'ratings':len(ratings),'headers':'ok'})
   for key,unit,y in ROWS:
    for rating,x in zip(ratings,xs):
     v=cell(im,x,y)
     if not valid(key,v):
      rejected.append({'page':page_no,'material':material,'rating_a':rating,'parameter':key,'ocr':v,'reason':'unreadable_or_out_of_range'})
      continue
     proposals.append({'vendor':'piton','product':'PitON E3','material':material,'rating_a':rating,'parameter':key,'value':v,'unit':unit,'source':f'{title}, PDF стр. {page_no}, OCR v6 (требует инженерной сверки)','status':'manual_review','page':page_no})
 return {'document':{'id':docid,'title':title},'proposals':proposals,'rejected':rejected,'diagnostics':diagnostics,'total_cells':sum(len(r[1])*len(ROWS) for r in TABLES.values()),'note':'Шаблон только для проверенного PDF PitON E3, страницы 60–63. Все значения предварительные, автозапись запрещена.'}
