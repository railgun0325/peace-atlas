const SEEDS = { google_news: 'seed_news', twitter: 'seed_twitter', instagram: 'seed_instagram' };
const kinds = ['research', 'collect'];

export function workflowValues(values = {}, initial = {}) {
  const result = { ...values };
  const legacy = values.workflow;
  result.workflow = legacy === 'batch' ? 'batch' : 'single';
  result.batch_kind = legacy === 'simple' ? 'collect' : legacy === 'research' ? 'research' : kinds.includes(values.batch_kind) ? values.batch_kind : initial.handle ? 'collect' : 'research';
  if (kinds.includes(initial.kind)) result.batch_kind = initial.kind;
  if (['single', 'batch'].includes(initial.workflow)) result.workflow = initial.workflow;
  // Older context links remain readable while new links carry the two axes.
  if (['simple', 'research'].includes(initial.workflow)) { result.workflow = 'single'; result.batch_kind = initial.workflow === 'simple' ? 'collect' : 'research'; }
  for (const key of ['source', 'handle', 'keyword', 'research_goal', 'start_date', 'end_date']) if (typeof initial[key] === 'string') result[key] = initial[key];
  result.source ||= 'google_news';
  const hasSeeds = Object.values(SEEDS).some(key => Object.hasOwn(values, key));
  if (!hasSeeds) for (const [source, key] of Object.entries(SEEDS)) result[key] = source === (SEEDS[result.source] ? result.source : 'google_news');
  if (SEEDS[initial.source]) result[SEEDS[initial.source]] = true;
  return result;
}

export function primaryResearchSource(values, preferred = '') {
  const selected = source => values[SEEDS[source]] === true || values[SEEDS[source]] === 'on';
  if (SEEDS[preferred] && selected(preferred)) return preferred;
  return Object.keys(SEEDS).find(selected) || '';
}
