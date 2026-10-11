import { api } from './api.js';
import { el, button, dialog, field, loading, errorBox, setContent } from './ui.js';

export function bridgePairingValue(data, platform) {
  let url;
  try { url = new URL(data.pairing_url); } catch { throw new Error('本地会话连接地址无法确认，请重试。'); }
  if (!['twitter', 'instagram'].includes(platform) || url.protocol !== 'http:' || url.hostname !== '127.0.0.1'
    || !url.port || Number(url.port) > 65535 || url.username || url.password || url.hash
    || url.pathname !== '/api/v2/social/bridge/connect' || url.searchParams.get('platform') !== platform
    || !/^[0-9a-f]{64}$/.test(url.searchParams.get('code') || '') || [...url.searchParams].length !== 2
    || !Number.isInteger(data.expires_in) || data.expires_in < 1 || data.expires_in > 300) {
    throw new Error('本地会话连接地址无法确认，请重试。');
  }
  return { url: url.href, expiresIn: data.expires_in };
}

export function openBrowserBridge(ctx, platform) {
  const name = platform === 'twitter' ? 'X' : 'Instagram';
  const modal = dialog(`连接已登录的 Chrome / Edge · ${name}`, { subtitle: 'LOCAL BROWSER SESSION / PAIR ONCE' });
  const controller = new AbortController();
  const abort = () => controller.abort();
  ctx.signal.addEventListener('abort', abort, { once: true });
  const content = el('div');
  modal.body.append(el('p', { class: 'intro' }, `直接复用 Chrome 或 Edge 中已登录的 ${name} 会话。首次使用需安装本项目的“PEACE ATLAS 会话连接”扩展，并由你在扩展中确认连接。`), content);
  modal.modal.addEventListener('close', () => {
    controller.abort(); ctx.signal.removeEventListener('abort', abort);
    content.querySelectorAll('textarea,input').forEach(input => { input.value = ''; });
    setContent(content);
  }, { once: true });
  setContent(modal.footer, button('关闭', modal.close, 'secondary small'));

  async function start() {
    setContent(content, loading('正在生成仅供本机使用的连接地址…'));
    try {
      const data = await api('social/bridge/start', { method: 'POST', body: { platform }, signal: controller.signal });
      if (controller.signal.aborted || !modal.modal.isConnected) return;
      const pairing = bridgePairingValue(data, platform);
      const address = field('本地配对地址', { name: 'bridge_pairing_url', type: 'textarea', rows: 3, value: pairing.url, readonly: true, hint: `地址在 ${Math.ceil(pairing.expiresIn / 60)} 分钟内有效，仅用于本机扩展连接。请勿发送给其他人或粘贴到网站。` });
      const copyStatus = el('div', { role: 'status' });
      const copy = button('复制配对地址', async () => {
        setContent(copyStatus);
        try {
          if (!navigator.clipboard?.writeText) throw new Error('clipboard_unavailable');
          await navigator.clipboard.writeText(address.querySelector('textarea').value);
          if (modal.modal.isConnected) setContent(copyStatus, el('p', { class: 'field-hint' }, '已复制，请粘贴到本机的会话连接扩展。'));
        } catch {
          if (modal.modal.isConnected) setContent(copyStatus, el('p', { class: 'warning-note' }, '当前环境不允许自动复制。请选中上面的地址并手动复制。'));
        }
      }, 'secondary small');
      setContent(content, el('ol', {}, el('li', {}, `在已登录 ${name} 的 Chrome 或 Edge 中，打开“PEACE ATLAS 会话连接”扩展。`), el('li', {}, '粘贴下面的本地配对地址，在扩展中点击“连接”。'), el('li', {}, '连接完成后关闭此窗口，点击工作台“检测并复用已有登录”，验证成功即可采集。')), address, el('div', { class: 'actions' }, copy), copyStatus, el('p', { class: 'field-hint' }, '此窗口仅生成连接地址，不会打开平台登录页，也不代表会话已同步。关闭窗口后不再查询连接状态。'));
    } catch (error) { if (!controller.signal.aborted && modal.modal.isConnected) setContent(content, errorBox(error)); }
  }
  start();
  return modal;
}
