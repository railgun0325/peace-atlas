'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const { spawn } = require('node:child_process');

const source = fs.readFileSync(path.join(__dirname, '..', 'main.cjs'), 'utf8');

function launcher(t, options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'peace-atlas-desktop-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const handlers = new Map();
  const safeStorage = {
    isEncryptionAvailable: () => true,
    encryptString: text => Buffer.from(text),
    decryptString: buffer => buffer.toString('utf8'),
    ...options.safeStorage,
  };
  const electron = {
    app: { setName() {}, setPath() {}, requestSingleInstanceLock: () => true,
      whenReady: () => new Promise(() => {}), on() {}, ...options.app },
    safeStorage, ipcMain: { handle: (name, fn) => handlers.set(name, fn) },
    shell: { openExternal: async () => {}, ...options.shell },
    ...options.electron,
  };
  const context = vm.createContext({
    require: name => name === 'electron' ? electron
      : name === 'node:fs' ? { ...fs, ...options.fs }
      : name === 'node:http' && options.http ? options.http
      : name === 'node:child_process' && options.spawn ? { spawn: options.spawn }
      : name === './restore.cjs' ? { ...require('../restore.cjs'), ...options.restoration } : require(name),
    module: { exports: {} }, process: { ...process, env: options.env || {} },
    __dirname: path.join(__dirname, '..'), Buffer, URL,
    setTimeout: options.clock?.setTimeout || setTimeout,
    clearTimeout: options.clock?.clearTimeout || clearTimeout,
  });
  vm.runInContext(source + `\nmodule.exports = { startBackend, stopBackend, saveConnection, installIpc, external, log, showWindow, lifecycle,
    setState(values) { ({ backend = backend, base = base, dataDir = dataDir, readyFile = readyFile, logFile = logFile, quitting = quitting, window = window } = values); } };`, context);
  const api = context.module.exports;
  api.setState({ dataDir: directory, readyFile: path.join(directory, 'ready.json') });
  return { ...api, directory, handlers };
}

function virtualClock() {
  let now = 0, next = 0;
  const pending = new Map();
  return {
    setTimeout(fn, delay) { const id = ++next; pending.set(id, { fn, at: now + delay }); return id; },
    clearTimeout(id) { pending.delete(id); },
    get now() { return now; },
    get pending() { return pending.size; },
    async advance(amount) {
      const target = now + amount;
      while (true) {
        const nextTimer = [...pending.entries()].sort((a, b) => a[1].at - b[1].at)[0];
        if (!nextTimer || nextTimer[1].at > target) break;
        now = nextTimer[1].at;
        pending.delete(nextTimer[0]);
        nextTimer[1].fn();
        await new Promise(resolve => setImmediate(resolve));
      }
      now = target;
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

function ignoredTermination() {
  const child = childProcess();
  child.kill = signal => { child.kills.push(signal || 'SIGTERM'); return true; };
  return child;
}

function childProcess() {
  const child = new EventEmitter();
  Object.assign(child, { pid: 12345, exitCode: null, signalCode: null,
    stdout: new EventEmitter(), stderr: new EventEmitter(), kills: [] });
  child.kill = signal => {
    child.kills.push(signal || 'SIGTERM');
    child.signalCode = signal || 'SIGTERM';
    child.emit('exit', null, child.signalCode);
  };
  return child;
}

async function service(t, action) {
  const requests = [];
  const server = http.createServer(async (request, response) => {
    let text = '';
    for await (const chunk of request) text += chunk;
    const payload = text ? JSON.parse(text) : null;
    requests.push({ route: request.url, payload });
    const status = await action?.(request, payload, requests.length) || 200;
    response.writeHead(status, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(status < 400 ? { ok: true } : { error: { message: 'busy' } }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return { base: `http://127.0.0.1:${server.address().port}`, requests };
}

test('release smoke refuses to read the normal profile without an explicit data directory', t => {
  assert.throws(() => launcher(t, { env: { WORKBENCH_SMOKE_EXIT: '1' } }), /requires an explicit isolated WORKBENCH_DATA_DIR/);
});

test('release smoke redirects the Electron profile before starting', t => {
  const calls = [], directory = path.join(os.tmpdir(), 'synthetic-isolated-smoke');
  launcher(t, { env: { WORKBENCH_SMOKE_EXIT: '1', WORKBENCH_DATA_DIR: directory },
    app: { setPath: (name, value) => calls.push([name, value]) } });
  assert.deepEqual(calls, [['userData', path.resolve(directory)]]);
});

test('restore acceptance requires explicit exit mode as well as an isolated profile', t => {
  assert.throws(() => launcher(t, { env: { WORKBENCH_SMOKE_RESTORE: '1', WORKBENCH_DATA_DIR: 'synthetic' } }), /requires explicit isolated WORKBENCH_SMOKE_EXIT=1/);
});

test('every close during restoration keeps the current window until the transaction finishes', async t => {
  const handlers = new Map(), contents = new EventEmitter();
  contents.setWindowOpenHandler = () => {};
  let finishLoading, releaseRestore, quitCalls = 0, api;
  const currentWindow = { webContents: contents, once() {}, on: (event, handler) => handlers.set(event, handler),
    loadURL: () => new Promise(resolve => { finishLoading = resolve; }) };
  api = launcher(t, { app: { quit() { quitCalls++; api.setState({ quitting: true }); } }, electron: {
    BrowserWindow: function () { return currentWindow; }, session: { defaultSession: { setPermissionRequestHandler() {} } },
    Menu: { buildFromTemplate: value => value, setApplicationMenu() {} },
  } });
  const transaction = api.lifecycle.restore(() => new Promise(resolve => { releaseRestore = resolve; }));
  await new Promise(resolve => setImmediate(resolve));
  const loading = api.showWindow();
  const prevented = [false, false];
  handlers.get('close')({ preventDefault: () => { prevented[0] = true; } });
  handlers.get('close')({ preventDefault: () => { prevented[1] = true; } });
  assert.deepEqual(prevented, [true, true]); assert.equal(quitCalls, 1);
  releaseRestore(); await transaction;
  api.setState({ quitting: false });
  let ordinaryPrevented = false;
  handlers.get('close')({ preventDefault: () => { ordinaryPrevented = true; } });
  assert.equal(ordinaryPrevented, false); assert.equal(quitCalls, 1);
  finishLoading(); await loading;
});

test('a restored original database still reports the failed restore after replacing the renderer', async t => {
  const actions = [], dialogs = [], oldFrame = { url: 'http://127.0.0.1:12345/#settings' };
  const webContents = { mainFrame: oldFrame };
  const currentWindow = { webContents, isDestroyed: () => false,
    async loadURL(url) { actions.push('renderer-replaced'); webContents.mainFrame = { url }; } };
  const failure = new Error('恢复失败：候选服务未能启动。\n已恢复原数据库并重新启动服务。\n旧库保留位置：C:\\isolated\\restores\\before-test');
  const api = launcher(t, { restoration: { restoreBackup: async options => { await options.navigate(); throw failure; } },
    electron: { dialog: { async showMessageBox(parent, settings) { assert.equal(parent, currentWindow); actions.push('native-error'); dialogs.push(settings); } } } });
  api.setState({ base: 'http://127.0.0.1:12345', window: currentWindow }); api.installIpc();
  await assert.rejects(api.handlers.get('desktop:restore-backup')({ sender: webContents, senderFrame: oldFrame }), error => error === failure);
  assert.notEqual(webContents.mainFrame, oldFrame);
  assert.deepEqual(actions, ['renderer-replaced', 'native-error']);
  assert.equal(dialogs.length, 1); assert.equal(dialogs[0].type, 'error');
  assert.equal(dialogs[0].title, '研究备份恢复失败'); assert.equal(dialogs[0].detail, failure.message);
});

test('a restore failure while quitting or during isolated acceptance does not open a blocking dialog', async t => {
  for (const acceptance of [false, true]) {
    const frame = { url: 'http://127.0.0.1:12345/#settings' }, webContents = { mainFrame: frame };
    const currentWindow = { webContents, isDestroyed: () => false };
    const failure = new Error('恢复失败：旧库回退失败；证据已保留');
    let api, dialogs = 0;
    api = launcher(t, { env: acceptance ? { WORKBENCH_SMOKE_EXIT: '1', WORKBENCH_DATA_DIR: os.tmpdir() } : {},
      restoration: { restoreBackup: async () => { if (!acceptance) api.setState({ quitting: true }); throw failure; } },
      electron: { dialog: { async showMessageBox() { dialogs++; } } } });
    api.setState({ base: 'http://127.0.0.1:12345', window: currentWindow }); api.installIpc();
    await assert.rejects(api.handlers.get('desktop:restore-backup')({ sender: webContents, senderFrame: frame }), error => error === failure);
    assert.equal(dialogs, 0);
  }
});

test('a failure opening the native restore error dialog preserves the original failure', async t => {
  const frame = { url: 'http://127.0.0.1:12345/#settings' }, webContents = { mainFrame: frame };
  const currentWindow = { webContents, isDestroyed: () => false };
  const failure = new Error('恢复失败：回退未完成；原库与失败库已保留');
  const api = launcher(t, { restoration: { restoreBackup: async () => { throw failure; } },
    electron: { dialog: { async showMessageBox() { throw new Error('native dialog unavailable'); } } } });
  api.setState({ base: 'http://127.0.0.1:12345', window: currentWindow, logFile: path.join(api.directory, 'desktop.log') }); api.installIpc();
  await assert.rejects(api.handlers.get('desktop:restore-backup')({ sender: webContents, senderFrame: frame }), error => error === failure);
  const diagnostics = fs.readFileSync(path.join(api.directory, 'desktop.log'), 'utf8');
  assert.match(diagnostics, /回退未完成/); assert.match(diagnostics, /native dialog unavailable/);
});

test('backend startup journals its spawned PID before readiness and owns the parent stdin pipe', async t => {
  const events = [], server = await service(t, () => { events.push('health'); });
  let api;
  api = launcher(t, { spawn: (_python, args, options) => {
    assert.ok(args.includes('--watch-parent-stdin'));
    assert.deepEqual(Array.from(options.stdio), ['pipe', 'pipe', 'pipe']);
    assert.equal(options.windowsHide, true);
    const child = childProcess();
    const ready = args[args.indexOf('--ready-file') + 1];
    fs.writeFileSync(ready, JSON.stringify({ pid: child.pid, port: Number(new URL(server.base).port) }));
    events.push('spawn'); return child;
  } });
  await api.startBackend(pid => { assert.equal(pid, 12345); events.push('journal'); });
  assert.deepEqual(events, ['spawn', 'journal', 'health']);
});

test('Python exits normally with the desktop parent pipe still open', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'peace-atlas-parent-pipe-'));
  t.after(() => { const actual = fs.realpathSync(directory); assert.ok(actual.startsWith(fs.realpathSync(os.tmpdir()) + path.sep)); fs.rmSync(actual, { recursive: true, force: true }); });
  const entry = path.join(directory, 'backend_entry.py');
  fs.copyFileSync(path.join(__dirname, '..', 'backend_entry.py'), entry);
  fs.mkdirSync(path.join(directory, 'app', 'workbench'), { recursive: true });
  fs.writeFileSync(path.join(directory, 'app', 'workbench', 'server.py'), 'from types import SimpleNamespace\ndef create_app():\n    return SimpleNamespace(state=SimpleNamespace())\n');
  // The actual entrypoint and watcher run; this deterministic server closes
  // normally without requiring installed HTTP dependencies or a remote call.
  fs.writeFileSync(path.join(directory, 'uvicorn.py'), 'import time\nclass Config:\n    def __init__(self, *args, **kwargs): pass\nclass Server:\n    def __init__(self, config): self.should_exit = False\n    def run(self, sockets): time.sleep(0.2)\n');
  const python = process.env.WORKBENCH_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const child = spawn(python, ['-B', entry, '--data-dir', path.join(directory, 'data'), '--ready-file', path.join(directory, 'ready.json'), '--watch-parent-stdin'], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  let diagnostics = '';
  child.stdout.on('data', chunk => { diagnostics += chunk; }); child.stderr.on('data', chunk => { diagnostics += chunk; });
  const result = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill(); reject(new Error('Parent-pipe shutdown test timed out')); }, 10000);
    child.once('error', error => { clearTimeout(timer); if (error.code === 'ENOENT') { t.skip('Python is not installed; set WORKBENCH_TEST_PYTHON to the bundled runtime'); resolve(null); } else reject(error); });
    child.once('close', (code, signal) => { clearTimeout(timer); resolve({ code, signal }); });
  });
  if (!result) return;
  assert.deepEqual(result, { code: 0, signal: null }, diagnostics);
  assert.doesNotMatch(diagnostics, /Fatal Python error|_enter_buffered_busy/);
});

test('shutdown allows a commit to finish beyond the former three-second cutoff', async t => {
  const api = launcher(t), child = childProcess();
  const server = await service(t, () => {
    setTimeout(() => { child.exitCode = 0; child.emit('exit', 0, null); }, 3400);
  });
  api.setState({ backend: child, base: server.base });
  fs.writeFileSync(path.join(api.directory, 'ready.json'), '{}');
  await api.stopBackend();
  assert.equal(child.exitCode, 0);
  assert.deepEqual(child.kills, []);
  assert.equal(fs.existsSync(path.join(api.directory, 'ready.json')), false);
});

test('signal-terminated backend is already stopped and stale readiness is cleaned', async t => {
  const api = launcher(t), child = childProcess();
  child.signalCode = 'SIGTERM';
  api.setState({ backend: child });
  fs.writeFileSync(path.join(api.directory, 'ready.json'), '{}');
  await api.stopBackend();
  assert.deepEqual(child.kills, []);
  assert.equal(fs.existsSync(path.join(api.directory, 'ready.json')), false);
});

test('startup failure terminates its owned process without waiting for nonexistent HTTP readiness', async t => {
  const api = launcher(t), child = childProcess();
  api.setState({ backend: child });
  await api.stopBackend();
  assert.deepEqual(child.kills, ['SIGTERM']);
});

test('the absolute request deadline rejects an alive child within 43 seconds and retains readiness', async t => {
  const clock = virtualClock(), child = ignoredTermination();
  const requests = [];
  const api = launcher(t, { clock, http: { request(url, options) {
    requests.push({ url, options });
    const request = new EventEmitter();
    request.write = () => {};
    // Simulate a socket that never emits its inactivity timeout. The launcher's
    // independent deadline must still begin the bounded process-exit waits.
    request.end = () => {};
    request.destroy = error => request.emit('error', error);
    return request;
  } } });
  api.setState({ backend: child, base: 'http://127.0.0.1:12345' });
  fs.writeFileSync(path.join(api.directory, 'ready.json'), '{}');
  let finished = false;
  const stopping = assert.rejects(api.stopBackend(), /进程尚未退出/).then(() => { finished = true; });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'http://127.0.0.1:12345/api/v2/lifecycle/shutdown');
  assert.equal(requests[0].options.method, 'POST');
  assert.equal(requests[0].options.timeout, 3000);
  await clock.advance(32_999);
  assert.deepEqual(child.kills, []);
  assert.equal(finished, false);
  await clock.advance(1);
  assert.deepEqual(child.kills, ['SIGTERM']);
  await clock.advance(4999);
  assert.deepEqual(child.kills, ['SIGTERM']);
  await clock.advance(1);
  assert.deepEqual(child.kills, ['SIGTERM', 'SIGKILL']);
  await clock.advance(4999);
  assert.equal(finished, false);
  await clock.advance(1);
  await stopping;
  assert.equal(clock.now, 43_000);
  assert.equal(finished, true);
  assert.equal(clock.pending, 0);
  assert.equal(child.listenerCount('exit'), 0);
  assert.equal(fs.existsSync(path.join(api.directory, 'ready.json')), true);
});

test('a stalled startup cleanup has only the two bounded termination waits', async t => {
  const clock = virtualClock(), child = ignoredTermination();
  const api = launcher(t, { clock });
  api.setState({ backend: child });
  let finished = false;
  const stopping = assert.rejects(api.stopBackend(), /进程尚未退出/).then(() => { finished = true; });
  assert.deepEqual(child.kills, ['SIGTERM']);
  await clock.advance(5000);
  assert.deepEqual(child.kills, ['SIGTERM', 'SIGKILL']);
  await clock.advance(4999);
  assert.equal(finished, false);
  await clock.advance(1);
  await stopping;
  assert.equal(clock.now, 10_000);
  assert.equal(clock.pending, 0);
  assert.equal(child.listenerCount('exit'), 0);
});

test('startup removes a reused PID readiness file and detects a signal exit', async t => {
  let api;
  api = launcher(t, { spawn: () => {
    assert.equal(fs.existsSync(path.join(api.directory, `ready-${process.pid}.json`)), false);
    const child = childProcess();
    setImmediate(() => child.kill());
    return child;
  } });
  fs.writeFileSync(path.join(api.directory, `ready-${process.pid}.json`), 'stale invalid JSON');
  await assert.rejects(api.startBackend(), /未能启动/);
});

test('a disk staging failure cannot change the running AI connection', async t => {
  const server = await service(t);
  const api = launcher(t, { fs: { writeFileSync() { throw new Error('disk full'); } } });
  api.setState({ base: server.base });
  await assert.rejects(api.saveConnection({ api_key: 'synthetic-test-key' }), /disk full/);
  assert.equal(server.requests.length, 0);
});

test('an encryption failure cannot change the running AI connection', async t => {
  const server = await service(t);
  const api = launcher(t, { safeStorage: { encryptString() { throw new Error('keychain offline'); } } });
  api.setState({ base: server.base });
  await assert.rejects(api.saveConnection({ api_key: 'synthetic-test-key' }), /keychain offline/);
  assert.equal(server.requests.length, 0);
});

test('backend rejection leaves the saved connection intact and removes temporary bytes', async t => {
  const server = await service(t, () => 409), api = launcher(t);
  const saved = JSON.stringify({ base_url: 'https://api.example.com', model: 'old', api_key: 'synthetic-old' });
  const filename = path.join(api.directory, 'connection.secure');
  fs.writeFileSync(filename, saved);
  api.setState({ base: server.base });
  await assert.rejects(api.saveConnection({ model: 'new' }), /busy/);
  assert.equal(fs.readFileSync(filename, 'utf8'), saved);
  assert.equal(fs.existsSync(filename + '.tmp'), false);
});

test('failure committing the encrypted file rolls the running connection back', async t => {
  const server = await service(t);
  const api = launcher(t, { fs: { renameSync() { throw new Error('rename denied'); } } });
  const saved = { base_url: 'https://api.example.com', model: 'old', api_key: 'synthetic-old' };
  const filename = path.join(api.directory, 'connection.secure');
  fs.writeFileSync(filename, JSON.stringify(saved));
  api.setState({ base: server.base });
  await assert.rejects(api.saveConnection({ model: 'new' }), /rename denied/);
  assert.deepEqual(server.requests.map(item => item.payload.model), ['new', 'old']);
  assert.deepEqual(JSON.parse(fs.readFileSync(filename, 'utf8')), saved);
  assert.equal(fs.existsSync(filename + '.tmp'), false);
});

test('rapid IPC submissions serialize and preserve a newly saved key when omitted next', async t => {
  const server = await service(t, async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
  const api = launcher(t);
  api.setState({ base: server.base });
  api.installIpc();
  const save = api.handlers.get('desktop:save-connection');
  await Promise.all([save(null, { api_key: 'synthetic-new', model: 'first' }), save(null, { model: 'second' })]);
  const saved = JSON.parse(fs.readFileSync(path.join(api.directory, 'connection.secure'), 'utf8'));
  assert.equal(saved.api_key, 'synthetic-new');
  assert.equal(saved.model, 'second');
  assert.deepEqual(server.requests.map(item => item.payload.api_key), ['synthetic-new', 'synthetic-new']);
});

test('unwritable logs and a rejected external opener do not crash lifecycle handlers', async t => {
  const api = launcher(t, { fs: { appendFileSync() { throw new Error('read only'); } },
    shell: { openExternal: async () => { throw new Error('no browser'); } } });
  api.setState({ logFile: path.join(api.directory, 'desktop.log') });
  assert.doesNotThrow(() => api.log('synthetic test'));
  api.external('https://example.com');
  await new Promise(resolve => setImmediate(resolve));
});
