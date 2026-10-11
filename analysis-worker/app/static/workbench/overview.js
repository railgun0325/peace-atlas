import { api } from './api.js';
import { el, button, linkButton, panel, empty, number, badge, dateText } from './ui.js';
import { openImport } from './actions.js';
import { itemRow } from './library.js';
import { activityMap } from './activities.js';

function ranking(items, emptyTitle, suffix = '条') {
  if (!items?.length) return empty(emptyTitle, '收录资料后，这里将依据实际记录自动汇总。', [], true);
  const max = Math.max(1, ...items.map(item => Number(item.count || 0)));
  return el('div', { class: 'rankings' }, items.slice(0, 7).map((item, index) => { const fill = el('span', { class: 'rank-fill', 'aria-hidden': 'true' }); fill.style.width = `${Math.max(0, Number(item.count || 0) / max * 100)}%`; return el('div', { class: 'ranking-row' }, el('span', { class: 'ranking-index mono' }, String(index + 1).padStart(2, '0')), el('div', { class: 'ranking-main' }, el('div', { class: 'ranking-label' }, el('span', {}, item.name), el('strong', { class: 'mono' }, `${number(item.count)} ${suffix}`)), el('div', { class: 'rank-track' }, fill))); }));
}
function timelineChart(rows) {
  if (!rows?.length) return empty('时间分布等待资料', '此图反映资料发布日期的分布，不推断现实事件数量。', [], true);
  const visible = rows.slice(-30); const max = Math.max(1, ...visible.map(row => Number(row.count || 0)));
  const bars = el('div', { class: 'timeline-bars', role: 'img', 'aria-label': `资料发布日期分布：${visible.map(row => `${row.date}，${row.count}条`).join('；')}` }, visible.map(row => { const bar = el('div', { class: 'timeline-bar' }, el('span', { class: 'bar-tooltip' }, `${row.date} · ${number(row.count)} 条`)); bar.style.height = `${Math.max(2, Number(row.count || 0) / max * 100)}%`; return el('div', { class: 'timeline-slot', tabindex: '0', 'aria-label': `${row.date}，${number(row.count)} 条` }, bar); }));
  return el('div', { class: 'timeline-chart' }, el('div', { class: 'chart-topline' }, el('span', { class: 'muted' }, '资料条数 / 仅显示有记录日期，非连续时间轴'), el('span', { class: 'mono' }, `MAX ${number(max)}`)), bars, el('div', { class: 'chart-axis mono' }, el('span', {}, visible[0].date), el('span', {}, visible.at(-1).date)));
}

export async function renderOverview(ctx) {
  const data = await api('overview', { signal: ctx.signal });
  const stats = data.stats || {};
  const content = el('div', { class: 'overview' });
  const hero = el('section', { class: 'hero' }, el('div', { class: 'hero-copy' }, el('div', { class: 'eyebrow' }, '01 / INTELLIGENCE OVERVIEW'), el('h1', {}, '观察动态。', el('br'), el('span', {}, '回到证据。')), el('p', {}, '日本和平与反战运动公开信息研究。', el('br'), '从分散的来源，建立可追溯的理解。'), el('div', { class: 'actions' }, linkButton('前往研究工作台', '#network', '', 'arrow'), linkButton('浏览资料库', '#library', 'secondary', 'arrow'))), el('div', { class: 'hero-emblem', 'aria-hidden': 'true' }, el('div', { class: 'emblem-top mono' }, '平和 / PEACE'), el('div', { class: 'emblem-kanji' }, '観'), el('div', { class: 'emblem-code mono' }, '記録 → 検証 → 分析'), el('div', { class: 'emblem-stripe' })));
  content.append(hero);
  content.append(el('section', { class: 'stat-grid', 'aria-label': '工作台实际统计' }, [
    ['收录资料', stats.items, 'TOTAL RECORDS', '#library'], ['新闻材料', stats.news, 'NEWS SOURCES', '#library?type=news'], ['研究报告', stats.reports, 'RESEARCH REPORTS', '#reports'], ['活动任务', stats.active_jobs, 'ACTIVE TASKS', '#jobs'],
  ].map(([label, value, english, href], index) => el('a', { class: `stat-item ${index === 0 ? 'stat-primary' : ''}`, href }, el('div', { class: 'stat-label' }, label, el('span', { 'aria-hidden': 'true' }, '↗')), el('strong', { class: 'stat-value mono' }, number(value)), el('span', { class: 'eyebrow' }, english)))));
  if (!stats.items) content.append(el('section', { class: 'onboarding' }, el('div', { class: 'onboarding-symbol mono', 'aria-hidden': 'true' }, '+'), el('div', {}, el('h2', {}, '从第一份公开材料开始'), el('p', {}, '创建新闻采集任务，或导入已有 JSON 资料。所有统计、趋势和报告都由真实收录材料生成。')), button('导入已有资料', () => openImport(ctx), 'secondary', 'arrow')));
  content.append(el('div', { class: 'dashboard-grid' }, panel('资料时间分布', 'PUBLICATION DATES', timelineChart(data.timeline), 'wide-panel'), panel('主题分布', 'TOPIC SIGNALS', ranking(data.topics, '尚无主题资料')), panel('来源构成', 'SOURCE COVERAGE', ranking(data.sources, '尚无来源记录')), panel('地点提及', 'LOCATION MENTIONS', ranking(data.locations, '尚无地点提及'), 'location-panel')));
  content.append(activityMap(ctx));
  content.append(el('div', { class: 'dashboard-lower' }, panel('最近收录', 'LATEST RECORDS', data.recent_items?.length ? el('div', {}, el('div', { class: 'item-list compact-list' }, data.recent_items.slice(0, 5).map(item => itemRow(item, ctx))), linkButton('查看全部资料', '#library', 'text small', 'arrow')) : empty('资料库尚为空', '完成采集或导入后，在这里查看最新的原始材料。', [], true)), panel('任务动态', 'OPERATIONS', data.recent_jobs?.length ? el('div', { class: 'recent-jobs' }, data.recent_jobs.slice(0, 5).map(job => el('a', { class: 'recent-job', href: `#jobs?selected=${encodeURIComponent(job.id)}` }, el('div', {}, el('strong', {}, job.kind === 'report' ? '报告生成' : job.kind === 'research' ? '深度研究' : job.kind === 'login' ? '会话检测' : '资料采集'), el('span', { class: 'muted small-text' }, dateText(job.created_at, true))), badge(job.status))), linkButton('前往任务中心', '#jobs', 'text small', 'arrow')) : empty('没有执行记录', '创建任务后，可查看进度、结果与来源诊断。', [], true))));
  return content;
}
