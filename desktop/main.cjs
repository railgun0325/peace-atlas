'use strict';
const { app, BrowserWindow, dialog, shell, Menu, ipcMain, safeStorage, session } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const restoration = require('./restore.cjs');

app.setName('PEACE ATLAS');
if (process.env.WORKBENCH_SMOKE_RESTORE === '1' && process.env.WORKBENCH_SMOKE_EXIT !== '1') throw new Error('Restore acceptance requires explicit isolated WORKBENCH_SMOKE_EXIT=1.');
if (process.env.WORKBENCH_SMOKE_CONNECTION_RECOVERY === '1') {
  if (process.env.WORKBENCH_SMOKE_EXIT !== '1') throw new Error('Connection recovery acceptance requires explicit isolated WORKBENCH_SMOKE_EXIT=1.');
  if (!/^[a-f0-9]{64}$/.test(process.env.WORKBENCH_SMOKE_CONNECTION_EXPECTED_SHA256 || '')) throw new Error('Connection recovery acceptance requires the explicit synthetic fixture SHA-256.');
}
// An acceptance launch owns its isolated profile and can run beside the user's app.
if (process.env.WORKBENCH_SMOKE_EXIT === '1') {
  if (!process.env.WORKBENCH_DATA_DIR) throw new Error('Release smoke requires an explicit isolated WORKBENCH_DATA_DIR.');
  app.setPath('userData', path.resolve(process.env.WORKBENCH_DATA_DIR));
}
let window = null, backend = null, base = '', token = '', quitting = false, quitComplete = false, reconnecting = false;
let readyFile = '', logFile = '', dataDir = '';
const lifecycle = restoration.createLifecycleCoordinator();
let smokeRestore = null, smokeRestoreResult = null, pendingRecovery = null;
let connectionUnconfirmed = false;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const log = text => {
  if (!logFile) return;
  try { fs.appendFileSync(logFile, `${new Date().toISOString()} ${text}\n`, 'utf8'); }
  catch (_) { /* An unavailable log directory must not crash lifecycle cleanup. */ }
};
const connectionFile = () => path.join(dataDir, 'connection.secure');
const backendRunning = child => Boolean(child?.pid && child.exitCode === null && child.signalCode === null);

function waitForBackendExit(child, timeout) {
  if (!backendRunning(child)) return Promise.resolve(true);
  return new Promise(resolve => {
    const finish = exited => {
      clearTimeout(timer);
      child.removeListener('exit', onExit);
      resolve(exited);
    };
    const onExit = () => finish(true);
    const timer = setTimeout(() => finish(false), timeout);
    child.once('exit', onExit);
  });
}

function readConnection() {
  const defaults = { base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: '' };
  if (!fs.existsSync(connectionFile())) return { settings: defaults, load_error: null, recovery: null };
  const unavailable = !safeStorage.isEncryptionAvailable();
  const failed = () => ({ settings: defaults,
    load_error: { code: unavailable ? 'connection_storage_unavailable' : 'connection_unreadable',
      message: unavailable ? '系统加密服务暂时不可用，已保留原 AI 配置。本次可继续整理证据；加密服务恢复后可重新保存连接。' : '已保存的 AI 配置无法读取，原文件已保留。本次可继续整理证据；重新保存连接时会先归档原文件。' },
    recovery: { needed: true, has_saved_config: true } });
  if (unavailable) return failed();
  try {
    const value = JSON.parse(safeStorage.decryptString(fs.readFileSync(connectionFile())));
    if (!value || typeof value !== 'object' || Array.isArray(value)) return failed();
    const settings = { ...defaults, ...value };
    validateConnection(settings);
    return { settings: { base_url: settings.base_url, model: settings.model, api_key: settings.api_key }, load_error: null, recovery: null };
  } catch (_) {
    // Parser/keychain errors can include decrypted fragments. Only fixed safe
    // status fields may cross IPC or reach the user and lifecycle logs.
    return failed();
  }
}

function getConnection() { return readConnection().settings; }

function validateConnection(value) {
  if (['base_url', 'model', 'api_key'].some(name => typeof value[name] !== 'string' || /[\x00-\x1f\x7f]/.test(value[name]) || Buffer.from(value[name], 'utf8').toString('utf8') !== value[name])) throw new Error('AI 连接字段格式不正确，请重新填写。');
  let address;
  try { address = new URL(value.base_url); } catch (_) { throw new Error('AI 服务地址必须是有效的 HTTPS 地址。'); }
  if (address.protocol !== 'https:' || !address.hostname || address.username || address.password || value.base_url.length > 2000) throw new Error('AI 服务地址必须是 HTTPS 地址且不能含用户名或密码。');
  if (!value.model.trim() || value.model.length > 160 || value.api_key.length > 4096) throw new Error('模型名称或密钥格式不正确。');
}

function requestApi(route, method = 'GET', body) {
  return new Promise((resolve, reject) => {
    let deadline;
    const fail = error => { clearTimeout(deadline); reject(error); };
    const request = http.request(base + route, { method, headers: { 'X-Workbench-Token': token, 'Content-Type': 'application/json' }, timeout: 3000 }, response => {
      let content = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { content += chunk; });
      response.on('error', fail);
      response.on('end', () => {
        clearTimeout(deadline);
        try {
          const data = JSON.parse(content || '{}');
          if (response.statusCode < 400) resolve(data);
          else { const error = new Error(data.error?.message || `服务返回 ${response.statusCode}`); error.statusCode = response.statusCode; reject(error); }
        }
        catch (_) { const error = new Error('本地服务返回了无法读取的响应。'); error.statusCode = response.statusCode; reject(error); }
      });
    });
    // Socket timeout measures inactivity. An unfinished response that keeps
    // sending bytes must not extend the desktop's shutdown deadline.
    deadline = setTimeout(() => request.destroy(new Error('服务响应超时')), 3000);
    request.on('timeout', () => request.destroy(new Error('服务响应超时')));
    request.on('error', fail);
    if (body) request.write(JSON.stringify(body));
    request.end();
  });
}

function agentAssetPath(name) {
  const root = app.isPackaged ? path.join(process.resourcesPath, 'backend') : path.join(__dirname, '..', 'analysis-worker');
  return path.join(root, 'app', 'workbench', 'agent_assets', name);
}

async function showAgentInstructions() {
  const error = await shell.openPath(agentAssetPath('INSTALL.md'));
  if (error) dialog.showErrorBox('接入说明暂时无法打开', '请在应用安装目录 resources/backend/app/workbench/agent_assets 查看 INSTALL.md。');
}

async function authorizeAgentAccess() {
  const choice = await dialog.showMessageBox(window, { type: 'question', title: '授权本机外部 Agent',
    message: '选择本次授权范围',
    detail: '授权有效期为 8 小时，应用重启或撤销后失效。连接文件只供当前系统用户读取；持有它的本机 Agent 可按选定范围读取资料或操作任务。研究与报告可能读取公开网络、调用已配置模型并产生费用。清理仅移入回收站或恢复。请选择你信任的本机 Agent，不要分享连接文件内容。',
    buttons: ['取消', '仅只读', '采集、研究与报告', '采集、研究、报告与可恢复清理', '审核、类型判定与账号核验', '全部业务操作'], defaultId: 0, cancelId: 0, noLink: true });
  if (![1, 2, 3, 4, 5].includes(choice.response)) return;
  const selected = await dialog.showSaveDialog(window, { title: '保存新的 Agent 授权连接',
    defaultPath: path.join(app.getPath('documents'), `PEACE-ATLAS-agent-${Date.now()}.json`),
    filters: [{ name: 'Agent 授权连接', extensions: ['json'] }] });
  if (selected.canceled || !selected.filePath) return;
  const scopes = { 1: ['read'], 2: ['read', 'run'], 3: ['read', 'run', 'cleanup'], 4: ['read', 'review'], 5: ['read', 'run', 'cleanup', 'review'] }[choice.response];
  try {
    await requestApi('/api/v2/agent/access', 'POST', { scopes, credential_path: selected.filePath, ttl_seconds: 28800 });
    await dialog.showMessageBox(window, { type: 'info', title: 'Agent 授权已导出', message: '连接文件已安全保存',
      detail: `向你信任的本机 Agent 提供这个文件的路径即可，勿复制文件内容到聊天或仓库。\n\n${selected.filePath}\n\n使用“外部 Agent → 接入与 Skill 安装说明”完成 Skill 安装，再先查询资料。研究、报告和清理须先核对预览。`, buttons: ['知道了'] });
  } catch (_) {
    dialog.showErrorBox('Agent 授权未导出', '请检查本地服务可用，并选择已有文件夹中的新 JSON 文件。已有文件不会被覆盖；权限设置失败时不会写出凭据。');
  }
}

async function revokeAgentAccess() {
  const choice = await dialog.showMessageBox(window, { type: 'question', title: '撤销外部 Agent 授权',
    message: '使全部已导出的 Agent 连接立即失效？',
    detail: '正在运行的业务任务继续执行，资料和报告保留。旧连接文件保留但不可再使用；需要时可重新导出授权连接。',
    buttons: ['取消', '撤销全部授权'], defaultId: 0, cancelId: 0, noLink: true });
  if (choice.response !== 1) return;
  try {
    await requestApi('/api/v2/agent/access', 'DELETE');
    await dialog.showMessageBox(window, { type: 'info', title: 'Agent 授权已撤销', message: '全部 Agent 连接已失效', buttons: ['知道了'] });
  } catch (_) { dialog.showErrorBox('撤销暂未完成', '本地服务未确认撤销结果，请恢复服务后重新操作。'); }
}

function backendRuntime() {
  const resources = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..');
  const python = process.env.WORKBENCH_PYTHON || (app.isPackaged
    ? path.join(resources, 'python', process.platform === 'win32' ? 'python.exe' : 'bin/python3')
    : (process.platform === 'win32' ? 'python' : 'python3'));
  const entry = app.isPackaged ? path.join(resources, 'backend', 'backend_entry.py') : path.join(__dirname, 'backend_entry.py');
  return { resources, python, entry };
}

async function startBackend(onSpawn) {
  if (backendRunning(backend)) throw new Error('本地服务进程仍在运行，不能重复启动。');
  const { resources, python, entry } = backendRuntime();
  token = crypto.randomBytes(32).toString('hex');
  readyFile = path.join(dataDir, `ready-${process.pid}.json`);
  // PIDs can be reused after a crash; a prior readiness file is never ours.
  try { fs.unlinkSync(readyFile); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const connection = getConnection();
  const environment = { ...process.env, WORKBENCH_SESSION_TOKEN: token, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1', DEEPSEEK_API_KEY: connection.api_key, DEEPSEEK_BASE_URL: connection.base_url, DEEPSEEK_MODEL: connection.model };
  environment.WORKBENCH_DATA_DIR = dataDir;
  environment.WORKBENCH_SOCIAL_DIR = path.join(dataDir, 'social');
  // The research sidecar and its public SDK child reuse our bundled Electron.
  // Each child sets ELECTRON_RUN_AS_NODE; no separately installed Node is needed.
  environment.WORKBENCH_HARNESS_NODE = process.execPath;
  if (app.isPackaged) {
    environment.PLAYWRIGHT_BROWSERS_PATH = path.join(resources, 'browsers');
    delete environment.PLAYWRIGHT_NODEJS_PATH;
  }
  delete environment.PYTHONHOME;
  delete environment.PYTHONPATH;
  // Local app owns a local DB; development/terminal database env cannot redirect it.
  delete environment.WORKBENCH_DATABASE_URL;
  backend = spawn(python, ['-B', entry, '--data-dir', dataDir, '--ready-file', readyFile, '--watch-parent-stdin'], { env: environment, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
  const child = backend;
  let startupError = null;
  backend.on('error', error => { startupError = error; log(`Backend launch error: ${error.message}`); });
  backend.stdout.on('data', data => log(data.toString().trim()));
  backend.stderr.on('data', data => log(data.toString().trim()));
  backend.on('exit', (code, signal) => {
    log(`Backend exited code=${code} signal=${signal}`);
    if (!quitting && !reconnecting && !lifecycle.isRestoring() && child === backend && base && window && !window.isDestroyed()) {
      window.webContents.send('backend-stopped');
      dialog.showMessageBox(window, { type: 'error', title: '服务已停止', message: '本地数据服务意外停止。数据仍保存在本机。', detail: '请关闭并重新打开应用；未完成的任务可以在任务中心重试。', buttons: ['知道了', '打开日志'] }).then(r => { if (r.response === 1) shell.openPath(path.join(dataDir, 'logs')); });
    }
  });
  if (onSpawn && backend.pid) onSpawn(backend.pid);
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (startupError) throw startupError;
    if (backend.exitCode !== null || backend.signalCode !== null) throw new Error('本地服务未能启动，请查看日志。');
    if (fs.existsSync(readyFile)) {
      const ready = JSON.parse(fs.readFileSync(readyFile, 'utf8'));
      if (ready.pid === backend.pid && Number.isInteger(ready.port) && ready.port > 0 && ready.port <= 65535) {
        base = `http://127.0.0.1:${ready.port}`;
        try { const health = await requestApi('/api/v2/health'); if (health.ok) { connectionUnconfirmed = false; return; } } catch (_) { /* wait for schema startup */ }
      }
    }
    await delay(250);
  }
  throw new Error('本地服务启动超时。');
}

async function stopBackend() {
  const child = backend;
  try {
    if (!backendRunning(child)) return;
    if (base) {
      try { await requestApi('/api/v2/lifecycle/shutdown', 'POST'); } catch (_) { /* fallback below */ }
      // Browser cleanup and a commit already in progress must be allowed to
      // complete. The old three-second deadline killed healthy shutdowns.
      if (await waitForBackendExit(child, 30_000)) return;
    }
    if (backendRunning(child)) {
      log('Backend did not exit gracefully; requesting termination');
      child.kill();
      if (!await waitForBackendExit(child, 5000) && backendRunning(child)) {
        log('Backend ignored termination; forcing exit');
        child.kill('SIGKILL');
        await waitForBackendExit(child, 5000);
      }
    }
    if (backendRunning(child)) throw new Error('本地服务进程尚未退出，数据库未替换。请保留应用和日志后重试。');
  } finally {
    if (!backendRunning(child) && readyFile) { try { fs.unlinkSync(readyFile); } catch (_) {} }
  }
}

function trustedRestoreCaller(event) {
  if (!window || window.isDestroyed() || event?.sender !== window.webContents || !event.senderFrame || event.senderFrame !== window.webContents.mainFrame) return false;
  try { return new URL(event.senderFrame.url).origin === base; } catch (_) { return false; }
}

async function navigateAfterRestore() {
  if (!window || window.isDestroyed()) throw new Error('应用窗口已关闭。');
  await window.loadURL(base + '/?desktop_token=' + token + '#settings');
}

async function restoreResearchBackup() {
  const { python, entry } = backendRuntime();
  const nativeSmoke = smokeRestore && process.env.WORKBENCH_SMOKE_EXIT === '1' && process.env.WORKBENCH_SMOKE_RESTORE === '1';
  const result = await restoration.restoreBackup({ dataDir, log, quitting: () => quitting,
    chooseSource: async () => {
      if (nativeSmoke) { smokeRestore.dialogs.push('open'); return smokeRestore.source; }
      const chosen = await dialog.showOpenDialog(window, { title: '选择研究数据备份', properties: ['openFile'], filters: [{ name: 'SQLite 研究备份', extensions: ['sqlite3', 'sqlite', 'db'] }] });
      return chosen.canceled ? null : chosen.filePaths[0];
    },
    prepare: (source, output) => {
      const environment = { ...process.env, PYTHONUTF8: '1', PYTHONDONTWRITEBYTECODE: '1' };
      for (const name of ['PYTHONHOME', 'PYTHONPATH', 'DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_MODEL', 'WORKBENCH_DATABASE_URL', 'WORKBENCH_DB_PATH', 'WORKBENCH_SESSION_TOKEN']) delete environment[name];
      return restoration.runPrivateJson({ spawn, python, entry, args: ['--prepare-restore', source, '--restore-output', output], env: environment });
    },
    confirm: async summary => {
      const counts = summary.counts;
      const detail = `候选文件：${summary.backup_file_name}\n资料 ${counts.items} 条；账号 ${counts.accounts} 个；报告 ${counts.reports} 份；研究 ${counts.research_runs} 个；任务 ${counts.jobs} 个；草稿 ${counts.drafts} 份。\n\n恢复将停止当前任务并替换研究数据库。旧数据库及 WAL/SHM 会自动保留在本机 restores 文件夹。备份源文件不会改动；AI 配置与社交登录不受影响。${summary.interrupted_jobs_count} 个未完成任务将标为已中断，需要在任务中心手动重试。`;
      if (nativeSmoke) { smokeRestore.dialogs.push('confirm'); smokeRestore.confirmation = { message: '恢复这份研究备份？', detail, summary }; return true; }
      const confirmed = await dialog.showMessageBox(window, { type: 'warning', title: '确认恢复研究备份', message: '恢复这份研究备份？', detail, buttons: ['取消', '停止任务并恢复'], defaultId: 0, cancelId: 0, noLink: true });
      return confirmed.response === 1;
    },
    stop: stopBackend, terminal: () => !backendRunning(backend), start: startBackend, navigate: navigateAfterRestore,
  });
  if (nativeSmoke) smokeRestoreResult = result;
  else if (result.restored && !quitting) await dialog.showMessageBox(window, { type: 'info', title: '研究备份已恢复', message: '研究数据已恢复，未完成任务需要手动重试。', detail: `旧库保留位置：${result.archive_path}`, buttons: ['知道了'] });
  return result;
}

function external(url) {
  try {
    const parsed = new URL(url);
    if (['https:', 'http:'].includes(parsed.protocol)) shell.openExternal(parsed.href).catch(() => log('External browser could not open the requested link'));
  } catch (_) {}
}

function archiveUnreadableConnection() {
  const root = fs.realpathSync(dataDir), directory = path.join(root, 'connection-recovery');
  fs.mkdirSync(directory, { recursive: true });
  if (fs.realpathSync(directory) !== directory) throw new Error('AI 配置归档目录无效，原配置未替换。');
  const filename = path.join(directory, 'connection-' + new Date().toISOString().replace(/[:.]/g, '-') + '-' + crypto.randomBytes(6).toString('hex') + '.secure');
  const bytes = fs.readFileSync(connectionFile());
  fs.writeFileSync(filename, bytes, { flag: 'wx', mode: 0o600 });
  if (!fs.readFileSync(filename).equals(bytes)) throw new Error('原 AI 配置归档未能确认，原配置未替换。');
  return filename;
}

async function reconnectSavedConnection() {
  // Stopping and confirming process exit cancels any late first PUT. The new
  // backend reads only the unchanged persisted settings, including its old key.
  reconnecting = true;
  try {
    await stopBackend();
    base = '';
    if (quitting) return false;
    await startBackend();
    if (window && !window.isDestroyed()) { await navigateAfterRestore(); return true; }
    return false;
  } finally { reconnecting = false; }
}

async function recoverConnectionFailure() {
  let reloaded = false, failed = false, message;
  connectionUnconfirmed = true;
  try {
    reloaded = await reconnectSavedConnection();
    message = quitting ? 'AI 连接未保存，原配置保持不变。应用正在退出。' : 'AI 连接未保存，原配置已恢复。请重试。';
  } catch (_) {
    failed = true;
    // A restart failure must not look like a healthy unchanged connection.
    message = 'AI 连接未保存，原文件保持不变，但本地服务未能重新连接。请关闭并重新打开应用后重试。';
    log('Connection save failed; persisted configuration preserved; backend reconnection could not be confirmed');
  }
  if (!quitting && window && !window.isDestroyed() && (reloaded || failed)) {
    try { await dialog.showMessageBox(window, { type: 'error', title: 'AI 连接未保存', message, buttons: ['知道了'], noLink: true }); } catch (_) {}
  }
  throw new Error(message);
}

async function saveConnection(settings) {
  if (!settings || typeof settings !== 'object') throw new Error('无效配置。');
  const loaded = readConnection(), existing = loaded.settings;
  const value = { base_url: String(settings.base_url || existing.base_url).trim(), model: String(settings.model || existing.model).trim(), api_key: settings.clear_key ? '' : (String(settings.api_key || '').trim() || existing.api_key) };
  validateConnection(value);
  if (!safeStorage.isEncryptionAvailable()) throw new Error('系统加密服务不可用，未保存密钥。');
  const temporary = connectionFile() + '.tmp';
  let recoveryArchive = null;
  try {
    // Stage encrypted bytes before changing the running backend. A full disk
    // or unavailable keychain must not turn a failed save into an applied one.
    fs.writeFileSync(temporary, safeStorage.encryptString(JSON.stringify(value)), { mode: 0o600 });
    if (loaded.recovery) recoveryArchive = archiveUnreadableConnection();
    try { await requestApi('/api/v2/connection', 'PUT', value); }
    catch (error) {
      // Auth/validation/busy responses are known rejections before mutation.
      // Transport/server failures have no such acknowledgement and require a
      // process boundary before trusting the original runtime settings again.
      if ([401, 403, 409, 422].includes(error.statusCode)) throw error;
      return await recoverConnectionFailure();
    }
    try { fs.renameSync(temporary, connectionFile()); }
    catch (error) {
      try { await requestApi('/api/v2/connection', 'PUT', existing); }
      catch (_) { return await recoverConnectionFailure(); }
      throw error;
    }
    connectionUnconfirmed = false;
    return { configured: Boolean(value.api_key), model: value.model, base_url: value.base_url, recovery_archive: recoveryArchive };
  } finally {
    try { fs.unlinkSync(temporary); } catch (_) {}
  }
}

function installIpc() {
  ipcMain.handle('desktop:info', () => ({ version: app.getVersion(), platform: process.platform, data_dir: dataDir, log_dir: path.join(dataDir, 'logs') }));
  ipcMain.handle('desktop:connection', () => { const loaded = readConnection(), settings = loaded.settings; return { base_url: settings.base_url, model: settings.model, configured: Boolean(settings.api_key), secure_storage: safeStorage.isEncryptionAvailable(), load_error: loaded.load_error, recovery: loaded.recovery,
    runtime_error: connectionUnconfirmed ? { code: 'connection_runtime_unconfirmed', message: '本地服务的 AI 运行配置暂时无法确认。请关闭并重新打开应用后重试；原保存文件保持不变。' } : null }; });
  ipcMain.handle('desktop:save-connection', (_, settings) => {
    // Preserve omitted keys and one on-disk transaction across rapid submits.
    return lifecycle.mutation(() => saveConnection(settings));
  });
  ipcMain.handle('desktop:restore-backup', (event, ...arguments_) => {
    if (arguments_.length || !trustedRestoreCaller(event)) throw new Error('恢复只允许通过本应用的设置页选择备份，不能传入文件路径或其他参数。');
    return lifecycle.restore(async () => {
      try { return await restoreResearchBackup(); }
      catch (error) {
        if (smokeRestore) smokeRestoreResult = { error: error.message };
        log('Restore failed: ' + error.message);
        // Rollback may have replaced the renderer to reconnect the original
        // database. Its old IPC promise cannot provide a visible error there,
        // so the trusted main-process request owns the failure notification.
        if (!quitting && process.env.WORKBENCH_SMOKE_EXIT !== '1' && window && !window.isDestroyed()) {
          try { await dialog.showMessageBox(window, { type: 'error', title: '研究备份恢复失败', message: '研究备份未能恢复，请查看下方处理结果。', detail: error.message, buttons: ['知道了'], noLink: true }); }
          catch (notification) { log('Restore error dialog unavailable: ' + notification.message); }
        }
        throw error;
      }
    });
  });
  ipcMain.handle('desktop:open-data', () => shell.openPath(dataDir));
  ipcMain.handle('desktop:open-logs', () => shell.openPath(path.join(dataDir, 'logs')));
}

function validateConnectionSmokeFixture() {
  if (process.env.WORKBENCH_SMOKE_CONNECTION_RECOVERY !== '1') return;
  const bytes = fs.readFileSync(connectionFile());
  const expected = process.env.WORKBENCH_SMOKE_CONNECTION_EXPECTED_SHA256;
  if (crypto.createHash('sha256').update(bytes).digest('hex') !== expected || !readConnection().load_error) throw new Error('Connection recovery acceptance requires its exact unreadable synthetic fixture.');
}

async function smokeNavigate(route, readyExpression) {
  const linkSelector = '#navigation a[data-route="' + route + '"]';
  for (let attempt = 0; attempt < 150; attempt++) {
    if (await window.webContents.executeJavaScript(`!!document.querySelector(${JSON.stringify(linkSelector)})`)) break;
    if (attempt === 149) throw new Error('Native connection acceptance navigation did not initialize.');
    await delay(100);
  }
  // The native hash router is exercised through the same links as the user.
  // A fragment-only loadURL just after token authentication can fail in Electron.
  await window.webContents.executeJavaScript(`document.querySelector(${JSON.stringify(linkSelector)}).click()`);
  for (let attempt = 0; attempt < 150; attempt++) {
    if (await window.webContents.executeJavaScript(`location.hash===${JSON.stringify('#' + route)} && !document.getElementById('main').hasAttribute('aria-busy') && (${readyExpression})`)) return;
    await delay(100);
  }
  throw new Error('Native connection acceptance navigation did not render its requested page.');
}

function smokeReloadPage() {
  return new Promise((resolve, reject) => {
    const contents = window.webContents;
    const finish = error => {
      clearTimeout(timer);
      contents.removeListener('did-finish-load', loaded);
      contents.removeListener('did-fail-load', failed);
      error ? reject(error) : resolve();
    };
    const loaded = () => finish();
    const failed = (_event, _code, _description, _url, mainFrame) => { if (mainFrame) finish(new Error('Native connection acceptance independent reload failed.')); };
    const timer = setTimeout(() => finish(new Error('Native connection acceptance independent reload timed out.')), 15000);
    contents.once('did-finish-load', loaded);
    contents.on('did-fail-load', failed);
    // Waiting for the real navigation event prevents old DOM from being counted
    // as an independent refresh. Diagnostics never include the URL/session token.
    try { contents.reload(); } catch (_) { finish(new Error('Native connection acceptance independent reload could not start.')); }
  });
}

async function connectionRecoveryAcceptance(checks, rendererErrors) {
  if (process.env.WORKBENCH_SMOKE_EXIT !== '1' || !process.env.WORKBENCH_DATA_DIR) throw new Error('Connection recovery acceptance must own an explicit isolated profile.');
  validateConnectionSmokeFixture();
  const original = fs.readFileSync(connectionFile()), originalHash = crypto.createHash('sha256').update(original).digest('hex');
  const archivedDirectory = path.join(dataDir, 'connection-recovery');
  if (fs.existsSync(archivedDirectory) && fs.readdirSync(archivedDirectory).length) throw new Error('Connection recovery acceptance requires a fresh archive directory.');
  const evaluate = expression => window.webContents.executeJavaScript(expression);
  async function uiWait(expression) {
    for (let attempt = 0; attempt < 150; attempt++) { if (await evaluate(expression)) return; await delay(100); }
    throw new Error('Native connection recovery UI did not reach its expected state.');
  }
  const capabilitiesBefore = await requestApi('/api/v2/capabilities');
  const researchBefore = await requestApi('/api/v2/research?page_size=1');
  const jobsBefore = await requestApi('/api/v2/jobs?page_size=1');
  if (capabilitiesBefore.llm.configured || !capabilitiesBefore.research.available || !capabilitiesBefore.backup.available || !checks.backup?.sqlite_header) throw new Error('Unreadable optional AI settings blocked evidence or backup capabilities.');
  await smokeNavigate('network', "document.querySelectorAll('.workspace-choice').length===2 && !document.querySelector('.page-failure')");
  const researchUi = await evaluate("({choices:document.querySelectorAll('.workspace-choice').length,create_available:[...document.querySelectorAll('button')].some(button=>button.textContent.trim()==='新建研究或采集'&&!button.disabled),page_error:!!document.querySelector('.page-failure')})");
  if (!researchUi.create_available) throw new Error('Native evidence research controls are not available with unreadable optional AI settings.');
  await delay(250);
  fs.writeFileSync(path.join(dataDir, 'desktop-connection-research-available.png'), (await window.webContents.capturePage()).toPNG());
  const warning = "document.querySelector('[data-testid=\"connection-recovery-warning\"]')";
  const saveButton = "document.querySelector('[data-action=\"save-connection\"]')";
  await smokeNavigate('settings', `${warning} && ${saveButton}`);
  await uiWait(`${warning} && ${saveButton} && !${saveButton}.disabled && document.querySelector('[name=api_key]')?.value===''`);
  const before = await evaluate(`({warning_visible:!!${warning}&&${warning}.getBoundingClientRect().height>0,key_empty:document.querySelector('[name=api_key]').value==='',save_enabled:!${saveButton}.disabled,backup_enabled:[...document.querySelectorAll('section[aria-label="研究数据备份"] button')].some(button=>button.textContent.trim()==='备份研究数据'&&!button.disabled),page_error:!!document.querySelector('.page-failure')})`);
  const ipcBefore = await evaluate('window.desktop.getConnection()');
  if (!before.warning_visible || !before.key_empty || !before.backup_enabled || before.page_error || ipcBefore.configured || !ipcBefore.load_error || !ipcBefore.recovery?.has_saved_config) throw new Error('Native settings did not show the safe unreadable connection state.');
  await evaluate(`${warning}.scrollIntoView({block:'center'})`); await delay(200);
  fs.writeFileSync(path.join(dataDir, 'desktop-connection-unreadable.png'), (await window.webContents.capturePage()).toPNG());
  const model = 'synthetic-native-recovery-' + crypto.randomBytes(6).toString('hex');
  await evaluate(`(() => { const form=${saveButton}.closest('form');form.elements.model.value=${JSON.stringify(model)};form.elements.model.dispatchEvent(new Event('input',{bubbles:true}));form.elements.api_key.value='';form.elements.api_key.dispatchEvent(new Event('input',{bubbles:true}));form.requestSubmit(); })()`);
  await uiWait(`!${warning} && ${saveButton} && !${saveButton}.disabled && document.querySelector('[name=model]')?.value===${JSON.stringify(model)} && !document.getElementById('main').hasAttribute('aria-busy')`);
  const formError = await evaluate(`!!${saveButton}.closest('form').querySelector('.form-error .error-box')`);
  if (formError) throw new Error('Native connection form reported a save error.');
  const loaded = readConnection(), capabilitiesAfter = await requestApi('/api/v2/capabilities');
  if (loaded.load_error || loaded.settings.model !== model || loaded.settings.api_key !== '' || capabilitiesAfter.llm.model !== model || capabilitiesAfter.llm.configured) throw new Error('Native form did not persist and apply its key-free synthetic connection.');
  const archives = fs.readdirSync(archivedDirectory).filter(name => name.startsWith('connection-') && name.endsWith('.secure'));
  if (archives.length !== 1) throw new Error('Native connection recovery did not preserve exactly one original archive.');
  const archive = path.join(archivedDirectory, archives[0]);
  if (!fs.readFileSync(archive).equals(original)) throw new Error('Native connection recovery archive differs from the synthetic original.');
  const encrypted = fs.readFileSync(connectionFile());
  const decrypted = JSON.parse(safeStorage.decryptString(encrypted));
  if (decrypted.model !== model || decrypted.api_key !== '') throw new Error('Native secureStorage round-trip did not match the key-free saved form.');
  await smokeReloadPage();
  await uiWait(`!${warning} && ${saveButton} && document.querySelector('[name=model]')?.value===${JSON.stringify(model)} && document.querySelector('[name=api_key]')?.value===''`);
  const after = await evaluate(`({warning_absent:!${warning},key_empty:document.querySelector('[name=api_key]').value==='',model:document.querySelector('[name=model]').value,page_error:!!document.querySelector('.page-failure'),form_error:!!${saveButton}.closest('form').querySelector('.form-error .error-box')})`);
  const ipcAfter = await evaluate('window.desktop.getConnection()');
  if (!after.warning_absent || !after.key_empty || after.page_error || after.form_error || ipcAfter.load_error || ipcAfter.runtime_error || ipcAfter.configured || ipcAfter.model !== model) throw new Error('Native settings did not remain clean after an independent refresh.');
  await evaluate(`${saveButton}.scrollIntoView({block:'center'})`); await delay(200);
  fs.writeFileSync(path.join(dataDir, 'desktop-connection-recovered.png'), (await window.webContents.capturePage()).toPNG());
  const jobsAfter = await requestApi('/api/v2/jobs?page_size=1');
  if (jobsAfter.total !== jobsBefore.total || rendererErrors.length) throw new Error('Native configuration acceptance created a job or observed a renderer failure.');
  checks.connection_recovery = { result: 'passed', isolated_profile: dataDir, original_sha256: originalHash,
    original_archived: true, archive_path: archive, archive_sha256: crypto.createHash('sha256').update(fs.readFileSync(archive)).digest('hex'),
    secure_storage_roundtrip: true, saved_encrypted_sha256: crypto.createHash('sha256').update(encrypted).digest('hex'),
    model, api_key_empty: true, private_http_applied: true, source_research_available: true, research_total: researchBefore.total,
    research_ui: researchUi, backup_downloaded: checks.backup.file, before, after, safe_ipc_before: ipcBefore, safe_ipc_after: ipcAfter,
    backend_before: capabilitiesBefore.llm, backend_after: capabilitiesAfter.llm, jobs_unchanged: true, renderer_error_count: rendererErrors.length,
    no_ai_job_created: true, native_form_submitted: true, independent_refresh: true, independent_refresh_did_finish_load: true };
  fs.writeFileSync(path.join(dataDir, 'desktop-connection-recovery-checks.json'), JSON.stringify(checks.connection_recovery, null, 2));
}

async function restoreAcceptance(checks) {
  if (process.env.WORKBENCH_SMOKE_EXIT !== '1' || !process.env.WORKBENCH_DATA_DIR) throw new Error('Restore acceptance must own an explicit isolated profile');
  const marker = process.env.WORKBENCH_SMOKE_RESTORE_MARKER;
  const sourceSetting = process.env.WORKBENCH_SMOKE_RESTORE_SOURCE;
  const expectedSetting = process.env.WORKBENCH_SMOKE_RESTORE_EXPECTATIONS;
  if (!sourceSetting || !marker || !expectedSetting) throw new Error('Restore acceptance requires an explicit source fixture, expectations and marker');
  const source = fs.realpathSync(path.resolve(sourceSetting));
  const expectedFile = fs.realpathSync(path.resolve(expectedSetting));
  const expected = JSON.parse(fs.readFileSync(expectedFile, 'utf8'));
  const currentPath = path.join(dataDir, 'workbench.sqlite3');
  if (source === fs.realpathSync(currentPath)) throw new Error('Restore acceptance source must differ from current database');
  const sha = filename => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
  if (source.toLowerCase() !== fs.realpathSync(expected.source_path).toLowerCase() || expected.marker !== marker || expected.source_sha256 !== sha(source) || expected.source_size_bytes !== fs.statSync(source).size) throw new Error('Restore acceptance fixture does not match its immutable expectations');
  if (!expected.expected_counts || !expected.record_ids || !Array.isArray(expected.source_jobs)) throw new Error('Restore acceptance expectations are incomplete');
  const originalMarker = 'desktop-restore-original-' + crypto.randomBytes(8).toString('hex');
  const priorDraft = await requestApi('/api/v2/drafts/collection');
  await requestApi('/api/v2/drafts/collection', 'PUT', { schema: 1, values: { source: 'google_news', keyword: originalMarker }, modeBySource: {}, revision: priorDraft.draft?.revision ?? 0 });
  const imported = await requestApi('/api/v2/items/import', 'POST', { items: [{ title: originalMarker, content: originalMarker, source_name: '本机恢复验收', type: 'news', published_at: '2026-10-08T00:00:00Z', url: 'https://example.invalid/' + originalMarker }] });
  if (imported.inserted !== 1) throw new Error('Restore acceptance original marker was not committed');
  // Synthetic isolated encrypted configuration and a social sentinel prove
  // the restore transaction only changes research storage. No credentials or
  // browser profile are discovered by this acceptance path.
  if (!fs.existsSync(connectionFile())) fs.writeFileSync(connectionFile(), safeStorage.encryptString(JSON.stringify({ base_url: 'https://api.deepseek.com', model: 'deepseek-flash', api_key: '' })), { mode: 0o600 });
  fs.mkdirSync(path.join(dataDir, 'social'), { recursive: true });
  const sentinel = path.join(dataDir, 'social', 'restore-smoke-sentinel.txt');
  fs.writeFileSync(sentinel, 'synthetic isolated social preservation ' + originalMarker, 'utf8');
  const before = { source: sha(source), expectations: sha(expectedFile), connection: sha(connectionFile()), social: sha(sentinel), pid: backend.pid, base, token,
    draft: (await requestApi('/api/v2/drafts/collection')).draft, items: (await requestApi('/api/v2/items?page_size=200')).items,
    jobs: (await requestApi('/api/v2/jobs?page_size=200')).items, overview: await requestApi('/api/v2/overview') };
  const evaluate = expression => window.webContents.executeJavaScript(expression);
  async function waitUi(expression) {
    for (let attempt = 0; attempt < 200; attempt++) { if (await evaluate(expression)) return; await delay(100); }
    throw new Error('Restore acceptance UI did not reach the expected state: ' + expression);
  }
  await waitUi("!!document.querySelector('#navigation a[data-route=settings]')");
  await evaluate("document.querySelector('#navigation a[data-route=settings]').click()");
  await waitUi("!!document.querySelector('[data-action=restore-backup]') && !document.querySelector('[data-action=restore-backup]').disabled");
  await evaluate("document.querySelector('[data-action=restore-backup]').scrollIntoView({block:'center'})");
  await delay(250);
  fs.writeFileSync(path.join(dataDir, 'desktop-restore-before.png'), (await window.webContents.capturePage()).toPNG());
  smokeRestore = { source, dialogs: [] };
  await evaluate("document.querySelector('[data-action=restore-backup]').click()");
  const deadline = Date.now() + 180000;
  while (!smokeRestoreResult && Date.now() < deadline) await delay(100);
  if (!smokeRestoreResult?.restored) throw new Error(smokeRestoreResult?.error || 'Restore acceptance transaction timed out');
  if (smokeRestore.dialogs.join(',') !== 'open,confirm') throw new Error('Restore acceptance did not traverse selection and confirmation');
  await waitUi("!!document.querySelector('[data-action=restore-backup]')");
  const restoredDraft = await requestApi('/api/v2/drafts/collection');
  const restoredItems = await requestApi('/api/v2/items?query=' + encodeURIComponent(marker) + '&page_size=200');
  const allItems = await requestApi('/api/v2/items?page_size=200');
  const accounts = await requestApi('/api/v2/accounts');
  const reports = await requestApi('/api/v2/reports?page_size=200');
  const research = await requestApi('/api/v2/research?page_size=100');
  const restoredReport = await requestApi('/api/v2/reports/' + expected.record_ids.report);
  const graph = await requestApi('/api/v2/research/' + expected.record_ids.run + '/export');
  const oldItems = await requestApi('/api/v2/items?query=' + encodeURIComponent(originalMarker));
  const jobs = await requestApi('/api/v2/jobs?page_size=200');
  const overview = await requestApi('/api/v2/overview');
  if (restoredDraft.draft?.values?.keyword !== marker || !restoredItems.items.some(item => item.title.includes(marker)) || oldItems.total !== 0) throw new Error('Restored API did not show the fixture marker replacing the original marker');
  const currentCounts = { items: allItems.total, accounts: accounts.items.length, reports: reports.total, research_runs: research.total, jobs: jobs.total, drafts: restoredDraft.draft ? 1 : 0 };
  for (const [name, count] of Object.entries(expected.expected_counts)) if (currentCounts[name] !== count || smokeRestoreResult.summary.counts[name] !== count) throw new Error('Restored API or confirmation count differs from source for ' + name);
  if (smokeRestoreResult.summary.interrupted_jobs_count !== expected.expected_interrupted_jobs_count) throw new Error('Interrupted job count differs from the fixture');
  for (const old of expected.source_jobs) {
    const current = jobs.items.find(job => job.id === old.id);
    const status = ['queued', 'running'].includes(old.status) ? 'interrupted' : old.status;
    if (!current || current.status !== status || current.kind !== old.kind) throw new Error('Restored job history differs from the fixture for ' + old.id);
  }
  if (!allItems.items.some(item => item.id === expected.record_ids.item && item.title === marker) || !accounts.items.some(account => account.id === expected.record_ids.account && account.name === marker && account.status === 'verified') || !restoredReport.markdown.includes(marker) || restoredReport.snapshot?.items?.[0]?.title !== marker) throw new Error('Restored material, account or report content differs from the fixture');
  if (graph.nodes.length !== expected.graph_counts.nodes || graph.edges.length !== expected.graph_counts.edges || graph.claims.length !== expected.graph_counts.claims || graph.run.status !== 'interrupted' || graph.run.metadata?.checkpoint_marker !== marker || graph.run.frontier?.[0]?.account_key !== 'twitter:restore_next' || !graph.run.visited.includes('twitter:restore_marker') || !graph.run.analysis?.findings?.includes('保留已记录证据')) throw new Error('Restored research checkpoint or graph content differs from the fixture');
  if (jobs.items.some(job => ['queued', 'running'].includes(job.status)) || jobs.items.filter(job => job.status === 'interrupted').length < smokeRestoreResult.summary.interrupted_jobs_count || overview.stats.active_jobs !== 0) throw new Error('Restored unfinished jobs ran automatically');
  const after = { source: sha(source), expectations: sha(expectedFile), connection: sha(connectionFile()), social: sha(sentinel) };
  if (before.source !== after.source || before.expectations !== after.expectations || before.connection !== after.connection || before.social !== after.social) throw new Error('Restore acceptance changed a protected source/configuration/social file');
  if (before.pid === backend.pid || before.token === token || before.base === base) throw new Error('Restore did not refresh the backend process, port and authentication token');
  await evaluate("document.querySelector('#navigation a[data-route=library]').click()");
  await waitUi(`document.querySelector('#main')?.textContent.includes(${JSON.stringify(marker)})`);
  await delay(250);
  fs.writeFileSync(path.join(dataDir, 'desktop-restore-library.png'), (await window.webContents.capturePage()).toPNG());
  await evaluate("document.querySelector('#navigation a[data-route=jobs]').click()");
  await waitUi("document.querySelector('#main')?.textContent.includes('中断')");
  await evaluate("document.querySelector('.job-list').scrollIntoView({block:'start'})");
  await delay(250);
  fs.writeFileSync(path.join(dataDir, 'desktop-restore-jobs.png'), (await window.webContents.capturePage()).toPNG());
  await evaluate("document.querySelector('#navigation a[data-route=settings]').click()");
  await waitUi("!!document.querySelector('[data-action=restore-backup]')");
  await evaluate("document.querySelector('[data-action=restore-backup]').scrollIntoView({block:'center'})");
  await delay(250);
  fs.writeFileSync(path.join(dataDir, 'desktop-restore-settings.png'), (await window.webContents.capturePage()).toPNG());
  const archived = path.join(smokeRestoreResult.archive_path, 'workbench.sqlite3');
  const archivedHeader = fs.readFileSync(archived).subarray(0, 16).toString('ascii') === 'SQLite format 3\0';
  if (!archivedHeader || fs.statSync(archived).size < 4096) throw new Error('Original archived database was not preserved');
  const settledJobs = await requestApi('/api/v2/jobs?page_size=200');
  if (settledJobs.total !== jobs.total || settledJobs.items.some(job => ['queued', 'running'].includes(job.status)) || settledJobs.items.some(job => jobs.items.find(previous => previous.id === job.id)?.status !== job.status)) throw new Error('Restored jobs changed after the UI and worker were given time to settle');
  checks.restore = { source_file: source, source_sha256: before.source, source_sha256_after: after.source, source_unchanged: before.source === after.source,
    expected_file: expectedFile, expected_sha256: before.expectations, expected_sha256_after: after.expectations, expected_counts: expected.expected_counts, expected_interrupted_jobs_count: expected.expected_interrupted_jobs_count,
    expected_record_ids: expected.record_ids, source_job_statuses: expected.source_jobs, current_database: currentPath, expected_prepared_database: expected.prepared_path,
    archive_path: smokeRestoreResult.archive_path, archived_files: fs.readdirSync(smokeRestoreResult.archive_path).map(name => ({ name, size_bytes: fs.statSync(path.join(smokeRestoreResult.archive_path, name)).size, sha256: sha(path.join(smokeRestoreResult.archive_path, name)) })),
    restored_counts: smokeRestoreResult.summary.counts, current_counts: currentCounts, summary: smokeRestoreResult.summary, queued_interrupted: true, active_jobs: overview.stats.active_jobs, jobs_stable_after_ui: true,
    job_statuses: jobs.items.map(job => ({ id: job.id, status: job.status, kind: job.kind })), original_preserved: archivedHeader,
    connection_unchanged: before.connection === after.connection, connection_sha256: after.connection, social_unchanged: before.social === after.social, social_sentinel: sentinel, social_sha256: after.social,
    original_current_marker: originalMarker, current_marker: restoredDraft.draft.values.keyword, source_marker: marker, markers_differ: originalMarker !== marker,
    before: { pid: before.pid, base: before.base, draft: before.draft, items: before.items, jobs: before.jobs, overview: before.overview },
    current: { pid: backend.pid, base, draft: restoredDraft.draft, items: allItems.items, accounts: accounts.items, report: restoredReport, research: research.items, graph, jobs: settledJobs.items, overview },
    ui_fixture_visible: true, same_browser_window: true, native_dialogs: smokeRestore.dialogs, native_confirmation: smokeRestore.confirmation,
    backend_restarted: before.pid !== backend.pid, port_changed: before.base !== base, session_token_changed: before.token !== token, isolated_profile: dataDir };
  fs.writeFileSync(path.join(dataDir, 'desktop-restore-checks.json'), JSON.stringify(checks.restore, null, 2));
  smokeRestore = null;
}

async function showWindow() {
  window = new BrowserWindow({ title: 'PEACE ATLAS / 反戦研究室', width: 1440, height: 940, minWidth: 800, minHeight: 600, backgroundColor: '#f2efe6', show: false, icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true } });
  const rendererErrors = [];
  if (process.env.WORKBENCH_SMOKE_CONNECTION_RECOVERY === '1') {
    // Record only safe codes/counts: renderer messages/URLs can contain input
    // or the desktop token and are not acceptance evidence.
    window.webContents.on('console-message', (_event, levelOrDetails) => {
      const level = typeof levelOrDetails === 'object' ? levelOrDetails.level : levelOrDetails;
      if (level === 'error' || level === 3) rendererErrors.push({ code: 'console_error' });
    });
    window.webContents.on('render-process-gone', () => rendererErrors.push({ code: 'renderer_stopped' }));
    window.webContents.on('did-fail-load', (_event, code, _description, _url, mainFrame) => {
      if (mainFrame && code !== -3) rendererErrors.push({ code: 'page_load_failed', error_code: code });
    });
  }
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  window.webContents.setWindowOpenHandler(({ url }) => { external(url); return { action: 'deny' }; });
  window.webContents.on('will-navigate', (event, url) => { if (new URL(url).origin !== base) { event.preventDefault(); external(url); } });
  window.once('ready-to-show', () => {
    if (process.env.WORKBENCH_SMOKE_EXIT === '1') window.showInactive();
    else window.show();
  });
  window.on('closed', () => { window = null; });
  window.on('close', event => {
    if (lifecycle.isRestoring()) {
      event.preventDefault();
      if (!quitting) app.quit();
    }
  });
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === 'darwin' ? [{ label: app.name, submenu: [{ role: 'about' }, { type: 'separator' }, { role: 'hide' }, { role: 'quit' }] }] : []),
    { label: '工作台', submenu: [{ label: '系统设置', click: () => window?.loadURL(base + '/#settings') }, { label: '打开数据文件夹', click: () => shell.openPath(dataDir) }, { label: '打开日志', click: () => shell.openPath(path.join(dataDir, 'logs')) }, { type: 'separator' }, { role: 'quit', label: '退出' }] },
    { label: '外部 Agent', submenu: [
      { label: '接入与 Skill 安装说明', click: showAgentInstructions },
      { label: '导出授权连接', click: authorizeAgentAccess },
      { label: '撤销全部 Agent 授权', click: revokeAgentAccess }
    ] },
    { label: '编辑', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] },
    { label: '显示', submenu: [{ role: 'reload' }, { role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'togglefullscreen' }] }
  ]));
  await window.loadURL(base + '/?desktop_token=' + token);
  if (pendingRecovery) { restoration.completeRecovery(dataDir, pendingRecovery.txid); log('Previous restore transaction recovered: ' + pendingRecovery.archive_path); pendingRecovery = null; }
  log('Application ready');
  // Optional release smoke mode never enabled in a normal user launch.
  if (process.env.WORKBENCH_SMOKE_EXIT === '1') {
    const checks = {};
    const probe = crypto.randomBytes(16).toString('hex');
    // Fixed phase names locate a native acceptance stall without sampling
    // process memory or logging the synthetic probe, ciphertext or settings.
    log('Release smoke phase: storage-availability-start');
    const storageAvailable = safeStorage.isEncryptionAvailable();
    log('Release smoke phase: storage-availability-complete');
    checks.secure_storage = false;
    if (storageAvailable) {
      log('Release smoke phase: storage-encrypt-start');
      const encryptedProbe = safeStorage.encryptString(probe);
      log('Release smoke phase: storage-encrypt-complete');
      log('Release smoke phase: storage-decrypt-start');
      checks.secure_storage = safeStorage.decryptString(encryptedProbe) === probe;
      log('Release smoke phase: storage-decrypt-complete');
    }
    async function runJob(kind, parameters) {
      const created = await requestApi('/api/v2/jobs', 'POST', { kind, params: parameters });
      for (let attempt = 0; attempt < 240; attempt++) {
        const result = await requestApi('/api/v2/jobs/' + created.id);
        if (!['queued', 'running'].includes(result.status)) {
          if (result.status !== 'succeeded') throw new Error(`Release smoke ${kind} failed: ${result.error || result.status}`);
          return result;
        }
        await delay(500);
      }
      throw new Error(`Release smoke ${kind} timed out`);
    }
    if (process.env.WORKBENCH_SMOKE_SESSIONS) {
      const platforms = process.env.WORKBENCH_SMOKE_SESSIONS.split(',');
      if (!platforms.every(platform => ['twitter', 'instagram'].includes(platform))) throw new Error('Invalid session smoke platform');
      checks.sessions = {};
      for (const platform of new Set(platforms)) {
        const first = await runJob('login', { platform, method: 'auto' });
        const second = first.result?.verified ? await runJob('login', { platform, method: 'auto' }) : null;
        checks.sessions[platform] = { first: { id: first.id, result: first.result }, second: second && { id: second.id, result: second.result } };
      }
      await window.loadURL(base + '/#settings');
      checks.default_browser_controls = false;
      for (let attempt = 0; attempt < 50; attempt++) {
        const text = await window.webContents.executeJavaScript('document.body.innerText');
        if (text.includes('用系统默认浏览器登录') && text.includes('检测并复用已有登录')) {
          checks.default_browser_controls = true;
          break;
        }
        await delay(100);
      }
      if (!checks.default_browser_controls) throw new Error('Default-browser controls missing from packaged UI');
    }
    if (process.env.WORKBENCH_SMOKE_FULL === '1') {
      const params = { source: 'google_news', keyword: '憲法九条', start_date: process.env.WORKBENCH_SMOKE_START, end_date: process.env.WORKBENCH_SMOKE_END, max_records: 5 };
      const collected = await runJob('collect', params);
      if (!collected.result.fetched) throw new Error('Release smoke received no public records');
      const again = await runJob('collect', params);
      if (!again.result.duplicates || again.result.inserted) throw new Error('Release smoke deduplication failed');
      const completed = await runJob('report', { title: '公开新闻 · 桌面发布验收', start_date: params.start_date, end_date: params.end_date, mode: 'evidence', include_news: true, include_social: false });
      const report = await requestApi('/api/v2/reports/' + completed.result.report_id);
      if (!report.snapshot || !report.source_count || !report.markdown) throw new Error('Release smoke report snapshot missing');
      checks.collection = { id: collected.id, result: collected.result };
      checks.deduplication = again.result;
      checks.report = { id: report.id, source_count: report.source_count, mode: report.mode };
      await window.loadURL(base + '/#overview');
    }
    if (process.env.WORKBENCH_SMOKE_RESEARCH === '1') {
      const params = { source: 'google_news', seed_sources: ['google_news'], keyword: '憲法九条,反戦', keyword_mode: 'any', research_goal: '公开资料研究 · 桌面验收', start_date: process.env.WORKBENCH_SMOKE_START, end_date: process.env.WORKBENCH_SMOKE_END, max_records: 1, depth: 1, max_accounts: 2, max_edges: 2, max_scrolls: 1, time_budget_seconds: 90, expand_relations: ['mentions'], analysis_mode: 'rules' };
      const preview = await requestApi('/api/v2/research/batch-plan', 'POST', { title: '任务串规模验收', kind: 'research', params: { ...params, max_records: 200 }, total_records: 1000 });
      if (preview.tasks.length <= 1 || preview.max_possible_records !== 1000) throw new Error('Research batch planning did not split the overall target');
      const created = await requestApi('/api/v2/jobs/batch', 'POST', { title: '', kind: 'research', params, total_records: 2 });
      if (created.batch.title !== '批量公开资料研究') throw new Error('Optional research batch title did not receive its default name');
      let completed;
      for (let attempt = 0; attempt < 360; attempt++) {
        completed = await requestApi('/api/v2/job-batches/' + created.batch.id);
        if (completed.jobs.every(job => !['queued', 'running'].includes(job.status))) break;
        await delay(500);
      }
      if (!completed || completed.jobs.length !== 2 || !completed.jobs.every(job => ['succeeded', 'partial'].includes(job.status))) throw new Error('Research batch did not complete its two queued jobs');
      const runId = completed.jobs[0].result?.run_id;
      const graph = await requestApi('/api/v2/research/' + runId);
      if (!graph.items.length || !graph.run.summary || !graph.run.params) throw new Error('Research evidence snapshot missing');
      checks.research = { batch: completed, preview_task_count: preview.tasks.length, overall_target: preview.total_records, graph: { run_id: graph.run.id, items: graph.items.length, nodes: graph.nodes.length, edges: graph.edges.length } };
      await window.loadURL(base + '/#network');
      async function uiWait(expression, attempts = 100) {
        for (let attempt = 0; attempt < attempts; attempt++) { if (await window.webContents.executeJavaScript(expression)) return; await delay(100); }
        throw new Error('Research desktop UI did not reach the expected state');
      }
      await uiWait("document.querySelectorAll('.workspace-choice').length===2");
      await delay(300); // The compositor can briefly retain the preceding page after the DOM changes.
      fs.writeFileSync(path.join(dataDir, 'desktop-workspace.png'), (await window.webContents.capturePage()).toPNG());
      await window.loadURL(base + '/#jobs');
      await uiWait("document.querySelector('.page-heading h1')?.textContent==='任务中心'");
      checks.research.workspace = await window.webContents.executeJavaScript("({task_center_creation_link:document.querySelector('.page-heading a')?.getAttribute('href')==='#network',duplicate_creation_buttons:[...document.querySelectorAll('button')].filter(b=>['新建采集','批量研究'].includes(b.textContent.trim())).length})");
      if (!checks.research.workspace.task_center_creation_link || checks.research.workspace.duplicate_creation_buttons) throw new Error('Task-center creation responsibilities did not migrate');
      await window.loadURL(base + '/#network');
      await uiWait("!![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='新建研究或采集')");
      await window.webContents.executeJavaScript("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='新建研究或采集').click()");
      await uiWait("!!document.querySelector('[name=keyword]')");
      await delay(400);
      if (await window.webContents.executeJavaScript("!!document.querySelector('[name=seed_twitter]')?.checked")) await uiWait("document.querySelector('.collection-session')?.textContent.includes('可以开始采集')", 300);
      const draftKeyword = '革命, 労働者, 矢嶋尋, 戦争阻止, 全学連, 階級闘争';
      await window.webContents.executeJavaScript(`(() => { const input=document.querySelector('[name=keyword]');input.value=${JSON.stringify(draftKeyword)};input.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[aria-label="关闭对话框"]').click(); })()`);
      await delay(700);
      const reloaded = new Promise(resolve => window.webContents.once('did-finish-load', resolve));
      window.reload();
      await Promise.race([reloaded, delay(10000).then(() => { throw new Error('Research UI reload timed out'); })]);
      await uiWait("!![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='新建研究或采集')");
      await window.webContents.executeJavaScript("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='新建研究或采集').click()");
      await uiWait(`document.querySelector('dialog[open]')?.getBoundingClientRect().height>100 && document.querySelector('[name=keyword]')?.value===${JSON.stringify(draftKeyword)}`);
      checks.research.draft_restored_after_reload = true;
      checks.research.workspace.composer_choices = await window.webContents.executeJavaScript("(() => { const kind=document.querySelector('[name=batch_kind]'),execution=document.querySelector('[name=workflow]');return {content:kind.value,execution:execution.value,content_visible:kind.getBoundingClientRect().height>0,execution_visible:execution.getBoundingClientRect().height>0,execution_options:[...execution.options].map(o=>o.value)}; })()");
      if (!checks.research.workspace.composer_choices.content_visible || !checks.research.workspace.composer_choices.execution_visible || checks.research.workspace.composer_choices.execution_options.join(',') !== 'single,batch') throw new Error('Composer content/execution choices are not independent');
      if (await window.webContents.executeJavaScript("!!document.querySelector('[name=seed_twitter]')?.checked")) await uiWait("document.querySelector('.collection-session')?.textContent.includes('可以开始采集')", 300);
      await delay(300);
      const composerImage = await window.webContents.capturePage();
      fs.writeFileSync(path.join(dataDir, 'desktop-research-composer.png'), composerImage.toPNG());
      await window.webContents.executeJavaScript(`document.querySelector('[aria-label="关闭对话框"]').click()`);
      // Retain the verified draft in this isolated acceptance profile. Trying
      // to restore an earlier revision would race the composer's final save.
      const runs = await requestApi('/api/v2/research?page_size=50');
      let displayRun = runId;
      for (const run of runs.items) {
        const candidate = await requestApi('/api/v2/research/' + run.id);
        if (candidate.nodes.length > 1 && candidate.edges.length) { displayRun = run.id; break; }
      }
      await window.loadURL(base + '/#network?run=' + encodeURIComponent(displayRun));
      await uiWait("!!document.querySelector('.network-toolbar')");
      await window.webContents.executeJavaScript("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='关系网络').click()");
      checks.research.display_run_id = displayRun;
    }
    if (process.env.WORKBENCH_SMOKE_TYPOGRAPHY === '1') {
      log('Release smoke phase: typography-start');
      async function fontPage(hash, selector, filename) {
        // Exercise the app's hash router through its own navigation links.
        // Electron loadURL can reject a fragment-only navigation immediately
        // after the initial authentication redirect has finished loading.
        const linkSelector = '#navigation a[data-route="' + hash.slice(1) + '"]';
        for (let attempt = 0; attempt < 100; attempt++) {
          if (await window.webContents.executeJavaScript(`!!document.querySelector(${JSON.stringify(linkSelector)})`)) break;
          if (attempt === 99) throw new Error('Typography navigation did not initialize');
          await delay(100);
        }
        await window.webContents.executeJavaScript(`document.querySelector(${JSON.stringify(linkSelector)}).click()`);
        for (let attempt = 0; attempt < 100; attempt++) {
          if (await window.webContents.executeJavaScript(`!!document.querySelector(${JSON.stringify(selector)})`)) break;
          if (attempt === 99) throw new Error('Typography page did not render');
          await delay(100);
        }
        await window.webContents.executeJavaScript('document.fonts.ready.then(()=>true)');
        await delay(300);
        const details = await window.webContents.executeJavaScript(`(() => { const heading=document.querySelector(${JSON.stringify(selector)});const style=getComputedStyle(heading);return {title:heading.textContent,family:style.fontFamily,weight:style.fontWeight,fonts:[...document.fonts].map(f=>({family:f.family,status:f.status})),horizontal_overflow:document.documentElement.scrollWidth>innerWidth}; })()`);
        if (!details.fonts.some(font => font.family.includes('Atlas Mincho') && font.status === 'loaded') || !details.fonts.some(font => font.family.includes('Atlas Signal') && font.status === 'loaded') || details.horizontal_overflow) throw new Error('Packaged typography/font layout check failed');
        fs.writeFileSync(path.join(dataDir, filename), (await window.webContents.capturePage()).toPNG());
        return details;
      }
      checks.typography = {};
      checks.typography.overview = await fontPage('#overview', '.hero h1', 'desktop-eva-overview.png');
      checks.typography.workspace = await fontPage('#network', '.page-heading h1', 'desktop-eva-workspace.png');
      await window.webContents.executeJavaScript("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='新建研究或采集').click()");
      for (let attempt = 0; attempt < 100; attempt++) {
        if (await window.webContents.executeJavaScript("!!document.querySelector('dialog[open] [name=keyword]')")) break;
        if (attempt === 99) throw new Error('Typography composer did not render');
        await delay(100);
      }
      await window.webContents.executeJavaScript('document.fonts.ready.then(()=>true)');
      await delay(300);
      checks.typography.composer = await window.webContents.executeJavaScript("(() => {const heading=document.querySelector('.dialog-heading h2');return {family:getComputedStyle(heading).fontFamily,weight:getComputedStyle(heading).fontWeight,body_family:getComputedStyle(document.querySelector('.dialog-body p')).fontFamily,dialog_overflow:document.querySelector('dialog').scrollWidth>document.querySelector('dialog').clientWidth};})()");
      if (checks.typography.composer.dialog_overflow) throw new Error('Typography composer overflow');
      fs.writeFileSync(path.join(dataDir, 'desktop-eva-composer.png'), (await window.webContents.capturePage()).toPNG());
      await window.webContents.executeJavaScript("document.querySelector('[aria-label=\"关闭对话框\"]').click()");
      log('Release smoke phase: typography-complete');
    }
    if (process.env.WORKBENCH_SMOKE_BUDGETS === '1') {
      log('Release smoke phase: budgets-start');
      // Acceptance exercises this application's composer against its packaged
      // service. Planning is read-only; this smoke never enqueues a long task.
      const capabilities = await requestApi('/api/v2/capabilities');
      if (capabilities.budget_limits?.research.max_records.max !== 1000000 || capabilities.budget_limits.research.max_scrolls.max !== 5000 || capabilities.budget_limits.research.time_budget_seconds.max !== 2592000) throw new Error('Packaged budget capabilities differ');
      const evaluate = expression => window.webContents.executeJavaScript(expression);
      async function waitBudget(expression) {
        for (let attempt = 0; attempt < 150; attempt++) { if (await evaluate(expression)) return; await delay(100); }
        throw new Error('Budget composer did not reach the expected state');
      }
      await waitBudget("!!document.querySelector('#navigation a[data-route=network]')");
      await evaluate("document.querySelector('#navigation a[data-route=network]').click()");
      await waitBudget("!![...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='新建研究或采集')");
      await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='新建研究或采集').click()");
      await waitBudget("!!document.querySelector('dialog[open] [name=workflow]')");
      await evaluate("(() => {const f=document.querySelector('dialog form');for(const [name,value] of Object.entries({batch_kind:'research',workflow:'batch',keyword:'憲法九条',max_records:'500',research_max_scrolls:'25',time_budget_seconds:'3600',total_records:'1000',seed_news:true,seed_twitter:false,seed_instagram:false})){const input=f.elements[name];if(input.type==='checkbox')input.checked=value;else input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}document.querySelector('.research-budget').open=true;})()");
      const primary = "document.querySelector('dialog .form-actions button[type=submit]')";
      await waitBudget(`${primary}?.textContent==='预览任务串' && !${primary}.disabled`);
      const beforeJobs = await requestApi('/api/v2/jobs');
      await evaluate(`${primary}.click()`);
      await waitBudget(`${primary}?.textContent==='创建研究任务串' && !${primary}.disabled`);
      checks.budgets = { capabilities: capabilities.budget_limits, ui: await evaluate("(() => {const f=document.querySelector('dialog form');return {records:f.elements.max_records.value,records_max:f.elements.max_records.max,scrolls:f.elements.research_max_scrolls.value,scrolls_max:f.elements.research_max_scrolls.max,seconds:f.elements.time_budget_seconds.value,seconds_max:f.elements.time_budget_seconds.max,primary:f.querySelector('button[type=submit]').textContent,status:f.querySelector('.submit-status').textContent,preview:f.querySelector('.batch-plan').textContent};})()") };
      await evaluate("document.querySelector('[name=research_max_scrolls]').scrollIntoView({block:'center'})");
      await delay(200);
      fs.writeFileSync(path.join(dataDir, 'desktop-budget-controls.png'), (await window.webContents.capturePage()).toPNG());
      if (checks.budgets.ui.records_max !== '1000000' || checks.budgets.ui.scrolls_max !== '5000' || checks.budgets.ui.seconds_max !== '2592000') throw new Error('Packaged research controls differ from their actual capabilities');
      await evaluate("(() => {const f=document.querySelector('dialog form');for(const [name,value] of Object.entries({max_records:'1000000',research_max_scrolls:'5000',time_budget_seconds:'2592000',total_records:'1000000'})){f.elements[name].value=value;f.elements[name].dispatchEvent(new Event('input',{bubbles:true}));}})()");
      await waitBudget(`${primary}?.textContent==='预览任务串' && !${primary}.disabled`);
      await evaluate(`${primary}.click()`);
      await waitBudget(`${primary}?.textContent==='创建研究任务串' && !${primary}.disabled`);
      const afterJobs = await requestApi('/api/v2/jobs');
      if (!Number.isInteger(beforeJobs.total) || afterJobs.total !== beforeJobs.total) throw new Error('Budget preview unexpectedly created jobs');
      checks.budgets.boundary_preview_ready = true;
      checks.budgets.preview_created_jobs = afterJobs.total - beforeJobs.total;
      checks.budgets.boundary_status = await evaluate("document.querySelector('.batch-plan').textContent");
      await evaluate("document.querySelector('.submit-status').scrollIntoView({block:'center'})");
      await delay(200);
      fs.writeFileSync(path.join(dataDir, 'desktop-budget-preview.png'), (await window.webContents.capturePage()).toPNG());
      await evaluate("document.querySelector('[aria-label=\"关闭对话框\"]').click()");
      log('Release smoke phase: budgets-complete');
    }
    if (process.env.WORKBENCH_SMOKE_BACKUP === '1' || process.env.WORKBENCH_SMOKE_CONNECTION_RECOVERY === '1') {
      log('Release smoke phase: backup-start');
      if (process.env.WORKBENCH_SMOKE_CONNECTION_RECOVERY === '1') await smokeNavigate('settings', "!!document.querySelector('[data-action=\"save-connection\"]')");
      else await window.loadURL(base + '/#settings');
      const button = "[...document.querySelectorAll('section[aria-label=\"研究数据备份\"] button')].find(button=>button.textContent.trim()==='备份研究数据')";
      for (let attempt = 0; attempt < 150; attempt++) {
        if (await window.webContents.executeJavaScript(`!!${button} && !${button}.disabled`)) break;
        if (attempt === 149) throw new Error('Packaged research backup control did not become available');
        await delay(100);
      }
      // A synthetic committed draft proves the snapshot includes recent WAL
      // changes. All acceptance data belongs to the explicit smoke profile.
      const marker = 'desktop-backup-smoke-' + crypto.randomBytes(8).toString('hex');
      const previous = await requestApi('/api/v2/drafts/collection');
      const saved = await requestApi('/api/v2/drafts/collection', 'PUT', { schema: 1, values: { source: 'google_news', keyword: marker }, modeBySource: {}, revision: previous.draft?.revision ?? 0 });
      const downloads = path.join(dataDir, 'smoke-downloads');
      fs.mkdirSync(downloads, { recursive: true });
      let timer, onDownload;
      const downloaded = new Promise((resolve, reject) => {
        timer = setTimeout(() => { session.defaultSession.removeListener('will-download', onDownload); reject(new Error('Packaged backup download timed out')); }, 90_000);
        onDownload = (_event, item, contents) => {
          if (contents !== window.webContents) return;
          session.defaultSession.removeListener('will-download', onDownload);
          const filename = path.basename(item.getFilename());
          if (!/^peace-atlas-backup-\d{8}T\d{12}Z\.sqlite3$/.test(filename)) { clearTimeout(timer); item.cancel(); reject(new Error('Packaged backup filename is invalid')); return; }
          const target = path.join(downloads, filename);
          item.setSavePath(target);
          item.once('done', (_event, state) => {
            clearTimeout(timer);
            if (state !== 'completed' || !fs.existsSync(target)) { reject(new Error('Packaged backup download did not complete: ' + state)); return; }
            const size = fs.statSync(target).size;
            const header = fs.readFileSync(target).subarray(0, 16).toString('ascii');
            if (size < 4096 || header !== 'SQLite format 3\0') { reject(new Error('Packaged backup is not a SQLite database')); return; }
            resolve({ filename, file: target, size_bytes: size, sqlite_header: true, state });
          });
        };
        session.defaultSession.on('will-download', onDownload);
      });
      let result;
      try { [result] = await Promise.all([downloaded, window.webContents.executeJavaScript(`${button}.click()`)]); }
      finally { clearTimeout(timer); session.defaultSession.removeListener('will-download', onDownload); }
      const current = await requestApi('/api/v2/drafts/collection');
      if (current.draft?.values?.keyword !== marker || current.draft.revision !== saved.draft.revision) throw new Error('Backup download changed the committed draft');
      checks.backup = { ...result, draft_keyword: marker, draft_revision: saved.draft.revision, source_draft_unchanged: true,
        sqlite_integrity: 'pending bundled Python quick_check', isolated_profile: dataDir };
      await delay(300);
      fs.writeFileSync(path.join(dataDir, 'desktop-backup-controls.png'), (await window.webContents.capturePage()).toPNG());
      log('Release smoke phase: backup-complete');
    }
    if (process.env.WORKBENCH_SMOKE_RESTORE === '1') await restoreAcceptance(checks);
    if (process.env.WORKBENCH_SMOKE_CONNECTION_RECOVERY === '1') await connectionRecoveryAcceptance(checks, rendererErrors);
    await delay(1500);
    log('Release smoke phase: final-capture-start');
    const image = await window.webContents.capturePage();
    fs.writeFileSync(path.join(dataDir, 'desktop-smoke.png'), image.toPNG());
    fs.writeFileSync(path.join(dataDir, 'desktop-smoke.json'), JSON.stringify({ title: window.getTitle(), health: await requestApi('/api/v2/health'), overview: await requestApi('/api/v2/overview'), checks, version: app.getVersion(), timestamp: new Date().toISOString() }, null, 2));
    log('Release smoke phase: final-capture-complete');
    app.quit();
  }
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (window) { if (window.isMinimized()) window.restore(); window.show(); window.focus(); } });
  app.whenReady().then(async () => {
    dataDir = path.resolve(process.env.WORKBENCH_DATA_DIR || app.getPath('userData'));
    logFile = path.join(dataDir, 'logs', 'desktop.log');
    try {
      fs.mkdirSync(path.join(dataDir, 'logs'), { recursive: true });
      dataDir = fs.realpathSync(dataDir);
      validateConnectionSmokeFixture();
      pendingRecovery = restoration.recoverPendingRestore(dataDir, { processAlive: pid => { try { process.kill(pid, 0); return true; } catch (error) { return error.code !== 'ESRCH'; } } });
      installIpc();
      if (pendingRecovery) restoration.recoveryStarting(dataDir, pendingRecovery.txid);
      await startBackend(pendingRecovery ? pid => restoration.recoveryStarting(dataDir, pendingRecovery.txid, pid) : undefined);
      await showWindow();
    }
    catch (error) {
      log(`Startup failed: ${error.message}`);
      if (process.env.WORKBENCH_SMOKE_EXIT === '1') {
        try { fs.writeFileSync(path.join(dataDir, 'desktop-smoke-error.json'), JSON.stringify({error: error.message}, null, 2)); } catch (_) {}
        quitting = true;
        await stopBackend();
        app.exit(1);
        return;
      } else {
        dialog.showErrorBox('PEACE ATLAS 启动失败', `${error.message}\n\n日志：${logFile}`);
      }
      app.quit();
    }
  });
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', event => {
    if (quitting && !quitComplete) { event.preventDefault(); return; }
    if (!quitting) {
      event.preventDefault(); quitting = true;
      lifecycle.quit(stopBackend).then(() => { quitComplete = true; app.quit(); }).catch(error => {
        log('Shutdown could not confirm process exit: ' + error.message);
        quitting = false; lifecycle.cancelQuit();
        if (process.env.WORKBENCH_SMOKE_EXIT === '1') { app.exit(1); return; }
        dialog.showErrorBox('本地服务尚未退出', error.message);
      });
    }
  });
}
