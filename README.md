# Lazy Syllabus Planner

Put your course syllabi in a folder. A local open-weight model (Qwen 2.5 or Google's Gemma 3, via Ollama)
reads them, pulls out every deadline, and tells you the least you need to do this week to stay on time.

It writes three files: `plan.md` (week by week, with "start by" dates), `deadlines.ics` (import into
any calendar, with a reminder on the day you should start) and `morning.txt` (two lines on what's due soon).
Everything runs offline. No account, no API key, nothing uploaded.

Built for the DEV Hacktoberfest Open-Source AI Challenge, Week 3: *Slow and Steady*.

## How it works

1. Read: PDF (`pypdf`), Word `.docx`, `.txt` or `.md` syllabi are turned into plain text.
2. Extract: [Ollama](https://ollama.com) runs an open-weight model locally (Qwen 2.5 7B by default, or Gemma 3 4B), with a JSON
   schema, temperature 0, and a prompt that asks for an exact quote ("evidence") for every deadline.
3. Check: plain code checks every answer before it is accepted:
   - the quoted evidence must really be in the syllabus (blocks invented items)
   - the date must be written next to that quote (blocks wrong table rows)
   - recurring items ("every Friday") are skipped rather than given one wrong date
   - duplicates ("Presentation" / "Group presentation") are merged
   - a second pass re-asks only about dated lines nothing covered
   - due times come from the item's own sentence or a syllabus-wide rule ("all work due 11:59 pm")
4. Plan: deterministic code works backwards from each due date by effort
   (homework: same day, essay: ~3 days, exam: 4–8 days) and writes the plan and calendar.
   Each week also shows a rough estimate of the hours of work due.

Results are cached per file, so unchanged syllabi are instant on later runs.

## Setup

1. Install [Ollama](https://ollama.com/download) and pull the model:
   ```
   ollama pull qwen2.5:7b
   ```
   Optional, to try Gemma: `ollama pull gemma3:4b`
2. Install Python 3.10+ and the PDF reader:
   ```
   python -m pip install pypdf
   ```

## Use

On Windows, without a terminal: put your syllabi in a folder called `syllabi` next to `run.bat`, then double-click `run.bat`.

Anywhere:
```
python planner.py syllabi
python planner.py syllabi --done "HW 7"        # tick something off; it leaves the plan
python planner.py syllabi --model gemma3:4b    # use Gemma 3 instead of Qwen (any Ollama model works)
python schedule.py syllabi                     # preview a daily 07:30 run (add --install to create it)
```

Other options: `--today YYYY-MM-DD`, `--weeks N`, `--fresh` (ignore the cache), `--quiet`,
`--raw` (turn off the checks, to see what they add).

## Accuracy

`sample_syllabi/` holds three made-up syllabi in different styles (a schedule table, prose paragraphs,
terse numeric dates) and a hand-written answer key. Regenerate the PDFs with `python make_samples.py sample_syllabi`
(needs `reportlab`), then:

```
python planner.py sample_syllabi --today 2026-10-20 --fresh
python eval.py
```

Measured on a CPU-only laptop (Intel i7-1355U, 16 GB RAM), qwen2.5:7b:

| Mode | Required deadlines found | Wrong / extra items | Time for 3 syllabi |
|---|---|---|---|
| `--raw` (model only) | 20/20 | 1 | ~6 min |
| first guarded version | 20/20 | 4 | ~8–11 min |
| final (all checks) | 20/20 | 0 | ~7–9 min |

Two runs in a row give identical output (temperature 0). Times are for a first read; cached files are instant.

Gemma 3 4B (`--model gemma3:4b`) on the same files: 20/20 found, 1 wrong item (a duplicate on a neighbouring
table row), about 9 minutes. Qwen stays the default.

## Limits

- Scanned (image-only) PDFs are skipped with a warning; there is no OCR.
- Slow on CPU-only machines: about 2–3 minutes per new syllabus.
- Times that apply to a whole list but are not written as a syllabus-wide rule are left blank (the event is all-day).
- Tested so far on the three sample syllabi only. Real syllabi are messier, so check the plan against them.

## License

MIT, see [LICENSE](LICENSE).
