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


def _local():
    local = pathlib.Path(os.environ.get("LETTERS_LOCAL") or HERE / "local.toml")
    return tomllib.loads(local.read_text()) if local.is_file() else {}


def reused_signed():
    """forms_orders_lines_SIGNED.md, the one signed source of lines shared across tools (the setting cards uses):
    REUSED_SIGNED, or reused_signed in local.toml."""
    r = os.environ.get("REUSED_SIGNED") or _local().get("reused_signed")
    return pathlib.Path(r) if r else None


def wording_dirs():
    """The folders holding the signing files (read-only): WORDING_DIRS (os.pathsep-separated) or wording_dirs."""
    r = os.environ.get("WORDING_DIRS")
    return [pathlib.Path(x) for x in (r.split(os.pathsep) if r else _local().get("wording_dirs", [])) if x]
