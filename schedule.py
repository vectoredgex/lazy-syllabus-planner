#!/usr/bin/env python3
"""Set up the planner to run by itself every morning (and nothing else).

    python schedule.py path/to/syllabi              # show what it WOULD do
    python schedule.py path/to/syllabi --install    # actually create the schedule
    python schedule.py --remove                     # undo it

Windows uses Task Scheduler. Mac and Linux get a crontab line.
It only re-reads syllabi that changed, so most mornings take a few seconds.
"""
import argparse
import platform
import shlex
import subprocess
import sys
from pathlib import Path

TASK = "LazySyllabusPlanner"
MARK = "# lazy-syllabus-planner"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", type=Path, help="folder with your syllabus files")
    ap.add_argument("--time", default="07:30", help="HH:MM, 24h (default 07:30)")
    ap.add_argument("--install", action="store_true", help="actually create the schedule")
    ap.add_argument("--remove", action="store_true", help="remove the schedule")
    args = ap.parse_args()
    windows = platform.system() == "Windows"

    if args.remove:
        if windows:
            return subprocess.call(["schtasks", "/delete", "/tn", TASK, "/f"])
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        keep = "".join(l + "\n" for l in cur.splitlines() if MARK not in l)
        subprocess.run(["crontab", "-"], input=keep, text=True, check=True)
        print("Removed.")
        return 0

    if not args.folder or not args.folder.is_dir():
        ap.error("give the folder that holds your syllabi")
    hh, mm = args.time.split(":")
    planner = Path(__file__).with_name("planner.py").resolve()
    folder = args.folder.resolve()

    if windows:
        # pythonw.exe has no console window, so nothing flashes on screen every morning
        exe = Path(sys.executable).with_name("pythonw.exe")
        exe = exe if exe.exists() else Path(sys.executable)
        cmd = ["schtasks", "/create", "/tn", TASK, "/sc", "daily", "/st", f"{int(hh):02d}:{mm}", "/f",
               "/tr", f'"{exe}" "{planner}" "{folder}" --quiet']
        shown = subprocess.list2cmdline(cmd)
    else:
        line = f"{int(mm)} {int(hh)} * * * {shlex.quote(sys.executable)} {shlex.quote(str(planner))} " \
               f"{shlex.quote(str(folder))} --quiet {MARK}"
        shown = f"(add to crontab) {line}"

    print(f"This will run the planner every day at {args.time} on:\n  {folder}\n")
    print(shown)
    print("\nEach run refreshes plan.md, deadlines.ics and morning.txt in that folder.")
    if not args.install:
        print("\nNothing was changed. Add --install to create it.")
        return 0
    if windows:
        return subprocess.call(cmd)
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    keep = "".join(l + "\n" for l in cur.splitlines() if MARK not in l)
    subprocess.run(["crontab", "-"], input=keep + line + "\n", text=True, check=True)
    print("Installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
