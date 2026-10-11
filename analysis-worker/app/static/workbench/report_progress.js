/** Display saved note counters; they are not external fact verification. */
export function reportNoteProgress(checkpoint) {
  if (!checkpoint || typeof checkpoint !== 'object' || Array.isArray(checkpoint)) return null;
  const generation = checkpoint.report_generation || {};
  if (generation.complete === true || ['complete', 'completed'].includes(generation.phase)) return null;
  const attempts = Array.isArray(generation.attempts) ? generation.attempts : [];
  const latest = attempts.at(-1) || {};
  const candidates = [['source_review', generation.phase === 'review_pending' ? latest.source_review_input_synthesis : null],
    ['synthesis', checkpoint.synthesis_state], ['legacy', { progress: checkpoint.progress }]];
  for (const [scope, state] of candidates) {
    const progress = state?.progress;
    if (progress && typeof progress === 'object' && !Array.isArray(progress) && Object.keys(progress).length) {
      return { scope, complete: state.complete === true, progress: { ...progress } };
    }
  }
  return null;
}

export function reportNoteProgressText(checkpoint, jobStatus = 'running') {
  const detail = reportNoteProgress(checkpoint);
  if (!detail) return '';
  const p = detail.progress, count = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const title = detail.scope === 'source_review' ? '报告来源复核笔记' : '证据综合笔记';
  const status = detail.complete ? '已保存' : ['running', 'queued'].includes(jobStatus) ? '正在处理' : '进度已保存';
  return `${title}${status}：第 ${count(p.level)} 层 · 当前批次 ${count(p.current_batch_number)} / ${count(p.planned_batch_count)} · 本层输入已复核 ${count(p.completed_unit_count)} / ${count(p.total_unit_count)} · 模型请求尝试记录已完成 ${count(p.completed_request_count)} / ${count(p.model_request_count)} 条 · 复用已审批次 ${count(p.reused_reviewed_batch_count)}。`;
}

/** A terminal checkpoint must never fall back to an in-progress label. */
export function reportCheckpointProgressText(checkpoint, jobStatus = 'running') {
  const detail = reportNoteProgressText(checkpoint, jobStatus);
  if (detail) return detail;
  const generation = checkpoint?.report_generation;
  if (generation) {
    if (generation.complete === true || ['complete', 'completed'].includes(generation.phase)) {
      return '报告草稿复核完成；保存结果以任务状态和报告为准。';
    }
    return ['queued', 'running'].includes(jobStatus)
      ? '正在撰写与复核报告，已阅读材料继续复用。'
      : '报告草稿与复核进度已保存；请查看任务状态和诊断。';
  }
  const count = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  return `已阅读 ${count(checkpoint?.coverage_progress?.reviewed_source_count)} / ${count(checkpoint?.coverage_progress?.input_source_count)} 条材料`;
}
