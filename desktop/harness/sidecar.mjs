// Python-facing coordinator around the public same-version DSH SDK/runtime.
// All credentials remain in the host broker; only this run capability travels
// to the DSH child environment. stdout is JSON-lines; stderr carries fixed codes.
import { createServer } from 'node:http';
import { randomBytes, randomUUID } from 'node:crypto';
import { createInterface } from 'node:readline';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { TOOL_NAMES } from './tool_names.mjs';
import { armDeadline } from './deadline_timer.mjs';
const directory = path.dirname(fileURLToPath(import.meta.url));
const runtimeRoot = path.join(directory, 'node_modules');
const { DeepSeekHarness } = await import(pathToFileURL(path.join(runtimeRoot, '@deepseek-ai/dsh-sdk-client/lib/index.js')).href);
const sdkPackage = JSON.parse(await readFile(path.join(runtimeRoot, '@deepseek-ai/dsh-sdk-client/package.json'), 'utf8'));
const dshPackage = JSON.parse(await readFile(path.join(runtimeRoot, '@deepseek-ai/dsh/package.json'), 'utf8'));
if (sdkPackage.version !== '0.2.0-rc.2' || dshPackage.version !== sdkPackage.version) throw new Error('pinned_runtime_version_mismatch');
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
let start, sdk, server, stopped = false, finalSent = false, timer, pending = new Map(), modelCalls = 0, toolCalls = 0;
const send = data => { if (!process.stdout.destroyed) process.stdout.write(JSON.stringify(data) + '\n'); };
const allowed = new Set(TOOL_NAMES);
async function closeOwned() {
  stopped = true; timer?.();
  for (const [, entry] of pending) entry.reject(new Error('owned_run_stopped'));
  pending.clear();
  if (sdk) await sdk.close();
  if (server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}
async function finish(kind, data = {}) {
  if (finalSent) return; finalSent = true;
  try { await closeOwned(); send({ kind, protocol: 1, session_id: start?.session_id, model_calls: modelCalls, tool_calls: toolCalls, runtime_closed: true, run_binding: start?.run_binding, ...data }); }
  catch { send({ kind: 'error', protocol: 1, code: 'runtime_cleanup_failed', session_id: start?.session_id, run_binding: start?.run_binding, runtime_closed: false }); process.exitCode = 1; }
  input.close(); process.stdin.pause();
}
const askHost = (kind, data, response) => new Promise((resolve, reject) => {
  const id = randomUUID();
  pending.set(id, { kind, resolve, reject });
  response.on('close', () => { if (!response.writableEnded && pending.has(id)) { pending.delete(id); reject(new Error('relay_request_cancelled')); send({ kind: 'request_cancelled', id }); } });
  send({ kind: kind + '_request', id, run_binding: start.run_binding, ...data });
});
const reviewCandidate = finalResponse => new Promise((resolve, reject) => {
  const id = randomUUID();
  pending.set(id, { kind: 'candidate', resolve, reject });
  send({ kind: 'final_candidate', protocol: 1, id, session_id: start.session_id,
    run_binding: start.run_binding, final_response: finalResponse });
});
async function run(config) {
  if (config.protocol !== 1 || !/^[A-Za-z0-9_-]{1,120}$/.test(config.session_id || '') || !config.run_binding?.snapshot_digest
    || typeof config.goal !== 'string' || typeof config.system !== 'string' || !config.model?.id
    || !Number.isSafeInteger(config.model.context_window) || !Number.isSafeInteger(config.model.max_output_tokens)
    || !Array.isArray(config.tool_schemas) || config.tool_schemas.length !== allowed.size
    || new Set(config.tool_schemas.map(t => t.name)).size !== allowed.size
    || config.tool_schemas.some(t => !allowed.has(t.name) || t.parameters?.type !== 'object' || t.parameters?.additionalProperties !== false)
    || !Number.isSafeInteger(config.budget?.max_model_calls) || config.budget.max_model_calls < 0
    || !Number.isSafeInteger(config.budget?.max_tool_calls) || config.budget.max_tool_calls < 0
    || !Number.isFinite(config.budget?.wall_seconds) || config.budget.wall_seconds < 0) throw new Error('invalid_start');
  start = config;
  const capability = randomBytes(32).toString('hex');
  server = createServer(async (request, response) => {
    try {
      if (request.method !== 'POST' || request.headers['x-peace-capability'] !== capability || stopped) { response.writeHead(403).end(); return; }
      let chunks = [], bytes = 0;
      for await (const chunk of request) { bytes += chunk.length; if (bytes > 16 * 1024 * 1024) throw new Error('relay_input_too_large'); chunks.push(chunk); }
      const data = JSON.parse(Buffer.concat(chunks).toString('utf8')); let result;
      if (request.url === '/bootstrap') result = { model: start.model, tool_schemas: start.tool_schemas };
      else if (request.url === '/roster') { if (!Array.isArray(data.names) || data.names.length !== allowed.size || data.names.some(n => !allowed.has(n))) throw new Error('unsafe_roster'); send({ kind: 'runtime_roster', names: data.names, runtime: dshPackage.version }); result = {}; }
      else if (request.url === '/model') { if (start.budget.max_model_calls && modelCalls >= start.budget.max_model_calls) throw new Error('model_budget_exhausted'); modelCalls++; result = await askHost('model', data, response); }
      else if (request.url === '/tool') { if (!allowed.has(data.name) || (start.budget.max_tool_calls && toolCalls >= start.budget.max_tool_calls)) throw new Error('tool_budget_or_name_refused'); toolCalls++; result = await askHost('tool', data, response); }
      else throw new Error('unknown_relay_route');
      response.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(result));
    } catch { if (!response.writableEnded) response.writeHead(400).end('{"error":"host_relay_failed"}'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const owned = path.join(process.cwd(), 'owned-runs', start.session_id);
  const home = path.join(owned, 'dsh-home'), workspace = path.join(owned, 'workspace');
  for (const folder of [home, workspace, path.join(owned, 'temp'), path.join(owned, 'user'), path.join(owned, 'appdata')]) await mkdir(folder, { recursive: true });
  const patch = path.join(owned, 'evidence-only.patch.yml');
  const disabled = ['llm-deepseek', 'deepseek-llm-api-extensions', 'session-log-deepseek', 'plugin-package-inventory-deepseek',
    'sandbox', 'sandbox-policy', 'subprocess', 'pty', 'terminal-bash', 'terminal-pwsh', 'persistent-bash', 'persistent-pwsh', 'session-title', 'mcp-resources', 'llm-retry'];
  const patchRows = [...disabled.map(id => ({ id, disabled: true })),
    { id: 'system-prompt', config: { includeHarnessIdentity: false, includeRuntimeContext: false, personaPrefix: start.system } },
    { insert: [{ id: 'peace-native-relay', name: pathToFileURL(path.join(directory, 'peace_relay_native_plugin.mjs')).href }] }];
  await writeFile(patch, JSON.stringify(patchRows, null, 2), 'utf8');
  const env = { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, ComSpec: process.env.ComSpec,
    TEMP: path.join(owned, 'temp'), TMP: path.join(owned, 'temp'), USERPROFILE: path.join(owned, 'user'),
    HOME: path.join(owned, 'user'), APPDATA: path.join(owned, 'appdata'), LOCALAPPDATA: path.join(owned, 'appdata'),
    ELECTRON_RUN_AS_NODE: '1', NO_COLOR: '1', DSH_HOME: home, DSH_TELEMETRY_MODE: 'DISABLED', PEACE_RUNTIME_MODULE_ROOT: runtimeRoot,
    PEACE_RELAY_URL: 'http://127.0.0.1:' + server.address().port, PEACE_RELAY_CAPABILITY: capability };
  sdk = new DeepSeekHarness({ profile: 'sdk-minimal', dshHome: home, processCwd: workspace, cwd: workspace,
    dshBin: path.join(runtimeRoot, '@deepseek-ai/dsh/lib/bin.js'), patches: [patch], env,
    provider: 'peace-atlas', model: start.model.id, maxTokens: start.model.max_output_tokens,
    initializeTimeoutMs: 30000, disposeEofGraceMs: 6000, disposeGraceMs: 3000 });
  timer = armDeadline(start.budget.wall_seconds, () => { void finish('cancelled', { reason: 'wall_budget' }); });
  const runOptions = { sessionId: start.session_id, onNotification: notification => {
    if (notification.method === 'session.event') { const params = notification.params; send({ kind: 'checkpoint', session_id: start.session_id, event_seq: params.event?.seq, ...params }); }
  } };
  let prompt = start.goal, result, originalReason, repairs = 0;
  while (true) {
    result = await sdk.run(prompt, runOptions);
  const ended = result.events.filter(e => e.type === 'turn/end').at(-1);
    originalReason = ended?.data?.reason?.kind ?? null;
    if (originalReason !== 'completed') break;
    const review = await reviewCandidate(result.finalResponse);
    if (review.accepted) break;
    if (++repairs > 2) throw new Error('final_repair_limit');
    prompt = review.feedback;
  }
  await finish(originalReason === 'completed' ? 'complete' : 'error', {
    ...(originalReason === 'completed' ? { finish_reason: 'stop' } : { code: 'runtime_turn_incomplete' }),
    original_reason: originalReason, final_response: result.finalResponse,
    run_binding: start.run_binding, events_count: result.events.length });
}
input.on('line', line => {
  try {
    if (line.length > 16 * 1024 * 1024) throw new Error('wire_input_too_large');
    const value = JSON.parse(line);
    if (value.kind === 'start' && !start) { start = value; void run(value).catch(() => { void finish('error', { code: 'sdk_run_failed' }); }); return; }
    if (value.kind === 'cancel' && start) { void finish('cancelled', { reason: 'host_cancel' }); return; }
    if (value.kind === 'candidate_response') {
      const request = pending.get(value.id);
      if (!request || request.kind !== 'candidate' || value.session_id !== start.session_id
        || JSON.stringify(value.run_binding) !== JSON.stringify(start.run_binding)
        || typeof value.accepted !== 'boolean'
        || (value.accepted ? value.feedback !== null : typeof value.feedback !== 'string' || value.feedback.length > 800)) throw new Error('invalid_candidate_review');
      pending.delete(value.id); request.resolve(value); return;
    }
    if (!['model_response', 'tool_response', 'response_error'].includes(value.kind)) throw new Error('unknown_wire_kind');
    const request = pending.get(value.id); if (!request) throw new Error('unknown_response_id'); pending.delete(value.id);
    if (value.kind === 'response_error') request.reject(new Error('host_response_error'));
    else if ((request.kind === 'model' && value.kind === 'model_response') || (request.kind === 'tool' && value.kind === 'tool_response')) request.resolve(request.kind === 'model' ? value.response : value.result);
    else throw new Error('response_kind_mismatch');
  } catch { void finish('error', { code: 'wire_protocol_refused' }); }
});
input.on('close', () => { if (!finalSent) void finish('cancelled', { reason: 'host_eof' }); });
process.on('SIGINT', () => { void finish('cancelled', { reason: 'signal' }); });
process.on('SIGTERM', () => { void finish('cancelled', { reason: 'signal' }); });
