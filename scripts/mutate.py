#!/usr/bin/env python3
"""Simple mutation testing for the 19 safety checks.

Operators, applied one at a time inside the check functions only:
  cmp   flip a comparison (== !=, < >=, > <=, in / not in, is / is not)
  bool  flip and/or, True/False, drop a 'not'
  guard remove a guard: an 'if' whose body is a single return/raise/continue/break gets 'pass' instead
Each mutant runs in its own copy of the repo. Stage 1 runs the rule's tests from RULES.md (fast); a mutant that
survives runs the FULL suite with -x and a timeout. Killed = any test failed or timed out.

  python3 scripts/mutate.py list            # count mutants
  python3 scripts/mutate.py run [-j N] [--only ID,ID] [--timeout S]
"""
import argparse, ast, concurrent.futures as cf, json, os, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "letters.py"
# check number -> functions holding its logic (see RULES.md / docs/SAFETY_CHECKS.md)
TARGETS = {
    1: ["check_safe", "first_out_of_range_line", "valid_date", "date_in_range", "in_event_id"],
    2: ["refuse_combination_brands"],
    3: ["refuse_copies", "refuse_note_copies", "pre_template_checks"],
    4: ["from_template"],
    5: ["read_brief"],
    6: ["cumsum:result"], 7: ["cumsum:result", "result_sensitive_cats"],
    8: ["sensitive_result_lines", "mark_linked_items"],
    9: ["check"],
    10: ["refuse_missing_required"],
    11: ["_build:pending_none"],
    12: ["verify_letter"],
    13: ["reverse_check"],
    14: ["apply_cut_rule", "flag_cuts"],
    15: ["verify_letter:leak"],
    16: ["flag_long_sentences"],
    17: ["_has_question"],
    18: ["finalise"],
    19: ["privacy_check"],
}
CMP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt, ast.Gt: ast.LtE, ast.LtE: ast.Gt,
       ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}


def _regions(tree, src):
    """(check, func name, first line, last line) for every target; ':part' narrows to a marked part."""
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    lines = src.splitlines()
    out = []
    for chk, names in TARGETS.items():
        for spec in names:
            name, _, part = spec.partition(":")
            f = funcs[name]
            lo, hi = f.lineno, f.end_lineno
            if part == "result":                       # the 'elif kind == "result":' branch of cumsum
                lo = next(i for i in range(lo, hi) if 'elif kind == "result":' in lines[i - 1])
                hi = next(i for i in range(lo + 1, hi) if re.match(r"\s+elif kind == ", lines[i - 1])) - 1
            elif part == "pending_none":
                lo = next(i for i in range(lo, hi) if 'EMPTY["pending"] in md' in lines[i - 1]); hi = lo + 2
            elif part == "leak":
                lo = next(i for i in range(lo, hi) if "is_cut(" in lines[i - 1] and "for" not in lines[i - 1])
                hi = lo + 2
            out.append((chk, spec, lo, hi))
    return out


def mutants():
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    regs = _regions(tree, src)
    seen, out = set(), []
    def owner(node):
        return [r for r in regs if r[2] <= node.lineno <= r[3]]
    for node in ast.walk(tree):
        if not hasattr(node, "lineno"):
            continue
        rs = owner(node)
        if not rs:
            continue
        cands = []
        if isinstance(node, ast.Compare):
            for k, op in enumerate(node.ops):
                if type(op) in CMP:
                    cands.append(("cmp", k))
        elif isinstance(node, ast.BoolOp):
            cands.append(("boolop", 0))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            cands.append(("const", 0))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            cands.append(("not", 0))
        elif isinstance(node, ast.If) and len(node.body) == 1 and isinstance(node.body[0], (ast.Return, ast.Raise,
                                                                                             ast.Continue, ast.Break)):
            cands.append(("guard", 0))
        for kind, k in cands:
            key = (node.lineno, node.col_offset, type(node).__name__, kind, k)
            if key in seen:
                continue
            seen.add(key)
            out.append({"id": len(out) + 1, "check": rs[0][0], "func": rs[0][1], "line": node.lineno,
                        "col": node.col_offset, "node": type(node).__name__, "op": kind, "k": k})
    return out


def _off(lines_b, line, col):
    """Byte offset into the whole file for an AST (line, col) (col is a UTF-8 byte offset)."""
    return sum(len(l) for l in lines_b[:line - 1]) + col


def _find(node, m):
    for n in ast.walk(node):
        if getattr(n, "lineno", None) == m["line"] and getattr(n, "col_offset", None) == m["col"] \
                and type(n).__name__ == m["node"]:
            return n
    raise LookupError(m)


OPTXT = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.GtE: ">=", ast.Gt: ">", ast.LtE: "<=",
         ast.In: "in", ast.NotIn: "not in", ast.Is: "is", ast.IsNot: "is not"}


def mutated_source(m):
    """Edit the original text in place (comments and layout kept), so source-reading tests still see the file."""
    src = SRC.read_text(encoding="utf-8")
    b = src.encode("utf-8")
    lines_b = b.splitlines(keepends=True)
    n = _find(ast.parse(src), m)
    def seg(a, z, new):
        return (b[:a] + new.encode() + b[z:]).decode("utf-8")
    if m["op"] == "cmp":
        left = n.left if m["k"] == 0 else n.comparators[m["k"] - 1]
        a = _off(lines_b, left.end_lineno, left.end_col_offset)
        z = _off(lines_b, n.comparators[m["k"]].lineno, n.comparators[m["k"]].col_offset)
        old = b[a:z].decode()
        new_op = OPTXT[CMP[type(n.ops[m["k"]])]]
        new = re.sub(r"(not\s+in|is\s+not|==|!=|<=|>=|<|>|\bin\b|\bis\b)", new_op, old, count=1)
        assert new != old, (m, old)
        return seg(a, z, new)
    if m["op"] == "boolop":
        a = _off(lines_b, n.values[0].end_lineno, n.values[0].end_col_offset)
        z = _off(lines_b, n.values[1].lineno, n.values[1].col_offset)
        old = b[a:z].decode()
        new = re.sub(r"\band\b", "or", old, 1) if isinstance(n.op, ast.And) else re.sub(r"\bor\b", "and", old, 1)
        assert new != old, (m, old)
        return seg(a, z, new)
    if m["op"] == "const":
        a = _off(lines_b, n.lineno, n.col_offset)
        return seg(a, a + len(str(n.value)), str(not n.value))
    if m["op"] == "not":
        a = _off(lines_b, n.lineno, n.col_offset)
        k = re.match(rb"not\s*", b[a:]).end()          # drop only the keyword; any '(' stays
        return seg(a, a + k, "")
    if m["op"] == "guard":
        st = n.body[0]
        a = _off(lines_b, st.lineno, st.col_offset)
        z = _off(lines_b, st.end_lineno, st.end_col_offset)
        return seg(a, z, "pass")
    raise ValueError(m)


def rule_tests():
    rows = {}
    for l in (ROOT / "RULES.md").read_text(encoding="utf-8").splitlines():
        if re.match(r"^\| \d+ \|", l):
            c = [x.strip() for x in l.strip("|").split("|")]
            rows[int(c[0])] = [t.strip() for t in c[-1].split(",") if t.strip()]
    return rows


def _pytest(repo, args, timeout):
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *args], cwd=repo,
                           capture_output=True, text=True, timeout=timeout)
        return "killed" if r.returncode != 0 else "survived"
    except subprocess.TimeoutExpired:
        return "timeout"


def run_one(m, timeout):
    with tempfile.TemporaryDirectory(prefix="mut") as d:
        repo = pathlib.Path(d) / "letters"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        (repo / "letters.py").write_text(mutated_source(m), encoding="utf-8")
        tests = rule_tests().get(m["check"], [])
        stage = _pytest(repo, ["-k", " or ".join(tests)], timeout) if tests else "survived"
        if stage == "survived":
            stage = _pytest(repo, [], timeout)
            m["stage"] = "full"
        else:
            m["stage"] = "rule-tests"
        m["result"] = stage
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "run"])
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--only")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--out", default=str(pathlib.Path(tempfile.gettempdir()) / "mutation_report.json"))
    a = ap.parse_args(argv)
    ms = mutants()
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        ms = [m for m in ms if m["id"] in keep]
    if a.cmd == "list":
        for m in ms:
            print(m)
        print(f"{len(ms)} mutants")
        return 0
    done = []
    with cf.ProcessPoolExecutor(a.j) as ex:
        for m in ex.map(run_one, ms, [a.timeout] * len(ms)):
            done.append(m)
            print(f"#{m['id']} check {m['check']} {m['func']} L{m['line']} {m['op']}: {m['result']} ({m['stage']})",
                  flush=True)
    pathlib.Path(a.out).write_text(json.dumps(done, indent=1))
    killed = sum(m["result"] != "survived" for m in done)
    print(f"kill rate: {killed}/{len(done)} = {100 * killed / max(1, len(done)):.1f}%")
    for m in done:
        if m["result"] == "survived":
            print(f"SURVIVED #{m['id']} check {m['check']} {m['func']} line {m['line']} {m['op']}")
    return 0 if killed == len(done) else 1


if __name__ == "__main__":
    sys.exit(main())
