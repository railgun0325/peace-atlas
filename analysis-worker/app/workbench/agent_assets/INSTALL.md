# 外部 Agent 接入与 Skill 安装

软件不需要增加聊天窗口。人在工作台操作，Codex、Claude Code、DSH 或其他能运行本机命令的 Agent 通过同一个授权通道操作保存的资料、研究任务和报告。

1. 在 PEACE ATLAS 的 **外部 Agent** 菜单选择 **导出授权连接**，选择只读、采集/研究/报告、审核/类型判定/账号核验，或全部业务权限，再选择一个新的 JSON 文件。文件只供当前操作系统用户及系统访问；它包含独立 Agent 凭据，请勿贴到聊天、提交仓库或发送给别人。最长授权 24 小时，默认 8 小时；应用重启、授权到期或撤销后失效。
2. 在本资产目录找到 `peace-atlas/scripts/peace_atlas.py`，使用 Python 3.10+。完整 Windows 包的 Python 位于 `resources/python/python.exe`；Mac 包位于 `PEACE ATLAS.app/Contents/Resources/python/bin/python3`。也可用 Agent 宿主提供的 Python。
3. 把完整 `peace-atlas` 文件夹安装到该宿主实际支持的 Skill 目录。安装命令会复制自包含 Skill，拒绝覆盖既存目录，不改宿主全局设置，也不复制凭据：

```text
<python> <asset-folder>/peace-atlas/scripts/peace_atlas.py install --destination <selected-root>/peace-atlas
```

4. 在宿主 Skills 列表验证能看到 `peace-atlas`，再向 Agent 提供你选定的连接文件路径。首次先查询 manifest/schema，再只读查询保存的资料。研究/报告/清理先预览，确认后才排队或变更数据。Skill 可被选择不等于已获授权。

| 宿主 | 官方本地发现位置 | 安装后核对 |
|---|---|---|
| Codex | 项目或用户 `.agents/skills/peace-atlas`；也支持所配置的兼容/自定义 Skill 路径 | 查看 Skills 列表；可显式 `$peace-atlas`。是否自动选择受版本/配置影响 |
| Claude Code（本机终端） | 项目 `.claude/skills/peace-atlas` 或用户 `~/.claude/skills/peace-atlas` | 查看 `/skills`；可 `/peace-atlas`。本机个人目录不自动提供给云/Cowork 会话 |
| DeepSeek Harness | 项目 `.dsh/skills/peace-atlas`、`.agents/skills/peace-atlas`，用户 `<DSH_HOME>/skills/peace-atlas` 或配置的 customSkillDirs | 需启用 filesystem Skill provider 与模型可见 consumer。只扫描直接 Skill 文件夹，不递归找任意嵌套 SKILL.md |
| 其他 Agent | 宿主支持标准 SKILL.md，并可运行本机 Python/命令 | 也可由用户手动提供 SKILL.md；不承诺自动发现 |

DSH 的用户目录安装请由用户明确选择路径，不让 Agent 自行探查私人 profile。此安装命令只复制到明确选择的 Skill 文件夹，不修改 DSH 配置。离线 Skill/CLI可用；云端 Agent若没有获授权的本机执行环境，不能直接连本机 loopback。

公开 `/agents/v1/manifest`、`/agents/v1/schema` 和本文件提供安装提示与操作契约，没有端口扫描、桌面会话 token 或 AI 密钥。API 默认关闭；连接文件是用户专门导出的授权材料。需要扩大权限时重新由人在桌面授权。**撤销全部 Agent 授权** 不影响正在运行的业务任务，只使 Agent 凭据失效；导出的旧文件保留但无效。

当前官方机制核对日期：2026-10-10。依据：[Codex Skills 官方文档](https://learn.chatgpt.com/docs/build-skills)、[Claude Code Skills 官方文档](https://code.claude.com/docs/en/skills)、[DeepSeek Harness 官方 Skill 子系统](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.md)。产品机制可能更新；实际以安装宿主的配置和 Skills 列表为准。


## 快速判定账号类型与人工快改

在“组织与账号”或“账号关系图”点击“快速判定账号类型”。先预估，再点击“开始类型粗分”，到任务中心查看组织、个人、未判定、分类把握与说明。它使用应用已配置的 DeepSeek，只把已保存的名称、用户名和公开简介送入模型；名称和用户名可作启发线索，结果标记为“类型粗分（可手动更改）”，不作为确定身份结论。不会搜索或读取外部公开网页，不自动核验身份，也不推断政治身份或组织隶属；模型请求仍需网络并按实际调用计费。

| 参数 | 默认与范围 | 作用 |
|---|---|---|
| 每批账号数 / `batch_size` | 默认 100，整数 1–200 | 通常每批一次模型调用，失败重试可能增加请求；不是账号总量上限。 |
| 重新判定已有类型 / `recheck` | 默认 false | 默认纳入未审账号和已有未判定账号；true 同时纳入已判为组织或个人的账号。人工锁定和已排除账号仍跳过。 |
| 指定账号 / `account_ids` | 默认空；Agent 一次最多 2000 个编号 | 空时选择全部符合条件的已保存账号；指定时只处理这些账号中符合条件的记录。页面入口不沿用列表搜索、图谱或研究筛选。 |

账号卡片、账号关系图和研究图谱详情提供“设为组织”“设为个人”“设为未判定”。点击后保存人工类型并锁定，不改变身份核验状态；“解除人工锁定”只解除类型保护，保留原类型、说明和依据，不立即运行模型。需要再次处理已知类型时，解除锁定后明确选择重新判定。

取消使用任务中心正常入口。取消、失败或中断保留已提交批次；在原任务重试会续用同一快照并跳过已完成账号。未提交的账号材料或类型已变化时，不强行覆盖，应重新预估创建新任务。每批决定与进度一起保存，输出遗漏、重复编号或无效类型时整批不写入。

外部 Agent 使用 `classify.start`，需要独立 `review` 权限；先 preview 再 execute。`job.get` 需要 `read`，该分类任务的 `job.cancel` / `job.retry` 需要 `review`。快速分类没有网页、身份核验或来源缓存开关；需要原文活动审核、关系主张审核或独立身份核验时，使用下文的严格审核或“自动核验候选账号”入口。

## 严格自动审核与核验

软件页面中的“自动审核与核验”使用已配置 DeepSeek，通过 DSH 工具逐批调查现有待审内容。先预估范围与用时，再开始；在任务中心查看结果和导出完整记录。更多参数、断点续跑和结果解释见同目录 AUTOMATIC_REVIEW.md。

外部 Agent 单独选择审核权限即可使用 `classify.start`、`review.queue`、`review.batch` 和 `review.start`。快速类型粗分与严格证据审核使用不同入口。原只读或研究权限不自动扩大。已完成审核不等于全部确认属实；证据不足与账号未判定保留理由，账号身份核验另查准确公开回链。原始材料、已观察关系和人工锁定类型保留。
