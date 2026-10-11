// Node clamps timer delays above 2^31-1 to one millisecond. Long research
// deadlines must be re-armed in bounded pieces; zero imposes no total timer.
export function armDeadline(seconds, expire, { now = Date.now, set = setTimeout, clear = clearTimeout } = {}) {
  if (!Number.isFinite(seconds) || seconds < 0) throw new Error('invalid_deadline');
  let cancelled = false, timer;
  const deadline = now() + seconds * 1000;
  function tick() {
    if (cancelled) return;
    const remaining = deadline - now();
    if (remaining <= 0) { cancelled = true; expire(); return; }
    timer = set(tick, Math.min(remaining, 2147483647));
  }
  if (seconds > 0) tick();
  return () => { cancelled = true; if (timer !== undefined) clear(timer); };
}
