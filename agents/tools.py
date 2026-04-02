"""
MCP tool wrappers for the caseorium pipeline.

Wraps existing scripts (transcribe, extract_slides, publish_to_wp) as SDK MCP tools
so agents can call them directly.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool, create_sdk_mcp_server

PROJECT_ROOT = Path(__file__).parent.parent


@tool(
    "transcribe_youtube",
    "Transcribe a YouTube video. Returns path to transcript.md file.",
    {
        "youtube_url": str,
        "output_dir": str,
        "method": str,  # "youtube" (free) or "deepgram" (high quality)
    },
)
async def transcribe_youtube(args: dict[str, Any]) -> dict[str, Any]:
    """Run transcribe_youtube.py and return the transcript path."""
    script = str(PROJECT_ROOT / "tools" / "transcribe_youtube.py")
    cmd = [
        sys.executable, script,
        args["youtube_url"],
        "--output", args["output_dir"],
        "--method", args.get("method", "youtube"),
    ]

    env = {**os.environ}
    # Load .env if exists
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, env=env
        )
        if result.returncode != 0:
            return {
                "content": [{"type": "text", "text": f"Error: {result.stderr}\n{result.stdout}"}],
                "isError": True,
            }

        transcript_path = os.path.join(args["output_dir"], "transcript.md")
        return {
            "content": [{"type": "text", "text": f"Transcript saved to: {transcript_path}\n\n{result.stdout}"}],
        }
    except subprocess.TimeoutExpired:
        return {
            "content": [{"type": "text", "text": "Error: transcription timed out (10 min limit)"}],
            "isError": True,
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "isError": True,
        }


@tool(
    "extract_slides",
    "Extract slides from a PDF presentation as PNG images. Returns list of slide paths.",
    {
        "pdf_path": str,
        "output_dir": str,
        "dpi": int,
    },
)
async def extract_slides(args: dict[str, Any]) -> dict[str, Any]:
    """Run extract_slides.py and return slide paths."""
    script = str(PROJECT_ROOT / "tools" / "extract_slides.py")
    cmd = [
        sys.executable, script,
        args["pdf_path"],
        "--output", args["output_dir"],
        "--dpi", str(args.get("dpi", 150)),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return {
                "content": [{"type": "text", "text": f"Error: {result.stderr}\n{result.stdout}"}],
                "isError": True,
            }
        return {
            "content": [{"type": "text", "text": f"Slides extracted to: {args['output_dir']}\n\n{result.stdout}"}],
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "isError": True,
        }


@tool(
    "publish_to_wordpress",
    "Publish a _READY.md case file to WordPress as a draft. Returns post URL.",
    {
        "case_file": str,
        "publish": bool,
        "dry_run": bool,
    },
)
async def publish_to_wordpress(args: dict[str, Any]) -> dict[str, Any]:
    """Run publish_to_wp.py and return the result."""
    script = str(PROJECT_ROOT / "tools" / "publish_to_wp.py")
    cmd = [sys.executable, script, args["case_file"]]

    if args.get("publish"):
        cmd.append("--publish")
    if args.get("dry_run"):
        cmd.append("--dry-run")

    env = {**os.environ}
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, env=env
        )
        if result.returncode != 0:
            return {
                "content": [{"type": "text", "text": f"Error: {result.stderr}\n{result.stdout}"}],
                "isError": True,
            }
        return {
            "content": [{"type": "text", "text": result.stdout}],
        }
    except Exception as e:
        return {
            "content": [{"type": "text", "text": f"Error: {e}"}],
            "isError": True,
        }


def create_pipeline_tools():
    """Create MCP server config with all pipeline tools."""
    return create_sdk_mcp_server(
        name="caseorium",
        version="1.0.0",
        tools=[transcribe_youtube, extract_slides, publish_to_wordpress],
    )
