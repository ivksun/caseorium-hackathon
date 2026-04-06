"""
Caseorium pipeline orchestrator.

Chains 5 agents: Transcriber → Analyst → Writer → Editor → Publisher.
Supports human-in-the-loop checkpoints between stages.
"""

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    PermissionResultAllow,
    PermissionResultDeny,
    query,
)

from .definitions import get_all_agents
from .tools import create_pipeline_tools
from tools.metrics import flush_deferred_completions

PROJECT_ROOT = Path(__file__).parent.parent


def _build_orchestrator_prompt(
    youtube_url: str | None = None,
    transcript_path: str | None = None,
    slides_pdf: str | None = None,
    company_name: str | None = None,
    method: str = "youtube",
    skip_publish: bool = False,
    hitl_after: list[str] | None = None,
) -> str:
    """Build the orchestrator prompt with all context."""

    # Determine input source
    if youtube_url:
        input_section = f"""INPUT:
- YouTube URL: {youtube_url}
- Transcription method: {method} {"(free YouTube captions)" if method == "youtube" else "(Deepgram high-quality)"}"""
    elif transcript_path:
        input_section = f"""INPUT:
- Transcript file: {transcript_path}"""
    else:
        raise ValueError("Either youtube_url or transcript_path must be provided")

    if slides_pdf:
        input_section += f"\n- Presentation PDF: {slides_pdf}"

    if company_name:
        input_section += f"\n- Company name: {company_name}"

    # HITL checkpoints — editor checkpoint is ALWAYS mandatory
    hitl_stages = list(hitl_after or [])
    if "editor" not in hitl_stages:
        hitl_stages.append("editor")

    hitl_section = f"""
HUMAN-IN-THE-LOOP CHECKPOINTS:
After these stages, STOP and report results to the user before continuing:
{', '.join(hitl_stages)}

MANDATORY: After the Editor stage, you MUST stop and wait for human approval.
Show the path to the _READY.md file so the human can review it.
Do NOT proceed to publishing without explicit human confirmation.

When stopping at a checkpoint:
1. Report what was produced (file paths, key findings)
2. Show a brief summary of the output
3. Ask "Continue to next stage?" and WAIT for user response
"""

    publish_note = ""
    if skip_publish:
        publish_note = "\nNOTE: Skip the Publisher stage. Stop after the Editor produces _READY.md."

    return f"""\
You are the Caseorium Pipeline Orchestrator.

You manage a 5-stage pipeline that transforms YouTube talks into published case studies.
You have 5 specialist agents and custom tools at your disposal.

{input_section}

PROJECT PATHS:
- Project root: {PROJECT_ROOT}
- Engine prompts: {PROJECT_ROOT}/engine/
- Knowledge base: {PROJECT_ROOT}/knowledge/
- Case examples: {PROJECT_ROOT}/cases/examples/
- Style guide: {PROJECT_ROOT}/references/russian-style-guide.md

---

## METRICS TRACKING (MANDATORY)

You MUST send metrics for EVERY stage using the metrics tools:

1. **BEFORE** starting each stage: call `metrics_task_started` with agent name and task_type
2. **AFTER** each stage succeeds: call `metrics_task_completed` with agent, task_type, latency (seconds since stage start), and tokens (estimate 0 if unknown)
3. **ON ERROR**: call `metrics_task_failed` with agent, task_type, error_type, and latency

Agent names and task_types:
| Stage | agent | task_type |
|-------|-------|-----------|
| 1 | transcriber | transcription |
| 2 | analyst | analysis |
| 3 | writer | case_writing |
| 4 | editor | editing |
| 5 | publisher | publishing |

Track time: note the current time before each stage starts, calculate latency = time after - time before.

---

## PIPELINE STAGES

Execute stages sequentially. Each stage MUST complete before the next begins.

### STAGE 1: TRANSCRIPTION
Use the "transcriber" agent.
- If YouTube URL provided: transcribe using the transcribe_youtube tool
- If transcript file provided: skip this stage, use existing file
- Working directory: {PROJECT_ROOT}/cases/[company_name]_draft/
- Create the working directory first

### STAGE 2: ANALYSIS
Use the "analyst" agent.
- Pass the transcript path to the analyst
- If slides PDF provided: first extract slides using extract_slides tool, then pass slide images
- Agent should produce: facts_extracted.md, company_metadata.md, slides_analysis.md (if slides)
- The analyst has vision capability — pass slide PNG paths for multimodal analysis

### STAGE 3: WRITING
Use the "writer" agent.
- Pass paths to all analysis files
- Agent reads engine prompts and reference cases itself
- Agent produces: case_draft_v1.md

### STAGE 4: EDITING
Use the "editor" agent.
- Pass the draft path + transcript path (for fact-checking against source)
- Agent performs 3 passes: validation → strengthening → final format
- Agent produces: validation_report.md, case_final.md, [company]_READY.md

### ⛔ MANDATORY CHECKPOINT: HUMAN REVIEW
After the Editor produces _READY.md, you MUST:
1. Print the full path to the _READY.md file
2. Show a brief summary (title, section count, rich block count)
3. Ask the human to review the file and confirm before publishing
4. WAIT for explicit approval. Do NOT proceed to Stage 5 without it.

### STAGE 5: PUBLISHING
Use the "publisher" agent.
- Pass the _READY.md file path
- Agent publishes to WordPress as draft
- If no WP credentials: run dry-run mode
{publish_note}
{hitl_section}
---

## RULES

1. SEQUENTIAL EXECUTION: Complete each stage before starting the next
2. FILE PATHS: Always pass absolute file paths to agents
3. STATUS REPORTS: After each stage, briefly report:
   ✓ STAGE [N] COMPLETE
   Output: [list of files]
   Moving to: STAGE [N+1]
4. ERROR HANDLING: If an agent fails, report the error and ask user what to do
5. NO INVENTION: Never add facts, numbers, or details not in the source material
6. LANGUAGE: All case content in Russian. Pipeline logs in Russian.

## STARTING THE PIPELINE

1. Determine company name (from URL, transcript, or ask user)
2. Create working directory: cases/[company_name]_draft/
3. Begin Stage 1 (or Stage 2 if transcript provided)
4. Execute all stages sequentially
5. Report final summary with paths and stats

START NOW. Do not ask for confirmation — begin executing the pipeline.
"""


async def run_pipeline(
    youtube_url: str | None = None,
    transcript_path: str | None = None,
    slides_pdf: str | None = None,
    company_name: str | None = None,
    method: str = "youtube",
    skip_publish: bool = True,
    hitl_after: list[str] | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
) -> dict[str, Any]:
    """
    Run the full caseorium pipeline.

    Returns dict with:
    - success: bool
    - result: str (final message)
    - session_id: str
    - cost_usd: float
    - duration_ms: int
    """
    prompt = _build_orchestrator_prompt(
        youtube_url=youtube_url,
        transcript_path=transcript_path,
        slides_pdf=slides_pdf,
        company_name=company_name,
        method=method,
        skip_publish=skip_publish,
        hitl_after=hitl_after,
    )

    # Set unique run ID for metrics tracking
    run_id = f"pipeline_{uuid.uuid4().hex[:8]}"
    os.environ["PIPELINE_RUN_ID"] = run_id

    agents = get_all_agents()
    mcp_tools = create_pipeline_tools()

    # Build allowed tools — exclude publish tool when skip_publish=True
    allowed = [
        "Read", "Write", "Bash", "Glob", "Grep", "Agent",
        "mcp__caseorium__transcribe_youtube",
        "mcp__caseorium__extract_slides",
        "mcp__caseorium__metrics_task_started",
        "mcp__caseorium__metrics_task_completed",
        "mcp__caseorium__metrics_task_failed",
    ]
    if not skip_publish:
        allowed.append("mcp__caseorium__publish_to_wordpress")

    options = ClaudeAgentOptions(
        model=model or "sonnet",
        agents=agents,
        mcp_servers={"caseorium": mcp_tools},
        allowed_tools=allowed,
        permission_mode="acceptEdits",
        cwd=str(PROJECT_ROOT),
        max_turns=100,
        max_budget_usd=max_budget_usd or 5.0,
    )

    start_time = time.time()
    result_data = {
        "success": False,
        "result": "",
        "session_id": "",
        "cost_usd": 0.0,
        "duration_ms": 0,
    }

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result_data["success"] = not message.is_error
            result_data["result"] = message.result or ""
            result_data["session_id"] = message.session_id
            result_data["cost_usd"] = message.total_cost_usd or 0.0
            result_data["duration_ms"] = message.duration_ms

    elapsed = time.time() - start_time
    result_data["wall_time_sec"] = round(elapsed, 1)

    # Flush deferred metrics with real proportional cost
    flush_deferred_completions(run_id, result_data["cost_usd"])

    return result_data


async def run_pipeline_interactive(
    youtube_url: str | None = None,
    transcript_path: str | None = None,
    slides_pdf: str | None = None,
    company_name: str | None = None,
    method: str = "youtube",
    skip_publish: bool = True,
    hitl_after: list[str] | None = None,
    model: str | None = None,
    max_budget_usd: float | None = None,
) -> dict[str, Any]:
    """
    Run pipeline with streaming output — prints progress as it goes.
    """
    prompt = _build_orchestrator_prompt(
        youtube_url=youtube_url,
        transcript_path=transcript_path,
        slides_pdf=slides_pdf,
        company_name=company_name,
        method=method,
        skip_publish=skip_publish,
        hitl_after=hitl_after,
    )

    # Set unique run ID for metrics tracking
    run_id = f"pipeline_{uuid.uuid4().hex[:8]}"
    os.environ["PIPELINE_RUN_ID"] = run_id

    agents = get_all_agents()
    mcp_tools = create_pipeline_tools()

    # Build allowed tools — exclude publish tool when skip_publish=True
    allowed = [
        "Read", "Write", "Bash", "Glob", "Grep", "Agent",
        "mcp__caseorium__transcribe_youtube",
        "mcp__caseorium__extract_slides",
        "mcp__caseorium__metrics_task_started",
        "mcp__caseorium__metrics_task_completed",
        "mcp__caseorium__metrics_task_failed",
    ]
    if not skip_publish:
        allowed.append("mcp__caseorium__publish_to_wordpress")

    options = ClaudeAgentOptions(
        model=model or "sonnet",
        agents=agents,
        mcp_servers={"caseorium": mcp_tools},
        allowed_tools=allowed,
        permission_mode="acceptEdits",
        cwd=str(PROJECT_ROOT),
        max_turns=100,
        max_budget_usd=max_budget_usd or 5.0,
    )

    start_time = time.time()
    result_data = {
        "success": False,
        "result": "",
        "session_id": "",
        "cost_usd": 0.0,
        "duration_ms": 0,
    }

    print("\n🔄 Caseorium Pipeline starting...\n")

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result_data["success"] = not message.is_error
            result_data["result"] = message.result or ""
            result_data["session_id"] = message.session_id
            result_data["cost_usd"] = message.total_cost_usd or 0.0
            result_data["duration_ms"] = message.duration_ms

    elapsed = time.time() - start_time
    result_data["wall_time_sec"] = round(elapsed, 1)

    # Flush deferred metrics with real proportional cost
    flush_deferred_completions(run_id, result_data["cost_usd"])

    # Print summary
    status = "✅ SUCCESS" if result_data["success"] else "❌ FAILED"
    print(f"\n{'='*60}")
    print(f"  {status}")
    print(f"  Time: {result_data['wall_time_sec']}s")
    print(f"  Cost: ${result_data['cost_usd']:.4f}")
    print(f"  Session: {result_data['session_id']}")
    print(f"{'='*60}\n")

    if result_data["result"]:
        print(result_data["result"])

    return result_data
