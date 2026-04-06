#!/usr/bin/env python3
"""
Caseorium — AI Case Study Pipeline.

YouTube URL (or transcript) → 5-agent pipeline → WordPress draft.

Usage:
    # From YouTube video
    python main.py --youtube "https://youtube.com/watch?v=..." --company "Sber"

    # From existing transcript
    python main.py --transcript cases/sber_draft/transcript.md --company "Sber"

    # With presentation slides
    python main.py --youtube "..." --company "Sber" --slides presentation.pdf

    # Enable WordPress publishing (default: stops after Editor for review)
    python main.py --youtube "..." --company "Sber" --publish

    # High-quality transcription (Deepgram)
    python main.py --youtube "..." --company "Sber" --method deepgram

    # Human-in-the-loop after analysis and draft
    python main.py --youtube "..." --company "Sber" --hitl analyst,writer
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(
        description="Caseorium: AI Case Study Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python main.py --youtube "https://youtube.com/watch?v=abc" --company "VkusVill"
  python main.py --transcript transcript.md --slides presentation.pdf --company "Sber"
  python main.py --youtube "..." --company "T-Bank" --method deepgram --hitl analyst,writer
        """,
    )

    # Input sources (at least one required)
    input_group = parser.add_argument_group("Input (one required)")
    input_group.add_argument(
        "--youtube", "-y",
        help="YouTube video URL to transcribe",
    )
    input_group.add_argument(
        "--transcript", "-t",
        help="Path to existing transcript file",
    )

    # Optional inputs
    parser.add_argument(
        "--slides", "-s",
        help="Path to presentation PDF (for multimodal analysis)",
    )
    parser.add_argument(
        "--company", "-c",
        help="Company name (auto-detected from transcript if not provided)",
    )

    # Pipeline options
    parser.add_argument(
        "--method", "-m",
        choices=["youtube", "deepgram"],
        default="youtube",
        help="Transcription method (default: youtube)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Enable WordPress publishing (default: stop after Editor for human review)",
    )
    parser.add_argument(
        "--hitl",
        help="Comma-separated list of stages after which to pause for human review "
             "(e.g., 'analyst,writer')",
    )

    # Model and budget
    parser.add_argument(
        "--model",
        default="sonnet",
        help="Claude model for orchestrator (default: sonnet)",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=5.0,
        help="Max budget in USD (default: 5.0)",
    )

    args = parser.parse_args()

    # Validate input
    if not args.youtube and not args.transcript:
        parser.error("At least one of --youtube or --transcript is required")

    if args.transcript and not Path(args.transcript).exists():
        parser.error(f"Transcript file not found: {args.transcript}")

    if args.slides and not Path(args.slides).exists():
        parser.error(f"Slides PDF not found: {args.slides}")

    # Load .env
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

    # Parse HITL stages
    hitl_after = None
    if args.hitl:
        hitl_after = [s.strip() for s in args.hitl.split(",")]

    # Import here to avoid slow import at parse time
    from agents.pipeline import run_pipeline_interactive

    # Run
    result = asyncio.run(
        run_pipeline_interactive(
            youtube_url=args.youtube,
            transcript_path=args.transcript,
            slides_pdf=args.slides,
            company_name=args.company,
            method=args.method,
            skip_publish=not args.publish,
            hitl_after=hitl_after,
            model=args.model,
            max_budget_usd=args.budget,
        )
    )

    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
