import {BridgeError, PLATFORMS, parsePairingURL, publicError, requiredPermissions} from './bridge-core.js';

const form = document.querySelector('#connect-form');
const input = document.querySelector('#pairing-url');
const hint = document.querySelector('#pairing-hint');
const notice = document.querySelector('#notice');
const platforms = document.querySelector('#platforms');
let busy = false;

function setBusy(value) {
  busy = value;
  document.querySelectorAll('button').forEach(button => { button.disabled = value; });
}
function line(tag, text, className) {
  const node = document.createElement(tag); node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function refresh() {
  const result = await chrome.runtime.sendMessage({type: 'status'});
  if (!result?.ok) throw new BridgeError('network_error');
  platforms.replaceChildren();
  const labels = {verified: '已验证', syncing: '正在验证', pending: '等待同步', waiting: '待验证', disconnected: '未连接'};
  for (const item of result.items) {
    const section = line('article', '', 'platform');
    const head = line('div', '', 'platform-head');
    head.append(line('h2', item.label), line('span', labels[item.state] || '待验证', 'badge' + (item.state === 'verified' ? ' verified' : '')));
    section.append(head, line('p', item.message));
    if (item.origin) section.append(line('p', item.origin, 'endpoint'));
    if (item.checkedAt) section.append(line('p', '最近确认：' + new Date(item.checkedAt).toLocaleString('zh-CN')));
    if (item.connected) {
      const buttons = line('div', '', 'buttons');
      for (const [type, text] of [['sync', '立即验证'], ['disconnect', '断开连接']]) {
        const button = line('button', text); button.type = 'button'; button.disabled = busy;
        button.addEventListener('click', () => act({type, platform: item.platform}));
        buttons.append(button);
      }
      section.append(buttons);
    }
    platforms.append(section);
  }
}
async function act(message) {
  if (busy) return;
  setBusy(true); notice.textContent = message.type === 'disconnect' ? '正在断开连接…' : '正在等待本机工作台验证，请稍候…';
  try {
    const result = await chrome.runtime.sendMessage(message);
    notice.textContent = result?.ok ? (result.verified ? '已验证并保存，无需再次登录。' : '已停止该平台的后续同步。') : (result?.message || publicError(new BridgeError('network_error')).message);
  } catch (error) { notice.textContent = publicError(error).message; }
  finally { setBusy(false); await refresh().catch(() => {}); }
}
input.addEventListener('input', () => {
  try { const pair = parsePairingURL(input.value); hint.textContent = '连接 ' + PLATFORMS[pair.platform].label + ' → ' + pair.origin; }
  catch { hint.textContent = '在工作台的浏览器连接区域生成链接，5 分钟内粘贴。'; }
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (busy) return;
  try {
    const pairingURL = input.value.trim();
    const pair = parsePairingURL(pairingURL);
    // Called directly from the user's click, before any worker/network await.
    const granted = await chrome.permissions.request(requiredPermissions(pair.platform));
    if (!granted) throw new BridgeError('permission_denied');
    input.value = '';
    await act({type: 'connect', pairingURL});
  } catch (error) { notice.textContent = publicError(error).message; }
});
chrome.storage.onChanged.addListener((_changes, area) => { if (area === 'local') refresh().catch(() => {}); });
refresh().catch(error => { notice.textContent = publicError(error).message; });
