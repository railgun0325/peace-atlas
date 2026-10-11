# PEACE ATLAS desktop packages

Verified release: **PEACE ATLAS 2.0.0, automatic review and transport recovery, 2026-10-11**, source-frozen build `20261011-023729`. The private GitHub release tag is `v2.0.0-review-assurance.20261011`. **2,688 backend + 172 frontend + 62 desktop + 17 builder = 2,939 tests** passed. The actual new Windows package passed native isolated launch, 14 core flows, 30 Agent CLI executions and a controlled run of the bundled official DSH SDK/runtime 0.2.0-rc.2. Empty response and HTTP 503 recovery used 4 transport attempts for 2 successful decision rounds; all 10 tools executed once and 4 decisions committed atomically. Formal upgrade preserved all 22 tables by exact row/type/schema comparison. Real model results are recorded separately in the [release record](../docs/RELEASE_20261011.md), alongside archive hashes and [safe build receipts](../docs/release-evidence/20261011.json). **Mac native launch, native Apple signature verification and notarization remain unverified.** Windows and Apple Silicon archives were audited against the same source freeze; asset names retain the historical harness-candidate-20261010 suffix. The version records below retain their historical scope.


The Harness distribution is vendored under `desktop/harness` with an audited runtime dependency closure, license notices and matching SDK/runtime versions. The builder checks every included runtime file and target-specific membership; it does not resolve floating npm `latest` at app launch. Windows uses Electron's own Node runtime via `ELECTRON_RUN_AS_NODE`, while the normal Python worker owns model requests and temporary scoped tool sessions. No user-side Node or separate DSH Desktop installation is required. Freeze each platform file set independently: Windows and Mac application sets are not subsets of one another. The format-2 source freeze records both complete target lists and their union. The actual new runtime, full union, each packaged target and archives must be audited together.

**Historical formally launched Windows candidate: `relations-candidate-20261009-010745`.** It added durable deferred observations, per-branch pending work and explicit continuation, including unfinished original news/query prefixes. Whole source regressions passed: 1,584 backend, 129 frontend and 59 desktop. That bundle passed 14 private-Python product checks, isolated native startup and actual legacy 21-to-22 restore; independent auditing checked all 5,716 Windows archive/portable files against frozen bytes. Its ordinary launch and original 21-table row/schema retention passed. Workbench storage then gained a 22nd table. Historical Mac archive/signing checks passed; native Mac launch and Apple codesign remained pending. Exact results and scope are in [frontier recovery acceptance](../docs/FRONTIER_RECOVERY_ACCEPTANCE_20261009.md). Earlier package counts below apply to their historical versions.

**Historical frozen Windows candidate: `relations-candidate-20261008-205926` (2026-10-09).** The backend reviews saved evidence in checkpointed batches, keeps partial collection distinct from completed AI review, and preserves independent public account verification. The graph requires a long physical confirmation window, sleeps after settling and wakes on direct dragging. This exact source passed 1,090 backend,120 frontend Node and59 desktop Node tests; the actual new bundle passed HTTP/lifecycle,21 saved-public-graph gesture groups,144-node synthetic layout tails,5 account groups,25 product interactions and native key-free configuration/21-table restore checks. The formal native worker automatically verified2 public account backlinks and completed an actual 515.047-second four-root research:8 visited accounts,80 materials,15 observed relationships,23 new records, actual `deepseek-flash` review of103/103 unique excerpts in2 batches,pending0. Collection remains partial due to account budget. [Current analysis and delivery acceptance](../docs/ANALYSIS_COVERAGE_ACCEPTANCE_20261009.md) separates actual collection/model evidence, saved-response replay and synthetic fixtures. Mac native launch/Apple codesign, production PostgreSQL concurrency, large-scale actual collection and24-hour endurance remain pending. Earlier190920 relationship,daily and recovery results below are historical.

**Previous recovery candidate, 2026-10-08: `-recovery-candidate`, Windows x64 and Apple
Silicon Mac only.** The candidate adds a native restore flow with read-only preflight,
confirmation, original-database archives, interrupted queued/running tasks, and
persistent recovery transactions. Parent-pipe monitoring and repeated close during
restore are covered by process/lifecycle regression checks. It retains SQLite online
backup, durable research safeguards and transactional desktop connection settings. Actual package
paths, hashes and live coverage belong to the new build and acceptance records;
earlier acceptance does not validate this candidate. Bundled OFL Mincho display fonts and condensed labels work offline. Research budgets reach 10,000 records, 200 scrolls and 24 hours; task-series creation explicitly previews before enqueueing. The creation workspace separates content from execution;
the task center monitors submitted work. These candidates include adaptive public-evidence research,
checkpointed account graphs, collection drafts and persistent task series.
The earlier `-product-candidate`, `-budget-candidate`, `-eva-candidate`, `-workspace-candidate`, `-research-candidate`, original `2.0.0` news-core and `-social-candidate`
packages are historical baselines. Their successful startup and collection
checks do not establish acceptance of the current research application.
See [current recovery acceptance](../docs/RECOVERY_ACCEPTANCE_20261008.md),
[previous product acceptance](../docs/PRODUCT_ACCEPTANCE_20261008.md),
[budget acceptance](../docs/BUDGET_ACCEPTANCE_20261006.md),
[typography acceptance](../docs/EVA_TYPOGRAPHY_ACCEPTANCE_20261006.md),
[workspace acceptance](../docs/WORKSPACE_ACCEPTANCE_20261006.md),
[research capability evidence](../docs/RESEARCH_ACCEPTANCE_20261006.md) and
[per-package delivery records](../docs/V2_ACCEPTANCE.md).

`build.py` creates self-contained Electron applications with a private CPython
runtime. End users do not need Python, Node.js, Docker, or a browser installation.
The desktop application uses the v2 backend and the existing local frontend.

## Build

The default build and `--target all` produce only Windows x64 and macOS Apple
Silicon, matching the current requested delivery scope. Intel Mac is not built
by default; old Intel artifacts are retained only as historical records.

Run from the repository root with a development Python installation that has pip:

```powershell
python desktop/build.py --prepare-only
python desktop/build.py --target all
```

On this Windows build host an existing WSL distribution can also apply ad-hoc
signatures without installing or reconfiguring any system tools:

```powershell
python desktop/build.py --target all --mac-signing wsl --wsl-distribution Ubuntu-OpenClaw
```

This optional path verifies the official Apple Codesign 0.29.0 Linux release
against its published SHA-256, unpacks into an isolated Linux build directory
with original symlinks/modes, signs the bundle, then rebuilds its ZIP. It checks
all Mach-O code-page hashes, preserved entitlements, and main bundle metadata /
resource signature hashes. It does **not** count those checks as Apple's native
`codesign --verify` or as macOS launch validation. The finalizer remains included
for the user's native acceptance test. The Windows rcodesign binary was tested
but rejected after it failed to recognize the outer bundle's Info.plist; partial
results from that experiment are never delivered.

Current delivery targets are `windows-x64` and `macos-arm64` (Apple Silicon).
The Intel target is a historical compatibility option, not a current output or
acceptance requirement. `--prepare-only` downloads runtimes and dependencies while
application code is still being edited. Run the normal build again to include
current source files; verified cached downloads and resolved wheels are reused.

The desktop backend uses SQLite and excludes PostgreSQL native libraries. The
wheel resolver targets macOS 13 on both architectures, matching the boundary
declared in Electron's preserved `LSMinimumSystemVersion`. The verified Electron 44.5.1
archives declare **macOS 13.0 minimum on both architectures**; wheel tags alone
are not an OS compatibility guarantee.

The builder pins Electron 44.5.1 and Astral's CPython 3.12.15 standalone release
20261003. Electron ZIPs are checked against the official `SHASUMS256.txt`, itself
verified against the GitHub release asset digest. Python archives are checked
against GitHub's SHA-256 asset digests (or official checksum attachments).
Dependency wheels come from PyPI and their actual SHA-256 values are recorded.
`sgmllib3k` is converted into a pure Python wheel in the build cache because its
upstream release is source-only. No package is installed into the host runtime.

Playwright **1.59.0** uses its official manifest's Chromium revision **1217**,
Chrome for Testing **147.0.7727.15**. The builder packages the complete browser
archive, including framework symlinks, helper executables, locales and notices.
It does not substitute the headless shell. Both login and background collection
launch the same explicit private executable. Python's Playwright wheel includes
its own Node driver; the resolved dependencies include `pyee` and `greenlet`.
Browser URLs come from the pinned wheel's official registry, which redirects
to Google's Chrome for Testing bucket. Initial downloads were checked against
Google's object MD5 and length; their locally computed SHA-256 values are pinned
for rebuilds. Those SHA-256 values are not described as an upstream published
SHA-256 list.

## Output and verification boundaries

The current `-foundation-recovery-20261011` candidate corrects the macOS main
executable name after native CI reproduced a startup failure in the previous
foundation-reuse package. The old `Electron` basename made Electron 44.5.1 treat
the application as an unpackaged development app and select the host Python
instead of the bundled backend. The corrected candidate still requires a new
source freeze, build and native acceptance; earlier archive or signing success
does not establish startup success for it.

Outputs are in `dist/`:

- `PEACE-ATLAS-2.0.0-windows-x64-foundation-recovery-20261011.zip` and a timestamped portable directory.
- `PEACE-ATLAS-2.0.0-macos-arm64-foundation-recovery-20261011.zip`, containing `PEACE ATLAS.app`.
- Per-target JSON files with exact paths, SHA-256 hashes, and structural checks.
- `desktop-build-results-foundation-recovery-20261011.json`, recording the most recent completed invocation.

No Intel Mac package is produced by the current default or `--target all`.
Existing social candidates remain historical records; do not replace current
research paths with a previous social executable merely because it was tested.

The Windows executable is `PEACE ATLAS.exe`. Extract the entire archive before
running it. macOS users extract the app bundle and can copy it to Applications.
The custom icon is embedded in the Windows PE using the official Electron
`rcedit` 5.0.2 npm distribution, verified against its published SHA-512 integrity
and pinned SHA-256. The macOS bundle uses the matching ICNS icon. The renderer
window uses the bundled PNG; original geometric artwork is in `desktop/assets`.
The Windows app is unsigned; Mac candidates use the selected ad-hoc signing
path. Neither carries a publisher's Developer ID signature or notarization.
The macOS packaging step
preserves runtime symlinks and executable modes without requiring Windows
symlink permissions. Both the main binary and `CFBundleExecutable` are renamed
to `PEACE ATLAS`, so Electron's normal `app.isPackaged` detection selects the
private Python and packaged backend. Helper executable names remain unchanged.
The archive audit checks that the main binary is present exactly once, agrees
with the plist, retains its executable bit and has no leftover `Electron` main
binary. Packaging removes the invalidated top-level signature.
**The macOS ZIP has not been launch-validated on a Mac.** Without `--mac-signing
wsl`, it is a staging bundle that requires native signature finalization.
With that option, it has an ad-hoc signature plus the independent checks above,
and still requires native acceptance. Modifying Electron's Info.plist and
resources invalidates its original outer signature, so signatures must be made
after the final application files are assembled.
The archive therefore includes **`Finalize PEACE ATLAS.command` as a double-click
entrypoint**. It signs, verifies, runs a temporary-data smoke test, and then opens
the app. Users do not need to type a command. For automated invocation on a Mac:

```bash
bash './Finalize PEACE ATLAS.command' --smoke
```

The finalizer signs individual Mach-O files (including Python extension modules,
the Playwright Node driver and Chromium's nested executables),
then nested bundles, then the outer app, preserving each executable's existing
entitlements. It verifies with `codesign --verify --deep --strict`, performs an
real desktop/backend smoke in a temporary data directory, and creates
a separate `-foundation-recovery-20261011-adhoc.zip` with its SHA-256. The
finalizer reads and validates `CFBundleExecutable` before signing or launching
the app; it uses the renamed binary without overriding `app.isPackaged`.
Its audit directory contains the signing
log, the smoke screenshot, and health JSON. Errors keep a readable Terminal
window open with the log location; success prints the ready app path. The script has
not been executed on macOS in the Windows build environment. Do not label the
staging ZIP as a Mac launch-tested artifact before native completion.
It also runs the bundled Python `browser_smoke.py` to launch the private Chromium
in visible and headless modes, render an offline `about:blank` page, and close
each browser. This does not log in to any external account or navigate a network
page. Its `browser-smoke.json` records the actual runtime and both modes.

Ad-hoc signing establishes local integrity; it does not provide Developer ID
trust or Apple notarization. macOS may still show its normal unknown-developer
or downloaded-file safety prompt; the user should use the system's normal
explicit approval flow for this known app. The finalizer changes neither
Gatekeeper nor file quarantine attributes, and does not require disabling them.
A public distribution should instead use the owner's
Developer ID signing identity and notarization pipeline. Apple's documented
[inside-out signing process](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac/)
is used; `--deep` is used for verification only, not as a blanket signing step.
The cross-platform [rcodesign tool](https://gregoryszorc.com/docs/apple-codesign/main/apple_codesign_rcodesign_signing.html)
signs a filesystem bundle. The optional WSL path preserves the framework symlink
graph on Linux; it does not flatten the links or mistake ZIP construction for
signature verification. Helper bundle metadata explicitly names each existing
helper executable so both signers can identify their main code correctly.

Each bundle includes `Resources/build-manifest.json` on macOS or
`resources/build-manifest.json` on Windows, with source, runtime, and wheel
provenance. The build requires the manifest to contain the complete current application
allowlist, including every workbench module such as `backup.py`. Missing or duplicate
manifest/archive entries, changed source snapshots and unexpected application paths
fail validation. It checks ZIP CRC, required files, absence of excluded secret
and legacy paths, and macOS symlink/executable metadata. Actual desktop startup,
API health, and a real input → saved evidence → report flow are separate tests.

## Included source and isolation

The backend is copied from a strict allowlist: `app/workbench`, its pure helper
modules (including the public RSS definitions in `gdelt_fetcher.py`), the
`app/static/workbench` frontend, and the two Japan topic/entity YAML files. The
legacy server, `.env`, runtime databases, reports, account seeds, browser login
state, and test fixtures are excluded. Build caches reside in `.build-cache`.
The builder never reads the repository `.env` and performs no recursive delete.

Desktop data belongs in the per-user directory configured by `main.cjs`; do not
store it inside the packaged application or write over the legacy database.
The application starts its private Python interpreter with `backend_entry.py`;
no generated wheel console scripts are needed. Desktop packages use SQLite.
The optional PostgreSQL driver remains a source/server deployment dependency.
The packaged launcher sets `PLAYWRIGHT_BROWSERS_PATH` to its own `resources/browsers`
and `WORKBENCH_DATA_DIR` to the current user's application directory. It forces
`WORKBENCH_SOCIAL_DIR` to that directory's `social` child, so inherited development
environment variables cannot redirect desktop sessions to another profile.
No account state, cookies or saved credentials are included in any build input.

For offline Windows browser acceptance after extraction, run the private Python:

```powershell
& '<portable>/resources/python/python.exe' -I -B '<portable>/resources/backend/browser_smoke.py' --browsers-path '<portable>/resources/browsers' --output '<isolated-audit>/browser-smoke.json'
```

This explicit acceptance action briefly opens a browser window, then closes it.

## Source checks and isolated backup acceptance

Agent delivery adds `agent_api.py` and an exact six-file public asset allowlist
under `app/workbench/agent_assets`. Missing required files fail the build;
credentials, connection exports, installed results and temporary files are not
included. The installed Skill copies only its four public files into a new,
explicitly selected destination. Agent access remains disabled until the desktop
user exports a separate owner-protected connection grant.

Resource editing now uses a build-owned temporary directory, removed after
successful archive ingestion or on failure. Completed source/runtime fixtures,
archive CRC and byte audits, Windows launch/product/restore and controlled actual
DSH SDK contract runs are separate acceptance stages. macOS archive assembly and
byte checks do not establish native launch, signing or notarization.

DSH supports zero count/time stops and long deadlines. The shipped timer avoids
Node's maximum-delay overflow by rearming bounded intervals; the runtime manifest
must include that timer and the changed sidecar. Actual requests still enforce
their model/context/output capacity. Every newly generated report is reviewed;
only unchanged, previously accepted evidence notes may be reused.

These checks do not download runtimes or build an archive:

```powershell
node --test desktop/tests/lifecycle.test.cjs
python -B -m unittest discover -s desktop/tests -p test_build.py -v
```

The lifecycle regression allows a real HTTP shutdown and commit to finish after
3.4 seconds. Virtual clock checks cover an unresponsive shutdown request and ignored
signals: an absolute request deadline is 3 seconds (independent of socket activity), graceful wait 30 seconds, and termination
waits 5 seconds each. Startup cleanup without readiness has only the two termination
waits. Connection regressions cover encryption, staging, backend rejection, commit
rollback and consecutive settings submissions.

Every `WORKBENCH_SMOKE_EXIT=1` launch requires an explicit `WORKBENCH_DATA_DIR`.
Use a fresh directory outside the user's normal app data; the smoke profile owns its
SQLite database and Electron profile and does not read the normal saved connection.
Set `WORKBENCH_SMOKE_BACKUP=1` to test the packaged backup button:

```powershell
$env:WORKBENCH_SMOKE_EXIT = '1'
$env:WORKBENCH_SMOKE_BACKUP = '1'
$env:WORKBENCH_DATA_DIR = '<absolute fresh audit directory>'
& '<portable>/PEACE ATLAS.exe'
```

The smoke opens Settings, commits a synthetic collection draft, clicks
`备份研究数据`, and uses Electron's real download event to save the backup under
`smoke-downloads/`. It verifies completion, SQLite file header/size and that the
source draft did not change. `desktop-smoke.json` records the backup path, synthetic
draft keyword and revision; `desktop-backup-controls.png` records the entrypoint.
SQLite integrity and the draft inside the downloaded file must then be checked
with the bundled Python interpreter (`PRAGMA quick_check` and `wb_drafts`), as part
of package acceptance. Header validation alone does not establish database integrity.
Clear the smoke environment variables after the acceptance run.
