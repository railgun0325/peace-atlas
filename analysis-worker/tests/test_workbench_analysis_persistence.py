import asyncio
import sqlite3

from app.workbench import relationship_analysis as relationships
from app.workbench.store import Store


def candidate(identity, label='公开团体A'):
    return {'id': identity, 'subject_key': 'twitter:alpha', 'relationship': 'affiliation',
            'object_label': label, 'basis': 'inferred', 'statement': '仍待独立核对',
            'method': 'configured_ai', 'evidence_urls': ['https://x.com/alpha/']}


def test_new_review_snapshot_replaces_invalidated_hashes_and_unreviewed_candidates(tmp_path):
    store = Store(tmp_path / 'snapshot.sqlite3')
    try:
        job = store.create_job('research', {})
        run = store.create_research_run(job['id'], {})
        store.checkpoint_research(run['id'], nodes=[{'account_key': 'twitter:alpha'}],
            claims=[candidate('old-candidate'), candidate('manually-reviewed', '公开团体B')],
            analysis={'ai': {'review_version': 2, 'review_batches': [{'id': 'old', 'evidence_hashes': {'post-1': 'old'}}]},
                      'evidence': [{'id': 'post-1', 'excerpt': '旧正文'}],
                      'affiliations': [{'id': 'old-candidate', 'account_key': 'twitter:alpha', 'organization': '公开团体A', 'status': 'candidate'},
                                       {'id': 'manually-reviewed', 'account_key': 'twitter:alpha', 'organization': '公开团体B', 'status': 'candidate'}],
                      'findings': [], 'decisions': [{'action': 'old-stage'}]})
        captured = store.get_research_run(run['id'])['claims']
        reviewed = next(row for row in captured if row['object_label'] == '公开团体B')
        store.review_research_claim(run['id'], reviewed['id'], 'confirmed',
            evidence_url='https://example.org/source', note='手动核对记录必须保留')
        latest = {'ai': {'review_version': 2, 'review_batches': [{'id': 'new', 'evidence_hashes': {'post-1': 'new'}}]},
                  'evidence': [{'id': 'post-1', 'excerpt': '更正正文'}],
                  'affiliations': [], 'findings': [], 'decisions': [{'action': 'new-stage'}]}
        store.checkpoint_research(run['id'], analysis=latest, phase='analysis')
        saved = store.get_research_run(run['id'])
        assert saved['analysis'] == latest
        assert {row['id'] for row in saved['claims']} == {reviewed['id']}
        assert saved['claims'][0]['status'] == 'confirmed'
        assert saved['claims'][0]['review_note'] == '手动核对记录必须保留'
        graph = store.research_graph(run['id'])
        assert graph['total_claims'] == len(graph['claims']) == 1
        assert store.export_research(run['id'])['total_claims'] == 1
        with sqlite3.connect(store.path) as db:
            assert db.execute('SELECT COUNT(*) FROM wb_research_claims').fetchone()[0] == 2
            assert db.execute('SELECT COUNT(*) FROM wb_research_claim_observations').fetchone()[0] == 2
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        store.close()


def test_persisted_source_reordering_does_not_repeat_completed_model_review(tmp_path, monkeypatch):
    store = Store(tmp_path / 'resume.sqlite3')
    calls = []
    async def model(system, data, **options):
        calls.append(data['evidence'])
        return {'affiliation_candidates': []}
    monkeypatch.setattr(relationships, 'configured_ai_json', model)
    accounts = [{'account_key': 'twitter:alpha', 'name': 'alpha', 'profile': {
        'verified_in_dom': True, 'url': 'https://x.com/alpha/', 'bio': '公开说明',
        'observed_at': '2026-10-09T00:00:00Z'}}]
    items = [{'account_key': 'twitter:alpha', 'url': f'https://x.com/alpha/status/{index}',
              'content': '公开团体A 的公开信息', 'published_at': '2026-10-08T00:00:00Z',
              'metadata': {'collected_at': '2026-10-09T00:00:00Z'}} for index in range(1, 90)]
    try:
        previous = asyncio.run(relationships.analyze_relationships_with_ai(accounts, items, [], use_ai=True))
        job = store.create_job('research', {})
        run = store.create_research_run(job['id'], {})
        store.checkpoint_research(run['id'], analysis=previous)
        restored = store.get_research_run(run['id'])['analysis']
        calls.clear()
        result = asyncio.run(relationships.analyze_relationships_with_ai(accounts, list(reversed(items)), [],
            use_ai=True, previous_analysis=restored))
        assert not calls and result['ai']['complete'] and result['ai']['resumed_batches'] == 2
    finally:
        store.close()


def test_partial_and_legacy_reports_do_not_claim_complete_analysis():
    from app.workbench.research_export import research_markdown
    for ai in ({'requested': True, 'used': True, 'complete': False, 'input_evidence_count': 80,
                'available_evidence_count': 131, 'pending_evidence_count': 51, 'completed_batches': 1},
               {'requested': True, 'used': True, 'input_evidence_count': 80, 'available_evidence_count': 131}):
        report = research_markdown({'run': {'analysis': {'ai': ai}}})
        assert '证据摘录 80/131 条' in report and '待审 51 条' in report
        assert 'AI 摘录审阅尚未完成' in report and '摘录覆盖不表示模型已阅读全文' in report
