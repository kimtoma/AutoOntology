# SPDX-License-Identifier: MIT
"""Explicit monetary approval rules; deterministic boundaries, never inferred authority."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation


class RuleError(ValueError):
    pass


def clean_rule(value):
    if not isinstance(value, dict) or set(value) - {
        "scope",
        "measure",
        "operator",
        "amount",
        "currency",
        "authority",
        "effective_from",
        "effective_to",
        "exceptions",
        "priority",
    }:
        raise RuleError("Use the explicit monetary approval rule schema.")
    result = dict(value)
    for key in ("scope", "measure", "authority"):
        if not isinstance(result.get(key), str) or not 1 <= len(result[key].strip()) <= 200:
            raise RuleError("Rule scope, measure and authority are required.")
        result[key] = result[key].strip()
    if result.get("operator") not in ("gt", "gte", "lt", "lte", "eq"):
        raise RuleError("Use gt, gte, lt, lte or eq.")
    raw = result.get("amount")
    if not isinstance(raw, str) or len(raw) > 64:
        raise RuleError("Use a bounded decimal amount string.")
    try:
        amount = Decimal(raw)
        if (
            not amount.is_finite()
            or amount < 0
            or amount.adjusted() > 24
            or amount.as_tuple().exponent < -6
        ):
            raise ValueError
    except (InvalidOperation, ValueError):
        raise RuleError("Use a finite nonnegative decimal amount.") from None
    if amount == 0:
        amount = Decimal(0)
    result["amount"] = (
        format(amount, "f").rstrip("0").rstrip(".")
        if "." in format(amount, "f")
        else format(amount, "f")
    )
    if not isinstance(result.get("currency"), str) or not re.fullmatch(
        r"[A-Z]{3}", result["currency"]
    ):
        raise RuleError("Use a three-letter currency code.")
    try:
        start = date.fromisoformat(result["effective_from"])
        end = date.fromisoformat(result["effective_to"]) if result.get("effective_to") else None
        if end and end <= start:
            raise ValueError
    except (TypeError, ValueError, KeyError):
        raise RuleError(
            "Use an inclusive start date and optional later exclusive end date."
        ) from None
    result["effective_from"] = start.isoformat()
    result["effective_to"] = end.isoformat() if end else None
    exceptions = result.setdefault("exceptions", [])
    if (
        not isinstance(exceptions, list)
        or len(exceptions) > 30
        or any(not isinstance(v, str) or not 1 <= len(v.strip()) <= 1000 for v in exceptions)
    ):
        raise RuleError("Use bounded, explicit exception descriptions.")
    result["exceptions"] = [v.strip() for v in exceptions]
    priority = result.setdefault("priority", 0)
    if type(priority) is not int or not 0 <= priority <= 1000:
        raise RuleError("Use an explicit integer priority from 0 to 1000.")
    return result


def _interval(rule):
    amount = Decimal(rule["amount"])
    return {
        "gt": (amount, False, Decimal("Infinity"), False),
        "gte": (amount, True, Decimal("Infinity"), False),
        "lt": (Decimal(0), True, amount, False),
        "lte": (Decimal(0), True, amount, True),
        "eq": (amount, True, amount, True),
    }[rule["operator"]]


def _overlap(a, b):
    al, ai, au, ax = _interval(a)
    bl, bi, bu, bx = _interval(b)
    lo, hi = max(al, bl), min(au, bu)
    return (
        lo < hi
        or lo == hi
        and (al < lo or ai)
        and (bl < lo or bi)
        and (au > hi or ax)
        and (bu > hi or bx)
    )


def rule_checks(model):
    rules, invalid, unsupported, conflicts = [], [], [], []
    legacy = 0
    for node in model.individuals.values():
        if node.type != "BusinessRule" and node.rule:
            invalid.append(
                {
                    "subject": node.local,
                    "reason": "Only BusinessRule individuals have monetary rule fields.",
                }
            )
        elif node.type == "BusinessRule":
            if not node.rule:
                legacy += 1
                continue
            try:
                value = clean_rule(node.rule)
                rules.append((node.local, value))
                if not node.derived_from or not any(
                    model.evidence.get(e) and model.evidence[e].verified for e in node.derived_from
                ):
                    unsupported.append(
                        {
                            "subject": node.local,
                            "reason": "No verified source quote is linked; rule semantics require human review.",
                        }
                    )
            except RuleError as error:
                invalid.append({"subject": node.local, "reason": str(error)})
    for index, (aid, a) in enumerate(rules):
        for bid, b in rules[index + 1 :]:
            if (
                any(a[k] != b[k] for k in ("scope", "measure", "currency", "priority"))
                or a["authority"] == b["authority"]
            ):
                continue
            dates = a["effective_from"] < (b["effective_to"] or "9999-12-31") and b[
                "effective_from"
            ] < (a["effective_to"] or "9999-12-31")
            if dates and _overlap(a, b):
                conflicts.append(
                    {
                        "subjects": [aid, bid],
                        "reason": "Overlapping amount/date scope assigns different authorities at the same priority. Resolve the conflict explicitly.",
                    }
                )
    return {
        "ok": not (invalid or unsupported or conflicts),
        "typed_rules": len(rules),
        "unstructured_legacy_rules": legacy,
        "invalid": invalid,
        "unsupported": unsupported,
        "conflicts": conflicts,
        "limits": "Explicit monetary fields only. Linked verified quotes are not automatic semantic entailment; free-text exceptions are not executed.",
    }
