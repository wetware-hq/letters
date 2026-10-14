#!/usr/bin/env python3
"""The safety-bar check run before a push. Exit 0 only when everything below passes."""
import pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402

ANSWER = re.compile(r"(truth\.toml|expected\.md|index\.md|patient_wording\.md|C\d\d\.toml|S\d\d[a-z]?-.*)")


def main():
    truth = paths.truth()
    if truth is None:
        import letters
        print(letters.RUN_TRACES_NOT_RUN)
        return 1
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    sys.path.insert(0, str(ROOT / "scripts"))
    import mutation_hash
    problems = [] if mutation_hash.fresh() else [mutation_hash.STALE]
    problems += [f"{f}: answer file in the commit" for f in files if any(ANSWER.fullmatch(p) for p in f.split("/"))]
    py = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    if subprocess.run(py + ["test_letters.py::test_refusal_codes_signed_in_both_files"], cwd=ROOT).returncode:
        problems.append("a refusal line is not signed in both files (test_refusal_codes_signed_in_both_files)")
    r = subprocess.run(py, cwd=ROOT, capture_output=True, text=True)
    print(r.stdout[-1500:])
    if r.returncode:
        problems.append("tests failed")
    if re.search(r"\d+ (skipped|deselected)", r.stdout):
        problems.append("tests skipped")
    if subprocess.run([sys.executable, "scripts/check14_derived.py"], cwd=ROOT).returncode:
        problems.append("derived traces failed")
    for p in problems:
        print(p)
    print(f"pre-push: {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
