"""Static listing of every refusal code in letters.py: module constants whose text starts 'Refused:' or 'Blocked:'
(dict values too) and 'Refused:' literals written inside functions. Used by the signed-line test."""
import ast, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _text(node.left), _text(node.right)
        return a + b if a is not None and b is not None else None
    return None


def codes(src=None):
    src = src if src is not None else (ROOT / "letters.py").read_text(encoding="utf-8")
    tree, out = ast.parse(src), {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
            name = n.targets[0].id
            if isinstance(n.value, ast.Dict):
                for k, v in zip(n.value.keys, n.value.values):
                    t = _text(v)
                    if t and t.startswith(("Refused:", "Blocked:")):
                        out[f"{name}[{k.value}]"] = t
            else:
                t = _text(n.value)
                if t and t.startswith(("Refused:", "Blocked:")):
                    out[name] = t
    consts = {n.targets[0].id: _text(n.value) for n in tree.body if isinstance(n, ast.Assign)
              and isinstance(n.targets[0], ast.Name) and _text(n.value)}
    for f in tree.body:
        if isinstance(f, ast.FunctionDef):
            inner = {id(v) for n in ast.walk(f) if isinstance(n, ast.JoinedStr) for v in n.values}
            for n in ast.walk(f):
                if id(n) in inner:
                    continue
                t = _text(n) if isinstance(n, (ast.Constant, ast.JoinedStr)) else None
                if t and t.startswith("Refused:"):
                    out[f"{f.name}: {t}"] = t
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) in ("Refused", "ComboRefused"):
                    for a in ast.walk(n):                        # a module constant used in the refusal text
                        if isinstance(a, ast.Name) and a.id in consts and a.id not in out:
                            out[a.id] = consts[a.id]
    return out
