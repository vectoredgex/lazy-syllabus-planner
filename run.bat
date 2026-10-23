@echo off
rem Double-click me. Put your syllabus files in a folder called "syllabi" next to this file.
rem Needs Python and Ollama installed (see README.md).
cd /d "%~dp0"
if not exist syllabi mkdir syllabi
python planner.py syllabi
echo.
if errorlevel 1 (
    echo Nothing was planned. Put your syllabus PDFs or Word files in the "syllabi" folder, make sure Ollama is running, then run me again.
) else (
    echo Your plan is in syllabi\plan.md, and the calendar file is syllabi\deadlines.ics
)
pause
