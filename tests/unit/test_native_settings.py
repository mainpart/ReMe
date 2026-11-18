"""Shared settings and direct HTTP calls, without models or user state."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access

import asyncio
import json
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import uvicorn
import yaml
from fastmcp import Client
from fastmcp.exceptions import ToolError

from reme.components.application_context import ApplicationContext
from reme.components.job import BaseJob
from reme.components.service.http_service import HttpService
from reme.config import resolve_app_config


@pytest.fixture(params=[".yaml", ".json"])
def configured(tmp_path, monkeypatch, request):
    monkeypatch.setenv("TEST_REME_TOKEN", "expanded-test-value")
    path = tmp_path / ("server" + request.param)
    config = {
        "service": {"backend": "http", "native_settings": True, "web_enabled": False},
        "environment": {"TOKEN": "${TEST_REME_TOKEN}"},
        "timezone": "Europe/Moscow",
        "jobs": {
            "search": {
                "backend": "base",
                "limit": 5,
                "min_score": 0.0,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer"},
                        "min_score": {"type": "number"},
                    },
                    "required": ["query"],
                },
            },
            "auto_dream": {"backend": "base", "hint": ""},
            "dream_cron": {"backend": "cron", "cron": "0 23 * * *"},
        },
    }
    path.write_text(json.dumps(config) if request.param == ".json" else yaml.safe_dump(config), encoding="utf-8")
    return path


def build_service(path):
    config = resolve_app_config(config=str(path), log_config=False)
    context = ApplicationContext(**config)
    app = SimpleNamespace(config=context.app_config, context=context, start=AsyncMock(), close=AsyncMock())

    async def echo(runtime):
        runtime.response.answer = {
            key: value for key, value in runtime.data.items() if key in {"query", "limit", "min_score", "hint"}
        }

    for name, job in app.config.jobs.items():
        instance = BaseJob(name=name, app_context=context, **job.model_dump())
        instance._build_steps = lambda: [echo]
        context.jobs[name] = instance
    service = HttpService(**config["service"])
    service.build_service(app)
    service.add_jobs(app)
    return app, service


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_settings_persist_and_calls_use_shared_defaults(configured, mode):
    app, service = build_service(configured)
    async with Client(service.mcp_server, mode=mode) as first, Client(service.mcp_server, mode=mode) as second:
        assert first.server_capabilities.experimental["openai/settings"]["readTool"] == "reme_settings_read"
        before = (await first.call_tool("reme_settings_read", {})).structured_content
        assert set(before["values"]) == {"searchLimit", "recallMinScore", "dreamHint"}
        saved = await first.call_tool(
            "reme_settings_update",
            {"set": {"searchLimit": 9, "recallMinScore": 0.01, "dreamHint": "keep decisions"}},
        )
        assert (await second.call_tool("reme_settings_read", {})).structured_content[
            "values"
        ] == saved.structured_content["values"]
        assert (await second.call_tool("search", {"query": "fact"})).data == {
            "query": "fact",
            "limit": 9,
            "min_score": 0.01,
        }
        explicit = await first.call_tool("search", {"query": "fact", "limit": 2, "min_score": 0})
        assert explicit.data == {"query": "fact", "limit": 2, "min_score": 0}
        assert (await first.call_tool("auto_dream", {})).data == {"hint": "keep decisions"}
        tools = {tool.name: tool for tool in await first.list_tools()}
        assert "default" not in tools["search"].input_schema["properties"]["limit"]
    text = configured.read_text()
    assert "${TEST_REME_TOKEN}" in text and "expanded-test-value" not in text
    assert app.config.timezone == "Europe/Moscow"
    assert app.config.jobs["dream_cron"].cron == "0 23 * * *"
    assert app.config.jobs["search"].limit == 9
    _, restarted = build_service(configured)
    async with Client(restarted.mcp_server) as client:
        assert (await client.call_tool("search", {"query": "restart"})).data["limit"] == 9


@pytest.mark.asyncio
async def test_settings_validate_and_failed_save_keeps_disk_and_runtime(configured, monkeypatch):
    _, service = build_service(configured)
    original = configured.read_bytes()
    async with Client(service.mcp_server) as client:
        for changes in ({}, {"searchLimit": 0}, {"searchLimit": True}, {"recallMinScore": -1}, {"path": "other"}):
            with pytest.raises(ToolError):
                await client.call_tool("reme_settings_update", {"set": changes})
        monkeypatch.setattr(
            "reme.components.service.native_settings.os.replace",
            lambda *_: (_ for _ in ()).throw(
                OSError("read-only test directory"),
            ),
        )
        with pytest.raises(ToolError, match="settings were not changed"):
            await client.call_tool("reme_settings_update", {"set": {"searchLimit": 7}})
        assert (await client.call_tool("search", {"query": "unchanged"})).data["limit"] == 5
    assert configured.read_bytes() == original
    assert not list(configured.parent.glob(".reme-settings-*"))


@pytest.mark.parametrize("options", [{}, {"config": "default"}, {"config": "default.yaml"}])
def test_settings_require_explicit_user_file(options):
    with pytest.raises(ValueError, match="user-owned"):
        resolve_app_config(**options, service={"backend": "http", "native_settings": True}, log_config=False)


def test_settings_are_opt_in():
    service = HttpService(web_enabled=False)
    service.build_service(SimpleNamespace(config=SimpleNamespace(app_name="plain")))

    async def check():
        async with Client(service.mcp_server) as client:
            assert "reme_settings_update" not in {tool.name for tool in await client.list_tools()}

    asyncio.run(check())


def test_two_direct_http_clients_and_job_api_share_defaults(configured):
    import httpx

    _, service = build_service(configured)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(service.service, log_level="error", ws="none"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"

            async def call():
                async with Client(url + "/mcp") as first, Client(url + "/mcp") as second:
                    await first.call_tool("reme_settings_update", {"set": {"searchLimit": 8}})
                    assert (await second.call_tool("search", {"query": "direct"})).data["limit"] == 8
                async with httpx.AsyncClient() as client:
                    response = await client.post(url + "/search", json={"query": "hook"})
                    assert response.json()["answer"]["limit"] == 8

            asyncio.run(call())
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()
