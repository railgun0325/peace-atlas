# PEACE ATLAS 本机 Agent 接入

本机桌面应用通过独立 `/agents/v1` 通道供外部 Agent 操作，不增加内部聊天窗口。普通桌面 API 的 session gate 保持完整；Agent 持有独立、有期限、可撤销的授权。通道默认未授权，公开 manifest/schema 只说明能力和安装方式。

人在桌面 **外部 Agent** 菜单选择只读、采集/研究/报告，或额外含可恢复清理的范围，再导出一个新的 JSON 连接文件。后端先设置仅文件所有者及系统可访问的受保护 ACL，再写入独立随机凭据；POSIX 使用 0600。已有文件拒绝覆盖。凭据不会出现在 ready、API 返回、公开 manifest/schema 或菜单日志。授权默认 8 小时，最长 24 小时；应用重启、到期或撤销失效。撤销不取消已授权创建的业务任务。此机制信任同一系统用户启动的本机进程，不构成针对同用户恶意程序的隔离沙箱。

Agent 使用随包 `agent_assets/peace-atlas/scripts/peace_atlas.py`。它只接受明确 loopback HTTP origin，忽略系统代理，不跟随重定向，不读浏览器 cookies、桌面 token 或 AI keys。数据接口拒绝 Origin/Referer 和远程客户端。管理授权接口只接受桌面原生调用的 session header，不接受仅浏览器 cookie 的授权导出。

只读操作包括资料正文/来源、账号、任务、报告、研究图及导出，规则查询规划。任务创建、续查、取消、重试、回收站/恢复都通过预览及显式确认，且作用于固定操作目录，不能提交任意 API 路径。Agent 不开放模型设置、密钥、登录、profile、永久删除或关闭应用。

预览保存标准化完整参数、授权身份、业务预估或清理 digest，五分钟有效。执行要求同一预览、完整参数、`confirmed:true` 和稳定操作键；并发提交串行处理。匹配重复请求返回原结果，包括业务失败和未确认异常；同一个已消费预览不能换新键执行。记录仅在当前后端授权生命周期内保留，重启后通过已保存任务 ID 查询业务状态。研究和续查继续使用业务层的参数及当前 run/job 绑定令牌，不能绕过 UI 同等级的确认校验。报告预览说明参数和模型/网络影响，不声称已实测的运行时间。

安装步骤及三种宿主的官方发现路径见随包 [INSTALL.md](../analysis-worker/app/workbench/agent_assets/INSTALL.md)。完整 Skill 可明确复制到用户选择的宿主 Skill 目录；安装命令拒绝覆盖，不安装凭据或修改宿主全局配置。宿主须能执行本机 Python 并已安装/启用 Skill。没有本机执行环境的云端 Agent 不能直接访问 loopback。安装完成需在宿主 Skills 列表核对，不能承诺尚未安装的任何 AI 自动发现应用。

集成位于 `server.create_app()` 的最后：

```python
from .agent_api import register_agent_api
register_agent_api(app)
```

打包必须包含 `agent_api.py`，以及 `agent_assets` 内明确白名单的 manifest、安装说明、SKILL、openai.yaml、API 引用和 Python CLI 六项。不要递归打包后来导出的连接文件、安装结果或凭据。

隔离验收在 `tests/test_workbench_agent_api.py` 使用自己的数据库、合成 session、独立临时连接与 loopback 服务。实际 CLI 创建证据报告、重复请求返回同一 job，并读取报告及来源引用；模型/公开平台的真实付费调用不属于该隔离证明。桌面菜单行为在不启动 Electron 或后端的隔离 VM 中测试；正式原生窗口的视觉/交付验收由统一打包阶段进行。
