'use strict';
// Execute the real menu callbacks with fake Electron dialogs and no backend,
// profile, filesystem reads, process launches, or native window.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'main.cjs'), 'utf8');

async function fixture({ responses = [], save = {}, fail = false, packaged = true } = {}) {
  const calls = [], messages = [], errors = [], opened = [];
  let menu;
  const electron = {
    app: { isPackaged: packaged, setName() {}, requestSingleInstanceLock: () => false, quit() {}, getPath: () => 'C:\\isolated-documents' },
    BrowserWindow: class {
      constructor() { this.webContents = { on() {}, setWindowOpenHandler() {} }; }
      on() {} once() {} async loadURL() {}
    },
    dialog: { async showMessageBox(_window, options) { messages.push(options); return { response: responses.shift() ?? 0 }; },
      async showSaveDialog() { return save; }, showErrorBox(...args) { errors.push(args); } },
    shell: { async openPath(file) { opened.push(file); return ''; } },
    Menu: { buildFromTemplate: value => value, setApplicationMenu: value => { menu = value; } },
    ipcMain: {}, safeStorage: {}, session: { defaultSession: { setPermissionRequestHandler() {} } }
  };
  const forbidden = () => { throw new Error('Isolated menu test must not access profiles or launch processes'); };
  const context = vm.createContext({ __dirname: 'C:\\isolated-app\\desktop', Buffer, URL, setTimeout, clearTimeout,
    process: { env: {}, platform: 'win32', resourcesPath: 'C:\\isolated-package\\resources' },
    require(name) {
      if (name === 'electron') return electron;
      if (name === 'node:path') return path;
      if (name === 'node:fs') return new Proxy({}, { get: () => forbidden });
      if (name === 'node:child_process') return { spawn: forbidden };
      if (name === 'node:http') return { request: forbidden };
      if (name === 'node:crypto') return {};
      if (name === './restore.cjs') return { createLifecycleCoordinator: () => ({ isRestoring: () => false }) };
      throw new Error('Unexpected dependency ' + name);
    },
    syntheticApi: async (...args) => { calls.push(JSON.parse(JSON.stringify(args))); if (fail) throw new Error('synthetic confidential backend error'); return {}; }
  });
  vm.runInContext(source + '\nrequestApi = syntheticApi; globalThis.openMenu = showWindow;', context);
  await context.openMenu();
  return { calls, messages, errors, opened, submenu: menu.find(item => item.label === '外部 Agent').submenu };
}

test('menu discovery opens packaged installation instructions only', async () => {
  const app = await fixture();
  await app.submenu[0].click();
  assert.equal(app.opened[0], path.join('C:\\isolated-package\\resources', 'backend', 'app', 'workbench', 'agent_assets', 'INSTALL.md'));
  assert.equal(app.calls.length, 0);
});

test('canceling authorization or save performs no grant mutation', async () => {
  const cancelled = await fixture({ responses: [0] });
  await cancelled.submenu[1].click();
  assert.equal(cancelled.calls.length, 0);
  assert.equal(cancelled.messages[0].defaultId, 0);
  const unsaved = await fixture({ responses: [2], save: { canceled: true } });
  await unsaved.submenu[1].click();
  assert.equal(unsaved.calls.length, 0);
});

test('each selected scope creates independent grant through existing desktop API', async () => {
  for (const [response, scopes] of [[1, ['read']], [2, ['read', 'run']], [3, ['read', 'run', 'cleanup']], [4, ['read', 'review']], [5, ['read', 'run', 'cleanup', 'review']]]) {
    const app = await fixture({ responses: [response], save: { filePath: 'C:\\isolated-documents\\chosen.json' } });
    await app.submenu[1].click();
    assert.deepEqual(app.calls, [['/api/v2/agent/access', 'POST', { scopes, credential_path: 'C:\\isolated-documents\\chosen.json', ttl_seconds: 28800 }]]);
    assert.equal(app.errors.length, 0);
    assert.match(app.messages[1].detail, /chosen.json/);
    assert.doesNotMatch(JSON.stringify(app.messages), /agent_token|desktop_token/);
  }
});

test('grant failure emits fixed message without confidential backend exception', async () => {
  const app = await fixture({ responses: [2], save: { filePath: 'C:\\isolated-documents\\chosen.json' }, fail: true });
  await app.submenu[1].click();
  assert.equal(app.errors.length, 1);
  assert.doesNotMatch(JSON.stringify(app.errors), /confidential/);
});

test('revoke requires selected confirmation and sends no cancellation or deletion', async () => {
  const no = await fixture({ responses: [0] });
  await no.submenu[2].click();
  assert.equal(no.calls.length, 0);
  const yes = await fixture({ responses: [1] });
  await yes.submenu[2].click();
  assert.deepEqual(yes.calls, [['/api/v2/agent/access', 'DELETE']]);
  assert.equal(yes.errors.length, 0);
});
