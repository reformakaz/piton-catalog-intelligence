"""Conservative extraction of Isolsbarra two-panel technical PDF tables; preview only."""
import pathlib,re,sqlite3
import pymupdf
ROOT=pathlib.Path(__file__).resolve().parent
DB=ROOT/'catalogs.sqlite3'
KEYS={'Ui':('nominal_voltage_v','V'),'Icw':('icw_1s_ka','kA'),'Ipk':('ipk_ka','kA'),'R20':('r20_mohm_m','mΩ/m')}
# Captures only the first phase Icw/Ipk, never the neutral rows.
NEXT={'Ui':'Частота','Icw':'Ток  динамической стойкости','Ipk':'Ток термической стойкости\nнейтрали','R20':'Реактивное сопротивление фазы'}
HEAD={'Ui':'Напряжение изоляции','Icw':'Ток термической стойкости(1c)','Ipk':'Ток  динамической стойкости','R20':'Активное сопротивление фазы (20°C)'}
def row_numbers(text,symbol,count):
 start=text.find(HEAD[symbol]);end=text.find(NEXT[symbol],start+1)
 if start<0 or end<0: return None
 chunk=text[start:end]
 m=re.search(r'(?m)^'+re.escape(symbol)+r'\s*\n\[[^\n]+\]\s*\n',chunk)
 if not m:return None
 nums=[]
 for s in chunk[m.end():].splitlines():
  s=s.strip()
  if not re.fullmatch(r'\d+(?:[,.]\d+)?',s):break
  nums.append(float(s.replace(',','.')))
 return nums if len(nums)==count else None

def candidates(docid):
 with sqlite3.connect(DB) as c:
  row=c.execute('SELECT filename,title,pages FROM documents WHERE id=?',(docid,)).fetchone()
 if not row: raise ValueError('Документ не найден')
 fn,title,pages=row; file=(ROOT/'catalogs'/fn).resolve()
 if file.parent!=(ROOT/'catalogs').resolve() or not file.is_file():raise ValueError('PDF отсутствует')
 document=pymupdf.open(file)
 try:
  # Strict layout detection: only known wide Graziadio technical pages.
  proposals=[];observations=[]
  for idx,p in enumerate(document):
   if idx>30:break
   if p.rect.width<900:continue
   for left,right,mat in [(0,.49,'Al'),(.51,1,'Cu')]:
    text=p.get_text(clip=pymupdf.Rect(p.rect.width*left,0,p.rect.width*right,p.rect.height))
    if 'ISOLSBARRA' not in text or 'R20' not in text or 'Icw' not in text or 'Ipk' not in text:continue
    start=text.find('Номинальный ток');stop=text.find('Номинальное напряжение',start+1)
    if start<0 or stop<0:continue
    segment=text[start:stop]
    a=re.search(r'(?m)^In\s*\n\[A\]\s*\n',segment)
    if not a:continue
    rates=[]
    for z in segment[a.end():].splitlines():
     if not re.fullmatch(r'\d{3,5}',z.strip()):break
     rates.append(int(z.strip()))
    if not (8<=len(rates)<=16 and len(set(rates))==len(rates) and 400<=min(rates) and max(rates)<=7500):continue
    extracted={k:row_numbers(text,k,len(rates)) for k in KEYS}
    if not all(extracted.values()):continue
    for k,values in extracted.items():
     parameter,unit=KEYS[k]
     for rating,value in zip(rates,values):
      proposals.append({'vendor':'graziadio','product':'Graziadio ISOLSBARRA','material':mat,'rating_a':rating,'parameter':parameter,'value':value,'unit':unit,'source':f'{title}, PDF стр. {idx+1}, {"левая" if mat=="Al" else "правая"} таблица','status':'manual_review','page':idx+1})
    observations.append({'page':idx+1,'material':mat,'ratings':rates,'rows':len(rates)*len(KEYS)})
  return {'document':{'id':docid,'title':title,'pages':pages},'proposals':proposals,'tables':observations,'scan_note':('Автоматически сопоставимые технические таблицы не обнаружены. Для PDF из изображений требуется отдельное OCR и инженерная проверка.' if not proposals else 'Таблицы извлечены только по строгому шаблону ISOLSBARRA. Нужна проверка инженером.'),'supported_parameters':list(KEYS)}
 finally:document.close()
