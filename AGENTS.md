# AGENTS.md — Caseorium Pipeline Agents

5-агентный пайплайн: Transcriber → Analyst → Writer → Editor → Publisher.

## Агенты

| # | Агент | Вход | Выход | Промпт |
|---|-------|------|-------|--------|
| 1 | **Transcriber** | YouTube URL | transcript.md | — |
| 2 | **Analyst** | transcript + slides | facts_extracted.md, slides_analysis.md, company_metadata.md | `engine/slides_mapping_prompt.md` |
| 3 | **Writer** | facts + metadata | case_draft_v1.md → *_READY.md | `engine/02_case_writing_rules.md`, `engine/wp_layout_rules.md` |
| 4 | **Editor** | draft | *_READY.md (отредактированный) | `engine/fact_validation_prompt.md` |
| 5 | **Publisher** | *_READY.md + slides/ | WordPress draft | `tools/publish_to_wp_v2.py` |

## Обязательные правила для всех агентов

- Читать: `engine/02_case_writing_rules.md` + `engine/wp_layout_rules.md`
- Стайлгайд: `references/russian-style-guide.md`
- Контекст: `engine/00_context.md`
- Референсные кейсы: `cases/examples/`

## HITL-чекпоинты

Пайплайн останавливается для ревью после этапов Analyst и Writer.
Настраивается в `agents/pipeline.py`.

## Запуск

```bash
# Полный пайплайн
python3 main.py --url <youtube_url> [--slides <path.pdf>]

# Writer + Editor на готовом анализе
python3 run_writer_editor.py <case_dir>

# Веб-интерфейс
python3 server.py
```
