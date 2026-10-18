'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const paths = {
  plus:'M12 5v14M5 12h14', close:'m6 6 12 12M6 18 18 6',
  layers:'m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5',
  inbox:'M4 4h16l2 12v4H2v-4L4 4ZM2 16h6l2 3h4l2-3h6',
  trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
  grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  list:'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  upload:'M12 16V3m-5 5 5-5 5 5M4 15v6h16v-6',
  download:'M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5',
  edit:'m15 4 5 5M4 20l4-1L20 7a2.8 2.8 0 0 0-4-4L4 15v5Z',
  arrow:'M4 12h16m-6-6 6 6-6 6',check:'m5 12 4 4L19 6',
  folder:'M3 7V4h6l3 3h9v13H3V7Z', restore:'M3 10a9 9 0 1 1 1 8M3 4v6h6M12 7v5l3 2',
  settings:'M4 7h16M4 17h16M8 4v6M16 14v6', up:'m6 15 6-6 6 6', down:'m6 9 6 6 6-6',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.list}"/></svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hydrate = root => $$('[data-icon]', root).forEach(el => { el.innerHTML = icon(el.dataset.icon); });
const kinds = { simple:'Простой', categorized:'С категориями', ordered:'По порядку' };
const modes = { strike:'Зачеркивать', trash:'Переносить в корзину', delete:'Удалять навсегда' };
const plural = n => `${n} ${n % 10 === 1 && n % 100 !== 11 ? 'список' : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? 'списка' : 'списков'}`;
const storage = {get(key) { try {return localStorage.getItem(key);} catch {return null;} },set(key, value) { try {localStorage.setItem(key, value);} catch {toast('Браузер не разрешил сохранить настройки устройства', true);} }};
let nickname = storage.get('klop.nickname') || '';
let view = storage.get('klop.view') || 'grid';
let filter = 'all';
let state = { lists:[], groups:[] };
let activeId = null;
let busy = false;
let toastTimer;
let settingsVersion;
let editorVersion;
let mutationEpoch = 0;
let refreshSequence = 0;

function toast(message, error = false) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('error', error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {el.hidden = true;}, error ? 6500 : 3200);
}

function syncStatus(ok) {
  const el = $('#sync-status');
  el.classList.toggle('error', !ok);
  el.innerHTML = `<span class="status-dot"></span>${ok ? 'Все изменения сохранены' : 'Нет связи с сервером'}`;
}

async function refresh(repaint = true) {
  const epoch = mutationEpoch;
  const sequence = ++refreshSequence;
  const response = await fetch('./api/state', {cache:'no-store'});
  if (!response.ok) throw new Error('Не удалось загрузить списки');
  const nextState = await response.json();
  if (epoch !== mutationEpoch || sequence !== refreshSequence) return state;
  state = nextState;
  syncStatus(true);
  if (repaint) render();
  return state;
}

async function mutate(data) {
  if (busy) {toast('Сохраняем предыдущее изменение. Повторите действие через секунду.'); return null;}
  busy = true;
  mutationEpoch++;
  try {
    const response = await fetch('./api/action', {method:'POST', headers:{'Content-Type':'application/json', 'X-Nickname':encodeURIComponent(nickname)}, body:JSON.stringify(data)});
    const payload = await response.json();
    if (!response.ok) {
      if (response.status === 409 || response.status === 400) await refresh();
      throw new Error(payload.error || 'Не удалось сохранить');
    }
    mutationEpoch++;
    state = payload.state;
    syncStatus(true);
    render();
    return payload.result;
  } catch (error) {
    toast(error.message === 'Failed to fetch' ? 'Нет связи. Изменение не сохранено — повторите действие.' : error.message, true);
    if (error instanceof TypeError) syncStatus(false);
    return null;
  } finally { busy = false; }
}

function currentList() { return state.lists.find(list => list.id === activeId); }
async function listAction(action, extra = {}, list = currentList(), version) {
  if (!list) return null;
  return mutate({action, id:list.id, version:version ?? list.version, ...extra});
}

function confirmDelete(title, description) {
  return new Promise(resolve => {
    const dialog = $('#confirm-dialog');
    $('#confirm-title').textContent = title;
    $('#confirm-description').textContent = description;
    const done = result => { dialog.close(); resolve(result); };
    $('#confirm-ok').onclick = () => done(true);
    $('#confirm-cancel').onclick = () => done(false);
    dialog.oncancel = event => { event.preventDefault(); done(false); };
    dialog.showModal();
    $('#confirm-cancel').focus();
  });
}

function exportJSON(lists = state.lists) {
  const groupIds = new Set(lists.flatMap(list => list.groups));
  const payload = {format:'klop', schemaVersion:1, groups:lists === state.lists ? state.groups : state.groups.filter(group => groupIds.has(group.id)), lists};
  const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'}));
  const a = document.createElement('a');
  a.href = url;
  a.download = lists.length === 1 ? `klop-${lists[0].title.replace(/[^\p{L}\p{N}_-]/gu, '-').slice(0, 60)}.json` : 'klop-all.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function updateProfile() {
  $('#nickname-label').textContent = nickname;
  $('#avatar').textContent = nickname.slice(0, 1).toUpperCase();
}

function render() {
  const live = state.lists.filter(list => !list.trashed);
  const trash = state.lists.filter(list => list.trashed);
  const discardedItems = live.flatMap(list => list.items.filter(item => item.trashed).map(item => ({list, item})));
  if (!['all','ungrouped','trash'].includes(filter) && !state.groups.some(g => g.id === filter)) filter = 'all';
  $('#all-count').textContent = live.length;
  $('#ungrouped-count').textContent = live.filter(l => !l.groups.length).length;
  $('#trash-count').textContent = trash.length + discardedItems.length;
  $('#group-nav').innerHTML = state.groups.map(group => `<button class="nav-item ${filter === group.id ? 'active' : ''}" data-filter="${esc(group.id)}"><span class="group-dot"></span><span>${esc(group.name)}</span><span class="count">${live.filter(l => l.groups.includes(group.id)).length}</span></button>`).join('');
  $$('[data-filter]').forEach(button => button.classList.toggle('active', button.dataset.filter === filter));
  const title = filter === 'all' ? 'Все списки' : filter === 'ungrouped' ? 'Без группы' : filter === 'trash' ? 'Корзина' : state.groups.find(g => g.id === filter)?.name;
  $('#page-title').innerHTML = `${esc(title)}<span>.</span>`;
  $('#page-description').textContent = filter === 'trash' ? 'Удалённое подождёт здесь. Восстановите или удалите навсегда.' : 'Дела, покупки и всё остальное — в одном месте.';
  const lists = filter === 'trash' ? trash : live.filter(l => filter === 'all' || (filter === 'ungrouped' ? !l.groups.length : l.groups.includes(filter)));
  $('#list-summary').textContent = plural(lists.length) + (filter === 'trash' && discardedItems.length ? ` · пунктов в корзине: ${discardedItems.length}` : '');
  $('#lists').className = `lists-grid ${view === 'rows' ? 'rows' : ''}`;
  $('#grid-view').classList.toggle('selected', view === 'grid');
  $('#row-view').classList.toggle('selected', view === 'rows');
  $('#grid-view').setAttribute('aria-pressed', String(view === 'grid'));
  $('#row-view').setAttribute('aria-pressed', String(view === 'rows'));
  $('#lists').innerHTML = lists.map(card).join('');
  const empty = !lists.length && (filter !== 'trash' || !discardedItems.length);
  $('#empty-state').hidden = !empty;
  $('#empty-state h2').textContent = filter === 'trash' ? 'Корзина пуста' : 'Здесь пока пусто';
  $('#empty-state p').textContent = filter === 'trash' ? 'Списки и пункты, отправленные в корзину, появятся здесь.' : 'Создайте первый список и освободите немного места в голове.';
  $('#empty-create').hidden = filter === 'trash';
  $('#trash-items').innerHTML = filter === 'trash' && discardedItems.length ? `<section class="trash-section"><h2>Пункты из списков</h2>${discardedItems.map(({list,item}) => `<div class="trash-item"><div class="trash-item-text">${esc(item.text)}<small>${esc(list.title)}</small></div><button class="icon-button" data-do="restore-item" data-id="${list.id}" data-item="${item.id}" title="Восстановить пункт" aria-label="Восстановить пункт">${icon('restore')}</button><button class="icon-button" data-do="delete-item" data-id="${list.id}" data-item="${item.id}" title="Удалить пункт навсегда" aria-label="Удалить пункт навсегда">${icon('trash')}</button></div>`).join('')}</section>` : '';
  if ($('#groups-dialog').open && !$('#groups-manager').contains(document.activeElement)) renderGroups();
  if ($('#editor-dialog').open) {
    const list = currentList();
    if (!list) { $('#editor-dialog').close(); activeId = null; }
    else if (list.version !== editorVersion) renderEditor();
  }
}

function card(list) {
  const items = list.items.filter(i => !i.trashed);
  const done = items.filter(i => i.done).length;
  return `<article class="list-card"><div class="card-top"><span class="type-badge ${list.kind}">${icon(list.kind === 'categorized' ? 'folder' : 'list')}${kinds[list.kind]}</span><div class="card-actions">${list.trashed ? `<button class="icon-button" data-do="restore-list" data-id="${list.id}" title="Восстановить" aria-label="Восстановить список">${icon('restore')}</button>` : `<button class="icon-button" data-do="trash-list" data-id="${list.id}" title="В корзину" aria-label="Перенести список в корзину">${icon('inbox')}</button>`}<button class="icon-button" data-do="delete-list" data-id="${list.id}" title="Удалить навсегда" aria-label="Удалить список навсегда">${icon('trash')}</button></div></div><button class="card-title" data-do="open" data-id="${list.id}">${esc(list.title)}</button><div class="card-groups">${list.groups.map(id => state.groups.find(g => g.id === id)).filter(Boolean).map(g => `<span class="tag">${esc(g.name)}</span>`).join('') || '<span class="tag">Без группы</span>'}</div><div class="card-items">${items.slice(0, 4).map((item,index) => `<div class="preview-row ${item.done ? 'done' : ''}">${list.kind === 'ordered' ? `<span class="order-number">${index+1}.</span>` : ''}<button class="check ${item.done ? 'checked' : ''}" data-do="complete-item" data-id="${list.id}" data-item="${item.id}" title="${item.done ? 'Вернуть в работу' : 'Выполнить пункт'}" aria-label="${item.done ? 'Вернуть в работу' : 'Выполнить пункт'}: ${esc(item.text)}" ${list.trashed ? 'disabled' : ''}>${item.done ? icon('check') : ''}</button><span>${esc(item.text)}</span></div>`).join('') || '<p class="card-empty">Пока без пунктов. Начнём?</p>'}${items.length > 4 ? `<span class="more-items">Ещё ${items.length - 4}…</span>` : ''}</div><div class="card-footer"><span>${done} из ${items.length} выполнено</span><div class="card-meta"><span class="card-owner" title="Последнее изменение: ${esc(list.updatedBy)}">${esc(list.updatedBy)}</span><progress class="mini-progress" max="${items.length || 1}" value="${done}" aria-label="Выполнено пунктов"></progress></div></div></article>`;
}

function openEditor(id) {
  activeId = id;
  renderEditor();
  $('#editor-dialog').showModal();
}

function categoryOptions(list, selected = '') {
  return `<option value="">Без категории</option>${list.categories.map(c => `<option value="${esc(c)}" ${c === selected ? 'selected' : ''}>${esc(c)}</option>`).join('')}`;
}

function renderEditor() {
  const list = currentList();
  if (!list) return;
  const dialog = $('#editor-dialog');
  const scroll = dialog.scrollTop;
  editorVersion = list.version;
  const items = list.items.filter(i => !i.trashed);
  const itemRow = item => {
    const position = items.indexOf(item);
    return `<div class="item-row ${item.done ? 'done' : ''}" data-item="${item.id}">${list.kind === 'ordered' ? `<span class="order-number">${position + 1}.</span>` : ''}<button class="check ${item.done ? 'checked' : ''}" data-editor="complete" data-item="${item.id}" aria-label="${item.done ? 'Вернуть в работу' : 'Выполнить пункт'}">${item.done ? icon('check') : ''}</button><input class="item-text" value="${esc(item.text)}" maxlength="1000" aria-label="Текст пункта" data-edit-item="${item.id}">${list.kind === 'categorized' ? `<select class="item-category" data-item-category="${item.id}" aria-label="Категория пункта">${categoryOptions(list, item.category)}</select>` : ''}<div class="item-controls">${list.kind === 'ordered' ? `<button class="icon-button" data-editor="up" data-item="${item.id}" ${position === 0 ? 'disabled' : ''} title="Выше" aria-label="Переместить выше">${icon('up')}</button><button class="icon-button" data-editor="down" data-item="${item.id}" ${position === items.length-1 ? 'disabled' : ''} title="Ниже" aria-label="Переместить ниже">${icon('down')}</button>` : ''}<button class="icon-button" data-editor="trash" data-item="${item.id}" title="В корзину" aria-label="Пункт в корзину">${icon('trash')}</button></div></div>`;
  };
  $('#editor').innerHTML = `<div class="dialog-heading"><h2>${esc(list.title)}</h2><div class="editor-tools"><button class="icon-button" data-editor="export" title="Экспорт списка" aria-label="Экспорт списка">${icon('download')}</button><button class="icon-button" data-editor="settings" title="Настройки списка" aria-label="Настройки списка">${icon('settings')}</button><button class="icon-button" data-close="editor-dialog" aria-label="Закрыть">${icon('close')}</button></div></div><div class="editor-caption"><span class="type-badge ${list.kind}">${kinds[list.kind]}</span><span>${modes[list.completion]}</span><span>· ${esc(list.updatedBy)}</span>${list.groups.map(id => state.groups.find(g => g.id === id)).filter(Boolean).map(g => `<span class="tag">${esc(g.name)}</span>`).join('')}</div>${list.trashed ? `<p class="muted">Этот список в корзине.</p><button class="button" data-editor="restore-list">${icon('restore')} Восстановить список</button>` : `<form id="item-form" class="editor-add"><input name="text" placeholder="Добавить пункт…" required maxlength="1000" aria-label="Новый пункт">${list.kind === 'categorized' ? `<select name="category" aria-label="Категория нового пункта">${categoryOptions(list)}</select>` : ''}<button class="button primary" type="submit" aria-label="Добавить пункт">${icon('plus')}</button></form>`}<div>${list.kind === 'categorized' ? ['', ...list.categories].map(category => `<div class="category-heading">${esc(category || 'Без категории')}<span>· ${items.filter(i => i.category === category).length}</span>${category && !list.trashed ? `<button class="icon-button" data-remove-category="${esc(category)}" aria-label="Удалить категорию" title="Удалить категорию (пункты сохранятся)">${icon('close')}</button>` : ''}</div>${items.filter(i => i.category === category).map(itemRow).join('')}`).join('') : items.map(itemRow).join('')}${!items.length ? '<p class="muted">Пунктов пока нет.</p>' : ''}</div>${list.kind === 'categorized' && !list.trashed ? `<form id="category-form" class="inline-form category-add"><input name="name" maxlength="100" required placeholder="Новая категория" aria-label="Новая категория"><button class="button" type="submit">${icon('plus')} Категория</button></form>` : ''}`;
  if (list.trashed) $$('.item-row input,.item-row select,.item-row button', $('#editor')).forEach(el => {el.disabled = true;});
  dialog.scrollTop = scroll;
}

function openSettings() {
  const list = currentList();
  settingsVersion = list.version;
  const form = $('#settings-form');
  for (const key of ['title','kind','completion']) form.elements[key].value = list[key];
  $('#settings-groups').innerHTML = state.groups.map(g => `<label class="checkbox-label"><input type="checkbox" name="groups" value="${g.id}" ${list.groups.includes(g.id) ? 'checked' : ''}>${esc(g.name)}</label>`).join('') || '<p class="hint">Групп пока нет. Создайте их в боковом меню.</p>';
  $('#settings-dialog').showModal();
}

function renderGroups() {
  $('#groups-manager').innerHTML = state.groups.map(g => `<div class="manager-row"><input data-group-name="${g.id}" value="${esc(g.name)}" maxlength="60" aria-label="Название группы"><button class="icon-button" data-delete-group="${g.id}" aria-label="Удалить группу" title="Удалить группу">${icon('trash')}</button></div>`).join('') || '<p class="hint">Групп пока нет.</p>';
}

async function completeItem(list, itemId, version) {
  if (list.completion === 'delete' && !await confirmDelete('Удалить пункт?', 'В этом списке выполненные пункты удаляются навсегда.')) return;
  await listAction('item.complete', {itemId}, list, version);
}

document.addEventListener('click', async event => {
  const close = event.target.closest('[data-close]');
  if (close) { $(`#${close.dataset.close}`).close(); return; }
  const nav = event.target.closest('[data-filter]');
  if (nav) { filter = nav.dataset.filter; render(); return; }
  const cardButton = event.target.closest('[data-do]');
  if (cardButton) {
    const list = state.lists.find(l => l.id === cardButton.dataset.id);
    if (!list) return;
    const version = list.version;
    switch (cardButton.dataset.do) {
      case 'open': openEditor(list.id); break;
      case 'trash-list': if (await listAction('list.trash', {trashed:true}, list)) toast('Список в корзине'); break;
      case 'restore-list': await listAction('list.trash', {trashed:false}, list); break;
      case 'delete-list': if (await confirmDelete('Удалить список навсегда?', `«${list.title}» и все его пункты будут удалены для всех пользователей.`)) await listAction('list.delete', {}, list, version); break;
      case 'complete-item': await completeItem(list, cardButton.dataset.item, version); break;
      case 'restore-item': await listAction('item.trash', {itemId:cardButton.dataset.item, trashed:false}, list); break;
      case 'delete-item': if (await confirmDelete('Удалить пункт навсегда?', 'Восстановить его не получится.')) await listAction('item.delete', {itemId:cardButton.dataset.item}, list, version); break;
    }
    return;
  }
  const editorButton = event.target.closest('[data-editor]');
  if (editorButton) {
    const list = currentList();
    const version = editorVersion;
    const itemId = editorButton.dataset.item;
    switch(editorButton.dataset.editor) {
      case 'export': exportJSON([list]); break;
      case 'settings': openSettings(); break;
      case 'complete': await completeItem(list, itemId, version); break;
      case 'up': case 'down': await listAction('item.move', {itemId, direction:editorButton.dataset.editor}, list, version); break;
      case 'trash': await listAction('item.trash', {itemId, trashed:true}, list, version); break;
      case 'restore-list': await listAction('list.trash', {trashed:false}, list, version); break;
    }
    return;
  }
  const category = event.target.closest('[data-remove-category]');
  if (category) await listAction('category.delete', {name:category.dataset.removeCategory}, currentList(), editorVersion);
  const group = event.target.closest('[data-delete-group]');
  if (group && await confirmDelete('Удалить группу?', 'Списки останутся. У них будет убрана только эта группа.')) {
    await mutate({action:'group.delete', id:group.dataset.deleteGroup});
    renderGroups();
  }
});

document.addEventListener('change', async event => {
  const el = event.target;
  if (el.matches('[data-edit-item]')) {
    const result = await listAction('item.update', {itemId:el.dataset.editItem, text:el.value}, currentList(), editorVersion);
    if (!result) renderEditor();
  }
  if (el.matches('[data-item-category]')) await listAction('item.update', {itemId:el.dataset.itemCategory, text:currentList().items.find(i => i.id === el.dataset.itemCategory).text, category:el.value}, currentList(), editorVersion);
  if (el.matches('[data-group-name]')) {
    await mutate({action:'group.rename', id:el.dataset.groupName, name:el.value});
    renderGroups();
  }
});

document.addEventListener('keydown', event => {
  if (event.key === 'Enter' && event.target.matches('[data-edit-item],[data-group-name]')) {
    event.preventDefault();
    event.target.blur();
  }
});

document.addEventListener('submit', async event => {
  const form = event.target;
  event.preventDefault();
  if (form.id === 'nickname-form') {
    const name = $('#nickname-input').value.trim();
    if (!name) return;
    nickname = name;
    storage.set('klop.nickname', name);
    updateProfile();
    $('#nickname-dialog').close();
  }
  if (form.id === 'create-form') {
    const result = await mutate({action:'list.create', ...Object.fromEntries(new FormData(form))});
    if (result) { form.reset(); $('#create-dialog').close(); if (filter === 'trash') { filter = 'all'; render(); } openEditor(result.id); }
  }
  if (form.id === 'settings-form') {
    const values = new FormData(form);
    const result = await listAction('list.update', {title:values.get('title'), kind:values.get('kind'), completion:values.get('completion'), groups:values.getAll('groups')}, currentList(), settingsVersion);
    if (result) $('#settings-dialog').close();
    else settingsVersion = currentList()?.version;
  }
  if (form.id === 'group-form') {
    const result = await mutate({action:'group.create', name:form.elements.name.value});
    if (result) {form.reset(); renderGroups(); form.elements.name.focus();}
  }
  if (form.id === 'item-form') {
    const selected = form.elements.category?.value || '';
    const result = await listAction('item.add', {text:form.elements.text.value, category:selected}, currentList(), editorVersion);
    if (result) { const next = $('#item-form'); if (next.elements.category) next.elements.category.value = selected; next.elements.text.focus(); }
  }
  if (form.id === 'category-form') {
    const result = await listAction('category.add', {name:form.elements.name.value}, currentList(), editorVersion);
    if (result) $('#category-form input').focus();
  }
});

function createDialog() { $('#create-dialog').showModal(); }
$('#new-list').onclick = createDialog;
$('#empty-create').onclick = createDialog;
$('#grid-view').onclick = () => {view = 'grid'; storage.set('klop.view', view); render();};
$('#row-view').onclick = () => {view = 'rows'; storage.set('klop.view', view); render();};
$('#manage-groups').onclick = () => {renderGroups(); $('#groups-dialog').showModal();};
$('#profile-button').onclick = () => {$('#nickname-input').value = nickname; $('#nickname-dialog').showModal();};
$('#nickname-dialog').addEventListener('cancel', event => {if (!nickname) event.preventDefault();});
$('#export-button').onclick = async () => {try {await refresh(); exportJSON();} catch {toast('Не удалось загрузить свежие данные для экспорта', true);}};
$('#import-button').onclick = () => $('#import-file').click();
$('#import-file').onchange = async event => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 5 * 1024 * 1024 - 1024) throw new Error('Максимальный размер файла — 5 МБ');
    const data = JSON.parse(await file.text());
    const result = await mutate({action:'import', data});
    if (result) toast(`Импортировано: ${plural(result.imported)}. Существующие списки сохранены.`);
  } catch (error) {toast(error instanceof SyntaxError ? 'Файл не содержит корректный JSON' : error.message, true);}
  event.target.value = '';
};

const mobileFiles = document.createElement('div');
mobileFiles.className = 'mobile-files';
mobileFiles.innerHTML = `<button id="mobile-import">${icon('upload')} Импорт JSON</button><button id="mobile-export">${icon('download')} Экспорт всего</button>`;
$('.content').prepend(mobileFiles);
$('#mobile-import').onclick = () => $('#import-button').click();
$('#mobile-export').onclick = () => $('#export-button').click();
hydrate(document);
updateProfile();
if (!nickname) $('#nickname-dialog').showModal();
refresh().catch(() => {syncStatus(false); toast('Не удалось загрузить списки. Проверьте соединение.', true);});
setInterval(async () => {
  if (busy || document.hidden || document.activeElement?.matches('input,select') || $('#confirm-dialog').open || $('#settings-dialog').open) return;
  try {await refresh();} catch {syncStatus(false);}
}, 4000);
