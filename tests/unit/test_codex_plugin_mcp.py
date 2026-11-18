"""Direct MCP hook transport, endpoint isolation, and delivery diagnostics."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import asyncio
import importlib
import json
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

PLUGIN = Path(__file__).resolve().parents[2] / "integrations/codex/plugins/reme"


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PLUGIN))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "host"))
    return importlib.import_module("reme_config"), None, importlib.import_module("reme_mcp")


def test_custom_endpoint_does_not_alias_standard_queue(plugin):
    config, _, _ = plugin
    standard = config.validate_config({"mcpUrl": "http://service.test/mcp"})
    custom = config.validate_config({"mcpUrl": "http://service.test"})
    assert standard["endpoint"] == "http://service.test"
    assert custom["endpoint"] != standard["endpoint"]


@pytest.mark.asyncio
async def test_hooks_call_mcp_and_preserve_structured_answers(plugin, monkeypatch):
    config, _, transport = plugin
    upstream = FastMCP("test")
    calls = []

    @upstream.tool
    def search(query: str) -> dict:
        calls.append(query)
        return {"path": "daily/fact.md"}

    @upstream.tool
    def auto_memory() -> str:
        raise ToolError("write failed")

    @upstream.tool
    async def delayed() -> str:
        await asyncio.sleep(1)
        return "too late"

    monkeypatch.setattr("fastmcp.Client", lambda *args, **kwargs: Client(upstream, **kwargs))
    result = await transport.call_async(config.load_config(), "search", {"query": "fact"}, 2)
    assert result == {"success": True, "answer": {"path": "daily/fact.md"}}
    assert calls == ["fact"]
    with pytest.raises(ToolError, match="write failed"):
        await transport.call_async(config.load_config(), "auto_memory", {}, 2)
    with pytest.raises((TimeoutError, MCPError)):
        await transport.call_async(config.load_config(), "delayed", {}, 0.05)


def test_diagnostics_rotate_and_detect_interrupted_dream(plugin, monkeypatch):
    config, _, _ = plugin
    state = importlib.import_module("reme_state")
    current = config.load_config()
    monkeypatch.setattr(state, "LOG_BYTES", 800)
    for _ in range(30):
        state.log_status("memory_saved", config=current, turns=5)
    assert (config.data_dir() / "hooks.log.1").exists()
    rows = state.recent_activity(current, limit=3)
    assert len(rows) == 3 and all(row["turns"] == 5 for row in rows)
    assert (config.data_dir() / "hooks.log").stat().st_mode & 0o777 == 0o600
    path = state.endpoint_dir(current) / "dream.json"
    config.write_json(path, {"status": "running", "started_at": 1})
    assert state.local_status(current)["auto_dream"]["last_run"]["status"] == "interrupted"
    assert json.loads(path.read_text())["status"] == "running"  # Status does not rewrite history.


@pytest.mark.asyncio
async def test_reme_job_failures_reach_plugin_when_service_error_flag_is_enabled(plugin, monkeypatch):
    from reme.components.service.mcp_tools import add_mcp_job
    from reme.schema import Response

    class FailingJob:
        """Exercise the real service's MCP error contract without a model call."""

        name = "auto_memory"
        description = "test memory job"
        parameters = {"type": "object", "properties": {}}

        async def __call__(self, **_kwargs):
            return Response(success=False, answer="memory extraction failed")

    config, _, transport = plugin
    upstream = FastMCP("real-error-contract")
    add_mcp_job(upstream, FailingJob(), injected_job_kwargs={}, tool_error_on_failure=True)
    monkeypatch.setattr("fastmcp.Client", lambda *args, **kwargs: Client(upstream, **kwargs))
    with pytest.raises(ToolError, match="memory extraction failed"):
        await transport.call_async(config.load_config(), "auto_memory", {}, 2)
