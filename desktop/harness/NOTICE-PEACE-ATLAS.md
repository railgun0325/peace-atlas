# Restricted Harness runtime distribution notice

PEACE ATLAS includes unmodified public npm runtime packages from DeepSeek
Harness **dsh-v0.2.0-rc.2** and a product-specific restricted model/tool relay.
The relay is authored for this product. Upstream:
https://github.com/deepseek-ai/deepseek-harness/tree/dsh-v0.2.0-rc.2

DeepSeek Harness's root MIT license is retained in `LICENSE-DSH.txt`; the official
third-party notice is retained in `NOTICE-DSH.md`. Every copied npm package keeps
its distributed license files, package metadata and copyright notices. Package
versions and their declared licenses are inventoried in `runtime-manifest.json`.
The lockfile and extra native tarball URLs/integrities document the public source.

The native system/file-lock packages use BSD-3-Clause; their package license files
are retained. Koffi and the require-builtin native loader carry their own MIT
licenses. No optional LibreOffice, speech, image-processing, browser, terminal,
Claude Code payload, or filesystem tool runtime is distributed in this closure.
The official broader notice describes the upstream distribution and is not a
claim that those optional components are included here. Upstream names do not
imply endorsement of PEACE ATLAS.
