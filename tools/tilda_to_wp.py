#!/usr/bin/env python3
"""
Migrate a case study from Tilda (generation-ai.ru) to WordPress.

Fetches a published Tilda page, parses the visual blocks,
maps them to ACF Flexible Content layouts, and publishes to WP as a draft.

Usage:
    python3 tilda_to_wp.py <tilda_url> [--dry-run] [--publish]

Examples:
    python3 tilda_to_wp.py https://generation-ai.ru/cases/aviasales-genai-quality
    python3 tilda_to_wp.py https://generation-ai.ru/cases/aviasales-genai-quality --dry-run

Tilda block type mapping (data-record-type):
    131  = spacer (skip)
    179  = hero title
    257  = navigation menu (skip)
    383  = mobile menu (skip)
    396  = artboard (footer, related cases)
    404  = related cases grid
    513  = author/date metadata
    510  = inset text block (numbered list / special formatting)
    657  = cookie banner (skip)
    673  = accent quote (green/dark background callout)
    702  = popup form (skip)
    758  = breadcrumb
    778  = carousel / metric cards
    1206 = company description card
    3    = image
    60   = text section (with optional h2)

Environment:
    WP_URL, WP_USER, WP_APP_PASSWORD — WordPress credentials
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup, Tag

sys.path.insert(0, str(Path(__file__).parent))
from publish_to_wp import WordPressClient, load_config, OTRASLI_MAP

# Block types to skip entirely
SKIP_TYPES = {'131', '257', '383', '394', '657', '702', '121', '217', '270'}

# Block types that are structural (not content)
STRUCTURAL_TYPES = {'396'}


class TildaParser:
    """Parse a Tilda case study page into ACF-compatible structure."""

    def __init__(self, url: str):
        self.url = url
        self.soup = None

    def fetch(self) -> None:
        resp = requests.get(self.url, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; CaseoriumBot/1.0)',
        })
        resp.raise_for_status()
        self.soup = BeautifulSoup(resp.text, 'html.parser')

    def parse(self) -> dict:
        if not self.soup:
            self.fetch()

        result = {
            'h1_title': '',
            'company_description': '',
            'card_title': '',
            'author_name': '',
            'author_role': '',
            'date': '',
            'seo_title': '',
            'seo_description': '',
        }

        # Extract SEO meta
        og_title = self.soup.find('meta', property='og:title')
        if og_title:
            result['seo_title'] = og_title.get('content', '')
        og_desc = self.soup.find('meta', property='og:description')
        if og_desc:
            result['seo_description'] = og_desc.get('content', '')

        # Process all t-rec blocks in order
        acf_sections = []
        recs = self.soup.find_all('div', class_='t-rec')

        for rec in recs:
            style = rec.get('style', '')
            if 'display:none' in style or 'display: none' in style:
                continue

            rtype = rec.get('data-record-type', '')

            if rtype in SKIP_TYPES:
                continue

            if rtype in STRUCTURAL_TYPES:
                # Check if it's "Другие кейсы" — stop processing
                text = rec.get_text(strip=True)
                if 'Другие кейсы' in text or 'МЕНЮ' in text:
                    break
                continue

            section = self._parse_block(rtype, rec, result)
            if section:
                acf_sections.append(section)

        # Add CTA
        acf_sections.append({
            'acf_fc_layout': 'layer_cta_form',
            'отступ_сверху_секции': '64',
            'отступ_снизу_секции': '0',
            'текст': ('Хотите внедрить AI-агентов в вашей компании? '
                       'Используйте Just AI Agent Platform — платформу для '
                       'автоматизации процессов с помощью AI-агентов'),
            'button': {
                'btn_text': 'Оставить заявку',
                'btn_type': 'popup',
                'btn_form': 'consultation',
            },
        })

        result['acf_sections'] = acf_sections
        return result

    def _parse_block(self, rtype: str, rec: Tag, result: dict) -> dict | None:
        """Parse a single t-rec block by its Tilda type."""

        # 758 — breadcrumb
        if rtype == '758':
            links = rec.find_all('a')
            if links:
                result['card_title'] = links[-1].get_text(strip=True)
            return None

        # 513 — author/date
        if rtype == '513':
            self._extract_author(rec, result)
            return None

        # 179 — hero title
        if rtype == '179':
            result['h1_title'] = rec.get_text(strip=True)
            return None

        # 1206 — company description card
        if rtype == '1206':
            result['company_description'] = rec.get_text(strip=True)
            return None

        # 673 — accent quote
        if rtype == '673':
            text = rec.get_text(strip=True)
            return {
                'acf_fc_layout': 'layer_accent_text',
                'отступ_сверху_секции': '32',
                'отступ_снизу_секции': '32',
                'заголовок_блока': '',
                'текст': '<p>%s</p>' % self._clean_text(text),
            }

        # 510 — inset text block (often numbered list or special formatting)
        if rtype == '510':
            return self._parse_text_block(rec, is_inset=True)

        # 778 — carousel / metric cards
        if rtype == '778':
            return self._parse_carousel(rec)

        # 3 — image
        if rtype == '3':
            return self._parse_image(rec)

        # 404 — related cases (skip, handled by WP theme)
        if rtype == '404':
            return None

        # 60 — text section (main content type)
        if rtype == '60':
            return self._parse_text_block(rec)

        # Unknown type — try generic text extraction
        text = rec.get_text(strip=True)
        if text and len(text) > 30:
            return {
                'acf_fc_layout': 'custom_text',
                'отступ_сверху_секции': '32',
                'отступ_снизу_секции': '32',
                'заголовок': '',
                'текст': '<p>%s</p>' % self._clean_text(text),
            }

        return None

    def _extract_author(self, rec: Tag, result: dict) -> None:
        """Extract author name, role, and date from type 513 block."""
        # Tilda 513 has date + author name + description
        all_text = rec.get_text('\n', strip=True).split('\n')
        all_text = [t.strip() for t in all_text if t.strip()]

        if len(all_text) >= 1:
            result['date'] = all_text[0]
        if len(all_text) >= 2:
            result['author_name'] = all_text[1]
        if len(all_text) >= 3:
            result['author_role'] = all_text[2]

    def _parse_text_block(self, rec: Tag, is_inset: bool = False) -> dict | None:
        """Parse type 60 (text section) or 510 (inset text)."""
        # Extract heading
        h2 = rec.find('h2')
        title = h2.get_text(strip=True) if h2 else ''

        # Extract body HTML — look for description/text divs
        body_parts = []

        # Find all text containers
        text_divs = rec.find_all(class_=re.compile(
            r't050__descr|t050__text|t-descr|t-text|t510__text', re.I
        ))

        if text_divs:
            for div in text_divs:
                # Skip if this IS the heading
                if div.find_parent('h2') or div == h2:
                    continue
                html = self._div_to_html(div)
                if html:
                    body_parts.append(html)
        else:
            # Fallback: get all paragraphs
            for p in rec.find_all(['p', 'ul', 'ol']):
                if p.find_parent('h2'):
                    continue
                html = self._element_to_html(p)
                if html:
                    body_parts.append(html)

        body_html = '\n'.join(body_parts)

        if not title and not body_html:
            # Try plain text fallback
            text = rec.get_text(strip=True)
            if text and len(text) > 30:
                body_html = '<p>%s</p>' % self._clean_text(text)
            else:
                return None

        layout = 'note_text' if is_inset else 'custom_text'

        if is_inset:
            return {
                'acf_fc_layout': 'note_text',
                'отступ_сверху_секции': '32',
                'отступ_снизу_секции': '32',
                'текст': body_html or '<p>%s</p>' % self._clean_text(rec.get_text(strip=True)),
            }

        return {
            'acf_fc_layout': 'custom_text',
            'отступ_сверху_секции': '64' if title else '32',
            'отступ_снизу_секции': '64' if title else '32',
            'заголовок': title,
            'текст': body_html,
        }

    def _parse_carousel(self, rec: Tag) -> dict | None:
        """Parse type 778 (carousel / metric cards) into layer_columns."""
        # 778 blocks contain slides with metric + description
        slides = rec.find_all(class_=re.compile(r't778__content|t778__textwrapper', re.I))

        if not slides:
            # Try generic extraction — look for repeated patterns
            all_text = rec.get_text('\n', strip=True).split('\n')
            all_text = [t.strip() for t in all_text if t.strip()]

            # Detect metric pairs
            columns = []
            i = 0
            while i < len(all_text):
                text = all_text[i]
                # Metric-like: short, has digits/% or is a known pattern
                if (len(text) < 30 and
                    (re.search(r'\d|%|\+', text) or text in ('р.', 'р. р.')) and
                    i + 1 < len(all_text)):
                    # Skip "р." artifacts
                    if text in ('р.', 'р. р.'):
                        i += 1
                        continue
                    desc = all_text[i + 1]
                    if desc in ('р.', 'р. р.'):
                        i += 1
                        continue
                    columns.append({'title': text, 'text': desc})
                    i += 2
                else:
                    # Could be a comparison block (Вариант 1 / Вариант 2)
                    if text.startswith('Вариант') or text.startswith('Сценарий'):
                        # Collect until next "Вариант" or end
                        variant_title = text
                        variant_text_parts = []
                        i += 1
                        while i < len(all_text) and not all_text[i].startswith('Вариант') and not all_text[i].startswith('Сценарий'):
                            variant_text_parts.append(all_text[i])
                            i += 1
                        columns.append({
                            'title': variant_title,
                            'text': ' '.join(variant_text_parts),
                        })
                    else:
                        i += 1

            if len(columns) >= 2:
                return {
                    'acf_fc_layout': 'layer_columns',
                    'отступ_сверху_секции': '32',
                    'отступ_снизу_секции': '32',
                    'заголовок_блока_с_колонками': '',
                    'подзаголовок_блока_с_колонками': '',
                    'колонки': [
                        {
                            'acf_fc_layout': '',
                            'заголовок_иконка': 'Заголовок',
                            'заголовок': col['title'],
                            'цвет_заголовка': '#24DD63',
                            'размер_заголовка': 'large' if re.search(r'\d', col['title']) else 'normal',
                            'текст': col['text'],
                        }
                        for col in columns
                    ],
                }

        # Fallback: render as text
        text = rec.get_text(strip=True)
        if text and len(text) > 20:
            return {
                'acf_fc_layout': 'custom_text',
                'отступ_сверху_секции': '32',
                'отступ_снизу_секции': '32',
                'заголовок': '',
                'текст': '<p>%s</p>' % self._clean_text(text),
            }

        return None

    def _parse_image(self, rec: Tag) -> dict | None:
        """Parse type 3 (image block)."""
        img = rec.find('img')
        caption = rec.get_text(strip=True)

        if img:
            src = img.get('data-original', '') or img.get('src', '')
            # Make absolute URL
            if src and src.startswith('/'):
                src = 'https://generation-ai.ru' + src
            elif src and not src.startswith('http'):
                src = 'https://generation-ai.ru/' + src

            return {
                'acf_fc_layout': 'custom_text',
                'отступ_сверху_секции': '16',
                'отступ_снизу_секции': '16',
                'заголовок': '',
                'текст': (
                    '<p style="color: #e67e22; font-style: italic;">'
                    '[ИЗОБРАЖЕНИЕ: %s]</p>'
                    '<!-- tilda_image_src: %s -->' % (
                        caption or 'без подписи',
                        src,
                    )
                ),
            }

        if caption:
            return {
                'acf_fc_layout': 'custom_text',
                'отступ_сверху_секции': '16',
                'отступ_снизу_секции': '16',
                'заголовок': '',
                'текст': '<p><em>%s</em></p>' % caption,
            }

        return None

    # --- Helpers ---

    def _div_to_html(self, div: Tag) -> str:
        """Convert a Tilda text div to clean HTML."""
        # Get inner HTML, strip Tilda classes
        parts = []
        for child in div.children:
            if isinstance(child, Tag):
                if child.name in ('p', 'div'):
                    text = child.get_text(strip=True)
                    if text:
                        parts.append('<p>%s</p>' % self._clean_text(text))
                elif child.name in ('ul', 'ol'):
                    items = []
                    for li in child.find_all('li'):
                        items.append('<li>%s</li>' % self._clean_text(li.get_text(strip=True)))
                    parts.append('<%s>%s</%s>' % (child.name, ''.join(items), child.name))
                elif child.name in ('h3', 'h4'):
                    text = child.get_text(strip=True)
                    if text:
                        parts.append('<%s>%s</%s>' % (child.name, text, child.name))
                elif child.name == 'br':
                    continue
                else:
                    text = child.get_text(strip=True)
                    if text:
                        parts.append('<p>%s</p>' % self._clean_text(text))
            elif hasattr(child, 'strip'):
                text = child.strip()
                if text:
                    parts.append('<p>%s</p>' % self._clean_text(text))

        return '\n'.join(parts)

    def _element_to_html(self, el: Tag) -> str:
        """Convert a single element to HTML."""
        text = el.get_text(strip=True)
        if not text:
            return ''
        if el.name in ('ul', 'ol'):
            items = []
            for li in el.find_all('li'):
                items.append('<li>%s</li>' % self._clean_text(li.get_text(strip=True)))
            return '<%s>%s</%s>' % (el.name, ''.join(items), el.name)
        return '<p>%s</p>' % self._clean_text(text)

    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean text: normalize whitespace, fix encoding."""
        text = re.sub(r'\s+', ' ', text).strip()
        # Fix common HTML entities
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        return text


def main():
    parser = argparse.ArgumentParser(description="Migrate Tilda case to WordPress")
    parser.add_argument("url", help="Tilda case page URL")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't publish")
    parser.add_argument("--publish", action="store_true", help="Publish immediately")
    parser.add_argument("--save-json", help="Save parsed payload to JSON file")
    args = parser.parse_args()

    print("Fetching: %s" % args.url)
    tilda = TildaParser(args.url)
    tilda.fetch()
    data = tilda.parse()

    print()
    print("  Title: %s" % (data.get('h1_title') or 'N/A'))
    print("  Author: %s, %s" % (data.get('author_name', 'N/A'), data.get('author_role', 'N/A')))
    print("  Date: %s" % data.get('date', 'N/A'))
    print("  Card title: %s" % data.get('card_title', 'N/A'))
    print("  Company: %s..." % (data.get('company_description', 'N/A')[:80]))
    print("  SEO title: %s" % data.get('seo_title', 'N/A'))
    print("  Sections: %d" % len(data.get('acf_sections', [])))
    print()

    for i, s in enumerate(data['acf_sections']):
        layout = s.get('acf_fc_layout', '?')
        title = (s.get('заголовок', '') or
                 s.get('заголовок_блока', '') or
                 s.get('заголовок_блока_с_колонками', '') or '')
        text_preview = (s.get('текст', '') or '')[:50]
        extra = ''
        if layout == 'layer_columns':
            extra = ' (%d cols)' % len(s.get('колонки', []))
        print("  %3d. [%-25s]%s %s" % (i + 1, layout, extra, title[:40] or text_preview))

    # Build WP payload
    payload = {
        'title': data['h1_title'],
        'status': 'publish' if args.publish else 'draft',
        'acf': {
            'заголовок_h1': data['h1_title'],
            'описание_под_заголовком': data.get('company_description', ''),
            'заголовок_для_карточки_кейса': data.get('card_title', data['h1_title']),
            'описание_для_карточки_кейса': data.get('seo_description', ''),
            'sections': data['acf_sections'],
        },
    }

    if args.save_json or args.dry_run:
        json_path = args.save_json or '/tmp/tilda_wp_payload.json'
        Path(json_path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        print("\nJSON payload saved to: %s" % json_path)

    if args.dry_run:
        print("\n[DRY RUN] Parsed successfully. No changes made.")
        return

    config = load_config()
    if not config['user'] or not config['password']:
        print("\nError: WP_USER and WP_APP_PASSWORD must be set.")
        sys.exit(1)

    client = WordPressClient(config['url'], config['user'], config['password'])
    status = 'publish' if args.publish else 'draft'
    print("\nPublishing as %s..." % status)

    resp = client.session.post("%s/cases" % client.api_url, json=payload)

    if resp.status_code == 201:
        r = resp.json()
        print("\n" + "=" * 50)
        print("Case migrated successfully!")
        print("  ID: %d" % r['id'])
        print("  Status: %s" % r['status'])
        print("  View: %s" % r['link'])
        print("  Edit: %s/wp-admin/post.php?post=%d&action=edit" % (client.base_url, r['id']))
        print("=" * 50)
    else:
        print("\nError: %d - %s" % (resp.status_code, resp.text[:500]))
        sys.exit(1)


if __name__ == "__main__":
    main()
