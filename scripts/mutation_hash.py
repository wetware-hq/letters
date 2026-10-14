"""Mutation hash: SHA-256 over safety_functions.toml and the AST source (ast.unparse) of every function it lists,
so editing a listed function's body changes the hash (comments and blank lines do not). scripts/mutate.py records it
in mutation_hash.txt after a full run that kills every mutant; pre-push recomputes it and fails if it differs.

    python3 scripts/mutation_hash.py check      exit 1 if the recorded hash is stale"""
import ast, hashlib, pathlib, sys, tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORD = "mutation_hash.txt"
STALE = "safety functions changed since the last full mutation run: a fresh full mutation run is needed"


def _listed(cfg):
    return [("letters.py", {f["name"] for f in cfg["functions"]})]


def compute(root=ROOT):
    root = pathlib.Path(root)
    toml = (root / "safety_functions.toml").read_text(encoding="utf-8")
    parts = [toml]
    for path, names in _listed(tomllib.loads(toml)):
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
        found = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names:
                parts.append(f"{path}:{n.name}\n{ast.unparse(n)}")
                found.add(n.name)
        if found != names:
            raise SystemExit(f"listed but not found in {path}: {sorted(names - found)}")
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()


def record(root=ROOT):
    (pathlib.Path(root) / RECORD).write_text(compute(root) + "\n")


def fresh(root=ROOT):
    p = pathlib.Path(root) / RECORD
    return p.is_file() and p.read_text().strip() == compute(root)


def main(argv):
    if argv[1:] != ["check"]:
        print(__doc__, file=sys.stderr)
        return 2
    if not fresh():
        print(STALE)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
