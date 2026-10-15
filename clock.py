"""The one approved clock (same pattern as figures 4112225). Mode is set explicitly by the environment:
LETTERS_CLOCK=test -> the scenario clock (--now, else LETTERS_SCENARIO_CLOCK); LETTERS_CLOCK=real -> the real clock.
Anything else refuses with RF-CLOCK-UNSET: staff-only, never in a letter or a patient line."""
import os
from datetime import date

# Signed (forms_orders_lines_SIGNED.md RF-CLOCK-UNSET, single source); test_clock checks it byte for byte.
RF_CLOCK_UNSET = ("Refused: this computer hasn't been told whether it's running a test or real care, so it can't tell "
                  "the time to use. Nothing was saved.")


class ClockUnset(Exception):
    def __init__(self):
        self.line_id, self.text = "RF-CLOCK-UNSET", RF_CLOCK_UNSET
        super().__init__(self.line_id)


def today(scenario=None):
    mode = os.environ.get("LETTERS_CLOCK")
    if mode == "real":
        return date.today().isoformat()
    if mode == "test":                   # no scenario date given: None, and the grant check fails closed (by hand)
        return scenario or os.environ.get("LETTERS_SCENARIO_CLOCK")
    raise ClockUnset()
