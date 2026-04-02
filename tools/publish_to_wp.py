#!/usr/bin/env python3
"""
Publish a ready case (_READY.md) to WordPress as a draft.

Parses the caseorium markdown format and creates a draft post
with ACF fields filled in via WP REST API.

Usage:
    python3 publish_to_wp.py <case_file> [--publish]

Examples:
    python3 publish_to_wp.py cases/vkusvill_READY.md
    python3 publish_to_wp.py cases/sber_READY.md --publish

Environment:
    WP_URL          - WordPress site URL (default: testforagents.just-ai.ru)
    WP_USER         - WordPress username
    WP_APP_PASSWORD - WordPress Application Password
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests


# --- Configuration ---

def load_config() -> dict:
    """Load WP config from env vars or .env file."""
    config = {
        'url': os.environ.get('WP_URL', ''),
        'user': os.environ.get('WP_USER', ''),
        'password': os.environ.get('WP_APP_PASSWORD', ''),
    }

    # Try .env file
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, val = line.split('=', 1)
                val = val.strip().strip('"')
                if key == 'WP_URL' and not config['url']:
                    config['url'] = val
                elif key == 'WP_USER' and not config['user']:
                    config['user'] = val
                elif key == 'WP_APP_PASSWORD' and not config['password']:
                    config['password'] = val

    if not config['url']:
        config['url'] = 'https://testforagents.just-ai.ru'

    return config


# --- Taxonomy mapping ---

OTRASLI_MAP = {
    'hr': 32,
    'ит': 33, 'it': 33,
    'контент и медиа': 34,
    'маркетинг': 50, 'marketing': 50,
    'медицина': 35,
    'промышленность': 36,
    'ритейл': 37, 'retail': 37,
    'услуги': 38,
    'финансы': 39, 'finance': 39,
}

ZADACHI_MAP = {
    'hr': 40,
    'аналитика данных': 41,
    'инструменты для команды': 42,
    'клиентский сервис': 43,
    'контент': 44,
    'работа с документами': 45,
}


# --- Markdown Parser ---

def parse_ready_md(filepath: str) -> dict:
    """Parse _READY.md into structured data for WordPress."""
    text = Path(filepath).read_text(encoding='utf-8')
    lines = text.split('\n')

    result = {
        'category': '',         # First H1 (industry)
        'h1_title': '',         # Main case title (first H2)
        'company_description': '',
        'design_block': {},     # Parsed design block
        'tldr': '',
        'sections': [],         # Main content sections
        'seo_keywords': '',
        'seo_title': '',
        'seo_description': '',
    }

    current_section = None
    current_content = []
    in_design_block = False
    in_tldr = False

    i = 0
    while i < len(lines):
        line = lines[i]

        # First H1 = category
        if line.startswith('# ') and not result['category']:
            result['category'] = line[2:].strip()
            i += 1
            continue

        # First H2 = main title
        if line.startswith('## ') and not result['h1_title']:
            result['h1_title'] = line[3:].strip()
            i += 1
            # Collect company description (until next ## or design block)
            desc_lines = []
            while i < len(lines) and not lines[i].startswith('## '):
                desc_lines.append(lines[i])
                i += 1
            result['company_description'] = '\n'.join(desc_lines).strip()
            continue

        # Design block
        if line.startswith('## Для дизайна'):
            in_design_block = True
            i += 1
            design_lines = []
            while i < len(lines) and not lines[i].startswith('## ') and not lines[i].startswith('### '):
                design_lines.append(lines[i])
                i += 1
            result['design_block'] = _parse_design_block('\n'.join(design_lines))
            in_design_block = False
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
            # Save previous section
            if current_section:
                result['sections'].append({
                    'title': current_section,
                    'content': '\n'.join(current_content).strip()
                })

            current_section = line[3:].strip()
            current_content = []
            i += 1
            continue

        if current_section:
            current_content.append(line)

        i += 1

    # Save last section
    if current_section:
        result['sections'].append({
            'title': current_section,
            'content': '\n'.join(current_content).strip()
        })

    # Extract SEO from design block
    db = result['design_block']
    result['seo_title'] = db.get('title', result['h1_title'])
    result['seo_description'] = db.get('description', '')
    result['seo_keywords'] = db.get('keywords', '')

    return result


def _parse_design_block(text: str) -> dict:
    """Parse the design block section into key-value pairs."""
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
        line_stripped = line.strip()
        if not line_stripped:
            if current_key:
                current_value.append('')
            continue

        matched = False
        for key, pattern in key_patterns.items():
            if re.match(pattern, line_stripped, re.IGNORECASE):
                if current_key:
                    block[current_key] = '\n'.join(current_value).strip()
                current_key = key
                # Value might be on same line after ':'
                parts = line_stripped.split(':', 1)
                current_value = [parts[1].strip()] if len(parts) > 1 and parts[1].strip() else []
                matched = True
                break

        if not matched and current_key:
            current_value.append(line_stripped)

    if current_key:
        block[current_key] = '\n'.join(current_value).strip()

    return block


# --- Markdown to HTML ---

def md_section_to_html(content: str) -> str:
    """Convert markdown section content to basic HTML for WP Classic Editor."""
    html_lines = []
    in_blockquote = False
    in_list = False

    for line in content.split('\n'):
        stripped = line.strip()

        # Empty line
        if not stripped:
            if in_blockquote:
                html_lines.append('</blockquote>')
                in_blockquote = False
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            continue

        # Blockquote
        if stripped.startswith('> '):
            quote_text = stripped[2:]
            if not in_blockquote:
                html_lines.append('<blockquote>')
                in_blockquote = True
            # Skip empty blockquote marker
            if quote_text.strip():
                html_lines.append(f'<p>{_inline_md(quote_text)}</p>')
            continue

        # List item
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                html_lines.append('<ul>')
                in_list = True
            html_lines.append(f'<li>{_inline_md(stripped[2:])}</li>')
            continue

        # Illustration marker — convert to comment/placeholder
        if stripped.startswith('`[ИЛЛЮСТРАЦИЯ:') or stripped.startswith('[ИЛЛЮСТРАЦИЯ:'):
            clean = stripped.strip('`[]')
            html_lines.append(f'<p style="color: #e67e22; font-style: italic;">[{clean}]</p>')
            continue

        # H3 subheading
        if stripped.startswith('### '):
            html_lines.append(f'<h3>{_inline_md(stripped[4:])}</h3>')
            continue

        # Regular paragraph
        html_lines.append(f'<p>{_inline_md(stripped)}</p>')

    if in_blockquote:
        html_lines.append('</blockquote>')
    if in_list:
        html_lines.append('</ul>')

    return '\n'.join(html_lines)


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, italic, links) to HTML."""
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', text)
    # Links
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    # Guillemets are already in place
    return text


# --- WordPress API ---

class WordPressClient:
    def __init__(self, url: str, user: str, password: str):
        self.base_url = url.rstrip('/')
        self.api_url = f"{self.base_url}/wp-json/wp/v2"
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.headers.update({'Content-Type': 'application/json'})

    def build_payload(self, data: dict, status: str = 'draft') -> dict:
        """Build the full WP REST API payload from parsed case data."""
        payload = {
            'title': data['h1_title'],
            'status': status,
            'slug': self._generate_slug(data['h1_title']),
        }

        # Taxonomy
        if data.get('category'):
            otrasli_id = OTRASLI_MAP.get(data['category'].lower())
            if otrasli_id:
                payload['otrasli'] = [otrasli_id]

        # ACF fields — real field names from WP API
        acf = {}
        acf['заголовок_h1'] = data.get('h1_title', '')
        acf['описание_под_заголовком'] = data.get('company_description', '')

        db = data.get('design_block', {})
        acf['заголовок_для_карточки_кейса'] = db.get('card_title', data.get('h1_title', ''))
        acf['описание_для_карточки_кейса'] = db.get('description', '')

        # Authors (repeater with acf_fc_layout) — parse from design block
        # Note: авторы_кейса requires acf_fc_layout field
        # Skipping for now — authors are added manually in WP admin

        # Build sections (Flexible Content)
        acf['sections'] = self._build_sections(data)

        payload['acf'] = acf
        return payload

    def _build_sections(self, data: dict) -> list:
        """Convert parsed markdown sections to ACF Flexible Content array."""
        sections = []

        # TLDR as first text section
        if data.get('tldr'):
            sections.append({
                'acf_fc_layout': 'custom_text',
                'отступ_сверху_секции': '64',
                'отступ_снизу_секции': '64',
                'заголовок': '',
                'текст': md_section_to_html(data['tldr']),
            })

        for section in data.get('sections', []):
            content = section['content']
            title = section['title']

            # Detect blockquote/note blocks within content and split
            parts = self._split_section_by_type(title, content)
            sections.extend(parts)

        # CTA at the end
        sections.append({
            'acf_fc_layout': 'layer_cta_form',
            'отступ_сверху_секции': '64',
            'отступ_снизу_секции': '0',
            'текст': 'Хотите внедрить AI-агентов в вашей компании? Используйте Just AI Agent Platform — платформу для автоматизации процессов с помощью AI-агентов',
            'button': {
                'btn_text': 'Оставить заявку',
                'btn_type': 'popup',
                'btn_form': 'consultation',
            },
        })

        return sections

    def _split_section_by_type(self, title: str, content: str) -> list:
        """Split a markdown section into ACF section blocks by content type."""
        results = []
        current_text_lines = []

        # Check if this is a "Ключевые результаты" metrics block
        if re.search(r'ключевые результаты|итоги', title, re.IGNORECASE):
            metrics = self._extract_metrics(content)
            if metrics:
                # Add text part if any non-metric content exists
                text_before = self._extract_text_before_metrics(content)
                if text_before.strip():
                    results.append({
                        'acf_fc_layout': 'custom_text',
                        'отступ_сверху_секции': '64',
                        'отступ_снизу_секции': '32',
                        'заголовок': title,
                        'текст': md_section_to_html(text_before),
                    })
                # Metrics as styled HTML in custom_text
                # (layer_columns requires nested acf_fc_layout — use text block instead)
                metrics_html_parts = []
                for metric in metrics:
                    metrics_html_parts.append(
                        f'<p><strong style="color: #24DD63; font-size: 1.5em;">'
                        f'{metric["number"]}</strong><br>{metric["description"]}</p>'
                    )
                results.append({
                    'acf_fc_layout': 'custom_text',
                    'отступ_сверху_секции': '32',
                    'отступ_снизу_секции': '64',
                    'заголовок': 'Ключевые результаты проекта',
                    'текст': '\n'.join(metrics_html_parts),
                })
                return results

        # Process line by line to extract blockquotes as note_text
        lines = content.split('\n')
        in_blockquote = False
        quote_lines = []

        for line in lines:
            stripped = line.strip()

            if stripped.startswith('> '):
                if not in_blockquote:
                    # Flush text before blockquote
                    if current_text_lines:
                        text = '\n'.join(current_text_lines).strip()
                        if text:
                            results.append({
                                'acf_fc_layout': 'custom_text',
                                'отступ_сверху_секции': '64' if not results else '32',
                                'отступ_снизу_секции': '32',
                                'заголовок': title if not results else '',
                                'текст': md_section_to_html(text),
                            })
                            title = ''  # Title only for first block
                        current_text_lines = []
                    in_blockquote = True

                quote_text = stripped[2:].strip()
                if quote_text and not re.match(r'^\*\*ПЛАШКА', quote_text):
                    quote_lines.append(quote_text)
            else:
                if in_blockquote:
                    # Flush blockquote as note_text
                    if quote_lines:
                        clean_quote = ' '.join(quote_lines)
                        # Remove bold markers
                        clean_quote = re.sub(r'\*\*(.+?)\*\*', r'\1', clean_quote)
                        results.append({
                            'acf_fc_layout': 'note_text',
                            'текст': clean_quote,
                        })
                    quote_lines = []
                    in_blockquote = False

                current_text_lines.append(line)

        # Flush remaining blockquote
        if quote_lines:
            clean_quote = ' '.join(quote_lines)
            clean_quote = re.sub(r'\*\*(.+?)\*\*', r'\1', clean_quote)
            results.append({
                'acf_fc_layout': 'note_text',
                'текст': clean_quote,
            })

        # Flush remaining text
        remaining = '\n'.join(current_text_lines).strip()
        if remaining:
            results.append({
                'acf_fc_layout': 'custom_text',
                'отступ_сверху_секции': '64' if not results else '32',
                'отступ_снизу_секции': '64',
                'заголовок': title if not results else '',
                'текст': md_section_to_html(remaining),
            })

        # If nothing was produced, create a basic text section
        if not results:
            results.append({
                'acf_fc_layout': 'custom_text',
                'отступ_сверху_секции': '64',
                'отступ_снизу_секции': '64',
                'заголовок': title,
                'текст': md_section_to_html(content),
            })

        return results

    def _extract_metrics(self, content: str) -> list:
        """Extract metrics from 'Ключевые результаты' block."""
        metrics = []
        lines = content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # Pattern: **number/metric**
            match = re.match(r'^\*\*(.+?)\*\*\s*$', line)
            if match:
                number = match.group(1)
                # Next non-empty line is description
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

    def _extract_text_before_metrics(self, content: str) -> str:
        """Get text content before the first bold metric line."""
        lines = []
        for line in content.split('\n'):
            if re.match(r'^\*\*.+\*\*\s*$', line.strip()):
                break
            lines.append(line)
        return '\n'.join(lines)

    def create_case(self, data: dict, status: str = 'draft') -> dict:
        """Create a case post via WP REST API."""
        payload = self.build_payload(data, status)

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
        else:
            return {
                'success': False,
                'status_code': resp.status_code,
                'error': resp.text,
            }

    def _build_full_html(self, data: dict) -> str:
        """Build complete HTML from parsed case data."""
        parts = []

        # Company description
        if data.get('company_description'):
            parts.append(f"<p>{_inline_md(data['company_description'])}</p>")

        # TLDR
        if data.get('tldr'):
            parts.append(f"<h3>TLDR</h3>")
            parts.append(md_section_to_html(data['tldr']))

        # Main sections
        for section in data.get('sections', []):
            parts.append(f"<h2>{section['title']}</h2>")
            parts.append(md_section_to_html(section['content']))

        return '\n\n'.join(parts)

    def _generate_slug(self, title: str) -> str:
        """Generate URL slug from Russian title."""
        # Extract company name if title starts with "Как [Company]..."
        match = re.match(r'Как\s+(.+?)\s+', title)
        if match:
            company = match.group(1).lower()
            # Transliterate basic Cyrillic
            return self._transliterate(company)
        return ''

    @staticmethod
    def _transliterate(text: str) -> str:
        """Basic Russian transliteration for URL slugs."""
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

    def test_connection(self) -> dict:
        """Test API connection and authentication."""
        # Test without auth
        resp = self.session.get(f"{self.api_url}/cases?per_page=1")
        if resp.status_code == 200:
            return {'connected': True, 'authenticated': True, 'cases_count': len(resp.json())}
        elif resp.status_code == 401:
            return {'connected': True, 'authenticated': False, 'error': 'Invalid credentials'}
        else:
            return {'connected': False, 'error': resp.text}


# --- Main ---

def main():
    parser = argparse.ArgumentParser(description="Publish case to WordPress")
    parser.add_argument("case_file", help="Path to _READY.md file")
    parser.add_argument("--publish", action="store_true", help="Publish immediately (default: draft)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't publish")
    parser.add_argument("--test", action="store_true", help="Test API connection")
    args = parser.parse_args()

    config = load_config()

    if args.test:
        client = WordPressClient(config['url'], config['user'], config['password'])
        result = client.test_connection()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Parse case file
    if not os.path.exists(args.case_file):
        print(f"Error: File not found: {args.case_file}")
        sys.exit(1)

    print(f"Parsing: {args.case_file}")
    data = parse_ready_md(args.case_file)

    print(f"\n  Category: {data['category']}")
    print(f"  Title: {data['h1_title']}")
    print(f"  TLDR: {data['tldr'][:100]}...")
    print(f"  Sections: {len(data['sections'])}")
    for s in data['sections']:
        print(f"    - {s['title']}")
    print(f"  Design block fields: {list(data['design_block'].keys())}")

    if args.dry_run:
        print("\n[DRY RUN] Parsed successfully. No changes made.")
        client = WordPressClient(config['url'] or 'https://testforagents.just-ai.ru', '', '')
        payload = client.build_payload(data)

        # Save JSON payload
        json_path = '/tmp/wp_payload.json'
        Path(json_path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        print(f"\nJSON payload saved to: {json_path}")

        # Show summary
        acf = payload.get('acf', {})
        sections = acf.get('sections', [])
        print(f"\nACF fields:")
        print(f"  заголовок_h1: {acf.get('заголовок_h1', '')[:80]}...")
        print(f"  описание_под_заголовком: {acf.get('описание_под_заголовком', '')[:80]}...")
        print(f"  заголовок_для_карточки_кейса: {acf.get('заголовок_для_карточки_кейса', '')}")
        print(f"  описание_для_карточки_кейса: {acf.get('описание_для_карточки_кейса', '')[:80]}...")
        print(f"\nSections ({len(sections)}):")
        for i, s in enumerate(sections):
            layout = s.get('acf_fc_layout', '?')
            title = s.get('заголовок', s.get('текст', ''))[:60]
            print(f"  {i+1}. [{layout}] {title}")
        return

    if not config['user'] or not config['password']:
        print("\nError: WP_USER and WP_APP_PASSWORD must be set.")
        print("Set environment variables or add to .env file:")
        print("  WP_USER=mainadmin")
        print("  WP_APP_PASSWORD=xxxx xxxx xxxx xxxx")
        sys.exit(1)

    status = 'publish' if args.publish else 'draft'
    print(f"\nPublishing as {status}...")

    client = WordPressClient(config['url'], config['user'], config['password'])
    result = client.create_case(data, status=status)

    if result['success']:
        print(f"\n{'='*50}")
        print(f"Case published successfully!")
        print(f"  ID: {result['id']}")
        print(f"  Status: {result['status']}")
        print(f"  View: {result['link']}")
        print(f"  Edit: {result['edit_link']}")
        print(f"{'='*50}")
    else:
        print(f"\nError: {result.get('status_code')} - {result.get('error', 'Unknown error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
