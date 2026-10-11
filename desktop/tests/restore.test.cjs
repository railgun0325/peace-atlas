'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
const restore = require('../restore.cjs');

function fixture(t, overrides = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'peace-atlas-restore-test-'));
  t.after(() => { const actual = fs.realpathSync(directory); assert.ok(actual.startsWith(fs.realpathSync(os.tmpdir()) + path.sep)); fs.rmSync(actual, { recursive: true, force: true }); });
  const profile = path.join(directory, 'profile'); fs.mkdirSync(profile);
  const source = path.join(directory, 'backup.sqlite3'); fs.writeFileSync(source, 'original-source');
  fs.writeFileSync(path.join(profile, 'workbench.sqlite3'), 'old-db');
  fs.writeFileSync(path.join(profile, 'workbench.sqlite3-wal'), 'old-wal');
  fs.writeFileSync(path.join(profile, 'workbench.sqlite3-shm'), 'old-shm');
  fs.writeFileSync(path.join(profile, 'connection.secure'), 'synthetic-encrypted-connection');
  fs.mkdirSync(path.join(profile, 'social')); fs.writeFileSync(path.join(profile, 'social', 'sentinel'), 'synthetic-social');
  let running = true, starts = 0, stops = 0, stage;
  const actions = [];
  const options = { dataDir: profile, chooseSource: async () => { actions.push('select'); return source; },
    prepare: async (_source, output) => {
      stage = output; actions.push('prepare');
      const bytes = Buffer.alloc(4096); bytes.write('SQLite format 3\0'); bytes.write('candidate-marker', 100);
      fs.writeFileSync(path.join(output, 'workbench.sqlite3'), bytes);
      const summary = { size_bytes: bytes.length, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), counts: { items: 3, accounts: 2, reports: 1, research_runs: 1, jobs: 2, drafts: 1 }, interrupted_jobs_count: 2, backup_file_name: path.basename(source) };
      fs.writeFileSync(path.join(output, 'summary.json'), JSON.stringify(summary));
      return summary;
    }, confirm: async () => { actions.push('confirm'); return true; },
    stop: async () => { stops++; actions.push('stop'); running = false; }, terminal: () => !running,
    start: async hook => { starts++; actions.push('start'); running = true; hook?.(123456); }, navigate: async () => { actions.push('navigate'); },
    ...overrides,
  };
  return { directory, profile, source, options, actions, get starts() { return starts; }, get stops() { return stops; }, get stage() { return stage; } };
}

test('selection cancellation and native confirmation cancellation preserve the running backend', async t => {
  for (const overrides of [{ chooseSource: async () => null }, { confirm: async () => false }]) {
    const f = fixture(t, overrides);
    assert.deepEqual(await restore.restoreBackup(f.options), { cancelled: true });
    assert.equal(f.stops, 0); assert.equal(f.starts, 0);
    assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3'), 'utf8'), 'old-db');
    if (f.stage) assert.equal(fs.existsSync(f.stage), false);
  }
});

test('invalid preflight and tampered candidate never stop the backend', async t => {
  const invalid = fixture(t, { prepare: async () => { throw Object.assign(new Error('不是有效备份'), { code: 'restore_integrity' }); } });
  await assert.rejects(restore.restoreBackup(invalid.options), /不是有效备份/);
  assert.equal(invalid.stops, 0);
  const changed = fixture(t), prepare = changed.options.prepare;
  changed.options.prepare = async (...args) => { const summary = await prepare(...args); fs.appendFileSync(path.join(args[1], 'workbench.sqlite3'), 'tampered'); return summary; };
  await assert.rejects(restore.restoreBackup(changed.options), /候选数据库与检查结果不一致/);
  assert.equal(changed.stops, 0);
});

test('current database, sidecars and a hardlink cannot be selected as backup sources', async t => {
  const f = fixture(t);
  for (const name of ['workbench.sqlite3', 'workbench.sqlite3-wal', 'workbench.sqlite3-shm']) {
    f.options.chooseSource = async () => path.join(f.profile, name);
    await assert.rejects(restore.restoreBackup(f.options), /当前正在使用|独立的 SQLite/);
  }
  const link = path.join(f.directory, 'hardlink.sqlite3'); fs.linkSync(path.join(f.profile, 'workbench.sqlite3'), link);
  f.options.chooseSource = async () => link;
  await assert.rejects(restore.restoreBackup(f.options), /当前正在使用/);
  assert.equal(f.stops, 0);
});

test('confirmed restore preserves the original complete DB set and unrelated state', async t => {
  const f = fixture(t), result = await restore.restoreBackup(f.options);
  assert.equal(result.restored, true); assert.equal(result.summary.counts.items, 3);
  assert.deepEqual(f.actions, ['select', 'prepare', 'confirm', 'stop', 'start', 'navigate']);
  for (const suffix of ['', '-wal', '-shm']) assert.equal(fs.readFileSync(path.join(result.archive_path, 'workbench.sqlite3' + suffix), 'utf8'), suffix === '' ? 'old-db' : 'old-' + suffix.slice(1));
  assert.equal(fs.readFileSync(f.source, 'utf8'), 'original-source');
  assert.equal(fs.readFileSync(path.join(f.profile, 'connection.secure'), 'utf8'), 'synthetic-encrypted-connection');
  assert.equal(fs.readFileSync(path.join(f.profile, 'social', 'sentinel'), 'utf8'), 'synthetic-social');
  assert.equal(fs.existsSync(path.join(f.profile, '.restore-transaction.json')), false);
  assert.equal(fs.existsSync(f.stage), false);
});

test('a partial archive move failure restores all old files and restarts the original backend', async t => {
  const f = fixture(t), originalRename = fs.renameSync;
  let rejected = false;
  f.options.fs = { ...fs, renameSync: (from, to) => { if (!rejected && from === path.join(f.profile, 'workbench.sqlite3-wal')) { rejected = true; throw new Error('simulated move denied'); } return originalRename(from, to); } };
  let error; try { await restore.restoreBackup(f.options); } catch (caught) { error = caught; }
  assert.match(error.message, /simulated move denied/); assert.match(error.message, /已恢复原数据库/);
  assert.equal(f.starts, 1);
  for (const [name, content] of [['workbench.sqlite3', 'old-db'], ['workbench.sqlite3-wal', 'old-wal'], ['workbench.sqlite3-shm', 'old-shm']]) assert.equal(fs.readFileSync(path.join(f.profile, name), 'utf8'), content);
  assert.equal(fs.readFileSync(path.join(error.archive_path, 'workbench.sqlite3'), 'utf8'), 'old-db');
});

test('partial archive rollback preserves sidecars before restarting and records the old service PID', async t => {
  const f = fixture(t), originalRename = fs.renameSync;
  let rejected = false;
  f.options.fs = { ...fs, renameSync: (from, to) => {
    if (!rejected && from === path.join(f.profile, 'workbench.sqlite3-wal')) { rejected = true; throw new Error('simulated interrupted archive'); }
    return originalRename(from, to);
  } };
  f.options.start = async onSpawn => {
    const before = restore.readJournal(f.profile);
    assert.equal(before.phase, 'candidate-starting'); assert.equal(before.backend_pid, null);
    for (const [name, bytes] of [['workbench.sqlite3', 'old-db'], ['workbench.sqlite3-wal', 'old-wal'], ['workbench.sqlite3-shm', 'old-shm']]) {
      assert.equal(fs.readFileSync(path.join(before.archive, name), 'utf8'), bytes);
    }
    onSpawn(765432);
    fs.writeFileSync(path.join(f.profile, 'workbench.sqlite3-wal'), 'old-service-new-wal');
    fs.writeFileSync(path.join(f.profile, 'workbench.sqlite3-shm'), 'old-service-new-shm');
    throw new Error('second desktop crash while original service started');
  };
  await assert.rejects(restore.restoreBackup(f.options), /second desktop crash/);
  const journal = restore.readJournal(f.profile);
  assert.equal(journal.phase, 'candidate-starting'); assert.equal(journal.backend_pid, 765432);
  assert.throws(() => restore.recoverPendingRestore(f.profile, { processAlive: pid => pid === 765432 }), /进程仍在运行/);
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3-wal'), 'utf8'), 'old-service-new-wal');
  const result = restore.recoverPendingRestore(f.profile, { processAlive: () => false });
  assert.equal(result.recovered, true);
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3-wal'), 'utf8'), 'old-wal');
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3-shm'), 'utf8'), 'old-shm');
  assert.equal(fs.readFileSync(path.join(result.failed_archive_path, 'workbench.sqlite3-wal'), 'utf8'), 'old-service-new-wal');
});

test('rollback spawn without a recorded PID conservatively blocks recovery', async t => {
  const f = fixture(t), start = f.options.start;
  let attempts = 0;
  f.options.start = async onSpawn => {
    if (++attempts === 1) { await start(onSpawn); throw new Error('candidate failed'); }
    const journal = restore.readJournal(f.profile);
    assert.equal(journal.phase, 'candidate-starting'); assert.equal(journal.backend_pid, null);
    throw new Error('original spawn interrupted before PID record');
  };
  await assert.rejects(restore.restoreBackup(f.options), /original spawn interrupted/);
  assert.throws(() => restore.recoverPendingRestore(f.profile, { processAlive: () => false }), /无法确认子进程已经退出/);
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3'), 'utf8'), 'old-db');
});

test('a candidate launch failure stops the candidate and rolls back without losing the archive', async t => {
  const f = fixture(t), start = f.options.start;
  let launches = 0;
  f.options.start = async hook => { await start(hook); if (++launches === 1) throw new Error('candidate health failed'); };
  let error; try { await restore.restoreBackup(f.options); } catch (caught) { error = caught; }
  assert.match(error.message, /已恢复原数据库/); assert.equal(f.stops, 2); assert.equal(f.starts, 2);
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3'), 'utf8'), 'old-db');
  assert.ok(fs.existsSync(path.join(error.failed_archive_path, 'workbench.sqlite3')));
  assert.equal(fs.readFileSync(path.join(error.archive_path, 'workbench.sqlite3-wal'), 'utf8'), 'old-wal');
  assert.equal(fs.existsSync(path.join(f.profile, '.restore-transaction.json')), false);
});

test('an alive backend is rejected before any database move', async t => {
  const f = fixture(t, { stop: async () => {}, terminal: () => false });
  await assert.rejects(restore.restoreBackup(f.options), /尚未退出/);
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3'), 'utf8'), 'old-db');
  assert.equal(fs.existsSync(path.join(f.profile, 'restores')), false);
});

test('rollback never covers a candidate backend that is still alive and retains its journal', async t => {
  const f = fixture(t), start = f.options.start;
  let stopping = 0, running = true;
  f.options.stop = async () => { if (++stopping === 1) running = false; else throw new Error('candidate ignored SIGKILL'); };
  f.options.terminal = () => !running;
  f.options.start = async hook => { running = true; await start(hook); throw new Error('failed startup'); };
  let error; try { await restore.restoreBackup(f.options); } catch (caught) { error = caught; }
  assert.match(error.message, /进程仍在运行/);
  assert.ok(fs.existsSync(path.join(f.profile, '.restore-transaction.json')));
  assert.equal(fs.readFileSync(path.join(error.archive_path, 'workbench.sqlite3'), 'utf8'), 'old-db');
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3')).subarray(0, 16).toString('ascii'), 'SQLite format 3\0');
});

test('all rollback file errors are retained and a damaged old set is not restarted', async t => {
  const f = fixture(t), start = f.options.start;
  f.options.start = async hook => { await start(hook); throw new Error('failed launch'); };
  f.options.fs = { ...fs, copyFileSync: () => { throw new Error('simulated copy failure'); } };
  let error; try { await restore.restoreBackup(f.options); } catch (caught) { error = caught; }
  assert.equal(error.rollback_errors.filter(message => message.includes('旧库回退失败')).length, 3);
  assert.equal(f.starts, 1);
  assert.ok(fs.existsSync(path.join(f.profile, '.restore-transaction.json')));
});

function pending(t, installed = false) {
  const f = fixture(t), uuid = crypto.randomUUID();
  const stage = restore.ownedDirectory(f.profile, 'restore-staging', uuid);
  const archive = restore.ownedDirectory(f.profile, 'restores', 'before-' + new Date().toISOString().replace(/[^0-9]/g, '') + '-' + crypto.randomUUID());
  const names = ['workbench.sqlite3', 'workbench.sqlite3-wal', 'workbench.sqlite3-shm'];
  const oldFiles = names.map(name => { const content = fs.readFileSync(path.join(f.profile, name)); return { name, size: content.length, sha256: crypto.createHash('sha256').update(content).digest('hex') }; });
  const record = { schema: 1, txid: uuid, phase: installed ? 'candidate-running' : 'swapping', archive, stage, old_files: oldFiles, original_empty: false, backend_terminal: true, backend_pid: installed ? 123456 : null };
  restore.writeJournal(f.profile, record);
  fs.renameSync(path.join(f.profile, names[0]), path.join(archive, names[0]));
  if (installed) {
    for (const name of names.slice(1)) fs.renameSync(path.join(f.profile, name), path.join(archive, name));
    fs.writeFileSync(path.join(f.profile, names[0]), 'failed-candidate');
    fs.writeFileSync(path.join(f.profile, names[0] + '-wal'), 'failed-candidate-wal');
  }
  return { ...f, record, archive, stage };
}

test('startup journal recovery handles a crash halfway through the archive moves', t => {
  const f = pending(t), result = restore.recoverPendingRestore(f.profile, { processAlive: () => false });
  assert.equal(result.recovered, true);
  for (const [name, content] of [['workbench.sqlite3', 'old-db'], ['workbench.sqlite3-wal', 'old-wal'], ['workbench.sqlite3-shm', 'old-shm']]) assert.equal(fs.readFileSync(path.join(f.profile, name), 'utf8'), content);
  assert.ok(fs.existsSync(path.join(f.profile, '.restore-transaction.json')));
  restore.completeRecovery(f.profile, result.txid);
  assert.equal(fs.existsSync(path.join(f.profile, '.restore-transaction.json')), false);
});

test('startup journal recovery preserves a failed installed candidate and rejects a live PID', t => {
  const f = pending(t, true);
  assert.throws(() => restore.recoverPendingRestore(f.profile, { processAlive: () => true }), /进程仍在运行/);
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3'), 'utf8'), 'failed-candidate');
  const result = restore.recoverPendingRestore(f.profile, { processAlive: () => false });
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3'), 'utf8'), 'old-db');
  assert.equal(fs.readFileSync(path.join(result.failed_archive_path, 'workbench.sqlite3'), 'utf8'), 'failed-candidate');
  assert.equal(fs.readFileSync(path.join(result.archive_path, 'workbench.sqlite3-shm'), 'utf8'), 'old-shm');
});

test('startup rollback completes the archive and records its restarted service across another crash', t => {
  const f = pending(t), first = restore.recoverPendingRestore(f.profile, { processAlive: () => false });
  for (const [name, bytes] of [['workbench.sqlite3', 'old-db'], ['workbench.sqlite3-wal', 'old-wal'], ['workbench.sqlite3-shm', 'old-shm']]) {
    assert.equal(fs.readFileSync(path.join(first.archive_path, name), 'utf8'), bytes);
  }
  restore.recoveryStarting(f.profile, first.txid);
  assert.throws(() => restore.recoverPendingRestore(f.profile, { processAlive: () => false }), /无法确认子进程已经退出/);
  restore.recoveryStarting(f.profile, first.txid, 876543);
  fs.writeFileSync(path.join(f.profile, 'workbench.sqlite3-wal'), 'second-generation-wal');
  assert.throws(() => restore.recoverPendingRestore(f.profile, { processAlive: pid => pid === 876543 }), /进程仍在运行/);
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3-wal'), 'utf8'), 'second-generation-wal');
  const second = restore.recoverPendingRestore(f.profile, { processAlive: () => false });
  assert.equal(fs.readFileSync(path.join(second.archive_path, 'workbench.sqlite3-wal'), 'utf8'), 'old-wal');
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3-wal'), 'utf8'), 'old-wal');
});

test('an interrupted archive copy can be retried without losing the original current sidecar', t => {
  const f = pending(t), archived = path.join(f.archive, 'workbench.sqlite3-wal');
  const interrupted = { ...fs, copyFileSync(from, to, flags) {
    if (to.startsWith(archived + '.copy-')) { fs.writeFileSync(to, 'partial-archive-copy'); throw new Error('archive copy interrupted'); }
    return fs.copyFileSync(from, to, flags);
  } };
  assert.throws(() => restore.recoverPendingRestore(f.profile, { fs: interrupted, processAlive: () => false }), /archive copy interrupted/);
  assert.equal(fs.existsSync(archived), false);
  assert.equal(fs.readFileSync(path.join(f.profile, 'workbench.sqlite3-wal'), 'utf8'), 'old-wal');
  const result = restore.recoverPendingRestore(f.profile, { processAlive: () => false });
  assert.equal(result.recovered, true);
  assert.equal(fs.readFileSync(archived, 'utf8'), 'old-wal');
  const evidence = fs.readdirSync(f.archive).filter(name => name.startsWith('workbench.sqlite3-wal.copy-'));
  assert.equal(evidence.length, 1);
  assert.equal(fs.readFileSync(path.join(f.archive, evidence[0]), 'utf8'), 'partial-archive-copy');
});

test('a legacy incomplete archive sibling is retained and rebuilt from a verified current original', t => {
  const f = pending(t), archived = path.join(f.archive, 'workbench.sqlite3-wal');
  fs.writeFileSync(archived, 'legacy-partial-copy');
  const result = restore.recoverPendingRestore(f.profile, { processAlive: () => false });
  assert.equal(result.recovered, true);
  assert.equal(fs.readFileSync(archived, 'utf8'), 'old-wal');
  const evidence = fs.readdirSync(f.archive).filter(name => name.startsWith('workbench.sqlite3-wal.incomplete-'));
  assert.equal(evidence.length, 1);
  assert.equal(fs.readFileSync(path.join(f.archive, evidence[0]), 'utf8'), 'legacy-partial-copy');
});

test('an interrupted old-file copy retains the original candidate in an earlier failed generation', t => {
  const f = pending(t, true), current = path.join(f.profile, 'workbench.sqlite3');
  const interrupted = { ...fs, copyFileSync(from, to, flags) {
    if (to === current) { fs.writeFileSync(to, 'partial-old-copy'); throw new Error('disk interrupted old copy'); }
    return fs.copyFileSync(from, to, flags);
  } };
  assert.throws(() => restore.recoverPendingRestore(f.profile, { processAlive: () => false, fs: interrupted }), /disk interrupted old copy/);
  const firstArchive = restore.readJournal(f.profile).failed_archive;
  assert.equal(fs.readFileSync(path.join(firstArchive, 'workbench.sqlite3'), 'utf8'), 'failed-candidate');
  assert.equal(fs.readFileSync(current, 'utf8'), 'partial-old-copy');
  const result = restore.recoverPendingRestore(f.profile, { processAlive: () => false });
  assert.notEqual(result.failed_archive_path, firstArchive);
  assert.equal(fs.readFileSync(path.join(firstArchive, 'workbench.sqlite3'), 'utf8'), 'failed-candidate');
  assert.equal(fs.readFileSync(path.join(result.failed_archive_path, 'workbench.sqlite3'), 'utf8'), 'partial-old-copy');
  assert.equal(fs.readFileSync(current, 'utf8'), 'old-db');
  assert.equal(fs.readFileSync(path.join(f.archive, 'workbench.sqlite3'), 'utf8'), 'old-db');
});

test('invalid journal paths and incomplete old sets prevent an empty database startup', t => {
  const f = pending(t);
  f.record.archive = path.join(f.directory, 'outside-profile'); restore.writeJournal(f.profile, f.record);
  assert.throws(() => restore.recoverPendingRestore(f.profile), /数据文件夹/);
  assert.equal(fs.existsSync(path.join(f.profile, 'workbench.sqlite3')), false);
  f.record.archive = f.archive; restore.writeJournal(f.profile, f.record);
  fs.unlinkSync(path.join(f.archive, 'workbench.sqlite3'));
  assert.throws(() => restore.recoverPendingRestore(f.profile), /旧库文件不完整/);
  assert.equal(fs.existsSync(path.join(f.profile, 'workbench.sqlite3')), false);
});

test('queued duplicate restore triggers share one transaction and configuration saves wait', async () => {
  const coordinator = restore.createLifecycleCoordinator(), actions = [];
  let release;
  const blocked = new Promise(resolve => { release = resolve; });
  const first = coordinator.restore(async () => { actions.push('restore'); await blocked; actions.push('restored'); return { restored: true }; });
  const duplicate = coordinator.restore(async () => { throw new Error('must not run'); });
  const saving = coordinator.mutation(() => { actions.push('save'); });
  assert.equal(first, duplicate); await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(actions, ['restore']); release(); await Promise.all([first, duplicate, saving]);
  assert.deepEqual(actions, ['restore', 'restored', 'save']);
});

test('quit races cancel a queued restore and wait for an active confirmed transaction', async () => {
  const queued = restore.createLifecycleCoordinator(), events = [];
  let release;
  const first = queued.mutation(() => new Promise(resolve => { release = resolve; }));
  await new Promise(resolve => setImmediate(resolve));
  const pendingRestore = queued.restore(() => { throw new Error('quit must cancel queued restore'); });
  const exiting = queued.quit(() => { events.push('quit'); }); release();
  assert.deepEqual(await pendingRestore, { cancelled: true }); await Promise.all([first, exiting]);
  const active = restore.createLifecycleCoordinator();
  let continueRestore;
  const transaction = active.restore(async () => { events.push('active'); await new Promise(resolve => { continueRestore = resolve; }); events.push('committed'); });
  await new Promise(resolve => setImmediate(resolve));
  const waitingQuit = active.quit(() => { events.push('final-stop'); });
  assert.deepEqual(events, ['quit', 'active']); continueRestore(); await Promise.all([transaction, waitingQuit]);
  assert.deepEqual(events, ['quit', 'active', 'committed', 'final-stop']);
  await assert.rejects(active.mutation(() => {}), /应用正在退出/);
});

test('private preparation terminates a timed-out owned process before reporting failure', async () => {
  let child;
  const spawning = (_python, _args, options) => { child = spawn(process.execPath, ['-e', 'setInterval(()=>{},1000)'], options); return child; };
  await assert.rejects(restore.runPrivateJson({ spawn: spawning, python: process.execPath, entry: 'ignored', args: [], env: process.env, timeout: 30 }), /检查超时/);
  assert.equal(restore.live(child), false);
});

test('preload never forwards renderer-supplied paths or arguments for restore', () => {
  let api, forwarded;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', 'preload.cjs'), 'utf8'), { require: () => ({ contextBridge: { exposeInMainWorld: (_name, value) => { api = value; } }, ipcRenderer: { invoke: (...args) => { forwarded = args; } } }) });
  api.restoreBackup('C:\\forged\\current.sqlite3', { confirmed: true });
  assert.deepEqual(forwarded, ['desktop:restore-backup']);
});

test('restore IPC rejects arbitrary arguments, external windows and subframes', () => {
  const handlers = new Map(), webContents = { mainFrame: { url: 'http://127.0.0.1:12345/#settings' } };
  const electron = { app: { setName() {}, requestSingleInstanceLock: () => true, whenReady: () => new Promise(() => {}), on() {} }, ipcMain: { handle: (name, fn) => handlers.set(name, fn) } };
  const context = vm.createContext({ require: name => name === 'electron' ? electron : name === './restore.cjs' ? restore : require(name), module: { exports: {} }, process: { ...process, env: {} }, __dirname: path.join(__dirname, '..'), Buffer, URL, setTimeout, clearTimeout });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'main.cjs'), 'utf8') + '\nwindow={webContents:globalThis.testContents,isDestroyed:()=>false};base="http://127.0.0.1:12345";installIpc();', Object.assign(context, { testContents: webContents }));
  const handler = handlers.get('desktop:restore-backup'), trusted = { sender: webContents, senderFrame: webContents.mainFrame };
  assert.throws(() => handler(trusted, 'C:\\forged.sqlite3'), /不能传入/);
  assert.throws(() => handler({ ...trusted, sender: {} }), /不能传入/);
  assert.throws(() => handler({ ...trusted, senderFrame: { url: webContents.mainFrame.url } }), /不能传入/);
  webContents.mainFrame.url = 'https://evil.example/'; assert.throws(() => handler(trusted), /不能传入/);
});
