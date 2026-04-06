#!/usr/bin/env python3
"""
Caseorium Web Server.

FastAPI backend that wraps the pipeline and serves a web UI.
Streams agent progress via Server-Sent Events (SSE).
"""

import asyncio
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# Load .env before anything else
load_dotenv(Path(__file__).parent / ".env")

app = FastAPI(title="Caseorium", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).parent
UPLOAD_DIR = PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# In-memory job store
jobs: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Pipeline runner with progress streaming
# ---------------------------------------------------------------------------

STAGES = [
    {"id": "transcriber", "name": "Транскрипция", "icon": "1"},
    {"id": "analyst", "name": "Анализ", "icon": "2"},
    {"id": "writer", "name": "Написание", "icon": "3"},
    {"id": "editor", "name": "Редактура", "icon": "4"},
    {"id": "publisher", "name": "Публикация", "icon": "5"},
]


def _find_draft_dir(company: str | None) -> Path | None:
    """Find the most recent *_draft directory in cases/."""
    cases_dir = PROJECT_ROOT / "cases"
    if not cases_dir.exists():
        return None

    # Try exact match first
    if company:
        for variant in [company, company.lower(), company.capitalize()]:
            d = cases_dir / f"{variant.replace(' ', '_')}_draft"
            if d.exists():
                return d

    # Fall back to most recently modified _draft dir
    draft_dirs = [d for d in cases_dir.iterdir()
                  if d.is_dir() and d.name.endswith("_draft") and d.name != "examples"]
    if draft_dirs:
        return max(draft_dirs, key=lambda d: d.stat().st_mtime)
    return None


def _find_ready_file(draft_dir: Path | None) -> Path | None:
    """Find the best output file in a draft directory."""
    if not draft_dir or not draft_dir.exists():
        return None
    for pattern in ["*_READY.md", "case_final.md", "case_draft_v3.md", "case_draft_v2.md"]:
        found = list(draft_dir.glob(pattern))
        if found:
            return max(found, key=lambda f: f.stat().st_mtime)
    return None


# File-based stage detection: which files signal which stage is done
# Stage 0 (Transcriber) → transcript.md
# Stage 1 (Analyst) → facts_extracted.md
# Stage 2 (Writer) → case_draft_v1.md
# Stage 3 (Editor) → case_final.md or *_READY.md
# Stage 4 (Publisher) → skip (we do it manually)
FILE_STAGE_MAP = [
    (0, "transcript.md"),
    (1, "facts_extracted.md"),
    (2, "case_draft_v1.md"),
    (3, "case_final.md"),
]


async def _file_watcher(job: dict, company: str | None):
    """Watch draft directory for new files and update job stage accordingly.
    Only considers files modified AFTER the job started."""
    detected_stage = -1
    job_start = job.get("started_at", time.time())

    while job["status"] == "running":
        draft_dir = _find_draft_dir(company)
        if draft_dir:
            job["draft_dir"] = str(draft_dir)
            for stage_idx, filename in FILE_STAGE_MAP:
                f = draft_dir / filename
                # Also try glob for *_READY.md pattern
                matches = [f] if f.exists() else list(draft_dir.glob(f"*{filename}"))
                for match in matches:
                    # Only count files created/modified AFTER job started
                    if match.exists() and match.stat().st_mtime >= job_start:
                        if stage_idx > detected_stage:
                            detected_stage = stage_idx
                            next_stage = min(stage_idx + 1, len(STAGES) - 1)
                            if job["current_stage"] < next_stage:
                                job["current_stage"] = next_stage
                                job["events"].append({
                                    "type": "stage",
                                    "stage": next_stage,
                                    "name": STAGES[next_stage]["name"],
                                    "time": time.time(),
                                })
                        break

            # Check for _READY.md created after job start
            ready = _find_ready_file(draft_dir)
            if ready and "_READY" in ready.name and ready.stat().st_mtime >= job_start:
                job["current_stage"] = len(STAGES) - 1
                if not any(e.get("stage") == len(STAGES) - 1 for e in job["events"]):
                    job["events"].append({
                        "type": "stage",
                        "stage": len(STAGES) - 1,
                        "name": STAGES[-1]["name"],
                        "time": time.time(),
                    })

        await asyncio.sleep(3)


async def run_pipeline_job(job_id: str, params: dict):
    """Run the pipeline and update job state with progress."""
    job = jobs[job_id]
    job["status"] = "running"
    job["started_at"] = time.time()

    # Start file watcher in parallel
    company = params.get("company_name")
    watcher_task = asyncio.create_task(_file_watcher(job, company))

    try:
        from agents.pipeline import _build_orchestrator_prompt
        from agents.definitions import get_all_agents
        from agents.tools import create_pipeline_tools
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        # Set unique run ID for metrics tracking
        os.environ["PIPELINE_RUN_ID"] = f"web_{job_id}"

        skip_publish = params.get("skip_publish", True)

        prompt = _build_orchestrator_prompt(
            youtube_url=params.get("youtube_url"),
            transcript_path=params.get("transcript_path"),
            slides_pdf=params.get("slides_pdf"),
            company_name=company,
            method=params.get("method", "youtube"),
            skip_publish=skip_publish,
        )

        agents = get_all_agents()
        mcp_tools = create_pipeline_tools()

        # Exclude publish tool when skip_publish — human reviews first
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
            model=params.get("model", "sonnet"),
            agents=agents,
            mcp_servers={"caseorium": mcp_tools},
            allowed_tools=allowed,
            permission_mode="acceptEdits",
            cwd=str(PROJECT_ROOT),
            max_turns=100,
            max_budget_usd=params.get("budget", 5.0),
        )

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                job["success"] = not message.is_error
                job["result_text"] = message.result or ""
                job["cost_usd"] = message.total_cost_usd or 0.0
                job["session_id"] = message.session_id

        # Flush deferred metrics with real proportional cost
        from tools.metrics import flush_deferred_completions
        flush_deferred_completions(f"web_{job_id}", job.get("cost_usd", 0.0))

        # Find output file
        draft_dir = _find_draft_dir(company)
        ready_file = _find_ready_file(draft_dir)

        if ready_file:
            job["ready_file"] = str(ready_file)
            job["case_text"] = ready_file.read_text(encoding="utf-8")

        job["status"] = "done" if job.get("success") else "error"

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)
        job["events"].append({"type": "error", "message": str(e), "time": time.time()})
        # Flush whatever stages completed before the error
        from tools.metrics import flush_deferred_completions
        flush_deferred_completions(f"web_{job_id}", job.get("cost_usd", 0.0))

    # Stop the file watcher
    watcher_task.cancel()
    job["finished_at"] = time.time()


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/run")
async def start_pipeline(
    youtube_url: str = Form(default=""),
    company_name: str = Form(default=""),
    method: str = Form(default="youtube"),
    slides: UploadFile | None = File(default=None),
    speaker_photo: UploadFile | None = File(default=None),
):
    """Start a new pipeline run."""
    job_id = str(uuid.uuid4())[:8]

    params = {
        "youtube_url": youtube_url or None,
        "company_name": company_name or None,
        "method": method,
        "skip_publish": True,  # always skip publish initially
    }

    # Save uploaded files
    if slides and slides.filename:
        slides_path = UPLOAD_DIR / f"{job_id}_{slides.filename}"
        with open(slides_path, "wb") as f:
            shutil.copyfileobj(slides.file, f)
        params["slides_pdf"] = str(slides_path)

    if speaker_photo and speaker_photo.filename:
        photo_path = UPLOAD_DIR / f"{job_id}_{speaker_photo.filename}"
        with open(photo_path, "wb") as f:
            shutil.copyfileobj(speaker_photo.file, f)
        params["speaker_photo"] = str(photo_path)

    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "params": params,
        "current_stage": 0,
        "events": [{"type": "stage", "stage": 0, "name": STAGES[0]["name"], "time": time.time()}],
        "success": False,
        "result_text": "",
        "case_text": "",
        "ready_file": "",
        "cost_usd": 0.0,
        "error": "",
    }

    # Run in background
    asyncio.create_task(run_pipeline_job(job_id, params))

    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def stream_status(job_id: str):
    """SSE stream of pipeline progress."""
    if job_id not in jobs:
        return {"error": "Job not found"}

    async def event_stream():
        last_event_idx = 0
        last_stage = -1

        while True:
            job = jobs.get(job_id)
            if not job:
                break

            # Send new events
            while last_event_idx < len(job["events"]):
                evt = job["events"][last_event_idx]
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                last_event_idx += 1

            # Send stage update
            if job["current_stage"] != last_stage:
                last_stage = job["current_stage"]
                yield f"data: {json.dumps({'type': 'progress', 'stage': last_stage}, ensure_ascii=False)}\n\n"

            # Send completion
            if job["status"] in ("done", "error"):
                final = {
                    "type": "complete",
                    "success": job.get("success", False),
                    "case_text": job.get("case_text", ""),
                    "cost_usd": job.get("cost_usd", 0),
                    "error": job.get("error", ""),
                }
                yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/save/{job_id}")
async def save_edits(job_id: str, text: str = Form(...)):
    """Save edited case text back to the _READY.md file."""
    job = jobs.get(job_id)
    if not job:
        return {"error": "Job not found"}

    ready_file = job.get("ready_file")
    if ready_file and Path(ready_file).exists():
        Path(ready_file).write_text(text, encoding="utf-8")
        job["case_text"] = text
        return {"success": True, "file": ready_file}

    return {"error": "No ready file found"}


@app.post("/api/complete/{job_id}")
async def force_complete(job_id: str):
    """Force-complete a stuck job by loading the ready file."""
    job = jobs.get(job_id)
    if not job:
        return {"error": "Job not found"}

    # Scan for ready files
    cases_dir = PROJECT_ROOT / "cases"
    ready_file = None
    for d in sorted(cases_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not d.is_dir() or not d.name.endswith("_draft"):
            continue
        for pattern in ["*_READY.md", "case_final.md"]:
            found = list(d.glob(pattern))
            if found:
                ready_file = max(found, key=lambda f: f.stat().st_mtime)
                break
        if ready_file:
            break

    if ready_file:
        job["ready_file"] = str(ready_file)
        job["case_text"] = ready_file.read_text(encoding="utf-8")
        job["status"] = "done"
        job["success"] = True
        job["events"].append({"type": "complete", "success": True,
                              "case_text": job["case_text"], "cost_usd": 0, "error": ""})
        return {"success": True, "file": str(ready_file)}

    return {"error": "No ready file found"}


@app.post("/api/publish/{job_id}")
async def publish_to_wp(job_id: str):
    """Publish the case to WordPress."""
    job = jobs.get(job_id)
    if not job or not job.get("ready_file"):
        return {"error": "No case to publish"}

    try:
        from tools.publish_to_wp_v2 import (
            parse_ready_md, build_sections, build_payload,
            build_rankmath_meta, WordPressClient, load_config,
        )

        config = load_config()
        if not config["user"] or not config["password"]:
            return {"error": "WordPress credentials not configured"}

        wp = WordPressClient(config["url"], config["user"], config["password"])
        data = parse_ready_md(job["ready_file"])

        # Auto-detect slides directory next to the ready file
        ready_path = Path(job["ready_file"])
        slides_dir = ready_path.parent / "slides"
        slides_dir_str = str(slides_dir) if slides_dir.exists() else None

        sections = build_sections(data, slides_dir=slides_dir_str, wp_client=wp)
        payload = build_payload(data, sections)
        result = wp.create_case(payload)

        if result["success"]:
            # Update Rank Math SEO
            seo_meta = build_rankmath_meta(data)
            if seo_meta:
                wp.update_rankmath_meta(result["id"], seo_meta)

            return {
                "success": True,
                "post_id": result["id"],
                "view_url": result["link"],
                "edit_url": result["edit_link"],
            }

        return {"error": result.get("error", "Unknown error")}

    except Exception as e:
        return {"error": str(e)}


@app.get("/api/load-latest")
async def load_latest_case():
    """Load the most recent _READY.md or case_final.md — useful after restart."""
    cases_dir = PROJECT_ROOT / "cases"
    ready_file = None
    for d in sorted(cases_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not d.is_dir() or not d.name.endswith("_draft"):
            continue
        for pattern in ["*_READY.md", "case_final.md"]:
            found = list(d.glob(pattern))
            if found:
                ready_file = max(found, key=lambda f: f.stat().st_mtime)
                break
        if ready_file:
            break

    if not ready_file:
        return {"error": "No case files found"}

    text = ready_file.read_text(encoding="utf-8")
    # Create a virtual job so save/publish work
    job_id = "latest"
    jobs[job_id] = {
        "id": job_id, "status": "done", "params": {}, "current_stage": 4,
        "events": [], "success": True, "result_text": "", "case_text": text,
        "ready_file": str(ready_file), "cost_usd": 0.0, "error": "",
    }
    return {"success": True, "job_id": job_id, "file": str(ready_file), "case_text": text}


@app.get("/api/jobs")
async def list_jobs():
    """List all jobs."""
    return [
        {
            "id": j["id"],
            "status": j["status"],
            "company": j["params"].get("company_name", ""),
            "current_stage": j["current_stage"],
            "cost_usd": j.get("cost_usd", 0),
        }
        for j in jobs.values()
    ]


# ---------------------------------------------------------------------------
# Serve frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = PROJECT_ROOT / "web" / "index.html"
    return html_path.read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
