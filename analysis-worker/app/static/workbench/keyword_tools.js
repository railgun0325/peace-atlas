const GROUP_SEPARATOR = /[,，;；\n\r]+/;

export function keywordGroups(value) {
  const groups = String(value || '').normalize('NFKC').split(GROUP_SEPARATOR).map(part => part.trim().replace(/\s+/g, ' ')).filter(Boolean);
  const identity = value => value.toLocaleLowerCase().replace(/憲法\s*(?:第\s*)?[9九]\s*条/g, '憲法九条').replace(/(?<![0-9一二三四五六七八九十百千])(?:第\s*)?[9九]\s*条/g, '九条');
  return groups.filter((group, index) => groups.findIndex(other => identity(other) === identity(group)) === index);
}

export function keywordQueries(value, mode = 'any', sourceMode = 'keyword') {
  const groups = keywordGroups(value);
  if (!['any', 'all'].includes(mode)) throw new Error('请选择匹配任一组或同时匹配全部组。');
  if (groups.length > 12) throw new Error('最多填写 12 组关键词；请合并或拆分研究任务。');
  if (String(value || '').length > 1200) throw new Error('检索关键词总长度最多 1200 个字符。');
  if (groups.some(group => group.length > 300)) throw new Error('每组关键词最多 300 个字符。');
  if (groups.some(group => /(?:^|\s)[A-Za-z_][A-Za-z0-9_]*:|(?:^|\s)(?:OR|AND|NOT)(?:\s|$)|(?:^|\s)[+-]\S|["()]/.test(group))) throw new Error('请填写普通关键词；日期、账号和组合方式使用对应选项，不填写 from:、OR 等检索运算符。');
  if (sourceMode === 'hashtag') {
    if (groups.length !== 1 || /\s/.test(groups[0]) || !groups[0].replace(/^#/, '')) throw new Error('主题标签只填写一个，不含空格；例如 #反戦。');
    if (!/^[\p{L}\p{N}_]{1,100}$/u.test(groups[0].replace(/^#/, ''))) throw new Error('主题标签只能包含文字、数字和下划线，最多 100 个字符。');
    return [groups[0].replace(/^#/, '')];
  }
  if (mode === 'all') return groups.length ? [[...new Set(groups.flatMap(group => group.split(' ')))].join(' ')] : [];
  return groups;
}

const RELATED = [
  { match: /憲法|九条|9条|article\s*9/i, terms: ['憲法9条', '九条の会', '護憲', '平和憲法', '改憲 反対'] },
  { match: /沖縄|okinawa|辺野古|琉球/i, terms: ['沖縄 平和運動', '辺野古 基地', '基地反対', '沖縄 戦争', '高江'] },
  { match: /反戦|反战|戦争|战争|anti.?war/i, terms: ['反戦', '平和運動', '戦争反対', '軍拡反対', '市民団体'] },
  { match: /平和|和平|peace/i, terms: ['平和運動', '平和団体', '憲法九条', '反戦', '核兵器廃絶'] },
  { match: /核|原爆|nuclear/i, terms: ['核兵器廃絶', '核禁条約', '被爆者', '日本被団協', '原水協'] },
  { match: /労働|工会|union|労組/i, terms: ['労働組合 反戦', '労働運動', '市民団体', '連帯', '平和運動'] },
  { match: /パレスチナ|巴勒斯坦|palestin|gaza|ガザ|加沙/i, terms: ['パレスチナ 連帯', 'ガザ 停戦', '即時停戦', '東京 パレスチナ', '市民団体'] },
];

export function relatedKeywords(value, sourceMode = 'keyword') {
  const existing = new Set(keywordGroups(value).map(term => term.replace(/^#/, '').toLocaleLowerCase()));
  const normalized = String(value || '');
  if (!normalized.trim()) return [];
  const suggestions = [];
  for (const group of RELATED) if (group.match.test(normalized)) for (const term of group.terms) {
    const candidate = sourceMode === 'hashtag' ? term.replace(/\s+/g, '') : term;
    const key = candidate.toLocaleLowerCase();
    if (existing.has(key) || suggestions.some(item => item.term.toLocaleLowerCase() === key)) continue;
    suggestions.push({ term: candidate, reason: '相关主题与常用日文检索词', source: 'built_in' });
  }
  return suggestions.slice(0, 10);
}

export function appendKeyword(value, term) {
  const groups = keywordGroups(value);
  const candidate = String(term || '').trim();
  if (!candidate || /[,，;；\n\r]/.test(candidate)) throw new Error('建议词应是一组完整关键词。');
  if (!groups.includes(candidate)) groups.push(candidate);
  const result = groups.join('\n');
  keywordQueries(result);
  return result;
}

export function normalizeSuggestions(data) {
  const values = Array.isArray(data?.keywords) ? data.keywords : Array.isArray(data?.suggestions) ? data.suggestions : [];
  return values.flatMap(item => {
    const term = String(typeof item === 'string' ? item : item?.term || item?.keyword || '').trim();
    if (!term || term.length > 160 || /[,，;；\n\r]/.test(term)) return [];
    return [{ term, reason: String(item?.reason || '研究计划建议').slice(0, 300), source: String(item?.source || data?.method || 'planner') }];
  }).filter((item, index, all) => all.findIndex(other => other.term === item.term) === index).slice(0, 16);
}
