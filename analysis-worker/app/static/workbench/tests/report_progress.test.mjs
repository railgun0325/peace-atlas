import test from 'node:test';
import assert from 'node:assert/strict';
import { reportNoteProgress, reportNoteProgressText, reportCheckpointProgressText } from '../report_progress.js';

const progress = () => ({ stage: 'evidence_notes', level: 1, planned_batch_count: 3, current_batch_number: 2,
  completed_unit_count: 200, total_unit_count: 601, model_request_count: 11, completed_request_count: 9,
  reused_reviewed_batch_count: 0, coverage_is_independent_truth_verification: false });
const finalReview = () => ({ progress: null, synthesis_state: { complete: true },
  report_generation: { phase: 'review_pending', complete: false,
    attempts: [{}, { source_review_input_synthesis: { complete: false, progress: progress() } }] } });

test('real final-review payload displays current batch units and requests without flattening', () => {
  const checkpoint = finalReview(), before = structuredClone(checkpoint);
  checkpoint.progress = { ...progress(), completed_unit_count: 601 };
  checkpoint.synthesis_state.progress = checkpoint.progress;
  const detail = reportNoteProgress(checkpoint);
  assert.equal(detail.scope, 'source_review');
  assert.deepEqual(detail.progress, progress());
  const text = reportNoteProgressText(checkpoint);
  for (const exact of ['报告来源复核笔记正在处理', '2 / 3', '200 / 601', '9 / 11']) assert.ok(text.includes(exact));
  detail.progress.completed_unit_count = 999;
  assert.equal(checkpoint.report_generation.attempts.at(-1).source_review_input_synthesis.progress.completed_unit_count, 200);
  assert.deepEqual(finalReview(), before);
});

test('legacy flat checkpoints and initial nested synthesis remain readable', () => {
  const flat = { progress: progress() }, nested = { synthesis_state: { complete: false, progress: progress() } };
  assert.equal(reportNoteProgress(flat).scope, 'legacy');
  assert.equal(reportNoteProgress(nested).scope, 'synthesis');
  assert.ok(reportNoteProgressText(flat).includes('200 / 601'));
  assert.ok(reportNoteProgressText(nested).includes('证据综合笔记正在处理'));
});

test('no notes means no made-up counters or resurrection of older review attempt', () => {
  const checkpoint = finalReview(); checkpoint.report_generation.attempts.push({});
  assert.equal(reportNoteProgress(checkpoint), null);
  assert.equal(reportNoteProgressText(checkpoint), '');
  assert.equal(reportNoteProgress({ progress: [] }), null);
  assert.ok(reportNoteProgressText(finalReview(), 'failed').includes('进度已保存'));
  assert.ok(!reportNoteProgressText(finalReview(), 'failed').includes('正在处理'));
  const pendingWithoutNotes = { report_generation: { phase: 'review_pending' } };
  assert.ok(reportCheckpointProgressText(pendingWithoutNotes, 'failed').includes('进度已保存'));
  assert.ok(!reportCheckpointProgressText(pendingWithoutNotes, 'failed').includes('正在'));
  assert.ok(reportCheckpointProgressText(pendingWithoutNotes, 'running').includes('正在撰写'));
});

test('completed report suppresses stale in-progress note text', () => {
  for (const terminal of [{ complete: true }, { phase: 'complete' }, { phase: 'completed' }]) {
    const checkpoint = finalReview(); Object.assign(checkpoint.report_generation, terminal);
    assert.equal(reportNoteProgressText(checkpoint), '');
    assert.ok(reportCheckpointProgressText(checkpoint, 'succeeded').includes('草稿复核完成'));
    assert.ok(!reportCheckpointProgressText(checkpoint, 'succeeded').includes('正在'));
  }
  const notesSaved = { synthesis_state: { complete: true, progress: progress() }, report_generation: { phase: 'draft' } };
  assert.ok(reportNoteProgressText(notesSaved).includes('已保存'));
  assert.ok(!reportNoteProgressText(notesSaved).includes('正在处理'));
});
