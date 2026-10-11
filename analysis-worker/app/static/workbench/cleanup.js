import { api, apiPage, queryString, pathId } from './api.js';
import { el, heading, button, linkButton, field, dialog, sourceLink, loading, empty, errorBox, setContent, notify, pagination, dateText } from './ui.js';

const names = { trash: '移入回收站', restore: '恢复资料', purge: '彻底删除' };
async function previewCleanup(ctx, ids, action) {
  const modal = dialog(`${names[action]} · 核对清理范围`, { subtitle: 'SELECTED MATERIALS / REVIEW BEFORE APPLY' });
  const body = el('div', {}, loading('正在读取所选资料的当前状态…')), errors = el('div');
  const reason = field('清理或恢复原因', { name: 'reason', type: 'textarea', required: true, maxlength: 500, value: action === 'restore' ? '重新核对后保留用于研究。' : '人工核对为不相关或无研究意义的材料。' });
  const confirmed = el('input', { type: 'checkbox', name: 'confirm_purge' });
  modal.body.append(body, reason, action === 'purge' && el('label', { class: 'cleanup-purge-confirm' }, confirmed, ' 我确认彻底删除所选源记录与历史版本，之后无法从回收站恢复。已有报告中的快照保留。'), errors);
  let preview, saving = false;
  const apply = button(names[action], async () => {
    if (!preview || saving || !reason.querySelector('textarea').reportValidity()) return;
    if (action === 'purge' && !confirmed.checked) { setContent(errors, errorBox(new Error('请明确勾选不可恢复的彻底删除确认。'))); return; }
    saving = true; apply.disabled = true;
    try { const result = await api('items/cleanup/apply', { method: 'POST', body: { ids, action, preview_digest: preview.preview_digest, reason: reason.querySelector('textarea').value, confirm_purge: confirmed.checked } }); modal.close(); notify(`${names[action]}完成：${result.count} 条。`); ctx.refresh(); }
    catch (error) { setContent(errors, errorBox(error)); }
    finally { saving = false; apply.disabled = false; }
  }, action === 'purge' ? 'danger' : '');
  apply.disabled = true; modal.footer.append(button('取消', modal.close, 'secondary'), apply);
  const controller = new AbortController(); modal.modal.addEventListener('close', () => controller.abort(), { once: true });
  try { preview = await api('items/cleanup/preview', { method: 'POST', body: { ids, action }, signal: controller.signal }); if (controller.signal.aborted) return;
    setContent(body, el('p', { class: 'result-box' }, `将对明确选中的 ${preview.count} 条源记录执行「${names[action]}」。`), el('p', { class: 'field-hint' }, preview.effect), el('ol', { class: 'cleanup-preview-list' }, preview.items.map(item => el('li', {}, el('strong', {}, item.title), sourceLink(item.url, '核对来源'))))); apply.disabled = false;
  } catch (error) { if (!controller.signal.aborted) setContent(body, errorBox(error)); }
}

export async function renderCleanup(ctx) {
  const trash = ctx.params.get('trash') === '1';
  const values = Object.fromEntries(['query', 'type', 'topic', 'source', 'start_date', 'end_date'].map(key => [key, ctx.params.get(key) || '']));
  const data = await apiPage('items', { ...values, trash, page: ctx.params.get('page'), page_size: 25 }, { signal: ctx.signal });
  const route = params => ctx.navigate('library', { ...values, ...(trash ? { trash: 1 } : { cleanup: 1 }), ...params });
  const content = el('div', { class: 'material-cleanup' }, heading('04', trash ? '资料回收站' : '资料清理', 'MATERIALS / MANUAL REVIEW / RECOVERY', trash ? '回收站资料不参与检索、新报告或活动地图。恢复会保留原资料编号与来源；彻底删除需要再次核对范围。' : '手动删减已核对为不相关、重复或无意义的材料。先预览所选内容，再移入可恢复的回收站。', [linkButton('返回资料库', '#library', 'secondary'), linkButton(trash ? '清理资料' : '查看回收站', trash ? '#library?cleanup=1' : '#library?trash=1', 'secondary')]));
  const search = field('按标题或正文筛选', { name: 'query', value: values.query, type: 'search', placeholder: '先筛选，再逐条核对' });
  content.append(el('form', { class: 'filter-panel', onSubmit: event => { event.preventDefault(); route({ query: search.querySelector('input').value, page: 1 }); } }, search, el('button', { type: 'submit', class: 'button secondary' }, '筛选清理范围')));
  const ids = new Set(), count = el('p', { class: 'field-hint', 'aria-live': 'polite' }, '已选 0 条 / 仅操作本页选中资料');
  const selectAll = el('input', { type: 'checkbox', 'aria-label': '选择本页全部资料' });
  const boxes = [];
  const actions = (trash ? ['restore', 'purge'] : ['trash']).map(action => button(names[action], () => previewCleanup(ctx, [...ids], action), action === 'purge' ? 'danger small' : 'secondary small'));
  function update() { count.textContent = `已选 ${ids.size} 条 / 仅操作本页选中资料`; actions.forEach(control => { control.disabled = !ids.size; }); selectAll.checked = boxes.length > 0 && boxes.every(box => box.checked); selectAll.indeterminate = ids.size > 0 && ids.size < boxes.length; }
  selectAll.addEventListener('change', () => { boxes.forEach((box, i) => { box.checked = selectAll.checked; if (box.checked) ids.add(data.items[i].id); else ids.delete(data.items[i].id); }); update(); });
  content.append(el('div', { class: 'actions' }, el('label', {}, selectAll, ' 选择本页全部'), count, actions));
  content.append(data.items.length ? el('div', { class: 'cleanup-select-list' }, data.items.map(item => {
    const box = el('input', { type: 'checkbox', 'aria-label': `选择资料：${item.title}`, onChange: event => { if (event.target.checked) ids.add(item.id); else ids.delete(item.id); update(); } }); boxes.push(box);
    return el('article', { class: 'cleanup-selection-row' }, box, el('div', {}, el('h3', {}, item.title), el('p', {}, `${dateText(item.published_at)} · ${item.source_name || '来源未标明'}`), el('p', {}, [...String(item.content || '')].slice(0, 180).join('')), item.metadata?.atlas_cleanup?.reason && el('p', {}, `清理原因：${item.metadata.atlas_cleanup.reason}`), el('div', { class: 'actions' }, sourceLink(item.url), button('查看完整资料', async () => { const module = await import('./library.js'); module.openItem(item.id, ctx.signal); }, 'text small'))));
  })) : empty(trash ? '回收站为空' : '本范围没有可清理资料', '可以调整筛选条件或返回资料库。', [], true));
  content.append(pagination(data, page => route({ page }))); update(); return content;
}
