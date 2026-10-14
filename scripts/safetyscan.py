"""Which functions must be in safety_functions.toml: any top-level function (nested helpers count for their parent)
that raises a refusal, maps a line id into a refusal or screen message, opens a queue entry or writes the session."""
import ast, pathlib, re, tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
REFUSALS = {"Refused", "ComboRefused"}


def kinds(fn):
    k = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Raise) and n.exc is not None:
            c = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
            if getattr(c, "id", getattr(c, "attr", None)) in REFUSALS:
                k.add("refuses")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "format" \
                and any(kw.arg in ("lid", "n") for kw in n.keywords):
            k.add("maps a line")
        if (isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith("CQ-")) or \
                (isinstance(n, ast.Name) and n.id == "BY_HAND_LINES"):
            k.add("opens a queue entry")
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("write_text", "write_bytes",
                                                                                             "mkdir"):
            k.add("writes the session")
    return k


def required(src):
    tree = ast.parse(src)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    fns += [m for c in tree.body if isinstance(c, ast.ClassDef) for m in c.body if isinstance(m, ast.FunctionDef)]
    return {f.name: sorted(kinds(f)) for f in fns if kinds(f)}


def listed(path=ROOT / "safety_functions.toml"):
    return {f["name"] for f in tomllib.loads(pathlib.Path(path).read_text(encoding="utf-8"))["functions"]}


def missing(src, names):
    return {n: k for n, k in required(src).items() if n not in names}
