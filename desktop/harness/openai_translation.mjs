// Maps the public DSH text/tool vocabulary to standard chat completions.
// Reference: DeepSeek Harness dsh-v0.2.0-rc.2 llm-deepseek serialize/translate
// (MIT); this bounded text-only adapter rejects unsupported modalities.
export function toOpenAIRequest(options, defaults = {}) {
  const messages = [];
  const text = blocks => blocks.map(b => { if (b.type !== 'text' || typeof b.text !== 'string') throw new Error('unsupported_tool_content'); return b.text; }).join('');
  if (options.system !== undefined) messages.push({ role: 'system', content: options.system });
  for (const message of options.messages || []) {
    if (!['system', 'user', 'assistant', 'tool'].includes(message.role)) throw new Error('unsupported_message_role');
    const blocks = message.content || [];
    for (const block of blocks) if (!['text', 'reasoning', 'tool-call', 'tool-result'].includes(block.type)) throw new Error('unsupported_content_block');
    const content = text(blocks.filter(b => b.type === 'text'));
    if (message.role === 'tool') {
      if (!message.toolCallId || blocks.some(b => b.type !== 'text')) throw new Error('invalid_tool_message');
      messages.push({ role: 'tool', tool_call_id: message.toolCallId, content: content || '(no output)' });
    } else if (message.role === 'assistant') {
      const reasoning = blocks.filter(b => b.type === 'reasoning').map(b => b.text).join('');
      const calls = blocks.filter(b => b.type === 'tool-call').map(b => ({ id: b.id, type: 'function', function: { name: b.name, arguments: b.arguments } }));
      messages.push({ role: 'assistant', content, ...(reasoning ? { reasoning_content: reasoning } : {}), ...(calls.length ? { tool_calls: calls } : {}) });
    } else {
      if (blocks.some(b => ['reasoning', 'tool-call'].includes(b.type))) throw new Error('invalid_nonassistant_block');
      const results = blocks.filter(b => b.type === 'tool-result');
      if (content || !results.length) messages.push({ role: message.role, content });
      for (const result of results) messages.push({ role: 'tool', tool_call_id: result.toolCallId, content: text(result.content) || '(no output)' });
    }
  }
  const request = { model: options.model, messages,
    ...(options.tools?.length ? { tools: options.tools.map(t => ({ type: 'function', function: { name: t.name, description: t.description, parameters: t.parameters } })) } : {}),
    ...(options.maxTokens !== undefined ? { max_tokens: options.maxTokens } : {}),
    ...(options.temperature !== undefined ? { temperature: options.temperature } : {}) };
  const effort = options.reasoningEffort ?? defaults.reasoning_effort;
  if (effort !== undefined) request.reasoning_effort = effort;
  return request;
}

export function completionToChunks(response) {
  if (!response || !Array.isArray(response.choices) || response.choices.length !== 1) throw new Error('invalid_completion_choices');
  const { message, finish_reason: reason } = response.choices[0];
  if (!message || message.role !== 'assistant' || !['stop', 'tool_calls', 'length', 'content_filter'].includes(reason)) throw new Error('invalid_completion_finish');
  const chunks = [];
  let index = 0;
  for (const [key, type] of [['reasoning_content', 'reasoning'], ['content', 'text']]) {
    if (message[key] === null || message[key] === undefined || message[key] === '') continue;
    if (typeof message[key] !== 'string') throw new Error('invalid_completion_content');
    const value = message[key];
    chunks.push({ type: 'block-start', index, blockType: type }, { type: type === 'text' ? 'text-delta' : 'reasoning-delta', index, text: value },
      { type: 'block-end', index, block: { type, text: value } }); index++;
  }
  for (const call of message.tool_calls || []) {
    if (!call.id || call.type !== 'function' || !call.function?.name || typeof call.function.arguments !== 'string') throw new Error('invalid_completion_tool_call');
    chunks.push({ type: 'block-start', index, blockType: 'tool-call' },
      { type: 'tool-call-delta', index, id: call.id, name: call.function.name, argumentsDelta: call.function.arguments },
      { type: 'block-end', index, block: { type: 'tool-call', id: call.id, name: call.function.name, arguments: call.function.arguments } }); index++;
  }
  if (response.usage) {
    const u = response.usage, hit = u.prompt_cache_hit_tokens || 0;
    chunks.push({ type: 'usage', usage: { inputTokens: Math.max(0, (u.prompt_tokens || 0) - hit), outputTokens: u.completion_tokens || 0, ...(hit ? { cacheReadTokens: hit } : {}) } });
  }
  const finish = reason === 'stop' ? { kind: 'stop' } : reason === 'tool_calls' ? { kind: 'tool-calls' } : reason === 'length' ? { kind: 'max-tokens' } : { kind: 'error', failure: { code: 'CONTENT_FILTER', message: 'model response filtered' } };
  chunks.push({ type: 'finish', reason: finish });
  return chunks;
}
