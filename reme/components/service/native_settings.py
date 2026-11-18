"""Edit shared job defaults through the existing MCP settings extension."""

import json
import os
import tempfile
from pathlib import Path
from threading import Lock

import yaml
from fastmcp.exceptions import ToolError
from fastmcp.tools import FunctionTool, ToolResult
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

SETTINGS_CAPABILITY = {
    "openai/settings": {"readTool": "reme_settings_read", "updateTool": "reme_settings_update"},
}
_JOB_FIELDS = {
    "searchLimit": ("search", "limit"),
    "recallMinScore": ("search", "min_score"),
    "dreamHint": ("auto_dream", "hint"),
}


class JobDefaults(BaseModel):
    """Only server-owned tool defaults belong in the shared settings form."""

    model_config = ConfigDict(extra="forbid", strict=True)

    searchLimit: int = Field(default=5, ge=1, title="Search result limit")
    recallMinScore: float = Field(default=0.0, ge=0, allow_inf_nan=False, title="Minimum recall score")
    dreamHint: str = Field(default="", title="Consolidation guidance")


def add_native_settings(server, app, settings_file: str | None) -> None:
    """Register settings on ReMe itself; paths never come from a tool caller."""
    if not settings_file:
        raise ValueError("Native settings require an explicit server config file")
    path = Path(settings_file)
    jobs = app.context.jobs
    for job_name, _ in _JOB_FIELDS.values():
        if job_name not in jobs:
            raise ValueError(f"Native settings require the {job_name!r} job")
    schema = JobDefaults.model_json_schema()
    schema.pop("required", None)
    lock = Lock()

    def values():
        configured = {
            field: jobs[job].kwargs[key] for field, (job, key) in _JOB_FIELDS.items() if key in jobs[job].kwargs
        }
        return JobDefaults(**configured).model_dump()

    values()

    def read_settings() -> ToolResult:
        """Return shared defaults for the native settings form."""
        with lock:
            return ToolResult(content=[], structured_content={"schema": schema, "values": values()})

    def save_settings(**arguments) -> ToolResult:
        """Save a partial update before publishing it to running jobs."""
        changes = arguments["set"]
        if not isinstance(changes, dict) or not changes:
            raise ToolError("Provide at least one supported ReMe setting")
        with lock:
            updated = JobDefaults(**{**values(), **changes}).model_dump()
            temporary = None
            try:
                # Keep placeholders unexpanded: saving a setting must never persist environment secrets.
                raw = path.read_text(encoding="utf-8")
                config = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
                if not isinstance(config, dict):
                    raise ValueError("Server config must be an object")
                for field in changes:
                    job, key = _JOB_FIELDS[field]
                    config.setdefault("jobs", {}).setdefault(job, {})[key] = updated[field]
                encoded = (
                    json.dumps(config, ensure_ascii=False, indent=2)
                    if path.suffix == ".json"
                    else yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
                )
                fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".reme-settings-")
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(encoded + ("" if encoded.endswith("\n") else "\n"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except (OSError, ValueError, TypeError, yaml.YAMLError):
                raise ToolError("Could not save the server configuration; settings were not changed") from None
            finally:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
            for field in changes:
                job, key = _JOB_FIELDS[field]
                jobs[job].kwargs[key] = updated[field]
                setattr(app.config.jobs[job], key, updated[field])
            return ToolResult(content=[], structured_content={"values": updated})

    server.add_tool(
        FunctionTool(
            name="reme_settings_read",
            description="Read shared ReMe search and consolidation defaults.",
            fn=read_settings,
            parameters={"type": "object", "additionalProperties": False},
            output_schema={
                "type": "object",
                "properties": {"schema": {"type": "object"}, "values": schema},
                "required": ["schema", "values"],
            },
            annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
        ),
    )
    server.add_tool(
        FunctionTool(
            name="reme_settings_update",
            description="Save shared ReMe defaults. Only change settings when the user asks.",
            fn=save_settings,
            parameters={
                "type": "object",
                "properties": {"set": {**schema, "minProperties": 1}},
                "required": ["set"],
                "additionalProperties": False,
            },
            output_schema={"type": "object", "properties": {"values": schema}, "required": ["values"]},
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
        ),
    )
