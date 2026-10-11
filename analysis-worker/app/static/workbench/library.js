import { api, apiPage, pageNumber, pathId } from './api.js';
import { openAutoReview, openFastClassification } from './review.js';
import { accountTypeSummary, accountTypeControls } from './account_type.js';
import { el, button, heading, field, badge, sourceLink, dateText, empty, loading, errorBox, dialog, pagination, number, setContent, notify } from './ui.js';
import { openImport, openAccountEdit, openVerification } from './actions.js';
import { evidenceMetadata, openAccountRelations, isSocial } from './social.js';
import { openPublicVerification } from './registry.js';
import { openActivityReview } from './activities.js';
import { renderCleanup } from './cleanup.js';

// Imported metadata may contain arbitrary values. Only actual text can be used
// as a legacy body fallback; missing text remains explicitly unavailable.
const itemContent = item => [item.content, item.clean_text, item.content_text].find(value => typeof value === 'string' && value.length > 0) || '';

export async function openItem(id, signal) {
  const modal = dialog('资料详情', { drawer: true, subtitle: 'SOURCE RECORD' });
  modal.body.append(loading());
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  modal.modal.addEventListener('close', () => { controller.abort(); signal?.removeEventListener('abort', abort); }, { once: true });
  try {
    const item = await api(`items/${pathId(id)}`, { signal: controller.signal });
    const topics = item.topics || []; const entities = item.entities || [];
    setContent(modal.body, el('div', { class: 'detail-topline' }, badge(item.type), el('span', { class: 'muted' }, item.source_name || '来源未标注')), el('h3', { class: 'detail-title' }, item.title || '无标题资料'), el('dl', { class: 'metadata' }, el('dt', {}, '发布时间'), el('dd', {}, dateText(item.published_at, true)), el('dt', {}, '收录时间'), el('dd', {}, dateText(item.created_at || item.fetched_at, true)), el('dt', {}, '资料编号'), el('dd', { class: 'mono small-text' }, item.id)), sourceLink(item.url), evidenceMetadata(item), (topics.length || entities.length) > 0 && el('div', { class: 'tag-list' }, topics.map(topic => el('span', { class: 'tag' }, topic)), entities.map(entity => el('span', { class: 'tag outline' }, entity))), el('div', { class: 'divider' }), el('h4', { class: 'eyebrow' }, '原始材料 / CONTENT'), el('div', { class: 'source-content' }, itemContent(item) || '该记录未保存正文，请查看原始来源。'));
    const historyBody = el('div');
    const history = el('details', { class: 'capture-history' }, el('summary', {}, '查看保留的历史捕获'), historyBody);
    let historyLoaded = false, historyBusy = false;
    async function loadHistory(page = 1) {
      if (historyBusy || controller.signal.aborted) return;
      historyBusy = true; setContent(historyBody, loading('读取旧版本…'));
      try {
        const data = await apiPage(`items/${pathId(id)}/revisions`, { page, page_size: 10 }, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setContent(historyBody, el('p', { class: 'field-hint' }, '更正与补全前的捕获按原样保留；已经生成的报告快照不随更正改变。'),
          data.items.length ? data.items.map(row => el('article', { class: 'capture-revision' },
            el('h4', {}, row.capture.title || '旧捕获'), el('p', { class: 'muted small-text' }, `被后续捕获替代于 ${dateText(row.revised_at, true)}`),
            sourceLink(row.capture.url), evidenceMetadata(row.capture), el('div', { class: 'source-content' }, itemContent(row.capture) || '旧捕获未保存正文。'))) : empty('没有历史捕获', '当前版本尚未被更正或补全。', [], true),
          pagination(data, next => loadHistory(next)));
        historyLoaded = true;
      } catch (error) { if (!controller.signal.aborted) setContent(historyBody, errorBox(error), button('重新读取历史捕获', () => loadHistory(page), 'secondary small')); }
      finally { historyBusy = false; }
    }
    history.addEventListener('toggle', () => { if (history.open && !historyLoaded) loadHistory(); });
    modal.body.append(history);
    const trashed = item.metadata?.atlas_cleanup?.status === 'trashed';
    if (item.type === 'news' && !trashed) {
      const captured = item.metadata?.content_kind === 'public_article_body';
      const original = field('出版方原文地址（聚合器资料需填写）', { name: 'original_url', type: 'url', value: item.metadata?.body_source_url || '', placeholder: 'https://出版方/新闻原文', maxlength: 2048 });
      const errors = el('div');
      modal.body.append(el('section', { class: 'source-capture', 'aria-label': '新闻内容范围' }, el('h4', {}, captured ? '已保存出版方公开正文' : '补充公开原文'),
        el('p', { class: 'field-hint' }, captured ? '公开页提取不保证包含分页、订阅内容或全部上下文。旧摘要已保留，可重新读取更正。' : '检索摘要或标题能支持的判断有限。读取出版方公开正文后再做分析，无法读取时保留摘要并显示原因。'),
        captured && sourceLink(item.metadata.body_source_url), original, button(captured ? '重新读取公开原文' : '读取公开原文', async event => {
          const input = original.querySelector('input'); if (!input.reportValidity()) return;
          const control = event.currentTarget; control.disabled = true;
          try { const job = await api(`items/${pathId(id)}/read-original`, { method: 'POST', body: { original_url: input.value.trim() } }); modal.close(); notify('原文读取任务已创建，结果与无法读取的原因会保留。'); location.hash = `#jobs?selected=${encodeURIComponent(job.id)}`; }
          catch (error) { setContent(errors, errorBox(error)); control.disabled = false; }
        }, 'secondary'), errors));
    }
    if (trashed) modal.body.append(el('p', { class: 'warning-note' }, '此资料已在回收站，不参与当前统计。可在资料回收站恢复。'));
    else modal.body.append(button('标记并核对活动', () => openActivityReview(item, null, () => {}, controller.signal), 'secondary'));
  } catch (err) { if (!controller.signal.aborted) setContent(modal.body, errorBox(err)); }
}

export function itemRow(item, ctx) {
  return el('button', { class: 'item-row', type: 'button', onClick: () => openItem(item.id, ctx.signal), 'aria-label': `查看资料：${item.title || '无标题'}` }, el('div', { class: 'item-date mono' }, item.published_at ? dateText(item.published_at) : '日期未知'), el('div', { class: 'item-body' }, el('div', { class: 'item-meta' }, badge(item.type), el('span', {}, item.source_name || '来源未标注')), el('h3', {}, item.title || '无标题资料'), el('div', { class: 'item-excerpt' }, itemContent(item).slice(0, 120)), el('div', { class: 'tag-list' }, (item.topics || []).slice(0, 3).map(topic => el('span', { class: 'tag' }, topic)))), el('span', { class: 'row-arrow', 'aria-hidden': 'true' }, '↗'));
}

export async function renderLibrary(ctx) {
  if (ctx.params.get('cleanup') === '1' || ctx.params.get('trash') === '1') return renderCleanup(ctx);
  const params = Object.fromEntries(ctx.params);
  let page = pageNumber(params.page);
  const values = { query: params.query || '', type: params.type || '', topic: params.topic || '', source: params.source || '', start_date: params.start_date || '', end_date: params.end_date || '', page, page_size: 25 };
  const data = await apiPage('items', values, { signal: ctx.signal });
  page = data.page; values.page = page;
  if (params.page && String(page) !== params.page) ctx.replaceParams?.({ ...params, page });
  const content = el('div', {}, heading('05', '资料库', 'EVIDENCE LIBRARY', '保留原始材料、公开来源与研究线索。', [button('自动审核与核验', () => openAutoReview(ctx), 'secondary'), button('资料清理', () => ctx.navigate('library', { ...values, cleanup: 1 }), 'secondary'), button('回收站', () => ctx.navigate('library', { trash: 1 }), 'secondary'), button('导入资料', () => openImport(ctx), '', 'plus')]));
  const form = el('form', { class: 'filter-panel', onSubmit: event => { event.preventDefault(); const next = Object.fromEntries(new FormData(form)); if (next.start_date && next.end_date && next.start_date > next.end_date) { form.querySelector('.filter-error').replaceChildren(errorBox(new Error('开始日期不能晚于结束日期。'))); return; } ctx.navigate('library', next); } });
  form.append(el('div', { class: 'filter-main' }, field('搜索资料', { name: 'query', value: values.query, placeholder: '搜索标题或正文关键词' }), field('资料类型', { name: 'type', value: values.type, options: [['', '全部类型'], ['news', '新闻'], ['social', '社媒']] }), el('button', { class: 'button', type: 'submit' }, '检索')), el('details', { class: 'advanced-filters', open: Boolean(values.topic || values.source || values.start_date || values.end_date) }, el('summary', {}, '主题、来源与日期筛选'), el('div', { class: 'form-grid four' }, field('主题', { name: 'topic', value: values.topic, placeholder: '主题名称' }), field('来源', { name: 'source', value: values.source, placeholder: '来源名称' }), field('开始日期', { name: 'start_date', type: 'date', value: values.start_date }), field('结束日期', { name: 'end_date', type: 'date', value: values.end_date })), el('p', { class: 'field-hint' }, '日期按日本时间 UTC+9 解释，包含结束当天。未知发布日期的资料会明确标注。')), el('div', { class: 'filter-error' }));
  content.append(form, el('div', { class: 'list-heading' }, el('h2', {}, '检索结果 ', el('span', { class: 'count-label mono' }, number(data.total))), Object.values(values).slice(0, 6).some(Boolean) && button('清除筛选', () => ctx.navigate('library'), 'text small')));
  content.append(data.items?.length ? el('div', { class: 'item-list' }, data.items.map(item => itemRow(item, ctx))) : empty('没有匹配的资料', '可以调整筛选条件，或导入已有材料作为研究起点。', [button('导入资料', () => openImport(ctx), 'secondary', 'plus')]));
  content.append(pagination({ ...data, page, page_size: 25 }, next => ctx.navigate('library', { ...values, page: next })));
  return content;
}

export function accountPage(accounts, { query = '', status = '', page = 1 } = {}) {
  const keyword = String(query).trim().toLowerCase();
  const rows = accounts.filter(account => (!status || account.status === status) && (!keyword || [account.name, account.handle, account.organization, account.platform].some(value => String(value || '').toLowerCase().includes(keyword))));
  const page_size = 30;
  const last = Math.max(1, Math.ceil(rows.length / page_size));
  const current = Math.min(last, Math.max(1, Math.trunc(Number(page)) || 1));
  return { items: rows.slice((current - 1) * page_size, current * page_size), total: rows.length, page: current, page_size };
}

export async function renderAccounts(ctx) {
  const data = await api('accounts', { signal: ctx.signal });
  const accounts = data.items || [];
  const content = el('div', {}, heading('06', '组织与账号', 'SOURCE REGISTRY', '类型粗分可手动更改；公开身份核验单独进行。', [button('快速判定账号类型', () => openFastClassification(ctx), 'secondary'), button('查看可拖拽关系图', () => ctx.navigate('connections'), 'secondary', 'network'), button('自动核验候选账号', () => openPublicVerification(ctx), 'secondary'), button('新增账号', () => openAccountEdit(ctx), '', 'plus')]));
  const counters = el('div', { class: 'account-stats' }, ['candidate', 'verified', 'excluded'].map(status => el('div', {}, el('strong', { class: 'mono' }, number(accounts.filter(a => a.status === status).length)), badge(status))));
  const list = el('div', { class: 'account-list', tabindex: '-1', 'aria-label': '账号结果' });
  const paging = el('div');
  let page = pageNumber(ctx.params.get('page'));
  const search = field('搜索名称、组织或用户名', { name: 'search', value: ctx.params.get('query') || '', placeholder: '查找研究账号' });
  const status = field('核验状态', { name: 'status', value: ctx.params.get('status') || '', options: [['', '全部状态'], ['candidate', '待核验'], ['verified', '已核验'], ['excluded', '已排除']] });
  function draw() {
    const keyword = search.querySelector('input').value.trim().toLowerCase();
    const selected = status.querySelector('select').value;
    const result = accountPage(accounts, { query: keyword, status: selected, page });
    page = result.page;
    ctx.replaceParams?.({ query: search.querySelector('input').value, status: selected, ...(page > 1 ? { page } : {}) });
    const rows = result.items;
    setContent(paging, pagination(result, next => { page = next; draw(); list.focus({ preventScroll: true }); list.scrollIntoView({ block: 'start', behavior: 'instant' }); }));
    setContent(list, ...(rows.length ? rows.map(account => el('article', { class: 'account-card', 'data-account-id': account.id }, el('div', { class: 'account-card-head' }, el('span', { class: 'platform mono' }, account.platform), badge(account.status)), el('h2', {}, account.name || account.handle), el('p', { class: 'account-handle mono' }, account.handle || ''), accountTypeSummary(account), accountTypeControls(ctx, account, async value => { Object.assign(account, value); draw(); }), account.organization && el('p', { class: 'muted' }, account.organization), sourceLink(account.url, '打开公开账号'), el('div', { class: 'tag-list' }, (account.tags || []).map(tag => el('span', { class: 'tag' }, tag))), account.evidence_url && el('div', { class: 'evidence-line' }, sourceLink(account.evidence_url, '核验依据')), el('div', { class: 'account-card-actions' }, button('关系', () => openAccountRelations(account, ctx), 'secondary small'), isSocial(account.platform) && button('采集', () => ctx.navigate('network', { compose: 'collect', source: account.platform, handle: account.handle }), 'secondary small'), button('编辑', () => openAccountEdit(ctx, account), 'secondary small'), account.status !== 'verified' && button('核验', () => openVerification(ctx, account), 'small'), account.status !== 'excluded' && button('排除', () => openVerification(ctx, account, false), 'text small')))) : [empty(accounts.length ? '没有匹配的账号' : '建立你的来源档案', accounts.length ? '试试其他关键词或核验状态。' : '添加公开账号，记录组织背景与核验依据。账号记录与采集能力分别管理。', [button('新增账号', () => openAccountEdit(ctx), 'secondary', 'plus')])]));
  }
  const filterChanged = () => { page = 1; draw(); };
  search.querySelector('input').addEventListener('input', filterChanged); status.querySelector('select').addEventListener('change', filterChanged);
  content.append(counters, el('div', { class: 'filter-panel form-grid' }, search, status), list, paging); draw();
  return content;
}
