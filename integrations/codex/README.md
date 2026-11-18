# Use ReMe in Codex

[中文说明](./README_ZH.md)

Codex connects directly to the shared ReMe HTTP MCP server. Hooks still recall memories and record
completed turns. There is no per-session MCP proxy or Codex consolidation timer.

## Start ReMe

Install this checkout with `pip install -e ".[core]"`. Create a user-owned `reme.yaml`:

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

Configure the server model using the [configuration guide](../../docs/en/configuration.md).
Keep an unauthenticated service on loopback or behind a protected proxy. Enabling native settings
lets MCP callers change the shared defaults for every client using this server.

`jobs.dream_cron` remains the server's consolidation schedule, daily at 23:00 by default. Keep your
existing schedule and timezone when migrating. The plugin does not create another scheduler.

## Install the plugin

Use Codex CLI >= 0.159.2 and Python 3.11+ with FastMCP >= 4.1.4 on the host for short-lived hooks:

```bash
codex plugin marketplace add /absolute/path/to/ReMe/integrations/codex
codex plugin add reme@reme-codex
```

Restart Codex. Review and trust all seven ReMe hook entries in **Settings → Hooks** or CLI `/hooks`.
MCP health alone does not enable hooks. In a new conversation, ask Codex to check ReMe's connection.

The bundled MCP entry uses `http://127.0.0.1:2333/mcp`. For another server or a gateway, configure
the HTTP MCP entry in the harness. Separately set `mcpUrl` in the hook config to the actual ReMe
endpoint. Hooks may keep calling ReMe directly even when model tools go through a gateway.

## Settings

With Codex desktop >= 26.924.22138, open the ReMe MCP settings form. It edits the server's search
limit, minimum score, and consolidation guidance. CLI users edit the same server YAML/JSON. The GUI needs a running ReMe server.
Both automatic recall and explicit `search` use `jobs.search` defaults. Explicit tool arguments win.
Scheduled and manual consolidation share `jobs.auto_dream.hint` unless the call overrides it.

Native settings are opt-in and require `config=` to point to an explicit user-owned YAML/JSON file.
Saves are atomic and affect running jobs immediately. Environment placeholders stay unexpanded;
YAML comments and formatting are not preserved. Other file edits require a restart. CLI overrides
still take precedence at startup. Do not run two ReMe services editing the same config file.

Hook-only options remain in `${CODEX_HOME:-~/.codex}/reme/config.json`: connection, recall/recording
switches, batch size, timeouts, language, timezone for captured batches, and root-agent filtering.
See `plugins/reme/config.example.json`. The server never reads a Codex profile or local transcripts.
Set the hook timezone to match the server. GUI settings no longer edit host-local options or show
the local queue panel; inspect `reme/hooks.log` and `reme/queue/` on the host instead.

## Migration and delivery

Reinstall the plugin from this checkout and reconnect MCP. Remove any manually configured duplicate
stdio entry. Existing hook configs and pending queues stay in place. Old `searchLimit`,
`recallMinScore`, `dreamHint`, `autoDreamEnabled`, and `dreamCron` keys are accepted but ignored;
move their intended values to the server config. Do not disable an existing server schedule.

Hooks capture only completed user/assistant text, excluding tool output and recalled context.
They batch five turns by default, retry failed writes, and attempt a bounded flush at session end.
Pending batches remain local and are isolated by endpoint. Delivery is at least once; keep the
session open and check `memory_saved` when testing a synthetic fact. A killed host can lose a turn
before capture. Disabling `autoMemoryEnabled` retains pending batches and stops automatic retries.

For manual operations use the server's `search`, `read`, `health_check`, `status`, and `auto_dream`.
The old `reme_search`, `reme_status`, and `reme_run_dream` adapter tools are removed.

## Validation

`pytest tests/unit/test_native_settings.py tests/unit/test_coding_agent_plugins.py tests/unit/test_codex_plugin_mcp.py`
checks shared defaults, atomic saves, direct HTTP clients, capture, queues, and retries without models.
