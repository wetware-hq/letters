"""Generalisation guard (same as stack): find hard-coded specialty, ward, discipline or drug names in code string literals.
Domain names belong in signed lists and data files (toml/csv/md), never in .py code. Docstrings are skipped
(prose); every other string constant is scanned word by word."""
import ast, pathlib, re

TERMS_FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "domain_terms.toml"
TERMS = __import__("tomllib").loads(TERMS_FILE.read_text(encoding="utf-8"))   # the vocabulary is data, not code
_RX = {k: re.compile(r"(?<![\w-])(" + "|".join(re.escape(t) for t in v) + r")(?![\w-])", 0 if k == "ward" else re.I) for k, v in TERMS.items()}


def _docstrings(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body:
            b = n.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                out.add(id(b.value))
    return out


def scan_file(path):
    src = pathlib.Path(path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    skip, hits = _docstrings(tree), []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip:
            for kind, rx in _RX.items():
                for m in rx.finditer(n.value):
                    hits.append((str(path), n.lineno, kind, m.group(1)))
    return hits


def scan(root, exclude=("tests", ".venv", "test_", "evals", "scripts/mutate")):
    out = []
    for p in sorted(pathlib.Path(root).rglob("*.py")):
        rel = str(p.relative_to(root))
        if any(rel.startswith(e) or f"/{e}" in rel or p.name.startswith("test_") for e in exclude):
            continue
        out += scan_file(p)
    return out


if __name__ == "__main__":
    import sys
    for root in sys.argv[1:]:
        for f, ln, kind, w in scan(root):
            print(f"{f}:{ln}\t{kind}\t{w}")
