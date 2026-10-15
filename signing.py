"""Signing files and the line ids they sign. A signing file is a current *_SIGNED.md, *_SAFETY.md or *_WORDING.md
(never a .bak, DRAFT or RETIRED copy). Files sharing a stem (letters_refusals_SAFETY.md + letters_refusals_WORDING.md,
signed together by Physician and Educator) are one signing set."""
import collections, re

FILE = re.compile(r"^(.+)_(SIGNED|SAFETY|WORDING)\.md$")
IDS = (re.compile(r"^\| ([A-Z]{2,4}(?:-[A-Z0-9]+)+) \|", re.M), re.compile(r"^- ([A-Z]{2,4}(?:-[A-Z0-9]+)+)\b", re.M),
       re.compile(r'^([A-Z]{2,4}(?:-[A-Z0-9]+)+): "', re.M))


def duplicates(dirs):
    where = collections.defaultdict(set)
    for d in dirs:
        for f in sorted(d.iterdir()):
            m = FILE.match(f.name)
            if not m or "DRAFT" in f.name or "RETIRED" in f.name:
                continue
            text = f.read_text(encoding="utf-8")
            for rx in IDS:
                for k in rx.findall(text):
                    where[k].add(f"{d.name}/{m.group(1)}")
    return {k: sorted(v) for k, v in where.items() if len(v) > 1}


def sources(dirs):
    """{line id: [signing set, ...]} for every hyphenated line id signed in the signing files."""
    where = collections.defaultdict(set)
    for d in dirs:
        for f in sorted(d.iterdir()):
            m = FILE.match(f.name)
            if not m or "DRAFT" in f.name or "RETIRED" in f.name:
                continue
            for rx in IDS:
                for k in rx.findall(f.read_text(encoding="utf-8")):
                    where[k].add(m.group(1))
    return {k: sorted(v) for k, v in where.items()}


ROW = re.compile(r"^\| ([A-Z]{2,4}(?:-[A-Z0-9]+)+) \| (?:[a-z]+ \| )?([^|]*?) \|$", re.M)


def rows(text):
    """{id: line text} from a signed table, the bytes between the cell separators."""
    return dict(ROW.findall(text))


def pattern(template):
    """A signed line with its [slots] as wildcards, anchored."""
    parts = re.split(r"\[[^\]]+\]", template)
    return re.compile("^" + ".+?".join(re.escape(p) for p in parts) + "$", re.S)
