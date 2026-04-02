#!/usr/bin/env python3
"""
Extract individual slides from PDF presentation as PNG images.

Used in Caseorium pipeline to get illustrations from speaker presentations.

Usage:
    python3 extract_slides.py <pdf_path> [--output dir] [--dpi 150]

Examples:
    python3 extract_slides.py presentation.pdf
    python3 extract_slides.py presentation.pdf --output cases/sber_draft/slides/
"""

import argparse
import os
import sys


def extract_slides_pymupdf(pdf_path: str, output_dir: str, dpi: int = 150) -> list:
    """Extract slides using PyMuPDF (fitz) — fast, no external deps."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    slide_paths = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        # Render page to image
        zoom = dpi / 72  # 72 is default DPI
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        slide_filename = f"slide_{page_num + 1:02d}.png"
        slide_path = os.path.join(output_dir, slide_filename)
        pix.save(slide_path)
        slide_paths.append(slide_path)

    doc.close()
    return slide_paths


def main():
    parser = argparse.ArgumentParser(description="Extract slides from PDF as PNG images")
    parser.add_argument("pdf_path", help="Path to PDF file")
    parser.add_argument("--output", "-o", default=None, help="Output directory (default: slides/ next to PDF)")
    parser.add_argument("--dpi", type=int, default=150, help="Output image DPI (default: 150)")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"Error: File not found: {args.pdf_path}")
        sys.exit(1)

    output_dir = args.output or os.path.join(os.path.dirname(args.pdf_path), "slides")
    os.makedirs(output_dir, exist_ok=True)

    print(f"Extracting slides from: {args.pdf_path}")
    print(f"Output directory: {output_dir}")
    print(f"DPI: {args.dpi}")

    slide_paths = extract_slides_pymupdf(args.pdf_path, output_dir, args.dpi)

    print(f"\nExtracted {len(slide_paths)} slides:")
    for path in slide_paths:
        size_kb = os.path.getsize(path) / 1024
        print(f"  {os.path.basename(path)} ({size_kb:.0f} KB)")

    return slide_paths


if __name__ == "__main__":
    main()
