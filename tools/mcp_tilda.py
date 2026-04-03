#!/usr/bin/env python3
"""
MCP Server for Tilda → WordPress case migration.

Gives Claude direct structured access to Tilda pages:
- Fetch a page and get typed content blocks
- List all published cases
- Get page metadata (title, author, SEO)

Usage:
    Add to Claude Code settings (.claude/settings.json):
    {
      "mcpServers": {
        "tilda": {
          "command": "python3",
          "args": ["/path/to/tools/mcp_tilda.py"]
        }
      }
    }
"""

import re
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "tilda",
    instructions=(
        "MCP server for reading generation-ai.ru Tilda pages. "
        "Use fetch_case to get structured content blocks from a case URL. "
        "Use list_cases to discover all published cases. "
        "Blocks are returned with their Tilda type, so you can map them to WordPress ACF layouts."
    ),
)

BASE_URL = "https://generation-ai.ru"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CaseoriumBot/1.0)"}

# Tilda block types to skip (nav, popups, spacers, cookies)
SKIP_TYPES = {"131", "257", "383", "394", "657", "702", "121", "217", "270"}


def _fetch_soup(url: str) -> BeautifulSoup:
    """Fetch a URL and return parsed BeautifulSoup."""
    resp = requests.get(url, timeout=30, headers=HEADERS)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def _parse_block(rec: Tag) -> dict[str, Any] | None:
    """Parse a single Tilda t-rec block into a structured dict."""
    style = rec.get("style", "")
    if "display:none" in style or "display: none" in style:
        return None

    rtype = rec.get("data-record-type", "")
    rec_id = rec.get("id", "")

    if rtype in SKIP_TYPES:
        return None

    text = rec.get_text(" ", strip=True)
    if not text and not rec.find("img"):
        return None

    block = {
        "id": rec_id,
        "tilda_type": rtype,
        "text": text[:2000],  # Cap text length
    }

    # Classify by Tilda block type
    if rtype == "758":
        links = rec.find_all("a")
        block["block_type"] = "breadcrumb"
        block["path"] = [a.get_text(strip=True) for a in links]
        return block

    if rtype == "513":
        block["block_type"] = "author_meta"
        parts = text.split("\n")
        parts = [p.strip() for p in rec.get_text("\n", strip=True).split("\n") if p.strip()]
        block["date"] = parts[0] if len(parts) >= 1 else ""
        block["author_name"] = parts[1] if len(parts) >= 2 else ""
        block["author_role"] = parts[2] if len(parts) >= 3 else ""
        return block

    if rtype == "179":
        block["block_type"] = "hero_title"
        return block

    if rtype == "1206":
        block["block_type"] = "company_card"
        return block

    if rtype == "673":
        block["block_type"] = "accent_quote"
        block["wp_layout"] = "layer_accent_text"
        return block

    if rtype == "510":
        block["block_type"] = "inset_text"
        block["wp_layout"] = "note_text"
        return block

    if rtype == "3":
        block["block_type"] = "image"
        img = rec.find("img")
        if img:
            src = img.get("data-original", "") or img.get("src", "")
            if src and not src.startswith("http"):
                src = BASE_URL + ("/" if not src.startswith("/") else "") + src
            block["image_url"] = src
            block["caption"] = rec.get_text(strip=True)
        block["wp_layout"] = "layer_media"
        return block

    if rtype == "778":
        block["block_type"] = "carousel"
        # Extract slide texts
        slides = []
        for wrapper in rec.find_all(class_=re.compile(r"t778", re.I)):
            slide_text = wrapper.get_text(" ", strip=True)
            if slide_text and len(slide_text) > 5:
                slides.append(slide_text)
        if not slides:
            slides = [text]
        block["slides"] = slides
        block["wp_layout"] = "layer_columns"
        return block

    if rtype == "60":
        h2 = rec.find("h2")
        block["block_type"] = "text_section"
        block["heading"] = h2.get_text(strip=True) if h2 else ""
        # Extract structured HTML content
        body_parts = []
        for el in rec.find_all(["p", "ul", "ol", "h3", "h4", "blockquote"]):
            if h2 and el == h2:
                continue
            if el.find_parent("h2"):
                continue
            el_text = el.get_text(strip=True)
            if el_text:
                if el.name in ("ul", "ol"):
                    items = [li.get_text(strip=True) for li in el.find_all("li")]
                    body_parts.append({"type": "list", "ordered": el.name == "ol", "items": items})
                elif el.name in ("h3", "h4"):
                    body_parts.append({"type": "subheading", "level": int(el.name[1]), "text": el_text})
                elif el.name == "blockquote":
                    body_parts.append({"type": "quote", "text": el_text})
                else:
                    body_parts.append({"type": "paragraph", "text": el_text})
        block["content"] = body_parts
        block["wp_layout"] = "custom_text"
        return block

    if rtype == "404":
        block["block_type"] = "related_cases"
        # Extract case links
        links = []
        for a in rec.find_all("a", href=True):
            href = a.get("href", "")
            link_text = a.get_text(strip=True)
            if "/cases/" in href and link_text:
                links.append({"url": href, "title": link_text})
        block["cases"] = links
        return block

    if rtype == "396":
        # Footer or artboard — check content
        if "Другие кейсы" in text:
            block["block_type"] = "related_header"
            return block
        if "МЕНЮ" in text or "Политика" in text:
            block["block_type"] = "footer"
            return None  # skip footer
        block["block_type"] = "artboard"
        return block

    # Unknown type — return as generic
    if text and len(text) > 20:
        block["block_type"] = "unknown"
        return block

    return None


@mcp.tool()
def fetch_case(url: str) -> dict[str, Any]:
    """Fetch a Tilda case page and return structured content blocks.

    Returns the page broken into typed blocks with Tilda type info
    and suggested WordPress ACF layout mapping.

    Args:
        url: Full URL of the case page (e.g. https://generation-ai.ru/cases/aviasales-genai-quality)
    """
    soup = _fetch_soup(url)

    # Extract SEO meta
    meta = {}
    og_title = soup.find("meta", property="og:title")
    if og_title:
        meta["og_title"] = og_title.get("content", "")
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        meta["og_description"] = og_desc.get("content", "")
    og_image = soup.find("meta", property="og:image")
    if og_image:
        meta["og_image"] = og_image.get("content", "")
    title_tag = soup.find("title")
    if title_tag:
        meta["page_title"] = title_tag.get_text(strip=True)

    # Parse all content blocks
    blocks = []
    hit_footer = False
    for rec in soup.find_all("div", class_="t-rec"):
        if hit_footer:
            break
        block = _parse_block(rec)
        if block:
            if block.get("block_type") in ("footer", "related_header"):
                hit_footer = True
                continue
            blocks.append(block)

    # Summary
    block_types = {}
    for b in blocks:
        bt = b.get("block_type", "unknown")
        block_types[bt] = block_types.get(bt, 0) + 1

    return {
        "url": url,
        "meta": meta,
        "block_count": len(blocks),
        "block_types_summary": block_types,
        "blocks": blocks,
    }


@mcp.tool()
def list_cases() -> list[dict[str, str]]:
    """List all published cases on generation-ai.ru/cases.

    Returns a list of case URLs with titles.
    """
    soup = _fetch_soup(BASE_URL + "/cases")

    cases = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/cases/" in href and href != "/cases/" and href not in seen:
            title = a.get_text(strip=True)
            if title and len(title) > 5:
                full_url = href if href.startswith("http") else BASE_URL + href
                cases.append({"url": full_url, "title": title})
                seen.add(href)

    return cases


@mcp.tool()
def fetch_block_html(url: str, block_id: str) -> str:
    """Fetch the raw HTML of a specific block by its rec ID.

    Use this when you need the exact HTML structure of a block
    for precise WordPress conversion.

    Args:
        url: Full URL of the case page
        block_id: The block's rec ID (e.g. 'rec1937500601')
    """
    soup = _fetch_soup(url)
    rec = soup.find("div", id=block_id)
    if rec:
        return str(rec)[:5000]
    return "Block not found: %s" % block_id


if __name__ == "__main__":
    mcp.run()
