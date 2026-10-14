"""Without the answer-file setting, every run says RUN-TRACES-NOT-RUN; such a run never passes the safety bar."""
import paths

NEEDS_SIGNED = "needs_signed"


def pytest_configure(config):
    config.addinivalue_line("markers", f"{NEEDS_SIGNED}: runs only with REUSED_SIGNED and WORDING_DIRS set")


def pytest_collection_modifyitems(config, items):
    if paths.reused_signed() is None or not paths.wording_dirs():
        drop = [i for i in items if i.get_closest_marker(NEEDS_SIGNED)]
        items[:] = [i for i in items if not i.get_closest_marker(NEEDS_SIGNED)]
        config.hook.pytest_deselected(items=drop)


def pytest_terminal_summary(terminalreporter):
    if paths.truth() is None:
        import letters
        terminalreporter.write_line(letters.RUN_TRACES_NOT_RUN)
