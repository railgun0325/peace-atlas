'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const live = child => Boolean(child?.pid && child.exitCode === null && child.signalCode === null);
const siblings = ['workbench.sqlite3', 'workbench.sqlite3-wal', 'workbench.sqlite3-shm'];
const journalName = '.restore-transaction.json';

function failure(code, message, extra = {}) {
  return Object.assign(new Error(message), { code, ...extra });
}

function waitForExit(child, milliseconds) {
  if (!live(child)) return Promise.resolve(true);
  return new Promise(resolve => {
    let timer;
    const finish = result => { clearTimeout(timer); child.removeListener('exit', exited); resolve(result); };
    const exited = () => finish(true);
    child.once('exit', exited);
    timer = setTimeout(() => finish(!live(child)), milliseconds);
    if (!live(child)) finish(true);
  });
}

// A failed preflight can only release its staging files once its owned Python
// process is terminal. In particular ChildProcess.killed is not exit evidence.
async function terminatePrivateChild(child) {
  if (!live(child)) return;
  child.kill();
  if (await waitForExit(child, 5000)) return;
  child.kill('SIGKILL');
  if (!await waitForExit(child, 5000) || live(child)) {
    throw failure('prepare_process_alive', '备份检查进程尚未退出。当前数据未改动，检查目录已保留。', { process_terminal: false });
  }
}

async function runPrivateJson({ spawn, python, entry, args, env, timeout = 75000 }) {
  const child = spawn(python, ['-B', entry, ...args], { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let timer, output = '', settled = false;
  try {
    const completed = new Promise((resolve, reject) => {
      const finish = (error, value) => { if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(value); };
      child.stdout.on('data', chunk => {
        output += chunk.toString('utf8');
        if (output.length > 1024 * 1024) finish(failure('prepare_output_invalid', '备份检查返回了无效结果。'));
      });
      // stderr is diagnostic only, and is deliberately not relayed to the
      // renderer, where it could contain paths or incidental configuration.
      child.stderr.on('data', () => {});
      child.once('error', () => finish(failure('prepare_launch_failed', '无法启动备份检查程序。')));
      child.once('close', (code, signal) => {
        let parsed;
        try { parsed = JSON.parse(output.trim()); }
        catch (_) { finish(failure('prepare_output_invalid', '备份检查未返回有效结果。')); return; }
        if (code !== 0 || signal || !parsed.ok) {
          finish(failure(parsed.error?.code || 'prepare_failed', parsed.error?.message || '备份检查未通过。')); return;
        }
        finish(null, parsed.summary);
      });
      timer = setTimeout(() => finish(failure('prepare_timeout', '备份检查超时，当前数据未改动。')), timeout);
    });
    return await completed;
  } catch (error) {
    try { await terminatePrivateChild(child); }
    catch (termination) { throw termination; }
    error.process_terminal = true;
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function createLifecycleCoordinator() {
  let chain = Promise.resolve(), restore = null, quitting = false;
  const enqueue = task => {
    const pending = chain.then(task);
    chain = pending.catch(() => {});
    return pending;
  };
  return {
    isRestoring: () => restore !== null,
    isQuitting: () => quitting,
    mutation: task => enqueue(() => {
      if (quitting) throw failure('app_quitting', '应用正在退出，请重新打开后再操作。');
      return task();
    }),
    restore: task => {
      if (restore) return restore;
      if (quitting) return Promise.reject(failure('app_quitting', '应用正在退出，请重新打开后再恢复。'));
      restore = enqueue(() => quitting ? { cancelled: true } : task());
      restore.finally(() => { restore = null; }).catch(() => {});
      return restore;
    },
    quit: task => { quitting = true; return enqueue(task); },
    cancelQuit: () => { quitting = false; },
  };
}

function canonicalOwnedRoot(dataDir, io = fs) {
  return io.realpathSync(path.resolve(dataDir));
}

function ownedDirectory(root, group, leaf, io = fs) {
  if (!['restore-staging', 'restores'].includes(group) || !/^[a-zA-Z0-9-]+$/.test(leaf)) throw failure('restore_path_invalid', '恢复目录无效。');
  const parent = path.join(root, group);
  io.mkdirSync(parent, { recursive: true });
  // Existing junctions/symlinks must not redirect an operation beyond its own
  // profile. Every directory we later clean is resolved and checked again.
  if (path.relative(root, io.realpathSync(parent)) !== group) throw failure('restore_path_invalid', '恢复目录不能指向数据文件夹之外。');
  const directory = path.join(parent, leaf);
  io.mkdirSync(directory);
  if (path.relative(parent, io.realpathSync(directory)) !== leaf) throw failure('restore_path_invalid', '恢复目录无效。');
  return directory;
}

function cleanupStage(root, directory, io = fs) {
  if (!directory || !io.existsSync(directory)) return;
  const relative = path.relative(root, io.realpathSync(directory));
  if (!/^restore-staging[\\/][a-zA-Z0-9-]+$/.test(relative)) throw failure('restore_path_invalid', '检查目录清理失败，目录已保留。');
  // Only known flat outputs are removed; no computed recursive deletion.
  for (const name of ['workbench.sqlite3', 'workbench.sqlite3-wal', 'workbench.sqlite3-shm', 'summary.json']) {
    const filename = path.join(directory, name);
    if (io.existsSync(filename)) io.unlinkSync(filename);
  }
  io.rmdirSync(directory);
}

function rejectCurrentSource(source, root, io = fs) {
  const selected = io.realpathSync(source);
  if (!io.statSync(selected).isFile() || /-(wal|shm)$/i.test(selected)) throw failure('backup_source_invalid', '请选择独立的 SQLite 研究备份文件。');
  for (const name of siblings) {
    const current = path.join(root, name);
    if (!io.existsSync(current)) continue;
    const a = io.statSync(selected), b = io.statSync(current);
    if (io.realpathSync(current).toLowerCase() === selected.toLowerCase() || (a.ino && b.ino && a.ino === b.ino && a.dev === b.dev)) {
      throw failure('backup_source_current', '不能将当前正在使用的数据库或 sidecar 作为恢复备份。请先导出独立备份。');
    }
  }
  return selected;
}

function validateCandidate(stage, summary, io = fs) {
  const candidate = path.join(stage, 'workbench.sqlite3');
  if (!io.existsSync(candidate) || io.lstatSync(candidate).isSymbolicLink() || !io.statSync(candidate).isFile()) throw failure('prepare_output_invalid', '备份检查没有生成完整候选数据库。');
  const header = Buffer.alloc(16), handle = io.openSync(candidate, 'r');
  try { io.readSync(handle, header, 0, header.length, 0); } finally { io.closeSync(handle); }
  const size = io.statSync(candidate).size;
  if (header.toString('ascii') !== 'SQLite format 3\0' || size < 4096 || !summary || !summary.counts || ['items', 'accounts', 'reports', 'research_runs', 'jobs', 'drafts'].some(key => !Number.isSafeInteger(summary.counts[key]) || summary.counts[key] < 0) || !Number.isSafeInteger(summary.interrupted_jobs_count) || summary.interrupted_jobs_count < 0 || typeof summary.backup_file_name !== 'string') throw failure('prepare_output_invalid', '备份检查结果不完整。');
  if (summary.size_bytes !== size || summary.sha256 !== fileDigest(candidate, io)) throw failure('prepare_output_invalid', '候选数据库与检查结果不一致。');
  // No open database handle or committed WAL can be left behind by preflight.
  if (io.existsSync(candidate + '-wal') || io.existsSync(candidate + '-shm')) throw failure('prepare_output_invalid', '备份检查的数据库尚未正确关闭。');
  return candidate;
}

const fileDigest = (filename, io) => {
  const digest = crypto.createHash('sha256'), bytes = Buffer.alloc(1024 * 1024), handle = io.openSync(filename, 'r');
  try { let count; while ((count = io.readSync(handle, bytes, 0, bytes.length, null)) > 0) digest.update(bytes.subarray(0, count)); }
  finally { io.closeSync(handle); }
  return digest.digest('hex');
};

function matchesOriginal(filename, metadata, io = fs) {
  return io.existsSync(filename) && !io.lstatSync(filename).isSymbolicLink() && io.statSync(filename).isFile() && io.statSync(filename).size === metadata.size && fileDigest(filename, io) === metadata.sha256;
}

function completeOriginalArchive(source, destination, metadata, io = fs) {
  if (matchesOriginal(destination, metadata, io)) return;
  if (!matchesOriginal(source, metadata, io)) throw failure('restore_old_set_incomplete', '旧库归档补全的源文件不完整。');
  if (io.existsSync(destination)) {
    // A prior interrupted copy can leave an incomplete archive sibling. Keep
    // its bytes as evidence, then rebuild from the verified original source.
    io.renameSync(destination, destination + '.incomplete-' + crypto.randomUUID());
  }
  const temporary = destination + '.copy-' + crypto.randomUUID();
  io.copyFileSync(source, temporary, io.constants?.COPYFILE_EXCL || fs.constants.COPYFILE_EXCL);
  if (!matchesOriginal(temporary, metadata, io)) throw failure('restore_old_set_incomplete', '旧库归档临时副本核对失败，证据已保留。');
  io.renameSync(temporary, destination);
}

function writeJournal(root, record, io = fs) {
  const filename = path.join(root, journalName), temporary = filename + '.tmp';
  for (const target of [filename, temporary]) if (io.existsSync(target) && io.lstatSync(target).isSymbolicLink()) throw failure('restore_journal_invalid', '恢复事务日志路径无效，当前数据未改动。');
  const handle = io.openSync(temporary, 'w', 0o600);
  try { io.writeFileSync(handle, JSON.stringify(record, null, 2), 'utf8'); io.fsyncSync(handle); }
  finally { io.closeSync(handle); }
  io.renameSync(temporary, filename);
}

function readJournal(root, io = fs) {
  const filename = path.join(root, journalName);
  if (!io.existsSync(filename)) return null;
  if (io.lstatSync(filename).isSymbolicLink()) throw failure('restore_journal_invalid', '恢复事务日志路径无效，已阻止创建新数据库。');
  let record;
  try { record = JSON.parse(io.readFileSync(filename, 'utf8')); }
  catch (_) { throw failure('restore_journal_invalid', '恢复事务日志无法读取，已阻止创建新数据库。'); }
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (record.schema !== 1 || !uuid.test(record.txid || '') || !['swapping', 'candidate-starting', 'candidate-running', 'rolling-back', 'rolled-back'].includes(record.phase) || !Array.isArray(record.old_files) || typeof record.original_empty !== 'boolean' || !record.backend_terminal || (record.backend_pid !== null && (!Number.isSafeInteger(record.backend_pid) || record.backend_pid <= 0))) throw failure('restore_journal_invalid', '恢复事务日志无效，已阻止创建新数据库。');
  const names = new Set();
  for (const file of record.old_files) {
    if (!siblings.includes(file.name) || names.has(file.name) || !Number.isSafeInteger(file.size) || file.size < 0 || !/^[0-9a-f]{64}$/.test(file.sha256 || '')) throw failure('restore_journal_invalid', '旧库文件集合无效，已阻止创建新数据库。');
    names.add(file.name);
  }
  if (record.original_empty === names.has(siblings[0])) throw failure('restore_journal_invalid', '旧库状态无效，已阻止创建新数据库。');
  const check = (directory, group, pattern) => {
    if (typeof directory !== 'string') throw failure('restore_journal_invalid', '恢复事务目录无效。');
    const relative = path.relative(root, path.resolve(directory));
    const pieces = relative.split(path.sep);
    if (pieces.length !== 2 || pieces[0] !== group || !pattern.test(pieces[1])) throw failure('restore_journal_invalid', '恢复事务目录不在本应用数据文件夹，已阻止创建新数据库。');
    const parent = path.dirname(directory);
    if (!io.existsSync(parent) || path.relative(root, io.realpathSync(parent)) !== group || (io.existsSync(directory) && path.relative(parent, io.realpathSync(directory)) !== pieces[1])) throw failure('restore_journal_invalid', '恢复事务目录路径无效。');
  };
  check(record.stage, 'restore-staging', uuid);
  check(record.archive, 'restores', /^before-\d{17}-[0-9a-f-]{36}$/i);
  if (record.failed_archive) check(record.failed_archive, 'restores', /^failed-\d{17}-[0-9a-f-]{36}$/i);
  return record;
}

function completeRecovery(dataDir, txid, io = fs) {
  const root = canonicalOwnedRoot(dataDir, io), record = readJournal(root, io);
  if (record && record.txid !== txid) throw failure('restore_journal_invalid', '恢复事务已经变化，日志已保留。');
  if (record) io.unlinkSync(path.join(root, journalName));
  try { if (record) cleanupStage(root, record.stage, io); } catch (_) { /* closed stage can be inspected later */ }
}

function recoveryStarting(dataDir, txid, pid = null, io = fs) {
  const root = canonicalOwnedRoot(dataDir, io), record = readJournal(root, io);
  if (!record || record.txid !== txid) throw failure('restore_journal_invalid', '恢复事务已经变化，已阻止启动新服务。');
  record.phase = 'candidate-starting'; record.backend_pid = pid;
  writeJournal(root, record, io);
}

function recoverPendingRestore(dataDir, options = {}) {
  const io = options.fs || fs, root = canonicalOwnedRoot(dataDir, io), record = readJournal(root, io);
  if (!record) return null;
  if (record.backend_pid && options.processAlive?.(record.backend_pid)) throw failure('restore_process_alive', `上次恢复的服务进程仍在运行，已阻止覆盖数据库。\n旧库保留位置：${record.archive}`);
  if (record.phase === 'candidate-starting' && !record.backend_pid) throw failure('restore_process_unknown', `上次恢复在启动服务时中断，无法确认子进程已经退出。已阻止创建新数据库。\n旧库保留位置：${record.archive}`);
  // Validate the complete old set before making another mutation. An old file
  // may still be current if the desktop crashed halfway through the move loop.
  const oldLocations = new Map();
  const matches = (filename, metadata) => matchesOriginal(filename, metadata, io);
  for (const metadata of record.old_files) {
    const archived = path.join(record.archive, metadata.name), current = path.join(root, metadata.name);
    if (matches(archived, metadata)) oldLocations.set(metadata.name, archived);
    else if (matches(current, metadata)) oldLocations.set(metadata.name, current);
    else throw failure('restore_old_set_incomplete', `上次恢复的旧库文件不完整，已阻止创建新数据库。\n旧库保留位置：${record.archive}`);
  }
  // Complete a partially moved old archive before any restored service can
  // checkpoint or modify files that had still been current at the crash.
  for (const metadata of record.old_files) {
    const archived = path.join(record.archive, metadata.name);
    completeOriginalArchive(oldLocations.get(metadata.name), archived, metadata, io);
    if (!matches(archived, metadata)) throw failure('restore_old_set_incomplete', '旧库归档核对失败，已阻止启动服务。');
    oldLocations.set(metadata.name, archived);
  }
  const currentCandidateFiles = siblings.filter(name => {
    const current = path.join(root, name), old = record.old_files.find(item => item.name === name);
    return io.existsSync(current) && (!old || !matches(current, old));
  });
  if (currentCandidateFiles.length) {
    // A second interrupted rollback can leave a partial old copy at current.
    // Always retain that generation separately; never overwrite a candidate
    // preserved by a previous attempt in its existing failed archive.
    record.failed_archive = ownedDirectory(root, 'restores', 'failed-' + new Date().toISOString().replace(/[^0-9]/g, '') + '-' + crypto.randomUUID(), io);
    record.phase = 'rolling-back';
    writeJournal(root, record, io);
    for (const name of currentCandidateFiles) io.renameSync(path.join(root, name), path.join(record.failed_archive, name));
  }
  for (const metadata of record.old_files) {
    const current = path.join(root, metadata.name);
    if (!io.existsSync(current)) io.copyFileSync(oldLocations.get(metadata.name), current, io.constants?.COPYFILE_EXCL || fs.constants.COPYFILE_EXCL);
    if (!matches(current, metadata)) throw failure('restore_old_set_incomplete', '旧库回退校验失败，恢复日志及旧库已保留。');
  }
  record.phase = 'rolled-back'; record.backend_pid = null;
  writeJournal(root, record, io);
  return { recovered: true, txid: record.txid, archive_path: record.archive, failed_archive_path: record.failed_archive || null, original_empty: record.original_empty };
}

async function restoreBackup(options) {
  const io = options.fs || fs;
  const root = canonicalOwnedRoot(options.dataDir, io);
  if (readJournal(root, io)) throw failure('restore_pending', '上次恢复事务尚未完成。请重新打开应用，让服务先回退并核对旧数据库。');
  const selected = await options.chooseSource();
  if (!selected || options.quitting?.()) return { cancelled: true };
  const source = rejectCurrentSource(selected, root, io);
  let stage = null, prepareTerminal = true, archive = null, failedArchive = null;
  let stopAttempted = false, candidateInstalled = false, journal = null;
  const moved = [];
  const timestamp = () => new Date().toISOString().replace(/[^0-9]/g, '') + '-' + crypto.randomUUID();
  const startJournaledBackend = async () => {
    if (journal) {
      // Persist the ambiguous spawn window before creating a process, including
      // when this is the original service after a rollback. A desktop crash
      // before the synchronous onSpawn hook must never look safe to recover.
      journal.phase = 'candidate-starting'; journal.backend_pid = null;
      writeJournal(root, journal, io);
    }
    await options.start(pid => {
      if (journal) { journal.phase = 'candidate-starting'; journal.backend_pid = pid; writeJournal(root, journal, io); }
    });
    if (journal) { journal.phase = 'candidate-running'; writeJournal(root, journal, io); }
  };
  try {
    stage = ownedDirectory(root, 'restore-staging', crypto.randomUUID(), io);
    let summary;
    try { summary = await options.prepare(source, stage); }
    catch (error) { prepareTerminal = error.process_terminal !== false; throw error; }
    const candidate = validateCandidate(stage, summary, io);
    if (options.quitting?.()) return { cancelled: true };
    if (!await options.confirm(summary) || options.quitting?.()) return { cancelled: true };
    stopAttempted = true;
    await options.stop();
    if (!options.terminal()) throw failure('backend_alive', '本地服务尚未退出，当前数据库未替换。');
    archive = ownedDirectory(root, 'restores', 'before-' + timestamp(), io);
    const oldFiles = siblings.filter(name => io.existsSync(path.join(root, name))).map(name => {
      const current = path.join(root, name);
      if (io.lstatSync(current).isSymbolicLink() || !io.statSync(current).isFile()) throw failure('restore_path_invalid', '当前数据库路径无效，恢复未执行。');
      return { name, size: io.statSync(current).size, sha256: fileDigest(current, io) };
    });
    journal = { schema: 1, txid: crypto.randomUUID(), phase: 'swapping', archive, stage, old_files: oldFiles, original_empty: !oldFiles.some(file => file.name === siblings[0]), backend_terminal: true, backend_pid: null };
    writeJournal(root, journal, io);
    for (const name of siblings) {
      const current = path.join(root, name);
      if (io.existsSync(current)) { io.renameSync(current, path.join(archive, name)); moved.push(name); }
    }
    io.renameSync(candidate, path.join(root, siblings[0]));
    candidateInstalled = true;
    await startJournaledBackend();
    await options.navigate();
    completeRecovery(root, journal.txid, io);
    return { restored: true, archive_path: archive, summary };
  } catch (original) {
    const errors = [];
    let terminal = options.terminal();
    if (candidateInstalled) {
      try { await options.stop(); terminal = options.terminal(); }
      catch (error) { errors.push('新服务退出失败：' + error.message); terminal = options.terminal(); }
    }
    if (journal && (moved.length || candidateInstalled)) {
      if (!terminal) errors.push('服务进程仍在运行，已保留现库和旧库，未尝试覆盖运行中的数据库');
      else {
        if (candidateInstalled) {
          try { failedArchive = ownedDirectory(root, 'restores', 'failed-' + timestamp(), io); }
          catch (error) { errors.push('失败数据库保留目录创建失败：' + error.message); }
          if (failedArchive) {
            journal.failed_archive = failedArchive;
            journal.phase = 'rolling-back'; journal.backend_pid = null;
            try { writeJournal(root, journal, io); } catch (error) { errors.push('恢复日志更新失败：' + error.message); }
            for (const name of siblings) {
              const current = path.join(root, name);
              try { if (io.existsSync(current)) io.renameSync(current, path.join(failedArchive, name)); }
              catch (error) { errors.push('失败数据库保留失败 ' + name + '：' + error.message); }
            }
          }
        }
        // Complete the original archive even if the failure interrupted its
        // first move loop. Remaining current sidecars are still original.
        for (const metadata of journal.old_files) {
          const archived = path.join(archive, metadata.name), current = path.join(root, metadata.name);
          try {
            completeOriginalArchive(current, archived, metadata, io);
          } catch (error) { errors.push('旧库归档补全失败 ' + metadata.name + '：' + error.message); }
        }
        for (const metadata of journal.old_files) {
          const name = metadata.name;
          try {
            const current = path.join(root, name);
            if (io.existsSync(current)) { if (fileDigest(current, io) !== metadata.sha256) throw new Error('目标文件仍存在，不能安全覆盖'); }
            else io.copyFileSync(path.join(archive, name), current, io.constants?.COPYFILE_EXCL || fs.constants.COPYFILE_EXCL);
          } catch (error) { errors.push('旧库回退失败 ' + name + '：' + error.message); }
        }
        if (!errors.length) {
          try { await startJournaledBackend(); await options.navigate(); completeRecovery(root, journal.txid, io); }
          catch (error) { errors.push('旧服务重新启动失败：' + error.message); }
        }
      }
    } else if (stopAttempted && terminal && !options.quitting?.()) {
      try { await startJournaledBackend(); await options.navigate(); if (journal) completeRecovery(root, journal.txid, io); }
      catch (error) { errors.push('原服务重新启动失败：' + error.message); }
    }
    const retained = archive ? `\n旧库保留位置：${archive}` : '';
    const failed = failedArchive ? `\n失败数据库保留位置：${failedArchive}` : '';
    const recovered = archive && !errors.length ? '\n已恢复原数据库并重新启动服务。' : '';
    throw failure(original.code || 'restore_failed', `恢复失败：${original.message}${recovered}${errors.length ? '\n' + errors.join('\n') : ''}${retained}${failed}`, { archive_path: archive, failed_archive_path: failedArchive, rollback_errors: errors, process_terminal: prepareTerminal });
  } finally {
    if (prepareTerminal && (!journal || !io.existsSync(path.join(root, journalName)))) {
      try { cleanupStage(root, stage, io); }
      catch (error) { options.log?.('Restore staging retained: ' + error.message); }
    }
  }
}

module.exports = { createLifecycleCoordinator, restoreBackup, runPrivateJson, terminatePrivateChild, waitForExit, live, ownedDirectory, cleanupStage, rejectCurrentSource, recoverPendingRestore, completeRecovery, recoveryStarting, writeJournal, readJournal };
