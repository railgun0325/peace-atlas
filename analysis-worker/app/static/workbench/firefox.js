import { api } from './api.js';
import { el, button, dialog, field, loading, empty, errorBox, notify, submit, setContent } from './ui.js';

export function defaultFirefoxProfile(profiles) {
  return (profiles.find(profile => profile.available === true && profile.default === true)
    || profiles.find(profile => profile.available === true))?.id || '';
}

export function firefoxLoginParams(profileId, profiles) {
  if (!profileId || !profiles.some(profile => profile.id === profileId && profile.available === true)) {
    throw new Error('请选择一个可用的 Firefox 配置。');
  }
  return { platform: 'twitter', method: 'firefox', profile_id: profileId };
}

export function openFirefoxImport(ctx, browserLogin) {
  const modal = dialog('复用 Firefox 登录', { subtitle: 'X SESSION / EXPLICIT LOCAL IMPORT' });
  const controller = new AbortController();
  const abort = () => controller.abort();
  ctx.signal.addEventListener('abort', abort, { once: true });
  modal.modal.addEventListener('close', () => { controller.abort(); ctx.signal.removeEventListener('abort', abort); }, { once: true });
  const content = el('div');
  modal.body.append(el('p', { class: 'intro' }, '选择已登录 X 的 Firefox 配置。只有点击“导入并验证 X 会话”后，才会读取该配置的 X 登录会话；不读取密码，也不导入其他网站的数据。'), content);
  let version = 0;

  async function useBrowserLogin() {
    modal.footer.querySelectorAll('button').forEach(control => control.disabled = true);
    try { await browserLogin(); if (modal.modal.isConnected) modal.close(); }
    catch (error) {
      if (modal.modal.isConnected) {
        content.append(errorBox(error));
        modal.footer.querySelectorAll('button').forEach(control => control.disabled = false);
      }
    }
  }

  async function loadProfiles() {
    const request = ++version;
    setContent(content, loading('正在列出本机 Firefox 配置…'));
    setContent(modal.footer, button('取消', modal.close, 'secondary small'));
    try {
      const data = await api('social/firefox-profiles', { signal: controller.signal });
      if (!modal.modal.isConnected || request !== version) return;
      if (!Array.isArray(data.items)) throw new Error('服务返回的 Firefox 配置列表格式无法读取。');
      const profiles = data.items;
      const selected = defaultFirefoxProfile(profiles);
      if (!selected) {
        setContent(content, empty('未发现可用的 Firefox 配置', '可先在系统默认浏览器登录 X。此处仅支持同步 Firefox 的 X 会话；Chrome / Edge 请使用工作台的本地会话连接扩展入口。', [], true));
        setContent(modal.footer, button('取消', modal.close, 'secondary small'), button('用系统默认浏览器登录', useBrowserLogin, 'small', 'external'));
        return;
      }
      const form = el('form', { class: 'stack firefox-import-form', onSubmit: event => {
        event.preventDefault();
        submit(form, async values => {
          const params = firefoxLoginParams(values.profile_id, profiles);
          const job = await api('jobs', { method: 'POST', body: { kind: 'login', params } });
          notify('Firefox 会话导入任务已创建，验证结果将在任务中心显示。');
          if (modal.modal.isConnected) modal.close();
          if (!ctx.signal.aborted) ctx.navigate('jobs', { selected: job.id });
        });
      } });
      const profileField = field('Firefox 配置', { name: 'profile_id', value: selected, required: true, options: profiles.map((profile, index) => [profile.id, `${profile.name || `配置 ${index + 1}`}${profile.default ? ' · 默认' : ''}${profile.available ? '' : ' · 不可用'}`]), hint: '配置名称用于区分本机浏览器资料，不代表已确认其中登录的是哪个 X 账号。' });
      profileField.querySelectorAll('option').forEach(option => { option.disabled = !profiles.some(profile => profile.id === option.value && profile.available === true); });
      form.append(profileField, el('div', { class: 'collection-session' }, el('strong', {}, '验证成功后，才保存到工作台'), el('p', { class: 'field-hint' }, '应用将访问 X 验证会话。原工作台会话会在验证成功后备份并更新；失败或取消会保留原会话。不会提交帖子、关注账号或修改 Firefox 登录状态。')), el('div', { class: 'form-error' }), el('div', { class: 'form-actions' }, button('取消', modal.close, 'secondary'), el('button', { class: 'button', type: 'submit' }, '导入并验证 X 会话')));
      setContent(content, form);
      setContent(modal.footer);
      profileField.querySelector('select').focus();
    } catch (error) {
      if (!controller.signal.aborted && modal.modal.isConnected && request === version) setContent(content, errorBox(error, loadProfiles));
    }
  }
  loadProfiles();
  return modal;
}
