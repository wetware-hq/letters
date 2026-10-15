#!/usr/bin/env python3
"""letters: synthetic raw medical notes + a brief -> a minimal, professional Markdown letter.

Text in, Markdown out. Deterministic, standard library only, no model calls.
Every body sentence carries the ids of the note lines it came from, and a checker
refuses any letter that states something those lines do not support.
"""
import argparse, os, pathlib, re, sys, tomllib
from collections import OrderedDict

HERE = pathlib.Path(__file__).resolve().parent
ND = "not documented"
INCIDENT = ("This looks like real patient information. Don't paste it here; delete it from where "
            "you copied it and report it to your organisation's privacy officer.")
TYPES = ("referral", "discharge", "gp-update", "specialist-reply")
AUDIENCES = ("gp", "specialist", "patient")
NEVER_CUT = ("allergies", "results", "meds", "med_changes", "pending", "followup")
DRAFT_MARK = "**DRAFT, unsigned**"
FOOTER = ["---", "", "Clinician: [name]  ", "Role: [role]  ", "Signature: [unsigned]", "",
          "Draft: check against the record before sending.", ""]
WORD_LIMIT = 400          # target; refused above WORD_LIMIT * 1.1, except discharge letters
SENTENCE_LIMIT = 25       # words per sentence

SYNTH = re.compile(r"\bSYNTH-[A-Za-z0-9]+\b")
# An event-id reference, e.g. SYNTH-EP-1002#7: an episode number and an event number. Never a patient identifier.
EVENT_ID = re.compile(r"(?<![\w#-])SYNTH-EP-\d+#\d+(?![\w#-])")
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DMY = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b")
LONGNUM = re.compile(r"(?<![\w.])\+?\d(?:[\s-]?\d){7,}(?![\w.])")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
# The synthetic-data tripwire: real calendar dates from SYNTH_FIRST to SYNTH_LAST only (approved 13 Oct 2026).
# Every parser matches any year (DATE); only check_safe() enforces this range.
SYNTH_FIRST, SYNTH_LAST = "1930-01-01", "1939-12-31"
SYNTH_RANGE = "1 Jan 1930 to 31 Dec 1939"
DATE = r"\d{4}-\d{2}-\d{2}"


def valid_date(v):
    """True for a real calendar date written YYYY-MM-DD (any year)."""
    import datetime
    try:
        datetime.date.fromisoformat(v)
        return bool(re.fullmatch(DATE, v))
    except ValueError:
        return False


class Refused(Exception):
    pass

# Refusal lines given ids by Educator (proposed ids confirmed); signed: Physician + Educator, 14 Oct 2026.
CHECK_SAFE_MULTI_ID = "Refused: the notes mention more than one patient identifier. Nothing was written. Use notes for one patient only."   # signed round 2, 15 Oct 2026
CHECK_WORD_LIMIT = 'Refused: the letter body is {words_total} words, and the limit is about {WORD_LIMIT}. Shorten the letter, then run again.'
CHECK_SHORTHAND = "Refused: the letter uses shorthand that isn't allowed at {at}. Write it out in full, then run again."
CHECK_DIRECTION_WORD = 'Refused: the results at {at} use a direction word instead of a value. Give values with dates.'
CHECK_UNSOURCED = "Refused: the sentence at {at} doesn't cite a line of the notes. Cite the line it comes from, or remove the sentence."
CHECK_BAD_CITE = "Refused: the sentence at {at} cites a line that isn't in the notes. Correct the line reference, then run again."
CHECK_SENTENCE_LIMIT = 'Refused: the sentence at {at} is over {SENTENCE_LIMIT} words. Split it, then run again.'
CHECK_ABBREV = "Refused: the abbreviation at {at} hasn't been flagged or resolved. Write it out in full, or confirm its meaning with `--resolve`."
CHECK_DOSE_UNIT = "Refused: the dose unit at {at} hasn't been flagged for checking. Check the unit against the notes, then run again."   # signed round 2, 15 Oct 2026
CHECK_DATE = "Refused: the date at {at} isn't in the lines of the notes it cites. Use the date from the notes, or cite the right line."
CHECK_NUMBER = "Refused: the number at {at} isn't in the lines of the notes it cites. Use the number from the notes, or cite the right line."
CHECK_WORD = "Refused: the word at {at} isn't in the lines of the notes it cites. Use the word from the notes, or cite the right line."
KEEP_OUT_ARG = 'Refused: --keep-out takes one line, like L3. Nothing was written.'
NOT_COPY_ARG = 'Refused: --not-copy takes one line, like L3. Nothing was written.'


# The synthetic-date safeguard. figures keeps its own copy of this same logic; the same boundary tests run in both.
SCAN_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
SCAN_DMY = re.compile(r"(?<![\d.])(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})(?![\d.])")
DATE_REFUSED = ("Refused: line L{n} of the notes has a date outside 1 Jan 1930 to 31 Dec 1939. Nothing was written. "
                "If these are real notes, delete them and use synthetic data only.")


def date_in_range(y, mo, d):
    """True only for a real calendar date from SYNTH_FIRST to SYNTH_LAST."""
    v = f"{y:04d}-{mo:02d}-{d:02d}"
    return valid_date(v) and SYNTH_FIRST <= v <= SYNTH_LAST


def first_out_of_range_line(text):
    """1-based line number (blank lines counted) of the first date outside the range; None if all are inside."""
    for n, line in enumerate(text.splitlines(), 1):
        for m in SCAN_ISO.finditer(line):
            if not date_in_range(*(int(x) for x in m.groups())):
                return n
        for m in SCAN_DMY.finditer(line):
            d, mo, y = (int(x) for x in m.groups())
            if not date_in_range(y + 1900 if y < 100 else y, mo, d):
                return n
    return None


# ---------- safety ----------
def check_safe(text):
    ids = set(SYNTH.findall(EVENT_ID.sub(" ", text)))       # event ids may sit beside the one patient id
    if not ids:
        raise Refused(INCIDENT + " (reason: no SYNTH- identifier)")
    if len(ids) > 1:
        raise Refused(CHECK_SAFE_MULTI_ID)
    n = first_out_of_range_line(text)
    if n:
        raise Refused(DATE_REFUSED.format(n=n))
    if LONGNUM.search(ISO.sub(" ", SYNTH.sub(" ", text))):
        raise Refused(INCIDENT + " (reason: phone- or card-number-like digits)")
    return ids.pop()


def fdate(iso):
    y, m, d = iso.split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


# ---------- 1. parse (every line keeps its id) ----------
HEAD = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\b[\s:,-]*(.*)$")


BULLET = re.compile(r"^(?:[*\u2022\u00b7]|-(?=\s))\s*")
HEAD_SEP = re.compile(r"^(?P<h>[A-Za-z][A-Za-z /'-]{0,40}?)\s*(?:\s[-\u2013\u2014]+\s|->|[\u2192\u279d])\s*(?P<b>.*)$")
MED_BLOCK_HEAD = re.compile(r"^(?P<h>[A-Za-z][A-Za-z /'-]{0,40}?)\s*(?::|\s[-\u2013\u2014]+)?\s*$")


def _med_block_head(line):
    """'Current Rx', 'Regular meds -', 'Tablets taken:': a short medicine heading with nothing after it."""
    m = MED_BLOCK_HEAD.match(line)
    return bool(m) and len(m.group("h").split()) <= 4 and bool(MED_HEAD.search(m.group("h"))) and not drugs_in(line)


def parse(text):
    """Return (entries, sources). A line starting with a date opens an entry. Ids are L<line number>.
    Every line stays on its own: nothing is ever joined with the line before or after it. Under a short medicine
    heading with nothing after it, each indented or bulleted line is read as one medicine line."""
    entries, sources, cur = [], {}, {"date": None, "lines": []}
    block = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            block = block and bool(line)
            continue
        m = HEAD.match(line)
        if m:
            if cur["lines"] or cur["date"]:
                entries.append(cur)
            cur = {"date": m.group(1), "lines": []}
            line = m.group(2).strip()
        sources[f"L{n}"] = (raw.strip(), cur["date"])
        indented = raw[:1].isspace() or bool(re.match(r"^[*\u2022\u00b7]", line))
        line = BULLET.sub("", line) if indented else line
        hm = HEAD_SEP.match(line)
        if hm and len(hm.group("h").split()) <= 4 and (ALLERGY_RX.search(hm.group("h")) or MED_HEAD.search(hm.group("h"))) \
                and not drugs_in(hm.group("h")) and hm.group("b"):
            line = f"{hm.group('h').strip()}: {hm.group('b').strip()}"     # 'Intolerances - x: y', 'What he takes -> x'
        if _med_block_head(line):
            block = True
            continue                                       # a heading only: its lines carry the content
        if block and indented and line and not TAG.match(line):
            line = f"Med: {line}"
        elif not indented:
            block = False
        if line:
            cur["lines"].append((f"L{n}", line))
    if cur["lines"] or cur["date"]:
        entries.append(cur)
    return entries, sources


# ---------- filter: stigmatising labels, sensitive details ----------
STIGMA_DROP = [r"\bdrug[- ]seeking\b", r"\bpoor historian\b", r"\bfrequent flyer\b", r"\btime[- ]waster\b"]
NONCOMPLIANT = re.compile(r"\bnon-?complian(?:t|ce)(?:\s+with\s+([^.;,]+))?", re.I)
SENSITIVE = {
    "HIV status": [r"\bHIV\b", r"\bantiretroviral"],
    "PrEP": [r"\bPrEP\b", r"\bpre-exposure prophylaxis"],
    "abuse": [r"\babuse", r"\bassault", r"\bdomestic violence"],
    "psychiatric history": [r"\bdepress", r"\bbipolar", r"\bschizo", r"\bpsychiatr", r"\bpsychos",
                            r"\bself[- ]harm", r"\boverdose", r"\banxiety", r"\bsuicid"],
}
DOMAIN = tomllib.loads((HERE / "data" / "letters_vocab.toml").read_text(encoding="utf-8"))   # domain words are data
RELEVANT = dict(DOMAIN["relevant"])
# H22 items (round 11 signed rulings): detection words come from linked_terms.toml only, whole word or phrase, any case.
H22_ITEMS = ("Mental Health Act status", "sexual health", "pregnancy loss or termination", "genetic results")
_LT = tomllib.loads((HERE / "linked_terms.toml").read_text(encoding="utf-8")).get("items", {})
NUMBERED_TERMS = {t.lower() for v in _LT.values() for t in v.get("numbered", [])}


def term_pattern(t):
    """Whole word or phrase, any case; a 'numbered' term also takes an optional space or hyphen and one digit."""
    tail = r"(?:[ -]?\d)?" if t.lower() in NUMBERED_TERMS else ""
    return rf"(?<![A-Za-z0-9]){re.escape(t)}{tail}(?![A-Za-z0-9])"


for _cat in H22_ITEMS:
    SENSITIVE[_cat] = [term_pattern(t) for t in _LT[_cat]["terms"]]
CLAUSE = re.compile(r"[^,;.]+[,;.]?")


def sensitive_cat(s):
    for cat, pats in SENSITIVE.items():
        if any(re.search(p, s, re.I) for p in pats):
            return cat
    return None


_TEMPLATE = []          # non-empty while a template letter is built: the Date field is the letter's own date
_NOT_COPY = []          # '--not-copy L<n>' lines for one letter
_EMPTY_FIELDS = set()   # template list fields left empty (set per letter by build_from_template)
_INCLUDE = set()     # items the brief asked to include ('include: <item name>'); set by build() for one letter


def relevant(cat, to):
    """An item is kept only when the brief includes it. The default is to cut it for every non-patient reader,
    including the GP. ('to' is kept for the log wording only.)"""
    return cat.lower() in _INCLUDE


PAREN = re.compile(r"\s*\(([^()]*)\)")


def cut_sensitive_parens(body, ids, to, dropped, keep=False):
    """Medicine lines: the medicine stays, but a bracketed reason or marker that names an irrelevant
    sensitive category (e.g. 'antiretroviral (HIV)', '(undetectable VL)') is removed and logged."""
    def sub(m):
        cat = sensitive_cat(m.group(1))
        if cat and not relevant(cat, to) and not keep:
            drug = body[:m.start()].split()[0].lower() if body[:m.start()].split() else "medicine"
            dropped.append(f"{','.join(ids)}: indication for {drug} cut ({cat}, not relevant to {to}): "
                           f"'({m.group(1).strip()})'")
            return ""
        return m.group(0)
    return PAREN.sub(sub, body)


def clean_text(body, ids, to, dropped, med=False, keep=False):
    """Rewrite or cut stigmatising labels and irrelevant sensitive clauses. Log every change."""
    if med:
        body = cut_sensitive_parens(body, ids, to, dropped, keep)
    def rw(m):
        dropped.append(f"{','.join(ids)}: stigmatising label '{m.group(0).split(' with')[0]}' rewritten")
        return f"not taking {m.group(1).strip()} as prescribed" if m.group(1) else "not taking medicines as prescribed"
    body = NONCOMPLIANT.sub(rw, body)
    kept = []
    for cl in CLAUSE.findall(body):
        hit = next((p for p in STIGMA_DROP if re.search(p, cl, re.I)), None)
        if hit:
            dropped.append(f"{','.join(ids)}: stigmatising label removed: '{cl.strip(' ,;.')}'")
            continue
        cat = None if med or keep else sensitive_cat(cl)
        if cat and not relevant(cat, to):
            dropped.append(f"{','.join(ids)}: {cat} cut, not relevant to {to}: '{cl.strip(' ,;.')}'")
            continue
        kept.append(cl)
    out = "".join(kept).strip(" ,;")
    out = re.sub(r"(?<![\w])\?\s*(?=\w)", "possible ", out)          # '?PE' -> 'possible PE'
    return out


# ---------- 2. cumsum: one running state, latest wins ----------
TAG = re.compile(r"^(?P<tag>[A-Za-z /-]+?)\s*:\s*(?P<body>.+)$")
TAGS = {
    "problem": "problem", "dx": "problem", "diagnosis": "problem", "pmhx": "history", "history": "history",
    "resolved": "resolved", "ruled out": "ruledout", "excluded": "ruledout",
    "med": "med", "meds": "med", "medication": "med", "medications": "med", "medicines": "med",
    "current meds": "med", "current medications": "med", "drugs": "med", "start": "med", "restart": "med", "change": "med",
    "stop": "stop", "ceased": "stop", "allergy": "allergy", "allergies": "allergy",
    "result": "result", "lab": "result", "pending": "pending", "plan": "followup",
    "follow-up": "followup", "followup": "followup", "f/u": "followup",
    "question": "question", "q": "question", "admitted": "admission", "admission": "admission",
    "course": "course", "exam": "exam", "o/e": "exam", "discharged": "discharge",
    "patient": "patient", "name": "patient", "id": "patient", "author": "author",
}
RES = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9 -]*?)\s+(?P<val>[<>]?-?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?)\s*(?P<unit>[^\s,;]*)\s*(?P<note>.*)$")
MEDP = re.compile(r"^(?P<drug>.*?)\s*(?:\((?P<k>for|reason:?|indication:?)\s*(?P<v>[^)]*)\))?\s*$", re.I)
HEDGE = re.compile(r"^(?:possible|probable|likely|suspected|query)\s+", re.I)
NEG = re.compile(r"^(?:no|denies|nil)\s+(?P<what>.+)$", re.I)
RULEDOUT = re.compile(r"^(?P<what>.+?)\s+(?:ruled out|excluded)\b", re.I)


# "penicillin - rash", "penicillin (rash)", "penicillin: rash", "penicillin -> rash", "penicillin causes rash"
REACTION = re.compile(r"^\s*(?P<drug>.+?)\s*(?:\((?P<p>[^)]*)\)|\s[-\u2013\u2014]+>?\s*|\s*:\s*|\s*->\s*|\s*[=\u2192]\s*|\s+/\s+|"
                      r"\s+(?:causes|caused|gives|gave|reaction|reaction:)\s+)(?P<r>.*?)\s*$", re.I)
# "echo - cardiology to arrange", "echo (cardiology to arrange)", "echo: renal team to chase", "echo, GP to follow up"
OWNER_SEP = re.compile(r"^\s*(?P<test>.+?)\s*(?:\((?P<p>[^)]*)\)|\s[-\u2013\u2014]+\s*|\s*:\s*|,\s*(?=.*\b(?:to|will)\b))"
                       r"(?P<who>.*?)\s*$")


OWNERISH = re.compile(r"\b(?:to|will|arrang\w*|chas\w*|follow\w*|f/u|team|clinic|GP|by|review\w*|booked|request\w*)\b", re.I)


NO_REACTION = re.compile(r"^\s*(?:(?:reaction|reactions)\s+)?(?:none|nil|no|not|unknown|unclear|undocumented|n/?k)\b"
                         r"[\w\s/]*$", re.I)


def split_reaction(part):
    """Return (drug, reaction or None). A documented reaction is always kept as written."""
    m = REACTION.match(part)
    if m:
        r = (m.group("p") if m.group("p") is not None else m.group("r")).strip()
        if r:
            return m.group("drug").strip(), r
    return part.strip(), None


OWNER_ROLE = re.compile(r"\b(?:" + "|".join(DOMAIN["owners"]["roles"]) + r")\b", re.I)

OWNER_ACTION = re.compile(r"\b(?:to\s+(?:arrange|chase|follow|review|check|action|phone|call|book|see)|will)\b", re.I)
OWNER_MOD = set(DOMAIN["owners"]["modifiers"])
OWNER_SEP_ANY = re.compile(r"\s[-\u2013\u2014]+\s|:|\(|,")


NOT_OWNER = re.compile(r"^(?:await\w*|pending|outstanding|to follow|results? to follow|sent|in progress)\b", re.I)


def split_owner(body):
    """Return (test, owner or None). The owner is a role or team, or anyone given an action ('X to chase')."""
    m = OWNER_SEP.match(body)
    if m:
        who = (m.group("p") if m.group("p") is not None else m.group("who")).strip()
        if who and NOT_OWNER.match(who):
            return m.group("test").strip(), None          # 'awaiting lab' says nobody follows it up
        if who and (OWNER_ROLE.search(who) or OWNER_ACTION.search(who)):
            return m.group("test").strip(), who
    for r in OWNER_ROLE.finditer(body):
        start = r.start()
        before = body[:start].rstrip().split()
        if before and before[-1].lower() in OWNER_MOD:
            start = body.rfind(before[-1], 0, start)
            before = before[:-1]
        test = re.sub(r"(?:\s+(?:by|with|via|for))+$", "", " ".join(before), flags=re.I).strip(" ,;:-")
        if test:
            return test, body[start:].strip(" .")
    return body.strip(), None


PENDING_AFTER = re.compile(r"^(?P<test>.+?)\s+(?:(?:is|are|still|results?|has been|have been)\s+)*"
                           r"(?:pending|awaited|outstanding|requested|ordered|sent|booked|arranged|to follow)\b"
                           r"[\s,;:.-]*(?P<rest>.*?)\s*\.?$", re.I)
PENDING_BEFORE = re.compile(r"^(?:awaiting(?: results? of)?|pending|outstanding|requested|ordered|booked)\s*:?\s+"
                            r"(?P<test>.+?)\s*\.?$", re.I)


def untagged_pending(body):
    """'echo pending, renal fellow to chase' / 'awaiting echo' -> pending body in 'test - rest' form, else None."""
    m = PENDING_BEFORE.match(body)
    if m:
        return m.group("test")
    m = PENDING_AFTER.match(body)
    if m and not NEG.match(body):
        rest = m.group("rest").strip(" ,;-")
        rest = re.sub(r"^(?:on\s+)?\d{4}-\d{2}-\d{2}\b[\s,;:.\-\u2013\u2014]*", "", rest)
        rest = re.sub(r"^(?:await(?:ed|ing)?|pending|outstanding|results? to follow)\b[\s.]*$", "", rest, flags=re.I).strip(" ,;-")
        return f"{m.group('test').strip()} - {rest}" if rest else m.group("test").strip()
    return None


DRUG_FILE = HERE / "drugs.txt"
DOSE_RX = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?(mg|mcg|micrograms?|g|mL|ml|units?|IU)(?![A-Za-z/])", re.I)
FREQ_RX = re.compile(r"(?<![A-Za-z])(daily|bd|tds|qid|nocte|mane|prn|weekly|od)(?![A-Za-z])", re.I)
STOP_RX = re.compile(r"\b(?:stopped|ceased|withheld|stop)\b", re.I)
ALLERGY_RX = re.compile(r"\b(?:allerg\w*|reactions?|intoleran\w*|anaphyla\w*|sensitivit\w*|adverse)\b", re.I)
PER_LITRE = re.compile(r"\d(?:\.\d+)?\s?[A-Za-z\u00b5\u03bc]*/L(?![A-Za-z])")
MED_HEAD = re.compile(r"\b(?:drugs?|med\w*|chart|prescri\w*|tablets?|regimen|rx|takes|taking)\b", re.I)
_DRUGS = {}


def drug_names():
    """{lowercase name: generic} for recognised medicines and brands (drugs.txt + linked_terms.toml)."""
    if not _DRUGS:
        for l in _lines(DRUG_FILE):
            if l.startswith("#"):
                continue
            if "=" in l:
                b, g = (x.strip() for x in l.split("=", 1)); _DRUGS[b.lower()] = g.lower()
            else:
                _DRUGS[l.lower()] = l.lower()
        for v in load_linked().values():
            for m in v.get("medicines", []):
                _DRUGS.setdefault(m.lower(), m.lower())
            for b, g in v.get("brands", {}).items():
                _DRUGS.setdefault(b.lower(), (g or b).lower())
    return _DRUGS


def drug_words():
    """Single words of multi-word medicine names (insulin glargine, glyceryl trinitrate): never flagged."""
    if not _DRUG_WORDS:
        _DRUG_WORDS.update(w for n in drug_names() for w in re.split(r"[\s/]+", n) if len(w) > 3)
    return _DRUG_WORDS


_DRUG_WORDS = set()


def drugs_in(s):
    """Recognised medicine names in s, longest first, as [(as written, generic)]."""
    out, low = [], s.lower()
    for name in sorted(drug_names(), key=len, reverse=True):
        m = re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", low)
        if m and not any(name in o[0].lower() for o in out):
            out.append((s[m.start():m.end()], drug_names()[name]))
    return out


def content_kind(head, body):
    """Kind for a line the heading didn't place: by heading words, then by content. None if unsure."""
    h = (head or "").lower()
    if h and ALLERGY_RX.search(h):
        return "allergy"
    if h and MED_HEAD.search(h):
        return "stop" if STOP_RX.search(body) else "med"
    if h and STOP_RX.search(h):
        return "stop"
    whole = f"{head}: {body}" if head else body
    drugs = drugs_in(whole)
    if ALLERGY_RX.search(whole) and drugs:
        return "allergy"
    if STOP_RX.search(whole) and drugs:
        return "stop"
    if drugs and DOSE_RX.search(whole):
        return "med"            # a drug and a frequency with no dose ('X bloods due weekly') is a sentence, not a medicine
    # Signed rule (13 Oct 2026): a dose in mg, g, mcg, units or mL (no "/L") is a medicine; a per-litre
    # concentration is a result only when the line has no frequency word, otherwise a medicine.
    if DOSE_RX.search(whole):
        return "med"
    if PER_LITRE.search(whole):
        return "med" if FREQ_RX.search(whole) else "result"
    return None


def classify(line):
    """(kind, body) for one notes line: a known heading first, otherwise by heading words and content."""
    m = TAG.match(line)
    kind = tag_kind(m.group("tag")) if m else None
    if kind:
        body = m.group("body").strip()
        if kind == "med" and STOP_RX.search(body) and not re.match(r"^(?:start|restart)$", m.group("tag").strip(), re.I):
            kind = "stop"
        return kind, body
    if m:
        k = content_kind(m.group("tag").strip(), m.group("body").strip())
        if k and (ALLERGY_RX.search(m.group("tag")) or MED_HEAD.search(m.group("tag")) or STOP_RX.search(m.group("tag"))):
            return k, m.group("body").strip()
        if k == "result":
            return k, m.group("body").strip()
        if k:
            return k, line
        return None, line
    return content_kind(None, line), line


DURATION = re.compile(r"^(?:\d|(?:a|an|one|two|three|four|five|six|several|another)\s+(?:day|week|month|year)s?\b|"
                      r"(?:day|week|month|year)s?\b|now\b|life\b|the\s+(?:next|rest)\b)", re.I)
DOSEISH = re.compile(r"\d|\b(?:mg|mcg|g|ml|units?|daily|nocte|mane|bd|tds|qds|od|prn|weekly|tablets?|puffs?|"
                     r"once|twice|morning|night|evening)\b", re.I)


def med_reason(drug):
    """Find a reason written another way on the medicine line: 'X 5 mg daily for gout', 'X - for gout',
    'X 5 mg (gout)', 'X 5 mg to treat gout', 'X 5 mg; indication gout'. Returns (drug text, reason or None)."""
    m = re.match(r"^(?P<d>.*?)\s*[;,-]?\s*\b(?:(?:indicated|used|taken|prescribed|given|started)\s+for|for|to treat|treats?|"
                 r"indication:?|reason:?|because of|as needed for)\s+(?P<r>[^()]+?)\s*$",
                 drug, re.I)
    if m and m.group("d").strip() and not DURATION.match(m.group("r")):
        return m.group("d").strip(" ,;-"), m.group("r").strip(" .")
    m = re.match(r"^(?P<d>.*?)\s+[-\u2013\u2014]\s+(?P<r>[^-\u2013\u2014()]+?)\s*$", drug)
    if m and m.group("d").strip() and not DOSEISH.search(m.group("r")) and not STOP_RX.search(m.group("r")):
        return m.group("d").strip(), m.group("r").strip(" .")
    m = re.match(r"^(?P<d>.*?)\s*\((?P<r>[^()]+)\)\s*(?P<tail>.*)$", drug)
    if m and m.group("d").strip() and not DOSEISH.search(m.group("r")):
        return f"{m.group('d').strip()} {m.group('tail').strip()}".strip(), m.group("r").strip()
    return drug, None


RESULT_WORDS = re.compile(r"\b(?:done|normal|abnormal|results?|showed|shows|reported|back|unremarkable|completed|"
                          r"negative|positive|confirmed|clear|came back)\b", re.I)


def clear_pending(st, body, tagged_result=False):
    """A later line naming a pending test and giving its result (done, normal, result...) clears it."""
    low = body.lower()
    if untagged_pending(body):
        return
    for k in list(st["pending"]):
        if k and re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", low) and (tagged_result or RESULT_WORDS.search(low)):
            st["pending"].pop(k)


MED_LIST_TAGS = {"meds", "medications", "medicines", "current meds", "current medications", "drugs"}


def split_med_lists(lines):
    """'Meds: a 5 mg daily, b 10 mg (for gout); c' -> one medicine per entry, same line id."""
    out = []
    for lid, line in lines:
        m = TAG.match(line)
        kind, body = classify(line)
        converted = False
        if kind in ("med", "allergy", "stop", "result") and not (m and tag_kind(m.group("tag"))):
            converted = True
            line = f"{ {'med': 'Med', 'allergy': 'Allergy', 'stop': 'Stop', 'result': 'Result'}[kind]}: {body}"   # unfamiliar heading or none
            m = TAG.match(line)
        if kind == "med" and m and (m.group("tag").strip().lower() in MED_LIST_TAGS or converted):
            parts = [p.strip() for p in re.split(r"[,;](?![^()]*\))", m.group("body")) if p.strip()]
            if len(parts) > 1 and (not converted or all(DOSE_RX.search(p) or FREQ_RX.search(p) for p in parts)):
                out += [(lid, f"Med: {p}") for p in parts]
                continue
        if kind == "stop" and m and m.group("tag").strip().lower() not in ("stop", "ceased"):
            line = f"Stop: {m.group('body').strip()}"
        out.append((lid, line))
    return out


QUESTION_UNTAGGED = re.compile(r"\?\s*$|\bplease advise\b|\bwould value your (?:opinion|advice|input)\b|"
                               r"\bfor consideration of\b|\byour (?:opinion|advice) (?:on|re|regarding)\b|"
                               r"\bgrateful if you could\b|\badvice re\b|^(?:please\s+)?(?:assess|evaluate)\b(?!ed)|"
                               r"\bplease (?:assess|review|see|evaluate)\b|\breview for\b|\bquery whether\b|\bwe wonder (?:if|whether)\b|"
                               r"\b(?:could|can|would) you\b|\bkindly\b|\bseeking (?:your )?advice\b|\b(?:advice|opinion) please\b|"
                               r"\bfor your (?:opinion|advice|thoughts)\b|\byour thoughts\b|\bwhether (?:she|he|they) (?:needs?|should)\b", re.I)


def tag_kind(tag):
    """Map a note tag to its kind."""
    t = re.sub(r"\s+", " ", tag.strip().lower())
    if re.match(r"^(?:question|q)(?: for .+)?$", t) or t in ("request", "referral question", "reason for referral"):
        return "question"
    return TAGS.get(t)


def _mkey(s):
    """One key per medicine: its recognised generic, else its first word. Two different drugs never share a key,
    so no change is ever inferred between them."""
    found = drugs_in(s)
    if found:
        return found[0][1]
    w = re.findall(r"[A-Za-z][A-Za-z0-9-]*", s)
    return w[0].lower() if w else s.lower()


def _pkey(s):
    return HEDGE.sub("", s.strip()).lower()


STOP_REASON = re.compile(r"^(?P<d>.+?)(?:\s+(?P<r>(?:due to|because of|because|owing to)\s+.+)|"
                         r"\s+[-\u2013\u2014]+\s+(?P<r2>[^\d].*))$", re.I)


FOLLOWUP_UNTAGGED = re.compile(r"^(?:re-?check|review|recall|repeat|follow[- ]?up|f/u|wound check|book|return|see)\b|"
                               r"\b(?:your )?next (?:blood test|test|review|appointment|visit|check)\b|"
                               r"\b(?:will|to) (?:chase|arrange|book|ring|call|follow)\b", re.I)


NO_UNIT_NOTICE = "Line {lid} left out: no unit written."       # signed; screen only; does not block --final
RESULT_UNREAD_NOTICE = "Flag: a result on line {lid} couldn't be read. Check it against the record."


def result_sensitive_cats(body):
    """Sensitive items a results line names (signed detection words: SENSITIVE patterns and linked_terms.toml)."""
    cats = {sensitive_cat(c) for c in CLAUSE.findall(body)} - {None}
    cats |= {c for c, v in load_linked().items() for w in list(v.get("terms", [])) + list(v.get("medicines", []))
             + list(v.get("brands", {})) if _term_re(w).search(body)}
    return cats


def _names_cut_item(d):
    return any(c in d for c in list(SENSITIVE) + list(load_linked()))


def cumsum(entries, to="", dropped=None, keep_indications=False):
    dropped = [] if dropped is None else dropped
    st = {"problems": OrderedDict(), "resolved": OrderedDict(), "ruledout": OrderedDict(), "history": [],
          "meds": OrderedDict(), "stopped": OrderedDict(), "med_changes": [], "allergies": [],
          "results": OrderedDict(), "pending": OrderedDict(), "followup": [], "question": [],
          "admission": [], "course": [], "exam": [], "narrative": [], "results_text": [], "first": None, "last": None,
          "admit": None, "dropped": dropped}
    dated = sorted([e for e in entries if e["date"]], key=lambda e: e["date"])
    for e in [e for e in entries if not e["date"]] + dated:
        d = e["date"]
        if d:
            st["first"] = st["first"] or d
            st["last"] = d
        for lid, line in split_med_lists(e["lines"]):
            ids = [lid]
            m = TAG.match(line)
            kind = tag_kind(m.group("tag")) if m else None
            body = m.group("body").strip() if kind else line
            body = clean_text(body, ids, to, dropped, med=kind in ("med", "stop"), keep=keep_indications)
            if not body:
                continue
            if not kind and QUESTION_UNTAGGED.search(body):
                st["question"].append((body, ids))
                continue
            if not kind:
                pb = untagged_pending(body)
                if pb:
                    kind, body = "pending", pb
            if not kind and FOLLOWUP_UNTAGGED.search(body) and (WHEN.search(body) or WHO.search(body)
                                                                or OWNER_ROLE.search(body)):
                st["followup"].append((body, ids))          # an action and its owner go to Actions
                continue
            if not kind:
                neg = NEG.match(body)
                ro = RULEDOUT.match(body)
                if neg or ro:
                    what = _pkey((neg or ro).group("what").split(",")[0].split(" or ")[0].strip(" ."))
                    for k in list(st["problems"]):
                        if what and (what in k or k in what):
                            b, _, pids = st["problems"].pop(k)
                            (st["ruledout"] if ro else st["resolved"])[k] = (b, d, pids + ids)
                clear_pending(st, body)
                st["narrative"].append((d, body, ids))
                continue
            if kind == "problem":
                k = _pkey(body)
                st["problems"][k] = (body, d, ids)
                st["resolved"].pop(k, None); st["ruledout"].pop(k, None)
            elif kind in ("resolved", "ruledout"):
                k = _pkey(body)
                old = st["problems"].pop(k, None)
                st[kind][k] = (old[0] if old else body, d, (old[2] if old else []) + ids)
            elif kind == "history":
                st["history"].append((body, ids))
            elif kind == "med":
                mm = MEDP.match(body)
                drug, kw, val = mm.group("drug").strip(), (mm.group("k") or "").lower(), mm.group("v")
                ind = val.strip() if val and kw.startswith(("for", "indication")) else None
                reason = val.strip() if val and kw.startswith("reason") else None
                if not ind and not reason:
                    drug, ind = med_reason(drug)
                if ind:
                    cat = sensitive_cat(ind)
                    if cat and not relevant(cat, to) and not keep_indications:
                        dropped.append(f"{lid}: indication for {_mkey(drug)} cut ({cat}, not relevant to {to})")
                        ind = None
                k = _mkey(drug)
                old = st["meds"].get(k)
                change = None
                if k in st["stopped"]:
                    change = ("restarted", f"{drug}")
                    st["stopped"].pop(k)
                elif old and old["drug"] != drug:
                    change = ("changed", f"from {old['drug']}. Now: {drug}")
                elif not old and st["admit"] and d and d > st["admit"]:
                    change = ("started", drug)
                if old and not ind:
                    ind = old["ind"]
                if change and not reason and any(lid in d_.split(":")[0].split(",") and "cut" in d_ for d_ in dropped):
                    reason = "reason not included"           # a stated reason was cut: dropped later with no marker (H22)
                if change:
                    st["med_changes"].append({"what": change, "reason": reason, "date": d,
                                              "ids": (old["ids"] if old else []) + ids})
                cut = any(lid in d_.split(":")[0].split(",") and "indication" in d_ for d_ in dropped)
                st["meds"][k] = {"drug": drug, "ind": ind, "date": d, "ids": (old["ids"] if old else []) + ids,
                                 "cut": cut and not ind}
            elif kind == "stop":
                if not any(_TEMPLATE) and re.search(r"(?<!\()\breason\s*:", line, re.I) and not re.search(r"\(\s*reason\s*:", line, re.I):
                    raise Refused(STOP_REASON_REFUSED.format(lid=lid))      # unbracketed: never partly parsed
                mm = MEDP.match(body)
                drug, reason = mm.group("drug").strip(), mm.group("v") if (mm.group("k") or "").lower().startswith("reason") else None
                if not reason:                                 # a stated reason is kept as written
                    sr = STOP_REASON.match(drug)
                    if sr:
                        drug, reason = sr.group("d").strip(" ,;-"), (sr.group("r") or sr.group("r2")).strip(" .")
                if not reason and any(lid in d_.split(":")[0].split(",") and "cut" in d_ for d_ in dropped):
                    reason = "reason not included"               # a stated reason was cut on purpose
                sd = ISO.search(drug)
                sdate = sd.group(0) if sd else (None if any(_TEMPLATE) else d)   # own date first; never the letter's date
                drug = re.sub(r"\s+", " ", ISO.sub(" ", drug)).strip(" ,;-") if sd else drug
                k = _mkey(drug)
                old = st["meds"].pop(k, None)
                st["stopped"][k] = d
                st["med_changes"].append({"what": ("stopped", old["drug"] if old else drug), "reason": reason,
                                          "date": sdate, "ids": (old["ids"] if old else []) + ids})
            elif kind == "allergy":
                st["allergies"].append((body, ids))
            elif kind == "result":
                raw = body                               # word for word, before any date handling
                clear_pending(st, body, tagged_result=True)
                rd = ISO.search(body)
                if rd:                                   # the result's own date, never the entry's
                    body0, body = body, re.sub(r"\s+", " ", ISO.sub(" ", body)).strip(" :,;")
                    body = re.sub(r"\s+(?:was|is|were|of)\s+(?=[<>]?-?\d)", " ", body).replace(" :", "")
                r = RES.match(body)
                if not r or not r.group("unit") or re.match(r"^[\d.]", r.group("unit")):
                    if PER_LITRE.search(body):
                        st["narrative"].append((d, body0 if rd else body, ids))      # a result written in a sentence stays as written
                        continue
                    if r:                                # a number but no unit: signed behaviour (as at 92d6341)
                        dropped.append(f"{lid}: result without a unit dropped")
                        continue
                    # Round 12 ruling (severity 5): a line with no value is never dropped silently. Sensitive first.
                    if any(d.split(":")[0].split(",").count(lid) and _names_cut_item(d) for d in dropped):
                        continue                         # already cut as sensitive (flag on screen, blocks --final)
                    cats = result_sensitive_cats(raw)
                    if cats and not keep_indications and any(c.lower() not in _INCLUDE for c in cats):
                        dropped.append(f"{lid}: {sorted(cats)[0]} result, not relevant to {to}")
                        continue                         # clinician letter: cut, sensitive flag on screen only
                    unread = not re.search(r"[A-Za-z]{2,}", ISO.sub(" ", raw))
                    st["results_text"].append((raw, ids, unread))   # kept word for word and cited
                    continue
                name = r.group("name").strip()
                k = name.lower()
                st["pending"].pop(k, None)
                h = st["results"].setdefault(k, {"name": name, "values": []})
                h["values"].append((rd.group(0) if rd else (None if any(_TEMPLATE) else d), r.group("val"), r.group("unit"), r.group("note").strip(), lid))
            elif kind == "pending":
                sm = re.search(r";\s*sent:\s*(\d{4}-\d{2}-\d{2})", body)
                sent = sm.group(1) if sm else None
                body = re.sub(r";\s*sent:\s*\d{4}-\d{2}-\d{2}", "", body)
                parts = re.split(r";\s*(?:follow-up|who|f/u)\s*:\s*", body, flags=re.I)
                test, who = (parts[0].strip(), parts[1].strip()) if len(parts) > 1 else split_owner(parts[0])
                k = test.lower()
                if k not in st["results"]:
                    st["pending"][k] = (test, who, sent, ids)   # only a sent date written on the line itself
            elif kind == "admission":
                st["admission"].append((body, ids))
                st["admit"] = st["admit"] or d
            elif kind in ("followup", "question", "course", "exam"):
                st[kind].append((body, ids))
    return st


# ---------- 3. group into sections ----------
SECTIONS = {
    "referral": ["question", "problems", "background", "results", "med_changes", "meds", "allergies", "pending", "followup"],
    "discharge": ["question", "admission", "course", "problems", "med_changes", "meds", "allergies", "results", "pending", "followup"],
    "gp-update": ["question", "problems", "background", "results", "med_changes", "meds", "allergies", "pending", "followup"],
    "specialist-reply": ["question", "exam", "problems", "results", "med_changes", "meds", "allergies", "pending", "followup"],
}
HEADINGS = {
    "specialist": {"question": "Question", "background": "Background", "problems": "Problems",
                   "results": "Results", "pending": "Pending results", "meds": "Current medicines",
                   "allergies": "Allergies", "admission": "Reason for admission", "course": "Hospital course",
                   "med_changes": "Medicine changes", "followup": "Actions", "exam": "Assessment"},
    "gp": {"question": "What we are asking", "background": "Background", "problems": "Problems",
           "results": "Results", "pending": "Results still to come", "meds": "Current medicines",
           "allergies": "Allergies", "admission": "Why they were admitted", "course": "What happened in hospital",
           "med_changes": "Medicine changes", "followup": "Actions", "exam": "Findings"},
    "patient": {"question": "What we want to find out", "background": "Your health so far",
                "problems": "Health problems", "results": "Your test results",
                "pending": "Tests we are still waiting on", "meds": "Your medicines now", "allergies": "Allergies",
                "admission": "Why you came to hospital", "course": "What happened while you were here",
                "med_changes": "Changes to your medicines", "followup": "What happens next",
                "exam": "What we found"},
}
WHO = re.compile(r"\b(" + "|".join(DOMAIN["actions"]["who"]) + r")\b", re.I)

WHEN = re.compile(r"\b(?:in|within|after)\s+\d+\s+(?:day|week|month|year)s?\b|\b\d{4}-\d{2}-\d{2}\b|"
                  r"\b(?:today|tomorrow|at discharge|next visit|at next review)\b", re.I)


def tag(ids):
    seen = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    return " [" + ", ".join(seen) + "]" if seen else ""


def dt(d):
    return f" on {fdate(d)}" if d else ""


ALLERGY_NO_REACTION_PATIENT = "the reaction wasn't written in your notes. Tell your next doctor what happened."
REASON_FLAG = "[flag: reason, plain words not confirmed]"
REASON_ND = "reason not documented"


def _patient_reason(ind):
    """Patient letters: the reason as written (ordinary English kept, no flag); flagged only for a shorthand.txt
    term with no signed plain words."""
    if not ind:
        return REASON_ND
    single = load_single_meaning()
    toks = tokens(ind)
    if any(no_plain_words(t) for t in toks):              # round 11: only shorthand with no signed plain words
        return f"for {ind} {REASON_FLAG}"
    return f"for {ind}"


def section_items(name, st, audience="specialist"):
    """Return (items, numbered, table) where each item already ends with its source ids."""
    if name == "question":
        return [b + tag(i) for b, i in st["question"]]
    if name in ("admission", "course", "exam"):
        items = [b + tag(i) for b, i in st[name]]
        if name == "course":
            items += [b + tag(i) for _, b, i in st["narrative"]]
        return items
    if name == "background":
        return [b + tag(i) for b, i in st["history"]] + [
            (f"{fdate(d)}: " if d else "") + b + tag(i) for d, b, i in st["narrative"]]
    if name == "problems":
        out = [b + tag(i) for b, _, i in st["problems"].values()]
        out += [f"{b}: resolved{dt(d)}" + tag(i) for b, d, i in st["resolved"].values()]
        out += [f"{b}: ruled out{dt(d)}" + tag(i) for b, d, i in st["ruledout"].values()]
        return out
    if name == "results":
        rows = []
        for r in st["results"].values():
            vals = ", ".join(f"{v} {u}{dt(d)}" + (f" ({n})" if n else "") for d, v, u, n, _ in r["values"])
            rows.append((r["name"], vals, [x[4] for x in r["values"]]))
        return rows + [(None, t, i) for t, i, _ in st.get("results_text", [])]
    if name == "meds" and audience == "patient":
        return [f"{m['drug']}, {_patient_reason(m['ind'])}" + tag(m["ids"]) for m in st["meds"].values()]  # patient: nothing cut
    if name == "meds":
        return [m["drug"] + (f" (for {m['ind']})" if m["ind"] else "")   # H22 + matching-line rule: no reason, no marker
                + tag(m["ids"]) for m in st["meds"].values()]
    if name == "med_changes":
        out = []
        for c in st["med_changes"]:
            verb, what = c["what"]
            out.append(f"{verb.capitalize()}{dt(c['date'])}: {what}. Reason for change: {c['reason'] or ND}." + tag(c["ids"]))
        return out
    if name == "allergies":
        out = []
        for b, i in st["allergies"]:
            if re.fullmatch(r"(nil( known)?( allergies)?|nkda|none( known)?|no known( drug)? allergies)\.?", b.strip(), re.I):
                out.append("No known drug allergies" + tag(i))
                continue
            no_react = ALLERGY_NO_REACTION_PATIENT if audience == "patient" else f"reaction {ND}"
            for part in re.split(r"[,;](?![^()]*\))", b):
                drug, react = split_reaction(part)
                if react and NO_REACTION.match(react):
                    out.append(f"{drug}: {no_react}" + tag(i))      # signed: never dropped, allergen as written
                elif react:
                    out.append(f"{drug}: {react}" + tag(i))
                elif len(part.split()) <= 1:              # nothing follows the drug name
                    out.append(f"{part.strip()}: {no_react}" + tag(i))
                else:                                      # trailing words not confidently parsed: keep, flag
                    out.append(f"{part.strip()}: {no_react} {ALLERGY_FLAG}" + tag(i))   # as written, both
        return out
    if name == "pending":
        out = []
        for b, who, d, i in st["pending"].values():
            if not who and (OWNERISH.search(b) or OWNER_SEP_ANY.search(b)):   # wording not parsed: keep, flag
                out.append(f"{b} {OWNER_FLAG}, sent{dt(d) or ' on an undocumented date'}." + tag(i))
                continue
            out.append(f"{b}, sent{dt(d) or ' on an undocumented date'}. Who follows up: {who or ND}." + tag(i))
        return out
    if name == "followup":
        out = []
        for b, i in st["followup"]:
            if " || " in b:                                   # template: '- action, owner'
                a, w = b.split(" || ", 1)
                t = WHEN.search(a)
                out.append(f"Who: {w or ND}. When: {t.group(0) if t else ND}. Action: {a}" + tag(i))
                continue
            dm = re.match(r"^(?P<a>.+?)\s[-\u2013\u2014]+\s(?P<w>.+)$", b)
            w = dm.group("w").strip(" .") if dm and (WHO.search(dm.group("w")) or OWNER_ROLE.search(dm.group("w"))) else None
            if not w:
                w = WHO.search(b); w = w.group(0) if w else None
            t = WHEN.search(b)
            out.append(f"Who: {w or ND}. When: {t.group(0) if t else ND}. Action: {b}" + tag(i))
        return out
    return []


EMPTY = {"pending": "No results pending at time of writing.", "allergies": f"Allergies {ND}.",
         "followup": f"Follow-up actions {ND}.", "med_changes": "No medicine changes documented.",
         "meds": f"Current medicines {ND}."}


# Signed letter status lines (letters_status_lines_SIGNED.md, Physician 15 Oct 2026), byte-exact.
DECLINED_LINES = {
    "LT-DECLINED-CLIN": "Not answered, the patient chose not to say: [question].",
    "LT-DECLINED-CLIN-SAFETY": "Safety alert: not answered, the patient chose not to say: [question].",
    "LT-DECLINED-PT": "You chose not to answer: [question plain wording].",
    "LT-DECLINED-PT-SAFETY": "You chose not to answer: [question plain wording]. Your care team may ask you about this "
                             "again.",
}
_DECLINED = {}          # section -> declined items in it (set per letter by build)
DECLINED_KEYS = ("item", "section", "safety", "sensitive", "question", "question_plain", "lines")
# Signed (letters_refusals_WORDING.md, 15 Oct 2026). Clinician only. Shows the line id only, never the question or topic.
SENSITIVE_DECLINED_REFUSED = "Refused: line {lid} comes from a sensitive question the patient chose not to answer. It can't go in this letter. Nothing was written."
# Signed (letters_refusals_WORDING.md id DECLINED-NO-ABSENCE-LINE, 15 Oct 2026). Clinician only.
DECLINED_NO_ABSENCE_LINE = "Refused: line {lid} says something is absent, but the patient chose not to answer that question. Not answering is never a no. Nothing was written."
ABSENCE = re.compile(r"\b(no|none|nil|not (?:documented|recorded|written|known)|nkda|nka|field empty)\b", re.I)
SECTION_WORD = {"allergies": "allerg", "meds": "medicine", "problems": "problem", "results": "result",
                "pending": "pending", "followup": "follow"}


def load_declined(text):
    """The declined questions forms hands over (TOML [[declined]]: item, section, safety, question, question_plain).
    Fails closed: ValueError on anything missing, unknown or of the wrong type; nothing is guessed."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"declined file: {e}")
    rows = raw.get("declined")
    if set(raw) != {"declined"} or not isinstance(rows, list) or not rows:
        raise ValueError("declined file: needs one or more [[declined]] tables and nothing else")
    known = {x for v in SECTIONS.values() for x in v}
    for d in rows:
        if not isinstance(d, dict) or set(d) != set(DECLINED_KEYS) or not re.fullmatch(r"[a-z][a-z0-9_]*", str(d["item"])) \
                or d["section"] not in known or not isinstance(d["safety"], bool) or not isinstance(d["sensitive"], bool) \
                or not isinstance(d["lines"], list) or not all(isinstance(x, str) and re.fullmatch(r"L\d+", x) for x in d["lines"]) \
                or not all(isinstance(d[k], str) and 0 < len(d[k]) <= 200 and re.fullmatch(r"[^\[\]\n{}]+", d[k])
                           for k in ("question", "question_plain")):
            raise ValueError("declined file: a [[declined]] table is missing a field or has a wrong value")
    return rows


def _decline_lines(name, audience):
    out = []
    for d in _DECLINED.get(name, []):
        if audience == "patient":
            k = "LT-DECLINED-PT-SAFETY" if d["safety"] else "LT-DECLINED-PT"
            out.append("- " + DECLINED_LINES[k].replace("[question plain wording]", d["question_plain"]))
        else:
            k = "LT-DECLINED-CLIN-SAFETY" if d["safety"] else "LT-DECLINED-CLIN"
            out.append("- " + DECLINED_LINES[k].replace("[question]", d["question"]))
    return out


def declined_absence_lines(md, declined):
    """Lines that state a declined item's section is absent, none or not recorded: in its own section, or any line
    naming that section. Empty means the letter is safe to write."""
    secs = {d["section"] for d in declined or ()}
    if not secs:
        return []
    heads = {HEADINGS[a][s]: s for a in HEADINGS for s in secs if s in HEADINGS[a]}
    bad, cur = [], None
    for line in md.splitlines():
        if line.startswith("## "):
            title = line[3:].split(" [flag:")[0].strip()
            cur = heads.get(title)
            if cur and "[flag: field empty]" in line:
                bad.append(line)
            continue
        if line.startswith("---"):
            cur = None
        if any(line.startswith("- " + v.split("[")[0]) for v in DECLINED_LINES.values()):
            continue
        named = any(SECTION_WORD.get(s, s) in line.lower() for s in secs)
        if (cur or named) and ABSENCE.search(line):
            bad.append(line)
    return bad


def render_section(name, st, audience):
    head = HEADINGS[audience][name]
    items = section_items(name, st, audience)
    if name in _DECLINED:                       # declined: never an absence line, only the signed decline line
        items = [x for x in items if not (isinstance(x, str) and ABSENCE.search(x))]
    body = []
    if name == "results":
        plain = [x for x in items if x[0] is None]           # results with no value: word for word, cited
        items = [x for x in items if x[0] is not None]
    if name == "results" and len(items) >= 3:
        body = ["| Test | Results |", "|---|---|"] + [f"| {n} | {v}{tag(i)} |" for n, v, i in items]
    elif name == "results":
        body = [f"- {n}: {v}" + tag(i) for n, v, i in items]
    if name == "results":
        body += [f"- {v}" + tag(i) for _, v, i in plain]
    elif name == "followup":
        body = [f"{k}. {x}" for k, x in enumerate(items, 1)]
    else:
        body = [f"- {x}" for x in items]
    if name in _DECLINED:
        body = body + _decline_lines(name, audience)
    if not body:
        if name in _EMPTY_FIELDS:
            body = [f"- {EMPTY_PATIENT.get(name, NONE_RECORDED) if audience == 'patient' else NONE_RECORDED}"]
        elif name in EMPTY:
            body = [f"- {EMPTY[name]}"]
        else:
            return None
    return "\n".join([f"## {head}", ""] + body + [""])


# ---------- 4. wording: abbreviations are flagged, never guessed; plain language ----------
# An abbreviation means different things in different specialties (MS, PE, CP, RA...). Default: keep it as
# written and flag it. Expand only from the reviewed single-meaning list (ships empty) or a clinician --resolve.
FLAG = "[flag: abbreviation, meaning not confirmed]"
DRUG_FLAG = "[flag: drug name, check spelling]"      # non-wordlist lowercase word on a medicine line
FLAG_ANY = re.compile(r" \[flag: [^\]]+\]")
# Fail-safe: a token is flagged unless it is clearly an ordinary word, a unit, or an exact reviewed entry.
TOKEN = re.compile(r"(?<![A-Za-z0-9.'])(?:c/o|o/e|(?:[A-Za-z]\.){2,}|"
                   r"(?:mmol|micromol|mcg|mg|g|kg|mL|mU|IU|U|units?)/(?:L|dL|min|kg|day|h)\b|"
                   r"[A-Za-z][A-Za-z0-9]*(?:'[a-z]+)?)")
UNITS = {"mg", "mcg", "g", "kg", "mL", "L", "dL", "mmol", "micromol", "min", "mU", "h", "units", "unit"}
UNITS_FILE = HERE / "locale" / "core" / "units.toml"


def load_units(path=None):
    """Signed units (round 13): {unit: patient wording ('' = no brackets)} and the always-flagged set."""
    p = pathlib.Path(path) if path else UNITS_FILE
    if not p.exists():
        return {}, frozenset()
    d = tomllib.loads(p.read_text(encoding="utf-8"))
    return (dict(d.get("units", {})), frozenset(d.get("always_flagged", {}).get("units", [])),
            frozenset(d.get("patient_wording_unsigned", {}).get("units", [])))


UNIT_WORDING, UNIT_ALWAYS, UNIT_PATIENT_UNSIGNED = load_units()
_UNIT_ALT = "|".join(re.escape(u).replace("\u00b5", "[\u00b5\u03bc]")
                     for u in sorted(UNIT_WORDING, key=len, reverse=True))
UNIT_AFTER_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(\s?)(" + _UNIT_ALT + r")(?![\w/^\u00b2])")


def unit_after_number(m):
    """A TOKEN match that is a signed unit straight after a number (optional space): never a shorthand flag."""
    tok = m.group(0)
    if tok not in UNIT_WORDING or tok in UNIT_ALWAYS:
        return False
    return re.search(r"\d\s?$", m.string[:m.start()]) is not None


def patient_units(text):
    """Patient letters: UNIT (wording) on every use, straight after a number; no second bracket; '' = no brackets."""
    def sub(m):
        w = UNIT_WORDING.get(mu_norm(m.group(3)), "")
        if not w or re.match(r"\s*\(", text[m.end():]):
            return m.group(0)
        return f"{m.group(0)} ({w})"
    return UNIT_AFTER_NUMBER.sub(sub, text)
ROMAN_CTX = {"stage", "grade", "class", "type", "types", "phase", "nyha"}
# u/U/iu/IU as a dose unit is a known misreading risk (10u read as 100): always flagged, attached or detached.
# cc as a dose unit ("5 cc saline") follows the same rule (Physician-requested); it is never a copy request.
# Lab units such as U/L, mU/L and IU/L stay clean; "10 units" is never flagged.
DOSE_U = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)( ?)([uU]|[iI][uU]|[cC][cC]|[\u00b5\u03bc]g)(?![A-Za-z0-9/])")
# round 13 item 2: micro sign U+00B5 and Greek mu U+03BC are the same; µg straight after a number is a dose: flagged
MU = "\u00b5\u03bc"


def mu_norm(s):
    return s.replace("\u03bc", "\u00b5")
TOOL_WORDS = {"DRAFT"}        # the tool's own fixed wording ("DRAFT, unsigned") is never flagged
SINGLE_MEANING_FILE = HERE / "single_meaning.toml"
SHORTHAND_FILE = HERE / "shorthand.txt"
WORDLISTS = [HERE / "wordlists" / "english.txt", HERE / "wordlists" / "medical.txt"]
# Plain-language swaps for patients: ordinary words only, never abbreviations.
PLAIN = [(r"\boral\b", "tablets"), (r"\bhypertension\b", "high blood pressure"), (r"\bexertional\b", "when active"),
         (r"(?<!twice )(?<!three times )(?<!four times )(?<!once )\bdaily\b", "once a day"),
         # signed frequency plain words (13 Oct 2026); od has none
         (r"(?i)\bnocte\b", "at night"), (r"(?i)\bmane\b", "in the morning"), (r"(?i)\bprn\b", "when needed"),
         # signed: a maximum over 24 hours; number and unit kept exactly; any other period stays as written
         (r"\bmax(?:imum)?\.?\s+(?:dose\s+)?(?:of\s+)?(\d+(?:\.\d+)?\s?(?:mg|mcg|g|ml|mL|tablets?|doses?))"
          r"\s*(?:in|per|/|every)\s*24\s?(?:h|hrs?|hours?)\b", r"no more than \1 in 24 hours")]


def _lines(p):
    return {l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}


def load_single_meaning(path=None):
    p = pathlib.Path(path) if path else SINGLE_MEANING_FILE
    if not p.exists():
        return {}
    return dict(tomllib.loads(p.read_text(encoding="utf-8")).get("abbreviations", {}))


SHORTHAND = {w.lower() for w in _lines(SHORTHAND_FILE)}
LOCALE = "au"
ALLOWED_SHORTHAND_FILES = [HERE / "locale" / "core" / "allowed_shorthand.toml",
                           HERE / "locale" / LOCALE / "allowed_shorthand.toml"]


def _detection_words():
    """Every sensitive-items word (terms, flag-only terms, medicines, brands), lower case, from linked_terms.toml."""
    out = set()
    p = HERE / "linked_terms.toml"
    if p.exists():
        for item in tomllib.loads(p.read_text(encoding="utf-8")).get("items", {}).values():
            for k in ("terms", "flag_terms", "medicines"):
                out.update(t.lower() for t in item.get(k, []))
            out.update(b.lower() for b in item.get("brands", {}))
    return out


def load_allowed_shorthand(paths=None):
    """Core list plus the locale pack of ordinary clinical words never flagged as shorthand (exact case). Any entry that is listed
    shorthand (more than one meaning, e.g. top/CTO) or a sensitive-items detection word is never allowed."""
    words = set()
    for p in map(pathlib.Path, paths or ALLOWED_SHORTHAND_FILES):
        if p.exists():
            words |= {w for v in tomllib.loads(p.read_text(encoding="utf-8")).get("allowed", {}).values() for w in v}
    banned = SHORTHAND | _detection_words()
    return frozenset(w for w in words if w.lower() not in banned)


ALLOWED_SHORTHAND = load_allowed_shorthand()
TEST_NAMES_FILE = HERE / "locale" / "core" / "test_names.toml"


def load_test_names(path=None):
    """Signed test names (round 13 item 3): {name: patient wording}. Shorthand.txt and detection words never."""
    p = pathlib.Path(path) if path else TEST_NAMES_FILE
    if not p.exists():
        return {}
    d = tomllib.loads(p.read_text(encoding="utf-8")).get("test_names", {})
    banned = SHORTHAND | _detection_words()
    return {k: v for k, v in d.items() if k.lower() not in banned}


TEST_NAMES = load_test_names()
PLAIN_WORDS_FILES = [HERE / "locale" / "core" / "plain_words.toml", HERE / "locale" / LOCALE / "plain_words.toml"]


def load_plain_words(paths=None):
    """Signed patient-letter plain words, for allowed words only (never shorthand.txt or detection words)."""
    out = {}
    for p in map(pathlib.Path, paths or PLAIN_WORDS_FILES):
        if p.exists():
            out.update(tomllib.loads(p.read_text(encoding="utf-8")).get("plain_words", {}))
    return {k: v for k, v in out.items() if k in ALLOWED_SHORTHAND}


PLAIN_WORDS = load_plain_words()


def patient_wording():
    """Signed patient wording: plain words plus test names (round 13). Read at call time."""
    return {**PLAIN_WORDS, **TEST_NAMES}
_PATIENT_LETTER = []    # [True] while a patient letter is built: allowed words don't clear the flag there (ruling)


def allowed_word(tok):
    """Allowed words clear the shorthand flag in clinician letters only. In patient letters they stay flagged unless
    a signed plain-words entry exists (single_meaning.toml, handled before this check)."""
    return (tok in ALLOWED_SHORTHAND or tok in TEST_NAMES) and (not any(_PATIENT_LETTER) or tok in patient_wording())
WORDS = set().union(*(_lines(p) for p in WORDLISTS)) | {m.lower() for m in MONTHS}


def tokens(s):
    return [m.group(0) for m in TOKEN.finditer(s)]


SIGNED_FREQ_PLAIN = {"nocte", "mane", "prn", "daily"}       # signed frequency plain words (13 Oct 2026); od has none


def no_plain_words(tok):
    """True for a shorthand.txt term with no signed plain words (an exact single_meaning.toml entry or a signed
    frequency wording). Only these get the 'no plain words' reason flag."""
    low = tok.lower()
    if low not in SHORTHAND:
        return False
    return not (tok in load_single_meaning() or low in SIGNED_FREQ_PLAIN)


REASON_AT = re.compile(r"(?:\(|, )for (?!\d)")


def reason_start(text):
    """Index where a medicine line's reason starts ('(for ' or ', for '), or None."""
    m = REASON_AT.search(text)
    return m.end() if m else None


def reason_flag(tok, prev="", exact=()):
    """Inside a medicine reason (ruling 13 Oct 2026): flag any shorthand.txt term whatever its case (unless it has
    signed plain words), plus all-capital words that aren't recognised. Never flag any other lowercase word."""
    if tok in exact or tok in TOOL_WORDS or tok.lower() in drug_names() or tok.lower() in drug_words():
        return False
    if tok.lower() in SHORTHAND:
        return needs_flag(tok, prev, exact)
    return len(tok) >= 2 and tok.isupper() and needs_flag(tok, prev, exact)


def needs_flag(tok, prev="", exact=()):
    """True if tok must be flagged. exact: abbreviations that may expand as written (reviewed list, resolutions)."""
    if tok in exact or tok in TOOL_WORDS or allowed_word(tok):     # allowed: exact case, clinician letters only
        return False
    low = tok.lower()
    if low in drug_names() or low in drug_words():
        return False                                        # recognised medicine or brand (or part of one)
    if tok in UNIT_ALWAYS:
        return True                                         # U, IU, cc, ug, bare mU: always flagged (round 13)
    if "/" in tok and tok not in ("c/o", "o/e"):
        return False                                        # slash unit, e.g. mmol/L
    if tok in UNITS or tok in UNIT_WORDING:
        return False                                        # a bare unit is not flagged (round 13 ruling 3)
    if low in ("max", "maximum"):
        return False                                        # dose-limit word: kept as written ('max X per week')
    if re.fullmatch(r"[IVX]+", tok) and prev.lower() in ROMAN_CTX:
        return False                                        # stage IV
    if low in SHORTHAND and not no_plain_words(tok) and "." not in tok:
        return False                                        # signed plain words exist (nocte, mane, prn): no flag
    if low in SHORTHAND or "." in tok or "/" in tok:
        return True
    if any(low == e.lower() for e in exact):
        return True                                         # case variant of a reviewed entry (Ecg, BD)
    if len(tok) >= 2 and (tok.isupper() or any(c.isupper() for c in tok[1:])):
        return True                                         # PEG, MAP, NSTEMIx, eGFR-like
    if len(tok) == 1:
        return False
    return low not in WORDS     # nbm, cx (and Nbm at sentence start); drug names too (acceptable)


def in_event_id(m):
    """True when the token match m sits inside an event-id reference (SYNTH-EP-1002#7): never an abbreviation."""
    return any(e.start() <= m.start() and m.end() <= e.end() for e in EVENT_ID.finditer(m.string))


def context_ok(m):
    """Signed context exemptions (ruling 14 Oct 2026) for a TOKEN match m: 'Ms' straight before a capitalised name
    (Ms Brown) is a title; 'ms' straight after a number (QTc 480 ms) is milliseconds. Neither is flagged."""
    tok, s = m.group(0), m.string
    if in_event_id(m):
        return True
    if unit_after_number(m):
        return True
    if tok == "Ms" and re.match(r"\.?\s+[A-Z][a-z]", s[m.end():]):
        return True
    return tok == "ms" and re.search(r"\d\s*$", s[:m.start()]) is not None


def flagged_tokens(s, exact=()):
    out, prev = [], ""
    for m in TOKEN.finditer(s):
        if needs_flag(m.group(0), prev, exact) and not context_ok(m):
            out.append(m.group(0))
        prev = m.group(0)
    return out


def abbreviations(s):
    """Tokens in s that are flagged or are exact reviewed entries."""
    single = load_single_meaning()
    return [t for t in tokens(s) if t in single] + flagged_tokens(s, tuple(single))


def resolution_sources(resolve, text):
    """Turn --resolve "AB=meaning" items into cited sources R1, R2... Refuse an abbreviation not in the notes."""
    out = OrderedDict()
    for i, item in enumerate(resolve or [], 1):
        if "=" not in item:
            raise ValueError(f'--resolve must look like "MS=multiple sclerosis", got: {item}')
        ab, full = (x.strip() for x in item.split("=", 1))
        if not ab or not full:
            raise ValueError(f'--resolve must look like "MS=multiple sclerosis", got: {item}')
        if ab not in tokens(text):
            raise ValueError(f"--resolve {ab}: that abbreviation is not in the notes")
        out[ab] = (f"R{i}", full)
    return out


def split_ids(line):
    m = re.search(r"( \[[LR]\d+(?:, [LR]\d+)*\])( \|)?$", line)
    return (line[:m.start()], line[m.start():]) if m else (line, "")


ROUTE_SLOT = "\x00route\x00"


def route_slot(text, ids, plain=False):
    """([(route, line id)], text with each route slot replaced by ROUTE_SLOT, in order) for a template medicine
    line, else ([], text). The slot is the route word as written after the dose and frequency. plain: also take
    the signed plain words that follow it in a patient letter."""
    found = []
    for lid in dict.fromkeys(re.findall(r"\bL\d+\b", ids)):
        route = _ROUTES.get(lid)
        if not route:
            continue
        tail = r"(?:\s+\(" + re.escape(ROUTE_PLAIN[route]) + r"\))?" if plain and route in ROUTE_PLAIN else ""
        start = (re.search(r"\d", text) or re.match("", text)).end()
        rm = re.compile(r"(?<![A-Za-z0-9-])" + re.escape(route) + tail + r"(?![A-Za-z0-9-])").search(text, start)
        if rm:
            text = text[:rm.start()] + ROUTE_SLOT + text[rm.end():]
            found.append((route, lid))
    return found, text


def wording(lines, audience, resolved=None, single=None, flags=None):
    resolved, single = resolved or {}, single if single is not None else load_single_meaning()
    flags = [] if flags is None else flags
    seen, out, med = set(), [], False
    med_heads = {h[k] for h in HEADINGS.values() for k in ("meds", "med_changes")}
    res_heads = {h["results"] for h in HEADINGS.values()}
    res = False
    for line in lines:
        if line.startswith("## "):
            med = line[3:].strip() in med_heads
            res = line[3:].strip() in res_heads
        if not re.match(r"^(- |\d+\. |\| (?!Test \|))", line):
            out.append(line); continue
        text, ids = split_ids(line)
        routes, text = route_slot(text, ids) if med else ([], text)   # route slot: never the shorthand flag
        if audience == "patient":
            for pat, rep in PLAIN:
                text = re.sub(pat, rep, text)
        extra, prev = [], [""]
        exact = tuple(resolved) + tuple(single)
        rs = reason_start(text) if med else None
        def sub(m):
            ab, before = m.group(0), prev[0]
            prev[0] = ab
            in_reason = rs is not None and m.start() >= rs   # a reason: never the misspelling flag; abbreviations
            if ab not in resolved and ab not in single and context_ok(m):
                return ab                                    # Ms Brown, 480 ms: kept as written, no flag
            if in_reason and ab not in resolved and ab not in single and not reason_flag(ab, before, exact):
                return ab                                    # ordinary English reason: kept as written, no flag
            if ab in resolved:
                rid, full = resolved[ab]
                if rid not in extra:
                    extra.append(rid)
                if full == ab:
                    return ab                                # confirmed as written (e.g. a drug name)
            elif ab in single:
                full = single[ab]["patient" if audience == "patient" else "clinical"]
            elif audience == "patient" and ab in patient_wording():
                if re.match(r"\s*\(", m.string[m.end():]):
                    return ab                                # the notes already explain it in a bracket
                return f"{ab} ({patient_wording()[ab]})"           # signed plain words, on every use
            elif needs_flag(ab, before, exact):
                label = DRUG_FLAG if med and not in_reason and (ab.islower() or (ab.istitle() and len(ab) > 3)) and ab.lower() not in SHORTHAND \
                    and ab.lower() not in WORDS else FLAG
                note = " (drug name, check spelling)" if label == DRUG_FLAG else ""
                flags.append(f"{ab}{note}{ids.strip(' |') and ' ' + ids.strip(' |')}")
                return f"{ab} {label}"
            else:
                return ab
            if ab in seen:
                return full if audience == "patient" else ab
            seen.add(ab)
            return f"{full} ({ab})"
        text = TOKEN.sub(sub, text)
        if audience == "patient" and res:
            text = patient_units(text)                       # results only; medicine lines stay as signed
        def dose(m):
            if FLAG_ANY.match(text, m.end()):
                return m.group(0)
            flags.append(f"{m.group(0)}{ids.strip(' |') and ' ' + ids.strip(' |')}")
            return f"{m.group(0)} {FLAG}"
        text = DOSE_U.sub(dose, text)
        for route, lid in routes:
            shown = render_route(route, audience)
            if not route_signed(route):
                shown = f"{route} {ROUTE_FLAG}"
                flags.append(ROUTE_NOTICE.format(lid=lid))
            text = text.replace(ROUTE_SLOT, shown, 1)
        if extra and ids:
            ids = re.sub(r"\]", ", " + ", ".join(extra) + "]", ids, count=1)
        out.append(text + ids)
    return out


# ---------- 5. checker (independent of the renderer) ----------
DIRECTION = re.compile(r"\b(improv\w*|rising|rose|risen|falling|fell|fallen|worsen\w*|better|worse|"
                       r"increas\w*|decreas\w*|trend\w*|stable|declin\w*|up|down|higher|lower|"
                       r"normalis\w*|settl\w*)\b", re.I)
_FIXED = [*EMPTY.values(), ND, "reaction", "Reason for change", "Who follows up", "Who When Action", "sent on an "
          "undocumented date", "No known drug allergies", "resolved ruled out", "started changed stopped restarted from now",
          "possible", " ".join(MONTHS), "Test Results", "reason not included", ALLERGY_NO_REACTION_PATIENT]
VOCAB = set()
for _s in _FIXED:   # only fixed scaffolding; expansions and rewrites are earned per cited line
    VOCAB.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]+", _s))
WORD = re.compile(r"[A-Za-z][A-Za-z-]{3,}")
NUM = re.compile(r"\d+(?:\.\d+)?")
OUTDATE = re.compile(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})\b")


# Fixed list of label rewrites: (pattern that must appear in the cited lines, wording it may become).
LABEL_REWRITES = [(NONCOMPLIANT, "not taking medicines as prescribed")]


def _words(s):
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]+", s)}


def _source_words(src, single=None):
    """Words a line may use: those in its cited lines (including cited R resolutions), plus single-meaning
    expansions and rewrites earned by those same lines only."""
    words = {w.lower() for w in WORD.findall(src)}
    single = load_single_meaning() if single is None else single
    for ab, full in single.items():                    # reviewed list, abbreviation must be in the cited lines
        if ab in tokens(src):
            words |= _words(full["clinical"]) | _words(full["patient"])
    for pat, rep in PLAIN:
        if re.search(pat, src):
            words |= _words(rep)
    for m in UNIT_AFTER_NUMBER.finditer(src):           # signed unit wording, earned by the unit in the line
        words |= _words(UNIT_WORDING.get(mu_norm(m.group(3)), ""))
    for ab, plain in patient_wording().items():        # signed patient plain words, earned by the word in the line
        if ab in tokens(src):
            words |= _words(plain)
    for pat, rep in LABEL_REWRITES:
        if pat.search(src):
            words |= _words(rep)
    for r, plain in ROUTE_PLAIN.items():               # signed route plain words, earned by the route in the line
        if re.search(rf"(?<![A-Za-z0-9-]){re.escape(r)}(?![A-Za-z0-9-])", src):
            words |= _words(plain)
    return words


ROUTE_SECTIONS = {h[k] for h in HEADINGS.values() for k in ("meds", "med_changes")}


def check(md, sources, letter_type):
    """Refuse any body line that is unsourced or states a word, number or date its cited lines lack."""
    problems, words_total, section = [], 0, None
    body = md.split("\n---\n")[0]
    started = False
    for mdn, line in enumerate(body.splitlines(), 1):
        if line.startswith("## "):
            section, started = line[3:], True
            continue
        if not started or not re.match(r"^(- |\d+\. |\| (?!Test \|)(?!---))", line):
            continue
        text, ids = split_ids(line)
        text = re.sub(r"^(- |\d+\. |\| )", "", text).replace(" | ", " ")
        words_total += len(text.split())
        cited = [i.strip() for i in ids.strip(" []|").split(",") if i.strip()]
        at = f"line {cited[0]}" if cited else f"letter line {mdn}"
        for sent in re.split(r"(?<=[.!])\s+", FLAG_ANY.sub("", text)):
            if len(sent.split()) > SENTENCE_LIMIT and SENTENCE_FLAG not in line:
                problems.append(CHECK_SENTENCE_LIMIT.format(SENTENCE_LIMIT=SENTENCE_LIMIT, at=at))
        if re.search(r"(?<!\w)\?\w", text):
            problems.append(CHECK_SHORTHAND.format(at=at))
        if section in ("Results", "Your test results") and DIRECTION.search(text):
            problems.append(CHECK_DIRECTION_WORD.format(at=at))
        if not cited:
            if text.strip() in EMPTY.values() or text.strip() == NONE_RECORDED or text.strip() in EMPTY_PATIENT.values():
                continue
            if text.strip() in {x[2:] for sec in _DECLINED for aud in ("patient", "gp") for x in _decline_lines(sec, aud)}:
                continue                                 # a signed decline line: sourced from forms, not the notes
            problems.append(CHECK_UNSOURCED.format(at=at))
            continue
        if any(c not in sources for c in cited):
            problems.append(CHECK_BAD_CITE.format(at=at))
            continue
        src = " ".join(sources[c][0] + " " + (sources[c][1] or "") for c in cited)
        res = {sources[c][0].split(" = ", 1)[0]: sources[c][0].split(" = ", 1)[1] for c in cited if c.startswith("R")}
        single = load_single_meaning()
        scan = text
        if section in ROUTE_SECTIONS:
            scan = route_slot(scan, ids, plain=True)[1].replace(ROUTE_SLOT, "route")   # signed route slot: checked in wording()
        forms = [(f, ab) for ab, d in single.items() for f in (d["clinical"], d["patient"])] + \
                [(f, ab) for ab, f in res.items()] + [(f"{ab} ({f})", ab) for ab, f in patient_wording().items()] + \
                [(f"{v} ({w})", v) for u, w in UNIT_WORDING.items() if w for v in {u, u.replace("\u00b5", "\u03bc")}]
        for f, ab in sorted(forms, key=lambda x: -len(x[0])):
            scan = scan.replace(f"{f} ({ab})", ab).replace(f, ab)
        exact = tuple(res) + tuple(single)
        prev = ""
        rs = reason_start(scan)
        for m in TOKEN.finditer(scan):
            ab = m.group(0)
            flag = reason_flag if rs is not None and m.start() >= rs else needs_flag
            if flag(ab, prev, exact) and not context_ok(m) and not FLAG_ANY.match(scan, m.end()):
                problems.append(CHECK_ABBREV.format(at=at))
            prev = ab
        for m in DOSE_U.finditer(scan):
            if not FLAG_ANY.match(scan, m.end()):
                problems.append(CHECK_DOSE_UNIT.format(at=at))
        text = FLAG_ANY.sub("", text)
        for m in OUTDATE.finditer(text):
            iso = f"{m.group(3)}-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"
            if iso not in src:
                problems.append(CHECK_DATE.format(at=at))
        rest = OUTDATE.sub(" ", text)
        srcnums = set(NUM.findall(src))
        for n in NUM.findall(rest):
            if n not in srcnums:
                problems.append(CHECK_NUMBER.format(at=at))
        sw = _source_words(src)
        for w in WORD.findall(rest):
            lw = w.lower()
            if lw not in sw and lw not in VOCAB and not any(p in sw for p in lw.split("-")):
                problems.append(CHECK_WORD.format(at=at))
    limit = int(WORD_LIMIT * 1.1)
    if letter_type != "discharge" and words_total > limit:
        problems.append(CHECK_WORD_LIMIT.format(words_total=words_total, WORD_LIMIT=WORD_LIMIT))
    if problems:
        raise Refused("\n".join(dict.fromkeys(problems)))
    return True


# ---------- 6. render: header, brand, template ----------
TITLES = {"referral": "Referral", "discharge": "Discharge summary", "gp-update": "Update",
          "specialist-reply": "Specialist reply"}
OPENERS = {
    "referral": "Please answer the question below.",
    "discharge": "Discharged{d}. Actions are numbered at the end.",
    "gp-update": "Update{d}. Actions are numbered at the end.",
    "specialist-reply": "My answer and advice are below.",
}
def no_question_opener(st):
    """No question found: open with the reason for referral (the first current problem), sourced."""
    for body, _, ids in st["problems"].values():
        return f"Reason for referral: {body} [{', '.join(ids)}]"
    for _, body, ids in st["narrative"]:                # no problem tagged: the first clinical note line
        return f"Reason for referral: {body} [{', '.join(ids)}]"
    return f"Reason for referral {ND}."
PATIENT_OPENER = "This letter explains your care and what happens next."


def load_brand(path):
    p = pathlib.Path(path)
    data = tomllib.loads((p / "brand.toml").read_text(encoding="utf-8"))
    for k in ("name", "address", "logo"):
        if k not in data:
            raise ValueError(f"brand.toml missing '{k}'")
    data["logo_path"] = str(p / data["logo"])
    return data


def brand_block(brand):
    if not brand:
        return [DRAFT_MARK, ""]
    fonts = brand.get("fonts", {}); cols = brand.get("colours", {})
    style = "; ".join([f"font-{k}: {v}" for k, v in fonts.items()] + [f"colour-{k}: {v}" for k, v in cols.items()])
    return [f"<!-- brand style: {style} -->", f"![{brand['name']} logo]({brand['logo_path']}) {DRAFT_MARK}", "",
            f"**{brand['name']}**  ", brand["address"].strip().replace("\n", "  \n"), ""]


def render(st, pid, letter_type, to, recipient=None, audience="specialist", template=None, brand=None, log=None,
           resolved=None, flags=None):
    log = [] if log is None else log
    header = brand_block(brand) + [
        f"**Date:** {fdate(st['last']) if st['last'] else ND}  ",
        f"**To:** {to}" + (f", attn {recipient}" if recipient else "") + "  ",
        f"**Patient:** {pid}  ", f"**Letter:** {TITLES[letter_type]}", "",
        f"Dear {recipient}," if recipient else ("Dear patient," if audience == "patient" else "Dear colleague,"), "",
        PATIENT_OPENER if audience == "patient" else
        (no_question_opener(st) if letter_type == "referral" and not st["question"] else OPENERS[letter_type].format(d=dt(st["last"]))), ""]
    blocks = OrderedDict((n, render_section(n, st, audience)) for n in SECTIONS[letter_type])
    footer = "\n".join(FOOTER)
    if template is None:
        body = "\n".join(b for b in blocks.values() if b)
        md = "\n".join(header) + "\n" + body + "\n" + footer
    else:
        slots = set(re.findall(r"\{\{(\w+)\}\}", template))
        unknown = slots - set(blocks) - {"header", "footer"} - set(HEADINGS["gp"])
        if unknown:
            raise ValueError(f"template has unknown slots: {', '.join(sorted(unknown))}")
        must = [n for n in blocks if n in NEVER_CUT or (n == "question" and letter_type == "referral")]
        missing = [n for n in must if n not in slots]
        for n in missing:
            log.append(f"template omitted never-cut section '{n}'; added by renderer")
        md = template
        md = md.replace("{{header}}", "\n".join(header)) if "header" in slots else "\n".join(header) + "\n" + md
        for n in slots - {"header", "footer"}:
            md = md.replace("{{%s}}" % n, blocks.get(n) or "")
        extra = "\n".join(blocks[n] for n in missing if blocks[n])
        if "footer" in slots:
            md = md.replace("{{footer}}", extra + "\n\n" + footer)
        else:
            md = md.rstrip("\n") + "\n\n" + extra + "\n\n" + footer
    md = "\n".join(wording(md.splitlines(), audience, resolved, flags=flags))
    if audience == "patient":
        md = md.rstrip("\n") + "\n\n" + PATIENT_PRIVACY      # fixed wording; after the footer, never flagged
    return re.sub(r"\n{3,}", "\n\n", md).rstrip("\n") + "\n"


# ---------- pipeline ----------
def llm_guard():
    if os.environ.get("LETTERS_LLM_PROVIDER"):
        raise SystemExit("LETTERS_LLM_PROVIDER is set, but letters makes no model calls in this version. "
                         "Unset it to run.")


COPY_TAIL = ("This tool can't send copies. Write a separate letter for each reader, "
             "so nobody assumes someone was copied in when they weren't.")
COPY_REFUSED = COPY_TAIL  # stable tail; the full message is "Refused: copy request <where>. " + COPY_TAIL
# LT-PRIVACY-PT, letters_status_lines_SIGNED.md (Physician, 15 Oct 2026), byte-exact.
PATIENT_PRIVACY = "This letter includes private details about your health. It's yours to share or keep private."
COPY_WORDS = re.compile(r"(?<![\w])(?:cc|c\.c\.|copy|copies|copied)(?![\w])", re.I)
COPY_LINE = re.compile(r"^\s*(?:\d{4}-\d{2}-\d{2}\s+)?(?:cc|c\.c\.|copy|copies|copied)(?:\s+to)?\s*:", re.I | re.M)
COPY_SLOT = re.compile(r"\{\{\s*(?:cc|copy|copies|copy_to)\s*\}\}", re.I)
# In notes: a cc dose unit is removed first; a "CC:" chief-complaint heading is allowed only when clearly a symptom.
DOSE_CC = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)? ?[cC][cC](?![A-Za-z0-9/])")
# Any copy word in the notes is a copy request ('send a copy of this to her son', 'copies to', 'cc'd'): when unclear,
# it counts. (Round 9: the earlier pattern needed 'to' straight after 'copy', so a sentence request slipped through.)
NOTE_COPY = re.compile(r"(?<![\w.])(?:cc|c\.c\.|cc'd|cop(?:y|ies|ied|ying))(?![\w])", re.I)
VIRAL_COPIES = re.compile(r"[<>]?\d[\d,.]*\s*copies\b(?:\s*/\s*\w+)?|\bcopies\s*/\s*\w+", re.I)
CHIEF_COMPLAINT = re.compile(r"^CC\s*:\s*(.+)$")
NOT_A_SYMPTOM = set(DOMAIN["symptoms"]["not_a_symptom"])


_KNOWN = []


def _known_words():
    if not _KNOWN:
        _KNOWN.append(_lines(WORDLISTS[0]) | _lines(WORDLISTS[1]))
    return _KNOWN[0]


COPY_VERB = re.compile(r"\b(?:send|post|forward|give|fax|e-?mail|mail)\b(?:\W+\w+){0,4}?\W+"
                       r"(?:this|it|letter|letters|notes|summary|report|results)\b", re.I)
RELATION = re.compile(r"\b(?:son|daughter|wife|husband|partner|mother|father|mum|dad|sister|brother|friend|neighbour|"
                      r"neighbor|carer|family|solicitor|lawyer|insurer|employer|school|next of kin|nok)s?\b", re.I)
SEND_TO = re.compile(r"\b(?:to|for|cc|in)\b:?\s+(?!(?:a|an|any|be|do|read|keep|check|review)\b)(?:the\s+|my\s+|her\s+|his\s+|their\s+)?"
                     r"(?:mr|mrs|ms|miss|dr|prof|[A-Za-z]{2,})", re.I)
NOTE_COPY_REFUSED = "Refused: line L{n} mentions a copy. This tool never sends copies. Nothing was written. If this isn't a copy request, run again with `--not-copy L{n}`."   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
NOT_COPY_REFUSED = "Refused: line L{n} names someone to send a copy to, so `--not-copy` can't be used on it. This tool never sends copies. Nothing was written. Remove the copy request from the notes, then run again."   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
NOT_COPY_MARK = "Line L{n} marked as not a copy request"


def copy_lines(text):
    """[(n, names_someone)] for each notes line that asks to copy, send, post, forward or give the letter or notes,
    or names a person or place to send it to. Never returns the line text. When unclear, it counts."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = HEAD.match(line)
        if m:
            line = m.group(2).strip()
        line = DOSE_CC.sub(" ", line)
        line = VIRAL_COPIES.sub(" ", line)                 # '<20 copies/mL' is a lab unit, not a copy request
        cc = CHIEF_COMPLAINT.match(line)
        if cc:
            words = re.findall(r"[A-Za-z']+|\S", cc.group(1))
            known = _known_words()
            symptom = words and all(w.isalpha() and w == w.lower() and w in known and w not in NOT_A_SYMPTOM
                                    for w in words if w not in (",", "."))
            if symptom and not NOTE_COPY.search(cc.group(1)):
                continue
            out.append((n, True)); continue
        hit = NOTE_COPY.search(line) or COPY_LINE.search(line) or COPY_VERB.search(line)
        if hit:
            after = line[hit.start():]
            names = bool(RELATION.search(line) or SEND_TO.search(after) or COPY_LINE.search(line)
                         or re.search(r"\b(?:mr|mrs|ms|miss|dr)\.?\s+[A-Z]", line))
            out.append((n, names))
    return out


def copy_where(text):
    """Return 'at L<n>' for the first copy request in the notes, else None (kept for older callers)."""
    found = copy_lines(text)
    return f"at L{found[0][0]}" if found else None


def refuse_note_copies(text, not_copy=()):
    """Refuse the first copy request line. '--not-copy L<n>' covers one line, never a line naming someone to send to.
    Returns the covered line numbers (printed beside the cut list)."""
    covered = []
    for n, names in copy_lines(text):
        if f"L{n}" in not_copy:
            if names:
                raise Refused(NOT_COPY_REFUSED.format(n=n))
            covered.append(n)
            continue
        raise Refused(NOTE_COPY_REFUSED.format(n=n))
    return covered


PATIENT_COPY_TAIL = "A letter to the patient can't be copied to anyone else. Write a separate letter for each other reader."


def copy_refusal(where, patient=False):
    return f"Refused: copy request {where}. {PATIENT_COPY_TAIL if patient else COPY_TAIL}"


def is_patient_letter(to, audience):
    return audience == "patient" or (to or "").strip().lower() in ("patient", "the patient")


def refuse_copies(text, template=None, brief=None, patient=False, not_copy=()):
    """Every letter has one reader. Refuse any copy recipient from the brief, the template or the notes."""
    if brief and COPY_WORDS.search(brief):
        raise Refused(copy_refusal("in the brief", patient))
    if template and (COPY_SLOT.search(template) or COPY_LINE.search(template)):
        raise Refused(copy_refusal("in the template", patient))
    return refuse_note_copies(text, not_copy)


def build(text, letter_type, to, recipient=None, audience=None, template=None, brand=None, resolve=None, flags=None,
          include=None, not_copy=(), keep_out=(), privacy=None, declined=None):
    """include: item names the brief asked to keep (see SENSITIVE); everything else sensitive is cut."""
    unknown = [i for i in (include or []) if i.lower() not in {c.lower() for c in SENSITIVE}]
    if unknown:
        raise Refused(SENSITIVE_REFUSED_NO_LINE)              # library call: no brief line is known, so none is shown
    _INCLUDE.clear(); _INCLUDE.update(i.lower() for i in (include or []))
    _NOT_COPY[:] = list(not_copy or ())
    _KEEP_OUT[:] = list(keep_out or ()); _SENS_CUTS.clear(); _KEPT_OUT.clear()
    own = [] if flags is None else flags                      # every flag is kept for finalise(), screen-only too
    _DECLINED.clear()
    for d in declined or ():
        _DECLINED.setdefault(d["section"], []).append(d)
    if not is_patient_letter(to, audience):                  # a clinician letter never carries a declined sensitive answer
        n_lines = len(text.splitlines())
        for d in declined or ():
            for lid in d["lines"] if d["sensitive"] else ():
                if 1 <= int(lid[1:]) <= n_lines:
                    _DECLINED.clear()
                    raise Refused(SENSITIVE_DECLINED_REFUSED.format(lid=lid))
    try:
        md, dropped = _build(text, letter_type, to, recipient, audience, template, brand, resolve, own)
        bad = declined_absence_lines(md, declined)        # severity 5: never write an absence line after a decline
        if bad:
            lid = (re.findall(r"\bL\d+\b", bad[0]) or ["L?"])[0]
            raise Refused(DECLINED_NO_ABSENCE_LINE.format(lid=lid))
        return BuildResult(md, dropped, own, privacy, is_patient_letter(to, audience))
    finally:
        _INCLUDE.clear(); _PATIENT_LETTER.clear(); _DECLINED.clear()


# Temporary (13 Oct 2026): combination medicines can't be fully listed yet, so any letter naming one is refused.
COMBINATION_FILE = HERE / "locale" / LOCALE / "combination.toml"
# Clinician-only (screen of the person running letters; never a patient view). AWAITING sign-off: docs/RULINGS.md.
COMBO_PACK_REFUSED = 'Refused: there is no signed list of combination medicines for this locale. The list needs brands, a source and sign-off. Nothing was written. Ask for the list to be completed and signed.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026


def load_combination(path=None):
    """Check 2's list from the locale pack. letters won't run without a list, a source and a sign-off."""
    p = COMBINATION_FILE if path is None else path
    try:
        d = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        raise Refused(COMBO_PACK_REFUSED)
    brands = [b for b in d.get("brands", []) if isinstance(b, str) and b.strip()]
    if not brands or not str(d.get("source", "")).strip() or not str(d.get("signed_off_by", "")).strip() \
            or not str(d.get("signed_off_on", "")).strip():
        raise Refused(COMBO_PACK_REFUSED)
    return tuple(brands)


def _combo_rx(brands):
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(b) for b in brands) + r")(?![a-z0-9])", re.I)


try:
    COMBO_BRANDS = load_combination()                    # listed combination medicines (locale pack, same order)
except Refused:
    COMBO_BRANDS = ()                                    # every run is refused instead (refuse_combination_brands)
COMBO_REFUSED = "Refused: line {lid} has a combination medicine that this tool can't fully list yet. Nothing was written."   # signed round 3, 15 Oct 2026


class ComboRefused(Refused):
    """Check 2 refusal: the letter must be written by hand (PV-/CQ-LETTER-BY-HAND)."""
    check = 2


def refuse_combination_brands(text):
    """Any line, any letter type or audience. Gives the line number only, never the drug name."""
    rx = _combo_rx(load_combination())                   # the pack is read on every run: no list, no letter
    if not rx.search(text):
        return
    for e in parse(text)[0]:
        for lid, line in e["lines"]:
            if rx.search(line):
                raise ComboRefused(COMBO_REFUSED.format(lid=lid))
    n = next(k for k, l in enumerate(text.splitlines(), 1) if rx.search(l))
    raise ComboRefused(COMBO_REFUSED.format(lid=f"L{n}"))


# Signed lines (forms_orders_lines_SIGNED.md, round 12, Physician 14 Oct 2026), copied word for word.
BY_HAND_LINES = {
    "PV-LETTER-BY-HAND": "Your care team will write this letter for you themselves. You don't need to do anything.",
    "CQ-LETTER-BY-HAND": "For a clinician to write: this letter couldn't be made automatically (check [check_n]). "
                         "It stays open until you write it.",
}


# CQ-LETTER-READER-ENDED: Educator r14 text, Physician safety re-sign (letters_refusals_*.md, 15 Oct 2026).
# RUN-TRACES-NOT-RUN: signed in forms_orders_lines_SIGNED.md, its one signed source (room ruling 15 Oct 09:42).
RUN_TRACES_NOT_RUN = ("Safety traces not run: the held-out answer files aren't on this computer. This run doesn't show "
                      "that the stack is safe, and it doesn't count towards a release.")
READER_ENDED_LINE = ("For a clinician to write: this letter is addressed under a permission to act for the patient "
                     "that has ended. Don't send it under that permission. Write to the patient, or to whoever has "
                     "permission to act for them now. It stays open until you write it.")


def letter_by_hand(check_n):
    """What each screen shows when a check means the letter must be written by hand. The patient line names no
    medicine, condition or check; the clinician entry stays open until a letter is written by hand."""
    return {"patient": BY_HAND_LINES["PV-LETTER-BY-HAND"],
            "clinician": {"id": "CQ-LETTER-BY-HAND", "open": True,
                          "line": BY_HAND_LINES["CQ-LETTER-BY-HAND"].replace("[check_n]", str(check_n))}}


REFUSAL_LINES_FILE = HERE / "data" / "refusal_lines.toml"


def _refusal_patterns():
    with open(REFUSAL_LINES_FILE, "rb") as fh:
        rows = tomllib.load(fh)["code"]
    out = []
    for r in rows:
        first = r["text"].split("\n")[0]
        parts = re.split(r"(\{[^}]*\})", first)
        rx = "".join(".*?" if re.fullmatch(r"\{[^}]*\}", x) else re.escape(x) for x in parts)
        out.append((re.compile(rx + (r"(?: \(reason: [A-Za-z -]+\))?$" if r["key"] == "INCIDENT" else "$"), re.S), r["check"]))
    return out


def refusal_check(msg):
    """The check number of a refusal line, from data/refusal_lines.toml. Fixed numbers only; None if not listed."""
    first = str(msg).split("\n")[0]
    return next((c for rx, c in _refusal_patterns() if rx.match(first)), None)


def report_refusal(msg, patient, rc=2):
    """Every refusal: the line goes to the clinician's screen (stderr) only. On a patient-letter request the patient
    sees PV-LETTER-BY-HAND (stdout, the only patient-visible output) and CQ-LETTER-BY-HAND opens for the clinician
    (Patient's fallback, 14 Oct 2026). Nothing is written to disk."""
    print(str(msg), file=sys.stderr)
    if patient:
        hand = letter_by_hand(refusal_check(msg) or "not listed")
        print(hand["patient"])
        print(hand["clinician"]["line"], file=sys.stderr)
    return rc


READERS = tomllib.loads((HERE / "data" / "readers.toml").read_text(encoding="utf-8"))
READER_ID = re.compile(r"SYNTH(?:-(?:REG|CARD|AG))?-\d{1,6}")
CARDS_DIR = pathlib.Path(os.environ.get("LETTERS_CARDS_DIR", HERE.parent / "cards"))


def load_patient_card(path):
    """The patient's card, typed and checked by cards' own loader (read-only use of cards; no change to it)."""
    if str(CARDS_DIR) not in sys.path:
        sys.path.insert(0, str(CARDS_DIR))
    import cards                                         # noqa: E402  the cards repository's loader
    return cards.load(pathlib.Path(path).read_text(encoding="utf-8"))


def _id_kind(rid):
    return rid.rsplit("-", 1)[0]


def acts_for_patient(card, rid):
    """True when this id has ever held a grant on the patient's card (cards grant data). A card keeps every grant,
    so revoked or expired grants still count (ruling, 15 Oct 2026). Never by name."""
    return any(g["agent_id"] == rid for g in (card or {}).get("grants", {}).values())


GRANT_ID = re.compile(r"SYNTH-GR-\d{1,6}")
SCENARIO_DATE = re.compile(r"19[3]\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])")


def reader_grants(card, reader_ids, grant_ids, now):
    """Fails closed; ids only, never names. Returns (route, cleared): route None when no reader acts for the patient
    and no grant id is given, "current" when every grant holder writes under a current grant, "ended" when one is
    addressed under a revoked or expired grant (even if the same person holds a newer one), else a by-hand reason.
    cleared: the reader ids whose current grant id was given. Grant state comes from cards' one read path."""
    ids, gids = [r.strip() for r in reader_ids], [g.strip() for g in grant_ids]
    holders = [r for r in ids if acts_for_patient(card, r)]
    if not holders and not gids:
        return None, ()
    if card is None or not SCENARIO_DATE.fullmatch(now or ""):
        return "grant data unreadable", ()
    grants, ended, cleared = card.get("grants", {}), False, []
    for g in gids:
        if not GRANT_ID.fullmatch(g) or g not in grants or grants[g].get("agent_id") not in holders:
            return "unknown grant id", ()
    for r in holders:
        mine = [g for g in gids if grants[g]["agent_id"] == r]
        if not mine:
            return "no grant id", ()
        for g in mine:
            st = _cards().grant_state(card, g, now)
            if st in ("revoked", "expired"):
                ended = True
            elif st != "current":
                return "grant not current", ()
        cleared.append(r)
    return ("ended" if ended else "current"), tuple(cleared)


def _cards():
    if str(CARDS_DIR) not in sys.path:
        sys.path.insert(0, str(CARDS_DIR))
    import cards                                         # noqa: E402  the cards repository's one grant read path
    return cards


def needs_hand(to, audience, brief_ok, n_readers, reader_ids, notes, card=None, cleared=()):
    """Fails closed. Returns why the letter takes the by-hand route on a refusal, or None when every reader resolves,
    by id, to a normal reader who is neither the patient nor someone holding a grant to act for them.
    No name is ever matched."""
    if is_patient_letter(to, audience):
        return "patient named"
    if not brief_ok:
        return "brief not read"
    ids = [r.strip() for r in reader_ids]
    if len(ids) < n_readers or any(not READER_ID.fullmatch(r) for r in ids):
        return "reader without id"
    pids = set(SYNTH.findall(EVENT_ID.sub(" ", notes)))
    card_id = (card or {}).get("header", {}).get("card_id")
    for r in ids:
        if r in pids or r == card_id:
            return "patient"
        if r in cleared:                                 # writes under a current grant id (reader_grants)
            continue
        if acts_for_patient(card, r):
            return "acts for patient"
        if _id_kind(r) not in READERS["normal"]:
            return "not a reader id"
    return None


def brief_readers(brief_text):
    """How many readers a brief names: its reader plus one per copy request."""
    return 1 + len(COPY_WORDS.findall(brief_text or ""))


def close_letter_by_hand(entry, hand_letter):
    """Closes the clinician entry only when a hand-written letter is supplied."""
    if not (hand_letter or "").strip():
        return entry
    return dict(entry, open=False)


def _has_question(md, audience):
    head = HEADINGS[audience if audience in HEADINGS else "specialist"]["question"]
    m = re.search(rf"^## {re.escape(head)}\n(.*?)(?=^## |^---|\Z)", md, re.S | re.M)
    return bool(m and re.search(r"^- \S", m.group(1), re.M))


def _build(text, letter_type, to, recipient=None, audience=None, template=None, brand=None, resolve=None, flags=None):
    """Return (markdown, dropped). The dropped list is for the clinician's eyes only; never store it.
    resolve: list of "AB=meaning" from the clinician; each becomes a cited source R1, R2...
    flags: optional list that receives every open abbreviation flag."""
    refuse_combination_brands(text)
    llm_guard()
    if letter_type not in TYPES:
        raise ValueError(f"unknown type {letter_type}; choose from {', '.join(TYPES)}")
    audience = audience or ("gp" if letter_type in ("discharge", "gp-update", "specialist-reply") else "specialist")
    pid = check_safe(text)
    _PATIENT_LETTER[:] = [True] if audience == "patient" else []
    refuse_copies(text, template, patient=is_patient_letter(to, audience), not_copy=_NOT_COPY)
    entries, sources = parse(text)
    resolved = resolution_sources(resolve, text)
    for ab, (rid, full) in resolved.items():
        sources[rid] = (f"{ab} = {full}", None)
    dropped = []
    st = cumsum(entries, to, dropped, keep_indications=(audience == "patient"))
    if audience != "patient":
        mark_linked_items(text, to, dropped)
    if flags is not None:
        flags.extend(RESULT_UNREAD_NOTICE.format(lid=i[0]) for _, i, unread in st["results_text"] if unread)
    md = render(st, pid, letter_type, to, recipient, audience, template, brand, dropped,
                resolved, flags)
    if not st["question"]:                       # every letter keeps its question; none found: the flag fires
        head = f"**Letter:** {TITLES[letter_type]}"
        md = md.replace(head, f"{head} {QUESTION_FLAG}", 1) if head in md else \
            md.replace("\n", f" {QUESTION_FLAG}\n", 1)
        if flags is not None:
            flags.append(QUESTION_NOTICE if letter_type == "referral" else QUESTION_NOTICE_ANY)
    md = flag_long_sentences(md, flags)
    for sec in ("allergies", "meds"):                   # an empty allergies or medicines field blocks --final
        if sec in _EMPTY_FIELDS and sec in SECTIONS[letter_type] and sec not in _DECLINED:
            head = f"**Letter:** {TITLES[letter_type]}"
            md = md.replace(head, f"{head} {EMPTY_FIELD_FLAG}", 1)
            if flags is not None:
                flags.append(EMPTY_FIELD_NOTICE[sec].format(lid=_EMPTY_LINES.get(sec, "L?")))
    for _, line in _body_lines(md):
        for fl, notice in ((ALLERGY_FLAG, ALLERGY_NOTICE), (OWNER_FLAG, OWNER_NOTICE)):
            if fl in line and flags is not None:
                flags.append(notice.format(lid=_line_ids(line)[-1]))
    patient = audience == "patient"           # the patient keeps their own sensitive details
    if not patient:
        md = flag_linked_medicines(md, text, dropped, flags)
    check(md, sources, letter_type)
    refuse_missing_required(md, text, {i for v in st["pending"].values() for i in v[3]})
    if st["pending"] and EMPTY["pending"] in md:
        lid = next(iter(st["pending"].values()))[3][-1]
        raise Refused(PENDING_NONE_REFUSED.format(lid=lid))
    if not patient:
        md = apply_cut_rule(md, text, dropped, flags, TITLES[letter_type])
        md = drop_cut_change_reasons(md, flags)
        md = flag_cuts(md, dropped, flags, TITLES[letter_type], text)
    if QUESTION_FLAG not in md and not _has_question(md, audience):
        head = f"**Letter:** {TITLES[letter_type]}"      # the question was cut or empty: the flag still fires
        md = md.replace(head, f"{head} {QUESTION_FLAG}", 1)
        if flags is not None:
            flags.append(QUESTION_NOTICE if letter_type == "referral" else QUESTION_NOTICE_ANY)
    verify_letter(md, text, audience, _INCLUDE)
    reverse_check(md, text, " ".join([x for x in (to, recipient) if x] + [f for _, f in resolved.values()]))
    return md, dropped


# ---------- reverse check: every date, number, name and owner in the letter is in the notes or the brief ----------
REVERSE_REFUSED = "Refused: line L{n} of this letter has a date, number or name that isn't in the notes. Nothing was written. Correct it to match the notes, then run again."   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
_LETTER_DATE = re.compile(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})\b")


def _tool_words():
    words = set()
    for v in list(globals().values()):
        if isinstance(v, (list, tuple)):
            v = " ".join(x for x in v if isinstance(x, str))
        if isinstance(v, str):
            words |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]*", v)}
        elif isinstance(v, dict):
            for x in v.values():
                if isinstance(x, str):
                    words |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]*", x)}
                if isinstance(x, dict):
                    for y in x.values():
                        if isinstance(y, str):
                            words |= {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]*", y)}
    return words


def reverse_check(md, text, extra="", template=None):
    """Every date, number, person name and owner in the letter must appear in the notes (cited lines for a cited
    letter line) or the brief. Allowed rewording: the signed lists only (frequency plain words, brand to generic,
    'no more than X in 24 hours'). A date may be reformatted, never recalculated. Line refs are exempt."""
    raw = {f"L{n}": l for n, l in enumerate(text.splitlines(), 1)}
    for lid, (_, d) in parse(text)[1].items():             # a line's own dated entry is a notes date for it
        if d and not (any(_TEMPLATE) if template is None else template):   # never the template Date field (the letter's own date)
            raw[lid] = raw.get(lid, "") + " " + d
    allnotes = text + "\n" + (extra or "")
    tool = _tool_words()
    single = load_single_meaning()
    expans = " ".join(v["clinical"] + " " + v["patient"] for v in single.values()).lower()
    expans += " " + " ".join(patient_wording().values()).lower()     # signed patient plain words
    expans += " " + " ".join(UNIT_WORDING.values()).lower()    # signed unit wording
    body = md.split("\n---\n")[0]
    tool |= {"dear", "who", "when", "action", "changed", "from", "now", "stopped", "started", "restarted", "reason",
             "change", "resolved", "ruled", "out", "sent", "undocumented", "date", "follows", "up"}
    declined = {x for sec in _DECLINED for aud in ("patient", "gp") for x in _decline_lines(sec, aud)}
    started = False
    for n, line in enumerate(body.split("\n"), 1):
        started = started or line.startswith("**Date:**")       # the brand pack's own header is not note content
        if line in declined:                                     # signed decline line; question wording from forms
            continue
        if not started or not line.strip() or line.startswith(("## ", "<!--", "![")) or line.startswith("| Test"):
            continue
        ids = [i for i in _line_ids(line) if i.startswith("L")]
        res = [i for i in _line_ids(line) if i.startswith("R")]
        src = " ".join(raw.get(i, "") for i in ids) if ids else allnotes
        src += " " + (extra or "")
        t = re.sub(r"^(?:- |\d+\. |\| )", "", FLAG_ANY.sub("", split_ids(line)[0]))
        bad = False
        for m in _LETTER_DATE.finditer(t):
            iso = f"{m.group(3)}-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"
            if iso not in src:
                bad = True
        for m in ISO.finditer(t):
            if m.group(0) not in src:
                bad = True
        rest = ISO.sub(" ", _LETTER_DATE.sub(" ", t))
        if re.search(PLAIN[-1][0], src):                        # signed: 'no more than X in 24 hours'
            rest = rest.replace(" in 24 hours", " ")
        num = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?![A-Za-z\d])")
        srcnums = set(num.findall(src))
        if any(x not in srcnums for x in num.findall(re.sub(r"\bSYNTH-\w+", " ", rest))):
            bad = True
        srcw = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'-]*", src)}
        for w in re.findall(r"(?<![\w-])[A-Z][a-z]+(?:-[A-Za-z]+)*(?![\w])", rest):     # person and place names
            lw = w.lower()
            if lw in srcw or lw in tool or lw in expans or lw in drug_names() or any(p in srcw for p in lw.split("-")):
                continue
            bad = True
        for m in re.finditer(r"\bWho(?: follows up)?: ([^.]+)\.", rest):          # owners
            who = m.group(1).strip()
            if who != ND and not all(w.lower() in srcw or w.lower() in expans
                                     for w in re.findall(r"[A-Za-z][A-Za-z'-]*", who)):
                bad = True
        if bad:
            raise Refused(REVERSE_REFUSED.format(n=n))


# ---------- post-write checks: linked terms, required details, long sentences ----------
LINKED_TERMS_FILE = HERE / "linked_terms.toml"
SENTENCE_FLAG = "[flag: long sentence]"
SENTENCE_NOTICE = "Flag: sentence at {lid} is over 25 words. Split it, or keep it as written."
MED_FLAG = "[flag: medicine may point to something cut]"
MED_NOTICE = ("Flag: a medicine on line {lid} may tell the reader about something that was cut from this letter. "
              "Keep it, or explain it to the reader.")
LINKED_REFUSED = 'Refused: line {lid} still mentions something this letter was meant to leave out. Nothing was written. Remove the mention, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
REQUIRED_REFUSED = 'Refused: line {lid} has an allergy reaction or a follow-up owner that this letter leaves out. Nothing was written. Add it to the letter, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
WORD_FLAG = "[flag: word may point to something cut]"
WORD_NOTICE = ("Flag: a word on line {lid} may tell the reader about something that was cut from this letter. "
               "Keep it, or reword it.")
ALLERGY_FLAG = "[flag: allergy has extra wording]"
ALLERGY_NOTICE = "Flag: the allergy on line {lid} has extra wording. Check whether it describes the reaction."
OWNER_FLAG = "[flag: pending test has follow-up wording]"
OWNER_NOTICE = "Flag: the pending test on line {lid} has follow-up wording. Check who follows it up."
MED_TAGS = ("med", "stop")


def load_linked(path=None):
    p = pathlib.Path(path) if path else LINKED_TERMS_FILE
    if not p.exists():
        return {}
    return dict(tomllib.loads(p.read_text(encoding="utf-8")).get("items", {}))


def _term_re(t):
    return re.compile(term_pattern(t), re.I)


def _meds_of(items):
    """Medicine and brand names of the cut items (allowed only on their own medication line)."""
    return [m for v in items.values() for m in list(v.get("medicines", [])) + list(v.get("brands", {}))]


CUT_FLAG = "[flag: something cut to keep it private]"
CUT_NOTICE = "Flag: something on line {lid} was cut to keep it private. Check the reader doesn't need it."
STOP_REASON_REFUSED = "Refused: line {lid} of the notes has a stop reason that isn't in brackets. Nothing was written. Put the stop reason in brackets, then run again."   # signed: Physician (safety) + Educator (wording), 14 Oct 2026


# Sensitive result cut (round 11 signed rulings; override: the flag is on screen only, never in letter.md).
SENS_CUT_RECORD = "Cut as sensitive: line {lid}."
SENS_RESULT_NOTICE = ("Flag: a result on line {lid} was left out as sensitive. If this reader needs it, add it to the "
                      "brief's Include: line once the patient agrees. Otherwise confirm it stays out.")
KEPT_OUT_RECORD = "Kept out by the clinician: line {lid}."
_KEEP_OUT = []          # '--keep-out L<n>' lines for one letter
_SENS_CUTS = []         # result lines cut as sensitive in the last letter built (screen only)
_KEPT_OUT = []          # of those, the lines whose flag --keep-out cleared (screen only)


KEEP_OUT_REFUSED = "Refused: line {lid} has nothing marked as sensitive, so `--keep-out` can't be used on it. Nothing was written. Check the line number, then run again."   # signed: Physician (safety) + Educator (wording), 14 Oct 2026


def sensitive_result_lines(text, include=(), patient=False):
    """Results lines a clinician letter would cut as sensitive (for check_template's --keep-out check)."""
    if patient:
        return []
    inc = {i.lower() for i in include}
    linked = load_linked()
    out = []
    for lid, (kind, body) in _source_lines(text).items():
        if kind != "result":
            continue
        cats = {sensitive_cat(c) for c in CLAUSE.findall(body)} - {None}
        cats |= {c for c, v in linked.items() for w in list(v.get("terms", [])) + list(v.get("medicines", []))
                 + list(v.get("brands", {})) if _term_re(w).search(body)}
        if any(c.lower() not in inc for c in cats):
            out.append(lid)
    return out


def flag_cuts(md, dropped, flags, title, text=None):
    """Every cut of a sensitive item carries a flag that blocks --final (line ids only). A result line cut as
    sensitive gets a cut record and its own flag, both on screen only; --keep-out L<n> clears that one flag."""
    lids = list(dict.fromkeys(i for d in dropped if any(c in d for c in SENSITIVE)
                              for i in re.findall(r"\b[LR]\d+\b", d.split(":")[0])))
    flags = [] if flags is None else flags
    if text is not None:
        src = _source_lines(text)
        results = [l for l in lids if src.get(l, (None,))[0] == "result"]
        lids = [l for l in lids if l not in results]
        for l in results:
            _SENS_CUTS.append(l)
            if l in _KEEP_OUT:
                _KEPT_OUT.append(l)
            else:
                flags.append(SENS_RESULT_NOTICE.format(lid=l))
    flags.extend(CUT_NOTICE.format(lid=l) for l in lids)
    return md          # round 11 freeze: the cut flag is on screen only and blocks --final; never in letter.md


def mark_linked_items(text, to, dropped):
    """A cut item is present even when its diagnosis is worded unusually or absent: any refuse-level term, listed
    medicine or brand in the notes marks it, for a reader who doesn't need it. Flag-only words never do."""
    src = _source_lines(text)
    for cat, v in load_linked().items():
        if relevant(cat, to):
            continue
        names = list(v.get("terms", [])) + list(v.get("medicines", [])) + list(v.get("brands", {}))
        lids = [lid for lid, (_, body) in src.items() if any(_term_re(w).search(body) for w in names)]
        if lids and not any(cat in d for d in dropped):
            dropped.append(f"{','.join(lids)}: {cat} linked words, not relevant to {to}")


def _cut_items(dropped):
    linked = load_linked()
    return {cat: v for cat, v in linked.items() if any(cat in d for d in dropped)}


def _source_lines(text):
    """{Lx: (tag kind or None, body)} using parse() numbering."""
    out = {}
    for e in parse(text)[0]:
        for lid, line in e["lines"]:
            parts = split_med_lists([(lid, line)])
            kind, body = classify(parts[0][1])
            if len(parts) > 1:
                body = " || ".join(classify(p)[1] for _, p in parts)
            out[lid] = (kind, body)
    return out


def _line_ids(line):
    _, ids = split_ids(line)
    return [i.strip() for i in ids.strip(" []|").split(",") if i.strip()]


def _body_lines(md):
    """Yield (index, line) for letter body lines that carry source ids."""
    lines = md.split("\n")
    for n, line in enumerate(lines):
        if _line_ids(line):
            yield n, line


def _add_flag(line, flag):
    text, ids = split_ids(line)
    return f"{text} {flag}{ids}"


def flag_long_sentences(md, flags):
    lines = md.split("\n")
    for n, line in list(_body_lines(md)):
        text, _ = split_ids(line)
        text = re.sub(r"^(- |\d+\. |\| )", "", FLAG_ANY.sub("", text)).replace(" | ", " ")
        if any(len(s.split()) > SENTENCE_LIMIT for s in re.split(r"(?<=[.!])\s+", text)):
            lines[n] = _add_flag(line, SENTENCE_FLAG)
            if flags is not None:
                flags.append(SENTENCE_NOTICE.format(lid=_line_ids(line)[-1]))
    return "\n".join(lines)


def _shows_reason(line, meds):
    """True when the medicine line itself still states the sensitive reason (e.g. a patient's own letter)."""
    body = FLAG_ANY.sub("", split_ids(line)[0])
    for m in meds:
        body = _term_re(m).sub(" ", body)
    return bool(sensitive_cat(body))


def flag_linked_medicines(md, text, dropped, flags):
    items = _cut_items(dropped)
    meds = _meds_of(items)                                # no cut items: no medicines, so nothing is flagged
    soft = [t for v in items.values() for t in v.get("flag_terms", [])]
    src = _source_lines(text)
    lines = md.split("\n")
    for n, line in list(_body_lines(md)):
        hit = [i for i in _line_ids(line) if i in src and src[i][0] in MED_TAGS
               and any(_term_re(m).search(src[i][1]) for m in meds)]
        if hit and any(_term_re(m).search(line) for m in meds) and not _shows_reason(line, meds):
            if flags is not None:                         # H22: on screen only, never in letter.md
                flags.append(MED_NOTICE.format(lid=hit[-1]))
    return "\n".join(lines)


LEFT_OUT_FLAG = "[flag: sentence left out]"
MED_PART_FLAG = "[flag: part of medicine entry left out]"
MED_PART_NOTICE = ("Flag: part of the medicine entry on line {lid} was left out to keep something private. "
                   "The medicine and its dose are still listed. Check nothing the reader needs was lost.")
LEFT_OUT_NOTICE = ("Flag: a sentence on line {lid} was left out to keep something private. "
                   "Check it doesn't hold a safety fact the reader needs.")
DRUG_FLAG_RX = r"(?: \[flag: [^\]]+\])?"


def _brand_map(items):
    return {b: g for v in items.values() for b, g in v.get("brands", {}).items()}


CUT_CHANGE_REASON = " Reason for change: reason not included."


NO_REASON_CHANGE = " Reason for change: not documented."
NO_REASON_NOTICE = "No reason was written for line {lid}."   # round 11: on screen only, does not block --final


def drop_cut_change_reasons(md, flags):
    """H22/H29: a stop or change whose reason was cut prints in full with no reason and no marker; the medicine
    flag is on screen only. Matching-line rule (round 11): a stop or change with no reason written prints exactly the
    same line, with a non-blocking notice on screen only. Never called for patient letters."""
    out = []
    for line in md.split("\n"):
        if NO_REASON_CHANGE in line and line.startswith(("- Changed", "- Stopped", "- Started", "- Restarted")):
            line = line.replace(NO_REASON_CHANGE, "")
            ids = _line_ids(line)
            if flags is not None and ids:
                flags.append(NO_REASON_NOTICE.format(lid=ids[-1]))
        if CUT_CHANGE_REASON in line:
            line = line.replace(CUT_CHANGE_REASON, "")
            ids = _line_ids(line)
            if flags is not None and ids:
                flags.append(MED_NOTICE.format(lid=ids[-1]))
        out.append(line)
    return "\n".join(out)


def apply_cut_rule(md, text, dropped, flags, title=None):
    """For a cut item (never in patient letters):
    - medication line: a listed brand becomes its generic; a reason naming the cut item becomes 'reason not included';
      other linked words on that line are left out; the medicine is flagged.
    - any other line: every sentence holding a linked word (term, flag-only term, medicine or brand) is left out, flagged.
    Refused only for a listed brand with no generic in the map."""
    flags = [] if flags is None else flags
    for d in dropped:                                     # H22: a reason cut from a medicine line: screen flag only
        m = re.match(r"^([LR]\d+(?:,[LR]\d+)*): indication for .* cut \(", d)
        if m:
            flags.append(MED_NOTICE.format(lid=m.group(1).split(",")[-1]))
    items = _cut_items(dropped)
    if not items:
        return md
    brands = _brand_map(items)
    generics = [m for v in items.values() for m in v.get("medicines", [])]
    hard = [t for v in items.values() for t in v.get("terms", [])]
    words = hard + [t for v in items.values() for t in v.get("flag_terms", [])]
    linked = sorted(set(words + generics + list(brands)))      # only ever tested for a match, so order is free
    rx = lambda ws: re.compile("|".join(term_pattern(w) for w in ws), re.I)
    any_linked = rx(linked)
    any_word = rx(sorted(set(hard), key=len, reverse=True)) if hard else None      # left out even on a medication line
    gen_rx = rx(sorted(set(generics + list(brands.values()))))   # fullmatch only; an empty list matches no word
    src = _source_lines(text)
    lines, left_out = md.split("\n"), []
    body_of = lambda i: src.get(i, (None, ""))[1]
    for n, line in list(_body_lines(md)):
        ids = _line_ids(line)
        text_part, id_part = split_ids(line)
        lead = re.match(r"^(- |\d+\. |\| )?", text_part).group(0)
        body = text_part[len(lead):]
        med_ids = [i for i in ids if src.get(i, (None, ""))[0] in MED_TAGS]
        if med_ids:
            if not any_linked.search(FLAG_ANY.sub("", body)) and "reason not included" not in body:
                continue
            for b, g in brands.items():
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(b)}(?![A-Za-z0-9])", body, re.I):
                    if not g:
                        lid = next((i for i in ids if re.search(re.escape(b), body_of(i), re.I)), ids[0])
                        raise Refused(LINKED_REFUSED.format(lid=lid))
                    body = re.sub(rf"(?<![A-Za-z0-9]){re.escape(b)}(?![A-Za-z0-9]){DRUG_FLAG_RX}", g, body, flags=re.I)
            # the medicine points to the cut item, so its stated reason is dropped entirely, with no marker (H22)
            body = re.sub(r"\s*\(for [^()]*\)", "", body)
            # a kept stop or change reason still goes through the cut rules (round 10)
            body = re.sub(r"Reason for change: ([^\[]*?)\.(?=\s|$)",
                          lambda m: "Reason for change: reason not included." if any_linked.search(m.group(1))
                          else m.group(0), body)
            if any_word:                                 # other linked words on the medication line: only that part goes
                bare = lambda p: FLAG_ANY.sub("", p)          # hard terms never include a bare generic
                parts = re.split(r",\s*(?![^\[]*\])(?![^()]*\))", body)       # not inside a flag or bracket
                first, rest = parts[0], parts[1:]
                kept = [p for p in rest if not (any_word.search(bare(p)) and p.strip() != "reason not included")]
                orig_first = first
                if any_word.search(bare(first)):
                    if not any_word.search(first):           # a flag sits inside the term: match on plain text
                        first = bare(first)
                    drug_word = FLAG_ANY.sub("", first).split()[0] if FLAG_ANY.sub("", first).split() else ""
                    first = re.sub(r"\s*\([^()]*\)", lambda m: "" if any_word.search(bare(m.group(0))) else m.group(0), first)
                    first = re.sub(r"\s+", " ", any_word.sub(lambda m: m.group(0) if gen_rx.fullmatch(m.group(0))
                                                            else "", first)).strip()
                    if not drug_word or not re.search(rf"(?<![A-Za-z0-9]){re.escape(drug_word)}(?![A-Za-z0-9])", first):
                        raise Refused(LINKED_REFUSED.format(lid=med_ids[0]))   # the medicine itself would disappear
                if len(kept) < len(rest) or first != orig_first:
                    body = ", ".join([first] + [p.strip() for p in kept])
                    body = f"{body} {MED_PART_FLAG}"
                    flags.append(MED_PART_NOTICE.format(lid=med_ids[0]))
            flags.append(MED_NOTICE.format(lid=med_ids[0]))      # H22: the medicine flag is on screen only
            lines[n] = lead + body + id_part
            continue
        if all(src.get(i, (None,))[0] == "allergy" for i in ids):   # allergies are safety facts: never left out
            if any_linked.search(FLAG_ANY.sub("", body)) and MED_FLAG not in body:
                lines[n] = lead + body + f" {MED_FLAG}" + id_part
                flags.append(MED_NOTICE.format(lid=ids[0]))
            continue
        sents = re.split(r"(?<=[.!?])\s+", body)
        keep = [x for x in sents if not any_linked.search(FLAG_ANY.sub("", x))]
        if len(keep) < len(sents):
            left_out.append(next((i for i in ids if any_linked.search(body_of(i))), ids[0]))
            lines[n] = (lead + " ".join(keep) + id_part) if keep else None
    if left_out:
        flags.extend(LEFT_OUT_NOTICE.format(lid=lid) for lid in dict.fromkeys(left_out))
        head = f"**Letter:** {title}" if title else None
        out = [l for l in lines if l is not None]
        i = next((k for k, l in enumerate(out) if head and l.startswith(head)), 0)
        out[i] = f"{out[i]} {LEFT_OUT_FLAG}"
        return "\n".join(out)
    return "\n".join(lines)


def refuse_missing_required(md, text, pending_ids=None):
    """Every documented allergy reaction and every named owner of a pending test must reach the letter."""
    plain = re.sub(r"\s+", " ", FLAG_ANY.sub("", md)).lower()
    for lid, (kind, body) in _source_lines(text).items():
        need = []
        if kind == "allergy":
            need = [r for _, r in (split_reaction(p) for p in re.split(r"[,;](?![^()]*\))", body))
                    if r and not NO_REACTION.match(r)]
        elif kind == "pending" and (pending_ids is None or lid in pending_ids):
            parts = re.split(r";\s*(?:follow-up|who|f/u)\s*:\s*", re.sub(r";\s*sent:\s*\S+", "", body), flags=re.I)
            who = parts[1].strip() if len(parts) > 1 else split_owner(parts[0])[1]
            need = [who] if who else []
        for x in need:
            if re.sub(r"\s+", " ", x).lower() not in plain:
                raise Refused(REQUIRED_REFUSED.format(lid=lid))


PENDING_NONE_REFUSED = 'Refused: line {lid} has a test still pending, but this letter says none are. Nothing was written. Add the pending test to the letter, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
QUESTION_FLAG = "[flag: no clear question]"
QUESTION_NOTICE = "Flag: this referral has no clear question. Add one before it's signed."
QUESTION_NOTICE_ANY = "Flag: this letter has no clear question. Add one before it's signed."
FINAL_REFUSED = ("--final refused: {n} flag(s) still open. Confirm each meaning with "
                 '--resolve "AB=meaning" (or edit the notes), then run --final again.')


COURSE_RX = re.compile(r"\bfor\s+\d+\s+(?:more\s+)?(?:days?|weeks?|months?)\b", re.I)
COURSE_MISSING = 'Refused: the medicine on line {lid} has a course length in the notes that this letter leaves out. Nothing was written. Add it to the letter, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
MED_MISSING = 'Refused: a medicine on line {lid} of the notes is missing from this letter. Nothing was written. Add it to the letter, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
ALLERGY_MISSING = 'Refused: an allergy on line {lid} of the notes is missing from this letter. Nothing was written. Add it to the letter, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
MAX_MISSING = 'Refused: the medicine on line {lid} has a maximum dose in the notes that this letter leaves out. Nothing was written. Add it to the letter, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
# Signed frequency equivalents (Physician, 13 Oct 2026). Nothing else counts; od has none.
FREQ_SIGNED = {"nocte": ["at night"], "mane": ["in the morning"], "daily": ["once a day", "once daily"],
               "bd": ["twice a day"], "tds": ["three times a day"], "qid": ["four times a day"], "prn": ["when needed"],
               "weekly": [], "od": []}
MULTI_DAILY = re.compile(r"\b(?:twice|three times|four times|once)\s+daily\b")
MAX_RX = re.compile(r"\bmax(?:imum)?\.?\s+(?:dose\s+)?(?:of\s+)?\d+(?:\.\d+)?\s?(?:mg|mcg|g|ml|tablets?|doses?)\b"
                    r"(?:\s*(?:in|per|/|every)\s*(?:24\s?(?:h|hrs?|hours?)|a day|day))?", re.I)


def _norm(s):
    s = re.sub(r"\s+", " ", FLAG_ANY.sub("", s)).lower()
    return DOSE_RX.sub(lambda m: f"{m.group(1)}{m.group(2).lower().rstrip('s')}", s)


MULTI_FREQ = re.compile(r"\b(once|twice|three times|four times)\s+(?:daily|a day)\b", re.I)
MULTI_TOK = {"once": "daily", "twice": "bd", "three times": "tds", "four times": "qid"}


def note_freqs(part):
    """[(canonical token, as written)] for the frequencies in a medicine line."""
    out = [(MULTI_TOK[m.group(1).lower()], m.group(0).lower()) for m in MULTI_FREQ.finditer(part)]
    rest = MULTI_FREQ.sub(" ", part)
    return out + [(m.group(1).lower(), m.group(1).lower()) for m in FREQ_RX.finditer(rest)]


def _freq_present(tok, plain, written=None):
    tok = tok.lower()
    if written and written != "daily" and re.search(rf"(?<![a-z]){re.escape(written)}(?![a-z])", plain):
        return True
    if tok == "daily":
        own = MULTI_DAILY.sub(lambda m: " once a day " if m.group(0).startswith("once") else " ", plain)
        return bool(re.search(r"(?<![a-z])daily(?![a-z])", own)) or any(p in own for p in FREQ_SIGNED["daily"])
    if re.search(rf"(?<![a-z]){re.escape(tok)}(?![a-z])", plain):
        return True
    single = load_single_meaning().get(tok, {})
    return any(p in plain for p in FREQ_SIGNED.get(tok, []) + ([single["patient"]] if single.get("patient") in
                                                                FREQ_SIGNED.get(tok, []) else []))


def _drug_present(body, plain):
    found = drugs_in(body)
    if found:
        return all(re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", plain) or
                   re.search(rf"(?<![a-z0-9]){re.escape(g)}(?![a-z0-9])", plain) for w, g in found)
    first = next((w for w in re.findall(r"[A-Za-z][A-Za-z-]+", body)), None)
    return first is None or re.search(rf"(?<![a-z0-9]){re.escape(first.lower())}(?![a-z0-9])", plain)


RESULT_MISSING = 'Refused: a result on line {lid} of the notes is missing from this letter. Nothing was written. Add it to the letter, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026


REASON_NOTICE = "Flag: the reason for the medicine on line {lid} has no signed plain words. Check it."
OPEN_NOTICE = "Flag: line {lid} has an open flag. Check it."


def printed_flags(flags, md):
    """The flag lines printed beside a letter: one per distinct notice, plus one for any flag in the letter that no
    notice names (so every flag in the letter is printed). --final counts exactly these lines."""
    out = list(dict.fromkeys(line_only_flag(f) for f in flags))
    named = {i for f in out for i in re.findall(r"\b[LR]\d+\b", f)}
    for line in md.split("\n"):
        found = FLAG_ANY.findall(line)
        if not found:
            continue
        ids = [i for i in _line_ids(line)]
        if not ids:
            if not out:                                    # a header flag always has its notice; keep fail-safe
                out.append("Flag: this letter has an open flag. Check it.")
            continue
        for fl in found:
            if fl.strip() == REASON_FLAG:
                notice = REASON_NOTICE.format(lid=ids[-1])
            elif any(i in named for i in ids):
                continue
            else:
                notice = OPEN_NOTICE.format(lid=ids[-1])
            if notice not in out:
                out.append(notice)
    return out


SCREEN_ONLY_PREFIXES = tuple(x.split("{")[0] for x in (SENS_RESULT_NOTICE, MED_NOTICE, CUT_NOTICE))


class BuildResult(tuple):
    """What build() returns: unpacks as (md, dropped) as before, and also carries every flag raised while building,
    including the screen-only ones that never appear in the letter. finalise() takes only this."""
    def __new__(cls, md, dropped, flags, privacy=None, patient=False):
        r = super().__new__(cls, (md, dropped))
        r.md, r.dropped, r.flags = md, dropped, list(flags)
        r.privacy, r.patient = privacy, patient          # check 19: explicit field, never inferred from the notes
        return r


# Check 19 (privacy line). Wording reviewed by Patient and signed by Physician (14 Oct 2026); change it here only.
PRIVACY_BLOCKED = {
    "missing": "Blocked: this letter needs the privacy line, and it's missing.",
    "unset": "Blocked: the privacy field isn't set. Mark whether this letter needs the privacy line.",
}


def privacy_check(result):
    """Check 19: reads only the explicit privacy field (True/False/None) and the finished letter."""
    if result.privacy is True and PATIENT_PRIVACY not in result.md:
        raise Refused(PRIVACY_BLOCKED["missing"])
    if result.privacy is None and result.patient:
        raise Refused(PRIVACY_BLOCKED["unset"])


FINAL_RAW_REFUSED = 'Refused: `--final` needs the built letter with its flags, not letter text on its own. Nothing was written. Build the letter first, then run `--final` on it.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026


def finalise(result):
    """Remove [Lx]/[Rx] source tags only. Takes only a build() result, so screen-only flags (cuts, sensitive results,
    medicines) can never be lost: refused while any flag is open, in the letter or on screen. A cut stays open until
    the brief's Include: line or --keep-out L<n> clears it. The draft mark and footer stay until the signing
    clinician removes them."""
    if not isinstance(result, BuildResult):
        raise Refused(FINAL_RAW_REFUSED)
    md = result.md
    shown = printed_flags(result.flags, md)
    screen_only = sum(f.startswith(SCREEN_ONLY_PREFIXES) for f in shown)
    n = (len(shown) or len(FLAG_ANY.findall(md))) if (FLAG_ANY.search(md) or screen_only) else 0
    if n:
        raise Refused(FINAL_REFUSED.format(n=n))
    privacy_check(result)
    return re.sub(r" \[[LR]\d+(?:, [LR]\d+)*\]", "", md)


def make_letter(text, letter_type, to, recipient=None, audience=None, template=None, brand=None):
    return build(text, letter_type, to, recipient, audience, template, brand)[0]


# ---------- independent post-write check ----------
# Reads the notes with its own line reader. It never uses parse(), classify(), cumsum() or any parser output, so
# a parser bug (a merged line, a misread heading) cannot hide from it. Only fixed vocabularies are shared
# (drug names, dose and frequency patterns, linked terms).
V_DATE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\b[\s:,-]*")
V_BULLET = re.compile(r"^\s*(?:[*\u2022\u00b7]|-(?=\s))\s*")
V_HEAD = re.compile(r"^(?P<h>[A-Za-z][A-Za-z /'-]{0,40}?)\s*(?::|\s[-\u2013\u2014]+(?=\s|$)|\s*(?:->|[\u2192\u279d]))\s*(?P<b>.*)$")
V_ALLERGY_HEAD = re.compile(r"\b(?:allerg\w*|sensitivit\w*|intoleran\w*|reactions?|anaphyla\w*|adverse)\b", re.I)
V_STOP_HEAD = re.compile(r"\b(?:stop\w*|ceased?|withheld|discontinued)\b", re.I)
V_MED_HEAD = re.compile(r"\b(?:med\w*|drugs?|chart|prescri\w*|tablets?|regimen|rx|takes|taking|start|restart|change)\b", re.I)
V_NIL = re.compile(r"^\s*(?:nil|none|nkda|nkfa|no known(?: drug)? allerg\w*|no allerg\w*)(?:\s+known)?\s*\.?\s*$", re.I)
V_REACT = re.compile(r"^\s*(?P<a>.+?)\s*(?:\((?P<p>[^)]*)\)\s*$|\s*(?:->|[\u2192\u279d])\s*|\s[-\u2013\u2014]+\s+|\s*:\s+|"
                     r"\s*=\s*|\s+/\s+|\s+(?:causes|caused|gives|gave)\s+)(?P<r>.*?)\s*$", re.I)
V_RESULT = re.compile(r"(?<![\d.])(?P<v>-?\d+(?:\.\d+)?)\s?(?P<u>[A-Za-z\u00b5]*/L)(?![A-Za-z])")
V_REASON = re.compile(r"(?:\s[-\u2013\u2014]+\s+|,\s*for\s+|\s+for\s+|\s*\((?:for\s+)?)(?P<r>[^()]+?)\)?\s*\.?$", re.I)
V_SPLIT = re.compile(r"[,;](?![^()]*\))")
LEAK_REFUSED = LINKED_REFUSED


def _v_heading(body):
    """(heading, rest) when the line opens with a short heading; never a medicine written with a dash reason."""
    m = V_HEAD.match(body)
    if not m:
        return None, body
    h = m.group("h").strip()
    if len(h.split()) > 4 or drugs_in(h) or FREQ_RX.search(h):
        return None, body
    return h, m.group("b").strip()


def read_notes(text):
    """[(lid, kind, body)] for every notes line, read on its own (no line is ever joined with another).
    kind: med, stop, allergy, result or None. A short medicine heading with nothing after it ('Current Rx',
    'Regular meds -') opens a block; indented or bulleted lines under it are medicines."""
    out, block = [], False
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            block = False if not raw.strip() else block
            continue
        lid = f"L{n}"
        indented = bool(re.match(r"^\s+\S", raw)) or bool(V_BULLET.match(raw) and raw.lstrip()[:1] in "*\u2022\u00b7")
        line = V_DATE.sub("", raw.strip())
        line = V_BULLET.sub("", line) if indented else line
        head, body = _v_heading(line)
        if head is None and len(line.split()) <= 4 and not re.search(r"\d", line) and V_MED_HEAD.search(line) \
                and not drugs_in(line):
            head, body = line, ""
        kind = None
        if head is not None and TAGS.get(head.lower()) == "result" and V_RESULT.search(body):
            out.append((lid, "result", body))            # H03: a tagged per-litre result (template Results: field)
            block = False
            continue
        if head is not None and TAGS.get(head.lower()) not in (None, "med", "stop", "allergy"):
            out.append((lid, None, line))            # a known non-medicine heading (Problem, Plan, Pending...)
            block = False
            continue
        if head is not None:
            if V_ALLERGY_HEAD.search(head):
                kind = "allergy"
            elif V_STOP_HEAD.search(head):
                kind = "stop"
            elif V_MED_HEAD.search(head):
                kind = "stop" if STOP_RX.search(body) else "med"
            if kind == "med" and not body:
                block = True
                out.append((lid, None, line))
                continue
        if kind is None and block and indented:
            kind = "stop" if STOP_RX.search(body) else "med"
        if not indented:
            block = False
        if kind is None:
            whole = line
            if PER_LITRE.search(whole) and not FREQ_RX.search(whole) and not DOSE_RX.search(whole):
                kind, body = "result", whole
            elif STOP_RX.search(whole) and drugs_in(whole):
                kind, body = "stop", whole
            elif DOSE_RX.search(whole) and not re.search(r"\b(?:sent|pending|awaiting|result)\b", whole, re.I):
                kind, body = "med", (body if head and drugs_in(body) else whole)
        out.append((lid, kind, body if kind else line))
    return out


def _v_med_parts(body):
    """Split a medicine list ('a 5 mg daily, b 10 mg bd') only when every part has its own dose or frequency."""
    parts = [p.strip() for p in V_SPLIT.split(body) if p.strip()]
    if len(parts) > 1 and all(DOSE_RX.search(p) or FREQ_RX.search(p) for p in parts):
        return parts
    return [body]


def _v_reason(part):
    m = V_REASON.search(part)
    if not m:
        return None
    r = m.group("r").strip(" .")
    return r if r and not DOSE_RX.search(r) and not FREQ_RX.search(r) and not STOP_RX.search(r) else None


def _v_allergies(body):
    """[(allergen as written, reaction or None)]; nil statements give []."""
    out = []
    for part in V_SPLIT.split(body):
        part = part.strip(" .")
        if not part or V_NIL.match(part):
            continue
        m = V_REACT.match(part)
        if m:
            r = (m.group("p") if m.group("p") is not None else m.group("r")).strip(" .")
            if r:
                out.append((m.group("a").strip(), None if NO_REACTION.match(r) else r))
                continue
        out.append((part, None))
    return out


def _v_cut_words(text, audience, include):
    """Cut categories present in the notes (never for the patient) -> list of regexes that mark a cut item."""
    if audience == "patient":
        return []
    inc = {i.lower() for i in include}
    rxs = []
    linked = load_linked()
    for cat, pats in SENSITIVE.items():
        if cat.lower() in inc:
            continue
        v = linked.get(cat, {})
        words = list(v.get("terms", [])) + list(v.get("flag_terms", [])) + list(v.get("medicines", [])) + \
            list(v.get("brands", {}))
        cat_rx = [re.compile(p, re.I) for p in pats] + [_term_re(w) for w in words]
        hard = [re.compile(p, re.I) for p in pats] + [_term_re(w) for w in words if w not in v.get("flag_terms", [])]
        if any(r.search(text) for r in hard):         # flag-only words never mark an item as present
            rxs += cat_rx
    return rxs


def _letter_lines(md):
    """[(section heading, ids, normalised text)] for each cited letter line."""
    out, sec = [], None
    for line in md.split("\n"):
        if line.startswith("## "):
            sec = line[3:].strip()
            continue
        ids = _line_ids(line)
        if ids:
            out.append((sec, ids, _norm(re.sub(r"^(- |\d+\. |\| )", "", split_ids(line)[0]))))
    return out


MED_SECTIONS = {h["meds"] for h in HEADINGS.values()}
ACTION_SECTIONS = {h["followup"] for h in HEADINGS.values()}
ACTION_MISSING = 'Refused: an action on line {lid} of the notes is missing from this letter. Nothing was written. Add it to the letter, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
CHANGE_SECTIONS = {h["med_changes"] for h in HEADINGS.values()}
ALLERGY_SECTIONS = {h["allergies"] for h in HEADINGS.values()}


def _v_key(part):
    found = drugs_in(part)
    if found:
        return found[0][1]
    w = re.findall(r"[A-Za-z][A-Za-z-]+", part)
    return w[0].lower() if w else part.lower()


def _v_med_ok(part, plain):
    if not _drug_present(re.sub(r"^\W+", "", part), plain):
        return False
    for m in MAX_RX.finditer(part):
        plain_max = re.sub(PLAIN[-1][0], PLAIN[-1][1], m.group(0))
        if _norm(m.group(0)) not in plain and _norm(plain_max) not in plain:
            return "max"
    for m in DOSE_RX.finditer(_norm(MAX_RX.sub(" ", part))):
        if not re.search(rf"(?<![\d.]){re.escape(m.group(0))}(?![a-z])", plain):
            return False
    return all(_freq_present(tok, plain, written) for tok, written in note_freqs(part))


WRONG_SECTION = 'Refused: {what} on line {lid} of the notes is in the wrong section of this letter. Nothing was written. Move it to the right section, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
SECTION_ND = 'Refused: the {sec} section says "not documented", but line {lid} of the notes has an entry for it. Nothing was written. Add that entry to the section, then run again.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026


def _section_says_nd(md, heads, empty):
    for head in heads:
        m = re.search(rf"^## {re.escape(head)}\n(.*?)(?=^## |^---|\Z)", md, re.S | re.M)
        if m and empty in m.group(1):
            return True
    return False


def verify_letter(md, text, audience="gp", include=(), collect=None):
    """Post-write check on the finished letter against the notes alone. Medicine: drug (generic ok), every dose,
    every frequency (or its signed plain words) and any maximum, in a current-medicines line citing that note line.
    Stop: drug + stop word in a medicines line. Allergy: allergen exactly as written + reaction when given.
    Per-litre result: value + unit. Non-patient letters: a cut reason or a cut sentence must not appear.
    collect: a list to receive every refusal (diagnostic use); otherwise the first one is raised."""
    def fail(msg):
        if collect is None:
            raise Refused(msg)
        collect.append(msg)
    lines = _letter_lines(md)
    whole = " ".join(t for _, _, t in lines)
    cut = _v_cut_words(text, audience, include)
    is_cut = lambda s: any(r.search(s) for r in cut)
    notes = read_notes(text)
    nd = {"med": _section_says_nd(md, MED_SECTIONS, EMPTY["meds"]),
          "allergy": _section_says_nd(md, ALLERGY_SECTIONS, EMPTY["allergies"])}
    for lid, kind, body in notes:                # a section may not say "not documented" when the notes have one
        if kind == "med" and nd["med"]:
            fail(SECTION_ND.format(sec="medicines", lid=lid)); nd["med"] = False
        elif kind == "allergy" and nd["allergy"]:
            fail(SECTION_ND.format(sec="allergies", lid=lid)); nd["allergy"] = False
    later = {}                                   # drug key -> number of later medicine or stop lines naming it
    for lid, kind, body in notes:
        if kind in ("med", "stop"):
            for part in _v_med_parts(body):
                later.setdefault(_v_key(part), []).append(lid)
    raw_lines = text.splitlines()
    for n, l in enumerate(raw_lines, 1):                 # an action with its owner must sit in Actions
        if re.match(r"^\s*Plan:\s*.+\|\|", l):
            secs = [s for s, ids, _ in lines if f"L{n}" in ids]
            if not secs:
                fail(ACTION_MISSING.format(lid=f"L{n}"))
            elif any(s not in ACTION_SECTIONS for s in secs):
                fail(WRONG_SECTION.format(what="an action", lid=f"L{n}"))
    for lid, kind, body in notes:
        cites = [(s, t) for s, ids, t in lines if lid in ids]
        anywhere = " ".join(t for _, t in cites)
        if kind == "med":
            for part in _v_med_parts(body):
                superseded = later.get(_v_key(part), [lid])[-1] != lid      # same drug written again later
                in_meds = " ".join(t for s, t in cites if s in MED_SECTIONS | (CHANGE_SECTIONS if superseded else set()))
                ok = _v_med_ok(part, in_meds)
                for dm in COURSE_RX.finditer(part):          # a course length is kept exactly as written
                    if _norm(dm.group(0)) not in in_meds:
                        fail(COURSE_MISSING.format(lid=lid))
                if ok == "max":
                    fail(MAX_MISSING.format(lid=lid))
                elif not ok:
                    fail(WRONG_SECTION.format(what="a medicine", lid=lid) if _v_med_ok(part, anywhere) is True
                         else MED_MISSING.format(lid=lid))
                reason = _v_reason(part)
                if reason and cut and (is_cut(reason) or is_cut(part)) and _norm(reason) in anywhere:
                    fail(LEAK_REFUSED.format(lid=lid))
        elif kind == "stop":
            in_meds = " ".join(t for s, t in cites if s in MED_SECTIONS | CHANGE_SECTIONS)
            if not _drug_present(body, in_meds) or not STOP_RX.search(in_meds):
                fail(MED_MISSING.format(lid=lid))
        elif kind == "allergy":
            in_all = " ".join(t for s, t in cites if s in ALLERGY_SECTIONS)
            has = lambda a, r, where: _norm(a) in where and (not r or _norm(r) in where)
            for allergen, reaction in _v_allergies(body):
                if not has(allergen, reaction, in_all):
                    fail(WRONG_SECTION.format(what="an allergy", lid=lid) if has(allergen, reaction, anywhere)
                         else ALLERGY_MISSING.format(lid=lid)); break
        elif kind == "result":
            if is_cut(body):
                continue
            for m in V_RESULT.finditer(body):
                if not re.search(rf"(?<![\d.]){re.escape(m.group('v'))}(?![\d])", anywhere) or \
                        _norm(m.group("u")) not in anywhere:
                    fail(RESULT_MISSING.format(lid=lid)); break
        elif is_cut(body):                       # an empty cut list cuts nothing
            for sent in re.split(r"(?<=[.!?])\s+", body):
                s = _norm(sent).strip(" .")
                if is_cut(sent) and s and (s in whole or any(is_cut(t) for _, t in cites)):
                    fail(LEAK_REFUSED.format(lid=lid)); break
    return True


def refuse_missing_medicines(md, text, audience="gp", include=()):
    """Kept name for older callers: the independent post-write check."""
    return verify_letter(md, text, audience, include)


# ---------- fixed notes template (round 10) ----------
# Fields start a line, in this order. List fields take one item per line starting with '- '.
TEMPLATE_FIELDS = ("Patient", "Date", "Recipients", "Question", "Problems", "History", "Medicines", "Allergies",
                   "Results", "Pending", "Actions")
TEMPLATE_REQUIRED = TEMPLATE_FIELDS             # every heading must be there; a list may be empty
TEMPLATE_LISTS = ("Problems", "Medicines", "Allergies", "Results", "Pending", "Actions")
TEMPLATE_REFUSED = "Refused: line L{n} of the notes doesn't follow the template. Nothing was written. Rewrite the line to follow the template, then run again."   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
FIELD_RX = re.compile(r"^(?P<f>[A-Za-z]+)\s*:\s*(?P<v>.*)$")
NKA_RX = re.compile(r"^(?:no known(?: drug)? allergies|nkda|nil known(?: allergies)?)\.?$", re.I)


def from_template(text):
    """Validate notes against the fixed template and turn each field into the tagged line the reader uses.
    Line numbers never move. The Recipients line is blanked: it never reaches the letter.
    Refuses on a missing required field, an unknown or out-of-order field, or a list item without '- '."""
    lines = text.splitlines()
    out, pos, field, seen = [], -1, None, []
    _ROUTES.clear()
    def bad(n):
        raise Refused(TEMPLATE_REFUSED.format(n=n))
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            out.append(""); continue
        if line.startswith("- "):
            if field not in TEMPLATE_LISTS:
                bad(n)
            item = line[2:].strip()
            if not item:
                bad(n)
            conv = _template_item(field, item)
            if conv is None:
                bad(n)
            if field == "Medicines" and med_item(item)["route"]:
                _ROUTES[f"L{n}"] = med_item(item)["route"]
            out.append(conv); continue
        if field == "Allergies" and NKA_RX.match(line) and out and out[-1] == "#field Allergies":
            out.append("Allergy: nil known"); continue           # 'No known allergies' / NKDA / nil known, no dash
        m = FIELD_RX.match(line)
        f = m.group("f").capitalize() if m else None
        if f not in TEMPLATE_FIELDS:
            bad(n)
        i = TEMPLATE_FIELDS.index(f)
        if i <= pos:
            bad(n)
        for req in TEMPLATE_REQUIRED:                        # a required field may not be skipped
            if TEMPLATE_FIELDS.index(req) < i and req not in seen:
                bad(n)
        pos, field, v = i, f, m.group("v").strip()
        seen.append(f)
        if f in TEMPLATE_LISTS:
            if v and not (f == "Allergies" and NKA_RX.match(v)):
                bad(n)
            out.append("Allergy: nil known" if v else f"#field {f}"); continue
        if f == "Patient":
            if not SYNTH.fullmatch(v):
                bad(n)
            out.append(f"Patient: {v}")
        elif f == "Date":
            if not valid_date(v):
                bad(n)
            out.append(v)
        elif f == "Recipients":
            if not v:
                bad(n)
            if RECIPIENT_SPLIT.search(v):
                raise Refused(RECIPIENTS_REFUSED.format(n=n))
            out.append("#")                                   # the only place a recipient may appear; never in the letter
        elif f == "Question":
            if not v:
                bad(n)
            out.append("#" if v.lower().strip(" .") == "none" else f"Question: {v}")
        elif f == "History":
            out.append(f"History: {v}" if v else "#")
    missing = [r for r in TEMPLATE_REQUIRED if r not in seen]
    if missing:
        bad(len(lines) + 1 if lines else 1)
    return "\n".join(out) + "\n"


TEMPLATE_SECTIONS = {"Problems": ("problems",), "Medicines": ("meds",), "Allergies": ("allergies",),
                     "Results": ("results",), "Pending": ("pending",), "Actions": ("followup",)}
NONE_RECORDED = "none recorded."
EMPTY_PATIENT = {"allergies": "No allergies were written in your notes. If you have any, tell your next doctor.",
                 "meds": "No medicines were written in your notes. If you take any, tell your next doctor."}
EMPTY_FIELD_FLAG = "[flag: field empty]"
EMPTY_FIELD_NOTICE = {"allergies": "Flag: the allergies field on line {lid} is empty. Check allergies before this letter is used.",
                      "meds": "Flag: the medicines field on line {lid} is empty. Check medicines before this letter is used."}
_EMPTY_LINES = {}       # section -> 'L<n>' of its empty template heading


def empty_template_sections(converted):
    """Sections whose template list field has a heading and no items: the letter says 'none recorded'."""
    lines = converted.splitlines()
    out = set()
    _EMPTY_LINES.clear()
    for i, l in enumerate(lines):
        if l.startswith("#field "):
            nxt = next((x for x in lines[i + 1:] if x.strip()), "")
            if nxt.startswith("#") or not nxt or bool(re.match(DATE, nxt)):
                out.update(TEMPLATE_SECTIONS[l.split(" ", 1)[1]])
                for sec in TEMPLATE_SECTIONS[l.split(" ", 1)[1]]:
                    _EMPTY_LINES[sec] = f"L{i + 1}"
    return out


RECIPIENTS_REFUSED = 'Refused: line L{n} lists more than one recipient. Nothing was written. Write a separate letter for each reader.'   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
RECIPIENT_SPLIT = re.compile(r"[,;&/+]|\band\b|\balso\b|\bplus\b", re.I)

# The medicine-line grammar lives here only (the reason marker may still change):
# '- drug dose frequency [route: signed list, any other word flagged] [max X in 24 hours | max X per <period>] [for N days] [reason: <why>]
#    [; stopped|ceased|withheld <date> - <stop reason>]'. Parts in this order; anything else doesn't fit.
MED_REASON_MARK = "reason:"
_FREQS = (r"(?:once daily|twice daily|three times daily|four times daily|four times a day|daily|bd|tds|qid|nocte|mane|prn|weekly|od"
          r"|fortnightly|monthly|every\s+(?:other\s+)?(?:\d+\s+)?(?:hours?|days?|weeks?|months?)"
          r"|once\s+a\s+(?:week|month|fortnight))")   # off-list frequencies are kept as written, never flagged
# 'four times a day' is the signed patient wording of qid in single_meaning.toml (signed 13 Oct 2026).
MED_ITEM = re.compile(
    r"^(?P<drug>[A-Za-z][\w/-]*(?:\s+(?!\d)[A-Za-z][\w/-]*)*?)"
    r"(?:\s+(?P<dose>\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?\s?(?:mg|mcg|micrograms?|g|mL|ml|units?|IU|%|puffs?|tablets?|drops?)?"
    r"(?:\s+(?:one|two)\s+puffs?)?))?"
    r"\s+(?P<freq>" + _FREQS + r"(?:\s+and\s+" + _FREQS + r")?)"
    r"(?:\s+(?P<route>by mouth|(?!(?:max|maximum|for|reason|and|stopped|ceased|withheld)\b)[A-Za-z][A-Za-z-]*(?![\w:])))?"
    r"(?:\s+(?P<max>max(?:imum)?\s+\d+(?:\.\d+)?\s?(?:mg|mcg|micrograms?|g|mL|ml|units?|tablets?|doses?|puffs?|drops?)?"
    r"\s+(?:in\s+24\s+hours|(?:per|in|every)\s+(?:\d+\s+)?[a-z]+)))?"
    r"(?:\s+(?P<dur>for\s+\d+\s+(?:more\s+)?days?))?"
    r"(?:\s+" + re.escape(MED_REASON_MARK) + r"\s*(?P<reason>(?:(?!\bmax(?:imum)?\s+\d|\bfor\s+\d+\s+(?:more\s+)?days?\b)[^;])+?))?"
    r"(?:\s*;\s*(?P<stop>stopped|ceased|withheld)\s+(?P<sdate>\d{4}-\d{2}-\d{2})(?:\s+-\s+(?P<sreason>.+?))?)?\s*$", re.I)


# Signed route list (ruling 14 Oct 2026): kept exactly as written in the route slot of a medicine line. Any other
# word in that slot is flagged, not refused. Patient letters keep the short form and add the signed plain words.
ROUTES_FILE = HERE / "routes.toml"
_ROUTE_TABLE = tomllib.loads(ROUTES_FILE.read_text(encoding="utf-8"))["routes"]   # route words live in the list only
ROUTES = tuple(_ROUTE_TABLE)
ROUTE_PLAIN = {r: p for r, p in _ROUTE_TABLE.items() if p}   # a plain-words route ('by mouth') gets no brackets
ROUTE_FLAG = "[flag: route not on the signed list]"
ROUTE_NOTICE = "Flag: the route on line {lid} isn't on the signed route list. Check it."
_ROUTES = {}            # 'L<n>' -> route word as written, for template medicine lines of one letter


def route_signed(route):
    return route in ROUTES


def render_route(route, audience):
    """The route as written; a patient letter adds the signed plain words after a signed route."""
    if audience == "patient" and route in ROUTE_PLAIN and ROUTE_PLAIN[route] != route:
        return f"{route} ({ROUTE_PLAIN[route]})"
    return route


def med_item(item):
    """Parsed template medicine line as a dict, or None when the parts don't fit the grammar in order."""
    m = MED_ITEM.match(item)
    return m.groupdict() if m else None


def _template_item(field, item):
    if field == "Problems":
        return f"Problem: {item}"
    if field == "Medicines":
        m = med_item(item)
        if m is None:
            return None
        core = " ".join(x for x in (m["drug"], m["dose"], m["freq"], m["route"], m["max"], m["dur"]) if x)
        if m["stop"]:
            return f"Stop: {core} {m['sdate']}" + (f" (reason: {m['sreason']})" if m["sreason"] else "")
        return f"Med: {core}" + (f" (for {m['reason']})" if m["reason"] else "")
    if field == "Allergies":
        return "Allergy: nil known" if NKA_RX.match(item) else f"Allergy: {item}"
    if field == "Results":
        return f"Result: {item}"
    if field == "Pending":
        parts = [p.strip() for p in item.split(",")]
        test = parts[0]
        sent = next((ISO.search(p).group(0) for p in parts[1:] if ISO.search(p)), None)
        who = ", ".join(p for p in parts[1:] if not re.match(r"^sent\b", p, re.I))
        return f"Pending: {test}" + (f"; sent: {sent}" if sent else "") + (f"; who: {who}" if who else "")
    if field == "Actions":
        a, _, who = item.rpartition(",")
        return f"Plan: {a.strip()} || {who.strip()}" if a.strip() else f"Plan: {item}"
    return item


RECIPIENTS_LINE = re.compile(r"^\s*recipients?\s*:\s*(?P<v>.*)$", re.I)


def pre_template_checks(raw, template=None, brief=None, patient=False, not_copy=()):
    """Copy, send and recipient checks on the raw notes. They run BEFORE the template check, so a template error
    can never hide them. The Recipients line is checked for one reader and then blanked (it is never a copy).
    Returns the --not-copy lines covered."""
    lines = raw.splitlines()
    for n, l in enumerate(lines, 1):
        m = RECIPIENTS_LINE.match(l)
        if m:
            if RECIPIENT_SPLIT.search(m.group("v")):
                raise Refused(RECIPIENTS_REFUSED.format(n=n))
            lines[n - 1] = "#"
    return refuse_copies("\n".join(lines) + "\n", template, brief, patient, not_copy)


def build_from_template(text, *a, **k):
    conv = from_template(text)
    _EMPTY_FIELDS.clear(); _EMPTY_FIELDS.update(empty_template_sections(conv))
    _TEMPLATE[:] = [True]
    try:
        return build(conv, *a, **k)
    finally:
        _EMPTY_FIELDS.clear(); _TEMPLATE.clear(); _ROUTES.clear()


# ---------- held-out harness ----------
def heldout(path):
    """Run every *.txt in an external folder. Print counts only, never note contents."""
    root = pathlib.Path(path).resolve()
    if root == HERE or HERE in root.parents:
        raise SystemExit("Held-out notes must live outside this repository.")
    files = sorted(root.glob("*.txt"))
    if not files:
        raise SystemExit("No *.txt held-out notes found.")
    stats = {"letter": 0, "refused": 0, "fail": 0}
    for f in files:
        text = f.read_text(encoding="utf-8")
        brief = dict(re.findall(r"^#\s*(type|to|audience|expect)\s*=\s*(.+?)\s*$", text, re.M))
        expect = brief.get("expect", "letter")
        musts = []
        exp = f.with_suffix(".expect")
        if exp.exists():
            musts = re.findall(r"^(must|must-not):\s*(.+?)\s*$", exp.read_text(encoding="utf-8"), re.M)
        try:
            md, _ = build(text, brief.get("type", "gp-update"), brief.get("to", DOMAIN["heldout"]["default_to"]),
                          audience=brief.get("audience"))
            ok = expect == "letter" and all((s in md) == (k == "must") for k, s in musts)
            stats["letter"] += 1
        except Refused:
            ok = expect == "refused"
            stats["refused"] += 1
        except Exception:
            ok = False
        if not ok:
            stats["fail"] += 1
            print(f"FAIL {f.name}", file=sys.stderr)
    n = len(files)
    print(f"held-out notes: {n}; letters: {stats['letter']}; refused: {stats['refused']}; "
          f"failures: {stats['fail']}; failure rate: {stats['fail'] / n:.1%}")
    return stats["fail"] / n


BRIEF_REFUSED = ('Refused: brief not understood. Write it as "<kind> to <reader>", for example '
                 '"discharge to GP" or "update to patient".')
SENSITIVE_REFUSED = ("Refused: line L{n} of the brief includes an item that isn't on the sensitive-items list. "
                     "Nothing was written. Remove the item from the brief, or use one from the list.")   # round 3; never echoes the brief
SENSITIVE_REFUSED_NO_LINE = "Refused: the brief includes an item that isn't on the sensitive-items list. Nothing was written. Remove the item from the brief, or use one from the list."   # signed: Physician (safety) + Educator (wording), 14 Oct 2026
BRIEF_KINDS = {
    "referral": "referral", "refer": "referral", "referral letter": "referral",
    "discharge": "discharge", "discharge summary": "discharge", "discharge letter": "discharge",
    "update": "gp-update", "gp update": "gp-update", "gp-update": "gp-update", "progress update": "gp-update",
    "clinic letter": "gp-update", "clinic update": "gp-update", "letter": "gp-update",
    "reply": "specialist-reply", "specialist reply": "specialist-reply", "specialist-reply": "specialist-reply",
    "response": "specialist-reply", "answer": "specialist-reply",
}


def parse_brief(core):
    """'<kind> to <reader>' -> (type, Reader), or (None, None). Never echoes the brief."""
    m = re.match(r"^\s*(?:an?\s+)?(?P<kind>[A-Za-z -]+?)\s+(?:to|for)\s+(?:the\s+(?=patient\b))?(?P<reader>\S.*?)\s*\.?\s*$",
                 core or "", re.I)
    if not m:
        return None, None
    kind = BRIEF_KINDS.get(re.sub(r"\s+", " ", m.group("kind").strip().lower()))
    if not kind:
        return None, None
    r = m.group("reader")
    if re.search(r"[.:;,]", r) or len(r.split()) > 4:      # brief text never goes into 'To:'
        return None, None
    return kind, r[:1].upper() + r[1:]


def line_only_flag(f):
    """Print every flag beside a letter as fixed wording plus line ids; never source words."""
    if f.startswith(("Flag: ", NO_REASON_NOTICE.split("{")[0])):
        return f
    ids = re.findall(r"\b[LR]\d+\b", f)
    where = f"on line {', '.join(dict.fromkeys(ids))}" if ids else "in this letter"
    if "(drug name, check spelling)" in f:
        return f"Flag: a drug name {where} may be misspelled. Check it."
    if re.match(r"^\d", f):
        return f"Flag: a dose unit {where} is easy to misread. Check it."
    return f"Flag: an abbreviation {where} has no confirmed meaning. Confirm it with --resolve."


def read_brief(brief):
    """The brief as (type, reader, include items, copy text or None). Refuses with BRIEF_REFUSED.
    Shared by the letter command and the standalone template checker."""
    numbered = [(n, l.strip()) for n, raw in enumerate(brief.splitlines(), 1)
                for l in re.split(r"[.;]\s*(?=include\s*:)", raw, flags=re.I) if l.strip()]
    blines = [l for _, l in numbered]
    rest = [l for l in blines if not re.match(r"^include\s*:", l, re.I)]
    if len(rest) > 1:
        raise Refused(BRIEF_REFUSED)
    include, known = [], {c.lower() for c in SENSITIVE}
    for n, l in numbered:
        m = re.match(r"^include\s*:\s*(.+?)\s*\.?$", l, re.I)       # signed 'Include:' line; a list is fine
        if not m:
            continue
        for x in (x.strip() for x in re.split(r"\s*,\s*|\s+and\s+", m.group(1)) if x.strip()):
            if x.lower() not in known:
                raise Refused(SENSITIVE_REFUSED.format(n=n))
            include.append(x)
    brief_text = rest[0] if rest else ""
    cut = re.search(r"[,;]?\s*(?:\b(?:cc|copy|copies|copied)\b|c\.c\.).*$", brief_text, re.I)
    brief_copy = cut.group(0) if cut else None
    core = brief_text[:cut.start()] if cut else brief_text
    kind, reader = parse_brief(core)
    if not kind:
        raise Refused(BRIEF_REFUSED)
    return kind, reader, include, brief_copy


TEMPLATE_OK = "Template check passed."


def check_template_main(argv=None):
    """Standalone template check: the same pre-template checks and the same from_template() the letter command
    uses. Exit 0 on pass, 2 on fail (signed wording with the line number). Writes nothing."""
    ap = argparse.ArgumentParser(prog="python -m check_template",
                                 description="Check synthetic notes against the fixed template. Writes nothing.")
    ap.add_argument("notes", help="notes text file, or - for stdin")
    ap.add_argument("--brief", help="file holding the brief")
    ap.add_argument("--not-copy", action="append", default=[], metavar="L<n>")
    ap.add_argument("--keep-out", action="append", default=[], metavar="L<n>")   # letter-only; format checked alike
    a = ap.parse_args(argv)
    llm_guard()
    patient, by_hand = False, False
    try:
        if any(not re.fullmatch(r"L\d+", x.strip()) for x in a.keep_out):
            raise Refused(KEEP_OUT_ARG)
        text = sys.stdin.read() if a.notes == "-" else open(a.notes, encoding="utf-8").read()
        brief_copy, patient, include = None, False, []
        if a.brief:
            btxt = open(a.brief, encoding="utf-8").read()
            by_hand = True                                       # fails closed: no reader ids here
            kind, reader, include, brief_copy = read_brief(btxt)
            patient = is_patient_letter(reader, None)
        pre_template_checks(text, None, brief_copy, patient, [x.strip() for x in a.not_copy])
        converted = from_template(text)
        check_safe(converted)                    # the same synthetic-data safeguard the letter command runs
        if a.keep_out:
            cuts = sensitive_result_lines(converted, include, patient)
            for x in a.keep_out:
                if x.strip() not in cuts:
                    raise Refused(KEEP_OUT_REFUSED.format(lid=x.strip()))
    except Refused as e:
        return report_refusal(e, patient or by_hand)
    print(TEMPLATE_OK)
    return 0


def main(argv=None):
    """CLI entry. Per-letter module state (template mode, empty fields, routes) never outlives one call, so an
    in-process call can't change a later library call (round 13 stability fix: found by seeded shuffle)."""
    try:
        return _main(argv)
    finally:
        _EMPTY_FIELDS.clear(); _TEMPLATE.clear(); _ROUTES.clear()


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Synthetic notes + brief -> Markdown letter. Nothing else is stored.")
    ap.add_argument("notes", nargs="?", help="notes text file, or - for stdin")
    ap.add_argument("--type", choices=TYPES)
    ap.add_argument("--to", help="audience or specialty, as the reader is named in the brief")
    ap.add_argument("--brief", help='e.g. "referral to <specialty>", or a file holding the brief (sets --type and --to)')
    ap.add_argument("--recipient")
    ap.add_argument("--audience", choices=AUDIENCES)
    ap.add_argument("--template", help="Markdown file with {{slot}} placeholders")
    ap.add_argument("--brand", help="brand pack folder containing brand.toml")
    ap.add_argument("--out", help="folder to write letter.md into (only the letter is written)")
    ap.add_argument("--final", action="store_true",
                    help="remove source tags only; the draft mark and footer stay; refused while a flag is open")
    ap.add_argument("--resolve", action="append", default=[], metavar='"AB=meaning"',
                    help='confirm an abbreviation\'s meaning, e.g. "MS=multiple sclerosis" (repeatable)')
    ap.add_argument("--not-copy", action="append", default=[], metavar="L<n>",
                    help="mark one notes line as not a copy request (repeat for each line); never a line naming someone")
    ap.add_argument("--keep-out", action="append", default=[], metavar="L<n>",
                    help="confirm a result cut as sensitive stays out (one line; clears only that line's flag)")
    ap.add_argument("--privacy", choices=("true", "false"),
                    help="check 19: does this letter need the privacy line? Required for --final on patient letters")
    ap.add_argument("--heldout", help="external folder of held-out synthetic notes; reports a failure rate")
    ap.add_argument("--reader-id", action="append", default=[], metavar="SYNTH-REG-<n>",
                    help="the id of each reader and copy recipient (names are never matched)")
    ap.add_argument("--card", help="the patient's card (cards TOML), for who holds a grant to act for them")
    ap.add_argument("--declined", help="questions the patient declined (TOML [[declined]] from forms): the letter "
                    "carries the signed LT-DECLINED-* line and never states the item is absent")
    ap.add_argument("--grant-id", action="append", default=[], metavar="SYNTH-GR-<n>",
                    help="the grant each reader who acts for the patient is written to under")
    ap.add_argument("--now", help="the scenario date (YYYY-MM-DD) a grant is checked at; never the wall clock")
    a = ap.parse_args(argv)
    llm_guard()
    if a.heldout:
        heldout(a.heldout)
        return 0
    brief_copy = None
    include = []
    n_readers = 1
    if a.brief is not None:
        btxt = a.brief
        if "\n" not in btxt and os.path.isfile(btxt):            # a brief file, as the checker takes it
            btxt = open(btxt, encoding="utf-8").read()
        n_readers = brief_readers(btxt)
        try:
            kind, reader, include, brief_copy = read_brief(btxt)
        except Refused as e:
            return report_refusal(e, True)                       # a brief that can't be read: fails closed
        a.type = a.type or kind
        a.to = a.to or reader
        if is_patient_letter(a.to, None) and not a.audience:
            a.audience = "patient"
    if not (a.notes and a.type and a.to):
        ap.error("notes, --type and --to are required")
    text = sys.stdin.read() if a.notes == "-" else open(a.notes, encoding="utf-8").read()
    tpl = open(a.template, encoding="utf-8").read() if a.template else None
    try:
        declined = load_declined(open(a.declined, encoding="utf-8").read()) if a.declined else None
    except (OSError, ValueError) as e:
        print(f"--declined: {e}", file=sys.stderr)                  # fails closed: nothing written
        return 2
    try:
        card = load_patient_card(a.card) if a.card else None
    except Exception:
        card = None                                              # a card that can't be read: grants unknown
    route, cleared = reader_grants(card, a.reader_id, a.grant_id, a.now)
    if route == "ended":                                         # CQ-LETTER-READER-ENDED: nothing written
        print(READER_ENDED_LINE, file=sys.stderr)
        return 2
    if route not in (None, "current"):                           # a grant holder that doesn't resolve: by hand now
        return report_refusal(f"Grant: {route}.", True)
    patient_req = bool(needs_hand(a.to, a.audience, True, n_readers, a.reader_id, text, card, cleared)) \
        or bool(a.card and card is None)
    try:
        not_copy = []
        for x in a.not_copy:
            if not re.fullmatch(r"L\d+", x.strip()):
                raise Refused(NOT_COPY_ARG)
            not_copy.append(x.strip())
        keep_out = []
        for x in a.keep_out:
            if not re.fullmatch(r"L\d+", x.strip()):
                raise Refused(KEEP_OUT_ARG)
            keep_out.append(x.strip())
        pre_template_checks(text, tpl, brief_copy, is_patient_letter(a.to, a.audience), not_copy)   # before the template
        text = from_template(text)
        _EMPTY_FIELDS.update(empty_template_sections(text))
        _TEMPLATE[:] = [True]
    except Refused as e:
        return report_refusal(e, patient_req)
    try:
        covered = refuse_copies(text, tpl, brief_copy, is_patient_letter(a.to, a.audience), not_copy)
    except Refused as e:
        return report_refusal(e, patient_req)
    try:
        flags = []
        result = build(text, a.type, a.to, a.recipient, a.audience, tpl, load_brand(a.brand) if a.brand else None,
                       a.resolve, flags, include, not_copy, keep_out,
                       privacy=None if a.privacy is None else a.privacy == "true", declined=declined)
        md, dropped = result
    except ComboRefused as e:                                 # check 2: written by hand; the clinician entry stays open
        if patient_req:
            return report_refusal(e, True)
        print(str(e), file=sys.stderr)
        print(letter_by_hand(e.check)["clinician"]["line"], file=sys.stderr)
        return 2
    except Refused as e:
        return report_refusal(e, patient_req)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    for lid in keep_out:
        if lid not in _SENS_CUTS:                                       # nothing written, no letter printed
            return report_refusal(KEEP_OUT_REFUSED.format(lid=lid), patient_req)
    shown = printed_flags(flags, md)
    for f in shown:
        print(f, file=sys.stderr)
    for d in dropped:                                                   # screen only; never in letter.md
        if d.endswith(": result without a unit dropped"):
            print(NO_UNIT_NOTICE.format(lid=d.split(":")[0]), file=sys.stderr)
    if dropped:
        cut = list(dict.fromkeys(i for d in dropped for i in re.findall(r"\b[LR]\d+\b", d.split(":")[0])))
        print(f"Cut from this letter: {', '.join(cut)}." if cut else "Cut from this letter: lines not known.",
              file=sys.stderr)
    for lid in _SENS_CUTS:
        print(SENS_CUT_RECORD.format(lid=lid), file=sys.stderr)       # screen only; never written to disk
    for lid in _KEPT_OUT:
        print(KEPT_OUT_RECORD.format(lid=lid), file=sys.stderr)
    for n in covered:
        print(NOT_COPY_MARK.format(n=n), file=sys.stderr)              # beside the cut list; never written to disk
    for item in dict.fromkeys(c for i in include for c in SENSITIVE if c.lower() == i.lower()):
        print(f"Included by the brief: {item}.", file=sys.stderr)        # known item names only
    dropped.clear()
    if a.final:
        try:
            md = finalise(result)
        except Refused as e:
            return report_refusal(e, patient_req, 3)
    if a.out:
        out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
        (out / "letter.md").write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
