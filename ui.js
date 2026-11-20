const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api = path => location.pathname === '/catalog/' ? '/catalog' + path : path;
async function req(path,options){const r=await fetch(api(path),options);const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||'HTTP '+r.status);return d}
const tabs = document.querySelectorAll('[data-tab]');
for (const tab of tabs) {
  tab.addEventListener('click', event => {
    if (tab.tagName === 'A') {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      history.replaceState(null, '', tab.href);
    }
    for (const button of tabs) {
      button.classList.toggle('active', button === tab);
      if (button.tagName === 'A') {
        if (button === tab) button.setAttribute('aria-current', 'page');
        else button.removeAttribute('aria-current');
      }
    }
    for (const id of ['compare', 'library', 'importer', 'extractor']) {
      $('#' + id).classList.toggle('hidden', id !== tab.dataset.tab);
    }
    if (tab.dataset.tab === 'library') load();
    if (tab.dataset.tab === 'extractor') loadExtractDocs();
  });
}
const CATALOG_TYPE_LABELS = {shp:'Шинопровод',tp:'Токопровод'};
let libraryCatalogues = new Map();
let editingCatalogue = null;
let openSpecs = null;
let catalogueParameters = {};
const specColumns = new Map([
  ['nominal_voltage_v','Ui'], ['icw_1s_ka','Icw, 1 с'], ['ipk_ka','Ipk'],
  ['r20_mohm_m','R20 при 20 °C'], ['dimensions_mm','Секция Ш × В'],
  ['phase_area_mm2','Сечение фазы'], ['phase_width_mm','Шина Ш'], ['phase_height_mm','Шина В'],
  ['mass_kg_m','Масса 1 пог. м'], ['insulation_type','Тип изоляции'],
]);
const serviceColumns = new Map([['@source','Источники'], ['@status','Статусы']]);
const pendingStatuses = new Set(['manual_review','ocr','unverified','pending']);
const statusLabels = new Map([['manual_review','Требует сверки'], ['ocr','Распознано из PDF'],
  ['unverified','Не проверено'], ['pending','На проверке'], ['catalog','По каталогу'],
  ['source_document','По указанному источнику']]);
const columnSettings = 'piton-catalogue-spec-columns';
let specPreferences = new Map();
try {
  const saved = JSON.parse(window.localStorage.getItem(columnSettings));
  if (Array.isArray(saved)) specPreferences = new Map(saved.filter(item =>
    Array.isArray(item) && typeof item[0] === 'string' && typeof item[1] === 'boolean'));
} catch (_) {}
const catalogueEditor = $('#catalog-editor');
const catalogueSave = $('#catalog-save-type');
const catalogueCancel = $('#catalog-editor-cancel');

function libraryStatus(text) {
  const status = $('#catalog-library-status');
  status.textContent = text;
  status.hidden = !text;
}

function editorStatus(text) {
  const status = $('#catalog-type-status');
  status.textContent = text;
  status.hidden = !text;
}

async function load() {
  try {
    const [library, metadata] = await Promise.all([req('/api/library'),req('/api/catalog-types')]);
    const types = new Map(metadata.catalogues.map(row => [String(row.document_id),row]));
    const specsByCatalogue = new Map();
    const fromDatabase = new Set();
    for (const [records,supplemental] of [[library.specs,false],[library.physical,true]]) {
      for (const row of records) {
        if (row.value == null || row.value === '') continue;
        const key = JSON.stringify([row.vendor,row.product]);
        const specKey = JSON.stringify([key,row.material,row.rating_a,row.parameter]);
        if (supplemental && fromDatabase.has(specKey)) continue;
        if (!supplemental) fromDatabase.add(specKey);
        if (!specsByCatalogue.has(key)) specsByCatalogue.set(key,[]);
        specsByCatalogue.get(key).push(row);
      }
    }
    catalogueParameters = library.parameters;
    const expanded = openSpecs && {
      id:openSpecs.id,
      material:openSpecs.details.querySelector('#catalog-spec-material')?.value,
      rating:openSpecs.details.querySelector('#catalog-spec-rating')?.value,
    };
    openSpecs = null;
    libraryCatalogues = new Map(library.documents.map(doc => {
      const id = String(doc.id);
      const type = types.get(id);
      return [id,{
        id,title:doc.title || '',vendor:type?.vendor || doc.vendor || '',
        catalogType:type?.catalog_type || '',
        specs:specsByCatalogue.get(JSON.stringify([type?.spec_vendor,type?.spec_product])) || [],
      }];
    }));
    $('#libStats').innerHTML = '<div class="status">Документов: <b>'+library.documents.length+'</b></div>';
    $('#docs').innerHTML = library.documents.map(doc => {
      const id = String(doc.id);
      const type = CATALOG_TYPE_LABELS[libraryCatalogues.get(id).catalogType] || 'Тип не указан';
      return '<tr><td><strong>'+esc(doc.title || id)+'</strong><br><small>'+esc(doc.created_at || '')+
        '</small></td><td>'+esc(type)+'</td><td>'+esc(doc.pages)+'</td><td>'+esc(doc.text_pages)+' / '+
        esc(doc.pages)+'</td><td><div class="catalog-row-actions">'+
        '<a class="catalog-secondary" target="_blank" rel="noopener noreferrer" href="'+api('/api/pdf/'+encodeURIComponent(id))+'">Открыть PDF ↗</a>'+
        '<button type="button" class="catalog-secondary" data-catalog-specs="'+esc(id)+
        '" aria-expanded="false" aria-label="Характеристики '+esc(doc.title || id)+'">Характеристики</button>'+
        '<button type="button" class="catalog-secondary" data-catalog-edit="'+esc(id)+
        '" aria-label="Редактировать каталог '+esc(doc.title || id)+'">Редактировать</button></div></td></tr>';
    }).join('') || '<tr><td colspan="5">Документы не найдены</td></tr>';
    if (expanded) {
      const button = [...$('#docs').querySelectorAll('[data-catalog-specs]')].find(item=>item.dataset.catalogSpecs === expanded.id);
      if (button) showCatalogueSpecs(button,expanded);
    }
    return true;
  } catch (e) {
    $('#libStats').textContent = 'Ошибка: '+e.message;
    return false;
  }
}

function closeCatalogueSpecs(restoreFocus = true) {
  if (!openSpecs) return;
  const {button, details} = openSpecs;
  openSpecs = null;
  button.setAttribute('aria-expanded','false');
  button.removeAttribute('aria-controls');
  details.remove();
  if (restoreFocus) button.focus();
}

function catalogueSpecColumns(specs) {
  const columns = new Map(specColumns);
  for (const {parameter} of specs) {
    if (!columns.has(parameter)) columns.set(parameter,catalogueParameters[parameter] || parameter);
  }
  for (const [code,label] of serviceColumns) columns.set(code,label);
  return columns;
}

function catalogueSpecValue(entries) {
  if (!entries?.length) return '<span class="catalog-spec-empty">—</span>';
  const values = [...new Set(entries.map(row => [row.value,row.unit]
    .filter(value=>value != null && value !== '').map(value=>
      typeof value === 'number' ? value.toLocaleString('ru-RU',{maximumFractionDigits:12,useGrouping:false}) : value
    ).join(' ')))];
  const review = entries.some(row=>pendingStatuses.has(String(row.status).toLowerCase())) ?
    '<sup class="catalog-spec-review" aria-label="Требует сверки">*</sup>' : '';
  return values.length === 1 ? esc(values[0])+review : '<span class="catalog-spec-conflict">'+
    esc(values.join(' / '))+'</span><small>Уточнить значение</small>';
}

function renderCatalogueSpecsRows(panel, specs, columns, preferredRating) {
  const material = panel.querySelector('#catalog-spec-material').value;
  const rating = panel.querySelector('#catalog-spec-rating');
  const priorRating = preferredRating ?? rating.value;
  const matchingMaterial = specs.filter(row => !material || row.material === material);
  const ratings = [...new Set(matchingMaterial.map(row=>String(row.rating_a ?? '')).filter(Boolean))]
    .sort((a,b)=>Number(a)-Number(b));
  rating.innerHTML = '<option value="">Все номиналы</option>'+ratings.map(amps=>
    '<option value="'+esc(amps)+'">'+esc(amps)+' А</option>').join('');
  rating.value = ratings.includes(priorRating) ? priorRating : '';

  const byVariant = new Map();
  for (const row of matchingMaterial) {
    if (rating.value && String(row.rating_a) !== rating.value) continue;
    const key = JSON.stringify([row.material,row.rating_a]);
    if (!byVariant.has(key)) byVariant.set(key,{material:row.material,rating:row.rating_a,specs:new Map()});
    const parameters = byVariant.get(key).specs;
    if (!parameters.has(row.parameter)) parameters.set(row.parameter,[]);
    parameters.get(row.parameter).push(row);
  }
  const checkboxes = [...panel.querySelectorAll('[data-spec-column]')];
  const visible = checkboxes.filter(input=>input.checked).map(input=>input.value);
  const minWidth = visible.reduce((total,code)=>total +
    (serviceColumns.has(code) || code === 'insulation_type' ? 160 : 90), 160);
  panel.querySelector('.catalog-spec-table').style.setProperty('--catalog-spec-min-width',minWidth+'px');
  panel.querySelector('#catalog-spec-headers').innerHTML =
    '<th scope="col">Материал</th><th scope="col">Номинал</th>'+visible.map(code=>
      '<th scope="col" title="'+esc(catalogueParameters[code] || columns.get(code))+'">'+esc(columns.get(code))+'</th>').join('');
  const body = panel.querySelector('#catalog-spec-values');
  const rows = [...byVariant.values()].sort((a,b)=>String(a.material).localeCompare(String(b.material)) || a.rating-b.rating);
  panel.querySelector('.catalog-spec-legend').hidden = !rows.some(row=>visible.some(code=>
    row.specs.get(code)?.some(item=>pendingStatuses.has(String(item.status).toLowerCase()))));
  body.innerHTML = rows.map(row => {
    const entries = [...row.specs.values()].flat();
    const notes = [...new Set(entries.map(item=>item.variant_note).filter(Boolean))];
    const cells = visible.map(code => {
      if (!serviceColumns.has(code)) return '<td>'+catalogueSpecValue(row.specs.get(code))+'</td>';
      const field = code === '@source' ? 'source' : 'status';
      const values = new Map(entries.filter(item=>item[field]).map(item=>[item[field],item]));
      const texts = [...values].map(([value,item])=>'<div>'+
        (field === 'source' && item.source_url ?
          '<a target="_blank" rel="noopener noreferrer" href="'+esc(item.source_url)+'">'+esc(value)+'</a>' :
          esc(field === 'status' ? statusLabels.get(value) || value : value))+'</div>');
      return '<td class="catalog-spec-service">'+(texts.join('') || '—')+'</td>';
    });
    return '<tr><td>'+esc(row.material)+'</td><th scope="row">'+
      esc(row.rating == null ? '—' : row.rating+' А')+notes.map(note=>'<small>'+esc(note)+'</small>').join('')+
      '</th>'+cells.join('')+'</tr>';
  }).join('') || '<tr><td colspan="'+(visible.length+2)+'">По выбранным фильтрам данных нет.</td></tr>';
}

function showCatalogueSpecs(button, filters = {}) {
  const id = button.dataset.catalogSpecs;
  const selection = libraryCatalogues.get(id);
  if (!selection) return;
  closeCatalogueSpecs(false);
  const {title, specs} = selection;
  const columns = catalogueSpecColumns(specs);
  const variant = specs.find(row=>row.variant)?.variant;
  const materials = [...new Set(specs.map(row=>row.material).filter(Boolean))].sort();
  const details = document.createElement('tr');
  details.id = 'catalog-spec-details';
  details.className = 'catalog-spec-details';
  let content;
  if (!specs.length) {
    content = '<p class="catalog-spec-note">Для этого каталога пока нет импортированных характеристик.</p>';
  } else {
    content = '<div class="catalog-spec-filters">'+
      '<div><label for="catalog-spec-material">Материал</label><select id="catalog-spec-material">'+
      '<option value="">Все материалы</option>'+materials.map(name=>
        '<option value="'+esc(name)+'">'+esc(name)+'</option>').join('')+'</select></div>'+
      '<div><label for="catalog-spec-rating">Номинальный ток</label><select id="catalog-spec-rating"></select></div>'+
      '<details class="catalog-spec-column-picker"><summary>Колонки</summary><div class="catalog-spec-column-options">'+
      [...columns].map(([code,label])=>'<label><input type="checkbox" data-spec-column value="'+esc(code)+'"'+
        ((specPreferences.get(code) ?? !serviceColumns.has(code)) ? ' checked' : '')+'> '+esc(label)+'</label>').join('')+
      '</div></details></div>'+
      (variant ? '<p class="catalog-spec-variant">'+esc(variant)+'.</p>' : '')+
      '<div class="catalog-spec-scroll" tabindex="0" role="region" aria-label="Таблица характеристик">'+
      '<table class="catalog-spec-table"><thead><tr id="catalog-spec-headers"></tr></thead>'+
      '<tbody id="catalog-spec-values"></tbody></table></div>'+
      '<p class="catalog-spec-legend" hidden>* Требует сверки с источником.</p>';
  }
  details.innerHTML = '<td colspan="5"><section aria-labelledby="catalog-spec-heading">'+
    '<h3 id="catalog-spec-heading">Характеристики'+(title ? ' · '+esc(title) : '')+'</h3>'+content+
    '<div class="catalog-spec-actions"><button class="catalog-secondary" type="button" data-catalog-collapse>'+
    'Свернуть характеристики</button></div></section></td>';
  button.closest('tr').after(details);
  button.setAttribute('aria-expanded','true');
  button.setAttribute('aria-controls',details.id);
  openSpecs = {id,button,details};
  const material = details.querySelector('#catalog-spec-material');
  if (!material) return;
  const rating = details.querySelector('#catalog-spec-rating');
  material.value = materials.includes(filters.material) ? filters.material : '';
  const render = prior => renderCatalogueSpecsRows(details,specs,columns,prior);
  render(filters.rating);
  material.addEventListener('change',()=>render());
  rating.addEventListener('change',()=>render());
  details.querySelector('.catalog-spec-column-options').addEventListener('change',event=>{
    const control = event.target;
    if (!control.hasAttribute('data-spec-column')) return;
    specPreferences.set(control.value,control.checked);
    try { window.localStorage.setItem(columnSettings,JSON.stringify([...specPreferences])); } catch (_) {}
    render();
  });
}

$('#docs').addEventListener('click', event => {
  if (event.target.closest('[data-catalog-collapse]')) {
    closeCatalogueSpecs();
    return;
  }
  const specsButton = event.target.closest('[data-catalog-specs]');
  if (specsButton) {
    if (openSpecs?.button === specsButton) closeCatalogueSpecs();
    else showCatalogueSpecs(specsButton);
    return;
  }
  const button = event.target.closest('[data-catalog-edit]');
  if (!button || catalogueEditor.open) return;
  const row = libraryCatalogues.get(button.dataset.catalogEdit);
  if (!row) return;
  editingCatalogue = row;
  $('#catalog-existing-title').value = row.title;
  $('#catalog-existing-type').value = row.catalogType;
  $('#catalog-existing-vendor').value = row.vendor;
  libraryStatus('');
  editorStatus('');
  catalogueEditor.showModal();
});

function closeCatalogueEditor() {
  const id = editingCatalogue?.id;
  const button = [...$('#docs').querySelectorAll('[data-catalog-edit]')].find(item => item.dataset.catalogEdit === id);
  editingCatalogue = null;
  catalogueEditor.close();
  if (button) button.focus();
}

catalogueCancel.addEventListener('click', closeCatalogueEditor);
catalogueEditor.addEventListener('cancel', event => {
  event.preventDefault();
  if (!catalogueSave.disabled) closeCatalogueEditor();
});

$('#catalog-editor-form').addEventListener('submit',async event => {
  event.preventDefault();
  const current = editingCatalogue;
  if (!current || catalogueSave.disabled) return;
  const title = $('#catalog-existing-title').value.trim();
  const vendor = $('#catalog-existing-vendor').value.trim();
  const catalogType = $('#catalog-existing-type').value;
  const renaming = title !== current.title.trim();
  const reclassifying = vendor !== current.vendor || catalogType !== current.catalogType;
  if (renaming && !title) {
    editorStatus('Укажите название каталога.');
    return;
  }
  if (reclassifying && !catalogType) {
    editorStatus('Выберите тип, чтобы сохранить код производителя.');
    return;
  }
  if (!renaming && !reclassifying) {
    closeCatalogueEditor();
    return;
  }
  const q = new URLSearchParams({document:current.id});
  if (renaming) q.set('title',title);
  if (reclassifying) {
    q.set('vendor',vendor);
    q.set('catalog_type',catalogType);
  }
  catalogueSave.disabled = catalogueCancel.disabled = true;
  editorStatus('');
  try {
    await req('/api/catalog-type?'+q,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const refreshed = await load();
    libraryStatus(refreshed ? 'Изменения сохранены.' : 'Изменения сохранены. Откройте библиотеку ещё раз, чтобы обновить список.');
    closeCatalogueEditor();
  } catch (e) {
    editorStatus('Ошибка: '+e.message);
  } finally {
    catalogueSave.disabled = catalogueCancel.disabled = false;
  }
});

$('#run').addEventListener('click',async()=>{const b=$('#run');b.disabled=true;$('#compareStatus').textContent='Формирование отчёта…';try{const d=await req('/api/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({material:$('#material').value,rating:Number($('#rating').value)})});$('#download').innerHTML=Object.entries(d.links).map(([ext,url])=>`<a href="${url}" ${ext==='html'?'target="_blank" rel="noopener noreferrer"':'download'}>${ext.toUpperCase()} ↗</a>`).join('');$('#preview').src=d.links.html;$('#results').classList.remove('hidden');$('#compareStatus').textContent='Отчёт готов. Отсутствующие данные обозначены отдельно.';}catch(e){$('#compareStatus').textContent='Ошибка: '+e.message}finally{b.disabled=false}});
$('#upload').addEventListener('click',async()=>{const file=$('#file').files[0],vendor=$('#vendor').value.trim(),title=$('#title').value.trim();const catalog_type=$('#catalog-type').value;if(!file||!title||!vendor||!catalog_type){$('#uploadStatus').textContent='Укажите тип, производителя, название и PDF';return}const btn=$('#upload');btn.disabled=true;$('#uploadStatus').textContent='Загрузка и индексация…';try{const q=new URLSearchParams({vendor,title,catalog_type});const d=await req('/api/upload?'+q,{method:'POST',headers:{'Content-Type':'application/pdf'},body:file});$('#uploadStatus').textContent=`Загружено: ${d.pages} страниц, текстовый слой на ${d.text_pages}. Параметры автоматически не извлечены.`;$('#file').value='';await load()}catch(e){$('#uploadStatus').textContent='Ошибка: '+e.message}finally{btn.disabled=false}});
const initialSection = new URLSearchParams(location.search).get('workspace');
const initialTab = Array.from(document.querySelectorAll('[data-tab]')).find(button => button.dataset.tab === initialSection);
if (initialTab) initialTab.click();
if (initialSection !== 'library') load();

let validatedBytes=null;
$('#specfile').addEventListener('change',()=>{validatedBytes=null;$('#specApply').disabled=true;$('#specStatus').textContent='CSV выбран. Нажмите «Проверить CSV».'});
$('#specPreview').addEventListener('click',async()=>{const f=$('#specfile').files[0];if(!f){$('#specStatus').textContent='Выберите файл CSV';return}$('#specApply').disabled=true;validatedBytes=null;try{const bytes=await f.arrayBuffer();const d=await req('/api/spec-import?apply=0',{method:'POST',headers:{'Content-Type':'text/csv'},body:bytes});$('#specStatus').textContent=d.ok?`Проверено: ${d.rows} ${d.kind==='phase'?'значений':'строк'}, новых ${d.new}, без изменений ${d.same}, изменяемых ${d.changed}. ${d.warning}`:`Найдены ошибки: ${JSON.stringify(d.errors)}`;if(d.ok){validatedBytes=bytes;$('#specApply').disabled=false}}catch(e){$('#specStatus').textContent='Ошибка: '+e.message}});
$('#specApply').addEventListener('click',async()=>{if(!validatedBytes)return;const b=$('#specApply');b.disabled=true;try{const d=await req('/api/spec-import?apply=1',{method:'POST',headers:{'Content-Type':'text/csv'},body:validatedBytes});$('#specStatus').textContent=d.ok?`Записано ${d.rows} ${d.kind==='phase'?'значений':'строк'}. Новых ${d.new}, изменено ${d.changed}. Теперь откройте «Сравнение».`:'Ошибка: '+JSON.stringify(d.errors);validatedBytes=null;await load()}catch(e){$('#specStatus').textContent='Ошибка: '+e.message}});

// PITON_CATALOG_EXTRACT_V4 — preview and explicit CSV review before commit.
let proposed=[], approvedBytes=null;
async function loadExtractDocs(){const d=await req('/api/library');$('#extractDoc').innerHTML=d.documents.map(x=>`<option value="${esc(x.id)}">${esc(x.title||x.id)} (${esc(x.pages)} стр.)</option>`).join('')}
$('#extractStart').addEventListener('click',async()=>{approvedBytes=null;$('#extractActions').classList.add('hidden');$('#extractStatus').textContent='Анализ PDF…';try{let d=await req('/api/extract-v4?document='+encodeURIComponent($('#extractDoc').value));proposed=d.proposals;$('#extractStatus').textContent=`Найдено ${proposed.length} значений; таблиц: ${d.tables.length}. ${d.scan_note}`;if(proposed.length){$('#extractRows').innerHTML=proposed.map((x,i)=>`<tr><td><input type="checkbox" class="extractPick" data-i="${i}" checked></td><td>${esc(x.material)}</td><td>${esc(x.rating_a)} А</td><td>${esc(x.parameter)}</td><td>${esc(x.value)} ${esc(x.unit)}</td><td><a href="${api('/api/pdf/'+encodeURIComponent(d.document.id))}#page=${x.page}" target="_blank" rel="noopener">PDF стр. ${x.page} ↗</a></td></tr>`).join('');$('#extractActions').classList.remove('hidden')}}catch(e){$('#extractStatus').textContent='Ошибка: '+e.message}});
$('#extractAll').addEventListener('change',e=>{document.querySelectorAll('.extractPick').forEach(x=>x.checked=e.target.checked);approvedBytes=null;$('#extractApply').disabled=true});
$('#extractRows').addEventListener('change',()=>{approvedBytes=null;$('#extractApply').disabled=true});
// PITON_OCR_EDITABLE_V5_3: the visible user-verified value is the only value imported.
function ocrReadEditedRows(){
 for(const row of document.querySelectorAll('#extractRows tr[data-ocr-row]')){
  const i=Number(row.dataset.ocrRow), input=row.querySelector('input.ocrValue');
  if(!input||!proposed[i])continue;
  const raw=input.value.trim().replace(',','.');
  if(!raw){proposed[i].value=null;continue;}
  if(!/^(?:\d+)(?:\.\d+)?$/.test(raw))throw Error('Недопустимое число для '+proposed[i].rating_a+' A, '+proposed[i].parameter);
  const value=Number(raw),key=proposed[i].parameter;
  const limits={nominal_voltage_v:[100,2000],icw_1s_ka:[10,500],ipk_ka:[20,1000],r20_mohm_m:[0.0001,0.5]};
  const [lo,hi]=limits[key]||[0,1e8];
  if(!Number.isFinite(value)||value<lo||value>hi)throw Error('Значение вне допустимого диапазона: '+proposed[i].rating_a+' A, '+key);
  proposed[i].value=value;
 }
}
function makeCSV(){ocrReadEditedRows();const selected=[...document.querySelectorAll('.extractPick:checked')].map(x=>proposed[Number(x.dataset.i)]);if(!selected.length)throw Error('Не выбраны характеристики');if(selected.some(x=>x.value===null||x.value===undefined))throw Error('У выбранной строки пустое значение. Сверьте с PDF и введите число.');const fields=['vendor','product','material','rating_a','parameter','value','unit','source','status'];function field(v){return '"'+String(v??'').replaceAll('"','""')+'"'}return new TextEncoder().encode(fields.join(';')+'\n'+selected.map(x=>fields.map(f=>field(x[f])).join(';')).join('\n')+'\n')}
$('#extractCheck').addEventListener('click',async()=>{try{approvedBytes=makeCSV();const d=await req('/api/spec-import?apply=0',{method:'POST',headers:{'Content-Type':'text/csv'},body:approvedBytes});$('#extractStatus').textContent=d.ok?`Проверено ${d.rows} характеристик: новых ${d.new}, изменяемых ${d.changed}, без изменений ${d.same}. Внимание: это не автоматическая инженерная верификация.`:JSON.stringify(d.errors);$('#extractApply').disabled=!d.ok;if(!d.ok)approvedBytes=null}catch(e){$('#extractStatus').textContent='Ошибка: '+e.message}});
$('#extractApply').addEventListener('click',async()=>{if(!approvedBytes)return;$('#extractApply').disabled=true;try{const d=await req('/api/spec-import?apply=1',{method:'POST',headers:{'Content-Type':'text/csv'},body:approvedBytes});$('#extractStatus').textContent=d.ok?`Импортировано ${d.rows} значений со статусом manual_review. Перед использованием у заказчика требуется подтверждение инженера.`:JSON.stringify(d.errors);approvedBytes=null;await load()}catch(e){$('#extractStatus').textContent='Ошибка: '+e.message}});

// PITON_CATALOG_SCAN_V6: four known tables, never auto-import.
$('#scanStart').addEventListener('click',async()=>{
 approvedBytes=null;$('#extractApply').disabled=true;$('#extractActions').classList.add('hidden');
 $('#extractStatus').textContent='OCR обрабатывает таблицы PitON E3 на страницах PDF 60–63. Подождите…';
 const b=$('#scanStart');b.disabled=true;
 try {
  const d=await req('/api/scan-v6?document='+encodeURIComponent($('#extractDoc').value));
  proposed=d.proposals||[];
  $('#extractStatus').textContent=`OCR: найдено ${proposed.length} характеристик; пропущено ${(d.rejected||[]).length}. ${d.note}`;
  // Include rejected OCR cells as empty manual proposals. Never infer a missing number.
  const existing=new Set(proposed.map(x=>x.material+'|'+x.rating_a+'|'+x.parameter));
  for(const miss of d.rejected||[]){
    const code=miss.material+'|'+miss.rating_a+'|'+miss.parameter;
    if(existing.has(code))continue;
    const unit={nominal_voltage_v:'V',icw_1s_ka:'kA',ipk_ka:'kA',r20_mohm_m:'mΩ/m'}[miss.parameter];
    if(!unit)continue;
    existing.add(code);
    proposed.push({vendor:'piton',product:'PitON E3',material:miss.material,rating_a:miss.rating_a,
      parameter:miss.parameter,value:null,unit,source:d.document.title+', PDF стр. '+miss.page+', восстановлено вручную по оригиналу',
      status:'manual_review',page:miss.page,manual:true});
  }
  if(proposed.length){
   $('#extractRows').innerHTML=proposed.map((x,i)=>`<tr data-ocr-row="${i}"><td><input type="checkbox" class="extractPick" data-i="${i}"></td><td>${esc(x.material)}</td><td>${esc(x.rating_a)} А</td><td>${esc(x.parameter)}</td><td><input class="ocrValue" type="text" inputmode="decimal" aria-label="${esc(x.parameter)} ${esc(x.rating_a)} А" value="${x.value===null?'':esc(x.value)}" placeholder="Сверьте с PDF" style="min-width:110px;width:130px;${x.manual?'border-color:#cc8530;background:#fff7e6;':''}"> ${esc(x.unit)}${x.manual?' <small style="color:#996316">пропущено OCR</small>':''}</td><td><a href="${api('/api/pdf/'+encodeURIComponent(d.document.id))}#page=${x.page}" target="_blank" rel="noopener">PDF стр. ${x.page} ↗</a></td></tr>`).join('');
   $('#extractAll').checked=false;$('#extractActions').classList.remove('hidden');
   $('#extractStatus').textContent+=` Показано ${proposed.length} строк, включая пустые для исправления. Все флажки сняты.`;
  }
 }catch(e){$('#extractStatus').textContent='Ошибка OCR: '+e.message}
 finally{b.disabled=false}
});

// PITON_OCR_EDITABLE_V5_3 invalidate preview when any value is edited.
$('#extractRows').addEventListener('input',e=>{if(e.target.classList.contains('ocrValue')){approvedBytes=null;$('#extractApply').disabled=true;$('#extractStatus').textContent='Значение изменено. Сверьте с PDF, выберите строки и повторите «Проверить выбранное».';}});
