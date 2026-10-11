# PEACE ATLAS restricted DSH sidecar

This directory contains the public DeepSeek Harness SDK client and CLI runtime,
both pinned to **0.2.0-rc.2**, plus a host model/tool relay. This is an actual SDK
stdio subprocess integration. It is not a reimplementation of the agent loop.
The upstream API is prerelease; upgrades require a new audited closure and test.

The Python report worker launches `sidecar.mjs` using its application's Electron
executable with `ELECTRON_RUN_AS_NODE=1`. The SDK launches the same `process.execPath`
with its JS CLI entry. No system Node or user's DSH Desktop installation is used.
The host must supply a fresh private current working directory for every run.
All home/profile/session/temp/workspace files are written under that directory;
the installed runtime directory is read only during a run.

Only these native research tools are registered: `list_evidence`, `read_evidence`,
`inspect_account`, `trace_observed_path`, `prioritize_frontier`,
`compare_evidence`, and `check_citation`. Their exact argument schemas and strict
flags come from the Python gateway. The gateway independently validates every
call and only projects its bound saved public evidence snapshot. There is no
remote source opening in this version. The public `sdk-minimal` profile's shell,
subprocess, sandbox, terminal, direct provider, title-model, retry and MCP resource
plugins are disabled before the research plugin is loaded. The runtime reports
its actual roster before a model request. Unknown names and invalid schemas fail
before the host tool is called. A model tool request is not evidence of success;
the host trace and quote/source/identity guards remain authoritative.

## JSON lines protocol, version 1

Host to sidecar:

- `start`: `protocol`, `session_id`, `run_binding:{run_id,revision,snapshot_digest}`,
  `goal`, `system`, `model:{id,context_window,max_output_tokens,reasoning_effort?}`,
  `tool_schemas:[{name,description,parameters,strict?}]`,
  `budget:{max_model_calls,max_tool_calls,wall_seconds}`. `wall_seconds` is a finite
  positive number, including a fractional value. Every tool schema is an object
  with `additionalProperties:false`, and the roster must contain exactly the
  seven names above. One start is accepted per process.
- `model_response:{id,response}`: a nonstream standard chat completion containing
  exactly one assistant choice, `finish_reason`, optional `usage`. Text,
  `reasoning_content`, and function `tool_calls` are translated to DSH chunks.
- `tool_response:{id,result}`: JSON returned by the validated host gateway.
- `response_error:{id,code?,message?}`: rejects that request with a fixed internal
  error; supplied exception text is not logged or forwarded to the runtime.
- `cancel:{reason?}`: closes the owned SDK runtime. EOF also closes it.

Sidecar to host:

- `runtime_roster:{names,runtime}`: runtime-observed names and runtime version.
- `model_request:{id,run_binding,request}`: ordinary OpenAI-shaped request with
  `model,messages,tools` and bounded generation options. It contains no `stream`
  flag and preserves the host's exact tool definitions. DSH tool results use the
  standard `tool` role and `tool_call_id`; assistant reasoning is retained for
  the model broker's provider-specific normal path.
- `tool_request:{id,run_binding,name,args}`: one allowed host call.
- `checkpoint:{session_id,event_seq,sessionId,event}`: public SDK session event.
  Events can contain internal reasoning. The host stores its independently
  validated evidence trace rather than displaying or persisting raw reasoning.
- `request_cancelled:{id}`: an outstanding relay request was aborted. The host
  must fail closed and retain its validated checkpoint.
- `complete:{protocol,session_id,run_binding,runtime_closed,model_calls,tool_calls,
  finish_reason:"stop",original_reason:"completed",final_response,events_count}`:
  emitted only after the real SDK child has been closed. `final_response` remains
  untrusted until the normal report guards validate it.
- `cancelled` with `reason`, or `error` with fixed `code`, share protocol/session,
  binding, counters and `runtime_closed`. Non-completed SDK turns are errors,
  including truncated model output. Unknown wire kinds or response IDs fail
  closed. Cleanup failure reports `runtime_closed:false` and exit status 1.

Request IDs are opaque, single-use and matched by kind. The private in-memory
loopback relay uses a fresh capability, never a model credential. The sidecar
does not inherit the application environment, provider keys, debug flags or DSH
profiles. Its child environment contains only OS launching values, owned paths,
disabled telemetry, `ELECTRON_RUN_AS_NODE=1`, and the private relay capability.
Standard output is only protocol JSON. No provider API is called by the sidecar.

## Packaging and evidence boundaries

`runtime-manifest.json` lists exact hashes, sizes, package versions, platform
selectors, downloaded native artifact provenance, and the closure digest.
`desktop/build.py` checks the complete source inventory and includes the matching
target's closure in the application build manifest. Npm tests and unsupported CLI
profile dependencies are excluded. Both full npm provenance files are retained;
they describe the original public install, not a promise that every CLI feature
is distributed. No installing or dependency resolution is needed at runtime.

Windows x64 requires the `node-addon-require-builtin` 0.1.9 native loader and Koffi
3.1.1 semaphore binding for session persistence. Apple Silicon packages include
the separate 0.1.9 Darwin loader and `@deepseek-ai/node-addon-system` 0.1.2 Darwin
file-lock binding. They use Node-API, but **native macOS execution is untested**.
Mac x64 is deliberately not listed as a packaged target.

Offline acceptance used a deterministic host model and synthetic public evidence
through the actual Python gateway and real Electron/SDK child. It verifies tool
round trips, exact schemas, denied shell/file names and unknown arguments, Unicode
and reasoning preservation, checkpoints, cancellation, EOF, bad wire input,
truncation, budgets and child reaping. It does not establish a paid model run,
real source correctness, native Mac launch, or final report acceptance.

Licenses: see `LICENSE-DSH.txt`, `NOTICE-DSH.md`, `NOTICE-PEACE-ATLAS.md`, and the
unmodified license files within each distributed package.
