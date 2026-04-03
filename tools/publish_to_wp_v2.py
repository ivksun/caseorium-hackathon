#!/usr/bin/env python3
"""
Smart WordPress publisher for Caseorium pipeline (v2).

Parses _READY.md and publishes to WordPress using the full set of ACF blocks:
- custom_text: main text content with headings
- layer_media: images from slides (uploaded to WP media library)
- layer_columns: metrics with colored headers
- layer_waypoint: numbered step-by-step processes
- note_text: highlighted text in a frame
- layer_accent_text: text block with background
- layer_text_columns: two-column comparisons
- layer_feedback: speaker quotes with photo
- layer_cta_form: call-to-action buttons
- layer_banner: banner with image + text

Usage:
    python3 publish_to_wp_v2.py <case_file> [--slides-dir dir] [--dry-run] [--publish]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    config = {
        'url': os.environ.get('WP_URL', ''),
        'user': os.environ.get('WP_USER', ''),
        'password': os.environ.get('WP_APP_PASSWORD', ''),
    }
    for env_path in [
        Path(__file__).parent.parent / ".env",
        Path(__file__).parent.parent.parent / ".env",
    ]:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    k, v = line.split('=', 1)
                    v = v.strip().strip('"')
                    if k == 'WP_URL' and not config['url']:
                        config['url'] = v
                    elif k == 'WP_USER' and not config['user']:
                        config['user'] = v
                    elif k == 'WP_APP_PASSWORD' and not config['password']:
                        config['password'] = v
    if not config['url']:
        config['url'] = 'https://testforagents.just-ai.ru'
    return config


OTRASLI_MAP = {
    'hr': 32, 'ит': 33, 'it': 33,
    'контент и медиа': 34, 'маркетинг': 50,
    'медицина': 35, 'промышленность': 36,
    'ритейл': 37, 'услуги': 38,
    'финансы': 39, 'finance': 39,
}

ZADACHI_MAP = {
    'hr': 40, 'аналитика данных': 41,
    'инструменты для команды': 42,
    'клиентский сервис': 43,
    'контент': 44, 'работа с документами': 45,
}


# ---------------------------------------------------------------------------
# WordPress client
# ---------------------------------------------------------------------------

class WordPressClient:
    def __init__(self, url: str, user: str, password: str):
        self.base_url = url.rstrip('/')
        self.api_url = f"{self.base_url}/wp-json/wp/v2"
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.headers.update({'Content-Type': 'application/json'})

    def upload_image(self, file_path: str, filename: str = None) -> dict:
        """Upload image to WP media library. Returns {id, url}."""
        import time
        path = Path(file_path)
        # Add timestamp to avoid duplicate filename rejection
        stem = path.stem if not filename else Path(filename).stem
        suffix = path.suffix if not filename else Path(filename).suffix or path.suffix
        fname = f"{stem}_{int(time.time())}{suffix}"
        content_type = 'image/png' if path.suffix == '.png' else 'image/jpeg'

        with open(path, 'rb') as f:
            data = f.read()

        resp = self.session.post(
            f"{self.api_url}/media",
            headers={
                'Content-Disposition': f'attachment; filename={fname}',
                'Content-Type': content_type,
            },
            data=data,
        )
        if resp.status_code == 201:
            media = resp.json()
            return {'id': media['id'], 'url': media['source_url']}
        else:
            print(f"  Warning: image upload failed for {fname}: {resp.status_code}")
            return None

    def create_case(self, payload: dict) -> dict:
        resp = self.session.post(f"{self.api_url}/cases", json=payload)
        if resp.status_code == 201:
            result = resp.json()
            return {
                'success': True,
                'id': result['id'],
                'link': result['link'],
                'edit_link': f"{self.base_url}/wp-admin/post.php?post={result['id']}&action=edit",
                'status': result['status'],
            }
        return {
            'success': False,
            'status_code': resp.status_code,
            'error': resp.text[:500],
        }

    def update_rankmath_meta(self, post_id: int, meta: dict) -> bool:
        """Update Rank Math SEO meta via its dedicated REST API."""
        resp = self.session.post(
            f"{self.base_url}/wp-json/rankmath/v1/updateMeta",
            json={
                'objectID': post_id,
                'objectType': 'post',
                'meta': meta,
            },
        )
        if resp.status_code == 200:
            print(f"  Rank Math SEO meta updated for post {post_id}")
            return True
        print(f"  Warning: Rank Math meta update failed: {resp.status_code} {resp.text[:200]}")
        return False

    def test_connection(self) -> bool:
        resp = self.session.get(f"{self.api_url}/cases?per_page=1")
        return resp.status_code == 200


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

def parse_ready_md(filepath: str) -> dict:
    """Parse _READY.md into structured data."""
    text = Path(filepath).read_text(encoding='utf-8')
    lines = text.split('\n')

    result = {
        'category': '',
        'h1_title': '',
        'company_description': '',
        'design_block': {},
        'tldr': '',
        'sections': [],  # list of {type, title, content, ...}
    }

    i = 0
    while i < len(lines):
        line = lines[i]

        # Category (first H1)
        if line.startswith('# ') and not result['category']:
            result['category'] = line[2:].strip()
            i += 1
            continue

        # Main title (first H2)
        if line.startswith('## ') and not result['h1_title']:
            result['h1_title'] = line[3:].strip()
            i += 1
            desc_lines = []
            while i < len(lines) and not lines[i].startswith('## '):
                desc_lines.append(lines[i])
                i += 1
            result['company_description'] = '\n'.join(desc_lines).strip()
            continue

        # Design block
        if line.startswith('## Для дизайна'):
            i += 1
            design_lines = []
            while i < len(lines) and not lines[i].startswith('## ') and not lines[i].startswith('### '):
                design_lines.append(lines[i])
                i += 1
            result['design_block'] = _parse_design_block('\n'.join(design_lines))
            continue

        # TLDR
        if line.startswith('### TLDR') or line.startswith('## TLDR'):
            i += 1
            tldr_lines = []
            while i < len(lines) and not lines[i].startswith('## '):
                tldr_lines.append(lines[i])
                i += 1
            result['tldr'] = '\n'.join(tldr_lines).strip()
            continue

        # Content sections (H2)
        if line.startswith('## '):
            title = line[3:].strip()
            i += 1
            content_lines = []
            while i < len(lines) and not lines[i].startswith('## '):
                content_lines.append(lines[i])
                i += 1
            content = '\n'.join(content_lines).strip()
            result['sections'].append({
                'title': title,
                'content': content,
            })
            continue

        i += 1

    # Extract SEO from design block
    db = result['design_block']
    result['seo_title'] = db.get('title', result['h1_title'])
    result['seo_description'] = db.get('description', '')
    result['seo_keywords'] = db.get('keywords', '')

    return result


def _parse_design_block(text: str) -> dict:
    block = {}
    current_key = None
    current_value = []
    key_patterns = {
        'card_title': r'^заголовок\s*/\s*подзаголовок',
        'filter': r'^фильтр',
        'author': r'^автор',
        'photo': r'^фото',
        'url': r'^ссылка',
        'pictures': r'^пикчи',
        'title': r'^тайтл',
        'description': r'^дескрипшен',
        'keywords': r'^seo.?ключев',
    }
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped:
            if current_key:
                current_value.append('')
            continue
        matched = False
        for key, pattern in key_patterns.items():
            if re.match(pattern, stripped, re.IGNORECASE):
                if current_key:
                    block[current_key] = '\n'.join(current_value).strip()
                current_key = key
                parts = stripped.split(':', 1)
                current_value = [parts[1].strip()] if len(parts) > 1 and parts[1].strip() else []
                matched = True
                break
        if not matched and current_key:
            current_value.append(stripped)
    if current_key:
        block[current_key] = '\n'.join(current_value).strip()
    return block


# ---------------------------------------------------------------------------
# Markdown → HTML helpers
# ---------------------------------------------------------------------------

def md_to_html(text: str) -> str:
    """Convert markdown text to HTML with proper paragraph spacing."""
    html_parts = []
    in_list = False
    in_blockquote = False

    for line in text.split('\n'):
        stripped = line.strip()

        if not stripped:
            if in_list:
                html_parts.append('</ul>')
                in_list = False
            if in_blockquote:
                html_parts.append('</blockquote>')
                in_blockquote = False
            # Add spacing between paragraphs
            html_parts.append('<p>&nbsp;</p>')
            continue

        # Skip illustration markers — they're handled separately
        if '[ИЛЛЮСТРАЦИЯ:' in stripped:
            continue

        # Blockquote
        if stripped.startswith('> '):
            qt = stripped[2:]
            if not in_blockquote:
                html_parts.append('<blockquote>')
                in_blockquote = True
            if qt.strip():
                html_parts.append(f'<p>{_inline(qt)}</p>')
            continue

        # List
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                html_parts.append('<ul>')
                in_list = True
            html_parts.append(f'<li>{_inline(stripped[2:])}</li>')
            continue

        # H3
        if stripped.startswith('### '):
            html_parts.append(f'<h3>{_inline(stripped[4:])}</h3>')
            continue

        # Paragraph
        html_parts.append(f'<p>{_inline(stripped)}</p>')

    if in_list:
        html_parts.append('</ul>')
    if in_blockquote:
        html_parts.append('</blockquote>')

    # Clean up multiple empty paragraphs
    result = '\n'.join(html_parts)
    result = re.sub(r'(<p>&nbsp;</p>\n?){2,}', '<p>&nbsp;</p>\n', result)
    # Remove leading/trailing empty paragraphs
    result = re.sub(r'^<p>&nbsp;</p>\n?', '', result)
    result = re.sub(r'\n?<p>&nbsp;</p>$', '', result)
    return result


def _inline(text: str) -> str:
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    return text


# ---------------------------------------------------------------------------
# Smart section builder — maps content to rich ACF blocks
# ---------------------------------------------------------------------------

def extract_illustration_markers(content: str) -> list:
    """Extract [ИЛЛЮСТРАЦИЯ: ...] markers from content.
    Only include slides with schemas, screenshots, or diagrams — not text-only slides."""
    markers = []
    # Keywords that indicate a visual worth including (schemas, screenshots, diagrams)
    # Exclude: metric-only slides (numbers, percentages) — those are covered by text
    visual_keywords = ['схем', 'скриншот', 'интерфейс', 'архитектур', 'диаграмм',
                       'роадмап', 'pipeline', 'dashboard', 'CRM', 'демо',
                       'мультивкладоч', 'таблиц']
    # Keywords that indicate metric-only slides — skip these
    exclude_keywords = ['метрик качества', 'баллов', 'экономи', '40%', '98 ',
                        'визуал с ключевой цифр']
    for match in re.finditer(r'`?\[ИЛЛЮСТРАЦИЯ:\s*(.+?)\]`?', content):
        full = match.group(0)
        desc = match.group(1)
        # Filter: only schemas/screenshots, exclude metric-only slides
        desc_lower = desc.lower()
        is_visual = any(kw in desc_lower for kw in visual_keywords)
        is_excluded = any(kw in desc_lower for kw in exclude_keywords)
        if not is_visual or is_excluded:
            continue
        # Extract slide number
        slide_match = re.search(r'слайд[ы]?\s*#?(\d+(?:-\d+)?)', desc, re.IGNORECASE)
        slide_num = slide_match.group(1) if slide_match else None
        # Extract caption — short, one line, capitalize
        caption = desc.split('Источник:')[0].strip().rstrip('.')
        if caption:
            caption = caption[0].upper() + caption[1:]
            # Truncate to ~80 chars for one-line caption
            if len(caption) > 80:
                caption = caption[:77].rsplit(' ', 1)[0] + '...'
        markers.append({
            'full_match': full,
            'description': desc,
            'slide_num': slide_num,
            'caption': caption,
        })
    return markers


def build_sections(data: dict, slides_dir: str = None, wp_client=None) -> list:
    """Build ACF Flexible Content sections from parsed case data."""
    sections = []
    uploaded_slides = {}  # slide_num -> media_id

    # Helper: upload slide and get media ID
    def get_slide_media_id(slide_ref: str) -> int | None:
        if not slides_dir or not wp_client:
            return None
        # Handle ranges like "10-11" — take first
        num = slide_ref.split('-')[0] if slide_ref else None
        if not num:
            return None
        if num in uploaded_slides:
            return uploaded_slides[num]
        # Try both naming conventions: slide_01.png and slide-01.png
        slide_path = Path(slides_dir) / f"slide_{int(num):02d}.png"
        if not slide_path.exists():
            slide_path = Path(slides_dir) / f"slide-{int(num):02d}.png"
        if not slide_path.exists():
            print(f"  Slide not found: {slide_path}")
            return None
        print(f"  Uploading slide {num}...")
        result = wp_client.upload_image(str(slide_path), f"slide_{num}.png")
        if result:
            uploaded_slides[num] = result['id']
            return result['id']
        return None

    # --- Running line (tech stack) ---
    # Only show if there are 3+ specific tools/technologies (not generic terms)
    tech_keywords = _extract_tech_keywords(data)
    if len(tech_keywords) >= 3:
        sections.append({
            'acf_fc_layout': 'layer_running_line',
            'отступ_сверху_секции': '0',
            'отступ_снизу_секции': '0',
            'бегущая_строка': [{'текст': kw} for kw in tech_keywords],
        })

    # NOTE: TLDR block is NOT generated — not supported in WP admin yet.

    # --- Process each section ---
    for section in data.get('sections', []):
        title = section['title']
        content = section['content']

        # Extract illustration markers
        markers = extract_illustration_markers(content)
        # Remove markers from content for text processing
        clean_content = content
        for m in markers:
            clean_content = clean_content.replace(m['full_match'], '')

        # Detect special section types
        is_metrics = bool(re.search(r'ключевые результаты', title, re.IGNORECASE))
        has_metrics_subsection = bool(re.search(r'### Ключевые результаты', content))
        is_steps = bool(re.search(r'шаг|шагов|этап', title, re.IGNORECASE))

        # --- Section with metrics subsection → split into metrics + text ---
        if is_metrics or has_metrics_subsection:
            if has_metrics_subsection:
                parts = re.split(r'### Ключевые результаты.*\n', content)
                text_part = parts[0].strip() if parts else ''
                metrics_text = parts[1] if len(parts) > 1 else content
            else:
                text_part = ''
                metrics_text = content

            metrics = _extract_metrics_from_content(metrics_text)
            if metrics:
                # METRICS FIRST (layer_columns with green headers)
                for chunk_start in range(0, len(metrics), 3):
                    chunk = metrics[chunk_start:chunk_start + 3]
                    sections.append({
                        'acf_fc_layout': 'layer_columns',
                        'отступ_сверху_секции': '32' if chunk_start > 0 else '64',
                        'отступ_снизу_секции': '32',
                        'заголовок_блока_с_колонками': 'Ключевые результаты проекта' if chunk_start == 0 else '',
                        'подзаголовок_блока_с_колонками': '',
                        'колонки': [
                            {
                                'acf_fc_layout': '',
                                'заголовок_иконка': 'Заголовок',
                                'заголовок': m['number'],
                                'цвет_заголовка': '#24DD63',
                                'размер_заголовка': 'normal',
                                'текст': m['description'],
                            }
                            for m in chunk
                        ],
                    })

            # THEN conclusions text
            if text_part:
                _build_text_section(sections, title, text_part)

            continue

        # --- Steps section → split into text + waypoint ---
        if is_steps and '### ' in content:
            # Split: text before first ###, then steps
            parts = re.split(r'(?=^### )', content, flags=re.MULTILINE)
            intro = parts[0].strip() if parts else ''
            steps = []
            step_contents = []

            for part in parts[1:]:
                step_lines = part.strip().split('\n')
                step_title = step_lines[0].replace('### ', '').strip()
                step_body = '\n'.join(step_lines[1:]).strip()
                # Remove illustration markers from step body
                for m in markers:
                    step_body = step_body.replace(m['full_match'], '')
                steps.append(step_title)
                # Truncate step body to first 1 sentence for waypoint block
                sentences = re.split(r'(?<=[.!?])\s+', step_body.strip())
                short_body = sentences[0] if sentences and sentences[0] else ''
                step_contents.append(short_body)

            # Section heading only (no intro image — avoid duplicating diagram + waypoint)
            clean_intro = intro
            for m in markers:
                clean_intro = clean_intro.replace(m['full_match'], '')
            if clean_intro.strip():
                sections.append(_text_block(title, clean_intro))
            else:
                sections.append(_text_block(title, ''))

            # Waypoint block — number in heading, clean title without "Шаг N:"
            if steps:
                clean_steps = []
                for s_title, s_body in zip(steps, step_contents):
                    # Remove "Шаг первый:", "Шаг 1:" etc from title
                    clean_title = re.sub(
                        r'^шаг\s+(?:первый|второй|третий|четвертый|пятый|\d+)\s*:\s*',
                        '', s_title, flags=re.IGNORECASE
                    ).strip()
                    if not clean_title:
                        clean_title = s_title
                    # Capitalize first letter
                    clean_title = clean_title[0].upper() + clean_title[1:] if clean_title else clean_title
                    text = f'{clean_title}. {s_body}' if s_body else clean_title
                    clean_steps.append(text)

                sections.append({
                    'acf_fc_layout': 'layer_waypoint',
                    'отступ_сверху_секции': '32',
                    'отступ_снизу_секции': '32',
                    'шаг': [
                        {
                            'acf_fc_layout': '',
                            'заголовок': str(i + 1),
                            'текст': clean_steps[i],
                        }
                        for i in range(len(clean_steps))
                    ],
                })

            continue

        # --- Detect problem/solution pairs → custom_text with 2 columns ---
        has_two_problems = bool(re.search(
            r'\*\*Проблема 1.*?\*\*.*?\*\*Проблема 2', clean_content, re.DOTALL
        ))
        if has_two_problems:
            _build_two_column_problems(sections, title, clean_content, markers, get_slide_media_id)
            continue

        # --- Regular section ---
        # Split content by illustration markers to interleave text + images
        if markers:
            _build_interleaved_section(
                sections, title, content, markers, get_slide_media_id
            )
        else:
            # Check for blockquotes → note_text or layer_feedback
            _build_text_section(sections, title, clean_content)

    # --- CTA at the end — always consultation, contextual text ---
    cta_text = _generate_cta_text(data)
    sections.append({
        'acf_fc_layout': 'layer_cta_form',
        'отступ_сверху_секции': '64',
        'отступ_снизу_секции': '0',
        'текст': cta_text,
        'button': {
            'btn_text': 'Получить консультацию',
            'btn_type': 'popup',
            'btn_form': 'consultation',
        },
    })

    return sections


def _text_block(title: str, content: str, top='64', bottom='32') -> dict:
    return {
        'acf_fc_layout': 'custom_text',
        'отступ_сверху_секции': top,
        'отступ_снизу_секции': bottom,
        'заголовок': title,
        'текст': md_to_html(content) if content else '',
        'растянуть_колонку_на_всю_ширину_контента': False,
        'сделать_в_2_колонки': False,
        '2-я_колонка_текста': '',
    }


def _media_block(media_id: int, caption: str = '', top='32', bottom='32') -> dict:
    return {
        'acf_fc_layout': 'layer_media',
        'отступ_сверху_секции': top,
        'отступ_снизу_секции': bottom,
        'тип_блока': 'Одиночное изображение',
        'изображение': media_id,
        'подпись_к_изображению': caption,
    }


def _note_block(text: str) -> dict:
    return {
        'acf_fc_layout': 'note_text',
        'отступ_сверху_секции': '32',
        'отступ_снизу_секции': '32',
        'текст': f'<p>{_inline(text)}</p>',
    }


def _build_interleaved_section(sections, title, content, markers, get_slide_fn):
    """Build section with text and images interleaved.
    Consecutive images are merged into a gallery (slider)."""
    remaining = content
    first_block = True
    pending_images = []  # collect consecutive images for gallery

    def flush_images():
        """Flush pending images — single or gallery."""
        if not pending_images:
            return
        if len(pending_images) == 1:
            sections.append(_media_block(pending_images[0][0], pending_images[0][1]))
        else:
            # Gallery / slider
            sections.append({
                'acf_fc_layout': 'layer_media',
                'отступ_сверху_секции': '32',
                'отступ_снизу_секции': '32',
                'тип_блока': 'Галерея с подписями',
                'блок_изображений': [
                    {
                        'acf_fc_layout': '',
                        'изображение': img_id,
                        'подпись_к_изображению': cap,
                    }
                    for img_id, cap in pending_images
                ],
                'изображение': pending_images[0][0],
                'подпись_к_изображению': pending_images[0][1],
            })
        pending_images.clear()

    for m in markers:
        idx = remaining.find(m['full_match'])
        if idx == -1:
            continue

        before = remaining[:idx].strip()
        if before:
            flush_images()
            _build_text_section(
                sections,
                title if first_block else '',
                before,
            )
            first_block = False

        media_id = get_slide_fn(m['slide_num'])
        if media_id:
            pending_images.append((media_id, m['caption']))

        remaining = remaining[idx + len(m['full_match']):].strip()

    # Flush any remaining images
    flush_images()

    # Remaining text after last marker
    if remaining:
        for m in markers:
            remaining = remaining.replace(m['full_match'], '')
        remaining = remaining.strip()
        if remaining:
            _build_text_section(
                sections,
                title if first_block else '',
                remaining,
            )


def _build_text_section(sections, title, content):
    """Build text section, extracting blockquotes as note_text blocks."""
    lines = content.split('\n')
    current_text = []
    first_text = True

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Blockquote → note_text
        if stripped.startswith('> '):
            # Flush accumulated text
            text = '\n'.join(current_text).strip()
            if text:
                sections.append(_text_block(
                    title if first_text else '',
                    text,
                ))
                first_text = False
                current_text = []

            # Collect blockquote
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith('> '):
                qt = lines[i].strip()[2:].strip()
                if qt and not re.match(r'^\*\*ПЛАШКА', qt):
                    # Remove bold markers for note_text
                    qt = re.sub(r'\*\*(.+?)\*\*', r'\1', qt)
                    quote_lines.append(qt)
                i += 1

            if quote_lines:
                sections.append(_note_block(' '.join(quote_lines)))
            continue

        current_text.append(line)
        i += 1

    # Flush remaining text
    text = '\n'.join(current_text).strip()
    if text:
        sections.append(_text_block(
            title if first_text else '',
            text,
        ))


def _extract_tech_keywords(data: dict) -> list:
    """Extract technology keywords for the running line.
    Only English/technical terms — no Russian words."""
    full_text = data.get('h1_title', '') + ' ' + data.get('tldr', '')
    for s in data.get('sections', []):
        full_text += ' ' + s.get('content', '')

    # Only SPECIFIC tools/frameworks — not generic terms like LLM, AI, NLP
    known_terms = [
        'T-Pro 32B', 'SFT', 'Kubernetes', 'computer-use',
        'ChatGPT', 'CRM', 'RAGAS',
        'Deepgram', 'Whisper', 'Claude', 'GPT-4',
        'TWork', 'A/B test', 'JSON', 'REST API',
        'red teaming', 'OpenAI', 'Anthropic',
    ]
    found = []
    for term in known_terms:
        if term.lower() in full_text.lower() and term not in found:
            found.append(term)
    return found[:10]


def _build_two_column_problems(sections, title, content, markers, get_slide_fn):
    """Split content with **Проблема 1** / **Проблема 2** into custom_text with 2 columns.
    Only the problem descriptions go into columns — everything after is separate blocks."""
    # Split by **Проблема N** markers
    parts = re.split(r'\*\*Проблема\s+\d+[^*]*\*\*\s*', content)
    headers = re.findall(r'\*\*(Проблема\s+\d+[^*]*)\*\*', content)

    # Intro text before first problem
    intro = parts[0].strip() if parts else ''
    for m in markers:
        intro = intro.replace(m['full_match'], '')
    if intro:
        sections.append(_text_block(title, intro))

    # Build two-column custom_text: only first paragraph of each problem
    if len(headers) >= 2 and len(parts) >= 3:
        col1_full = parts[1].strip()
        col2_full = parts[2].strip()

        # Take only first paragraph for each column
        col1_para = col1_full.split('\n\n')[0].strip()
        col2_para = col2_full.split('\n\n')[0].strip()

        # Remove illustration markers from column text
        for m in markers:
            col1_para = col1_para.replace(m['full_match'], '')
            col2_para = col2_para.replace(m['full_match'], '')

        col1_html = f'<h3>{_inline(headers[0].strip())}</h3>\n{md_to_html(col1_para)}'
        col2_html = f'<h3>{_inline(headers[1].strip())}</h3>\n{md_to_html(col2_para)}'

        sections.append({
            'acf_fc_layout': 'custom_text',
            'отступ_сверху_секции': '32',
            'отступ_снизу_секции': '32',
            'заголовок': '',
            'текст': col1_html,
            'растянуть_колонку_на_всю_ширину_контента': False,
            'сделать_в_2_колонки': True,
            '2-я_колонка_текста': col2_html,
        })

        # Remaining text after first paragraphs of both problems
        col1_rest = '\n\n'.join(col1_full.split('\n\n')[1:]).strip()
        col2_rest = '\n\n'.join(col2_full.split('\n\n')[1:]).strip()
        after_text = (col1_rest + '\n\n' + col2_rest).strip()

        # Remove illustration markers, collect them separately
        for m in markers:
            after_text = after_text.replace(m['full_match'], '')

        if after_text.strip():
            sections.append(_text_block('', after_text))

    # Illustrations from this section
    for m in markers:
        media_id = get_slide_fn(m['slide_num'])
        if media_id:
            sections.append(_media_block(media_id, m['caption']))


def _generate_cta_text(data: dict) -> str:
    """Generate contextual CTA text based on case topic."""
    title = data.get('h1_title', '').lower()
    full_text = data.get('tldr', '').lower()

    if 'агент' in title or 'агент' in full_text:
        return 'У вас похожая задача в операционке? Расскажите — обсудим, как AI-агенты могут помочь'
    elif 'автоматиз' in title or 'автоматиз' in full_text:
        return 'Хотите автоматизировать рутинные процессы с помощью AI? Расскажите о вашей задаче'
    elif 'чат-бот' in title or 'чат-бот' in full_text:
        return 'Думаете о внедрении AI в клиентский сервис? Расскажите о вашем кейсе'
    else:
        return 'У вас есть похожий опыт внедрения AI? Поделитесь — мы напишем кейс вместе'


def _extract_metrics_from_content(content: str) -> list:
    """Extract **number**\\ndescription pairs from metrics block."""
    metrics = []
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = re.match(r'^\*\*(.+?)\*\*\s*$', line)
        if match:
            number = match.group(1)
            desc = ''
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                if next_line:
                    desc = next_line
                    break
                i += 1
            metrics.append({'number': number, 'description': desc})
        i += 1
    return metrics


# ---------------------------------------------------------------------------
# Build full payload
# ---------------------------------------------------------------------------

def build_payload(data: dict, sections: list, status: str = 'draft', author_photo_id: int = None) -> dict:
    # WordPress title (breadcrumb) = card title, NOT h1
    db = data.get('design_block', {})
    card_title = db.get('card_title', data.get('h1_title', ''))
    # Card title may have two lines — take first line only for WP title
    card_title_first = card_title.split('\n')[0].strip()

    payload = {
        'title': card_title_first,
        'status': status,
        'slug': _generate_slug(card_title_first, data.get('seo_keywords', '').split(',')[0].strip() if data.get('seo_keywords') else ''),
    }

    # Taxonomy
    cat = data.get('category', '').lower()
    otrasli_id = OTRASLI_MAP.get(cat)
    if otrasli_id:
        payload['otrasli'] = [otrasli_id]

    # Detect zadachi from content
    zadachi_ids = []
    full_text = data.get('h1_title', '') + ' ' + data.get('company_description', '')
    if 'клиентск' in full_text.lower() or 'обслуживан' in full_text.lower():
        zadachi_ids.append(ZADACHI_MAP['клиентский сервис'])
    if zadachi_ids:
        payload['zadachi'] = zadachi_ids

    # ACF fields
    acf = {
        'заголовок_h1': data.get('h1_title', ''),
        # Company description: plain text, no bold markers
        'описание_под_заголовком': re.sub(r'\*\*(.+?)\*\*', r'\1', data.get('company_description', '')),
        # Card title = breadcrumb = WP title
        'заголовок_для_карточки_кейса': card_title_first,
        # Card description: starts with noun/participle, not verb
        'описание_для_карточки_кейса': db.get('description', ''),
        'sections': sections,
    }

    # Author with photo
    author_name = db.get('author', '')
    if author_name and author_photo_id:
        name_parts = author_name.split(',', 1)
        acf['авторы_кейса'] = [
            {
                'acf_fc_layout': '',
                'фото_автора': author_photo_id,
                'имя_автора': name_parts[0].strip(),
                'подпись_автора': name_parts[1].strip() if len(name_parts) > 1 else '',
            }
        ]

    payload['acf'] = acf
    return payload


def build_rankmath_meta(data: dict) -> dict:
    """Build Rank Math SEO meta dict from parsed case data."""
    seo_title = data.get('seo_title', '')
    seo_desc = data.get('seo_description', '')
    seo_keywords = data.get('seo_keywords', '')

    focus_keyword = ''
    if seo_keywords:
        focus_keyword = seo_keywords.split(',')[0].strip()

    meta = {}
    if seo_title:
        meta['rank_math_title'] = seo_title
    if seo_desc:
        meta['rank_math_description'] = seo_desc
    if focus_keyword:
        meta['rank_math_focus_keyword'] = focus_keyword
    if seo_keywords:
        # Rank Math stores additional keywords in this field
        meta['rank_math_focus_keyword'] = focus_keyword
    return meta


def _generate_slug(title: str, focus_keyword: str = '') -> str:
    """Generate SEO-friendly slug from focus keyword or title.

    Prefers focus keyword (e.g. "Т-Банк AI-агенты" → "tbank-ai-agenty").
    Falls back to extracting company name + first meaningful word from title.
    """
    if focus_keyword:
        return _transliterate(focus_keyword)
    # Extract company name + first meaningful words from "Как [Company] [action]..."
    match = re.search(r'(?:Как\s+)?(\S+)\s+(\S+)', title)
    if match:
        words = f"{match.group(1)} {match.group(2)}".lower()
        return _transliterate(words)
    match = re.search(r'(?:Как\s+)?(\S+)', title)
    if match:
        return _transliterate(match.group(1).lower())
    return ''


def _transliterate(text: str) -> str:
    mapping = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd',
        'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i',
        'й': 'j', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
        'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
        'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch',
        'ш': 'sh', 'щ': 'shch', 'ъ': '', 'ы': 'y', 'ь': '',
        'э': 'e', 'ю': 'yu', 'я': 'ya', ' ': '-',
    }
    result = ''
    for char in text.lower():
        if char in mapping:
            result += mapping[char]
        elif char.isalnum() or char == '-':
            result += char
    return re.sub(r'-+', '-', result).strip('-')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Smart WordPress publisher v2")
    parser.add_argument("case_file", help="Path to _READY.md file")
    parser.add_argument("--slides-dir", help="Directory with slide PNGs")
    parser.add_argument("--author-photo", help="Path to author photo (JPG/PNG)")
    parser.add_argument("--publish", action="store_true", help="Publish (default: draft)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no publish")
    parser.add_argument("--test", action="store_true", help="Test API connection")
    args = parser.parse_args()

    config = load_config()

    if args.test:
        client = WordPressClient(config['url'], config['user'], config['password'])
        ok = client.test_connection()
        print(f"Connection: {'OK' if ok else 'FAILED'}")
        return

    if not os.path.exists(args.case_file):
        print(f"Error: File not found: {args.case_file}")
        sys.exit(1)

    # Parse
    print(f"Parsing: {args.case_file}")
    data = parse_ready_md(args.case_file)

    print(f"  Title: {data['h1_title']}")
    print(f"  Category: {data['category']}")
    print(f"  TLDR: {data['tldr'][:80]}...")
    print(f"  Sections: {len(data['sections'])}")
    for s in data['sections']:
        markers = extract_illustration_markers(s['content'])
        print(f"    - {s['title']} ({len(markers)} illustrations)")

    # Build sections
    wp_client = None
    if not args.dry_run:
        if not config['user'] or not config['password']:
            print("\nError: WP_USER and WP_APP_PASSWORD required.")
            sys.exit(1)
        wp_client = WordPressClient(config['url'], config['user'], config['password'])

    print(f"\nBuilding sections...")
    sections = build_sections(
        data,
        slides_dir=args.slides_dir,
        wp_client=wp_client if not args.dry_run else None,
    )

    print(f"  Built {len(sections)} ACF sections:")
    for i, s in enumerate(sections):
        layout = s.get('acf_fc_layout', '?')
        title = s.get('заголовок', s.get('заголовок_блока_с_колонками', ''))
        print(f"    {i+1}. [{layout}] {title[:60]}")

    # Upload author photo if provided
    author_photo_id = None
    if args.author_photo and wp_client and not args.dry_run:
        if os.path.exists(args.author_photo):
            print(f"\nUploading author photo: {args.author_photo}")
            result = wp_client.upload_image(args.author_photo)
            if result:
                author_photo_id = result['id']
                print(f"  Author photo ID: {author_photo_id}")
        else:
            print(f"  Warning: author photo not found: {args.author_photo}")

    # Dry run
    if args.dry_run:
        payload = build_payload(data, sections)
        json_path = '/tmp/wp_payload_v2.json'
        Path(json_path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        print(f"\nDry run complete. Payload saved to: {json_path}")
        return

    # Publish
    status = 'publish' if args.publish else 'draft'
    print(f"\nPublishing as {status}...")

    payload = build_payload(data, sections, status, author_photo_id=author_photo_id)
    result = wp_client.create_case(payload)

    if result['success']:
        # Update Rank Math SEO meta (uses separate API endpoint)
        seo_meta = build_rankmath_meta(data)
        if seo_meta:
            wp_client.update_rankmath_meta(result['id'], seo_meta)

        print(f"\n{'='*60}")
        print(f"  Case published!")
        print(f"  ID: {result['id']}")
        print(f"  Status: {result['status']}")
        print(f"  View: {result['link']}")
        print(f"  Edit: {result['edit_link']}")
        print(f"  SEO: {'set' if seo_meta else 'no SEO data in case file'}")
        print(f"{'='*60}")
    else:
        print(f"\nError: {result.get('status_code')} - {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
