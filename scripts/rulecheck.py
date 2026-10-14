#!/usr/bin/env python3
"""Exit 0 only if every row of RULES.md lists at least one test and every listed test exists in the test files."""
import pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    defined = set()
    for p in ROOT.glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_\w+)\(", p.read_text(encoding="utf-8"), re.M))
    rows = [l for l in (ROOT / "RULES.md").read_text(encoding="utf-8").splitlines() if re.match(r"^\| \d+ \|", l)]
    bad = []
    for r in rows:
        cells = [c.strip() for c in r.strip("|").split("|")]
        n, tests = cells[0], [t.strip() for t in cells[-1].split(",") if t.strip()]
        missing = [t for t in tests if t not in defined]
        if not tests or missing or cells[2] != "Physician" or cells[3] != "safety":
            bad.append(f"rule {n}: " + (f"missing tests {missing}" if missing else "no tests or wrong signer/class"))
    nums = [int(r.split("|")[1]) for r in rows]
    if nums != list(range(1, 20)):
        bad.append(f"rows must be 1..19, got {nums}")
    for b in bad:
        print(b)
    print(f"rulecheck: {len(rows)} rules, {len(bad)} problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
