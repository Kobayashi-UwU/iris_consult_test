"""Knock-out rules: objective eligibility checks on structured form fields. No AI."""


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check_rule(cand: dict, rule: dict) -> tuple[bool, str]:
    field, op, values = rule["field"], rule["operator"], [str(v) for v in rule["values"]]
    actual = cand.get(field)
    ok = True
    if op == "in":
        ok = str(actual).strip().lower() in {v.strip().lower() for v in values}
    elif op == "equals":
        ok = str(actual).strip().lower() == values[0].strip().lower()
    elif op == "between":
        a, lo, hi = _num(actual), _num(values[0]), _num(values[1])
        ok = a is not None and lo is not None and hi is not None and lo <= a <= hi
    elif op == "gte":
        a, lo = _num(actual), _num(values[0])
        ok = a is not None and lo is not None and a >= lo
    return ok, f"{rule['description']} (candidate: {actual})"


def check(cand: dict, rules: list[dict]) -> tuple[bool, list[str]]:
    """Return (passed, reasons for failure). Disabled rules are skipped."""
    reasons = []
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        ok, why = check_rule(cand, rule)
        if not ok:
            reasons.append(why)
    return not reasons, reasons
