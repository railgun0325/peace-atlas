"""Read-only DOM extraction adapted from the existing local collectors.

Selectors are centralized for fixture regression tests. They do not interact
with credentials, modify platform access controls, or issue hidden API calls.
"""

TW_SEARCH_STATE = r"""(argument) => { // social_twitter_search_state
 const normalize = value => String(value || '').normalize('NFKC').trim().replace(/\s+/g, ' ');
 const expected = normalize(typeof argument === 'object' ? argument.query : argument);
 const committed = typeof argument === 'object' && argument.document_committed === true;
 const url = new URL(location.href);
 const primary = document.querySelector('[data-testid="primaryColumn"]');
 const input = primary?.querySelector('[data-testid="SearchBox_Search_Input"],input[role="combobox"]');
 const active = primary?.querySelector('[role="tab"][aria-selected="true"],[role="tab"][aria-current="page"],[role="tab"][data-state="active"]');
 const link = active?.matches('a[href]') ? active : active?.querySelector('a[href]');
 let latest = false;
 if (link) {
  const tab = new URL(link.getAttribute('href'), location.origin);
  latest = tab.pathname.replace(/\/$/, '') === '/search' && tab.searchParams.get('f') === 'live' && normalize(tab.searchParams.get('q')) === expected;
 }
 const route = url.pathname.replace(/\/$/, '') === '/search' && url.searchParams.get('f') === 'live' && normalize(url.searchParams.get('q')) === expected;
 const inputMatches = !!input && normalize(input.value) === expected;
 // Loading media inside a tweet and the separate sidebar never determine
 // whether the primary search result view has hydrated.
 const result = primary?.querySelector('section[role="region"]') || primary;
 const visible = el => !!el.getClientRects().length;
 const first = [...(result?.querySelectorAll('article[data-testid="tweet"]') || [])].find(el =>
  visible(el) && el.querySelector('time[datetime]') && el.querySelector('[data-testid="tweetText"]')?.innerText?.trim());
 const indicators = result ? [result, ...result.querySelectorAll('[role="progressbar"],[aria-busy="true"]')].filter(el =>
  el.matches('[role="progressbar"],[aria-busy="true"]') && !el.closest('article[data-testid="tweet"]') && visible(el)) : [];
 // A paging spinner after a loaded card does not invalidate that card when
 // the new document, query input and Latest tab are all confirmed. A root
 // busy flag, spinner before the first card, or an unproven SPA stays blocked.
 const paging = el => committed && first && !el.contains(first) &&
  !!(el.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_PRECEDING);
 const loading = indicators.some(el => !paging(el));
 const pagination = indicators.some(paging);
 const ready = route && inputMatches && latest && !!result && !loading;
 return {ready, route_matches: route, input_matches: inputMatches, latest_selected: latest,
  loading, pagination_loading: pagination, document_committed: committed,
  result_text: ready ? (result.innerText || '').slice(0, 12000) : '',
  reason: !route ? 'search_route_mismatch' : !inputMatches ? 'search_input_pending' : !latest ? 'latest_tab_pending' : loading ? 'search_results_loading' : !result ? 'search_results_missing' : 'ready'};
}"""

TW_POSTS = r"""() => { // social_twitter_posts
 const primary = document.querySelector('[data-testid="primaryColumn"]') || document.querySelector('main,[role="main"]');
 return [...(primary?.querySelectorAll('article[data-testid="tweet"]') || [])].map(art => {
  // Primary permalinks and profile links are also <a role="link">. They
  // are not quote cards, and must not hide an enclosing quote container.
  const inQuote = el => {
   for (let node = el; node && node !== art; node = node.parentElement) {
    if (node.matches('[data-testid="quoteTweet"]') || (node.getAttribute('role') === 'link' && node.tagName !== 'A')) return true;
   }
   return false;
  };
  const own = el => el.closest('article[data-testid="tweet"]') === art && !inQuote(el);
  const user = [...art.querySelectorAll('[data-testid="User-Name"]')].find(own);
  const profile = [...(user?.querySelectorAll('a[href]') || [])].map(a => a.getAttribute('href')).find(h => /^\/[\w]{1,15}\/?$/.test(h || '')) || '';
  const author = profile.replace(/^\/|\/$/g, '').toLowerCase();
  const time = [...art.querySelectorAll('time')].find(el => {
   const href = el.closest('a[href*="/status/"]')?.getAttribute('href') || '';
   const match = href.match(/^\/([\w]{1,15})\/status\/\d+/);
   return match && match[1].toLowerCase() === author && own(el);
  });
  const link = time?.closest('a[href*="/status/"]');
  const href = link?.getAttribute('href') || '';
  const mainText = [...art.querySelectorAll('[data-testid="tweetText"]')].find(own);
  const originalControl = [...art.querySelectorAll('button,[role="button"],span')].find(el =>
   own(el) && !mainText?.contains(el) && el.getClientRects().length &&
   /^(Show original|显示原文|顯示原文|原文を表示|原文を表示する)$/i.test(el.innerText?.trim() || ''));
  const articleCard = [...art.querySelectorAll('[data-testid="article-cover-image"]')].some(own);
  const statusId = href.match(/\/status\/(\d+)/)?.[1];
  const cited = [...art.querySelectorAll('a[href*="/status/"]')].map(a => a.getAttribute('href') || '').filter(h => h.match(/\/status\/(\d+)/)?.[1] !== statusId);
  const metric = testid => {
   const el = [...art.querySelectorAll('[data-testid="' + testid + '"]')].find(own);
   return el?.innerText?.trim() || el?.getAttribute('aria-label') || null;
  };
  return {status_link: href, author_link: profile, name: (user?.innerText || '').split('\n')[0],
   text: mainText?.innerText || '', translation_visible: !!originalControl,
   original_control: originalControl?.innerText?.trim() || '', body_language: mainText?.getAttribute('lang') || null,
   skip_reason: originalControl ? 'translated_body_not_restored' : articleCard && !mainText?.innerText?.trim() ? 'article_card_without_body' : null,
   published_at: time?.getAttribute('datetime') || null,
   social_context: art.querySelector('[data-testid="socialContext"]')?.innerText || '', cited_links: [...new Set(cited)],
   likes: metric('like') || metric('unlike'), replies: metric('reply'),
   reposts: metric('retweet') || metric('unretweet'),
   image_alt: [...art.querySelectorAll('[data-testid="tweetPhoto"] img')].filter(own).map(img => img.getAttribute('alt') || '').filter(Boolean)};
 }).slice(0, 200);
}"""

TW_ACCOUNTS = r"""() => { // social_twitter_accounts
 return [...document.querySelectorAll('[data-testid="UserCell"]')].map(cell => {
  const link = [...cell.querySelectorAll('a[href]')].find(a => /^\/[\w]{1,15}\/?$/.test(a.getAttribute('href') || ''));
  return {handle: (link?.getAttribute('href') || '').replace(/^\/|\/$/g,''),
   name: (link?.innerText || '').split('\n')[0]};
 }).filter(row => row.handle).slice(0, 100);
}"""

TW_PROFILE = r"""(expectedHandle) => { // social_twitter_profile
 const main = document.querySelector('main,[role="main"]');
 const handle = location.pathname.match(/^\/([\w]{1,15})\/?$/)?.[1]?.toLowerCase() || '';
 const bio = main?.querySelector('[data-testid="UserDescription"]');
 const header = main?.querySelector('[data-testid="UserName"],[data-testid="UserProfileHeader_Items"]');
 const website = main?.querySelector('[data-testid="UserUrl"]');
 const name = main?.querySelector('[data-testid="UserName"]')?.innerText?.trim()?.split('\n')[0] || '';
 const count = direction => [...(main?.querySelectorAll('a[href]') || [])].find(a =>
   new URL(a.href, location.origin).pathname.replace(/\/$/, '') === '/' + handle + '/' + direction)?.innerText || null;
 return {handle, name, bio: bio?.innerText || '',
  links: website ? [{url: website.getAttribute('href') || '', label: website.innerText || ''}] : [],
  following_count: count('following'), followers_count: count('followers'),
  verified_in_dom: handle === expectedHandle && !!(bio || header),
  private: /these posts are protected|これらのポストは非公開|这些帖子受到保护/i.test(main?.innerText || ''),
  url: location.href};
}"""

IG_PROFILE = r"""(expectedHandle) => { // social_instagram_profile
 const main = document.querySelector('main,[role="main"]');
 const handle = location.pathname.match(/^\/([\w.]{1,30})\/?$/)?.[1]?.toLowerCase() || '';
 const heading = [...(main?.querySelectorAll('h1,h2') || [])].find(el => el.innerText?.trim().toLowerCase() === expectedHandle);
 const header = heading?.closest('header') || main?.querySelector('header');
 const count = direction => [...(header?.querySelectorAll('a[href]') || [])].find(a =>
   new URL(a.href, location.origin).pathname.replace(/\/$/, '') === '/' + handle + '/' + direction)?.innerText || null;
 const bioNode = header?.querySelector('[data-testid="user-bio"],[data-testid="profile-bio"]');
 // A bounded semantic header can contain profile descriptions and counters,
 // but excludes post captions, suggested accounts and navigation. Preserve
 // this raw profile text instead of inventing an unlabelled organization.
 const bio = bioNode?.innerText || (header?.innerText || '').slice(0, 1800);
 const links = [...(header?.querySelectorAll('a[href]') || [])].filter(a => {
  try { return !['instagram.com','www.instagram.com'].includes(new URL(a.href, location.origin).hostname); }
  catch (_) { return false; }
 }).map(a => ({url: a.href, label: a.innerText || ''})).slice(0, 10);
 return {handle, name: heading?.innerText || expectedHandle, bio, links,
  following_count: count('following'), followers_count: count('followers'),
  verified_in_dom: handle === expectedHandle && !!header && !!heading && header.contains(heading),
  private: /this account is private|非公開アカウント|这是私密账户/i.test(main?.innerText || ''),
  url: location.href};
}"""

IG_GRID = r"""() => { // social_instagram_grid
 return [...document.querySelectorAll('main a[href*="/p/"], main a[href*="/reel/"], [role="main"] a[href*="/p/"], [role="main"] a[href*="/reel/"], article a[href*="/p/"], article a[href*="/reel/"]')].map(a => ({
   href: a.getAttribute('href') || '', image_alt: a.querySelector('img')?.getAttribute('alt') || ''
 })).slice(0, 200);
}"""

IG_DETAIL = r"""(postUrl) => { // social_instagram_detail
 const root = document.querySelector('article') || document.querySelector('main,[role="main"]');
 if (!root) return {};
 const parts = value => {
  try {
   const url = new URL(value, 'https://www.instagram.com');
   if (!['www.instagram.com','instagram.com'].includes(url.hostname) || url.searchParams.has('comment_id')) return null;
   const match = url.pathname.match(/^\/(?:([\w.]{1,30})\/)?(p|reel)\/([\w-]+)\/?$/);
   return match ? {owner: (match[1] || '').toLowerCase(), code: match[3]} : null;
  } catch (_) { return null; }
 };
 const wanted = parts(postUrl || location.href);
 const times = [...root.querySelectorAll('time[datetime]')];
 const time = times.find(el => {
  const link = parts(el.closest('a[href]')?.getAttribute('href') || '');
  return wanted && link?.code === wanted.code;
 });
 // The first bare time can be a caption edit or a comment. Only the same
 // shortcode's actual post permalink supplies publication time.
 const linkedOwner = parts(time?.closest('a[href]')?.getAttribute('href') || '')?.owner || wanted?.owner || '';
 const profiles = [...root.querySelectorAll('a[href]')].filter(a => /^\/[\w.]{1,30}\/?$/.test(a.getAttribute('href') || '') && !a.closest('nav'));
 const handleOf = a => (a?.getAttribute('href') || '').replace(/^\/|\/$/g, '').toLowerCase();
 const profileLink = (linkedOwner ? profiles.find(a => handleOf(a) === linkedOwner) : null)
  || profiles.find(a => a.closest('header'))
  || profiles.find(a => !a.closest('ul li,[data-testid="comment"]'));
 const owner = linkedOwner || handleOf(profileLink);
 const primaryCaption = [...root.querySelectorAll('h1,[data-testid="post-caption"]')].find(el => !el.closest('ul li,[data-testid="comment"]'));
 let caption = primaryCaption?.innerText?.trim() || '';
 // A generic span may contain another user's comment, so only consider a
 // caption list item whose named author matches the post header.
 if (!caption && owner) {
  const firstRow = root.querySelector('ul li');
  const author = firstRow?.querySelector('h2 a[href]');
  const rowTime = firstRow?.querySelector('time[datetime]');
  const ownsRow = author?.getAttribute('href')?.replace(/^\/|\/$/g,'') === owner;
  const sameTime = !rowTime || (time && rowTime.getAttribute('datetime') === time.getAttribute('datetime'));
  if (ownsRow && sameTime) {
   const text = [...firstRow.querySelectorAll('h1,[data-testid="post-caption"],span[dir="auto"]')].find(el =>
    el.closest('li') === firstRow && !el.closest('h2,a,button,time') && el.innerText?.trim());
   caption = text?.innerText?.trim() || '';
  }
 }
 // New Instagram layout: a caption container has an author/meta child and a
 // separate body span, without article/h1/h2/ul. Never select a row with a
 // comment permalink, even if its author is the original poster.
 if (!caption && owner) {
  for (const authorLink of profiles.filter(a => handleOf(a) === owner)) {
   let container = authorLink.parentElement;
   for (let depth = 0; container && container !== root && depth < 7; depth++, container = container.parentElement) {
    if (container.querySelector('a[href*="/c/"],a[href*="comment_id="]') || container.closest('[data-testid="comment"]')) continue;
    const children = [...container.children];
    const authorBranch = children.find(child => child.contains(authorLink));
    if (!authorBranch) continue;
    const body = children.find(child => child !== authorBranch && child.tagName === 'SPAN' && !child.querySelector('time') && !child.closest('h2') &&
     child.innerText?.trim() && !/^(已编辑|edited|編集済み|編集済|•|·)$/i.test(child.innerText.trim()));
    if (body) { caption = body.innerText.trim(); break; }
   }
   if (caption) break;
  }
 }
 const likesEl = root.querySelector('a[href*="/liked_by/"]');
 // A modern main element also contains unrelated recommendations. Only a
 // same-shortcode media link or a single-post semantic article establishes
 // image ownership; an unlinked image in a generic main is not evidence.
 const media = new Set();
 if (wanted) {
  for (const link of root.querySelectorAll('a[href]')) {
   if (parts(link.getAttribute('href'))?.code === wanted.code) {
    for (const img of link.querySelectorAll('img[alt]')) media.add(img);
   }
  }
  const article = time?.closest('article');
  const singlePost = article && [...article.querySelectorAll('a[href]')].every(link => {
   const post = parts(link.getAttribute('href'));
   return !post || post.code === wanted.code;
  });
  if (singlePost) {
   for (const img of article.querySelectorAll('img[alt]')) {
    if (img.closest('article') === article && !img.closest('a,header,nav,[data-testid="comment"]')) media.add(img);
   }
  }
 }
 const imageAlt = [...new Set([...media].map(img => img.getAttribute('alt') || '')
  .filter(alt => alt && !/profile picture|プロフィール写真/i.test(alt)))].slice(0, 10);
 return {caption, handle: owner, name: profileLink?.innerText?.trim() || owner,
   published_at: time?.getAttribute('datetime') || null,
   likes: likesEl?.innerText?.trim() || null,
   image_alt: imageAlt,
   detail_present: !!time || !!caption};
}"""

IG_SEARCH = r"""() => { // social_instagram_search
 try {
  const data = JSON.parse(document.body?.innerText || '');
  if (!Array.isArray(data.users)) return {valid: false, users: []};
  return {valid: true, users: data.users.map(row => ({handle: row.user?.username || '', name: row.user?.full_name || ''})).filter(row => row.handle).slice(0, 30)};
 } catch (_) { return {valid: false, users: []}; }
}"""

IG_FOLLOWING = r"""() => { // social_instagram_following
 const dialog = document.querySelector('[role="dialog"]');
 if (!dialog) return {valid: false, users: []};
 return {valid: true, users: [...dialog.querySelectorAll('a[href]')].filter(a => /^\/[\w.]{1,30}\/?$/.test(a.getAttribute('href') || '')).map(a => ({
  handle: a.getAttribute('href').replace(/^\/|\/$/g,''), name: a.innerText?.trim() || ''
 })).slice(0, 100)};
}"""

SCROLL = """() => { // social_scroll
 const dialog = document.querySelector('[role="dialog"]');
 const scroller = dialog && [...dialog.querySelectorAll('div')].find(el => el.scrollHeight > el.clientHeight + 30 && /auto|scroll/.test(getComputedStyle(el).overflowY));
 if (scroller) scroller.scrollBy(0, 650); else window.scrollBy(0, 750);
}"""
