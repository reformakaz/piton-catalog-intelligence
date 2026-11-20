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
    for (const id of ['compare', 'library', 'characteristics']) {
      $('#' + id).classList.toggle('hidden', id !== tab.dataset.tab);
    }
    if (tab.dataset.tab === 'library') load();
    if (tab.dataset.tab === 'characteristics') loadCharacteristicCatalogues();
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
    const variantsByDocument = new Map();
    for (const row of library.variants || []) {
      const id = String(row.document_id);
      if (!variantsByDocument.has(id)) variantsByDocument.set(id,[]);
      const source_url = row.page ? api('/api/pdf/' + encodeURIComponent(id)) + '#page=' + row.page : '';
      variantsByDocument.get(id).push({...row,source_url});
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
        specs:[...(specsByCatalogue.get(JSON.stringify([type?.spec_vendor,type?.spec_product])) || []),
               ...(variantsByDocument.get(id) || [])],
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

  const showVariant = specs.some(row=>row.rcp_variant);
  const byVariant = new Map();
  for (const row of matchingMaterial) {
    if (rating.value && String(row.rating_a) !== rating.value) continue;
    const key = JSON.stringify([row.rcp_variant,row.material,row.rating_a]);
    if (!byVariant.has(key)) byVariant.set(key,{variant:row.rcp_variant,material:row.material,rating:row.rating_a,specs:new Map()});
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
    (showVariant ? '<th scope="col">Исполнение</th>' : '')+
    '<th scope="col">Материал</th><th scope="col">Номинал</th>'+visible.map(code=>
      '<th scope="col" title="'+esc(catalogueParameters[code] || columns.get(code))+'">'+esc(columns.get(code))+'</th>').join('');
  const body = panel.querySelector('#catalog-spec-values');
  const rows = [...byVariant.values()].sort((a,b)=>String(a.variant || '').localeCompare(String(b.variant || '')) || String(a.material).localeCompare(String(b.material)) || a.rating-b.rating);
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
    return '<tr>'+(showVariant ? '<td>'+esc(row.variant?.toUpperCase() || '—')+'</td>' : '')+'<td>'+esc(row.material)+'</td><th scope="row">'+
      esc(row.rating == null ? '—' : row.rating+' А')+notes.map(note=>'<small>'+esc(note)+'</small>').join('')+
      '</th>'+cells.join('')+'</tr>';
  }).join('') || '<tr><td colspan="'+(visible.length+(showVariant ? 3 : 2))+'">По выбранным фильтрам данных нет.</td></tr>';
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
const char = id => $('#characteristics-' + id);
const characteristicStages = ['source', 'review', 'save'];
const characteristicStates = {
  new: 'Новая запись', changed: 'Изменит запись', same: 'Уже в базе',
  missing: 'Нет значения', conflict: 'Занято другим каталогом', edited: 'Значение изменено',
};
let characteristics = null;

function characteristicsMessage(message = '') {
  char('message').textContent = message;
  char('message').hidden = !message;
}

function characteristicsStage(stage) {
  characteristicStages.forEach((name, index) => {
    char(name).hidden = name !== stage;
    const step = document.querySelectorAll('.characteristics-steps li')[index];
    if (name === stage) step.setAttribute('aria-current', 'step');
    else step.removeAttribute('aria-current');
  });
  characteristicsMessage();
}

const sourceMethod = () => $('input[name="characteristics-method"]:checked').value;
function selectCharacteristicMethod(mode) {
  $('input[name="characteristics-method"][value="' + mode + '"]').checked = true;
  characteristicsSource();
}

function characteristicsSource() {
  const mode = sourceMethod();
  const document = char('document').value;
  char('new').hidden = mode !== 'new';
  char('existing').hidden = mode === 'new';
  char('csv').hidden = mode !== 'csv';
  char('pdf-pages').hidden = mode === 'csv';
  char('use-csv').hidden = true;
  const withAI = mode !== 'csv' && char('ai').checked;
  char('prepare').textContent = {
    new: withAI ? 'Загрузить и распознать с OpenAI' : 'Загрузить и распознать',
    pdf: withAI ? 'Распознать с OpenAI' : 'Распознать PDF',
    csv: 'Проверить файл',
  }[mode];
  char('templates').hidden = !document;
  for (const [id, kind] of [['standard-template', 'electrical'], ['phase-template', 'phase']]) {
    char(id).href = api('/api/characteristics-template?' + new URLSearchParams({document, kind}));
  }
}

async function loadCharacteristicCatalogues() {
  if (characteristics) return;
  try {
    const result = await req('/api/catalog-types');
    const select = char('document');
    const previous = select.value;
    select.length = 1;
    for (const row of result.catalogues) {
      if (!row.comparison_type || !row.spec_product || !row.spec_vendor) continue;
      const option = document.createElement('option');
      option.value = row.document_id;
      option.textContent = row.title + ' · ' + CATALOG_TYPE_LABELS[row.comparison_type];
      select.add(option);
    }
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
    if (select.options.length === 1 && sourceMethod() !== 'new') characteristicsMessage('Загрузите PDF или укажите данные каталога в библиотеке.');
    characteristicsSource();
  } catch (error) {
    characteristicsMessage(error.message);
  }
}

function characteristicsRequest(action, document, body, type = 'application/json') {
  const query = new URLSearchParams({action, document});
  return req('/api/characteristics?' + query, {
    method: 'POST', headers: {'Content-Type': type}, body,
  });
}

function characteristicsSelection() {
  const count = char('rows').querySelectorAll('input[type=checkbox]:checked').length;
  char('selection').textContent = 'К сохранению выбрано: ' + count;
  char('confirm').disabled = !count;
}

function filterCharacteristics() {
  const active = char('filter').getAttribute('aria-pressed') === 'true';
  char('filter').textContent = active ? 'Показать все строки' : 'Только требуют внимания';
  for (const row of char('rows').rows) row.hidden = active && ['same', 'new'].includes(row.dataset.state);
}

function renderCharacteristics() {
  char('rows').innerHTML = characteristics.rows.map((row, index) => {
    const label = catalogueParameters[row.parameter] || specColumns.get(row.parameter) || row.parameter;
    const locked = row.state === 'conflict';
    const source = row.page
      ? '<a href="' + api('/api/pdf/' + encodeURIComponent(characteristics.document)) + '#page=' + row.page +
        '" target="_blank" rel="noopener">PDF, стр. ' + row.page + ' ↗</a>'
      : esc(row.source);
    const textual = ['dimensions_mm','insulation_type'].includes(row.parameter);
    const shown = value => textual ? String(value ?? '') : String(value ?? '').replace('.', ',');
    const current = row.current == null ? '' : '<small>В базе: ' + esc(shown(row.current)) + ' ' + esc(row.unit) + '</small>';
    return '<tr data-index="' + index + '" data-state="' + esc(row.state) + '">' +
      '<td><input type="checkbox" aria-label="Выбрать ' + esc(label) + '"' +
      (row.state === 'new' ? ' checked' : '') + (['missing','conflict','same'].includes(row.state) ? ' disabled' : '') + '></td>' +
      '<td>' + (row.rcp_variant ? esc(row.rcp_variant.toUpperCase()) + ' · ' : '') + esc(row.material) + ' · ' + esc(row.rating_a) + ' А</td><td>' + esc(label) + '</td>' +
      '<td><input type="text" class="characteristics-value" inputmode="' + (textual ? 'text' : 'decimal') + '" maxlength="' + (textual ? '140' : '20') + '" aria-label="' +
      esc(label + ' · ' + row.material + ' ' + row.rating_a + ' А') + '" value="' +
      esc(shown(row.value)) + '"' + (locked ? ' readonly' : '') + '> ' + esc(row.unit) + current + '</td>' +
      '<td>' + source + '</td><td><span class="characteristics-state characteristics-state-' + esc(row.state) + '">' +
      esc(characteristicStates[row.state]) + '</span></td></tr>';
  }).join('');
  char('filter').setAttribute('aria-pressed', 'false');
  filterCharacteristics();
  characteristicsSelection();
}

char('ai').addEventListener('change', characteristicsSource);
char('document').addEventListener('change', characteristicsSource);
document.querySelectorAll('input[name="characteristics-method"]').forEach(
  input => input.addEventListener('change', characteristicsSource),
);
char('use-csv').addEventListener('click', () => {
  selectCharacteristicMethod('csv');
  char('file').focus();
});

async function uploadCharacteristicPdf() {
  const title = $('#title').value.trim();
  const vendor = $('#vendor').value.trim().toLowerCase();
  const catalog_type = $('#catalog-type').value;
  const file = $('#file').files[0];
  if (!title || !vendor || !catalog_type || !file) {
    throw Error('Укажите название каталога, производителя и тип, затем выберите PDF.');
  }
  if (!/^[a-z][a-z0-9_-]{1,35}$/.test(vendor)) {
    throw Error('Код производителя — от 2 до 36 символов: латинские буквы, цифры, дефис или подчёркивание.');
  }
  const query = new URLSearchParams({vendor,title,catalog_type});
  const saved = await req('/api/upload?' + query, {
    method: 'POST', headers: {'Content-Type': 'application/pdf'}, body: file,
  });
  const option = document.createElement('option');
  option.value = saved.id;
  option.textContent = title + ' · ' + CATALOG_TYPE_LABELS[catalog_type];
  char('document').add(option);
  char('document').value = option.value;
  $('#file').value = '';
  selectCharacteristicMethod('pdf');
  return saved.id;
}

char('prepare').addEventListener('click', async () => {
  const target = char('document');
  let documentId = target.value;
  const mode = sourceMethod();
  const file = char('file').files[0];
  const pages = char('pages').value.trim();
  const withAI = mode !== 'csv' && char('ai').checked;
  if (withAI && !/^[1-9][0-9]*(?:-[1-9][0-9]*)?(?:\s*,\s*[1-9][0-9]*(?:-[1-9][0-9]*)?)*$/.test(pages)) {
    char('pdf-pages').open = true;
    char('pages').focus();
    characteristicsMessage('Укажите страницы для отправки в OpenAI, например 4, 9 или 4-6.');
    return;
  }
  if (mode !== 'new' && (!documentId || (mode === 'csv' && !file))) {
    characteristicsMessage(!documentId ? 'Выберите каталог.' : 'Выберите файл CSV.');
    return;
  }
  char('prepare').disabled = true;
  char('use-csv').hidden = true;
  const fields = [...char('source').querySelectorAll('input,select')];
  fields.forEach(field => field.disabled = true);
  let uploaded = false;
  try {
    if (mode === 'new') {
      characteristicsMessage('Загружаем PDF…');
      documentId = await uploadCharacteristicPdf();
      uploaded = true;
    }
    const csv = mode === 'csv';
    characteristicsMessage(csv ? 'Проверяем CSV…' : withAI ? 'Распознаём выбранные страницы с OpenAI…' : 'Извлекаем значения из PDF…');
    const action = csv ? 'csv' : withAI ? 'ai-pdf' : 'pdf';
    const body = csv ? file : JSON.stringify({pages, send_selected_pages: withAI});
    const result = await characteristicsRequest(action, documentId, body, csv ? 'text/csv' : 'application/json');
    const title = [...target.options].find(option => option.value === documentId)?.textContent || 'Каталог';
    characteristics = {document: documentId, rows: result.rows};
    document.querySelectorAll('.characteristics-context').forEach(context => {
      context.textContent = title + ' · ' + (csv ? file.name : 'PDF из библиотеки');
    });
    renderCharacteristics();
    characteristicsStage('review');
  } catch (error) {
    characteristicsMessage((uploaded ? 'PDF сохранён в библиотеке. ' : '') + error.message);
    if ((uploaded || mode === 'pdf') && documentId) char('use-csv').hidden = false;
  } finally {
    char('ai').checked = false;
    fields.forEach(field => field.disabled = false);
    char('prepare').disabled = false;
    // Preserve the offer to import CSV when a new PDF could not be read.
    const fallback = !char('use-csv').hidden;
    characteristicsSource();
    char('use-csv').hidden = !fallback;
  }
});

char('rows').addEventListener('change', event => {
  if (event.target.type === 'checkbox') characteristicsSelection();
});
char('rows').addEventListener('input', event => {
  if (!event.target.matches('.characteristics-value')) return;
  const tr = event.target.closest('tr');
  const row = characteristics.rows[Number(tr.dataset.index)];
  row.value = event.target.value.trim();
  if (row.status !== 'source_document') row.status = 'manual_review';
  tr.dataset.state = row.value ? 'edited' : 'missing';
  const state = tr.querySelector('.characteristics-state');
  state.className = 'characteristics-state characteristics-state-' + tr.dataset.state;
  state.textContent = characteristicStates[tr.dataset.state];
  const checkbox = tr.querySelector('input[type=checkbox]');
  checkbox.disabled = !row.value;
  if (!row.value) checkbox.checked = false;
  characteristicsSelection();
});
char('filter').addEventListener('click', () => {
  char('filter').setAttribute('aria-pressed', String(char('filter').getAttribute('aria-pressed') === 'false'));
  filterCharacteristics();
});
char('back-source').addEventListener('click', () => {
  characteristics = null;
  characteristicsStage('source');
});
char('back-review').addEventListener('click', () => characteristicsStage('review'));
char('reset').addEventListener('click', () => {
  characteristics = null;
  char('file').value = '';
  characteristicsStage('source');
});
function showCharacteristicCounts(data, total, saved) {
  const items = [
    [data.new, saved ? 'Добавлено' : 'Новые записи'],
    [data.changed, saved ? 'Обновлено' : 'Будут обновлены'],
    [total - data.new - data.changed, 'Пропущено'],
  ];
  char('counts').innerHTML = items.map(([n, title]) => '<span><strong>' + n + '</strong>' + title + '</span>').join('');
  char('confirm-title').textContent = saved ? 'Характеристики сохранены' : 'Подтвердите сохранение';
  char('back-review').hidden = saved;
  char('reset').hidden = !saved;
  char('apply').hidden = saved;
  char('apply').disabled = saved || data.new + data.changed === 0;
}
char('confirm').addEventListener('click', async () => {
  const job = characteristics;
  const rows = [...char('rows').querySelectorAll('input[type=checkbox]:checked')]
    .map(box => ({...job.rows[Number(box.closest('tr').dataset.index)]}));
  char('confirm').disabled = true;
  char('back-source').disabled = true;
  characteristicsMessage('Проверяем выбранные строки…');
  try {
    const result = await characteristicsRequest('confirm', job.document, JSON.stringify({rows}));
    job.confirmed = {rows, digest: result.digest};
    showCharacteristicCounts(result, job.rows.length, false);
    characteristicsStage('save');
  } catch (error) {
    characteristicsMessage(error.message);
  } finally {
    characteristicsSelection();
    char('back-source').disabled = false;
  }
});
char('apply').addEventListener('click', async () => {
  const job = characteristics;
  char('apply').disabled = true;
  char('back-review').disabled = true;
  characteristicsMessage('Сохраняем характеристики…');
  try {
    const result = await characteristicsRequest('save', job.document, JSON.stringify(job.confirmed));
    showCharacteristicCounts(result, job.rows.length, true);
    characteristicsMessage();
  } catch (error) {
    characteristicsMessage(error.message);
  } finally {
    char('back-review').disabled = false;
  }
});
characteristicsStage('source');
characteristicsSource();
const initialSection = new URLSearchParams(location.search).get('workspace');
const initialTab = Array.from(tabs).find(button => button.dataset.tab === initialSection);
if (initialTab) initialTab.click();
if (initialSection !== 'library') load();
