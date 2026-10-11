"""Portable regressions: temporary data only, no worker, browser, HTTP or model."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.workbench import pipeline
from app.workbench.account_identity import correct_identity, describe_identity
from app.workbench.account_verification import OFFICIAL_SOURCES, source_candidates
from app.workbench.research_export import checkpoint_payload
from app.workbench.store import Store


def public_profile(handle='alpha', *, links=True):
    value = {'platform': 'twitter', 'handle': handle, 'account_key': 'twitter:' + handle,
             'name': 'Public organization fixture', 'url': 'https://x.com/' + handle,
             'profile': {'verified_in_dom': True, 'url': 'https://x.com/' + handle,
                         'bio': 'Public organization profile fixture.',
                         'observed_at': '2026-10-09T00:00:00Z'}}
    if links:
        value['profile']['links'] = [{'url': 'https://t.co/fixture',
                                      'label': 'organization.example.org'}]
    return value


class ProfileProjectionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name) / 'owned.sqlite3', database_url='')
        # A storage/read regression must never invoke transport or a provider.
        self.fetch_guard = patch.object(pipeline, '_fetch_public_feed',
                                       side_effect=AssertionError('Unexpected public GET'))
        self.fetch = self.fetch_guard.start()

    def tearDown(self):
        try:
            self.fetch.assert_not_called()
        finally:
            self.fetch_guard.stop()
            self.store.close()
            self.temporary.cleanup()

    def import_profile(self, value):
        imported = self.store.import_social([], [value], [])
        return self.store.get_account(imported['account_ids'][value['account_key']])

    def run_with_nodes(self, handles, *, accounts=None):
        params = {'source': 'twitter', 'mode': 'account',
                  'seed_accounts': ['https://x.com/' + handle for handle in handles]}
        job = self.store.create_job('research', params)
        run = self.store.create_research_run(job['id'], params)
        event = {'kind': 'account', 'accounts': accounts or [],
                 'nodes': [{'account_key': 'twitter:' + handle, 'depth': 0,
                            'status': 'completed'} for handle in handles]}
        payload = checkpoint_payload(event, existing=self.store.get_research_run(run['id']))
        self.store.checkpoint_research(run['id'], **payload)
        return run['id']

    def historical_curated(self, handle='zengakuren'):
        source = OFFICIAL_SOURCES['twitter:' + handle]
        account = self.store.save_account({'name': 'Historical public organization fixture',
                  'platform': 'twitter', 'handle': handle, 'url': 'https://x.com/' + handle,
                  'status': 'verified', 'evidence_url': source})
        check = {'account_id': account['id'], 'account_key': 'twitter:' + handle,
                 'account_url': account['url'], 'source_url': source,
                 'source_basis': 'official_registry', 'verified': True,
                 'outcome': 'public_backlink', 'evidence_url': account['evidence_url'],
                 'matched_account_url': account['url'], 'checked_at': '2026-10-09T00:00:00Z'}
        # A historical fixture; this is never a production or current provider write.
        with closing(sqlite3.connect(self.store.path)) as database:
            with database:
                database.execute('UPDATE wb_accounts SET metadata=? WHERE id=?',
                                 (json.dumps({'public_verification': check}), account['id']))
        return self.store.get_account(account['id'])

    def test_ordinary_import_preserves_original_url_and_label(self):
        value = public_profile()
        account = self.import_profile(value)
        self.assertEqual(account['metadata']['last_public_profile']['links'],
                         value['profile']['links'])
        self.assertEqual(account['status'], 'candidate')
        self.assertEqual(describe_identity(account)['account_type'], 'unknown')

    def test_normal_checkpoint_keeps_source_fields_until_registry(self):
        value = public_profile()
        run_id = self.run_with_nodes(['alpha'], accounts=[value])
        account = self.store.get_research_run(run_id)['accounts'][0]
        self.assertEqual(account['profile']['links'], value['profile']['links'])
        self.assertEqual(account['metadata']['last_public_profile']['links'],
                         value['profile']['links'])
        self.assertEqual(account['status'], 'candidate')

    def test_both_research_read_routes_restore_curated_full_input(self):
        self.historical_curated('zengakuren')
        self.historical_curated('zengakuren_west')
        run_id = self.run_with_nodes(['zengakuren', 'zengakuren_west'])
        for graph in (self.store.research_graph(run_id), self.store.get_research_run(run_id)):
            self.assertEqual([n.get('account_type') for n in graph['nodes']],
                             ['organization', 'organization'])
            self.assertTrue(all(n['account_type_details']['basis'] == 'curated_registry_backlink'
                                for n in graph['nodes']))
        self.assertTrue(all(a['status'] == 'verified'
                            for a in self.store.get_research_run(run_id)['accounts']))

    def test_manual_lock_survives_untrusted_checkpoint_metadata(self):
        value = public_profile()
        account = self.import_profile(value)
        correct_identity(self.store, account['id'], type='individual', manual_locked=True,
                         note='Human fixture decision')
        value['metadata'] = {'account_identity': {'version': 1, 'type': 'organization',
                             'basis': 'manual_review', 'manual_locked': False}}
        run_id = self.run_with_nodes(['alpha'], accounts=[value])
        for graph in (self.store.research_graph(run_id), self.store.get_research_run(run_id)):
            node = graph['nodes'][0]
            self.assertEqual(node.get('account_type'), 'individual')
            self.assertTrue(node['account_type_details']['manual_locked'])
        self.assertEqual(self.store.get_account(account['id'])['status'], 'candidate')

    def test_organization_type_is_independent_of_candidate_verification(self):
        value = public_profile()
        account = self.import_profile(value)
        correct_identity(self.store, account['id'], type='organization', manual_locked=False,
                         note='Candidate type fixture')
        run_id = self.run_with_nodes(['alpha'])
        node = self.store.research_graph(run_id)['nodes'][0]
        self.assertEqual(node['account_type'], 'organization')
        self.assertEqual(node['account_status'], 'candidate')
        self.assertEqual(self.store.get_account(account['id'])['status'], 'candidate')

    def test_legacy_profile_and_string_links_remain_compatible(self):
        value = public_profile(links=False)
        account = self.import_profile(value)
        self.assertNotIn('links', account['metadata']['last_public_profile'])
        value['profile']['links'] = ['https://source.example.org/']
        account = self.import_profile(value)
        self.assertEqual(account['metadata']['last_public_profile']['links'],
                         [{'url': 'https://source.example.org/'}])
        self.assertEqual(account['status'], 'candidate')

    def test_tco_label_is_neither_expanded_nor_trusted(self):
        value = public_profile()
        value['profile']['links'][0]['label'] = OFFICIAL_SOURCES['twitter:zengakuren']
        account = self.import_profile(value)
        candidates = source_candidates(account)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['url'], 'https://t.co/fixture')
        self.assertEqual(candidates[0]['basis'], 'public_profile_link')
        self.assertFalse(candidates[0]['trusted'])
        self.assertEqual(account['status'], 'candidate')

    def test_curated_projection_rejects_reverse_identity_spoof(self):
        account = self.historical_curated()
        run_id = self.run_with_nodes(['zengakuren'])
        with closing(sqlite3.connect(self.store.path)) as database:
            with database:
                database.execute('UPDATE wb_accounts SET evidence_url=? WHERE id=?',
                                 ('https://spoof.example.org/', account['id']))
        for graph in (self.store.research_graph(run_id), self.store.get_research_run(run_id)):
            self.assertEqual(graph['nodes'][0].get('account_type'), 'unknown')
        self.assertEqual(self.store.get_account(account['id'])['status'], 'verified')


if __name__ == '__main__':
    unittest.main(verbosity=2)
