"""
investment_evidence.py — checks every figure in the AI's text against the
evidence pack the AI was given, so a user does not have to take the AI on trust.

For each number the AI writes, it looks for:
  traced    — the same value exists in the evidence pack. Where several values
              match, the ones whose LABEL shares words with the sentence are
              preferred ("labelled" match); otherwise the number matches only by
              value ("value_only") and the user is told so.
  computed  — it is simple arithmetic (difference, sum, ratio, % change) on two
              figures that appear in the SAME passage, or on such a figure and
              the current price / 52-week high / low. Deliberately strict: an
              earlier, looser check let almost any number pass as "derivable"
              from some pair of evidence numbers, which proves nothing.
  unverified — anything else. Shown to the user, highlighted, not hidden.

What this can and cannot do (stated in the UI as well): it verifies that the
NUMBERS exist in, or follow from, the data the AI was given. It cannot verify
judgement ("nothing indicating a major shift in outlook"), and it cannot tell
whether the underlying data provider was right.

Pure functions, no I/O.
"""
import re
from typing import Optional

_NUM_RE = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_RUPEE = chr(0x20B9)
_STOPWORDS = {"pct", "pa", "the", "and", "for", "with", "from", "model", "last", "history", "snapshot"}
_TOKEN_NAMES = {"pe": "P/E", "pb": "P/B", "eps": "EPS", "roe": "ROE", "ttm": "TTM", "pct": "%", "pa": "/yr", "yoy": "YoY",
                "cagr": "CAGR", "fii": "FII", "dii": "DII", "ebitda": "EBITDA", "dps": "DPS", "fcf": "FCF", "cfo": "CFO"}
_THRESHOLD_RE = re.compile(r"(above|below|exceed\w*|over|under|at least|at most|greater than|less than|more than|falls? to|drops? to|dropping|falling|rises? to|reach\w*|beyond|surpass\w*)\W{0,12}$", re.I)
_MOMENTUM = ["1-month", "3-month", "6-month", "12-month"]


def _decimals(raw: str) -> int:
    return len(raw.split(".")[1]) if "." in raw else 0


def _close(n: float, v: float, raw: str = "", slack: float = 0.0) -> bool:
    """n (as written, with its own rounding) matches v. A figure written as '23' may stand for 23.2;
    one written as '23.2' may not stand for 23.9. slack widens the test for arithmetic results,
    whose operands were themselves rounded."""
    half_unit = 0.5 * 10 ** (-_decimals(raw)) if raw else 0.051
    return abs(n - v) <= half_unit + 1e-9 + slack * abs(v)


def _label(path: str, tag: Optional[str]) -> str:
    parts = [p for p in re.split(r"[.\[\]]+", path) if p and not p.isdigit()]
    words = []
    for p in parts[-3:]:
        for t in p.split("_"):
            if t and t not in ("history", "last", "6", "fy"):
                words.append(_TOKEN_NAMES.get(t, t))
    label = " ".join(words)
    m = re.search(r"momentum_1m_3m_6m_12m_pct\[(\d)\]", path)
    if m:
        label = f"{_MOMENTUM[int(m.group(1))]} price change %"
    return f"{tag} · {label}" if tag else label


def _keywords(path: str) -> set:
    toks = set()
    for p in re.split(r"[.\[\]_]+", path.lower()):
        if p and not p.isdigit() and p not in _STOPWORDS:
            toks.add(p if len(p) <= 3 else p[:4])
    return toks


def build_index(evidence) -> list:
    """Every number in the evidence pack with its path, label and keywords."""
    out = []

    def add(value, path, tag):
        out.append({"value": abs(float(value)), "signed": float(value), "path": path, "label": _label(path, tag), "kw": _keywords(path)})

    def walk(o, path="", tag=None):
        if isinstance(o, bool):
            return
        if isinstance(o, (int, float)):
            add(o, path, tag)
        elif isinstance(o, str):
            for m in _NUM_RE.findall(o.replace(_RUPEE, " ")):
                try:
                    add(float(m.replace(",", "").rstrip(".")), path, tag)
                except ValueError:
                    pass
        elif isinstance(o, dict):
            t = next((o[k] for k in ("fy", "quarter", "date") if isinstance(o.get(k), str)), tag)
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k, t)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]", tag)

    walk(evidence)
    return out


def _texts(obj, path=""):
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _texts(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _texts(v, f"{path}[{i}]")


def _sentence_words(text: str, start: int, end: int) -> set:
    a = max(text.rfind(". ", 0, start), text.rfind("; ", 0, start), -1) + 1
    b = min([x for x in (text.find(". ", end), text.find("; ", end)) if x != -1] or [len(text)])
    return {w[:4] if len(w) > 3 else w for w in re.findall(r"[a-z]{2,}", text[a:b].lower())}


def _skip(raw: str, n: float, text: str, end: int) -> bool:
    if re.fullmatch(r"(19|20)\d\d", raw):                       # a year
        return True
    if "." not in raw and "," not in raw and n < 100 and not text[end:end + 1] == "%":   # small whole numbers: counts, "5 years"
        return True
    return False


_DIRECTION_RE = re.compile(r"\b(below|above|lower|higher|premium|discount|up|down|off|gain\w*|drop\w*|declin\w*|ris\w+|fall\w*|fell|rose|cheaper|richer|under|over|from)\b", re.I)


def check_output(ai: dict, evidence: dict) -> dict:
    index = build_index(evidence)
    price = (evidence.get("price") or {}) if isinstance(evidence, dict) else {}
    refs = [(price.get("current"), "current price"), (price.get("week52_high"), "52-week high"), (price.get("week52_low"), "52-week low")]
    refs = [(float(v), name) for v, name in refs if isinstance(v, (int, float)) and v]

    items, counts = [], {"traced": 0, "value_only": 0, "computed": 0, "threshold": 0, "unverified": 0}
    for path, text in _texts(ai):
        clean = text.replace(_RUPEE, " ").replace(chr(0x1b), " ")
        figs = []
        for m in _NUM_RE.finditer(clean):
            raw = m.group(0)
            n = float(raw.replace(",", ""))
            if _skip(raw, n, clean, m.end()):
                continue
            figs.append((m.start(), m.end(), raw, n))
        local_vals = [f[3] for f in figs]

        for start, end, raw, n in figs:
            words = _sentence_words(clean, start, end)
            cands = [e for e in index if _close(n, e["value"], raw)]
            item = {"path": path, "start": start, "end": end, "figure": text[start:end]}
            after_threshold_word = bool(_THRESHOLD_RE.search(clean[max(0, start - 30):start]))

            def find_formula():
                """Only the two calculations that appear in real analysis text. Arbitrary sums/ratios of
                arbitrary pairs of numbers can 'explain' almost any figure, which proves nothing."""
                a0 = max(clean.rfind(". ", 0, start), clean.rfind("; ", 0, start), -1) + 1
                b0 = min([x for x in (clean.find(". ", end), clean.find("; ", end)) if x != -1] or [len(clean)])
                sentence = clean[a0:b0]
                others = [v for v in local_vals if v != n]
                # (1) "X% below/above/from <reference>": |a - b| / b x 100, with a direction word in the sentence
                if _DIRECTION_RE.search(sentence) and "%" in clean[end:end + 3]:
                    ops = [(v, "") for v in others] + [(v, name) for v, name in refs]
                    for a, na in ops:
                        for b, nb in ops:
                            if a == b or not b or not (na == "" or nb == "" or (na and nb)):
                                continue
                            if _close(n, abs(a - b) / b * 100, raw, slack=0.004):
                                named = [x for x in (na, nb) if x]
                                return f"|{a:g} - {b:g}| / {b:g} x 100" + (f"   (with {' and '.join(named)} from the data)" if named else "")
                # (2) a gap in percentage points between two percentages in the same passage
                if re.search(r"\b(points?|pp|pts)\b", sentence, re.I):
                    for a in others:
                        for b in others:
                            if a > b and _close(n, a - b, raw, slack=0.004):
                                return f"{a:g} - {b:g}  (percentage points)"
                return None

            if cands:
                for e in cands:
                    e["_aff"] = len(e["kw"] & words)
                    e["_off"] = int(abs(n - e["value"]) / max(e["value"], 1e-9) * 1000)     # an exact match outranks a near one
                best = sorted(cands, key=lambda e: (e["_off"], -e["_aff"], len(e["path"])))[:3]
                digits = len(raw.replace(",", "").replace(".", ""))
                labelled = best[0]["_aff"] > 0 or digits >= 4      # a 4+ digit exact match (e.g. 1233.95) is distinctive on its own
                if not labelled:
                    f = find_formula()                              # a real explanation beats a coincidental number match
                    if f:
                        item.update(status="computed", formula=f, sources=[]); counts["computed"] += 1
                        items.append(item); continue
                    if after_threshold_word:                        # "above 10%": a level the AI proposes, not a data claim
                        item.update(status="threshold", sources=[], context=text[max(0, start - 60): end + 40]); counts["threshold"] += 1
                        items.append(item); continue
                item.update(status="traced", quality="labelled" if labelled else "value_only",
                            sources=[{"label": e["label"], "value": e["signed"], "path": e["path"]} for e in best])
                counts["traced" if labelled else "value_only"] += 1
            else:
                f = find_formula()
                if f:
                    item.update(status="computed", formula=f, sources=[]); counts["computed"] += 1
                elif after_threshold_word:
                    item.update(status="threshold", sources=[]); counts["threshold"] += 1
                else:
                    item.update(status="unverified", sources=[]); counts["unverified"] += 1
            if item["status"] in ("unverified", "threshold") or item.get("quality") == "value_only":
                item["context"] = text[max(0, start - 60): end + 40]      # so the user can find it without hunting
            items.append(item)
    total = len(items)
    return {"total": total, **counts, "items": items,
            "scope": "Checks that each number the AI wrote exists in, or follows from, the data it was given. It cannot check judgement, and it cannot tell whether the data provider was right."}
