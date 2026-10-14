"""Where the frozen scenario answer files live: a local setting, never a path in the repo. Read from the environment
(LETTERS_TRUTH, or TRUTH_DIR as before) or, failing that, from the untracked local.toml beside this file
(truth = "..."). LETTERS_LOCAL can name another local file. The folder is read-only. Unset: traces are not run."""
import os, pathlib, tomllib

HERE = pathlib.Path(__file__).resolve().parent


def truth():
    r = os.environ.get("LETTERS_TRUTH") or os.environ.get("TRUTH_DIR")
    local = pathlib.Path(os.environ.get("LETTERS_LOCAL") or HERE / "local.toml")
    if not r and local.is_file():
        r = tomllib.loads(local.read_text()).get("truth")
    return pathlib.Path(r) if r else None
