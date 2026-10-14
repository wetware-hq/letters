"""Physician's fill-in rule (letters_refusals_SAFETY.md, condition 1): every fill-in of a refusal line holds only a
line ref, position, count, limit or fixed label from a closed set; never note text, a value, a medicine, a brand,
a condition or a name. Static scan of letters.py; returns every fill-in that is not one of the allowed forms."""
import ast, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import refusalcodes  # noqa: E402

LABELS = {"sec": {"medicines", "allergies"}, "what": {"an action", "a medicine", "an allergy"},
          "where": {"in the brief", "in the template"}}
NAMES = {"lid": "line ref", "n": "line number", "at": "position", "words_total": "count",
         "WORD_LIMIT": "limit", "SENTENCE_LIMIT": "limit"}
REF_FORMS = {"f'L{n}'", "med_ids[0]"}
AT_PARTS = {"cited[0]", "mdn"}


def _const_names(src):
    return {k.split("[")[0] for k in refusalcodes.codes(src) if ":" not in k}


def scan(src=None):
    src = src if src is not None else (ROOT / "letters.py").read_text(encoding="utf-8")
    tree, codes, bad, seen = ast.parse(src), _const_names(src), [], []
    for f in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        fsrc = ast.unparse(f)
        for n in ast.walk(f):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "format":
                v = n.func.value
                name = v.id if isinstance(v, ast.Name) else getattr(getattr(v, "value", None), "id", None)
                if name not in codes:
                    continue
                if n.args:
                    bad.append((n.lineno, name, "positional", ast.unparse(n.args[0])))
                for k in n.keywords:
                    e = ast.unparse(k.value)
                    seen.append((n.lineno, name, k.arg, e))
                    ok = (isinstance(k.value, ast.Constant) and k.value.value in LABELS.get(k.arg, ())) or \
                         (isinstance(k.value, ast.Name) and k.value.id in NAMES and k.arg in NAMES) or e in REF_FORMS or \
                         (e == "x.strip()" and "re.fullmatch('L\\\\d+', x.strip())" in fsrc)
                    if not ok:
                        bad.append((n.lineno, name, k.arg, e))
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "copy_refusal":
                w = n.args[0] if n.args else None
                seen.append((n.lineno, "copy_refusal", "where", ast.unparse(w) if w else ""))
                if not (isinstance(w, ast.Constant) and w.value in LABELS["where"]):
                    bad.append((n.lineno, "copy_refusal", "where", ast.unparse(w) if w else ""))
            if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "at" for t in n.targets):
                parts = {ast.unparse(x.value) for x in getattr(n.value, "values", []) if isinstance(x, ast.FormattedValue)}
                ok_at = isinstance(n.value, ast.IfExp) and all(
                    isinstance(b, ast.JoinedStr) and {ast.unparse(x.value) for x in b.values
                                                      if isinstance(x, ast.FormattedValue)} <= AT_PARTS
                    for b in (n.value.body, n.value.orelse))
                if not ok_at and not (parts and parts <= AT_PARTS):
                    bad.append((n.lineno, "at", "at", ast.unparse(n.value)))
            if isinstance(n, ast.JoinedStr) and ast.unparse(n).startswith(("f'Refused:", 'f"Refused:')) \
                    and f.name != "copy_refusal":
                bad.append((n.lineno, f.name, "f-string", ast.unparse(n)))
    return bad, seen


if __name__ == "__main__":
    b, s = scan()
    print(f"{len(s)} fill-ins, {len(b)} not allowed")
    for x in b:
        print(x)
    sys.exit(1 if b else 0)
