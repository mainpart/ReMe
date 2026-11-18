# 在 Codex 中使用 ReMe

[English](./README.md)

Codex 直接连接共享 ReMe HTTP MCP 服务，不再为每个会话启动 Python MCP 代理。
Hooks 仍负责自动召回和完成轮次记录；每日整理由 ReMe 服务自己的 `jobs.dream_cron` 负责。

## 启动服务

从此 checkout 安装：`pip install -e ".[core]"`。创建自己的 `reme.yaml`：

```yaml
extends: default
workspace_dir: /absolute/path/to/reme-workspace
service:
  backend: http
  host: 127.0.0.1
  tool_error_on_failure: true
  native_settings: true
jobs:
  search:
    limit: 5
    min_score: 0.0
  auto_dream:
    hint: ""
```

```bash
reme start config=/absolute/path/to/reme.yaml
```

通过[配置指南](../../docs/zh/configuration.md)设置服务端模型。未认证的服务仅监听本机，或置于受保护的代理后。
启用 native settings 后，MCP 调用方可以修改所有客户端共享的默认值。
迁移时保留现有的服务端 cron 和时区；默认整理时间为每天 23:00，插件不再创建定时器。

## 安装

宿主需要 Codex CLI >= 0.159.2、Python 3.11+ 和 FastMCP >= 4.1.4，以运行短生命周期 Hooks。

```bash
codex plugin marketplace add /absolute/path/to/ReMe/integrations/codex
codex plugin add reme@reme-codex
```

重启 Codex，在 **Settings → Hooks** 或 CLI `/hooks` 中检查并信任全部七个 ReMe 条目。
MCP 连接正常不代表 Hooks 已启用。新建会话并检查 ReMe 连接。

内置 MCP 地址为 `http://127.0.0.1:2333/mcp`。其他地址或网关通过宿主的 HTTP MCP 配置设置。
Hooks 的 `mcpUrl` 单独指向实际 ReMe 服务；模型经过网关时，Hooks 仍可直接访问 ReMe。

## 设置和迁移

Codex desktop >= 26.924.22138 的 MCP 设置表单修改服务端的搜索数量、最低分数和整理提示。
命令行用户直接编辑同一个 YAML/JSON。自动召回和模型的 `search` 共用 `jobs.search` 默认值，显式参数优先。
手动和定时整理默认使用 `jobs.auto_dream.hint`。

GUI 保存需显式指定用户自己的 `config=` 文件。写入采用原子替换，并立即更新运行中的任务。
环境变量占位符保持原样；YAML 注释和格式不会保留。其他文件修改需重启，启动时 CLI 覆盖仍优先。
不要让两个 ReMe 服务同时编辑同一配置文件。

宿主本地配置 `${CODEX_HOME:-~/.codex}/reme/config.json` 只负责 Hooks：连接、记录/召回开关、批量大小、
超时、语言、批次时区和子代理过滤。参考 `plugins/reme/config.example.json`，时区应与服务端一致。
服务端不读取 Codex 配置或会话文件。GUI 不再修改宿主选项或显示本地队列面板，诊断使用
`reme/hooks.log` 和 `reme/queue/`。

从本 checkout 重新安装插件并重新连接 MCP，删除手动配置的重复 stdio 条目。
保留现有配置和队列；旧 `searchLimit`、`recallMinScore`、`dreamHint`、`autoDreamEnabled`、`dreamCron`
字段仍被接受，但不再生效，请把需要的值移到服务端。保留已经运行的服务端整理计划。

Hooks 默认每五个完成轮次提交一次，只记录用户和最终助手文本，排除工具结果和召回上下文。
失败提交保留并重试；退出时仅做有时间限制的刷新。队列按端点隔离，交付为至少一次。
验证时保持会话开启并检查 `memory_saved`。进程在捕获前被杀可能丢失轮次；关闭自动记录保留待提交队列。

直接使用服务端 `search`、`read`、`health_check`、`status`、`auto_dream`；旧适配工具
`reme_search`、`reme_status`、`reme_run_dream` 已移除。
