CREATE TABLE IF NOT EXISTS articles (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    language TEXT,
    raw_text TEXT,
    clean_text TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles (published_at);
CREATE INDEX IF NOT EXISTS idx_articles_source_type ON articles (source_type);
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles (content_hash);

CREATE TABLE IF NOT EXISTS article_entities (
    id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    entity_text TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_article_entities_article_id ON article_entities (article_id);
CREATE INDEX IF NOT EXISTS idx_article_entities_normalized_name ON article_entities (normalized_name);

CREATE TABLE IF NOT EXISTS article_topics (
    id BIGSERIAL PRIMARY KEY,
    article_id BIGINT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    topic_name TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_article_topics_article_id ON article_topics (article_id);
CREATE INDEX IF NOT EXISTS idx_article_topics_topic_name ON article_topics (topic_name);

CREATE TABLE IF NOT EXISTS weekly_reports (
    id BIGSERIAL PRIMARY KEY,
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    markdown_path TEXT NOT NULL,
    executive_summary TEXT,
    political_temperature TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_weekly_reports_week ON weekly_reports (week_start, week_end);

CREATE TABLE IF NOT EXISTS source_registry (
    id BIGSERIAL PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    political_leaning_note TEXT,
    reliability_note TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT INTO source_registry
    (source_name, source_url, source_type, political_leaning_note, reliability_note, enabled)
VALUES
    ('NHK NEWS WEB', 'https://www.nhk.or.jp/rss/news/cat0.xml', 'public_broadcaster', '日本公共放送；中立的報道。', '公開RSS、信頼性の高いソース。', TRUE),
    ('朝日新聞', 'https://www.asahi.com/rss/asahi/newsheadlines.rdf', 'national_newspaper', '全国紙。', '公開RSS、他ソースとのクロスチェック推奨。', TRUE),
    ('毎日新聞', 'https://mainichi.jp/rss/etc/mainichi-flash.rss', 'national_newspaper', '全国紙。', '公開RSS。', TRUE),
    ('沖縄タイムス', 'https://www.okinawatimes.co.jp/list/feed.xml', 'local_newspaper', '沖縄県地方紙。基地問題に詳しい。', '公開RSS、沖縄関連の重要なソース。', TRUE),
    ('Google News Japan', 'https://news.google.com/rss/search?q=Japan&hl=ja&gl=JP&ceid=JP:ja', 'news_aggregator', '集約検索ソース。Googleの立場を代表しない。', '国際報道の発見に使用。元リンクでの確認が必要。', TRUE),
    ('GDELT DOC 2.0', 'https://api.gdeltproject.org/api/v2/doc/doc', 'news_aggregator', 'グローバルニュース索引。GDELTの立場を代表しない。', '国際報道の発見に使用。失敗時はメインフローに影響しない。', TRUE)
ON CONFLICT (source_url) DO NOTHING;

CREATE TABLE IF NOT EXISTS discussion_posts (
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    board_name TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    author_hash TEXT,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    language TEXT,
    raw_text TEXT,
    clean_text TEXT,
    interaction_count INTEGER NOT NULL DEFAULT 0,
    comment_count INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_discussion_posts_time ON discussion_posts (published_at, fetched_at);
CREATE INDEX IF NOT EXISTS idx_discussion_posts_platform ON discussion_posts (platform, board_name);

CREATE TABLE IF NOT EXISTS discussion_comments (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES discussion_posts(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    author_hash TEXT,
    comment_text TEXT NOT NULL,
    vote_type TEXT,
    score INTEGER NOT NULL DEFAULT 0,
    floor INTEGER,
    published_at TIMESTAMPTZ,
    comment_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (post_id, comment_hash)
);

CREATE INDEX IF NOT EXISTS idx_discussion_comments_post ON discussion_comments (post_id);

CREATE TABLE IF NOT EXISTS discussion_sentiments (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT REFERENCES discussion_posts(id) ON DELETE CASCADE,
    comment_id BIGINT REFERENCES discussion_comments(id) ON DELETE CASCADE,
    sentiment_label TEXT NOT NULL,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    narrative_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    llm_analyzed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_discussion_sentiments_post ON discussion_sentiments (post_id);

CREATE TABLE IF NOT EXISTS discussion_fetch_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'running',
    diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb,
    fetched_posts INTEGER NOT NULL DEFAULT 0,
    fetched_comments INTEGER NOT NULL DEFAULT 0
);

-- ══════════════════════════════════════════════════════════════
-- Japan anti-war intelligence tables
-- ══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS japan_news (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'japan_media',
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    language TEXT DEFAULT 'ja',
    raw_text TEXT,
    clean_text TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    summary_ja TEXT,
    summary_en TEXT,
    sentiment TEXT,
    category TEXT DEFAULT 'general',
    relevance_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    antiwar_relevance BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_japan_news_published_at ON japan_news (published_at);
CREATE INDEX IF NOT EXISTS idx_japan_news_category ON japan_news (category);
CREATE INDEX IF NOT EXISTS idx_japan_news_antiwar ON japan_news (antiwar_relevance);

CREATE TABLE IF NOT EXISTS japan_social_accounts (
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    account_name TEXT,
    organization TEXT,
    url TEXT NOT NULL DEFAULT '',
    language TEXT DEFAULT 'ja',
    note TEXT,
    status TEXT NOT NULL DEFAULT 'candidate',
    discovery_method TEXT NOT NULL DEFAULT 'manual',
    evidence_url TEXT NOT NULL DEFAULT '',
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Compatibility aliases used by the first frontend/API pass.
    username TEXT,
    display_name TEXT,
    account_url TEXT,
    follower_count INTEGER NOT NULL DEFAULT 0,
    bio TEXT,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    is_candidate BOOLEAN NOT NULL DEFAULT TRUE,
    verification_date TIMESTAMPTZ,
    verification_evidence_url TEXT,
    category TEXT DEFAULT 'other',
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    added_by TEXT NOT NULL DEFAULT 'manual',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (platform, handle)
);

CREATE INDEX IF NOT EXISTS idx_japan_social_platform ON japan_social_accounts (platform);
CREATE INDEX IF NOT EXISTS idx_japan_social_verified ON japan_social_accounts (verified);
CREATE INDEX IF NOT EXISTS idx_japan_social_candidate ON japan_social_accounts (is_candidate);
CREATE INDEX IF NOT EXISTS idx_japan_social_category ON japan_social_accounts (category);
CREATE INDEX IF NOT EXISTS idx_japan_social_status ON japan_social_accounts (status);

CREATE TABLE IF NOT EXISTS japan_social_account_sources (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT NOT NULL REFERENCES japan_social_accounts(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL DEFAULT 'manual',
    source_detail TEXT,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_japan_social_sources_account ON japan_social_account_sources (account_id);

CREATE TABLE IF NOT EXISTS japan_social_posts (
    id BIGSERIAL PRIMARY KEY,
    account_id BIGINT REFERENCES japan_social_accounts(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    content_text TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    language TEXT DEFAULT 'ja',
    fetched_content_hash TEXT NOT NULL,
    engagement_count INTEGER NOT NULL DEFAULT 0,
    repost_count INTEGER NOT NULL DEFAULT 0,
    comment_count INTEGER NOT NULL DEFAULT 0,
    like_count INTEGER NOT NULL DEFAULT 0,
    sentiment TEXT,
    narrative_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_japan_social_posts_published ON japan_social_posts (published_at);
CREATE INDEX IF NOT EXISTS idx_japan_social_posts_account ON japan_social_posts (account_id);
CREATE INDEX IF NOT EXISTS idx_japan_social_posts_platform ON japan_social_posts (platform);

CREATE TABLE IF NOT EXISTS japan_antiwar_events (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    event_date DATE,
    location TEXT,
    organizer TEXT,
    source_url TEXT,
    category TEXT DEFAULT 'protest',
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_japan_antiwar_events_date ON japan_antiwar_events (event_date);
CREATE INDEX IF NOT EXISTS idx_japan_antiwar_events_category ON japan_antiwar_events (category);

CREATE TABLE IF NOT EXISTS japan_research_tasks (
    id BIGSERIAL PRIMARY KEY,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    params JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_japan_research_tasks_status ON japan_research_tasks (status);

CREATE TABLE IF NOT EXISTS japan_reports (
    id BIGSERIAL PRIMARY KEY,
    report_type TEXT NOT NULL DEFAULT 'weekly',
    title TEXT NOT NULL,
    content_path TEXT NOT NULL,
    summary TEXT,
    period_start DATE,
    period_end DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_japan_reports_period ON japan_reports (period_start, period_end);
