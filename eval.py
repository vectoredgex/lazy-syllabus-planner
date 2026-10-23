#!/usr/bin/env python3
"""Score a planner run against the sample answer key.

    python planner.py sample_syllabi --today 2026-10-20 --fresh
    python eval.py sample_syllabi/deadlines.json sample_syllabi/answer_key.json
"""
import json
import re
import sys
from collections import defaultdict


def words(s):
    return set(re.findall(r"[a-z0-9]+", s.lower())) - {"the", "a", "of", "due", "in", "class"}


def main(found_path, key_path):
    found = json.load(open(found_path, encoding="utf-8"))
    key = {k: v for k, v in json.load(open(key_path, encoding="utf-8")).items() if not k.startswith("_")}
    by_file = defaultdict(list)
    for f in found:
        by_file[f["source"]].append(f)

    hit = miss = extra = 0
    for src, expected in key.items():
        got = list(by_file.get(src, []))
        print(f"\n{src}")
        for e in expected:
            # match on the date plus at least one shared word, so the right item gets credit
            match = next((g for g in got if g["due"] == e["due"] and words(g["title"]) & words(e["what"])), None)
            match = match or next((g for g in got if g["due"] == e["due"]), None)
            if match:
                got.remove(match)
                if not e.get("optional"):
                    hit += 1
                print(f"  ✅ {e['due']}  {e['what']:<24} → {match['title']}")
            elif e.get("optional"):
                print(f"  ·  {e['due']}  {e['what']:<24} (optional, not found)")
            else:
                miss += 1
                print(f"  ❌ {e['due']}  {e['what']:<24} MISSED")
        for g in got:
            extra += 1
            print(f"  ➕ {g['due']}  {g['title']}  (not in key)")

    total = hit + miss
    print(f"\nFound {hit}/{total} required deadlines ({100 * hit / max(total, 1):.0f}%), "
          f"{miss} missed, {extra} extra.")
    return 0 if miss == 0 else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(*(sys.argv[1:3] or ["sample_syllabi/deadlines.json", "sample_syllabi/answer_key.json"])))
