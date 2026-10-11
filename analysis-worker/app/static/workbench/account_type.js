import { api, pathId } from './api.js';
import { el, button, setContent, errorBox, notify } from './ui.js';
import { accountTypeInfo, accountTypeBasisName } from './network_model.js';

const confidenceNames = { high: '较有把握', medium: '一般把握', low: '低把握' };

export function accountTypeSummary(account) {
  const info = accountTypeInfo(account);
  return el('div', { class: 'account-type-details' }, el('span', { class: 'account-type-label' }, info.label),
    el('p', { class: 'field-hint' }, `${accountTypeBasisName(info)}${info.confidence ? ` · ${confidenceNames[info.confidence] || '低把握'}` : ''}${info.manual_locked ? ' · 人工锁定' : ''}`),
    info.note && el('p', { class: 'field-hint' }, info.note));
}

export function accountTypeControls(ctx, account, refresh) {
  const identity = account.account_id || account.id, info = accountTypeInfo(account);
  if (!identity) return null;
  const errors = el('div'), actions = el('div', { class: 'actions account-type-quick', 'aria-label': '快捷更改账号类型' });
  let saving = false;
  async function save(type) {
    if (saving) return;
    saving = true; actions.querySelectorAll('button').forEach(control => { control.disabled = true; });
    setContent(errors);
    try {
      const value = await api(`accounts/${pathId(identity)}/identity`, { method: 'PATCH', signal: ctx.signal,
        body: { type, manual_locked: true, note: '人工快捷更改账号类型', evidence_urls: [] } });
      notify('类型已更改并锁定。'); await refresh(value);
    } catch (error) { if (!ctx.signal?.aborted) setContent(errors, errorBox(error)); }
    finally { saving = false; actions.querySelectorAll('button').forEach(control => { control.disabled = false; }); }
  }
  for (const [type, label] of [['organization', '设为组织'], ['individual', '设为个人'], ['unknown', '设为未判定']]) {
    const control = button(label, () => save(type), 'secondary small');
    control.setAttribute('aria-pressed', String(info.type === type && info.manual_locked)); actions.append(control);
  }
  if (info.manual_locked) actions.append(button('解除人工锁定', async () => {
    if (saving) return;
    saving = true; actions.querySelectorAll('button').forEach(control => { control.disabled = true; }); setContent(errors);
    try {
      const value = await api(`accounts/${pathId(identity)}/type-lock`, { method: 'PATCH', signal: ctx.signal, body: { manual_locked: false } });
      notify('已解除锁定，可在重新判定时更新类型。'); await refresh(value);
    } catch (error) { if (!ctx.signal?.aborted) setContent(errors, errorBox(error)); }
    finally { saving = false; actions.querySelectorAll('button').forEach(control => { control.disabled = false; }); }
  }, 'text small'));
  return el('div', { class: 'account-type-correction' }, actions,
    el('p', { class: 'field-hint' }, '类型粗分可手动更改；更改后锁定，不影响身份核验。'), errors);
}
