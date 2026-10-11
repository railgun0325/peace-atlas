import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { toOpenAIRequest, completionToChunks } from './openai_translation.mjs';
const root = process.env.PEACE_RUNTIME_MODULE_ROOT;
const { LlmAdapter } = await import(pathToFileURL(path.join(root, '@deepseek-ai/dsh-llm/lib/index.js')).href);
const { validateJsonSchemaValue } = await import(pathToFileURL(path.join(root, '@deepseek-ai/dsh-tools/lib/index.js')).href);
export const name = 'peace-native-model-tool-relay';
export const inject = ['llm', 'tools'];
export async function apply(ctx) {
  const endpoint = process.env.PEACE_RELAY_URL;
  if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(endpoint)) throw new Error('invalid_owned_relay');
  const invoke = async (kind, data, signal) => {
    const response = await fetch(endpoint + '/' + kind, { method: 'POST',
      headers: { 'content-type': 'application/json', 'x-peace-capability': process.env.PEACE_RELAY_CAPABILITY },
      body: JSON.stringify(data), signal });
    if (!response.ok) throw new Error('host_relay_refused');
    return response.json();
  };
  const setup = await invoke('bootstrap', {});
  const allowed = new Set(setup.tool_schemas.map(t => t.name));
  ctx.tools.guard(exec => allowed.has(exec.name) ? undefined : 'Tool not in this run evidence allowlist.');
  for (const schema of setup.tool_schemas) {
    ctx.tools.register({ name: schema.name, description: schema.description, parameters: schema.parameters,
      output: { schema: {}, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute(args, exec) {
        const issues = validateJsonSchemaValue(schema.parameters, args);
        if (issues.length) throw new Error('host_tool_arguments_invalid');
        return invoke('tool', { name: schema.name, args }, exec.signal);
      } });
  }
  class HostModelRelay extends LlmAdapter {
    async resolveModel(provider, model) {
      if (provider !== 'peace-atlas' || model !== setup.model.id) throw new Error('unowned_model_route');
      return { provider, id: model, name: model, inputModalities: ['text'],
        context: { contextWindow: setup.model.context_window }, defaultMaxTokens: setup.model.max_output_tokens };
    }
    async * stream(options) {
      const names = (options.tools || []).map(t => t.name);
      if (names.length !== allowed.size || names.some(n => !allowed.has(n))) throw new Error('unsafe_runtime_tool_roster');
      const request = toOpenAIRequest(options, setup.model);
      // Preserve the host's exact JSON schemas, including strict flags.
      request.tools = setup.tool_schemas.map(schema => ({ type: 'function', function: schema }));
      const completion = await invoke('model', { request }, options.signal);
      for (const chunk of completionToChunks(completion)) { if (options.signal?.aborted) throw new Error('host_model_cancelled'); yield chunk; }
    }
  }
  ctx.llm.registerAdapter(['peace-atlas'], new HostModelRelay());
  // Runtime-observed list, delivered through the owned authenticated broker.
  await invoke('roster', { names: ctx.tools.schemas().map(t => t.name) });
}
