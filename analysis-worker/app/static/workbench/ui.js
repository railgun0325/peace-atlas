import { download } from './api.js';

function contentNodes(children) { return children.flat(Infinity).filter(child => child !== null && child !== undefined && typeof child !== 'boolean').map(child => child instanceof Node ? child : document.createTextNode(String(child))); }
export function appendContent(node, ...children) { node.append(...contentNodes(children)); return node; }
export function setContent(node, ...children) { node.replaceChildren(...contentNodes(children)); return node; }
export function beforeContent(node, ...children) { node.before(...contentNodes(children)); }
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'checked' || key === 'disabled' || key === 'hidden' || key === 'selected') node[key] = Boolean(value);
    else node.setAttribute(key, String(value));
  }
  appendContent(node, ...children);
  return node;
}

const icons = {
  overview: ['M3 3h7v7H3z', 'M14 3h7v7h-7z', 'M3 14h7v7H3z', 'M14 14h7v7h-7z'],
  library: ['M5 3h12l3 3v15H5z', 'M8 9h9M8 13h9M8 17h6'],
  accounts: ['M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2', 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8', 'M17 4a4 4 0 0 1 0 7M22 21v-2a4 4 0 0 0-3-3.87'],
  network: ['M9 2h6v6H9z', 'M2 16h6v6H2z', 'M16 16h6v6h-6z', 'M12 8v4M5 16v-4h14v4'],
  reports: ['M4 3h16v18H4z', 'M8 7h8M8 11h8M8 15h3M8 18h8'],
  jobs: ['M8 5h13M8 12h13M8 19h13', 'M3 5h1M3 12h1M3 19h1'],
  settings: ['M4 6h16M4 12h16M4 18h16', 'M8 3v6M16 9v6M10 15v6'],
  arrow: ['M5 12h14M13 6l6 6-6 6'],
  external: ['M14 3h7v7M21 3l-9 9M10 5H4v15h15v-6'],
  plus: ['M12 5v14M5 12h14'],
  close: ['M6 6l12 12M18 6 6 18'],
  refresh: ['M20 7v5h-5M4 17v-5h5', 'M6 7a7 7 0 0 1 12-2l2 2M4 17l2 2a7 7 0 0 0 12-2'],
  search: ['M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14M16 16l5 5'],
  download: ['M12 3v12M7 10l5 5 5-5M4 16v5h16v-5'],
};
export function icon(name, size = 18) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  Object.entries({ viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6', 'stroke-linecap': 'square', 'stroke-linejoin': 'miter', 'aria-hidden': 'true' }).forEach(([k, v]) => svg.setAttribute(k, v));
  (icons[name] || icons.arrow).forEach(d => { const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', d); svg.append(path); });
  return svg;
}
export function button(label, onClick, variant = '', iconName) {
  return el('button', { class: `button ${variant}`, type: 'button', onClick }, iconName && icon(iconName), label);
}
export function linkButton(label, href, variant = '', iconName) { return el('a', { class: `button ${variant}`, href }, iconName && icon(iconName), label); }
export function downloadButton(label, path, { signal } = {}, variant = 'secondary small') {
  const control = button(label, async () => {
    if (control.disabled) return;
    control.disabled = true; control.setAttribute('aria-busy', 'true');
    try { await download(path, { signal }); if (!signal?.aborted) notify('文件已准备好，请在下载列表中确认保存位置。'); }
    catch (error) { if (!signal?.aborted) notify(error.message || '下载未完成，请重试。', 'error'); }
    finally { control.disabled = false; control.removeAttribute('aria-busy'); }
  }, variant, 'download');
  return control;
}
export function safeURL(value) { try { const url = new URL(String(value)); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; } catch { return null; } }
export function sourceLink(value, label = '查看原始来源') { const href = safeURL(value); return href ? el('a', { class: 'source-link', href, target: '_blank', rel: 'noopener noreferrer' }, label, icon('external', 14)) : el('span', { class: 'muted' }, '未提供有效来源链接'); }
export const number = value => Number(value || 0).toLocaleString('zh-CN');
export function dateText(value, time = false) { if (!value) return '日期未知'; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit', ...(time ? { hour: '2-digit', minute: '2-digit' } : {}) }).format(date); }
export function localDate(date = new Date()) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; }
function japanCalendarDate(reference = new Date()) { const parts = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Tokyo', year: 'numeric', month: 'numeric', day: 'numeric' }).formatToParts(reference); const get = key => Number(parts.find(part => part.type === key).value); return new Date(get('year'), get('month') - 1, get('day')); }
export function recentDates(days = 30, reference = new Date()) { const end = japanCalendarDate(reference); const start = new Date(end); start.setDate(start.getDate() - days + 1); return { start: localDate(start), end: localDate(end) }; }
export function quarterDates(which = 'current', reference = new Date()) { const now = japanCalendarDate(reference); const month = Math.floor(now.getMonth() / 3) * 3 - (which === 'last' ? 3 : 0); return { start: localDate(new Date(now.getFullYear(), month, 1)), end: localDate(new Date(now.getFullYear(), month + 3, 0)) }; }
export const labels = { queued: '等待执行', running: '执行中', succeeded: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消', interrupted: '已中断', candidate: '待核验', verified: '已核验', excluded: '已排除', collect: '信息采集', report: '报告生成', evidence: '证据整理', ai: 'AI 分析', news: '新闻', social: '社媒', ready: '就绪', unverified: '待实测', requires_login: '需要登录', login: '浏览器登录', login_required: '需要登录', session_unverified: '会话待实测', runtime_unavailable: '组件未就绪', unavailable: '暂不可用' };
export function badge(status) { return el('span', { class: `badge badge-${Object.hasOwn(labels, status) ? status : 'unknown'}` }, labels[status] || status || '未知'); }
export function heading(numberText, title, english, description, actions = []) { return el('div', { class: 'page-heading' }, el('div', { class: 'heading-main' }, el('span', { class: 'section-number mono' }, numberText), el('div', {}, el('div', { class: 'eyebrow' }, english), el('h1', {}, title), description && el('p', { class: 'page-description' }, description))), el('div', { class: 'actions' }, actions)); }
export function panel(title, subtitle, content, className = '') { return el('section', { class: `panel ${className}` }, el('div', { class: 'panel-heading' }, el('h2', {}, title), subtitle && el('span', { class: 'eyebrow' }, subtitle)), content); }
export function empty(title, description, actions = [], compact = false) { return el('div', { class: `empty ${compact ? 'compact' : ''}` }, el('span', { class: 'empty-mark mono', 'aria-hidden': 'true' }, '∅'), el('h3', {}, title), el('p', {}, description), el('div', { class: 'actions' }, actions)); }
export function loading(message = '正在读取资料…') { return el('div', { class: 'loading', role: 'status' }, el('span', { class: 'loader', 'aria-hidden': 'true' }), message); }
export function errorBox(error, retry) { return el('div', { class: 'error-box', role: 'alert' }, el('strong', {}, '操作未完成'), el('p', {}, error.message || String(error)), retry && button('重试', retry, 'small', 'refresh')); }
export function notify(message, kind = 'success') { const item = el('div', { class: `notice ${kind}`, role: kind === 'error' ? 'alert' : 'status' }, el('span', {}, message), button('关闭', () => item.remove(), 'text small')); document.getElementById('notifications').append(item); setTimeout(() => item.remove(), 6500); }
export function field(label, { type = 'text', name, value = '', options, required = false, hint, ...attrs } = {}) {
  const id = `field-${name}-${Math.random().toString(36).slice(2, 8)}`;
  let input;
  if (options) input = el('select', { id, name, required, ...attrs }, options.map(option => { const [val, text] = typeof option === 'string' ? [option, option] : option; return el('option', { value: val, selected: val === value }, text); }));
  else if (type === 'textarea') input = el('textarea', { id, name, required, ...attrs }, value);
  else input = el('input', { id, name, type, value, required, ...attrs });
  return el('div', { class: 'field' }, el('label', { for: id }, label, required && el('span', { class: 'required' }, ' *')), input, hint && el('p', { class: 'field-hint' }, hint));
}
export function check(name, label, checked = true) { return el('label', { class: 'checkbox' }, el('input', { name, type: 'checkbox', checked }), el('span', {}, label)); }
export function formData(form) { return Object.fromEntries(new FormData(form)); }
export function validateDates(values) { if (values.start_date && values.end_date && values.start_date > values.end_date) throw new Error('开始日期不能晚于结束日期。'); }
export function dialog(title, { drawer = false, subtitle, dismissBackdrop = true } = {}) {
  const previous = document.activeElement;
  const modal = el('dialog', { class: drawer ? 'dialog drawer' : 'dialog' });
  let dismissed = false;
  const cleanup = () => {
    if (dismissed) return;
    dismissed = true;
    modal.remove();
    if (previous?.isConnected) previous.focus();
  };
  // HTMLDialogElement.close() clears open synchronously but its close event is
  // queued. Detach now so a response arriving before that event cannot treat a
  // dismissed drawer as active and reopen it. The event remains available to
  // draft/save listeners; focus restoration happens only once.
  const close = () => { modal.close(); cleanup(); };
  const body = el('div', { class: 'dialog-body' });
  const footer = el('div', { class: 'dialog-footer' });
  modal.append(el('div', { class: 'dialog-heading' }, el('div', {}, subtitle && el('div', { class: 'eyebrow' }, subtitle), el('h2', {}, title)), el('button', { class: 'icon-button', type: 'button', 'aria-label': '关闭对话框', onClick: close }, icon('close'))), body, footer);
  modal.setAttribute('aria-label', title);
  modal.addEventListener('close', cleanup, { once: true });
  modal.addEventListener('cancel', event => { event.preventDefault(); close(); });
  modal.addEventListener('click', event => { if (dismissBackdrop && event.target === modal) { const bounds = modal.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close(); } });
  document.body.append(modal); modal.showModal();
  return { modal, body, footer, close };
}
const submittingForms = new WeakSet();
export async function submit(form, task) {
  if (submittingForms.has(form) || !form.reportValidity()) return;
  submittingForms.add(form); form.setAttribute('aria-busy', 'true');
  const error = form.querySelector('.form-error');
  error?.replaceChildren();
  const buttons = [...form.querySelectorAll('button[type=submit]')];
  buttons.forEach(b => b.disabled = true);
  try { await task(formData(form)); } catch (err) { (error || form).append(errorBox(err)); } finally { submittingForms.delete(form); form.removeAttribute('aria-busy'); buttons.forEach(b => b.disabled = false); }
}
export function pagination({ total, page, page_size }, onPage) { const last = Math.max(1, Math.ceil(Number(total || 0) / page_size)); const prev = button('上一页', () => onPage(page - 1), 'secondary small'); const next = button('下一页', () => onPage(page + 1), 'secondary small'); prev.disabled = page <= 1; next.disabled = page >= last; return el('div', { class: 'pagination' }, el('span', { class: 'muted' }, `共 ${number(total)} 条`), el('div', { class: 'actions' }, prev, el('span', { class: 'mono' }, `${page} / ${last}`), next)); }

// A deliberately small Markdown reader: DOM nodes only, never executable HTML.
function inline(text) {
  const output = document.createDocumentFragment();
  const pattern = /\[([^\]]+)\]\((?:<(https?:\/\/[^>]+)>|(https?:\/\/[^\s)]+))\)|\*\*([^*]+)\*\*|`([^`]+)`/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) { output.append(text.slice(last, match.index)); if (match[1]) output.append(sourceLink(match[2] || match[3], match[1])); else if (match[4]) output.append(el('strong', {}, match[4])); else output.append(el('code', {}, match[5])); last = match.index + match[0].length; }
  output.append(text.slice(last)); return output;
}
function tableCells(line) {
  const text = line.trim();
  const cells = []; let cell = '', separators = 0;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (char === '\\' && ['|', '\\'].includes(text[index + 1])) { cell += text[++index]; }
    else if (char === '|') { cells.push(cell.trim()); cell = ''; separators++; }
    else cell += char;
  }
  if (!separators) return null;
  cells.push(cell.trim());
  if (text.startsWith('|') && cells[0] === '') cells.shift();
  if (text.endsWith('|') && cells.at(-1) === '') cells.pop();
  return cells;
}

export function markdown(source) {
  const article = el('article', { class: 'prose' });
  const lines = String(source || '').split('\n');
  let code = null, list = null;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    if (line.startsWith('```')) { if (code) code = null; else { code = el('code'); article.append(el('pre', {}, code)); } list = null; continue; }
    if (code) { code.append(line + '\n'); continue; }
    if (!line.trim()) { list = null; continue; }
    const headers = tableCells(line);
    const separator = tableCells(lines[index + 1] || '');
    if (headers?.length && separator?.length === headers.length && separator.every(cell => /^:?-{3,}:?$/.test(cell))) {
      list = null;
      const alignment = separator.map(cell => cell.startsWith(':') ? (cell.endsWith(':') ? 'center' : 'left') : (cell.endsWith(':') ? 'right' : 'left'));
      const body = el('tbody');
      const table = el('table', {}, el('thead', {}, el('tr', {}, headers.map((cell, column) => el('th', { scope: 'col', class: `table-${alignment[column]}` }, inline(cell))))), body);
      index++;
      while (index + 1 < lines.length) {
        const cells = tableCells(lines[index + 1]);
        if (!cells || cells.length !== headers.length) break;
        body.append(el('tr', {}, cells.map((cell, column) => el('td', { class: `table-${alignment[column]}` }, inline(cell)))));
        index++;
      }
      article.append(el('div', { class: 'prose-table', tabindex: '0', role: 'region', 'aria-label': '报告表格' }, table));
      continue;
    }
    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    const listMatch = line.match(/^\s*(?:([-*])|(\d{1,9})[.)])\s+(.+)$/);
    if (headingMatch) { list = null; article.append(el(`h${Math.min(headingMatch[1].length + 1, 6)}`, {}, inline(headingMatch[2]))); }
    else if (listMatch) {
      const tag = listMatch[2] === undefined ? 'ul' : 'ol';
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = el(tag, tag === 'ol' ? { start: Number(listMatch[2]) } : {}); article.append(list);
      }
      list.append(el('li', {}, inline(listMatch[3])));
    }
    else if (/^---+$/.test(line)) { list = null; article.append(el('hr')); }
    else if (line.startsWith('>')) { list = null; article.append(el('blockquote', {}, inline(line.replace(/^>\s*/, '')))); }
    else { list = null; article.append(el('p', {}, inline(line))); }
  }
  return article;
}
