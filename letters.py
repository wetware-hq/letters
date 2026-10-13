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
ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DMY = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b")
LONGNUM = re.compile(r"(?<![\w.])\+?\d(?:[\s-]?\d){7,}(?![\w.])")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


class Refused(Exception):
    pass


# ---------- safety ----------
def check_safe(text):
    ids = set(SYNTH.findall(text))
    if not ids:
        raise Refused(INCIDENT + " (reason: no SYNTH- identifier)")
    if len(ids) > 1:
        raise Refused("Refused: notes mention more than one patient identifier.")
    for m in ISO.finditer(text):
        if m.group(1) != "1937":
            raise Refused(INCIDENT + " (reason: date outside 1937)")
    for m in DMY.finditer(text):
        if m.group(3) not in ("1937", "37"):
            raise Refused(INCIDENT + " (reason: date outside 1937)")
    if LONGNUM.search(ISO.sub(" ", SYNTH.sub(" ", text))):
        raise Refused(INCIDENT + " (reason: phone- or card-number-like digits)")
    return ids.pop()


def fdate(iso):
    y, m, d = iso.split("-")
    return f"{int(d)} {MONTHS[int(m) - 1]} {y}"


# ---------- 1. parse (every line keeps its id) ----------
HEAD = re.compile(r"^\s*(1937-\d{2}-\d{2})\b[\s:,-]*(.*)$")


def parse(text):
    """Return (entries, sources). A line starting with a 1937 date opens an entry. Ids are L<line number>."""
    entries, sources, cur = [], {}, {"date": None, "lines": []}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = HEAD.match(line)
        if m:
            if cur["lines"] or cur["date"]:
                entries.append(cur)
            cur = {"date": m.group(1), "lines": []}
            line = m.group(2).strip()
        sources[f"L{n}"] = (raw.strip(), cur["date"])
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
RELEVANT = {
    "HIV status": r"infectious|immunolog|hiv|sexual health",
    "PrEP": r"sexual health|infectious|hiv",
    "abuse": r"social work|safeguarding|psychiatr|mental health",
    "psychiatric history": r"psychiatr|mental health|psycholog",
}
CLAUSE = re.compile(r"[^,;.]+[,;.]?")


def sensitive_cat(s):
    for cat, pats in SENSITIVE.items():
        if any(re.search(p, s, re.I) for p in pats):
            return cat
    return None


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
RES = re.compile(r"^(?P<name>[A-Za-z][A-Za-z0-9 -]*?)\s+(?P<val>[<>]?-?\d+(?:\.\d+)?)\s*(?P<unit>[^\s,;]*)\s*(?P<note>.*)$")
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


OWNER_ROLE = re.compile(r"\b(?:fellow|registrar|reg|consultant|team|nurse|GP|clinic|service|SHO|doctor|dr|surgeon|"
                        r"physician|pharmacist|ward|practice|cardiology|nephrology|renal|oncology|haematology|"
                        r"respiratory|neurology|gastroenterology|endocrinology|dermatology|rheumatology|urology|"
                        r"microbiology|micro|lab|physio|dietitian|midwife|patient)\b", re.I)
OWNER_ACTION = re.compile(r"\b(?:to\s+(?:arrange|chase|follow|review|check|action|phone|call|book|see)|will)\b", re.I)
OWNER_MOD = {"renal", "cardiology", "ward", "practice", "medical", "surgical", "respiratory", "on-call", "duty",
             "specialist", "junior", "senior", "oncology", "haematology", "neurology", "urology", "district"}
OWNER_SEP_ANY = re.compile(r"\s[-\u2013\u2014]+\s|:|\(|,")


def split_owner(body):
    """Return (test, owner or None). The owner is a role or team, or anyone given an action ('X to chase')."""
    m = OWNER_SEP.match(body)
    if m:
        who = (m.group("p") if m.group("p") is not None else m.group("who")).strip()
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
        rest = re.sub(r"^(?:on\s+)?1937-\d{2}-\d{2}\b[\s,;:.\-\u2013\u2014]*", "", rest)
        rest = re.sub(r"^(?:await(?:ed|ing)?|pending|outstanding|results? to follow)\b[\s.]*$", "", rest, flags=re.I).strip(" ,;-")
        return f"{m.group('test').strip()} - {rest}" if rest else m.group("test").strip()
    return None


DRUG_FILE = HERE / "drugs.txt"
DOSE_RX = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?(mg|mcg|micrograms?|g|mL|ml|units?|IU)(?![A-Za-z/])", re.I)
FREQ_RX = re.compile(r"(?<![A-Za-z])(daily|bd|tds|qid|nocte|mane|prn|weekly|od)(?![A-Za-z])", re.I)
STOP_RX = re.compile(r"\b(?:stopped|ceased|withheld|stop)\b", re.I)
ALLERGY_RX = re.compile(r"\b(?:allerg\w*|reactions?|intoleran\w*|anaphyla\w*)\b", re.I)
PER_LITRE = re.compile(r"\d(?:\.\d+)?\s?[A-Za-z\u00b5]*/L(?![A-Za-z])")
MED_HEAD = re.compile(r"\b(?:drugs?|med\w*|chart|prescri\w*|tablets?|regimen|rx)\b", re.I)
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
    if drugs and (DOSE_RX.search(whole) or FREQ_RX.search(whole)):
        return "med"
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
    return s.split()[0].lower() if s.split() else s.lower()


def _pkey(s):
    return HEDGE.sub("", s.strip()).lower()


def cumsum(entries, to="", dropped=None, keep_indications=False):
    dropped = [] if dropped is None else dropped
    st = {"problems": OrderedDict(), "resolved": OrderedDict(), "ruledout": OrderedDict(), "history": [],
          "meds": OrderedDict(), "stopped": OrderedDict(), "med_changes": [], "allergies": [],
          "results": OrderedDict(), "pending": OrderedDict(), "followup": [], "question": [],
          "admission": [], "course": [], "exam": [], "narrative": [], "first": None, "last": None,
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
                if change:
                    st["med_changes"].append({"what": change, "reason": reason, "date": d,
                                              "ids": (old["ids"] if old else []) + ids})
                cut = any(lid in d_.split(":")[0].split(",") and "indication" in d_ for d_ in dropped)
                st["meds"][k] = {"drug": drug, "ind": ind, "date": d, "ids": (old["ids"] if old else []) + ids,
                                 "cut": cut and not ind}
            elif kind == "stop":
                mm = MEDP.match(body)
                drug, reason = mm.group("drug").strip(), mm.group("v") if (mm.group("k") or "").lower().startswith("reason") else None
                k = _mkey(drug)
                old = st["meds"].pop(k, None)
                st["stopped"][k] = d
                st["med_changes"].append({"what": ("stopped", old["drug"] if old else drug), "reason": reason,
                                          "date": d, "ids": (old["ids"] if old else []) + ids})
            elif kind == "allergy":
                st["allergies"].append((body, ids))
            elif kind == "result":
                clear_pending(st, body, tagged_result=True)
                r = RES.match(body)
                if not r or not r.group("unit") or re.match(r"^[\d.]", r.group("unit")):
                    dropped.append(f"{lid}: result without a unit dropped")
                    continue
                name = r.group("name").strip()
                k = name.lower()
                st["pending"].pop(k, None)
                h = st["results"].setdefault(k, {"name": name, "values": []})
                h["values"].append((d, r.group("val"), r.group("unit"), r.group("note").strip(), lid))
            elif kind == "pending":
                parts = re.split(r";\s*(?:follow-up|who|f/u)\s*:\s*", body, flags=re.I)
                test, who = (parts[0].strip(), parts[1].strip()) if len(parts) > 1 else split_owner(parts[0])
                k = test.lower()
                if k not in st["results"]:
                    st["pending"][k] = (test, who, d, ids)
            elif kind == "admission":
                st["admission"].append((body, ids))
                st["admit"] = st["admit"] or d
            elif kind in ("followup", "question", "course", "exam"):
                st[kind].append((body, ids))
    return st


# ---------- 3. group into sections ----------
SECTIONS = {
    "referral": ["question", "problems", "background", "results", "med_changes", "meds", "allergies", "pending", "followup"],
    "discharge": ["admission", "course", "problems", "med_changes", "meds", "allergies", "results", "pending", "followup"],
    "gp-update": ["problems", "background", "results", "med_changes", "meds", "allergies", "pending", "followup"],
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
WHO = re.compile(r"\b(GP|general practitioner|patient|you|cardiology|renal|endocrinology|podiatry|clinic|"
                 r"ward|nurse|pharmacist|physiotherapy|team|surgeon|specialist|outpatients)\b", re.I)
WHEN = re.compile(r"\b(?:in|within|after)\s+\d+\s+(?:day|week|month|year)s?\b|\b1937-\d{2}-\d{2}\b|"
                  r"\b(?:today|tomorrow|at discharge|next visit|at next review)\b", re.I)


def tag(ids):
    seen = []
    for i in ids:
        if i not in seen:
            seen.append(i)
    return " [" + ", ".join(seen) + "]" if seen else ""


def dt(d):
    return f" on {fdate(d)}" if d else ""


REASON_FLAG = "[flag: reason, plain words not confirmed]"
REASON_ND = "reason not documented"


def _patient_reason(ind):
    """Patient letters: the reason as written; flagged unless every word is a signed single-meaning entry."""
    if not ind:
        return REASON_ND
    single = load_single_meaning()
    toks = tokens(ind)
    if toks and all(t in single for t in toks) and not re.sub(r"[A-Za-z0-9]+|\s|[,/-]", "", ind):
        return f"for {ind}"
    return f"for {ind} {REASON_FLAG}"


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
        return rows
    if name == "meds" and audience == "patient":
        return [f"{m['drug']}, {_patient_reason(m['ind'])}" + tag(m["ids"]) for m in st["meds"].values()]  # patient: nothing cut
    if name == "meds":
        return [m["drug"] + (f" (for {m['ind']})" if m["ind"] else ", reason not included" if m.get("cut") else f", {REASON_ND}")
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
            if re.fullmatch(r"(nil( known)?|nkda|none( known)?)", b.strip(), re.I):
                out.append("No known drug allergies" + tag(i))
                continue
            for part in re.split(r"[,;](?![^()]*\))", b):
                drug, react = split_reaction(part)
                if react and NO_REACTION.match(react):
                    out.append(f"{drug}: reaction {ND}" + tag(i))
                elif react:
                    out.append(f"{drug}: {react}" + tag(i))
                elif len(part.split()) <= 1:              # nothing follows the drug name
                    out.append(f"{part.strip()}: reaction {ND}" + tag(i))
                else:                                      # trailing words not confidently parsed: keep, flag
                    out.append(f"{part.strip()} {ALLERGY_FLAG}" + tag(i))
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
            w = WHO.search(b); t = WHEN.search(b)
            out.append(f"Who: {w.group(0) if w else ND}. When: {t.group(0) if t else ND}. Action: {b}" + tag(i))
        return out
    return []


EMPTY = {"pending": "No results pending at time of writing.", "allergies": f"Allergies {ND}.",
         "followup": f"Follow-up actions {ND}.", "med_changes": "No medicine changes documented.",
         "meds": f"Current medicines {ND}."}


def render_section(name, st, audience):
    head = HEADINGS[audience][name]
    items = section_items(name, st, audience)
    body = []
    if name == "results" and len(items) >= 3:
        body = ["| Test | Results |", "|---|---|"] + [f"| {n} | {v}{tag(i)} |" for n, v, i in items]
    elif name == "results":
        body = [f"- {n}: {v}" + tag(i) for n, v, i in items]
    elif name == "followup":
        body = [f"{k}. {x}" for k, x in enumerate(items, 1)]
    else:
        body = [f"- {x}" for x in items]
    if not body:
        if name in EMPTY:
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
ROMAN_CTX = {"stage", "grade", "class", "type", "types", "phase", "nyha"}
# u/U/iu/IU as a dose unit is a known misreading risk (10u read as 100): always flagged, attached or detached.
# cc as a dose unit ("5 cc saline") follows the same rule (Physician-requested); it is never a copy request.
# Lab units such as U/L, mU/L and IU/L stay clean; "10 units" is never flagged.
DOSE_U = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)( ?)([uU]|[iI][uU]|[cC][cC])(?![A-Za-z0-9/])")
TOOL_WORDS = {"DRAFT"}        # the tool's own fixed wording ("DRAFT, unsigned") is never flagged
SINGLE_MEANING_FILE = HERE / "single_meaning.toml"
SHORTHAND_FILE = HERE / "shorthand.txt"
WORDLISTS = [HERE / "wordlists" / "english.txt", HERE / "wordlists" / "medical.txt"]
# Plain-language swaps for patients: ordinary words only, never abbreviations.
PLAIN = [(r"\boral\b", "tablets"), (r"\bhypertension\b", "high blood pressure"), (r"\bexertional\b", "when active"),
         (r"(?<!twice )(?<!three times )(?<!four times )(?<!once )\bdaily\b", "once a day"),
         # signed frequency plain words (13 Oct 2026); od has none
         (r"\bnocte\b", "at night"), (r"\bmane\b", "in the morning"), (r"\bprn\b", "when needed"),
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
WORDS = set().union(*(_lines(p) for p in WORDLISTS)) | {m.lower() for m in MONTHS}


def tokens(s):
    return [m.group(0) for m in TOKEN.finditer(s)]


def needs_flag(tok, prev="", exact=()):
    """True if tok must be flagged. exact: abbreviations that may expand as written (reviewed list, resolutions)."""
    if tok in exact or tok in TOOL_WORDS:
        return False
    low = tok.lower()
    if low in drug_names():
        return False                                        # recognised medicine or brand
    if "/" in tok and tok not in ("c/o", "o/e"):
        return False                                        # slash unit, e.g. mmol/L
    if tok in UNITS:
        return False
    if re.fullmatch(r"[IVX]+", tok) and prev.lower() in ROMAN_CTX:
        return False                                        # stage IV
    if low in SHORTHAND or "." in tok or "/" in tok:
        return True
    if any(low == e.lower() for e in exact):
        return True                                         # case variant of a reviewed entry (Ecg, BD)
    if len(tok) >= 2 and (tok.isupper() or any(c.isupper() for c in tok[1:])):
        return True                                         # PEG, MAP, NSTEMIx, eGFR-like
    if len(tok) == 1:
        return False
    return low not in WORDS     # nbm, cx (and Nbm at sentence start); drug names too (acceptable)


def flagged_tokens(s, exact=()):
    out, prev = [], ""
    for m in TOKEN.finditer(s):
        if needs_flag(m.group(0), prev, exact):
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


def wording(lines, audience, resolved=None, single=None, flags=None):
    resolved, single = resolved or {}, single if single is not None else load_single_meaning()
    flags = [] if flags is None else flags
    seen, out, med = set(), [], False
    med_heads = {h[k] for h in HEADINGS.values() for k in ("meds", "med_changes")}
    for line in lines:
        if line.startswith("## "):
            med = line[3:].strip() in med_heads
        if not re.match(r"^(- |\d+\. |\| (?!Test \|))", line):
            out.append(line); continue
        text, ids = split_ids(line)
        if audience == "patient":
            for pat, rep in PLAIN:
                text = re.sub(pat, rep, text)
        extra, prev = [], [""]
        exact = tuple(resolved) + tuple(single)
        def sub(m):
            ab, before = m.group(0), prev[0]
            prev[0] = ab
            if ab in resolved:
                rid, full = resolved[ab]
                if rid not in extra:
                    extra.append(rid)
                if full == ab:
                    return ab                                # confirmed as written (e.g. a drug name)
            elif ab in single:
                full = single[ab]["patient" if audience == "patient" else "clinical"]
            elif needs_flag(ab, before, exact):
                label = DRUG_FLAG if med and (ab.islower() or (ab.istitle() and len(ab) > 3)) and ab.lower() not in SHORTHAND \
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
        def dose(m):
            if FLAG_ANY.match(text, m.end()):
                return m.group(0)
            flags.append(f"{m.group(0)}{ids.strip(' |') and ' ' + ids.strip(' |')}")
            return f"{m.group(0)} {FLAG}"
        text = DOSE_U.sub(dose, text)
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
          "possible", " ".join(MONTHS), "Test Results", "reason not included"]
VOCAB = set()
for _s in _FIXED:   # only fixed scaffolding; expansions and rewrites are earned per cited line
    VOCAB.update(w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]+", _s))
WORD = re.compile(r"[A-Za-z][A-Za-z-]{3,}")
NUM = re.compile(r"\d+(?:\.\d+)?")
OUTDATE = re.compile(r"\b(\d{1,2}) (" + "|".join(MONTHS) + r") (1937)\b")


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
    for pat, rep in LABEL_REWRITES:
        if pat.search(src):
            words |= _words(rep)
    return words


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
                problems.append(f"Refused: sentence over {SENTENCE_LIMIT} words at {at}.")
        if re.search(r"(?<!\w)\?\w", text):
            problems.append(f"Refused: banned shorthand at {at}.")
        if section in ("Results", "Your test results") and DIRECTION.search(text):
            problems.append(f"Refused: direction word in results at {at}. Give values with dates.")
        if not cited:
            if text.strip() in EMPTY.values():
                continue
            problems.append(f"Refused: unsourced sentence at {at}.")
            continue
        if any(c not in sources for c in cited):
            problems.append(f"Refused: cites a line not in the notes at {at}.")
            continue
        src = " ".join(sources[c][0] + " " + (sources[c][1] or "") for c in cited)
        res = {sources[c][0].split(" = ", 1)[0]: sources[c][0].split(" = ", 1)[1] for c in cited if c.startswith("R")}
        single = load_single_meaning()
        scan = text
        forms = [(f, ab) for ab, d in single.items() for f in (d["clinical"], d["patient"])] + \
                [(f, ab) for ab, f in res.items()]
        for f, ab in sorted(forms, key=lambda x: -len(x[0])):
            scan = scan.replace(f"{f} ({ab})", ab).replace(f, ab)
        exact = tuple(res) + tuple(single)
        prev = ""
        for m in TOKEN.finditer(scan):
            ab = m.group(0)
            if needs_flag(ab, prev, exact) and not FLAG_ANY.match(scan, m.end()):
                problems.append(f"Refused: abbreviation not flagged or resolved at {at}.")
            prev = ab
        for m in DOSE_U.finditer(scan):
            if not FLAG_ANY.match(scan, m.end()):
                problems.append(f"Refused: dose unit not flagged at {at}.")
        text = FLAG_ANY.sub("", text)
        for m in OUTDATE.finditer(text):
            iso = f"1937-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"
            if iso not in src:
                problems.append(f"Refused: date not in cited lines at {at}.")
        rest = OUTDATE.sub(" ", text)
        srcnums = set(NUM.findall(src))
        for n in NUM.findall(rest):
            if n not in srcnums:
                problems.append(f"Refused: number not in cited lines at {at}.")
        sw = _source_words(src)
        for w in WORD.findall(rest):
            lw = w.lower()
            if lw not in sw and lw not in VOCAB and not any(p in sw for p in lw.split("-")):
                problems.append(f"Refused: word not in cited lines at {at}.")
    limit = int(WORD_LIMIT * 1.1)
    if letter_type != "discharge" and words_total > limit:
        problems.append(f"Refused: letter body is {words_total} words; limit is about {WORD_LIMIT}.")
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


COPY_TAIL = ("letters can't send copies. Write a separate letter for each reader, "
             "so nobody assumes someone was copied in when they weren't.")
COPY_REFUSED = COPY_TAIL  # stable tail; the full message is "Refused: copy request <where>. " + COPY_TAIL
PATIENT_PRIVACY = "This letter includes private details about your health. It's yours to share or keep private."
COPY_WORDS = re.compile(r"(?<![\w])(?:cc|c\.c\.|copy|copies|copied)(?![\w])", re.I)
COPY_LINE = re.compile(r"^\s*(?:\d{4}-\d{2}-\d{2}\s+)?(?:cc|c\.c\.|copy|copies|copied)(?:\s+to)?\s*:", re.I | re.M)
COPY_SLOT = re.compile(r"\{\{\s*(?:cc|copy|copies|copy_to)\s*\}\}", re.I)
# In notes: a cc dose unit is removed first; a "CC:" chief-complaint heading is allowed only when clearly a symptom.
DOSE_CC = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)? ?[cC][cC](?![A-Za-z0-9/])")
NOTE_COPY = re.compile(r"(?<![\w.])(?:cc|c\.c\.)(?![\w])|\bcop(?:y|ies|ied)\s+(?:in\s+)?to\b", re.I)
CHIEF_COMPLAINT = re.compile(r"^CC\s*:\s*(.+)$")
NOT_A_SYMPTOM = {"dr", "doctor", "gp", "patient", "pt", "clinic", "team", "nurse", "carer", "family", "daughter", "son",
                 "wife", "husband", "partner", "mother", "father", "mum", "dad", "sister", "brother", "friend", "and",
                 "mr", "mrs", "ms", "miss", "prof", "consultant", "department", "dept", "ward", "hospital", "service",
                 "cardiology", "renal", "podiatry", "neurology", "oncology", "respiratory", "physio", "pharmacy",
                 "social", "worker", "solicitor", "lawyer", "insurer", "employer", "school", "next", "kin", "nok"}


_KNOWN = []


def _known_words():
    if not _KNOWN:
        _KNOWN.append(_lines(WORDLISTS[0]) | _lines(WORDLISTS[1]))
    return _KNOWN[0]


def copy_where(text):
    """Return 'at L<n>' for the first copy request in the notes, else None. Never returns the line text.
    Line ids match parse(). When unclear, it counts as a copy request."""
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = HEAD.match(line)
        if m:
            line = m.group(2).strip()
        line = DOSE_CC.sub(" ", line)
        cc = CHIEF_COMPLAINT.match(line)
        if cc:
            words = re.findall(r"[A-Za-z']+|\S", cc.group(1))
            known = _known_words()
            symptom = words and all(w.isalpha() and w == w.lower() and w in known and w not in NOT_A_SYMPTOM
                                    for w in words if w not in (",", "."))
            if symptom and not NOTE_COPY.search(cc.group(1)):
                continue
            return f"at L{n}"
        if NOTE_COPY.search(line) or COPY_LINE.search(line):
            return f"at L{n}"
    return None


PATIENT_COPY_TAIL = "A letter to the patient can't be copied to anyone else. Write a separate letter for each other reader."


def copy_refusal(where, patient=False):
    return f"Refused: copy request {where}. {PATIENT_COPY_TAIL if patient else COPY_TAIL}"


def is_patient_letter(to, audience):
    return audience == "patient" or (to or "").strip().lower() in ("patient", "the patient")


def refuse_copies(text, template=None, brief=None, patient=False):
    """Every letter has one reader. Refuse any copy recipient from the brief, the template or the notes."""
    if brief and COPY_WORDS.search(brief):
        raise Refused(copy_refusal("in the brief", patient))
    if template and (COPY_SLOT.search(template) or COPY_LINE.search(template)):
        raise Refused(copy_refusal("in the template", patient))
    where = copy_where(text)
    if where:
        raise Refused(copy_refusal(where, patient))


def build(text, letter_type, to, recipient=None, audience=None, template=None, brand=None, resolve=None, flags=None,
          include=None):
    """include: item names the brief asked to keep (see SENSITIVE); everything else sensitive is cut."""
    unknown = [i for i in (include or []) if i.lower() not in {c.lower() for c in SENSITIVE}]
    if unknown:
        raise Refused(BRIEF_REFUSED)
    _INCLUDE.clear(); _INCLUDE.update(i.lower() for i in (include or []))
    try:
        return _build(text, letter_type, to, recipient, audience, template, brand, resolve, flags)
    finally:
        _INCLUDE.clear()


# Temporary (13 Oct 2026): combination medicines can't be fully listed yet, so any letter naming one is refused.
COMBO_BRANDS = ("biktarvy", "triumeq", "dovato", "genvoya", "truvada", "descovy")
COMBO_RX = re.compile(r"(?<![a-z0-9])(?:" + "|".join(COMBO_BRANDS) + r")(?![a-z0-9])", re.I)
COMBO_REFUSED = "Refused: line {lid} has a combination medicine that letters can't fully list yet. Nothing was written."


def refuse_combination_brands(text):
    """Any line, any letter type or audience. Gives the line number only, never the drug name."""
    if not COMBO_RX.search(text):
        return
    for e in parse(text)[0]:
        for lid, line in e["lines"]:
            if COMBO_RX.search(line):
                raise Refused(COMBO_REFUSED.format(lid=lid))
    n = next(k for k, l in enumerate(text.splitlines(), 1) if COMBO_RX.search(l))
    raise Refused(COMBO_REFUSED.format(lid=f"L{n}"))


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
    refuse_copies(text, template, patient=is_patient_letter(to, audience))
    entries, sources = parse(text)
    resolved = resolution_sources(resolve, text)
    for ab, (rid, full) in resolved.items():
        sources[rid] = (f"{ab} = {full}", None)
    dropped = []
    st = cumsum(entries, to, dropped, keep_indications=(audience == "patient"))
    if audience != "patient":
        mark_linked_items(text, to, dropped)
    md = render(st, pid, letter_type, to, recipient, audience, template, brand, dropped,
                resolved, flags)
    if letter_type == "referral" and not st["question"]:
        head = f"**Letter:** {TITLES[letter_type]}"
        md = md.replace(head, f"{head} {QUESTION_FLAG}", 1) if head in md else \
            md.replace("\n", f" {QUESTION_FLAG}\n", 1)
        if flags is not None:
            flags.append(QUESTION_NOTICE)
    md = flag_long_sentences(md, flags)
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
        md = flag_cuts(md, dropped, flags, TITLES[letter_type])
    refuse_missing_medicines(md, text)
    refuse_missing_results(md, text, dropped)
    return md, dropped


# ---------- post-write checks: linked terms, required details, long sentences ----------
LINKED_TERMS_FILE = HERE / "linked_terms.toml"
SENTENCE_FLAG = "[flag: long sentence]"
SENTENCE_NOTICE = "Flag: sentence at {lid} is over 25 words. Split it, or keep it as written."
MED_FLAG = "[flag: medicine may point to something cut]"
MED_NOTICE = ("Flag: a medicine on line {lid} may tell the reader about something that was cut from this letter. "
              "Keep it, or explain it to the reader.")
LINKED_REFUSED = "Refused: line {lid} still mentions something this letter was meant to cut. Nothing was written."
REQUIRED_REFUSED = ("Refused: line {lid} has an allergy reaction or a follow-up owner that this letter leaves out. "
                    "Nothing was written.")
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
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", re.I)


def _meds_of(items):
    """Medicine and brand names of the cut items (allowed only on their own medication line)."""
    return [m for v in items.values() for m in list(v.get("medicines", [])) + list(v.get("brands", {}))]


CUT_FLAG = "[flag: something cut to keep it private]"
CUT_NOTICE = "Flag: something on line {lid} was cut to keep it private. Check the reader doesn't need it."


def flag_cuts(md, dropped, flags, title):
    """Every cut of a sensitive item carries a flag that blocks --final (line ids only)."""
    lids = list(dict.fromkeys(i for d in dropped if any(c in d for c in SENSITIVE)
                              for i in re.findall(r"\b[LR]\d+\b", d.split(":")[0])))
    if not lids:
        return md
    if flags is not None:
        flags.extend(CUT_NOTICE.format(lid=l) for l in lids)
    head = f"**Letter:** {title}"
    return md.replace(head, f"{head} {CUT_FLAG}", 1) if head in md else md.replace("\n", f" {CUT_FLAG}\n", 1)


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
    if not items:
        return md
    meds = _meds_of(items)
    soft = [t for v in items.values() for t in v.get("flag_terms", [])]
    src = _source_lines(text)
    lines = md.split("\n")
    for n, line in list(_body_lines(md)):
        hit = [i for i in _line_ids(line) if i in src and src[i][0] in MED_TAGS
               and any(_term_re(m).search(src[i][1]) for m in meds)]
        if hit and any(_term_re(m).search(line) for m in meds) and not _shows_reason(line, meds):
            lines[n] = _add_flag(line, MED_FLAG)
            if flags is not None:
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


def apply_cut_rule(md, text, dropped, flags, title=None):
    """For a cut item (never in patient letters):
    - medication line: a listed brand becomes its generic; a reason naming the cut item becomes 'reason not included';
      other linked words on that line are left out; the medicine is flagged.
    - any other line: every sentence holding a linked word (term, flag-only term, medicine or brand) is left out, flagged.
    Refused only for a listed brand with no generic in the map."""
    items = _cut_items(dropped)
    if not items:
        return md
    brands = _brand_map(items)
    generics = [m for v in items.values() for m in v.get("medicines", [])]
    hard = [t for v in items.values() for t in v.get("terms", [])]
    words = hard + [t for v in items.values() for t in v.get("flag_terms", [])]
    linked = sorted(set(words + generics + list(brands)), key=len, reverse=True)
    rx = lambda ws: re.compile("|".join(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])" for w in ws), re.I)
    any_linked = rx(linked)
    any_word = rx(sorted(set(hard), key=len, reverse=True)) if hard else None      # left out even on a medication line
    gen_rx = rx(sorted(set(generics + list(brands.values())), key=len, reverse=True)) if generics or brands else None
    src = _source_lines(text)
    lines, left_out = md.split("\n"), []
    def note(ids, pat):
        lid = next((i for i in ids if i in src and pat.search(src[i][1])), ids[0])
        left_out.append(lid)
    for n, line in list(_body_lines(md)):
        ids = _line_ids(line)
        text_part, id_part = split_ids(line)
        lead = re.match(r"^(- |\d+\. |\| )?", text_part).group(0)
        body = text_part[len(lead):]
        if any(i in src and src[i][0] in MED_TAGS for i in ids):
            if not any_linked.search(FLAG_ANY.sub("", body)) and "reason not included" not in body:
                continue
            for b, g in brands.items():
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(b)}(?![A-Za-z0-9])", body, re.I):
                    if not g:
                        lid = next((i for i in ids if i in src and re.search(re.escape(b), src[i][1], re.I)), ids[0])
                        raise Refused(LINKED_REFUSED.format(lid=lid))
                    body = re.sub(rf"(?<![A-Za-z0-9]){re.escape(b)}(?![A-Za-z0-9]){DRUG_FLAG_RX}", g, body, flags=re.I)
            def reason(m):
                return ", reason not included" if any_linked.search(m.group(0)) else m.group(0)
            body = re.sub(r"\s*\(for [^()]*\)", reason, body)
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
                    first = re.sub(r"\s+", " ", any_word.sub(lambda m: m.group(0) if gen_rx and gen_rx.fullmatch(m.group(0))
                                                            else "", first)).strip()
                    if not drug_word or not re.search(rf"(?<![A-Za-z0-9]){re.escape(drug_word)}(?![A-Za-z0-9])", first):
                        lid = next((i for i in ids if i in src and src[i][0] in MED_TAGS), ids[0])
                        raise Refused(LINKED_REFUSED.format(lid=lid))   # the medicine itself would disappear
                if len(kept) < len(rest) or first != orig_first:
                    body = ", ".join([first] + [p.strip() for p in kept])
                    lid = next((i for i in ids if i in src and src[i][0] in MED_TAGS), ids[0])
                    body = f"{body} {MED_PART_FLAG}"
                    if flags is not None:
                        flags.append(MED_PART_NOTICE.format(lid=lid))
            if MED_FLAG not in body:
                body = f"{body} {MED_FLAG}"
                if flags is not None:
                    flags.append(MED_NOTICE.format(lid=next((i for i in ids if i in src and src[i][0] in MED_TAGS), ids[0])))
            lines[n] = lead + body + id_part
            continue
        if all(src.get(i, (None,))[0] == "allergy" for i in ids):   # allergies are safety facts: never left out
            if any_linked.search(FLAG_ANY.sub("", body)) and MED_FLAG not in body:
                lines[n] = lead + body + f" {MED_FLAG}" + id_part
                if flags is not None:
                    flags.append(MED_NOTICE.format(lid=ids[0]))
            continue
        sents = re.split(r"(?<=[.!?])\s+", body)
        keep = [x for x in sents if not any_linked.search(FLAG_ANY.sub("", x))]
        if len(keep) < len(sents):
            note(ids, any_linked)
            lines[n] = (lead + " ".join(keep) + id_part) if keep else None
    if left_out:
        for lid in dict.fromkeys(left_out):
            if flags is not None:
                flags.append(LEFT_OUT_NOTICE.format(lid=lid))
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
            parts = re.split(r";\s*(?:follow-up|who|f/u)\s*:\s*", body, flags=re.I)
            who = parts[1].strip() if len(parts) > 1 else split_owner(parts[0])[1]
            need = [who] if who else []
        for x in need:
            if re.sub(r"\s+", " ", x).lower() not in plain:
                raise Refused(REQUIRED_REFUSED.format(lid=lid))


PENDING_NONE_REFUSED = "Refused: line {lid} has a test still pending, but this letter says none are. Nothing was written."
QUESTION_FLAG = "[flag: no clear question]"
QUESTION_NOTICE = "Flag: this referral has no clear question. Add one before it's signed."
FINAL_REFUSED = ("--final refused: {n} flag(s) still open. Confirm each meaning with "
                 '--resolve "AB=meaning" (or edit the notes), then run --final again.')


MED_MISSING = "Refused: a medicine on line {lid} of the notes is missing from this letter. Nothing was written."
ALLERGY_MISSING = "Refused: an allergy on line {lid} of the notes is missing from this letter. Nothing was written."
MAX_MISSING = ("Refused: the medicine on line {lid} has a maximum dose in the notes that this letter leaves out. "
               "Nothing was written.")
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


def refuse_missing_medicines(md, text):
    """Post-write guard, every letter: each medicine, stop and allergy line's parts must reach the letter, in the
    lines that cite it. Medicine: drug (generic ok) + every dose + every frequency (or its signed plain words) + any
    maximum. Stop: drug + stop word. Allergy: drug + reaction."""
    cited = {}
    for _, line in _body_lines(md):
        for i in _line_ids(line):
            cited[i] = cited.get(i, "") + " " + _norm(split_ids(line)[0])
    for lid, (kind, body) in _source_lines(text).items():
        if kind not in ("med", "stop", "allergy"):
            continue
        plain = cited.get(lid, "")
        if kind == "allergy":
            for part in re.split(r"[,;](?![^()]*\))", body):
                drug, reaction = split_reaction(part)
                if not drug or NO_REACTION.match(drug) or re.match(r"^(?:nil|none|nkda|no known)", drug, re.I):
                    continue
                need = [drug] + ([reaction] if reaction and not NO_REACTION.match(reaction) else [])
                if not all(_norm(x) in plain for x in need):
                    raise Refused(ALLERGY_MISSING.format(lid=lid))
            continue
        for part in body.split(" || "):
            if not _drug_present(part, plain):
                raise Refused(MED_MISSING.format(lid=lid))
            if kind == "stop":
                if not STOP_RX.search(plain):
                    raise Refused(MED_MISSING.format(lid=lid))
                continue
            for m in MAX_RX.finditer(part):
                plain_max = re.sub(PLAIN[-1][0], PLAIN[-1][1], m.group(0))
                if _norm(m.group(0)) not in plain and _norm(plain_max) not in plain:
                    raise Refused(MAX_MISSING.format(lid=lid))
            noted = _norm(MAX_RX.sub(" ", part))
            for m in DOSE_RX.finditer(noted):
                if not re.search(rf"(?<![\d.]){re.escape(m.group(0))}(?![a-z])", plain):
                    raise Refused(MED_MISSING.format(lid=lid))
            for tok, written in note_freqs(part):
                if not _freq_present(tok, plain, written):
                    raise Refused(MED_MISSING.format(lid=lid))
            for m in MAX_RX.finditer(part):
                plain_max = re.sub(PLAIN[-1][0], PLAIN[-1][1], m.group(0))
                if _norm(m.group(0)) not in plain and _norm(plain_max) not in plain:
                    raise Refused(MAX_MISSING.format(lid=lid))


RESULT_MISSING = "Refused: a result on line {lid} of the notes is missing from this letter. Nothing was written."


def refuse_missing_results(md, text, dropped):
    """Post-write guard, every letter: each result line with a unit keeps its value and unit in the lines that
    cite it, unless the item on that line was cut."""
    cut = {i.strip() for d in dropped for i in d.split(":")[0].split(",")
           if "result without a unit" not in d}
    cited = {}
    for _, line in _body_lines(md):
        for i in _line_ids(line):
            cited[i] = cited.get(i, "") + " " + _norm(split_ids(line)[0])
    for lid, (kind, body) in _source_lines(text).items():
        if kind != "result" or lid in cut:
            continue
        r = RES.match(body)
        if not r or not r.group("unit") or re.match(r"^[\d.]", r.group("unit")):
            continue
        plain = cited.get(lid, "")
        if not re.search(rf"(?<![\d.]){re.escape(r.group('val'))}(?![\d])", plain) or \
                _norm(r.group("unit")) not in plain:
            raise Refused(RESULT_MISSING.format(lid=lid))


def finalise(md):
    """Remove [Lx]/[Rx] source tags only. Refused while any abbreviation flag is open.
    The draft mark and draft footer stay until the signing clinician removes them."""
    n = len(FLAG_ANY.findall(md))
    if n:
        raise Refused(FINAL_REFUSED.format(n=n))
    return re.sub(r" \[[LR]\d+(?:, [LR]\d+)*\]", "", md)


def make_letter(text, letter_type, to, recipient=None, audience=None, template=None, brand=None):
    return build(text, letter_type, to, recipient, audience, template, brand)[0]


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
            md, _ = build(text, brief.get("type", "gp-update"), brief.get("to", "General practice"),
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
    return kind, r[:1].upper() + r[1:]


def line_only_flag(f):
    """Print every flag beside a letter as fixed wording plus line ids; never source words."""
    if f.startswith("Flag: "):
        return f
    ids = re.findall(r"\b[LR]\d+\b", f)
    where = f"on line {', '.join(dict.fromkeys(ids))}" if ids else "in this letter"
    if "(drug name, check spelling)" in f:
        return f"Flag: a drug name {where} may be misspelled. Check it."
    if re.match(r"^\d", f):
        return f"Flag: a dose unit {where} is easy to misread. Check it."
    return f"Flag: an abbreviation {where} has no confirmed meaning. Confirm it with --resolve."


def main(argv=None):
    ap = argparse.ArgumentParser(description="Synthetic notes + brief -> Markdown letter. Nothing else is stored.")
    ap.add_argument("notes", nargs="?", help="notes text file, or - for stdin")
    ap.add_argument("--type", choices=TYPES)
    ap.add_argument("--to", help="audience or specialty, e.g. Cardiology")
    ap.add_argument("--brief", help='e.g. "referral to cardiology" (sets --type and --to)')
    ap.add_argument("--recipient")
    ap.add_argument("--audience", choices=AUDIENCES)
    ap.add_argument("--template", help="Markdown file with {{slot}} placeholders")
    ap.add_argument("--brand", help="brand pack folder containing brand.toml")
    ap.add_argument("--out", help="folder to write letter.md into (only the letter is written)")
    ap.add_argument("--final", action="store_true",
                    help="remove source tags only; the draft mark and footer stay; refused while a flag is open")
    ap.add_argument("--resolve", action="append", default=[], metavar='"AB=meaning"',
                    help='confirm an abbreviation\'s meaning, e.g. "MS=multiple sclerosis" (repeatable)')
    ap.add_argument("--heldout", help="external folder of held-out synthetic notes; reports a failure rate")
    a = ap.parse_args(argv)
    llm_guard()
    if a.heldout:
        heldout(a.heldout)
        return 0
    brief_copy = None
    include = []
    if a.brief is not None:
        blines = [l.strip() for l in a.brief.splitlines() if l.strip()]
        include = [m.group(1).strip() for m in (re.match(r"^include\s*:\s*(.+?)\s*\.?$", l, re.I) for l in blines) if m]
        rest = [l for l in blines if not re.match(r"^include\s*:", l, re.I)]
        if len(rest) > 1 or any(i.lower() not in {c.lower() for c in SENSITIVE} for i in include):
            print(BRIEF_REFUSED, file=sys.stderr)
            return 2
        brief_text = rest[0] if rest else ""
        cut = re.search(r"[,;]?\s*(?:\b(?:cc|copy|copies|copied)\b|c\.c\.).*$", brief_text, re.I)
        brief_copy = cut.group(0) if cut else None
        core = brief_text[:cut.start()] if cut else brief_text
        kind, reader = parse_brief(core)
        if not kind:
            print(BRIEF_REFUSED, file=sys.stderr)
            return 2
        a.type = a.type or kind
        a.to = a.to or reader
        if is_patient_letter(a.to, None) and not a.audience:
            a.audience = "patient"
    if not (a.notes and a.type and a.to):
        ap.error("notes, --type and --to are required")
    text = sys.stdin.read() if a.notes == "-" else open(a.notes, encoding="utf-8").read()
    tpl = open(a.template, encoding="utf-8").read() if a.template else None
    try:
        refuse_copies(text, tpl, brief_copy, is_patient_letter(a.to, a.audience))
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        flags = []
        md, dropped = build(text, a.type, a.to, a.recipient, a.audience, tpl, load_brand(a.brand) if a.brand else None,
                            a.resolve, flags, include)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 2
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    for f in dict.fromkeys(line_only_flag(f) for f in flags):
        print(f, file=sys.stderr)
    if dropped:
        cut = list(dict.fromkeys(i for d in dropped for i in re.findall(r"\b[LR]\d+\b", d.split(":")[0])))
        print(f"Cut from this letter: {', '.join(cut)}." if cut else "Cut from this letter: lines not known.",
              file=sys.stderr)
    for item in dict.fromkeys(c for i in include for c in SENSITIVE if c.lower() == i.lower()):
        print(f"Included by the brief: {item}.", file=sys.stderr)        # known item names only
    dropped.clear()
    if a.final:
        try:
            md = finalise(md)
        except Refused as e:
            print(str(e), file=sys.stderr)
            return 3
    if a.out:
        out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
        (out / "letter.md").write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
