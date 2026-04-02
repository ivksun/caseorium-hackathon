#!/usr/bin/env python3
"""
YouTube Video Transcription Tool for Caseorium Pipeline.

Two modes:
1. YouTube auto-captions (free, instant, lower quality)
2. Deepgram API (high quality, needs API key, $200 free credits)

Usage:
    python3 transcribe_youtube.py <youtube_url> [--output dir] [--method deepgram|youtube]

Examples:
    python3 transcribe_youtube.py "https://www.youtube.com/watch?v=MFRvuV6rjss"
    python3 transcribe_youtube.py "https://www.youtube.com/watch?v=MFRvuV6rjss" --method deepgram
    python3 transcribe_youtube.py "https://www.youtube.com/watch?v=MFRvuV6rjss" --output cases/sber_draft/
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_video_id(url: str) -> str:
    """Extract video ID from various YouTube URL formats."""
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise ValueError(f"Could not extract video ID from: {url}")


def get_video_title(url: str) -> str:
    """Get video title using pytubefix."""
    try:
        from pytubefix import YouTube
        yt = YouTube(url)
        return yt.title
    except Exception:
        return "unknown"


def transcribe_youtube_captions(video_id: str) -> dict:
    """Fetch YouTube auto-generated captions (free, instant)."""
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    transcript = api.fetch(video_id=video_id, languages=['ru', 'en'])

    segments = []
    full_text_parts = []

    for entry in transcript.snippets:
        segments.append({
            'start': entry.start,
            'duration': entry.duration,
            'text': entry.text
        })
        full_text_parts.append(entry.text)

    full_text = ' '.join(full_text_parts)

    return {
        'method': 'youtube_captions',
        'text': full_text,
        'segments': segments,
        'char_count': len(full_text),
        'word_count': len(full_text.split()),
    }


def download_audio(url: str, output_dir: str) -> str:
    """Download audio from YouTube using pytubefix."""
    from pytubefix import YouTube

    yt = YouTube(url)
    audio_stream = yt.streams.filter(only_audio=True).order_by('abr').desc().first()
    if not audio_stream:
        raise RuntimeError("No audio stream found for this video")

    path = audio_stream.download(output_path=output_dir, filename="audio.mp4")
    return path


def transcribe_deepgram(url: str, api_key: str) -> dict:
    """Transcribe using Deepgram API v6 (high quality)."""
    import json as json_mod
    import urllib.request

    with tempfile.TemporaryDirectory() as tmpdir:
        print("  Downloading audio from YouTube...")
        audio_path = download_audio(url, tmpdir)
        audio_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        print(f"  Audio downloaded: {audio_size_mb:.1f} MB")

        print("  Sending to Deepgram for transcription...")

        with open(audio_path, "rb") as f:
            audio_data = f.read()

        # Use REST API directly for reliability across SDK versions
        api_url = (
            "https://api.deepgram.com/v1/listen?"
            "model=nova-3&language=ru&smart_format=true&"
            "paragraphs=true&diarize=true&punctuate=true"
        )

        # Detect content type from file extension
        content_type = "audio/mp4"
        if audio_path.endswith(".mp3"):
            content_type = "audio/mp3"
        elif audio_path.endswith(".webm"):
            content_type = "audio/webm"

        req = urllib.request.Request(
            api_url,
            data=audio_data,
            headers={
                "Authorization": f"Token {api_key}",
                "Content-Type": content_type,
            },
            method="POST"
        )

        print("  Waiting for Deepgram response (may take a few minutes)...")
        with urllib.request.urlopen(req, timeout=600) as resp:
            response_data = json_mod.loads(resp.read().decode())

        results = response_data["results"]
        channels = results["channels"]
        alt = channels[0]["alternatives"][0]
        transcript_text = alt["transcript"]

        # Build text with speaker labels from paragraphs if available
        speaker_text_parts = []
        paragraphs = alt.get("paragraphs", {}).get("paragraphs", [])
        if paragraphs:
            for para in paragraphs:
                speaker = para.get("speaker")
                speaker_label = f"\n[Спикер {speaker}]\n" if speaker is not None else ""
                sentences = ' '.join(s["text"] for s in para.get("sentences", []))
                speaker_text_parts.append(f"{speaker_label}{sentences}")

        final_text = '\n'.join(speaker_text_parts) if speaker_text_parts else transcript_text

        return {
            'method': 'deepgram',
            'text': final_text,
            'raw_transcript': transcript_text,
            'char_count': len(final_text),
            'word_count': len(final_text.split()),
        }


def save_transcript(result: dict, output_dir: str, video_title: str, video_url: str) -> str:
    """Save transcript to markdown file."""
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "transcript.md")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# Транскрипт: {video_title}\n\n")
        f.write(f"**Источник:** {video_url}\n")
        f.write(f"**Метод:** {result['method']}\n")
        f.write(f"**Символов:** {result['char_count']} | **Слов:** {result['word_count']}\n\n")
        f.write("---\n\n")
        f.write(result['text'])
        f.write("\n")

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Transcribe YouTube video for Caseorium pipeline")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("--output", "-o", default=None, help="Output directory (default: current dir)")
    parser.add_argument("--method", "-m", choices=["youtube", "deepgram"], default="youtube",
                        help="Transcription method (default: youtube)")
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    output_dir = args.output or "."

    print(f"Video ID: {video_id}")
    print(f"Method: {args.method}")

    # Get video title
    title = get_video_title(args.url)
    print(f"Title: {title}")

    if args.method == "youtube":
        print("Fetching YouTube auto-captions...")
        result = transcribe_youtube_captions(video_id)
    elif args.method == "deepgram":
        api_key = os.environ.get("DEEPGRAM_API_KEY")
        if not api_key:
            env_path = Path(__file__).parent.parent.parent.parent / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("DEEPGRAM_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"')
            if not api_key:
                print("Error: DEEPGRAM_API_KEY not set. Set env var or add to .env file.")
                sys.exit(1)
        print("Transcribing with Deepgram...")
        result = transcribe_deepgram(args.url, api_key)

    output_path = save_transcript(result, output_dir, title, args.url)

    print(f"\n{'='*50}")
    print(f"Transcription complete!")
    print(f"  Method: {result['method']}")
    print(f"  Characters: {result['char_count']}")
    print(f"  Words: {result['word_count']}")
    print(f"  Saved to: {output_path}")
    print(f"{'='*50}")

    return output_path


if __name__ == "__main__":
    main()
