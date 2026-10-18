"""Structural bar on the README's References section.

A green here means only: the README ends with a `## References` section, that section
is not empty, and every citation entry in it carries a checkable id (DOI, PMID or arXiv).
It does not check that a citation is real or that it supports the claim it is tied to;
that is a human-verified property recorded when the entry was added, not a test.

Convention: a citation entry is a bullet that opens with bold text (`- **`). A plain
bullet (for example the honest `- None.` or an unsupported-claims line) is not a
citation and is not required to carry an id.
"""
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent
README = _HERE / "README.md"
if not README.is_file():
    README = _HERE.parent / "README.md"

ID_RE = re.compile(
    r"(10\.\d{4,}/\S+)"            # DOI
    r"|(PMID:?\s*\d+)"             # PMID
    r"|(arXiv:\s*\d{4}\.\d{4,})",  # arXiv id
    re.IGNORECASE,
)


def _references_section():
    text = README.read_text(encoding="utf-8")
    heads = [m.start() for m in re.finditer(r"(?m)^## .+$", text)]
    assert heads, "README has no `## ` headers"
    last = text[heads[-1]:]
    return text, last


def test_readme_ends_with_references_header():
    _text, last = _references_section()
    assert last.splitlines()[0].strip() == "## References", (
        "the README's last `## ` section must be `## References`, found: "
        + last.splitlines()[0].strip()
    )


def test_references_section_not_empty():
    _text, last = _references_section()
    body = "\n".join(last.splitlines()[1:])
    body = re.sub(r"(?m)^#+ .*$", "", body)  # drop sub-headers
    assert body.strip(), "the References section has no content below its header"


def test_every_citation_entry_has_a_checkable_id():
    _text, last = _references_section()
    missing = []
    for line in last.splitlines():
        if line.startswith("- **"):          # a citation entry by convention
            if not ID_RE.search(line):
                missing.append(line.strip())
    assert not missing, (
        "citation entries without a DOI, PMID or arXiv id: " + "; ".join(missing)
    )
