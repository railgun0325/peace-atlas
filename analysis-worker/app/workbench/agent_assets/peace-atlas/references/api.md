# Local Agent contract

The supplied CLI is dependency-free Python 3.10+. Pass global `--connection <private-file>` before the subcommand. The file is explicitly exported by the desktop user, protected for its owner, and stores an independent Agent credential. No desktop session, AI key, login cookie or profile is required by the CLI. Public `manifest` and `schema` can also use `--base http://127.0.0.1:<port>` when the user knows the endpoint.

```text
python <skill-folder>/scripts/peace_atlas.py --connection <file> schema
python <skill-folder>/scripts/peace_atlas.py --connection <file> read items.list --json "{\"query\":\"反戦\"}"
python <skill-folder>/scripts/peace_atlas.py --connection <file> preview research.start --input research.json --output preview.json
python <skill-folder>/scripts/peace_atlas.py --connection <file> execute --preview-file preview.json --key research-20261010-001 --confirm
```

Use an input JSON file when shell quoting differs. The CLI creates output files exclusively and does not overwrite existing artifacts. Keep preview files outside the installed Skill folder.

| Operation | Arguments | Permission/effect |
|---|---|---|
| `overview.get`, `accounts.list` | `{}` | read |
| `items.list` | query/type/topic/source/dates/page/page_size/trash | read saved sources |
| `items.get` | `{id}` | read saved body and source metadata |
| `jobs.list`, `reports.list`, `research.list` | page/page_size | read |
| `job.get`, `report.get`, `research.graph`, `research.export` | `{id}` | read |
| `query.plan` | current query schema; `use_ai:false` | rules-only preview, no paid model call |
| `research.start`, `report.start`, `collection.start` | `{params:<current schema>}` | run; preview → confirmation → queued job |
| `research.continue` | `{id:<run>,limits:{...optional stops}}` | run; preview binds saved current job + limits |
| `job.cancel` | `{id:<job>}` | run; preview → request cancellation |
| `job.retry` | `{id:<job>,options:{...supported report overrides}}` | run; preview → explicit retry |
| `items.cleanup` | `{ids:[...],action:"trash"/"restore",reason}` | cleanup; digest-bound recoverable action |
| `review.queue` | `{kind:"accounts"/"claims"/"activities",pending_only:true,page:1,page_size:100}` | read; IDs, evidence and expected_sha256 |
| `review.batch` | `{accounts:[...],claims:[...],activities:[...]}` | review; atomic, snapshot-bound decisions; up to 500 per commit |
| `review.start` | `{kinds:["accounts","claims","activities"],batch_size:16,recheck:false,use_public_network:true}` | review; estimate → confirmation → DeepSeek/DSH investigation and public backlink checks |

Read `/agents/v1/schema` for complete, current JSON schemas. Identifiers cannot contain path separators. Unknown operations/extra arguments are rejected. The operation catalog never accepts arbitrary URLs/API paths. Login jobs cannot be mutated by this channel.

For a write, `/agents/v1/preview` stores the normalized operation/arguments, grant ID, business estimate/cleanup proof and a five-minute expiry. `/agents/v1/execute` requires the exact same normalized arguments, that preview ID, `confirmed:true` and an 8–80 character stable operation key. The server serializes actions and remembers the original result for this grant/backend lifetime. A consumed preview cannot run under a new key. Matching retries return the original response (also on failure); changing arguments under a key conflicts. Revoke/restart invalidates the grant, so idempotency receipts are not a cross-restart recovery mechanism. Query the saved business job ID to resume work after restart.

Initial research calls the same research estimate/confirmation path as the UI. Continuation also uses the current saved run/current-job binding and preserves omitted limits. Zero for max_records/max_accounts/max_edges/time_budget_seconds means no extra stop on that axis; finite date/depth/per-account bounds still apply. Report/collection previews show parameters and whether remote reads/models may occur; they do not claim a measured runtime. Actual execution uses the configured app model when selected, without exposing its key.

Default access is disabled. Every data/action request requires the independent credential, a loopback client and no Origin/Referer header. Browser CORS, remote calls, system proxy routing and HTTP redirects are unsupported. Scope checks precede both preview and execution. Credential possession trusts local processes running as the same OS user; this is not a sandbox against malicious programs already running as that user. Public schemas are not a credential-discovery endpoint.

Review batches bind each target's current evidence hash. Activity hashes are per activity and original source, so reviewing another paragraph does not invalidate sibling decisions. A missing record, changed source or invalid quote rolls back the whole batch. Automatic review saves its decisions and completed progress in one transaction, allowing an explicit retry to skip committed work. Inconclusive claims retain storage status candidate with reviewed_at and public review_outcome inconclusive; they are not pending unreviewed facts. Reviewed activity candidates likewise have review_outcome inconclusive and remain outside the map denominator. Source corrections reactivate activity review. Unknown account types are recorded decisions without granting verified status. Automatic sources require an exact publisher-owned homepage backlink; new authority is not propagated to other accounts on the same site.
