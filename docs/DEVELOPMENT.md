# 开发与构建

PEACE ATLAS 的源码运行、测试和桌面构建说明。成品下载安装与操作流程见[项目首页](../README.md)和[操作说明书](USER_MANUAL_20261010_AGENT.md)。

## 从源码启动

建议使用 Python 3.12，在仓库根目录创建独立虚拟环境。源码工作台使用本地 SQLite；下面的启动链路直接加载 V2 后端。Chromium 用于采集和会话验证，用户登录入口仍由应用引导到系统浏览器。

Windows PowerShell：

```powershell
python -m venv .venv-v2
.\.venv-v2\Scripts\python.exe -m pip install -r desktop/requirements-runtime.txt
.\.venv-v2\Scripts\python.exe -m playwright install chromium
.\.venv-v2\Scripts\python.exe scripts/run_workbench.py
```

macOS / Linux：

```bash
python3 -m venv .venv-v2
.venv-v2/bin/python -m pip install -r desktop/requirements-runtime.txt
.venv-v2/bin/python -m playwright install chromium
.venv-v2/bin/python scripts/run_workbench.py
```

访问 [http://127.0.0.1:18090](http://127.0.0.1:18090)，按 `Ctrl+C` 停止。可追加 `--port 18091` 更换端口，或 `--data ./output/development.sqlite3` 使用独立测试库。源码运行 DSH 工具分析还需可执行的 Node.js；运行时自动查找 `node`，也可用 `WORKBENCH_HARNESS_NODE` 指定解释器路径。桌面成品已包含相应运行时。

开发启动器只从本地 `.env` 补充 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL` 和 `DEEPSEEK_TIMEOUT_SECONDS`，进程环境变量优先。真实 `.env`、AI 密钥、社媒会话、数据库及个人导出文件都应保留在本机，不进入源代码提交或发布包。默认源码数据库为 `analysis-worker/data/workbench.sqlite3`；桌面数据位置可在“能力与设置 → 打开数据目录”查询。研究备份与会话、密钥分别管理，操作方法见说明书。

## 开发验证与桌面构建

从仓库根目录运行，使用虚拟环境时将 `python` 换成该环境的解释器：

```powershell
python -m pip install -r analysis-worker/requirements.txt -r desktop/requirements-runtime.txt
python -m pytest -q analysis-worker/tests
node --test analysis-worker/app/static/workbench/tests/*.test.mjs
node --test desktop/tests/*.test.cjs
python -m unittest discover -s desktop/tests -p test_build.py
```

外部 Agent 菜单与可选浏览器桥接另有独立检查入口：

```powershell
node --test desktop/tests/test_agent_menu.cjs
node --test browser-extension/tests/*.test.mjs
```

构建前需可用的开发 Python 和 pip，构建器会下载并校验固定运行时。两平台默认产出 Windows x64 与 Apple Silicon macOS：

```powershell
python desktop/build.py --prepare-only
python desktop/build.py --target all
```

也可用 `--target windows-x64` 或 `--target macos-arm64` 单独构建。Mac 本机签名/启动收尾、可选已有 WSL 签名路径和验收边界见[构建文档](../desktop/BUILDING.md)。每次重建应重新核对源代码冻结清单、平台文件、通知与许可证、归档哈希及实际运行结果。

| 路径 | 职责 |
| --- | --- |
| `analysis-worker/app/workbench/` | 本地 API、存储、任务、采集、研究、报告、审核与恢复 |
| `analysis-worker/app/static/workbench/` | 无运行时 CDN 的模块化前端、离线字体及地图 |
| `analysis-worker/app/workbench/agent_assets/` | 外部 Agent 的公开契约、Skill 与 CLI |
| `desktop/` | Electron 主程序、私有 Python 运行时和跨平台构建 |
| `desktop/harness/` | 固定 DSH SDK/runtime、产品 relay、精确依赖 manifest 与许可 |
| `browser-extension/` | 可选 Chrome / Edge 会话桥接源码与测试 |
| `docs/` | 操作说明、设计审查、发布和验收记录 |

源代码提交保留固定 Harness 依赖闭包及第三方许可证；其文件集合由 `desktop/harness/runtime-manifest.json` 逐项校验。一般开发依赖、运行输出与用户数据由 `.gitignore` 排除。当前未为自有产品代码指定开源许可证；字体、地图和第三方依赖继续保留各自许可与署名。

历史版本的说明、验收和固定源码清单保留在 [Git 历史](https://github.com/railgun0325/peace-atlas/tree/776e5854653b5776c0c0d5b0ba17e3e5a47bf278/docs)及对应提交、标签中，只适用于各自版本。当前分支保留完整操作说明、设计契约和本轮发布记录，避免多份旧清单与重复说明混入当前入口。
