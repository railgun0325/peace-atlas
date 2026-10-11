import { api, apiPage, pathId } from './api.js';
import { openFirefoxImport } from './firefox.js';
import { openBrowserBridge } from './session_bridge.js';
import { el, button, badge, dateText, number, sourceLink, dialog, loading, errorBox, empty, notify, setContent, validateDates, pagination } from './ui.js';
import { keywordQueries } from './keyword_tools.js';

export const platformName = id => ({ twitter: 'Twitter / X', instagram: 'Instagram', google_news: 'Google News', rss: 'RSS / Atom' })[id] || id || '公开来源';
export const modeName = id => ({ keyword: '关键词发现', account: '指定公开账号', hashtag: '主题标签' })[id] || id || '公开采集';
export const isSocial = source => ['twitter', 'instagram'].includes(source);
export function socialModes(source, platform) {
  const supported = source === 'twitter' ? ['keyword', 'hashtag', 'account'] : source === 'instagram' ? ['keyword', 'hashtag', 'account'] : [];
  return Array.isArray(platform?.modes) ? supported.filter(mode => platform.modes.includes(mode)) : supported;
}
function integer(value, fallback, min, max, label) {
  const parsed = value === undefined || value === '' ? fallback : Number(value);
  if (!Number.isInteger(parsed) || parsed < min || parsed > max) throw new Error(`${label}必须是 ${min}–${max} 之间的整数。`);
  return parsed;
}
export function collectParams(values, platform) {
  validateDates(values);
  const source = values.source;
  if (!['google_news', 'rss', 'twitter', 'instagram'].includes(source)) throw new Error('请选择有效的采集来源。');
  const params = { source, start_date: values.start_date, end_date: values.end_date, max_records: integer(values.max_records, 50, 1, 100, '最多保存条数') };
  const keywords = (mode = 'keyword') => {
    const keyword = String(values.keyword || '').trim();
    if (!keyword) throw new Error(mode === 'hashtag' ? '请填写主题标签。' : '请填写检索关键词。');
    const queries = keywordQueries(keyword, values.keyword_mode || 'any', mode);
    return { keyword: mode === 'hashtag' ? queries[0] : keyword, ...(Object.hasOwn(values, 'keyword_mode') && mode !== 'hashtag' ? { keyword_mode: values.keyword_mode } : {}) };
  };
  if (!isSocial(source)) {
    if (source === 'rss') params.feed_url = String(values.feed_url || '').trim();
    else Object.assign(params, keywords());
    return params;
  }
  const mode = values.mode || 'keyword';
  if (!socialModes(source, platform).includes(mode)) throw new Error('当前平台不支持此采集方式，请重新选择。');
  const relevance = values.relevance || 'focused';
  if (!['focused', 'broad'].includes(relevance)) throw new Error('请选择有效的相关性筛选方式。');
  const handle = String(values.handle || '').trim().replace(/^@/, '').toLowerCase();
  if (mode === 'account' && !(source === 'twitter' ? /^[a-z0-9_]{1,15}$/ : /^[a-z0-9_.]{1,30}$/).test(handle)) throw new Error('请填写有效的公开账号用户名，不填网址或 @。');
  const include_following = values.include_following === true || values.include_following === 'on';
  return { ...params, mode, relevance, ...(mode === 'account' ? { handle } : keywords(mode)), max_accounts: integer(values.max_accounts, 3, 1, 10, '最多访问账号'), max_scrolls: integer(values.max_scrolls, 5, 1, 20, '每页最多滚动'), include_following, max_following: include_following ? integer(values.max_following, 10, 0, 50, '本次最多关注关系') : 0 };
}

export function jobTitle(job) {
  const params = job.params || {};
  if (params._account_classification) return 'DS 快速判定账号类型';
  if (params._evidence_review) return 'DeepSeek 自动审核与核验';
  if (params._account_verification) return '公开账号自动核验';
  if (params._news_capture) return '出版方公开原文读取';
  if (job.kind === 'login') return `${platformName(params.platform)} · ${params.method === 'firefox' ? 'Firefox 会话导入' : params.method === 'auto' ? '已有登录检测与复用' : '手动登录'}`;
  if (job.kind === 'report') return params.title || '研究报告生成';
  if (job.kind === 'research') return params.title || `深度研究 · ${params.keyword || '公开账号网络'}`;
  const name = platformName(params.source);
  return isSocial(params.source) ? `${name} · ${params.handle ? `@${params.handle}` : params.keyword || modeName(params.mode) || '公开资料采集'}` : params.keyword || `${name} 公开资料采集`;
}

export async function openSystemBrowserLogin(ctx, platform) {
  if (!isSocial(platform)) throw new Error('请选择 X 或 Instagram 登录。');
  const result = await api('social/browser/open', { method: 'POST', body: { platform }, signal: ctx.signal });
  if (result.opened !== true) throw new Error('系统默认浏览器未确认打开，请重试。');
  notify(`已请求在系统默认浏览器打开 ${platformName(platform)}。完成登录后，请回工作台同步会话。`);
  return result;
}

function waitForSessionCheck(signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(signal.reason || new DOMException('检查已取消。', 'AbortError')); return; }
    const abort = () => { clearTimeout(timer); reject(signal.reason || new DOMException('检查已取消。', 'AbortError')); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, 500);
    signal?.addEventListener('abort', abort, { once: true });
  });
}

export async function detectSocialSession(ctx, platform) {
  if (!isSocial(platform)) throw new Error('请选择 X 或 Instagram 登录。');
  const job = await api('jobs', { method: 'POST', body: { kind: 'login', params: { platform, method: 'auto' } }, signal: ctx.signal });
  if (!job.id) throw new Error('登录检测任务缺少标识，请刷新后重试。');
  for (let attempt = 0; attempt < 240; attempt++) {
    const current = await api(`jobs/${pathId(job.id)}`, { signal: ctx.signal });
    if (current.status === 'succeeded') {
      const result = current.result || {};
      if (typeof result.verified !== 'boolean' || typeof result.requires_login !== 'boolean') throw new Error('登录检测结果无法确认，请在任务中心查看详情。');
      if (result.verified === true) return { ...result, job_id: job.id };
      if (result.requires_login === true) {
        await openSystemBrowserLogin(ctx, platform);
        return { ...result, job_id: job.id, browser_opened: true };
      }
      return { ...result, job_id: job.id };
    }
    if (['failed', 'cancelled', 'interrupted'].includes(current.status)) throw new Error(current.error?.message || current.error || '登录检测未完成，请在任务中心查看详情。');
    await waitForSessionCheck(ctx.signal);
  }
  throw new Error('登录检测仍在执行，请稍后在任务中心查看结果。');
}

export function sessionCheckMessage(result) {
  if (result.verified === true) return result.source === 'firefox' ? '已自动复用 Firefox 登录，并验证采集会话可用。可以开始采集。' : '已验证工作台保存的登录会话可用。可以开始采集。';
  if (result.browser_opened === true) return '未发现可用的现有会话，已请求打开系统默认浏览器。完成登录后回工作台再次检测；本次打开尚未同步采集会话。';
  return result.message || '已有登录暂时无法验证，请查看任务诊断后重试。';
}

export async function socialSessions(ctx, onCollect) {
  const section = el('section', { class: 'social-sessions', 'aria-label': '社媒登录会话' });
  const content = el('div', {}, loading('检查社媒浏览器与登录会话…'));
  let requestVersion = 0;
  section.append(el('div', { class: 'social-section-heading' }, el('div', {}, el('div', { class: 'eyebrow' }, 'SOCIAL SOURCES / LOCAL SESSIONS'), el('h2', {}, '社媒来源与登录')), el('p', {}, '优先检测并复用已有登录。需要登录时，使用电脑的系统默认浏览器；浏览器由系统设置决定。')), content);
  async function refresh() {
    const version = ++requestVersion;
    try {
      const data = await api('social', { signal: ctx.signal });
      if (ctx.signal.aborted || version !== requestVersion) return;
      const runtime = data.runtime || {};
      const platforms = data.platforms || [];
      setContent(content, el('div', { class: 'social-runtime' }, el('span', { class: 'mono' }, 'BROWSER ENGINE'), badge(runtime.ready ? 'ready' : 'runtime_unavailable'), el('span', {}, `采集组件${runtime.playwright_installed ? '已安装' : '未安装'} · 浏览器${runtime.browser_installed ? '已安装' : '未安装'}`)), platforms.length ? el('div', { class: 'social-session-grid' }, platforms.map(platform => {
        const errors = el('div');
        const stateBadge = el('span', {}, badge(platform.state));
        const detail = el('p', {}, platform.detail || '登录状态以实际访问结果为准。');
        const savedSession = el('dd', {}, platform.session_available ? '本地已有会话，尚需任务验证' : '尚无可用会话');
        const detect = button('检测并复用已有登录', async () => {
          detect.disabled = true; setContent(errors, loading('正在验证保存会话并检查可复用的本机登录…'));
          try {
            const result = await detectSocialSession(ctx, platform.id);
            if (ctx.signal.aborted) return;
            setContent(errors, el('p', { class: result.verified ? 'field-hint' : 'warning-note', role: 'status' }, sessionCheckMessage(result)));
            if (result.verified === true) {
              collect.disabled = false; setContent(stateBadge, badge('ready'));
              detail.textContent = sessionCheckMessage(result); savedSession.textContent = '本地会话已通过本次访问验证';
              notify(sessionCheckMessage(result));
            } else { collect.disabled = true; setContent(stateBadge, badge(result.state || 'session_unverified')); }
          } catch (error) { if (!ctx.signal.aborted) setContent(errors, errorBox(error)); }
          finally { detect.disabled = false; }
        }, 'small', 'refresh');
        detect.disabled = !runtime.ready;
        const login = button('用系统默认浏览器登录', async () => {
          login.disabled = true; setContent(errors);
          try { await openSystemBrowserLogin(ctx, platform.id); setContent(errors, el('p', { class: 'field-hint', role: 'status' }, '已请求打开系统默认浏览器。登录完成后，请回工作台同步会话；当前采集会话尚未更新。')); }
          catch (error) { setContent(errors, errorBox(error)); }
          finally { login.disabled = false; }
        }, 'secondary small', 'external');
        const collect = button('新建采集', () => onCollect(platform.id), 'small', 'plus');
        collect.disabled = !runtime.ready || !platform.session_available;
        const firefox = platform.id === 'twitter' ? button('同步 Firefox 会话', () => openFirefoxImport(ctx, () => openSystemBrowserLogin(ctx, 'twitter')), 'secondary small') : null;
        if (firefox) firefox.disabled = !runtime.ready || platform.state === 'runtime_unavailable';
        const bridge = button('连接已登录的 Chrome / Edge', () => openBrowserBridge(ctx, platform.id), 'secondary small');
        return el('article', { class: 'social-session' }, el('div', { class: 'social-session-top' }, el('span', { class: 'social-glyph mono', 'aria-hidden': 'true' }, platform.id === 'twitter' ? 'X' : 'IG'), stateBadge), el('h3', {}, platform.name || platformName(platform.id)), detail, el('dl', { class: 'metadata' }, el('dt', {}, '保存会话'), savedSession, el('dt', {}, '更新时间'), el('dd', {}, platform.session_updated_at ? dateText(platform.session_updated_at, true) : '尚未保存'), el('dt', {}, '采集方式'), el('dd', {}, socialModes(platform.id, platform).map(modeName).join(' / ') || '暂不可用')), el('div', { class: 'actions' }, detect, collect, login, firefox, bridge), errors);
      })) : empty('尚无社媒来源配置', '来源状态由本地服务提供，请检查应用版本或刷新状态。', [], true), el('div', { class: 'social-session-foot' }, el('p', { class: 'field-hint' }, '检测会先验证工作台会话，X 与 Instagram 均可自动复用默认 Firefox 中已登录的会话。Chrome / Edge 可通过本地会话连接扩展同步，首次需要你安装并确认连接。打开默认浏览器本身不代表采集会话已同步。'), button('刷新会话状态', refresh, 'text small', 'refresh')));
    } catch (error) { if (!ctx.signal.aborted && version === requestVersion) setContent(content, errorBox(error, refresh)); }
  }
  await refresh();
  return section;
}

const diagnosticLabels = { ok: '读取完成', no_matches: '本次没有匹配材料', date_filtered_out: '本次材料不在日期范围内', partial: '部分完成', parse_error: '页面内容无法确认', challenge: '需要本人验证', missing_date: '发布时间未知', unknown_date: '发布时间未知', outside_date_range: '日期不在范围内', date_out_of_range: '日期不在范围内', login_required: '需要登录', session_expired: '登录已过期', challenge_or_rate_limited: '平台要求验证或限制请求', rate_limited: '平台限制请求', runtime_unavailable: '浏览器组件不可用', no_results: '未发现结果', detail_failed: '帖子详情读取失败' };
const diagnosticCounts = { scanned: '检查帖子', irrelevant: '相关性排除', query_mismatch: '检索条件不符', author_mismatch: '作者不符', reposts_filtered: '转帖排除', skipped_article_cards: '无正文文章卡', duplicates: '重复', date_filtered: '日期范围外', unknown_dates: '日期未知' };
export function diagnosticsView(diagnostics) {
  if (!Array.isArray(diagnostics) || !diagnostics.length) return null;
  return el('section', { class: 'source-diagnostics' }, el('h3', {}, '来源诊断'), el('ul', {}, diagnostics.map(entry => {
    if (typeof entry === 'string') return el('li', {}, el('p', {}, entry));
    const item = entry || {};
    const code = item.outcome || item.code || item.status || item.reason;
    return el('li', {}, el('div', { class: 'item-meta' }, el('strong', {}, diagnosticLabels[code] || code || '执行信息'), item.count !== undefined && el('span', { class: 'mono' }, `${number(item.count)} 条`)), el('p', {}, item.message || item.detail || item.error || ''), el('div', { class: 'diagnostic-counts' }, Object.entries(diagnosticCounts).filter(([key]) => item[key] !== undefined).map(([key, label]) => el('span', {}, `${label} ${number(item[key])}`))), (item.url || item.evidence_url) && sourceLink(item.url || item.evidence_url, '核对来源'));
  })));
}

export function evidenceDescription(item) {
  const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {};
  const rawKind = metadata.content_kind || metadata.content_source || metadata.text_source || metadata.extraction_method || '';
  const clue = /alt|image|clue|link_only/.test(String(rawKind));
  return { metadata, kind: clue ? '图片说明 / 线索' : rawKind === 'rss_summary' ? 'RSS 标题与摘要 · 未读取完整原文' : rawKind === 'legacy_unverified' ? '历史材料 · 提取方式待核对' : /caption|body|detail|text|dom/.test(String(rawKind)) ? '页面正文' : '未标注提取方式', date: item.published_at ? '已保存原始发布时间' : '发布时间未知 · 不计入有日期范围的研究材料', clue };
}
export function sourceVerification(item) {
  if (item.type !== 'social') return null;
  const linked = Boolean(item.account_id);
  const status = linked && ['candidate', 'verified', 'excluded'].includes(item.account_status) ? item.account_status : 'candidate';
  return el('section', { class: 'source-verification', 'aria-label': '来源账号核验状态' },
    el('h4', {}, '来源账号核验'), badge(status),
    !linked && el('p', { class: 'field-hint' }, '未关联账号，材料按待核验来源保留。'),
    linked && item.account_url && sourceLink(item.account_url, '查看来源账号'),
    status === 'verified' && (item.evidence_url ? sourceLink(item.evidence_url, '公开核验依据') : el('p', { class: 'warning-note' }, '账号标记已核验，但此记录未提供公开核验依据。')),
    status === 'candidate' && el('p', { class: 'field-hint' }, '账号身份与背景尚未完成核验；该材料不自动成为可信来源。'),
    status === 'excluded' && el('p', { class: 'warning-note' }, '关联账号已排除；该材料不纳入新报告。'));
}
export function evidenceMetadata(item) {
  const info = evidenceDescription(item);
  if (item.type !== 'social' && !Object.keys(info.metadata).length) return null;
  const relevance = info.metadata.relevance || {};
  const unreliableLegacyEngagement = info.metadata.engagement_verified === false && Object.hasOwn(info.metadata, 'legacy_engagement');
  const engagement = unreliableLegacyEngagement ? {} : info.metadata.engagement || {};
  const engagementLabels = { likes: '点赞', like_count: '点赞', comments: '评论', replies: '评论', comment_count: '评论', reposts: '转发', repost_count: '转发', views: '浏览' };
  return el('section', { class: 'evidence-provenance' }, sourceVerification(item), el('div', { class: 'eyebrow' }, 'PROVENANCE / CONTENT & TIME'), el('h4', {}, '材料提取说明'), el('dl', { class: 'metadata' }, el('dt', {}, '文本来源'), el('dd', {}, info.kind), el('dt', {}, '时间依据'), el('dd', {}, info.date), info.metadata.collected_at && el('dt', {}, '采集时间'), info.metadata.collected_at && el('dd', {}, dateText(info.metadata.collected_at, true)), info.metadata.capture_version && el('dt', {}, '捕获版本'), info.metadata.capture_version && el('dd', {}, String(info.metadata.capture_version)), relevance.mode && el('dt', {}, '筛选方式'), relevance.mode && el('dd', {}, relevance.mode === 'focused' ? '日本和平 / 反战研究相关' : '按检索条件 · 候选材料'), (info.metadata.query || info.metadata.discovery_query) && el('dt', {}, '检索条件'), (info.metadata.query || info.metadata.discovery_query) && el('dd', {}, String(info.metadata.query || info.metadata.discovery_query))), info.clue && el('p', { class: 'warning-note' }, '这段内容是图片说明或发现线索，可能不等同于完整原帖正文。请打开原始来源复查。'), Array.isArray(relevance.reasons) && relevance.reasons.length > 0 && el('p', { class: 'field-hint' }, `匹配依据：${relevance.reasons.join('；')}`), unreliableLegacyEngagement && el('p', { class: 'field-hint' }, '历史库未保留可靠互动指标。'), el('div', { class: 'diagnostic-counts' }, Object.entries(engagementLabels).filter(([key]) => engagement[key] !== undefined && engagement[key] !== null).map(([key, label]) => el('span', {}, `${label} ${number(engagement[key])}`))), info.metadata.image_alt && !info.clue && el('details', { class: 'technical-details' }, el('summary', {}, '附带图片说明'), el('p', { class: 'source-content' }, String(info.metadata.image_alt))), Object.keys(info.metadata).length > 0 && el('details', { class: 'technical-details' }, el('summary', {}, '查看来源元数据'), el('pre', {}, JSON.stringify(info.metadata, null, 2))));
}

export function relationObservationLabels(relation) {
  const first = relation.observed_at ? `首次观察 ${dateText(relation.observed_at, true)}` : '首次观察时间未保存';
  const last = relation.last_observed_at && relation.last_observed_at !== relation.observed_at ? `最近观察 ${dateText(relation.last_observed_at, true)}` : null;
  return [first, last].filter(Boolean);
}

export async function openAccountRelations(account, ctx) {
  const modal = dialog('公开账号关系', { drawer: true, subtitle: 'SOURCE CONNECTIONS / OBSERVED EVIDENCE' });
  const controller = new AbortController();
  const abort = () => controller.abort();
  ctx.signal.addEventListener('abort', abort, { once: true });
  modal.modal.addEventListener('close', () => { controller.abort(); ctx.signal.removeEventListener('abort', abort); }, { once: true });
  modal.body.append(loading('读取已保存的账号关系…'));
  modal.footer.append(button('打开可拖拽关系图', () => { modal.close(); ctx.navigate('network', { registry: 1, account: `${account.platform}:${account.handle?.replace(/^@/, '').toLowerCase()}` }); }, 'secondary', 'network'));
  let generation = 0;
  async function load(page = 1) {
    const request = ++generation;
    setContent(modal.body, loading('读取已保存的账号关系…'));
    try {
      const data = await apiPage(`accounts/${pathId(account.id)}/relations`, { page, page_size: 1000 }, { signal: controller.signal });
      if (!modal.modal.isConnected || controller.signal.aborted || request !== generation) return;
      setContent(modal.body, el('h3', { class: 'detail-title' }, account.name || account.handle), el('p', { class: 'intro' }, '关系来自采集时观察到的公开页面。关注关系不代表组织隶属、观点认同或账号已经核验。'), el('p', { class: 'muted' }, `${number(data.total)} 条已记录关系 · 本页显示 ${number(data.items?.length || 0)} 条`), data.items?.length ? el('ol', { class: 'relation-list' }, data.items.map(item => el('li', {}, el('div', { class: 'relation-path' }, el('strong', {}, item.source_name || '未知来源账号'), el('span', { class: 'relation-arrow', 'aria-label': item.relation === 'follows' ? '关注' : item.relation || '关联' }, '→'), el('strong', {}, item.target_name || '未知目标账号')), el('div', { class: 'item-meta' }, el('span', {}, item.relation === 'follows' ? '公开关注关系' : item.relation || '公开关联'), relationObservationLabels(item).map(label => el('span', {}, label))), item.evidence_url ? sourceLink(item.evidence_url, '查看关系证据') : el('p', { class: 'field-hint' }, '旧库未保存关系证据链接')))) : empty('尚无关系记录', '采集时启用“发现公开关注关系”后，成功获取的关系会保存在此处。', [], true), pagination(data, load));
    } catch (error) { if (!controller.signal.aborted && request === generation) setContent(modal.body, errorBox(error, () => load(page))); }
  }
  await load();
}
