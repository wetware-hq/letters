"""Without the answer-file setting, every run says RUN-TRACES-NOT-RUN; such a run never passes the safety bar."""
import paths


def pytest_terminal_summary(terminalreporter):
    if paths.truth() is None:
        import letters
        terminalreporter.write_line(letters.RUN_TRACES_NOT_RUN)
