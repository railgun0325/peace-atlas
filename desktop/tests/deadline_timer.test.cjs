const test = require('node:test');
const assert = require('node:assert/strict');

async function fixture() {
  const { armDeadline } = await import('../harness/deadline_timer.mjs');
  let clock = 0, expired = 0;
  const tasks = [], cleared = [];
  const options = { now: () => clock, set: (fn, delay) => { const task = { fn, delay }; tasks.push(task); return task; }, clear: task => cleared.push(task) };
  return { armDeadline, options, tasks, cleared, advance: ms => { clock += ms; }, expire: () => expired++, expired: () => expired };
}
test('zero total deadline schedules no timer and cancellation remains callable', async () => {
  const f = await fixture(); const cancel = f.armDeadline(0, f.expire, f.options);
  assert.equal(f.tasks.length, 0); cancel(); assert.equal(f.expired(), 0);
});
test('30 day deadline uses safe timer pieces and expires only at the real deadline', async () => {
  const f = await fixture(); const duration = 30 * 86400 * 1000;
  f.armDeadline(duration / 1000, f.expire, f.options);
  assert.equal(f.tasks[0].delay, 2147483647);
  f.advance(2147483647); f.tasks[0].fn();
  assert.equal(f.expired(), 0); assert.equal(f.tasks[1].delay, duration - 2147483647);
  f.advance(f.tasks[1].delay); f.tasks[1].fn(); assert.equal(f.expired(), 1);
  f.tasks[1].fn(); assert.equal(f.expired(), 1);
});
test('cancelling a rearmed deadline clears the current timer and ignores stale callbacks', async () => {
  const f = await fixture(); const cancel = f.armDeadline(60, f.expire, f.options);
  f.advance(1000); f.tasks[0].fn(); cancel();
  assert.deepEqual(f.cleared, [f.tasks[1]]);
  f.advance(60000); f.tasks[1].fn(); assert.equal(f.expired(), 0);
});
