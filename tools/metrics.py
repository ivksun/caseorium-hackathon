"""
Metrics client for the AI hackathon dashboard.

Sends events (task_started, task_completed, task_failed, etc.) to the
agent-metrics API so the team's AI-revenue is tracked on the leaderboard.

Supports deferred completions: MCP tools record stage latency during pipeline
execution, then flush_deferred_completions() sends all task_completed events
with proportional cost after the pipeline finishes and total_cost_usd is known.
"""

import dataclasses
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hours a human would spend on each pipeline stage
HOURS_SAVED_BY_AGENT = {
    "transcriber": 1.0,   # manual transcription of a 40-60 min talk
    "analyst": 1.0,        # reading transcript + extracting facts + slides
    "writer": 3.0,         # writing a full 3000-4000 word case study
    "editor": 2.0,         # 3 editing passes: fact-check, style, layout
    "publisher": 1.0,      # parsing → ACF blocks → WP upload
}

# Proportional cost weights per agent (model complexity + expected token usage)
AGENT_COST_WEIGHT = {
    "transcriber": 1,   # haiku, simple wrapper
    "analyst": 5,        # sonnet, medium context
    "writer": 5,         # sonnet, long output
    "editor": 7,         # sonnet, 3 passes, high effort
    "publisher": 1,      # haiku, simple task
}

# Fallback cost estimates in RUB (used when total_cost_usd is 0 or unavailable)
COST_FALLBACK_RUB = {
    "transcriber": 0.5,
    "analyst": 3.0,
    "writer": 4.0,
    "editor": 5.0,
    "publisher": 0.5,
}

USD_TO_RUB = float(os.getenv("USD_TO_RUB", "100.0"))


# ---------------------------------------------------------------------------
# Deferred completions store
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class DeferredCompletion:
    """A recorded stage completion, waiting for cost calculation."""
    agent: str
    task_type: str
    latency: float
    tokens: int
    hours_saved: float


# run_id -> list of completions recorded during that pipeline run
_deferred_completions: dict[str, list[DeferredCompletion]] = {}


def defer_task_completed(
    run_id: str,
    agent: str,
    task_type: str,
    latency: float,
    tokens: int = 0,
) -> None:
    """Record a stage completion for later flushing with real cost."""
    record = DeferredCompletion(
        agent=agent,
        task_type=task_type,
        latency=latency,
        tokens=tokens,
        hours_saved=HOURS_SAVED_BY_AGENT.get(agent, 0.5),
    )
    _deferred_completions.setdefault(run_id, []).append(record)
    logger.info("Deferred task_completed for %s (run=%s, latency=%.1fs)", agent, run_id, latency)


def flush_deferred_completions(run_id: str, total_cost_usd: float) -> list[dict]:
    """
    Send all deferred task_completed events with proportional cost.

    Calculates each agent's cost as:
        cost_rub = total_cost_usd * (agent_weight / sum_of_completed_weights) * USD_TO_RUB

    If total_cost_usd <= 0, falls back to COST_FALLBACK_RUB estimates.
    """
    records = _deferred_completions.pop(run_id, [])
    if not records:
        logger.warning("No deferred completions to flush for run=%s", run_id)
        return []

    client = get_metrics_client()
    use_real_cost = total_cost_usd and total_cost_usd > 0

    if use_real_cost:
        total_weight = sum(AGENT_COST_WEIGHT.get(dc.agent, 1) for dc in records)

    results = []
    for dc in records:
        if use_real_cost:
            weight = AGENT_COST_WEIGHT.get(dc.agent, 1)
            cost_rub = total_cost_usd * (weight / total_weight) * USD_TO_RUB
        else:
            cost_rub = COST_FALLBACK_RUB.get(dc.agent, 1.0)

        result = client.task_completed(
            distinct_id=run_id,
            agent=dc.agent,
            task_type=dc.task_type,
            latency=dc.latency,
            hours_saved=dc.hours_saved,
            cost=round(cost_rub, 2),
            tokens=dc.tokens,
        )
        results.append(result)
        logger.info(
            "Flushed task_completed for %s: cost=%.2f RUB (run=%s)",
            dc.agent, cost_rub, run_id,
        )

    return results


def clear_deferred(run_id: str) -> None:
    """Remove deferred completions without sending (for cleanup)."""
    _deferred_completions.pop(run_id, None)


# ---------------------------------------------------------------------------
# Metrics API client
# ---------------------------------------------------------------------------

class MetricsClient:
    """Client for the hackathon metrics API."""

    def __init__(self):
        self.api_url = os.getenv(
            "METRICS_API_URL",
            "https://agent-metrics.lo.test-ai.net/api/v1",
        )
        self.api_key = os.getenv("METRICS_API_KEY", "")
        self.team_id = os.getenv("TEAM_ID", "")
        self._client = httpx.Client(timeout=10.0)

    def _send_event(self, event: str, properties: dict) -> dict:
        """Send an event to the metrics API. Non-blocking on failure."""
        if not self.api_key or not self.team_id:
            logger.warning("Metrics not configured (missing METRICS_API_KEY or TEAM_ID)")
            return {"status": "skipped", "reason": "not configured"}

        payload = {
            "event": event,
            "properties": {
                "team_id": self.team_id,
                **properties,
            },
        }

        try:
            response = self._client.post(
                f"{self.api_url}/event",
                headers={
                    "Content-Type": "application/json",
                    "X-API-Key": self.api_key,
                },
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
            logger.info("Metrics event '%s' sent: %s", event, result)
            return result
        except Exception as e:
            logger.warning("Failed to send metrics event '%s': %s", event, e)
            return {"status": "error", "reason": str(e)}

    def task_started(
        self,
        distinct_id: str,
        agent: str,
        task_type: str,
    ) -> dict:
        return self._send_event("task_started", {
            "distinct_id": distinct_id,
            "agent": agent,
            "task_type": task_type,
        })

    def task_completed(
        self,
        distinct_id: str,
        agent: str,
        task_type: str,
        latency: float,
        hours_saved: float,
        cost: float,
        tokens: int,
    ) -> dict:
        return self._send_event("task_completed", {
            "distinct_id": distinct_id,
            "agent": agent,
            "task_type": task_type,
            "latency": round(latency, 2),
            "hours_saved": round(min(hours_saved, 8.0), 2),
            "cost": round(cost, 2),
            "tokens": tokens,
        })

    def task_failed(
        self,
        distinct_id: str,
        agent: str,
        task_type: str,
        error_type: str,
        latency: Optional[float] = None,
    ) -> dict:
        props = {
            "distinct_id": distinct_id,
            "agent": agent,
            "task_type": task_type,
            "error_type": error_type,
        }
        if latency is not None:
            props["latency"] = round(latency, 2)
        return self._send_event("task_failed", props)

    def user_feedback(
        self,
        distinct_id: str,
        agent: str,
        task_type: str,
        rating: int,
    ) -> dict:
        return self._send_event("user_feedback", {
            "distinct_id": distinct_id,
            "agent": agent,
            "task_type": task_type,
            "rating": max(1, min(5, rating)),
        })

    def evaluation_result(
        self,
        distinct_id: str,
        agent: str,
        task_type: str,
        score: float,
        evaluation_type: str,
    ) -> dict:
        return self._send_event("evaluation_result", {
            "distinct_id": distinct_id,
            "agent": agent,
            "task_type": task_type,
            "score": round(max(0.0, min(1.0, score)), 2),
            "evaluation_type": evaluation_type,
        })


# Singleton for use across the pipeline
_client: Optional[MetricsClient] = None


def get_metrics_client() -> MetricsClient:
    """Get or create the singleton metrics client."""
    global _client
    if _client is None:
        _client = MetricsClient()
    return _client
