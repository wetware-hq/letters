"""Standalone template checker: `python -m check_template <notes> [--brief file]`.

Not a second implementation: it calls the same pre-template checks and the same from_template() parser that
letters.py uses for every letter. Exit 0 = pass, 2 = fail (signed template wording with the line number)."""
import sys

from letters import check_template_main

if __name__ == "__main__":
    sys.exit(check_template_main())
