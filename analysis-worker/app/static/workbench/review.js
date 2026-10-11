import { api } from './api.js';
import { el, button, field, dialog, setContent, loading, errorBox, notify, number, sourceLink } from './ui.js';

const kinds = { accounts: '账号类型', claims: '关系主张', activities: '活动与举办地' };
const outcomes = { organization: '组织', individual: '个人', unknown: '类型证据不足', confirmed: '已确认', rejected: '已排除', inconclusive: '证据不足' };
const phases = { starting: '准备范围', investigating: 'DS 正在阅读与查找依据', classifying: 'DS 正在快速判定类型', saving: '保存本批决定', verifying_accounts: '程序核对准确账号回链', complete: '审核流程已完成' };
const sourceCacheModes = [['reuse', '复用有效期内已保存来源（默认）'], ['refresh', '强制刷新'], ['off', '本次关闭']];
const sourceCacheTTL = 3600;

function sourceCacheResults(cache) {
  if (!cache || typeof cache !== 'object') return null;
  const count = key => Number.isSafeInteger(cache[key]) && cache[key] >= 0 ? `${number(cache[key])} 次` : '未记录';
  const ttl = cache.ttl_seconds;
  const duration = Number.isSafeInteger(ttl) && ttl > 0 ? ttl % 3600 === 0 ? `${number(ttl / 3600)} 小时` : ttl % 60 === 0 ? `${number(ttl / 60)} 分钟` : `${number(ttl)} 秒` : '未记录';
  const time = value => typeof value === 'string' && value.trim() && Number.isFinite(new Date(value).getTime())
    ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(value)) : '时间未记录';
  const mode = { reuse: '复用有效期内已保存来源', refresh: '强制刷新', off: '本次关闭' }[cache.mode];
  const captures = Array.isArray(cache.captures) ? cache.captures.slice(0, 32).filter(row => row && typeof row === 'object') : [];
  const truncated = Number.isSafeInteger(cache.capture_receipts_truncated) && cache.capture_receipts_truncated > 0 ? cache.capture_receipts_truncated : 0;
  return el('section', { class: 'review-source-cache', 'aria-label': '来源读取与复用' },
    el('h4', {}, '来源读取与复用'),
    el('p', {}, `${mode ? `${mode} · ` : ''}有效期 ${duration}`),
    el('p', {}, `跨任务复用 ${count('persistent_hits')} · 本任务内复用 ${count('run_hits')} · 成功新读取 ${count('successful_fetches')} · 新读取请求 ${count('fetch_calls')}`),
    el('p', { class: 'field-hint' }, '复用的是原捕获时间保存的内容，不等于现场新读取。请求次数包含失败；成功新读取只计完成提取的新捕获。目标账号仍需按自身公开回链核验，不能沿用其他账号的身份结论。'),
    cache.mode === 'refresh' && el('p', { class: 'field-hint' }, '本次跳过跨任务缓存读取，新捕获可保存供后续复用；本任务内仍可复用已读取内容。'),
    cache.mode === 'off' && el('p', { class: 'field-hint' }, '不读取或保存跨任务缓存；本任务内仍可复用已读取内容。'),
    (captures.length > 0 || truncated > 0) && el('details', {}, el('summary', {}, `查看 ${number(captures.length)} 条来源捕获记录`),
      el('p', { class: 'field-hint' }, '以下为当次读取记录的捕获及有效期时间（日本时间），不表示来源现在仍有效。'),
      el('ol', { class: 'relation-list' }, captures.map(row => el('li', {}, sourceLink(row.source_url, '查看来源'),
        el('p', {}, { persistent: '复用保存内容', network: '本次新读取' }[row.first_read_via] || '读取方式未记录'),
        el('p', {}, `捕获 ${time(row.captured_at)} · 有效期至 ${time(row.expires_at)}`)))),
      truncated > 0 && el('p', { class: 'field-hint' }, `另有 ${number(truncated)} 条捕获记录未展示；以上读取统计仍包含全部记录。`)));
}

export function reviewProgress(result = {}) {
  result = result || {};
  const phase = result.classification_version === 1 && result.phase === 'complete' ? '类型粗分已完成' : phases[result.phase] || '准备自动审核';
  return `已保存 ${number(result.completed)} / ${number(result.total)} 项 · ${result.current_batch && result.phase !== 'complete' ? `第 ${number(result.current_batch)} 批 / 本批 ${number(result.current_batch_size)} 项 · ` : ''}${phase} · 模型 ${number(result.model_calls)} 次 / 工具 ${number(result.tool_calls)} 次`;
}

export function reviewResults(result = {}) {
  result = result || {};
  const fast = result.classification_version === 1;
  return el('section', { class: 'review-results' }, el('h3', {}, fast ? '账号类型粗分结果' : '自动审核与核验结果'), el('p', { role: 'status' }, reviewProgress(result)),
    el('p', { class: 'field-hint' }, fast ? '仅按已保存名称、用户名和简介近似判定，可在账号列表或图谱详情手动更改。人工锁定类型不会被覆盖。' : '已完成表示全部指定内容已有审核结果。证据不足不等于属实；身份核验、账号类型、关系主张分别记录。原始资料保留，人工锁定类型不会被覆盖。'),
    el('p', {}, Object.entries(result.outcomes || {}).map(([key, value]) => `${outcomes[key] || key} ${number(value)}`).join(' · ')),
    sourceCacheResults(result.source_cache),
    el('ol', { class: 'relation-list' }, (result.results || []).map(row => el('li', {},
      el('details', {}, el('summary', {}, `${kinds[row.kind] || row.kind} · ${outcomes[row.outcome] || row.outcome} · ${row.label || row.id.slice(0, 12)}`),
        el('p', {}, row.note), row.confidence && el('p', { class: 'field-hint' }, `分类把握：${({ high: '较有把握', medium: '一般把握', low: '低把握' })[row.confidence] || '低把握'}`), row.date && el('p', {}, `实际日期 ${row.date} · 举办地 ${row.prefecture || '无法定位'}`),
        (row.resolved_evidence || []).map(proof => el('div', {}, sourceLink(proof.url, '查看已核对出处'), el('blockquote', {}, proof.quote))))))));
}

export function openFastClassification(ctx) {
  const modal = dialog('快速判定账号类型', { subtitle: 'DEEPSEEK / ACCOUNT TYPES' });
  const batch = field('每批账号数', { name: 'classification_batch_size', type: 'number', value: 100, min: 1, max: 200,
    hint: '通常每批一次 DS 调用，全部待分类账号会依次处理。' });
  const recheck = el('input', { type: 'checkbox' }), preview = el('div'), errors = el('div');
  const controller = new AbortController();
  modal.modal.addEventListener('close', () => controller.abort(), { once: true });
  let proof = null, values = null, version = 0, busy = false;
  const start = button('开始类型粗分', async () => {
    if (!proof || busy) return;
    busy = true; start.disabled = true;
    try {
      const job = await api('accounts/classify/start', { method: 'POST', body: { ...values, input_digest: proof.input_digest }, signal: controller.signal });
      modal.close(); notify('类型粗分已开始，可在任务中心查看进度或继续中断任务。'); ctx.navigate('jobs', { selected: job.id });
    } catch (error) { if (!controller.signal.aborted) setContent(errors, errorBox(error)); busy = false; start.disabled = !proof; }
  });
  start.disabled = true;
  const invalidate = () => { version++; proof = null; start.disabled = true; setContent(preview); };
  batch.querySelector('input').addEventListener('input', invalidate); recheck.addEventListener('change', invalidate);
  modal.body.append(el('p', {}, '类型粗分（可手动更改）。仅根据已保存的名称、用户名和公开简介判定组织、个人或未判定。'),
    el('p', { class: 'field-hint' }, '人工锁定类型会跳过。身份核验可另选“自动核验候选账号”。'), batch,
    el('label', { class: 'checkbox' }, recheck, '重新判定已有类型（仍跳过人工锁定）'), preview, errors);
  modal.footer.append(button('取消', modal.close, 'secondary'), button('预估范围与用时', async event => {
    if (busy) return;
    const control = event.currentTarget, mine = ++version;
    proof = null; start.disabled = true; control.disabled = true; setContent(errors); setContent(preview, loading('正在统计账号…'));
    try {
      const input = batch.querySelector('input'); if (!input.reportValidity()) throw new Error('每批账号数需要 1 至 200。');
      values = { batch_size: Number(input.value), recheck: recheck.checked };
      const estimate = await api('accounts/classify/estimate', { method: 'POST', body: values, signal: controller.signal });
      if (mine !== version || controller.signal.aborted) return;
      proof = estimate;
      setContent(preview, el('p', { class: 'result-box' }, `${number(proof.total)} 个账号 / ${number(proof.batches)} 批 · 通常 ${number(proof.model_calls_estimate)} 次模型调用`),
        el('p', {}, `粗略预计 ${Math.max(1, Math.ceil(proof.lower_seconds / 60))}–${Math.max(1, Math.ceil(proof.upper_seconds / 60))} 分钟。`),
        el('p', { class: 'field-hint' }, `${proof.model} · 使用已配置模型，按实际请求产生费用。`),
        !proof.ai_configured && el('p', { class: 'warning-note' }, '请在能力与设置保存 AI 连接。'),
        !proof.total && el('p', { class: 'field-hint' }, '当前没有待分类账号，可选择重新判定已有类型。'));
      start.disabled = !proof.total || !proof.ai_configured;
    } catch (error) { if (!controller.signal.aborted && mine === version) { setContent(preview); setContent(errors, errorBox(error)); } }
    finally { control.disabled = false; }
  }, 'secondary'), start);
}

export function openAutoReview(ctx) {
  const modal = dialog('自动审核与核验', { subtitle: 'DEEPSEEK / EVIDENCE REVIEW / SAVED PROGRESS' });
  const checks = Object.entries(kinds).map(([key, label]) => {
    const input = el('input', { type: 'checkbox', checked: key !== 'accounts', value: key });
    return { key, input, node: el('label', { class: 'checkbox' }, input, key === 'accounts' ? '账号公开证据审核与核验（较慢）' : label) };
  });
  const batch = field('每批记录数', { name: 'review_batch_size', type: 'number', value: 16, min: 1, max: 40,
    hint: '决定一次 DS 调查处理多少记录，默认 16。不是任务总量上限；全部所选记录会依次处理。' });
  const sourceCache = field('来源读取方式', { name: 'source_cache_mode', value: 'reuse', options: sourceCacheModes,
    hint: '默认复用 1 小时内成功保存的来源；超过有效期重新读取。复用不等于现场新读取，目标账号仍须独立核验。强制刷新跳过跨任务复用；本次关闭不读取或保存跨任务缓存。各方式均可复用本任务内已读取内容。' });
  const recheck = el('input', { type: 'checkbox' });
  const network = el('input', { type: 'checkbox', checked: true });
  const preview = el('div'), errors = el('div');
  let proof = null, values = null, version = 0, busy = false;
  const controller = new AbortController();
  modal.modal.addEventListener('close', () => controller.abort(), { once: true });
  const start = button('同意预估并开始自动审核', async () => {
    if (!proof || busy) return;
    busy = true; start.disabled = true;
    try {
      const job = await api('reviews/start', { method: 'POST', body: { ...values, input_digest: proof.input_digest }, signal: controller.signal });
      modal.close(); notify('自动审核已开始。可在任务中心查看逐批进度、核验结果，或取消后继续。'); ctx.navigate('jobs', { selected: job.id });
    } catch (error) { if (!controller.signal.aborted) setContent(errors, errorBox(error)); busy = false; start.disabled = !proof; }
  });
  start.disabled = true;
  const invalidate = () => { version++; proof = null; start.disabled = true; setContent(preview); };
  [...checks.map(row => row.input), recheck, network, batch.querySelector('input'), sourceCache.querySelector('select')].forEach(input => input.addEventListener('change', invalidate));
  modal.body.append(el('p', {}, '让已配置的 DeepSeek 通过 DSH 工具调查保存材料、查找独立公开出处、核对原文摘录，再批量保存结论。每批保存审核结果与进度；原始内容保留。'),
    ...checks.map(row => row.node), batch, sourceCache, el('label', { class: 'checkbox' }, recheck, '重新审核已有结论（人工锁定类型仍保留）'),
    el('label', { class: 'checkbox' }, network, '允许读取与搜索独立公开网页，并执行准确回链核验'), preview, errors);
  modal.footer.append(button('取消', modal.close, 'secondary'), button('预估范围与用时', async event => {
    if (busy) return;
    const control = event.currentTarget, mine = ++version;
    proof = null; start.disabled = true; control.disabled = true; setContent(errors); setContent(preview, loading('正在统计待审内容…'));
    try {
      const input = batch.querySelector('input'); if (!input.reportValidity()) throw new Error('每批记录数需要 1 至 40。');
      values = { kinds: checks.filter(row => row.input.checked).map(row => row.key), batch_size: Number(input.value), recheck: recheck.checked, use_public_network: network.checked,
        source_cache_mode: sourceCache.querySelector('select').value, source_cache_ttl_seconds: sourceCacheTTL };
      if (!values.kinds.length) throw new Error('请至少选择一个审核范围。');
      const estimate = await api('reviews/estimate', { method: 'POST', body: values, signal: controller.signal });
      if (mine !== version || controller.signal.aborted) return;
      proof = estimate;
      const minutes = seconds => Math.max(1, Math.ceil(seconds / 60));
      setContent(preview, el('p', { class: 'result-box' }, `共 ${number(proof.total)} 项 / ${number(proof.batches)} 批 · ${Object.entries(proof.counts).map(([key, value]) => `${kinds[key]} ${number(value)}`).join(' · ')}`),
        el('p', {}, `粗略预计 ${minutes(proof.lower_seconds)}–${minutes(proof.upper_seconds)} 分钟。网页与模型响应可能使实际时间更长，无总体数量或时长停止。`),
        el('p', { class: 'field-hint' }, `${proof.model} · 将调用已配置模型，按实际请求产生费用。${proof.basis}`),
        !proof.ai_configured && el('p', { class: 'warning-note' }, '请在能力与设置保存 AI 连接。'),
        !proof.harness_available && el('p', { class: 'warning-note' }, '当前环境缺少 DSH 运行时，请使用完整桌面版本。'),
        proof.total === 0 && el('p', { class: 'field-hint' }, '当前所选范围没有待审内容。证据不足结果可勾选重新审核。'));
      start.disabled = !proof.total || !proof.ai_configured || !proof.harness_available;
    } catch (error) { if (!controller.signal.aborted && mine === version) { setContent(preview); setContent(errors, errorBox(error)); } }
    finally { control.disabled = false; }
  }, 'secondary'), start);
}
