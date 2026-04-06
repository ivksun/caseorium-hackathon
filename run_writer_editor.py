#!/usr/bin/env python3
"""
Run Writer + Editor stages on existing analysis files.
Usage: python3 run_writer_editor.py <working_dir>
"""

import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    query,
)

from agents.definitions import writer, editor
from tools.metrics import get_metrics_client, HOURS_SAVED_BY_AGENT, USD_TO_RUB

PROJECT_ROOT = Path(__file__).parent
WORKING_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "cases/Аспирити_draft"

# Load .env for metrics credentials
load_dotenv(PROJECT_ROOT / ".env")

# Agent name → task_type mapping
TASK_TYPES = {
    "writer": "case_writing",
    "editor": "editing",
}


async def run_agent(agent_def, prompt, label, agent_name: str):
    """Run a single agent and return result."""
    print(f"\n{'='*60}")
    print(f"  RUNNING: {label}")
    print(f"{'='*60}\n")

    run_id = os.environ.get("PIPELINE_RUN_ID", "standalone")
    task_type = TASK_TYPES.get(agent_name, agent_name)
    metrics = get_metrics_client()

    # Send task_started
    metrics.task_started(distinct_id=run_id, agent=agent_name, task_type=task_type)

    options = ClaudeAgentOptions(
        model=agent_def.model or "sonnet",
        allowed_tools=agent_def.tools,
        cwd=str(PROJECT_ROOT),
        max_turns=agent_def.maxTurns or 20,
        max_budget_usd=3.0,
    )

    # Combine agent system prompt with task prompt
    full_prompt = f"{agent_def.prompt}\n\n---\n\nTASK:\n{prompt}"

    start = time.time()
    result = None

    async for message in query(prompt=full_prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
            break

    elapsed = time.time() - start
    status = "OK" if result and not result.is_error else "FAILED"
    cost = result.total_cost_usd if result else 0
    print(f"\n  {label}: {status} ({elapsed:.0f}s, ${cost:.4f})")

    # Send metrics with real cost from SDK
    if result and not result.is_error:
        cost_rub = round((result.total_cost_usd or 0) * USD_TO_RUB, 2)
        metrics.task_completed(
            distinct_id=run_id,
            agent=agent_name,
            task_type=task_type,
            latency=elapsed,
            hours_saved=HOURS_SAVED_BY_AGENT.get(agent_name, 0.5),
            cost=cost_rub,
            tokens=0,
        )
    else:
        metrics.task_failed(
            distinct_id=run_id,
            agent=agent_name,
            task_type=task_type,
            error_type="agent_error",
            latency=elapsed,
        )

    return result


async def main():
    wd = WORKING_DIR.resolve()
    print(f"Working directory: {wd}")

    # Set unique run ID for this session
    os.environ["PIPELINE_RUN_ID"] = f"writer_editor_{uuid.uuid4().hex[:8]}"

    # --- WRITER ---
    writer_prompt = f"""
Write a case study using these analysis files:
- Facts: {wd}/facts_extracted.md
- Company metadata: {wd}/company_metadata.md
- Slides analysis: {wd}/slides_analysis.md

Save output as {wd}/case_draft_v1_new.md
"""
    await run_agent(writer, writer_prompt, "WRITER", agent_name="writer")

    # --- EDITOR ---
    editor_prompt = f"""
Edit this case draft:
- Draft: {wd}/case_draft_v1_new.md
- Transcript (source of truth): {wd}/transcript.md
- Slides analysis: {wd}/slides_analysis.md

Save final version as:
- {wd}/case_final_new.md
- {wd}/Аспирити_READY_new.md
"""
    await run_agent(editor, editor_prompt, "EDITOR", agent_name="editor")

    print("\n\nDONE. Compare files:")
    print(f"  OLD: {wd}/Аспирити_READY.md")
    print(f"  NEW: {wd}/Аспирити_READY_new.md")


if __name__ == "__main__":
    asyncio.run(main())
