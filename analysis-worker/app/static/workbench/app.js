import { api, queryString } from './api.js';
import { el, icon, loading, errorBox } from './ui.js';
import { renderOverview } from './overview.js';
import { renderLibrary, renderAccounts } from './library.js';
import { renderReports, renderJobs, renderSettings } from './research.js';
import { renderNetwork } from './network.js';
import { renderRegistry } from './registry.js';

const routes = [
  ['overview', '情报总览', '01', renderOverview],
  ['network', '研究工作台', '02', renderNetwork],
  ['connections', '账号关系图', '03', renderRegistry],
  ['jobs', '任务中心', '04', renderJobs],
  ['library', '资料库', '05', renderLibrary],
  ['accounts', '组织与账号', '06', renderAccounts],
  ['reports', '研究报告', '07', renderReports],
  ['settings', '能力与设置', '08', renderSettings],
];
const nav = document.getElementById('navigation');
const main = document.getElementById('main');
document.querySelector('.skip-link')?.addEventListener('click', event => {
  event.preventDefault();
  main.focus({ preventScroll: true });
  main.scrollIntoView({ block: 'start' });
});
nav.append(...routes.map(([route, title, number]) => el('a', { class: 'nav-link', href: `#${route}`, 'data-route': route }, icon(route === 'connections' ? 'network' : route), el('span', {}, title), el('span', { class: 'nav-number mono' }, number))));
let currentController = null;
let cleanup = [];
function navigate(route, params = {}) { const query = queryString(params); const hash = `#${route}${query ? `?${query}` : ''}`; if (window.location.hash === hash) render(); else window.location.hash = hash; }
async function render({ preserveDialogs = false } = {}) {
  currentController?.abort(); cleanup.forEach(fn => fn()); cleanup = [];
  if (!preserveDialogs) document.querySelectorAll('dialog').forEach(modal => modal.close());
  const controller = new AbortController(); currentController = controller;
  let [requested = 'overview', query = ''] = window.location.hash.slice(1).split('?');
  const legacyParams = new URLSearchParams(query);
  if (requested === 'network' && legacyParams.get('registry') === '1') {
    requested = 'connections'; legacyParams.delete('registry'); query = legacyParams.toString();
    history.replaceState(null, '', `#connections${query ? `?${query}` : ''}`);
  }
  const selected = routes.find(route => route[0] === requested) || routes[0];
  const [route, title, , renderer] = selected;
  if (route !== requested) history.replaceState(null, '', '#overview');
  document.title = `${title} · PEACE ATLAS`;
  nav.querySelectorAll('a').forEach(link => { const active = link.dataset.route === route; link.classList.toggle('active', active); if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current'); });
  const ctx = {
    route, params: new URLSearchParams(query), signal: controller.signal, navigate,
    replaceParams: params => { if (!controller.signal.aborted) { const next = queryString(params); history.replaceState(null, '', `#${route}${next ? `?${next}` : ''}`); } },
    refresh: () => render({ preserveDialogs: true }),
    poll: (task, delay) => {
      let timer;
      const run = async () => { if (controller.signal.aborted) return; await task(); if (!controller.signal.aborted) timer = setTimeout(run, delay); };
      timer = setTimeout(run, delay);
      cleanup.push(() => clearTimeout(timer));
    },
  };
  main.setAttribute('aria-busy', 'true');
  main.replaceChildren(loading(`正在读取${title}…`));
  try {
    const view = await renderer(ctx);
    if (controller.signal.aborted) return;
    main.replaceChildren(view); main.removeAttribute('aria-busy');
  } catch (error) {
    if (controller.signal.aborted) return;
    main.replaceChildren(el('div', { class: 'page-failure' }, el('div', { class: 'eyebrow' }, 'CONNECTION / ATTENTION REQUIRED'), el('h1', {}, `${title}暂时无法读取`), errorBox(error, () => render()), el('p', { class: 'muted' }, '此处是加载失败，不能据此判断资料库为空。请检查本地服务后重试。')));
    main.removeAttribute('aria-busy');
  }
}

async function health() {
  const status = document.getElementById('connection');
  try { const result = await api('health', { timeout: 5000 }); const state = result.status; const ok = result.ok !== false && !['error', 'unhealthy', 'unavailable'].includes(state); status.replaceChildren(el('span', { class: `status-dot ${ok ? '' : 'offline'}` }), ok ? '本地服务已连接' : '服务需要检查'); status.title = '仅表示本地服务可访问，来源采集能力请查看“能力与设置”。'; }
  catch { status.replaceChildren(el('span', { class: 'status-dot offline' }), '本地服务未连接'); }
  setTimeout(health, 30000);
}
window.addEventListener('hashchange', () => render());
window.addEventListener('unhandledrejection', event => { if (event.reason?.name === 'AbortError') event.preventDefault(); });
render(); health();
