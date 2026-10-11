import { api, apiPage, pathId, queryString } from './api.js';
import { el, button, field, dialog, sourceLink, loading, empty, errorBox, setContent, notify, pagination, number } from './ui.js';

import { openAutoReview } from './review.js';

let geometryPromise;
async function geometry() {
  if (!geometryPromise) geometryPromise = fetch(new URL('./japan_prefectures.json', import.meta.url)).then(response => { if (!response.ok) throw new Error('地图边界暂时无法读取。'); return response.json(); }).catch(error => { geometryPromise = null; throw error; });
  return geometryPromise;
}
const percent = value => value === null || value === undefined ? '—' : `${Number(value).toFixed(1)}%`;
const svgEl = (tag, attrs = {}, text = '') => { const node = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value); node.textContent = text; return node; };

export async function openActivityReview(item, candidate = null, onSaved = () => {}, signal) {
  const modal = dialog('核对活动与举办地', { subtitle: 'ACTIVITY / DATE / PLACE / SOURCE' });
  const title = field('活动名称', { name: 'title', value: candidate?.title || item.title || '', required: true, maxlength: 260 });
  const day = field('实际活动日期', { name: 'date', type: 'date', value: candidate?.date || '', required: true });
  const place = field('实际举办地', { name: 'prefecture', options: [['', '暂时无法定位']] });
  const quote = field('保存原文中支持活动的摘录', { name: 'quote', type: 'textarea', value: candidate?.quote || item.content || item.title || '', required: true, maxlength: 2000 });
  const merge = field('重复报道合并', { name: 'merge_with', options: [['', '单独活动 / 相同名称、摘录及日期地点自动合并']] });
  const note = field('核对说明', { name: 'note', type: 'textarea', value: candidate?.note || '', maxlength: 1600 });
  const errors = el('div');
  modal.body.append(el('p', {}, '核对原始来源中的实际活动日期和举办地。同一活动的多篇报道只计一次；不同日期或不同举办地分别记录。无法定位的活动可以保留，但不进入各地占比分母。'), sourceLink(item.url || candidate?.source_url, '打开原始来源核对'), title, day, place, quote, merge, note, errors);
  let ready = false, saving = false;
  const save = async status => {
    if (!ready || saving) return;
    if (status === 'confirmed' && [title, day, quote].some(control => !control.querySelector('input,textarea').reportValidity())) return;
    saving = true; confirm.disabled = reject.disabled = true;
    try { await api(`items/${pathId(item.id || candidate.item_id)}/activities`, { method: 'POST', body: { id: candidate?.id || '', status, title: title.querySelector('input').value, date: day.querySelector('input').value, prefecture: place.querySelector('select').value, quote: quote.querySelector('textarea').value, merge_with: status === 'confirmed' ? merge.querySelector('select').value : '', note: note.querySelector('textarea').value } }); modal.close(); notify(status === 'confirmed' ? '活动核对已保存，地图按去重后的活动重新统计。' : '已排除此活动线索。'); await onSaved(); }
    catch (error) { setContent(errors, errorBox(error)); }
    finally { saving = false; confirm.disabled = reject.disabled = false; }
  };
  const confirm = button('保存已核对活动', () => save('confirmed')), reject = button('排除此线索', () => save('rejected'), 'text');
  confirm.disabled = reject.disabled = true;
  modal.footer.append(button('取消', modal.close, 'secondary'), reject, confirm);
  const controller = new AbortController();
  modal.modal.addEventListener('close', () => controller.abort(), { once: true });
  if (signal?.aborted) controller.abort();
  signal?.addEventListener('abort', () => controller.abort(), { once: true });
  try { const [map, records] = await Promise.all([geometry(), api('activities?page_size=100', { signal: controller.signal })]);
    if (controller.signal.aborted) return;
    setContent(place.querySelector('select'), el('option', { value: '' }, '暂时无法定位'), map.prefectures.map(row => el('option', { value: row.name, selected: row.name === candidate?.prefecture }, row.name)));
    const keys = new Set();
    records.items.forEach(row => { if (keys.has(row.event_key)) return; keys.add(row.event_key); merge.querySelector('select').append(el('option', { value: row.event_key }, `${row.date} · ${row.prefecture || '未定位'} · ${row.title}`)); });
    ready = true; confirm.disabled = reject.disabled = false;
  } catch (error) { if (!controller.signal.aborted) setContent(errors, errorBox(error), button('关闭后重试', modal.close, 'secondary')); }
}

export function activityMap(ctx) {
  const section = el('section', { class: 'panel activity-panel', 'aria-label': '日本各地活动数量占比' }, el('div', { class: 'panel-heading' }, el('div', {}, el('span', { class: 'eyebrow' }, 'JAPAN / ACTIVITIES / EVIDENCE'), el('h2', {}, '各地活动数量占比')), button('自动审核活动', () => openAutoReview(ctx), 'secondary small'), el('span', { class: 'mono muted' }, '47 都道府県')));
  const dayStart = field('活动日期 · 从', { type: 'date', name: 'start_date', value: ctx.params.get('activity_start') || '' }), dayEnd = field('至', { type: 'date', name: 'end_date', value: ctx.params.get('activity_end') || '' });
  const mapHost = el('div', { class: 'activity-map-host' }, loading('正在读取离线日本地图…'));
  const metrics = el('div', { class: 'activity-metrics' }), detail = el('div', { class: 'activity-detail' }), list = el('div', { class: 'activity-list' }), errors = el('div'), paging = el('div');
  const totals = el('div'), scope = el('p', { class: 'field-hint activity-definition' });
  const view = field('活动记录', { name: 'status', options: [['confirmed', '已核对活动'], ['candidate', '待核对与证据不足线索']] });
  let shape, data, selected = '', hovered = '', requestVersion = 0, page = 1;
  const selection = el('p', { class: 'activity-selection', 'aria-live': 'polite' }, '选择一个都道府县查看活动、占比及来源。');
  const clear = button('查看全国', () => { selected = ''; page = 1; refresh(); }, 'secondary small');
  function mapDraw() {
    const lookup = new Map(data.prefectures.map(row => [row.name, row]));
    const svg = svgEl('svg', { class: 'japan-activity-map', viewBox: shape.viewBox, role: 'group', 'aria-label': '日本各地活动占比地图。每个都道府县可按回车选择，统计不代表完整社会活动分布。' });
    const ocean = svgEl('rect', { x: 0, y: 0, width: 760, height: 590, rx: 16, class: 'map-ocean' }); svg.append(ocean);
    svg.append(svgEl('text', { x: 38, y: 49, class: 'map-kicker' }, '日本 / 活動記録'), svgEl('text', { x: 38, y: 78, class: 'map-caption' }, data.denominator ? `统计分母 ${data.denominator} 场已定位活动` : '等待已核对、已定位的活动'));
    const labelNames = new Set([...data.prefectures].filter(row => row.count > 0).sort((a, b) => b.count - a.count).slice(0, 8).map(row => row.name));
    if (selected) labelNames.add(selected);
    const labels = svgEl('g', { class: 'map-labels', 'aria-hidden': 'true' });
    for (const pref of shape.prefectures) {
      const row = lookup.get(pref.name) || { count: 0, percent: null };
      const path = svgEl('path', { d: pref.path, 'fill-rule': 'evenodd', class: `map-prefecture ${selected === pref.name ? 'selected' : ''}`, tabindex: '0', role: 'button', 'data-prefecture': pref.name, 'data-label-x': pref.label[0], 'data-label-y': pref.label[1], 'aria-pressed': selected === pref.name, 'aria-label': `${pref.name}：${row.count} 场活动，占比 ${percent(row.percent)}。` });
      path.style.setProperty('--map-fill', row.count ? `hsl(9 66% ${Math.max(29, 76 - Math.sqrt(row.percent / 100) * 45)}%)` : '#dfdfd4');
      path.append(svgEl('title', {}, `${pref.name} · ${row.count} 场 · ${percent(row.percent)}`));
      const show = () => { hovered = pref.name; selection.textContent = `${pref.name} · ${row.count} 场 · ${percent(row.percent)}${data.denominator ? ` / 全国 ${data.denominator} 场` : '（暂无统计分母）'}`; };
      path.addEventListener('pointerenter', show); path.addEventListener('focus', show);
      const pick = () => { selected = selected === pref.name ? '' : pref.name; page = 1; refresh(); };
      path.addEventListener('click', pick); path.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); pick(); } });
      svg.append(path);
      if (labelNames.has(pref.name)) { const [x, y] = pref.label; const group = svgEl('g', { transform: `translate(${x},${y})`, class: 'map-percentage-label' }); group.append(svgEl('rect', { x: -34, y: -12, width: 68, height: 36, rx: 7 }), svgEl('text', { x: 0, y: 3, 'text-anchor': 'middle' }, pref.name), svgEl('text', { x: 0, y: 17, 'text-anchor': 'middle', class: 'map-percent' }, percent(row.percent))); labels.append(group); }
    }
    svg.append(labels); setContent(mapHost, svg);
    const ranking = [...data.prefectures].sort((a, b) => b.count - a.count).filter(row => row.count > 0 || row.name === selected).slice(0, 8);
    setContent(detail, el('h3', {}, selected || '活动分布'), selection, ranking.length ? ranking.map(row => button(`${row.name} · ${row.count} 场 · ${percent(row.percent)}`, () => { selected = row.name; page = 1; refresh(); }, `activity-rank ${selected === row.name ? 'selected' : ''}`)) : el('p', { class: 'field-hint' }, '尚无已核对且已定位的活动。可以先核对下方候选线索，也可以在资料详情中标记活动。'), clear);
  }
  async function review(row) {
    try { const item = await api(`items/${pathId(row.item_id)}`, { signal: ctx.signal }); if (!ctx.signal.aborted) openActivityReview(item, row, () => refresh(), ctx.signal); }
    catch (error) { if (!ctx.signal.aborted) setContent(errors, errorBox(error)); }
  }
  async function refresh() {
    const version = ++requestVersion;
    const start = dayStart.querySelector('input').value, end = dayEnd.querySelector('input').value;
    if (start && end && start > end) { setContent(errors, errorBox(new Error('开始日期不能晚于结束日期。'))); return; }
    setContent(errors); list.setAttribute('aria-busy', 'true');
    try {
      const results = await Promise.all([geometry(), apiPage('activities', { start_date: start, end_date: end, status: view.querySelector('select').value, prefecture: selected, page, page_size: 12 }, { signal: ctx.signal })]);
      if (ctx.signal.aborted || version !== requestVersion) return;
      [shape, data] = results; page = data.page;
      ctx.replaceParams?.({ activity_start: start, activity_end: end });
      setContent(metrics, [['已核对活动', data.confirmed], ['已定位 / 统计分母', data.denominator], ['未定位', data.unlocated], ['待核对线索', data.pending], ['已审证据不足', data.inconclusive]].map(([name, value]) => el('div', {}, el('strong', { class: 'mono' }, number(value)), el('span', {}, name))));
      scope.textContent = data.definition;
      setContent(totals, el('p', { class: 'field-hint' }, `${selected || '全国'} · ${view.querySelector('select').value === 'candidate' ? '候选线索' : '去重活动'} ${data.total} 条。日期未明线索 ${data.unknown_date} 条，保留在待核对中。`));
      mapDraw();
      setContent(list, data.items.length ? data.items.map(row => el('article', { class: 'activity-card' }, el('div', { class: 'activity-card-meta mono' }, `${row.date || '日期待核对'} · ${row.prefecture || '地点待核对'} · ${row.status === 'confirmed' ? '已核对' : row.review_outcome === 'inconclusive' ? '已审核 · 证据不足' : '候选'}`), el('h3', {}, row.title), el('blockquote', {}, row.quote), (row.note || row.reason) && el('p', { class: 'field-hint' }, row.note || row.reason), el('div', { class: 'actions' }, button(row.status === 'confirmed' ? '重新核对' : '核对这场活动', () => review(row), 'secondary small'), ...(row.sources || [{ url: row.source_url }]).map(source => sourceLink(source.url, '公开活动来源'))))) : empty(view.querySelector('select').value === 'candidate' ? '本范围暂无候选活动' : '本范围暂无已核对活动', '地图只统计实际活动。文章里的普通地名提及、文章条数和未经核对的候选不会计入。', [], true));
      setContent(paging, pagination(data, next => { page = next; refresh(); }));
    } catch (error) { if (!ctx.signal.aborted && version === requestVersion) { setContent(errors, errorBox(error), button('重新读取活动地图', refresh, 'secondary small')); if (!shape) setContent(mapHost, empty('地图暂时无法读取', '活动数据与来源仍保留，可重试。', [], true)); } }
    finally { if (version === requestVersion) list.removeAttribute('aria-busy'); }
  }
  const form = el('form', { class: 'activity-date-filter', onSubmit: event => { event.preventDefault(); page = 1; refresh(); } }, dayStart, dayEnd, el('button', { type: 'submit', class: 'button secondary small' }, '更新活动范围'));
  view.querySelector('select').addEventListener('change', () => { page = 1; if (view.querySelector('select').value === 'candidate') selected = ''; refresh(); });
  section.append(form, metrics, errors, el('div', { class: 'activity-map-layout' }, mapHost, detail), scope, el('p', { class: 'map-attribution small-text muted' }, '地图：国土交通省「国土数値情報（行政区域データ）」2026，CC BY 4.0。海岸线已简化，远方小岛省略。', sourceLink('https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html', '边界来源')), el('div', { class: 'activity-record-toolbar' }, view, totals), list, paging);
  refresh(); return section;
}
