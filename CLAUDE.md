# CLAUDE.md — Caseorium Hackathon

Мульти-агентный пайплайн: YouTube → транскрипция → анализ → написание → редактура → WordPress.
Медиа Generation AI (generation-ai.ru) — кейсы о внедрении AI в бизнес.

## Быстрый старт

```bash
# Полный пайплайн (YouTube → WordPress)
python3 main.py --url <youtube_url> [--slides <path.pdf>]

# Только Writer + Editor (на готовом анализе)
python3 run_writer_editor.py <case_dir>

# Публикация в WordPress
python3 tools/publish_to_wp_v2.py <file>_READY.md [--slides-dir dir] [--dry-run]

# Веб-интерфейс
python3 server.py
```

## Структура проекта

### Код пайплайна
- `main.py` — CLI: полный пайплайн (YouTube URL → WordPress)
- `server.py` — FastAPI веб-сервер + UI
- `run_writer_editor.py` — запуск только Writer + Editor на готовых файлах

### `agents/` — агенты пайплайна (Claude Agent SDK)
- `definitions.py` — определения 5 агентов (Transcriber → Analyst → Writer → Editor → Publisher)
- `pipeline.py` — оркестратор цепочки агентов, HITL-чекпоинты
- `tools.py` — MCP-обертки над скриптами для вызова агентами

### `tools/` — утилиты
- `transcribe_youtube.py` — транскрипция YouTube (Deepgram / субтитры)
- `extract_slides.py` — PDF презентация → PNG слайды
- `publish_to_wp_v2.py` — **основной** WP-публикатор (парсит _READY.md → ACF блоки)
- `publish_to_wp.py` — старая версия публикатора (deprecated)
- `tilda_to_wp.py` — миграция кейса с Tilda → WordPress
- `mcp_tilda.py` — MCP-сервер для доступа к Tilda-страницам

### `engine/` — промпты и правила (мозг пайплайна)
- `00_context.md` — контекст Generation AI, Just AI, ценность проекта
- `00_pipeline_orchestrator.md` — инструкция для AI-оркестратора (все этапы)
- `01_reference_case.md` — эталонный кейс (Совкомбанк) для обучения
- **`02_case_writing_rules.md`** — **ГЛАВНЫЙ**: правила написания кейсов, ToV, структура
- `03_pipeline_v1.md` — описание пайплайна v1 (5 этапов)
- **`wp_layout_rules.md`** — **ГЛАВНЫЙ**: правила верстки для WordPress (заголовки, блоки, маркеры)
- `SKILL_case_builder.md` — скилл case-builder для Claude Code
- `brief_analyzer_prompt.md` — промпт: анализ брифов (не расшифровок)
- `design_block_guide.md` — гайд по блоку "Для дизайна:" (метаданные)
- `fact_validation_prompt.md` — промпт: валидация фактов кейса vs источников
- `seo_checklist.md` — SEO-чеклист (Rank Math, ключевые слова)
- `slides_mapping_prompt.md` — промпт: извлечение фактуры из слайдов
- `CHANGELOG.md` — история изменений движка

### `knowledge/` — база знаний о продукте и аудитории
- `00_product_overview.md` — продуктовая линейка Just AI
- `01_product_use_cases.md` — реальные сценарии использования
- `02_expert_pool.md` — экспертная база (маркетинг, продукт, продажи)
- `03_cases_knowledge_base.md` — база существующих 51 кейсов
- `04_generation_ai_project.md` — база знаний проекта Generation AI
- `05_audience.md` — целевая аудитория
- `brand_brief.md` — бриф на редизайн знака

### `cases/` — кейсы (входные данные + результаты)

Каждый кейс = папка с файлами пайплайна:
- `transcript.md` / `*.srt` / `*.vtt` — исходная расшифровка
- `slides/` — PNG-слайды из презентации
- `facts_extracted.md` — извлеченные факты (этап Analyst)
- `slides_analysis.md` — анализ слайдов
- `company_metadata.md` — метаданные компании
- `case_draft_v*.md` — черновики (этап Writer)
- `*_READY.md` — **финальный кейс** для публикации

#### Готовые кейсы
| Папка | Статус | Файл |
|-------|--------|------|
| `cases/examples/sber_READY.md` | Референс | Сбер |
| `cases/examples/lamoda_READY.md` | Референс | Lamoda |
| `cases/examples/vkusvill_READY.md` | Референс | ВкусВилл |
| `cases/Аспирити_draft/` | Готов | `Аспирити_READY.md` |
| `cases/Т-Банк_draft/` | Готов | `Т-Банк_READY.md` |
| `cases/tbank_draft/` | Готов | `tbank_READY.md` |
| `cases/aviasales_draft/` | Черновик | `aviasales_RICH.md`, `aviasales_READY.md` |
| `cases/nigoyan_lamoda/` | Исходники | транскрипт + слайды |
| `cases/vkusvill_freshness/` | В работе | `case_vkusvill_freshness.md` |

### Прочее
- `references/russian-style-guide.md` — стайлгайд (инфостиль, антиклише)
- `web/index.html` — веб-интерфейс
- `uploads/` — загруженные PDF и картинки
- `tests/test_rich_blocks.md` — тесты rich-блоков

## Правила

- Язык общения: русский
- Код и комментарии: английский
- Стиль текстов: инфостиль, без воды, без нейросетевых клише
- Стайлгайд: `references/russian-style-guide.md`
- **ОБЯЗАТЕЛЬНО читать перед работой с кейсами:** `engine/02_case_writing_rules.md` + `engine/wp_layout_rules.md`
- **Главное правило текста:** максимально близко к тому, что говорит спикер. Сохранять ВСЕ детали, примеры, технические подробности. Убирать только разговорные обороты. НИКОГДА не резать фактуру агрессивно.

## ЖЕСТКИЕ ЗАПРЕТЫ (нарушение = брак)

1. **НЕ упоминать спикера по имени в тексте кейса.** Пиши от лица компании: «команда называет», «в компании объясняют», безличные конструкции. Имя спикера — ТОЛЬКО в блоке «Для дизайна:» (поле «Автор:»).
2. **НЕ давать ссылки на Telegram, соцсети, внешние ресурсы.** Единственная допустимая ссылка — сайт компании.
3. **НЕ ставить 3+ картинки подряд** без текста между ними. Максимум 2 подряд (галерея).
4. **Жирным ТОЛЬКО числа и метрики.** НЕ описательные фразы, НЕ инсайты. Справка о компании — НИКОГДА жирным.
5. **НЕ додумывать факты и цифры.** Если нет в исходниках — пометить [уточнить].

## Rich-блоки для WordPress (markdown-маркеры)

Кейс должен содержать 4-8 rich-блоков. Маркеры:
- `:::columns` — карточки для метрик и сравнений (2-4 штуки)
- `:::steps` — пошаговые процессы (номера автоматические)
- `:::accent` — ключевые инсайты (макс 2-3 на кейс)
- `:::list` — структурированные списки с заголовками
- `:::tech` — tech stack (только если 3+ инструментов)
- `> цитата` — плашка, литературный стиль (НЕ разговорная речь)
- Все `:::` блоки ЗАКРЫВАТЬ маркером `:::`

Подробный синтаксис: `engine/wp_layout_rules.md`

## WordPress

- Тестовый: testforagents.just-ai.ru
- Post type: `cases`
- ACF Flexible Content для секций (14 типов layouts, см. память `reference_acf_blocks.md`)
- Таксономии: отрасли + задачи
- Публикация: `python3 tools/publish_to_wp_v2.py <file> [--slides-dir dir] [--dry-run]`
