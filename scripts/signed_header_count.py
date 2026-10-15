#!/usr/bin/env python3
"""Standalone check (standard library only; copy as-is into any repo): the line count a signing file states equals
the rows parsed from that file.

Checked statements:
  * a title like "(SIGNED by Physician, 12 lines" -> the number of table rows that start with a line id
    ("| ID-LIKE-THIS |" or "| ID_LIKE_THIS |");
  * a heading like "## Per-id safety register (...), 44 ids" -> the number of "- ID" bullets under that heading.
A file with no such statement has nothing to check.

    python3 signed_header_count.py FILE [FILE ...]     exit 1 and one line per mismatch if any count is wrong
"""
import re, sys

TITLE = re.compile(r"\bSIGNED by [A-Za-z ]+, (\d+) lines\b")
TABLE_ROW = re.compile(r"^\| ([A-Z][A-Za-z0-9]*(?:[-_][A-Za-z0-9\[\]]+)+) \|", re.M)
REGISTER = re.compile(r"^(## .*register\b.*?), (\d+) ids\b.*$", re.M)
BULLET = re.compile(r"^- (\S+)\s*$", re.M)


def problems(text, name="file"):
    out = []
    m = TITLE.search(text)
    if m:
        rows = len(TABLE_ROW.findall(text))
        if rows != int(m.group(1)):
            out.append(f"{name}: header says {m.group(1)} lines, {rows} rows parsed")
    for r in REGISTER.finditer(text):
        section = text[r.end():].split("\n## ")[0]
        n = len(BULLET.findall(section))
        if n != int(r.group(2)):
            out.append(f"{name}: '{r.group(1)}' says {r.group(2)} ids, {n} listed")
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    bad = [p for f in argv[1:] for p in problems(open(f, encoding="utf-8").read(), f)]
    for p in bad:
        print(p)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
