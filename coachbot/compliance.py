"""compliance.py — output filter. Blocks advice-like text before sending."""
import re

FORBIDDEN_PATTERNS = [
    r"\byou should (buy|sell|enter|exit|open|close|go long|go short)\b",
    r"\b(buy|sell) (now|today|tomorrow|soon)\b",
    r"\bi recommend\b",
    r"\bwe recommend\b",
    r"\bmy advice\b",
    r"\bprice (target|will|is going to)\b",
    r"\bwill (rise|fall|go up|go down|increase|decrease)\b",
    r"\bguaranteed?\b",
    r"\brisk[- ]free\b",
    r"\bcan't lose\b",
    r"\bsure thing\b",
    r"\bnext trade you should\b",
    r"\benter (a|the) (long|short|position)\b",
    r"\bshould have (bought|sold|gone long|gone short|entered|shorted|held|closed|exited|waited|stayed|kept|taken|cut|added|scaled)\b",
    r"\byou should have\b",
    r"\bconsider (buying|selling|entering|exiting|opening|closing|shorting)\b",
    r"\blook to (buy|sell|enter|exit|short)\b",
    r"\bnext time,? (buy|sell|enter|try|go|exit|hold|close|wait|target|aim)\b",
    r"\bnext time,? you\b",
    r"\bkeep an eye on\b",
    r"\bwatch (this|the) (level|price|market) for\b",
    r"\ba better (entry|exit|time|price|level) (was|would)\b",
    r"\byou could have (made|saved|earned|captured|avoided|gained)\b",
    r"\bexit(ing)? at [0-9]",
    r"\benter(ing)? at [0-9]",
    r"\bsmart money\b",
    r"\bliquidity (grab|pool|sweep|hunt)\b",
    r"\bstop[- ]hunt",
    r"\btrapped (traders|buyers|sellers)\b",
    r"\binstitutions? (were|was|are|is) (accumulat|distribut)",
]


def check_output(text: str) -> dict:
    violations = []
    low = text.lower()
    for pat in FORBIDDEN_PATTERNS:
        for m in re.finditer(pat, low):
            violations.append({"pattern": pat, "matched": m.group(0)})
    return {"passed": len(violations) == 0, "violations": violations}
