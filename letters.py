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
NEVER_CUT = ("allergies", "meds", "med_changes", "pending", "followup")
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
    "abuse": [r"\babuse", r"\bassault", r"\bdomestic violence"],
    "psychiatric history": [r"\bdepress", r"\bbipolar", r"\bschizo", r"\bpsychiatr", r"\bpsychos",
                            r"\bself[- ]harm", r"\boverdose", r"\banxiety", r"\bsuicid"],
}
RELEVANT = {
    "HIV status": r"infectious|immunolog|hiv|sexual health",
    "abuse": r"social work|safeguarding|psychiatr|mental health",
    "psychiatric history": r"psychiatr|mental health|psycholog",
}
CLAUSE = re.compile(r"[^,;.]+[,;.]?")


def sensitive_cat(s):
    for cat, pats in SENSITIVE.items():
        if any(re.search(p, s, re.I) for p in pats):
            return cat
    return None


def relevant(cat, to):
    return bool(re.search(RELEVANT[cat], to or "", re.I))


def clean_text(body, ids, to, dropped, med=False):
    """Rewrite or cut stigmatising labels and irrelevant sensitive clauses. Log every change."""
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
        cat = None if med else sensitive_cat(cl)
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
    "med": "med", "meds": "med", "start": "med", "restart": "med", "change": "med",
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


def _mkey(s):
    return s.split()[0].lower() if s.split() else s.lower()


def _pkey(s):
    return HEDGE.sub("", s.strip()).lower()


def cumsum(entries, to="", dropped=None):
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
        for lid, line in e["lines"]:
            ids = [lid]
            m = TAG.match(line)
            kind = TAGS.get(m.group("tag").strip().lower()) if m else None
            body = m.group("body").strip() if kind else line
            body = clean_text(body, ids, to, dropped, med=kind in ("med", "stop"))
            if not body:
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
                if ind:
                    cat = sensitive_cat(ind)
                    if cat and not relevant(cat, to):
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
                st["meds"][k] = {"drug": drug, "ind": ind, "date": d, "ids": (old["ids"] if old else []) + ids}
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
                k = parts[0].strip().lower()
                if k not in st["results"]:
                    st["pending"][k] = (parts[0].strip(), parts[1].strip() if len(parts) > 1 else None, d, ids)
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


def section_items(name, st):
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
    if name == "meds":
        return [m["drug"] + (f" (for {m['ind']})" if m["ind"] else "") + tag(m["ids"]) for m in st["meds"].values()]
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
                mm = re.match(r"^\s*(?P<drug>[^(]+?)\s*(?:\((?P<r>[^)]*)\))?\s*$", part)
                drug = mm.group("drug")
                out.append(f"{drug}: {mm.group('r') if mm.group('r') else 'reaction ' + ND}" + tag(i))
        return out
    if name == "pending":
        return [f"{b}, sent{dt(d) or ' on an undocumented date'}. Who follows up: {who or ND}." + tag(i)
                for b, who, d, i in st["pending"].values()]
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
    items = section_items(name, st)
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


# ---------- 4. wording: abbreviations, plain language ----------
ALWAYS = [(r"\bc/o\b", "reports"), (r"\bSOB\b", "shortness of breath"), (r"\bhx\b", "history"),
          (r"\bo/e\b", "on examination"), (r"\bpt\b", "patient")]
ABBR = OrderedDict([
    ("PE", "pulmonary embolism"), ("CAP", "community-acquired pneumonia"), ("CKD", "chronic kidney disease"),
    ("T2DM", "type 2 diabetes"), ("eGFR", "estimated glomerular filtration rate"),
    ("LDL", "low-density lipoprotein cholesterol"), ("CRP", "C-reactive protein"), ("ECG", "electrocardiogram"),
    ("BP", "blood pressure"), ("IV", "intravenous"), ("PO", "by mouth"), ("ED", "emergency department"),
    ("AF", "atrial fibrillation"), ("GP", "general practitioner"), ("Hb", "haemoglobin"),
    ("TSH", "thyroid-stimulating hormone"), ("tds", "three times a day"), ("bd", "twice a day"),
    ("nocte", "at night"), ("mane", "in the morning"), ("PMHx", "past medical history"),
])
PLAIN = [(r"\bIV antibiotics\b", "antibiotics through a drip"), (r"\boral\b", "tablets"),
         (r"\bhypertension\b", "high blood pressure"), (r"\bexertional\b", "when active"),
         (r"\bCKD\b", "reduced kidney function"), (r"\bT2DM\b", "type 2 diabetes"),
         (r"\bCAP\b", "a chest infection (pneumonia)"), (r"\bbd\b", "twice a day"), (r"\btds\b", "three times a day"),
         (r"\bmane\b", "in the morning"), (r"\bnocte\b", "at night"), (r"\bdaily\b", "once a day"),
         (r"\bPO\b", "by mouth"), (r"\bIV\b", "through a drip"), (r"\bHb\b", "haemoglobin (blood count)"),
         (r"\bCRP\b", "CRP (infection marker)"), (r"\beGFR\b", "eGFR (kidney function)"),
         (r"\bECG\b", "heart tracing (ECG)"), (r"\bGP\b", "your family doctor (GP)"),
         (r"\bLDL\b", "LDL cholesterol"), (r"\bPE\b", "a blood clot in the lung"), (r"\bED\b", "the emergency department")]


def split_ids(line):
    m = re.search(r"( \[L\d+(?:, L\d+)*\])( \|)?$", line)
    return (line[:m.start()], line[m.start():]) if m else (line, "")


def wording(lines, audience):
    seen, out = set(), []
    for line in lines:
        if not re.match(r"^(- |\d+\. |\| (?!Test \|))", line):
            out.append(line); continue
        text, ids = split_ids(line)
        for pat, rep in ALWAYS:
            text = re.sub(pat, rep, text, flags=re.I)
        if audience == "patient":
            for pat, rep in PLAIN:
                text = re.sub(pat, rep, text)
        else:
            for ab, full in ABBR.items():
                def sub(m, ab=ab, full=full):
                    if ab in seen:
                        return ab
                    seen.add(ab)
                    return f"{full} ({ab})"
                text = re.sub(rf"(?<![\w-]){re.escape(ab)}(?![\w-])", sub, text)
        out.append(text + ids)
    return out


# ---------- 5. checker (independent of the renderer) ----------
DIRECTION = re.compile(r"\b(improv\w*|rising|rose|risen|falling|fell|fallen|worsen\w*|better|worse|"
                       r"increas\w*|decreas\w*|trend\w*|stable|declin\w*|up|down|higher|lower|"
                       r"normalis\w*|settl\w*)\b", re.I)
_FIXED = [*EMPTY.values(), ND, "reaction", "Reason for change", "Who follows up", "Who When Action", "sent on an "
          "undocumented date", "No known drug allergies", "resolved ruled out", "started changed stopped restarted from now",
          "possible", " ".join(MONTHS), "Test Results"]
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


def _source_words(src):
    """Words a line may use: those in its cited lines, plus expansions/rewrites earned by those same lines only."""
    words = {w.lower() for w in WORD.findall(src)}
    for ab, full in ABBR.items():                      # fixed dictionary, abbreviation must be in the cited lines
        if re.search(rf"(?<![\w-]){re.escape(ab)}(?![\w-])", src):
            words |= _words(full)
    for pat, rep in ALWAYS:
        if re.search(pat, src, re.I):
            words |= _words(rep)
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
    for line in body.splitlines():
        if line.startswith("## "):
            section, started = line[3:], True
            continue
        if not started or not re.match(r"^(- |\d+\. |\| (?!Test \|)(?!---))", line):
            continue
        text, ids = split_ids(line)
        text = re.sub(r"^(- |\d+\. |\| )", "", text).replace(" | ", " ")
        words_total += len(text.split())
        for sent in re.split(r"(?<=[.!])\s+", text):
            if len(sent.split()) > SENTENCE_LIMIT:
                problems.append(f"sentence over {SENTENCE_LIMIT} words: {sent[:40]}...")
        if re.search(r"\bc/o\b|\bSOB\b|(?<!\w)\?\w", text):
            problems.append(f"banned shorthand: {text[:40]}")
        if section in ("Results", "Your test results") and DIRECTION.search(text):
            problems.append(f"direction word in results ('{DIRECTION.search(text).group(0)}'): give values with dates")
        cited = [i.strip() for i in ids.strip(" []|").split(",") if i.strip()]
        if not cited:
            if text.strip() in EMPTY.values():
                continue
            problems.append(f"unsourced sentence: {text[:60]}")
            continue
        if any(c not in sources for c in cited):
            problems.append(f"cites unknown line: {ids.strip()}")
            continue
        src = " ".join(sources[c][0] + " " + (sources[c][1] or "") for c in cited)
        for m in OUTDATE.finditer(text):
            iso = f"1937-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"
            if iso not in src:
                problems.append(f"date not in cited lines: {m.group(0)}")
        rest = OUTDATE.sub(" ", text)
        srcnums = set(NUM.findall(src))
        for n in NUM.findall(rest):
            if n not in srcnums:
                problems.append(f"number not in cited lines ({','.join(cited)}): {n}")
        sw = _source_words(src)
        for w in WORD.findall(rest):
            lw = w.lower()
            if lw not in sw and lw not in VOCAB and not any(p in sw for p in lw.split("-")):
                problems.append(f"word not in cited lines ({','.join(cited)}): {w}")
    limit = int(WORD_LIMIT * 1.1)
    if letter_type != "discharge" and words_total > limit:
        problems.append(f"letter body is {words_total} words; limit is about {WORD_LIMIT}")
    if problems:
        raise Refused("Checker refused the letter:\n  " + "\n  ".join(problems))
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


def render(st, pid, letter_type, to, recipient=None, audience="specialist", template=None, brand=None, log=None):
    log = [] if log is None else log
    header = brand_block(brand) + [
        f"**Date:** {fdate(st['last']) if st['last'] else ND}  ",
        f"**To:** {to}" + (f", attn {recipient}" if recipient else "") + "  ",
        f"**Patient:** {pid}  ", f"**Letter:** {TITLES[letter_type]}", "",
        f"Dear {recipient}," if recipient else ("Dear patient," if audience == "patient" else "Dear colleague,"), "",
        PATIENT_OPENER if audience == "patient" else OPENERS[letter_type].format(d=dt(st["last"])), ""]
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
    md = "\n".join(wording(md.splitlines(), audience))
    return re.sub(r"\n{3,}", "\n\n", md).rstrip("\n") + "\n"


# ---------- pipeline ----------
def llm_guard():
    if os.environ.get("LETTERS_LLM_PROVIDER"):
        raise SystemExit("LETTERS_LLM_PROVIDER is set, but letters makes no model calls in this version. "
                         "Unset it to run.")


def build(text, letter_type, to, recipient=None, audience=None, template=None, brand=None):
    """Return (markdown, dropped). The dropped list is for the clinician's eyes only; never store it."""
    llm_guard()
    if letter_type not in TYPES:
        raise ValueError(f"unknown type {letter_type}; choose from {', '.join(TYPES)}")
    audience = audience or ("gp" if letter_type in ("discharge", "gp-update", "specialist-reply") else "specialist")
    pid = check_safe(text)
    entries, sources = parse(text)
    dropped = []
    st = cumsum(entries, to, dropped)
    if letter_type == "referral" and not st["question"]:
        raise Refused("Referral refused: the notes have no 'Question:' line. A referral must open with its question.")
    md = render(st, pid, letter_type, to, recipient, audience, template, brand, dropped)
    check(md, sources, letter_type)
    return md, dropped


def finalise(md):
    """Remove [Lx] source tags only. The draft mark and draft footer stay until the signing clinician removes them."""
    return re.sub(r" \[L\d+(?:, L\d+)*\]", "", md)


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
                    help="remove [Lx] source tags only; the draft mark and footer stay")
    ap.add_argument("--heldout", help="external folder of held-out synthetic notes; reports a failure rate")
    a = ap.parse_args(argv)
    llm_guard()
    if a.heldout:
        heldout(a.heldout)
        return 0
    if a.brief:
        m = re.match(r"^\s*(referral|discharge|gp[- ]update|update|specialist[- ]reply|reply)\s+(?:to|for)\s+(.+?)\s*$",
                     a.brief, re.I)
        if not m:
            ap.error('--brief must look like "referral to cardiology"')
        t = m.group(1).lower().replace(" ", "-")
        a.type = a.type or {"update": "gp-update", "reply": "specialist-reply"}.get(t, t)
        a.to = a.to or m.group(2)[:1].upper() + m.group(2)[1:]
    if not (a.notes and a.type and a.to):
        ap.error("notes, --type and --to are required")
    text = sys.stdin.read() if a.notes == "-" else open(a.notes, encoding="utf-8").read()
    tpl = open(a.template, encoding="utf-8").read() if a.template else None
    try:
        md, dropped = build(text, a.type, a.to, a.recipient, a.audience, tpl, load_brand(a.brand) if a.brand else None)
    except Refused as e:
        print(str(e), file=sys.stderr)
        return 2
    if dropped:
        print("Dropped or changed (for the clinician only; not saved):", file=sys.stderr)
        for d in dropped:
            print(f"  - {d}", file=sys.stderr)
    dropped.clear()
    if a.final:
        md = finalise(md)
    if a.out:
        out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
        (out / "letter.md").write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
