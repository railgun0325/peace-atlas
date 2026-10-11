'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const http = require('node:http');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');

const repo = path.resolve(__dirname, '../..');
const mainSource = fs.readFileSync(path.join(repo, 'desktop/main.cjs'), 'utf8');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
function fixture(t, name) {
  const evidence = process.env.WORKBENCH_CONFIGURATION_EVIDENCE_DIR;
  const root = evidence ? path.resolve(evidence) : os.tmpdir();
  fs.mkdirSync(root, { recursive: true });
  const directory = fs.mkdtempSync(path.join(root, name + '-'));
  if (!evidence) t.after(() => {
    const actual = fs.realpathSync(directory), ownedRoot = fs.realpathSync(root);
    assert.ok(actual.startsWith(ownedRoot + path.sep));
    fs.rmSync(actual, { recursive: true, force: true });
  });
  return directory;
}
function launcher(t, options = {}) {
  const directory = options.directory || fixture(t, 'connection');
  const handlers = new Map(), dialogs = [], navigation = [], appEvents = new Map();
  let ready, windowCount = 0;
  const contents = new EventEmitter();
  contents.setWindowOpenHandler = () => {};
  contents.send = () => {};
  const currentWindow = new EventEmitter();
  Object.assign(currentWindow, { webContents: contents, isDestroyed: () => false, show() {}, showInactive() {},
    loadURL: async url => { navigation.push(url); }, isMinimized: () => false, focus() {} });
  const electron = {
    app: { setName() {}, setPath() {}, requestSingleInstanceLock: () => true,
      whenReady: () => ({ then(callback) { ready = callback; } }),
      on: (name, callback) => appEvents.set(name, callback), getPath: () => directory,
      getVersion: () => '2.0.0', quit() {}, exit() {} },
    BrowserWindow: function () { windowCount++; return currentWindow; },
    Menu: { buildFromTemplate: value => value, setApplicationMenu() {} },
    session: { defaultSession: { setPermissionRequestHandler() {} } },
    safeStorage: { isEncryptionAvailable: () => true, encryptString: text => Buffer.from(text),
      decryptString: bytes => bytes.toString('utf8'), ...options.safeStorage },
    ipcMain: { handle: (name, callback) => handlers.set(name, callback) },
    dialog: { showErrorBox: (title, message) => dialogs.push({ title, message }),
      showMessageBox: async (_parent, settings) => { dialogs.push(settings); return { response: 0 }; } },
  };
  const context = vm.createContext({
    require: name => name === 'electron' ? electron
      : name === 'node:fs' ? { ...fs, ...options.fs }
      : name === 'node:child_process' && options.spawn ? { spawn: options.spawn }
      : name === './restore.cjs' ? require('../restore.cjs') : require(name),
    module: { exports: {} }, process: { ...process, env: { WORKBENCH_DATA_DIR: directory, ...options.env } },
    __dirname: path.join(repo, 'desktop'), Buffer, URL, setTimeout, clearTimeout,
  });
  vm.runInContext(mainSource + `\nmodule.exports = { startBackend, stopBackend, saveConnection, getConnection, installIpc, lifecycle, validateConnectionSmokeFixture, smokeNavigate, smokeReloadPage,
    setState(values) { ({ base = base, token = token, dataDir = dataDir, backend = backend, window = window, quitting = quitting } = values); },
    state() { return { base, token, pid: backend?.pid, connectionUnconfirmed }; } };`, context);
  const api = context.module.exports;
  api.setState({ dataDir: directory });
  return { ...api, directory, handlers, dialogs, navigation, currentWindow, appEvents,
    ready: () => ready(), windowCount: () => windowCount };
}
async function service(t, action) {
  const requests = [];
  const server = http.createServer(async (request, response) => {
    let body = ''; for await (const chunk of request) body += chunk;
    const payload = body ? JSON.parse(body) : null;
    requests.push({ route: request.url, payload });
    if (await action?.(request, response, payload) === false) return;
    response.writeHead(200, { 'Content-Type': 'application/json' }); response.end('{"ok":true}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  return { base: 'http://127.0.0.1:' + server.address().port, requests };
}
function fakeChild(pid = 12345) {
  const child = new EventEmitter();
  Object.assign(child, { pid, exitCode: null, signalCode: null, stdout: new EventEmitter(), stderr: new EventEmitter() });
  child.kill = signal => { child.signalCode = signal || 'SIGTERM'; child.emit('exit', null, child.signalCode); };
  return child;
}
const old = { base_url: 'https://api.example.com', model: 'old-model', api_key: 'synthetic-old-key' };
function saveFixture(api, value = old) { fs.writeFileSync(path.join(api.directory, 'connection.secure'), JSON.stringify(value)); }
function getStatus(api) { api.installIpc(); return api.handlers.get('desktop:connection')(); }
async function getJson(api, route) {
  const current = api.state();
  return new Promise((resolve, reject) => {
    const request = http.get(current.base + route, { headers: { 'X-Workbench-Token': current.token } }, response => {
      let content = ''; response.on('data', bytes => { content += bytes; }); response.on('end', () => {
        try { assert.equal(response.statusCode, 200, content); resolve(JSON.parse(content)); } catch (error) { reject(error); }
      });
    }); request.on('error', reject);
  });
}

test('native connection acceptance requires exit mode, an isolated profile and an explicit fixture hash', t => {
  assert.throws(() => launcher(t, { env: { WORKBENCH_SMOKE_CONNECTION_RECOVERY: '1' } }), /requires explicit isolated WORKBENCH_SMOKE_EXIT/);
  assert.throws(() => launcher(t, { env: { WORKBENCH_SMOKE_CONNECTION_RECOVERY: '1', WORKBENCH_SMOKE_EXIT: '1' } }), /synthetic fixture SHA-256/);
  assert.throws(() => launcher(t, { env: { WORKBENCH_SMOKE_CONNECTION_RECOVERY: '1', WORKBENCH_SMOKE_EXIT: '1', WORKBENCH_SMOKE_CONNECTION_EXPECTED_SHA256: 'a'.repeat(64), WORKBENCH_DATA_DIR: '' } }), /explicit isolated WORKBENCH_DATA_DIR/);
});

test('native connection acceptance refuses a hash mismatch and readable saved settings before backend startup', t => {
  const directory = fixture(t, 'native-connection-preflight'), broken = 'synthetic-unreadable-configuration';
  const api = launcher(t, { directory, env: { WORKBENCH_SMOKE_CONNECTION_RECOVERY: '1', WORKBENCH_SMOKE_EXIT: '1', WORKBENCH_SMOKE_CONNECTION_EXPECTED_SHA256: sha(broken) } });
  const filename = path.join(directory, 'connection.secure');
  fs.writeFileSync(filename, broken);
  assert.doesNotThrow(() => api.validateConnectionSmokeFixture());
  fs.writeFileSync(filename, 'different-synthetic-fixture');
  assert.throws(() => api.validateConnectionSmokeFixture(), /exact unreadable synthetic fixture/);
  const readable = JSON.stringify(old);
  const readableApi = launcher(t, { directory: fixture(t, 'native-readable-preflight'), env: { WORKBENCH_SMOKE_CONNECTION_RECOVERY: '1', WORKBENCH_SMOKE_EXIT: '1', WORKBENCH_SMOKE_CONNECTION_EXPECTED_SHA256: sha(readable) } });
  saveFixture(readableApi);
  assert.throws(() => readableApi.validateConnectionSmokeFixture(), /exact unreadable synthetic fixture/);
});

test('native connection acceptance navigates through the sidebar and checks the requested settled route', async t => {
  const api = launcher(t), expressions = [];
  api.currentWindow.webContents.executeJavaScript = async expression => { expressions.push(expression); return true; };
  api.setState({ window: api.currentWindow });
  await api.smokeNavigate('settings', 'document.querySelector("safe-settings-ready")');
  assert.equal(api.navigation.length, 0);
  assert.equal(expressions.length, 3);
  assert.match(expressions[1], /\.click\(\)/);
  assert.match(expressions[1], /data-route/);
  assert.match(expressions[2], /location\.hash.*#settings/);
  assert.match(expressions[2], /aria-busy/);
  assert.match(expressions[2], /safe-settings-ready/);
});

test('native connection independent refresh waits for did-finish-load rather than the old DOM', async t => {
  const api = launcher(t), contents = api.currentWindow.webContents;
  let reloads = 0, finished = false;
  contents.reload = () => { reloads++; };
  api.setState({ window: api.currentWindow });
  const reload = api.smokeReloadPage().then(() => { finished = true; });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(reloads, 1); assert.equal(finished, false);
  contents.emit('did-finish-load'); await reload;
  assert.equal(finished, true); assert.equal(contents.listenerCount('did-finish-load'), 0);
  assert.equal(contents.listenerCount('did-fail-load'), 0);
});

test('native connection independent refresh reports a safe load failure and removes both listeners', async t => {
  const api = launcher(t), contents = api.currentWindow.webContents;
  contents.reload = () => {};
  api.setState({ window: api.currentWindow });
  const rejected = assert.rejects(api.smokeReloadPage(), error => {
    assert.match(error.message, /independent reload failed/); assert.doesNotMatch(error.message, /SYNTHETIC_SECRET_SENTINEL|desktop_token/); return true;
  });
  contents.emit('did-fail-load', {}, -2, 'SYNTHETIC_SECRET_SENTINEL', 'http://127.0.0.1/?desktop_token=SYNTHETIC_SECRET_SENTINEL', true);
  await rejected;
  assert.equal(contents.listenerCount('did-finish-load'), 0); assert.equal(contents.listenerCount('did-fail-load'), 0);
});

test('unreadable optional connections start the application without a key and expose only safe status', async t => {
  let currentChild;
  const server = await service(t, request => { if (request.url.includes('/shutdown')) { currentChild.exitCode = 0; currentChild.emit('exit', 0, null); } });
  for (const kind of ['json', 'decrypt', 'unavailable', 'wrong-type']) {
    let spawned = 0, api, environment;
    const bytes = kind === 'json' ? '{"api_key":SYNTHETIC_SECRET_SENTINEL}' : kind === 'wrong-type' ? '{"api_key":["SYNTHETIC_SECRET_SENTINEL"]}' : 'synthetic-ciphertext';
    api = launcher(t, { safeStorage: kind === 'decrypt' ? { decryptString() { throw new Error('SYNTHETIC_SECRET_SENTINEL'); } }
      : kind === 'unavailable' ? { isEncryptionAvailable: () => false } : {},
      spawn: (_python, args, options) => {
        spawned++; environment = options.env; const child = fakeChild(10000 + spawned); currentChild = child;
        fs.writeFileSync(args[args.indexOf('--ready-file') + 1], JSON.stringify({ pid: child.pid, port: Number(new URL(server.base).port) }));
        return child;
      } });
    fs.writeFileSync(path.join(api.directory, 'connection.secure'), bytes);
    await api.ready();
    assert.equal(spawned, 1); assert.equal(api.windowCount(), 1);
    assert.equal(environment.DEEPSEEK_API_KEY, '');
    assert.equal(api.dialogs.length, 0);
    const status = api.handlers.get('desktop:connection')();
    assert.equal(status.configured, false);
    assert.equal(status.recovery.has_saved_config, true);
    assert.equal(status.load_error.code, kind === 'unavailable' ? 'connection_storage_unavailable' : 'connection_unreadable');
    assert.doesNotMatch(JSON.stringify(status), /SYNTHETIC_SECRET_SENTINEL|Unexpected token|decrypt/i);
    assert.equal(fs.readFileSync(path.join(api.directory, 'connection.secure'), 'utf8'), bytes);
    const log = path.join(api.directory, 'logs/desktop.log');
    if (fs.existsSync(log)) assert.doesNotMatch(fs.readFileSync(log, 'utf8'), /SYNTHETIC_SECRET_SENTINEL/);
    api.setState({ quitting: true }); await api.stopBackend();
  }
});

test('explicit replacement and clear archive unreadable original bytes before committing the new connection', async t => {
  const server = await service(t);
  for (const clear of [false, true]) {
    const api = launcher(t), bytes = 'unreadable-original-SYNTHETIC_SECRET_SENTINEL';
    fs.writeFileSync(path.join(api.directory, 'connection.secure'), bytes);
    api.setState({ base: server.base });
    const saved = await api.saveConnection(clear ? { clear_key: true } : { model: 'new-model', api_key: 'synthetic-new-key' });
    assert.ok(saved.recovery_archive.startsWith(path.join(fs.realpathSync(api.directory), 'connection-recovery') + path.sep));
    assert.equal(fs.readFileSync(saved.recovery_archive, 'utf8'), bytes);
    assert.equal(api.getConnection().api_key, clear ? '' : 'synthetic-new-key');
    assert.equal(getStatus(api).load_error, null);
    assert.equal(fs.existsSync(path.join(api.directory, 'connection.secure.tmp')), false);
  }
});

test('an archive failure preserves the only unreadable original and never applies runtime settings', async t => {
  const server = await service(t), bytes = 'synthetic-original';
  const api = launcher(t, { fs: { writeFileSync(filename, ...arguments_) {
    if (filename.includes(path.sep + 'connection-recovery' + path.sep)) throw new Error('archive denied');
    return fs.writeFileSync(filename, ...arguments_);
  } } });
  fs.writeFileSync(path.join(api.directory, 'connection.secure'), bytes); api.setState({ base: server.base });
  await assert.rejects(api.saveConnection({ api_key: 'synthetic-new-key' }), /archive denied/);
  assert.equal(fs.readFileSync(path.join(api.directory, 'connection.secure'), 'utf8'), bytes);
  assert.equal(server.requests.length, 0);
  assert.equal(fs.existsSync(path.join(api.directory, 'connection.secure.tmp')), false);
});

test('unavailable encryption cannot replace the preserved unreadable connection', async t => {
  const server = await service(t), api = launcher(t, { safeStorage: { isEncryptionAvailable: () => false } });
  saveFixture(api); api.setState({ base: server.base });
  const before = fs.readFileSync(path.join(api.directory, 'connection.secure'));
  await assert.rejects(api.saveConnection({ clear_key: true }), /系统加密服务不可用/);
  assert.ok(fs.readFileSync(path.join(api.directory, 'connection.secure')).equals(before));
  assert.equal(server.requests.length, 0);
  assert.equal(getStatus(api).configured, false);
});

test('invalid persisted fields and unsafe new fields never expose source fragments or reach the backend', async t => {
  const server = await service(t), api = launcher(t);
  saveFixture(api, { ...old, model: 'model\0SYNTHETIC_SECRET_SENTINEL' }); api.setState({ base: server.base });
  assert.equal(getStatus(api).load_error.code, 'connection_unreadable');
  assert.doesNotMatch(JSON.stringify(getStatus(api)), /SYNTHETIC_SECRET_SENTINEL/);
  await assert.rejects(api.saveConnection({ api_key: 'synthetic\0key' }), /字段格式/);
  assert.equal(server.requests.length, 0);
});

test('a failed reconnect reports an explicit safe runtime state while preserving the old saved key', async t => {
  const server = await service(t, (request, response) => {
    if (request.method === 'PUT') { response.destroy(); return false; }
  });
  const api = launcher(t, { spawn() { throw new Error('SYNTHETIC_SECRET_SENTINEL'); } });
  saveFixture(api); api.setState({ base: server.base, window: api.currentWindow }); api.installIpc();
  await assert.rejects(api.handlers.get('desktop:save-connection')(null, { clear_key: true }), /本地服务未能重新连接/);
  assert.equal(api.getConnection().api_key, old.api_key);
  const status = api.handlers.get('desktop:connection')();
  assert.equal(status.runtime_error.code, 'connection_runtime_unconfirmed');
  assert.equal(api.dialogs.length, 1);
  assert.doesNotMatch(JSON.stringify(api.dialogs) + JSON.stringify(status), /SYNTHETIC_SECRET_SENTINEL/);
});

test('unknown acknowledgement recovery remains serialized with a queued restore', async t => {
  const events = []; let api, child, starts = 0, failPut = true;
  const server = await service(t, (request, response) => {
    if (request.method === 'PUT' && failPut) { failPut = false; events.push('put-lost'); response.destroy(); return false; }
    if (request.url.includes('/shutdown')) { events.push('stop'); child.exitCode = 0; child.emit('exit', 0, null); }
  });
  api = launcher(t, { spawn: (_python, args) => {
    starts++; events.push('start-' + starts); child = fakeChild(12000 + starts);
    fs.writeFileSync(args[args.indexOf('--ready-file') + 1], JSON.stringify({ pid: child.pid, port: Number(new URL(server.base).port) })); return child;
  } });
  saveFixture(api); await api.startBackend(); api.installIpc();
  const saving = api.handlers.get('desktop:save-connection')(null, { clear_key: true });
  const restoring = api.lifecycle.restore(() => { events.push('restore'); });
  await assert.rejects(saving, /原配置已恢复/); await restoring;
  assert.deepEqual(events.slice(0, 5), ['start-1', 'put-lost', 'stop', 'start-2', 'restore']);
  assert.equal(api.getConnection().api_key, old.api_key); await api.stopBackend();
});

test('quit during an uncertain save stops the old process without starting another backend', async t => {
  let api, child, starts = 0, releasePut, sawPut;
  const putStarted = new Promise(resolve => { sawPut = resolve; });
  const server = await service(t, async (request, response) => {
    if (request.method === 'PUT') {
      sawPut(); await new Promise(resolve => { releasePut = resolve; }); response.destroy(); return false;
    }
    if (request.url.includes('/shutdown')) { child.exitCode = 0; child.emit('exit', 0, null); }
  });
  api = launcher(t, { spawn: (_python, args) => {
    starts++; child = fakeChild(13000 + starts);
    fs.writeFileSync(args[args.indexOf('--ready-file') + 1], JSON.stringify({ pid: child.pid, port: Number(new URL(server.base).port) })); return child;
  } });
  saveFixture(api); await api.startBackend(); api.installIpc();
  const saving = api.handlers.get('desktop:save-connection')(null, { clear_key: true });
  await putStarted;
  let prevented = false;
  api.appEvents.get('before-quit')({ preventDefault() { prevented = true; } });
  releasePut();
  await assert.rejects(saving, /应用正在退出/);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prevented, true); assert.equal(starts, 1); assert.equal(child.exitCode, 0);
  assert.equal(api.dialogs.length, 0); assert.equal(api.navigation.length, 0);
  assert.equal(api.getConnection().api_key, old.api_key);
  assert.equal(fs.existsSync(path.join(api.directory, 'connection.secure.tmp')), false);
});

test('actual Python recovers old settings after a 3.6-second lost clear acknowledgement', { timeout: 30000 }, async t => {
  const python = process.env.WORKBENCH_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const directory = fixture(t, 'actual-python-response-lost');
  const children = [], launches = [];
  let api;
  api = launcher(t, { directory, env: { WORKBENCH_PYTHON: python }, spawn: (command, args, options) => {
    const first = children.length === 0;
    const actualArgs = first ? ['-B', path.join(__dirname, 'connection_fixture.py'), '--repo', repo, '--delay-connection', '3.6', ...args.slice(2)] : Array.from(args);
    const child = spawn(command, actualArgs, options);
    children.push(child);
    launches.push({ pid: child.pid, model: options.env.DEEPSEEK_MODEL, configured: Boolean(options.env.DEEPSEEK_API_KEY), key_sha256: sha(options.env.DEEPSEEK_API_KEY) });
    return child;
  } });
  t.after(async () => { for (const child of children) { if (child.exitCode === null && child.signalCode === null) { child.kill(); await new Promise(resolve => child.once('exit', resolve)); } } });
  saveFixture(api); const before = fs.readFileSync(path.join(directory, 'connection.secure'));
  api.setState({ window: api.currentWindow });
  await api.startBackend(); api.installIpc();
  const started = Date.now();
  await assert.rejects(api.handlers.get('desktop:save-connection')(null, { clear_key: true, model: 'cleared-model' }), /原配置已恢复/);
  const capabilities = await getJson(api, '/api/v2/capabilities');
  const applied = JSON.parse(fs.readFileSync(path.join(directory, 'first-put-applied.json'), 'utf8'));
  assert.equal(applied.model, 'cleared-model'); assert.equal(applied.configured, false);
  assert.equal(capabilities.llm.model, old.model); assert.equal(capabilities.llm.configured, true);
  assert.equal(children.length, 2); assert.notEqual(children[0].pid, children[1].pid);
  assert.notEqual(children[0].exitCode, null);
  assert.equal(launches[1].key_sha256, sha(old.api_key));
  assert.ok(fs.readFileSync(path.join(directory, 'connection.secure')).equals(before));
  assert.equal(api.handlers.get('desktop:connection')().runtime_error, null);
  assert.equal(api.navigation.length, 1); assert.match(api.navigation[0], /#settings$/);
  assert.equal(api.dialogs.length, 1); assert.match(api.dialogs[0].message, /原配置已恢复/);
  assert.equal(fs.existsSync(path.join(directory, 'connection.secure.tmp')), false);
  fs.writeFileSync(path.join(directory, 'recovery-evidence.json'), JSON.stringify({ result: 'passed', source_sha256: sha(mainSource), python,
    evidence_type: 'original_main_vm_real_http_actual_source_python', applied, launches, capabilities: capabilities.llm,
    persisted_unchanged: true, old_process_exited: true, native_safe_notice: true, same_window: true, elapsed_ms: Date.now() - started }, null, 2));
  api.setState({ quitting: true }); await api.stopBackend();
});

test('actual Python starts evidence workbench despite an unreadable optional AI configuration', { timeout: 20000 }, async t => {
  const python = process.env.WORKBENCH_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const directory = fixture(t, 'actual-python-unreadable'), children = [];
  const api = launcher(t, { directory, env: { WORKBENCH_PYTHON: python }, spawn: (command, args, options) => {
    const child = spawn(command, args, options); children.push(child); return child;
  } });
  t.after(async () => { for (const child of children) if (child.exitCode === null && child.signalCode === null) { child.kill(); await new Promise(resolve => child.once('exit', resolve)); } });
  const bytes = '{"api_key":SYNTHETIC_SECRET_SENTINEL}';
  fs.writeFileSync(path.join(directory, 'connection.secure'), bytes);
  await api.ready();
  assert.equal(api.windowCount(), 1); assert.equal(api.dialogs.length, 0);
  const health = await getJson(api, '/api/v2/health'), capabilities = await getJson(api, '/api/v2/capabilities');
  assert.equal(health.ok, true); assert.equal(capabilities.llm.configured, false);
  assert.equal(fs.readFileSync(path.join(directory, 'connection.secure'), 'utf8'), bytes);
  const status = api.handlers.get('desktop:connection')(); assert.equal(status.load_error.code, 'connection_unreadable');
  fs.writeFileSync(path.join(directory, 'startup-evidence.json'), JSON.stringify({ result: 'passed', source_sha256: sha(mainSource), python,
    evidence_type: 'original_main_startup_vm_actual_source_python', backend_spawn_count: children.length, application_window_count: api.windowCount(),
    health, capabilities: capabilities.llm, safe_status: status, preserved_original: true }, null, 2));
  api.setState({ quitting: true }); await api.stopBackend();
});
