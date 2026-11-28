# SPDX-License-Identifier: MIT
"""The logic layer: the expression language (tokenizer, parser, type checker,
evaluator, limits), specs and their validation, the definition store and registry
(versions, history, revalidation -> broken -> restored) and function evaluation over
object records with links (sources faked with :class:`FakeBackend`)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ontology.config import Settings
from ontology.logic import expr as ex
from ontology.logic import functions, registry, specs
from ontology.logic.schema import Schema, TypeInfo, build_schema, column_type
from ontology.objects import overlay
from ontology.objects.reader import ObjectError
from ontology.store.db import Store
from tests import logic_fixtures as fx
from tests.logic_fixtures import CON, CUS, INV

TODAY = dt.date(2026, 11, 25)
SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "quickstart" / "logic"


# --- a schema without a store -------------------------------------------------------------


def _schema(labels: dict[str, str] | None = None, *, lower: bool = False) -> Schema:
    """The quickstart schema (*labels*: class label renames; *lower*: column names as
    Postgres folds their ASCII parts, ``청구ID`` -> ``청구id``)."""
    from ontology.objects import binding

    m = fx.model(labels)
    types = []
    for cand in binding.candidates(m):
        t = binding.proposal(cand, 1)
        t.status = "active"
        t.columns = fx.tables()[f"public.{t.table}"]["columns"]
        if lower:
            t.columns = [{**c, "name": c["name"].lower()} for c in t.columns]
        types.append(t)
    return build_schema(types, m)


SCHEMA = _schema()


def scope(name: str = INV, params: dict[str, str] | None = None, fns=None) -> ex.Scope:
    return ex.Scope(SCHEMA, SCHEMA.types[name], params or {}, fns or (lambda n: None))


def run(text: str, values: dict | None = None, *, name: str = INV, params=None, **kw):
    compiled = ex.compile_expr(
        text, scope(name, {k: ex.type_of(v) for k, v in (params or {}).items()})
    )
    obj = ex.Obj(SCHEMA.types[name], values if values is not None else dict(fx.INVOICES[1]))
    return compiled.evaluate(obj, ex.EvalContext(today=TODAY, **kw), params)


def err(text: str, name: str = INV, **kw) -> ex.ExprError:
    with pytest.raises(ex.ExprError) as e:
        ex.compile_expr(text, scope(name, **kw))
    return e.value


# --- tokenizer and parser ---------------------------------------------------------------


def test_tokens_unicode_backticks_strings_comments():
    toks = ex.tokenize("청구금액 >= 10.5 and `수납 금액` != 'a\\'b' # comment\n")
    assert [(t.kind, t.value) for t in toks] == [
        ("name", "청구금액"),
        ("op", ">="),
        ("num", 10.5),
        ("kw", "and"),
        ("name", "수납 금액"),
        ("op", "!="),
        ("str", "a'b"),
        ("end", None),
    ]
    assert ex.tokenize("a = b <> c")[1].value == "==" and ex.tokenize("a <> b")[1].value == "!="
    assert ex.tokenize("AND Or NOT")[0].value == "and"


def test_precedence_and_associativity():
    assert run("1 + 2 * 3") == 7
    assert run("(1 + 2) * 3") == 9
    assert run("10 - 4 - 3") == 3
    assert run("2 * 3 % 4") == 2
    assert run("-2 * -3") == 6
    assert run("not 1 > 2 and 2 > 1") is True
    assert run("true or false and false") is True
    assert run("not (true or false)") is False
    assert run("7 / 2") == 3.5 and run("8 / 2") == 4 and isinstance(run("8 / 2"), int)


@pytest.mark.parametrize(
    ("text", "code", "pos"),
    [
        ("1 +", "syntax_error", 3),
        ("(1 + 2", "syntax_error", 6),
        ("1 2", "syntax_error", 2),
        ("'open", "unterminated_string", 0),
        ("`open", "unterminated_name", 0),
        ("1 < 2 < 3", "syntax_error", 6),
        ("청구금액 $ 2", "unexpected_character", 5),
        ("12abc", "invalid_number", 0),
        ("a.", "syntax_error", 2),
        ("x in (1)", "syntax_error", 5),
    ],
)
def test_syntax_errors_have_positions(text, code, pos):
    e = err(text)
    assert (e.code, e.pos) == (code, pos), e.to_dict()


def test_malicious_input_is_refused():
    assert err("__import__('os')").code == "unknown_function"
    assert err("__import__('os').system('x')").code == "syntax_error"
    assert err("eval('1')").code == "unknown_function"
    assert err("__class__").code == "unknown_name"
    assert err("fn.__globals__").code == "unknown_function"
    assert err("(" * 100 + "1" + ")" * 100).code == "too_deep"
    assert err("not " * 60 + "true").code == "too_deep"
    assert err("-" * 60 + "1").code == "too_deep"
    assert err("1" + " + 1" * 500).code == "too_complex"
    assert err("'" + "x" * 5000 + "'").code == "too_long"
    assert err("'" + "x" * 1500 + "'").code == "string_too_long"
    assert err("9" * 50).code == "number_too_long"
    assert err("a.b.c.d.e.f").code == "path_too_long"
    assert err("1 in [" + ", ".join(["1"] * 150) + "]").code == "too_complex"
    with pytest.raises(ex.ExprError):
        ex.compile_expr(None, scope())  # type: ignore[arg-type]


def test_budget_exhaustion_and_huge_values():
    with pytest.raises(ex.EvalError) as e:
        run("1 + 1 + 1 + 1 + 1", step_budget=3)
    assert e.value.code == "budget_exceeded"
    big = " * ".join(["999999999"] * 5)
    with pytest.raises(ex.EvalError) as e:
        run(big)
    assert e.value.code == "number_out_of_range"
    with pytest.raises(ex.EvalError) as e:
        run("1 / 0")
    assert e.value.code == "division_by_zero"
    with pytest.raises(ex.EvalError):
        run("5 % 0")


# --- compiled-expression cache -------------------------------------------------------------


def _count_parses(monkeypatch) -> list[str]:
    calls: list[str] = []
    real = ex.parse
    monkeypatch.setattr(ex, "parse", lambda text: calls.append(text) or real(text))
    return calls


def test_compile_cache_compiles_an_expression_once_per_scope(monkeypatch):
    ex.clear_compile_cache()
    calls = _count_parses(monkeypatch)
    text = "청구금액 - coalesce(수납금액, 0) > 0"
    first = ex.compile_expr(text, scope())
    for _ in range(20):
        again = ex.compile_expr(text, scope())  # a new, equal scope each time
        assert again.type == first.type and again.run is first.run
    assert len(calls) == 1
    again.functions.add("mutated")  # callers get their own sets
    assert "mutated" not in ex.compile_expr(text, scope()).functions
    # the budget is still enforced at evaluation on a cached compile
    with pytest.raises(ex.EvalError) as e:
        run("1 + 1 + 1 + 1 + 1", step_budget=3)
    with pytest.raises(ex.EvalError) as e:
        run("1 + 1 + 1 + 1 + 1", step_budget=3)
    assert e.value.code == "budget_exceeded"


def test_compile_cache_does_not_leak_across_schemas():
    """The same text against another schema (here: columns folded to lower case) or
    another type, parameter types or function signatures is compiled afresh."""
    ex.clear_compile_cache()
    lower = _schema(lower=True)
    upper = ex.compile_expr("청구ID", scope())
    obj = ex.Obj(SCHEMA.types[INV], {"청구ID": "A", "청구id": "a"})
    assert upper.evaluate(obj, ex.EvalContext(today=TODAY)) == "A"
    folded = ex.compile_expr("청구ID", ex.Scope(lower, lower.types[INV]))
    assert folded.evaluate(obj, ex.EvalContext(today=TODAY)) == "a"
    assert ex.compile_expr("param.x + 1", scope(params={"x": "number"})).type == "number"
    with pytest.raises(ex.ExprError):
        ex.compile_expr("param.x + 1", scope(params={"x": "bool"}))
    num = {"f": ex.FunctionSig("f", INV, "number")}
    txt = {"f": ex.FunctionSig("f", INV, "text")}
    assert ex.compile_expr("f()", scope(fns=num.get)).type == "number"
    assert ex.compile_expr("f()", scope(fns=txt.get)).type == "text"
    with pytest.raises(ex.ExprError):
        ex.compile_expr("f()", scope(fns=lambda n: None))
    with pytest.raises(ex.ExprError):
        ex.compile_expr("청구금액", scope(CUS))  # another type: no such field


def test_compile_cache_keeps_the_parse_limits(monkeypatch):
    ex.clear_compile_cache()
    text = " + ".join(["1"] * 30)
    assert ex.compile_expr(text, scope()).type == "number"
    monkeypatch.setattr(ex, "MAX_NODES", 10)
    with pytest.raises(ex.ExprError) as e:
        ex.compile_expr(text, scope())
    assert e.value.code == "too_complex"


# --- names and types ---------------------------------------------------------------------


def test_fields_by_column_label_and_case():
    assert run("청구금액") == 9000000
    assert run("`청구금액`") == 9000000
    assert run("청구id", {"청구ID": "INV-9"}) == "INV-9"  # case-insensitive column
    assert run("소재지", dict(fx.CUSTOMERS[0]), name=CUS) == "부산"  # by label
    assert run("지역", dict(fx.CUSTOMERS[0]), name=CUS) == "부산"
    e = err("없는필드")
    assert e.code == "unknown_name" and "청구금액" in e.message
    assert err("param.x").code == "unknown_param"


def test_type_checking():
    assert ex.compile_expr("청구금액 - 수납금액", scope()).type == "number"
    assert ex.compile_expr("청구일 + 30", scope()).type == "date"
    assert ex.compile_expr("today() - 청구일", scope()).type == "number"
    assert ex.compile_expr("수납상태 + '!'", scope()).type == "text"
    assert ex.compile_expr("if(true, 1, null)", scope()).type == "number"
    assert ex.compile_expr("coalesce(수납금액, 0)", scope()).type == "number"
    for bad in (
        "청구금액 + 수납상태",
        "청구금액 == '1'",
        "not 청구금액",
        "청구금액 and true",
        "if(1, 2, 3)",
        "if(true, 1, 'a')",
        "청구일 * 2",
        "true < false",
        "len(청구금액)",
        "coalesce(1, 'a')",
        "수납상태 in [1, 2]",
        "days_between(청구일, 3)",
        "-수납상태",
    ):
        assert err(bad).code == "type_error", bad
    assert err("len()").code == "wrong_arguments"
    assert err("today(1)").code == "wrong_arguments"
    assert err("nosuch(1)").code == "unknown_function"
    assert err("date('2026-13-40')").code == "invalid_date"
    assert err("청구일 > '어제'").code == "invalid_date"
    assert err("upper(수납상태) where true").code == "syntax_error"


def test_null_semantics():
    nulls = dict(fx.INVOICES[2])  # 수납금액 is null
    assert run("수납금액 + 1", nulls) is None
    assert run("수납금액 > 1", nulls) is None
    assert run("수납금액 == null", nulls) is True
    assert run("수납금액 != null", nulls) is False
    assert run("청구금액 == null", nulls) is False
    assert run("수납금액 > 1 and false", nulls) is False
    assert run("수납금액 > 1 and true", nulls) is None
    assert run("수납금액 > 1 or true", nulls) is True
    assert run("not (수납금액 > 1)", nulls) is None
    assert run("if(수납금액 > 1, 'y', 'n')", nulls) == "n"
    assert run("coalesce(수납금액, 7)", nulls) == 7
    assert run("수납금액 in [1, null]", nulls) is True
    assert run("max(수납금액, 3)", nulls) == 3
    assert run("len(null)") is None


def test_dates_and_builtins():
    assert run("today()") == TODAY
    assert run("date('2026-01-31') + 1") == dt.date(2026, 2, 1)
    assert run("add_days(청구일, 30)") == dt.date(2026, 10, 1)
    assert run("today() - 청구일") == 85
    assert run("days_between(청구일, today())") == 85
    assert run("청구일 < '2026-10-01'") is True
    assert run("청구일 - 1") == dt.date(2026, 8, 31)
    assert run("청구일 in ['2026-09-01']") is True
    assert run("lower('ABC') + upper('d')") == "abcD"
    assert run("contains(수납상태, '미')") is True
    assert run("len('가나다')") == 3
    assert run("round(2.567, 2)") == 2.57 and run("round(2.5)") == 2
    assert run("abs(-3)") == 3 and run("min(3, 1, 2)") == 1 and run("max('a', 'b')") == "b"
    assert run("text(청구일)") == "2026-09-01" and run("number('1,200')") == 1200
    assert run("'a' + 'b'") == "ab"
    assert run("수납상태 not in ['완납', '부분']") is True
    # source values arrive as text from Postgres: coerced per column type
    assert run("청구금액 * 2", {**fx.INVOICES[1], "청구금액": "100"}) == 200
    assert run("청구일", {**fx.INVOICES[1], "청구일": "2026-09-01T10:00:00"}) == dt.date(2026, 9, 1)
    with pytest.raises(ex.EvalError) as e:
        run("청구금액 * 2", {**fx.INVOICES[1], "청구금액": "lots"})
    assert e.value.code == "bad_value"
    assert column_type("timestamp without time zone") == "date"
    assert column_type("xsd:integer") == "number" and column_type("") == "text"
    assert column_type("boolean") == "bool" and column_type("character varying") == "text"


def test_params():
    assert run("청구금액 > param.기준", params={"기준": 100}) is True
    sc = scope(params={"기준": "number"})
    assert ex.compile_expr("param.기준 * 2", sc).type == "number"
    assert err("param.기준 + 'a'", params={"기준": "number"}).code == "type_error"


# --- links -------------------------------------------------------------------------------


class MemLoader:
    """Links over the fixture rows, counting follows."""

    def __init__(self):
        self.calls = 0
        self.rows = {
            CON: fx.CONTRACTS,
            CUS: fx.CUSTOMERS,
            INV: fx.INVOICES,
        }

    def follow(self, obj, link):
        self.calls += 1
        target = SCHEMA.types[link.target]
        if link.direction == "out":
            value = obj.values.get(link.column)
            rows = [r for r in self.rows[link.target] if r[target.key] == value]
        else:
            value = obj.values.get(obj.type.key)
            rows = [r for r in self.rows[link.target] if r[link.column] == value]
        return [ex.Obj(target, dict(r)) for r in rows]


def test_link_paths_and_aggregates():
    loader = MemLoader()
    inv = dict(fx.INVOICES[1])
    assert run("계약.계약명", inv, loader=loader) == "가람물산 물류 시스템"
    assert run("계약.고객.담당자", inv, loader=loader) == "최하은"
    assert run("계약.고객.소재지", inv, loader=loader) == "부산"
    con = dict(fx.CONTRACTS[1])
    assert run("count(청구)", con, name=CON, loader=loader) == 2
    assert run("count(청구 where 수납상태 != '완납')", con, name=CON, loader=loader) == 2
    assert run("sum(청구.청구금액)", con, name=CON, loader=loader) == 11000000
    assert (
        run("sum(청구.수납금액 where 청구금액 > 5000000)", con, name=CON, loader=loader) == 3000000
    )
    assert run("min(청구.청구일)", con, name=CON, loader=loader) == dt.date(2026, 9, 1)
    assert run("max(청구.청구금액)", con, name=CON, loader=loader) == 9000000
    assert run("exists(청구 where 수납상태 == '완납')", con, name=CON, loader=loader) is False
    assert (
        run("count(계약 where 상태 == '진행')", dict(fx.CUSTOMERS[0]), name=CUS, loader=loader) == 1
    )
    # a missing foreign key: the path is null
    orphan = dict(fx.CONTRACTS[2])
    assert run("고객.담당자", orphan, name=CON, loader=loader) is None
    assert run("count(청구)", orphan, name=CON, loader=loader) == 0
    assert run("max(청구.청구금액)", orphan, name=CON, loader=loader) is None
    # the link is named by its target label, label, name or column
    assert run("고객id.담당자", dict(fx.CONTRACTS[0]), name=CON, loader=loader) == "윤서아"
    compiled = ex.compile_expr("계약.고객.담당자", scope())
    assert compiled.links == {"fk-fk-b-1"}


def test_link_errors():
    assert err("계약").code == "link_not_value"
    assert err("청구.청구금액", CON).code == "link_many"
    assert err("count(청구.청구금액)", CON).code == "unknown_link"
    assert err("sum(청구)", CON).code == "wrong_arguments"
    assert err("sum(청구.수납상태)", CON).code == "type_error"
    assert err("nolink.x").code == "unknown_link"
    assert err("count(청구 where 없는필드 > 1)", CON).code == "unknown_name"
    assert err("count(청구 where 청구금액)", CON).code == "type_error"
    assert err("count(1)", CON).code == "wrong_arguments"
    inactive = Schema({**SCHEMA.types, CUS: TypeInfo(CUS, "고객", "고객ID", "broken")})
    with pytest.raises(ex.ExprError) as e:
        ex.compile_expr("고객.담당자", ex.Scope(inactive, inactive.types[CON]))
    assert e.value.code == "link_target_inactive"
    with pytest.raises(ex.EvalError) as ee:
        run("계약.계약명")  # no loader here
    assert ee.value.code == "no_loader"


def test_calls_between_functions():
    sigs = {
        "미수금": ex.FunctionSig("미수금", INV, "number"),
        "배수": ex.FunctionSig("배수", INV, "number", [("n", "number")]),
        "계약함수": ex.FunctionSig("계약함수", CON, "number"),
    }
    sc = scope(fns=sigs.get)
    compiled = {
        "미수금": ex.compile_expr("청구금액 - coalesce(수납금액, 0)", sc),
        "배수": ex.compile_expr(
            "미수금() * param.n", ex.Scope(SCHEMA, SCHEMA.types[INV], {"n": "number"}, sigs.get)
        ),
    }
    main = ex.compile_expr("fn.미수금() + 미수금 + 배수(2)", sc)
    assert main.functions == {"미수금", "배수"} and main.type == "number"
    obj = ex.Obj(SCHEMA.types[INV], dict(fx.INVOICES[1]))
    ctx = ex.EvalContext(today=TODAY, function=compiled.get)
    assert main.evaluate(obj, ctx) == 6000000 * 4
    # not runnable (not active): an evaluation error, not a crash
    with pytest.raises(ex.EvalError) as e:
        main.evaluate(obj, ex.EvalContext(today=TODAY))
    assert e.value.code == "function_unavailable"
    with pytest.raises(ex.ExprError) as e2:
        ex.compile_expr("계약함수()", sc)
    assert e2.value.code == "wrong_object_type"
    with pytest.raises(ex.ExprError):
        ex.compile_expr("배수('x')", sc)
    with pytest.raises(ex.ExprError):
        ex.compile_expr("미수금(1)", sc)
    # a runtime cycle is stopped even if validation missed it
    loop = {"미수금": ex.compile_expr("fn.미수금()", sc)}
    with pytest.raises(ex.EvalError) as e3:
        loop["미수금"].evaluate(obj, ex.EvalContext(today=TODAY, function=loop.get, stack=[]))
    assert e3.value.code in ("cycle", "budget_exceeded")


# --- specs --------------------------------------------------------------------------------


def ctx_with(*defs: dict, statuses: dict | None = None) -> specs.LogicContext:
    ctx = specs.LogicContext(SCHEMA)
    for d in defs:
        spec = specs.parse_spec(d)
        getattr(ctx, spec.kind + "s")[spec.name] = spec
        ctx.statuses[(spec.kind, spec.name)] = (statuses or {}).get(spec.name, "active")
    return ctx


MIUSU = {
    "kind": "function",
    "name": "미수금",
    "object_type": "청구",
    "returns": "number",
    "expr": "청구금액 - coalesce(수납금액, 0)",
}


def test_spec_loading_yaml_json_and_schema_errors():
    docs = specs.load_documents(
        "kind: function\nname: a\nobject_type: 청구\nreturns: number\nexpr: 1\n---\n- kind: function\n  name: b\n  object_type: 청구\n  returns: date\n  expr: 2026-01-31\n"
    )
    assert [d["name"] for d in docs] == ["a", "b"]
    assert docs[1]["expr"] == "2026-01-31"  # dates stay text
    assert specs.parse_spec(docs[0]).expr == "1"
    assert specs.load_documents(json.dumps(MIUSU))[0]["name"] == "미수금"
    with pytest.raises(specs.SpecError) as e:
        specs.load_documents("a: &x [1]\nb: *x\n")
    assert e.value.errors[0]["code"] == "invalid_yaml" and "aliases" in e.value.errors[0]["message"]
    with pytest.raises(specs.SpecError):
        specs.load_documents("!!python/object/apply:os.system ['x']")
    with pytest.raises(specs.SpecError):
        specs.load_documents("x" * 70_000)
    with pytest.raises(specs.SpecError):
        specs.load_documents("- 1\n- 2\n")
    with pytest.raises(specs.SpecError) as e:
        specs.parse_spec({"kind": "macro"})
    assert e.value.errors[0]["path"] == "kind"
    with pytest.raises(specs.SpecError) as e:
        specs.parse_spec({**MIUSU, "returns": "money", "extra": 1, "name": "1bad"})
    paths = {x["path"] for x in e.value.errors}
    assert {"returns", "extra", "name"} <= paths


def test_action_and_workflow_schema():
    action = {
        "kind": "action",
        "name": "독촉",
        "label": "독촉",
        "object_type": "청구",
        "params": [{"name": "단계", "type": "number", "enum": [1, 2, 3], "default": 1}],
        "preconditions": [{"expr": "수납상태 != '완납'", "message": "이미 완납"}],
        "effects": [{"set": "독촉단계", "value": "param.단계", "operational": True}],
        "notify": [
            {"to": "role:owner", "subject": "독촉 {{ 청구id }}", "body": "금액 {{ 청구금액 }}"}
        ],
        "approval": {"role": "owner", "when": "청구금액 > 100000000"},
        "agent": "propose",
    }
    spec = specs.parse_spec(action)
    assert spec.permission.min_role == "editor" and spec.notify[0].to == ["role:owner"]
    assert specs.dump(spec)["effects"][0]["set"] == "독촉단계"
    with pytest.raises(specs.SpecError):
        specs.parse_spec({**action, "effects": [], "notify": []})
    wf = {
        "kind": "workflow",
        "name": "연체독촉",
        "label": "연체 독촉",
        "trigger": {"schedule": "daily 02:00"},
        "for_each": {"object_type": "청구", "where": "수납상태 != '완납'"},
        "steps": [
            {
                "if": "today() - 청구일 >= 30",
                "then": [{"action": "독촉", "params": {"단계": "2"}}],
                "else": [{"notify": {"to": "role:editor", "subject": "x"}}],
            }
        ],
    }
    parsed = specs.parse_spec(wf)
    assert parsed.steps[0].if_ == "today() - 청구일 >= 30"
    assert specs.dump(parsed)["steps"][0]["if"] == "today() - 청구일 >= 30"
    assert specs.parse_schedule("weekly MON 09:30") == {
        "every": "weekly",
        "weekday": 0,
        "hour": 9,
        "minute": 30,
    }
    assert specs.parse_schedule("hourly")["every"] == "hourly"
    for trigger in ({"schedule": "daily 25:00"}, {"schedule": "daily 02:00", "manual": True}, {}):
        with pytest.raises(specs.SpecError):
            specs.parse_spec({**wf, "trigger": trigger})
    for step in (
        {"action": "a", "notify": {"to": "role:x", "subject": "s"}},
        {"then": [{"action": "a"}]},
        {"if": "true"},
    ):
        with pytest.raises(specs.SpecError):
            specs.parse_spec({**wf, "steps": [step]})
    ctx = ctx_with(action)
    assert specs.validate(parsed, ctx) == []
    assert specs.validate(spec, ctx) == []
    assert specs.references(parsed, ctx) == [("action", "독촉")]


def _codes(spec: dict, ctx: specs.LogicContext | None = None) -> set[str]:
    return {e["code"] for e in specs.validate(specs.parse_spec(spec), ctx or ctx_with())}


def test_function_validation_errors():
    assert _codes(MIUSU) == set()
    assert _codes({**MIUSU, "object_type": "없는유형"}) == {"unknown_object_type"}
    assert _codes({**MIUSU, "returns": "text"}) == {"type_error"}
    assert _codes({**MIUSU, "expr": "청구금액 +"}) == {"syntax_error"}
    assert _codes({**MIUSU, "name": "count"}) == {"reserved_name"}
    assert _codes({**MIUSU, "name": "청구금액"}) == {"shadows_field"}
    errors = specs.validate(specs.parse_spec({**MIUSU, "expr": "청구금액 + 없음"}), ctx_with())
    assert errors[0]["path"] == "expr" and errors[0]["pos"] == 7
    a = {**MIUSU, "name": "a", "expr": "b()"}
    b = {**MIUSU, "name": "b", "expr": "c()"}
    c = {**MIUSU, "name": "c", "expr": "a()"}
    codes = _codes(a, ctx_with(b, c))
    assert codes == {"cycle"}
    assert _codes({**MIUSU, "name": "self", "expr": "self()"}) == {"cycle"}
    assert _codes(a, ctx_with(b, {**MIUSU, "name": "c"})) == set()
    assert _codes(a, ctx_with(b)) == set()  # b calling a missing c is b's error
    assert _codes({**MIUSU, "name": "z", "expr": "zzz()"}) == {"unknown_function"}
    assert _codes(a, ctx_with({**MIUSU, "name": "b"}, statuses={"b": "broken"})) == {
        "reference_broken"
    }
    assert _codes({**MIUSU, "params": [{"name": "p", "type": "number", "default": "x"}]}) == {
        "type_error"
    }


def test_action_validation_errors():
    base = {
        "kind": "action",
        "name": "act",
        "label": "act",
        "object_type": "청구",
        "effects": [{"set": "수납상태", "value": "'완납'"}],
    }
    assert _codes(base) == set()
    assert _codes({**base, "effects": [{"set": "수납금액", "value": "'x'"}]}) == {"type_error"}
    assert _codes({**base, "effects": [{"set": "없는열", "value": "1"}]}) == {"unknown_property"}
    assert _codes({**base, "effects": [{"set": "청구ID", "value": "'x'"}]}) == {"key_readonly"}
    assert _codes(
        {**base, "effects": [{"set": "수납상태", "value": "1", "operational": True}]}
    ) == {"operational_is_column"}
    assert _codes({**base, "effects": [{"set": "a b", "value": "1", "operational": True}]}) == {
        "invalid_name"
    }
    assert _codes(
        {
            **base,
            "effects": [{"set": "수납상태", "value": "'a'"}, {"set": "수납상태", "value": "'b'"}],
        }
    ) == {"duplicate"}
    assert _codes({**base, "preconditions": [{"expr": "1", "message": "m"}]}) == {"type_error"}
    assert _codes(
        {
            **base,
            "notify": [
                {"to": ["role:boss", "email:nope", "field:청구금액"], "subject": "{{ 없음 }}"}
            ],
        }
    ) == {
        "invalid_recipient",
        "type_error",
        "unknown_name",
    }
    assert _codes({**base, "approval": {"when": "true"}}) == {"schema"}
    assert _codes({**base, "params": [{"name": "p"}, {"name": "p"}]}) == {"duplicate"}
    # an operational property an action declares is a field for functions
    act = {**base, "effects": [{"set": "독촉단계", "value": "1", "operational": True}]}
    assert _codes({**MIUSU, "expr": "coalesce(number(독촉단계), 0)"}, ctx_with(act)) == set()
    assert _codes({**MIUSU, "expr": "독촉단계"}) == {"unknown_name"}


def test_workflow_validation_errors():
    act = {
        "kind": "action",
        "name": "act",
        "label": "a",
        "object_type": "청구",
        "params": [{"name": "n", "type": "number"}],
        "effects": [{"set": "수납상태", "value": "'x'"}],
    }
    wf = {
        "kind": "workflow",
        "name": "wf",
        "label": "w",
        "trigger": {"manual": True},
        "for_each": {"object_type": "청구"},
        "steps": [{"action": "act", "params": {"n": "1"}}],
    }
    ctx = ctx_with(act)
    assert _codes(wf, ctx) == set()
    assert _codes(wf) == {"unknown_action"}
    assert _codes({**wf, "steps": [{"action": "act"}]}, ctx) == {"missing_param"}
    assert _codes({**wf, "steps": [{"action": "act", "params": {"n": "'a'", "x": "1"}}]}, ctx) == {
        "type_error",
        "unknown_param",
    }
    assert _codes({**wf, "for_each": {"object_type": "계약"}}, ctx) == {"wrong_object_type"}
    assert _codes({**wf, "trigger": {"on_action": "act"}}, ctx) == {"cycle"}
    assert _codes({**wf, "trigger": {"on_action": "nope"}}, ctx) == {"unknown_action"}
    assert _codes({**wf, "for_each": {"object_type": "청구", "where": "청구금액"}}, ctx) == {
        "type_error"
    }
    deep: dict = {"action": "act", "params": {"n": "1"}}
    for _ in range(6):
        deep = {"if": "true", "then": [deep]}
    assert "too_deep" in _codes({**wf, "steps": [deep]}, ctx)


def test_sample_specs_validate_against_the_quickstart_schema():
    files = sorted(SAMPLES.glob("*.yaml"))
    assert {f.name for f in files} >= {"functions.yaml", "actions.yaml", "workflows.yaml"}
    parsed = [
        specs.parse_spec(d) for f in files for d in specs.load_documents(f.read_text("utf-8"))
    ]
    names = {(s.kind, s.name) for s in parsed}
    assert {
        ("function", "연체일수"),
        ("function", "연체여부"),
        ("function", "미수금"),
        ("function", "남은일수"),
        ("function", "미수청구건수"),
        ("action", "독촉단계기록"),
        ("workflow", "연체독촉"),
    } <= names
    ctx = specs.LogicContext(SCHEMA)
    for s in parsed:
        getattr(ctx, s.kind + "s")[s.name] = s
    for s in parsed:
        assert specs.validate(s, ctx) == [], s.name


def test_sample_specs_validate_when_the_build_labels_classes_in_english():
    """The samples name types and links by table, so they work whatever language the
    build gave the class labels; field references ignore case (``청구ID`` vs ``청구id``)."""
    schema = _schema(fx.ENGLISH, lower=True)
    assert {t.label for t in schema.types.values()} == {"Contract", "Customer", "Invoice"}
    parsed = [
        specs.parse_spec(d)
        for f in sorted(SAMPLES.glob("*.yaml"))
        for d in specs.load_documents(f.read_text("utf-8"))
    ]
    assert {s.object_type for s in parsed if hasattr(s, "object_type")} <= {
        "invoices",
        "contracts",
    }
    ctx = specs.LogicContext(schema)
    for s in parsed:
        getattr(ctx, s.kind + "s")[s.name] = s
    for s in parsed:
        assert specs.validate(s, ctx) == [], s.name


def test_types_resolve_by_id_class_label_and_table():
    schema = _schema(fx.ENGLISH)
    inv = schema.types[INV]
    for ref in (INV, "Invoice", "invoice", "invoices", "public.invoices", "INVOICES"):
        assert schema.resolve(ref) is inv, ref
    assert schema.resolve("nope") is None and schema.resolve("") is None
    # links by the target's table too
    con = schema.types[CON]
    assert [lk.target for lk in con.link("invoices", schema)] == [INV]
    assert [lk.target for lk in inv.link("contracts", schema)] == [CON]
    scope_ = ex.Scope(schema, con, {}, lambda n: None)
    assert ex.compile_expr("count(invoices where 수납상태 != '완납')", scope_).type == "number"
    assert ex.compile_expr("contracts.계약명", ex.Scope(schema, inv, {}, lambda n: None))


def test_an_ambiguous_type_reference_is_an_error():
    from ontology.objects import naming

    def n(name, label, table, status="active", schema="public"):
        return naming.TypeNames(name, f"ot_{name}", label, schema, table, status)

    items = [n("a", "Invoice", "invoices"), n("b", "Invoice", "invoices", schema="billing")]
    found = naming.matches(items, "Invoice", lambda x: x)
    assert [x.name for x in found] == ["a", "b"]  # ambiguous
    assert [x.name for x in naming.matches(items, "billing.invoices", lambda x: x)] == ["b"]
    assert [x.name for x in naming.matches(items, "ot_a", lambda x: x)] == ["a"]
    # a retired or proposed twin does not make an active type ambiguous
    items[1] = n("b", "Invoice", "invoices", status="retired")
    assert [x.name for x in naming.matches(items, "invoices", lambda x: x)] == ["a"]
    items[1] = n("b", "Invoice", "invoices", status="proposed")
    assert [x.name for x in naming.matches(items, "Invoice", lambda x: x)] == ["a"]
    # an exact label wins over a table of the same name
    items = [n("a", "invoices", "bills"), n("b", "Bill", "invoices")]
    assert [x.name for x in naming.matches(items, "invoices", lambda x: x)] == ["a"]
    # specs: an ambiguous object_type is reported with the candidates
    schema = Schema(
        {
            "a": TypeInfo("a", "Invoice", "id", "active", table="invoices", schema="public"),
            "b": TypeInfo("b", "Invoice", "id", "active", table="invoices", schema="billing"),
        }
    )
    errors = specs.validate(
        specs.parse_spec(
            {
                "kind": "function",
                "name": "f",
                "object_type": "Invoice",
                "returns": "number",
                "expr": "1",
            }
        ),
        specs.LogicContext(schema),
    )
    assert errors[0]["code"] == "ambiguous_object_type"
    assert "billing.invoices" in errors[0]["message"]


# --- store --------------------------------------------------------------------------------


@pytest.fixture
def store(isolated_env) -> Store:
    return Store.for_home(Settings.from_env().home)


def test_store_versions_history_and_status(store):
    defs = store.project().logic_defs()
    a = defs.save("function", "f", {"x": 1}, actor="a@x")
    assert (a.version, a.status, a.created_by) == (1, "draft", "a@x")
    assert defs.save("function", "f", {"x": 1}).version == 1  # unchanged draft: no new version
    defs.set_status("function", "f", "active", actor="b")
    b = defs.save("function", "f", {"x": 2}, actor="c")
    assert (b.version, b.status, b.spec) == (2, "draft", {"x": 2})  # edited: a draft again
    defs.set_status("function", "f", "active")
    defs.set_status("function", "f", "broken", reason="gone")
    assert defs.get("function", "f").broken_reason == "gone"
    assert defs.status_before_broken("function", "f") == "active"
    history = defs.history("function", "f")
    assert [h["status"] for h in history] == ["broken", "active", "draft", "active", "draft"]
    assert history[-1]["spec"] == {"x": 1} and history[0]["note"] == "gone"
    assert [d.name for d in defs.list("function", "broken")] == ["f"]
    assert defs.list("action") == [] and defs.get("action", "f") is None
    with pytest.raises(ValueError):
        defs.save("macro", "x", {})
    with pytest.raises(ValueError):
        defs.set_status("function", "f", "bogus")
    assert defs.set_status("function", "nope", "active") is None
    # per project
    assert store.create_project("beta", name="Beta").logic_defs().list() == []


# --- registry -----------------------------------------------------------------------------


@pytest.fixture
def backend(monkeypatch):
    return fx.install_backend(monkeypatch)


def test_registry_save_validate_activate(store, backend):
    view = fx.seed(store)
    with pytest.raises(ObjectError) as e:
        registry.save(view, {**MIUSU, "expr": "청구금액 + 없음"})
    assert e.value.code == "invalid_spec" and e.value.extra["errors"][0]["pos"] == 7
    with pytest.raises(ObjectError):
        registry.save(view, "kind: function\nname: x\n")
    saved = registry.save(view, MIUSU, actor="ed")
    assert (saved.status, saved.version, saved.spec["object_type"]) == ("draft", 1, "청구")
    caller = {**MIUSU, "name": "두배", "expr": "미수금() * 2"}
    registry.save(view, caller)
    with pytest.raises(ObjectError) as e:
        registry.set_status(view, "function", "두배", "active")
    assert e.value.code == "reference_not_active"
    registry.set_status(view, "function", "미수금", "active")
    assert registry.set_status(view, "function", "두배", "active").status == "active"
    # a new version of 미수금 is a draft: 두배 depends on it and breaks until it is active
    registry.save(view, {**MIUSU, "expr": "청구금액"})
    assert view.logic_defs().get("function", "두배").status == "broken"
    registry.set_status(view, "function", "미수금", "active")
    assert view.logic_defs().get("function", "두배").status == "active"
    with pytest.raises(ObjectError) as e:
        registry.set_status(view, "function", "미수금", "broken")
    assert e.value.code == "invalid_status"
    with pytest.raises(ObjectError) as e:
        registry.get(view, "function", "nope")
    assert e.value.code == "unknown_definition"
    spec, errors = registry.check(view, {**MIUSU, "object_type": "없음"})
    assert spec is not None and errors[0]["code"] == "unknown_object_type"


def test_revalidation_breaks_and_restores_on_snapshot_and_type_changes(store, backend):
    view = fx.seed(store)
    registry.save(view, MIUSU)
    registry.set_status(view, "function", "미수금", "active")
    registry.save(view, {**MIUSU, "name": "초안"})
    # a snapshot without the invoices class: both break (keeping what they were)
    from tests.test_objects import seed as seed_raw

    m = fx.model()
    del m.classes[INV]
    seed_raw(store, m)
    registry.ensure_validated(view)
    a, d = view.logic_defs().get("function", "미수금"), view.logic_defs().get("function", "초안")
    assert a.status == d.status == "broken" and "청구" in a.broken_reason
    # back in the next snapshot: restored to active and draft
    seed_raw(store, fx.model())
    registry.ensure_validated(view)
    assert view.logic_defs().get("function", "미수금").status == "active"
    assert view.logic_defs().get("function", "초안").status == "draft"
    # an object type retired by hand: broken; active again: restored
    view.object_types().set_status(INV, "retired")
    registry.ensure_validated(view)
    assert view.logic_defs().get("function", "미수금").status == "broken"
    view.object_types().set_status(INV, "active")
    registry.ensure_validated(view)
    assert view.logic_defs().get("function", "미수금").status == "active"
    history = view.logic_defs().history("function", "미수금")
    assert {h["changed_by"] for h in history if h["status"] == "broken"} == {"system"}
    # a retired definition is left alone
    registry.set_status(view, "function", "초안", "retired")
    view.object_types().set_status(INV, "retired")
    registry.ensure_validated(view)
    assert view.logic_defs().get("function", "초안").status == "retired"


# --- evaluation ---------------------------------------------------------------------------

FUNCS = [
    MIUSU,
    {
        **MIUSU,
        "name": "연체일수",
        "expr": "if(수납상태 == '완납', 0, max(today() - (청구일 + 30), 0))",
    },
    {**MIUSU, "name": "연체여부", "returns": "bool", "expr": "연체일수() > 0"},
    {
        "kind": "function",
        "name": "미수청구건수",
        "object_type": "계약",
        "returns": "number",
        "expr": "count(청구 where 수납상태 != '완납')",
    },
    {
        "kind": "function",
        "name": "담당자",
        "object_type": "계약",
        "returns": "text",
        "expr": "고객.담당자",
    },
    {
        "kind": "function",
        "name": "망가짐",
        "object_type": "청구",
        "returns": "number",
        "expr": "청구금액 / (수납금액 - 수납금액)",
    },
]


def _activate_all(view, funcs=FUNCS):
    for f in funcs:
        registry.save(view, f)
    for f in funcs:
        registry.set_status(view, "function", f["name"], "active")


def test_computed_values_on_records_with_links_batched(store, backend, monkeypatch):
    view = fx.seed(store)
    _activate_all(view)
    from ontology.objects import service

    settings = Settings.from_env()
    invs = service.search(settings, view, INV)["items"]
    values = functions.computed(settings, view, view.object_types().get(INV), invs, when=TODAY)
    by_key = dict(zip([i["key"] for i in invs], values, strict=True))
    assert by_key["INV-2"]["미수금"] == {"value": 6000000, "type": "number"}
    assert by_key["INV-2"]["연체일수"]["value"] == 55
    assert by_key["INV-2"]["연체여부"]["value"] is True
    assert (
        by_key["INV-1"]["연체일수"]["value"] == 0 and by_key["INV-1"]["연체여부"]["value"] is False
    )
    assert by_key["INV-3"]["연체여부"]["value"] is False
    assert by_key["INV-1"]["망가짐"]["error"]["code"] == "division_by_zero"
    assert set(by_key["INV-1"]) == {"미수금", "연체일수", "연체여부", "망가짐"}
    # contracts: links fetched once for the page, not per object
    backend.queries.clear()
    cons = service.search(settings, view, CON)["items"]
    backend.queries.clear()
    values = functions.computed(settings, view, view.object_types().get(CON), cons, when=TODAY)
    got = {c["key"]: v for c, v in zip(cons, values, strict=True)}
    assert got["K2024-003"]["미수청구건수"]["value"] == 2
    assert got["K2019-001"]["미수청구건수"]["value"] == 0
    assert got["K2024-003"]["담당자"]["value"] == "최하은"
    assert got["K2025-009"]["담당자"]["value"] is None
    selects = [q for q in backend.queries if q.any_of]
    assert len(selects) == 2, [(q.table, q.any_of) for q in selects]
    # edits made by actions are seen by functions
    inv = view.object_types().get(INV)
    overlay.record_edit(view, inv, "INV-2", "수납금액", 9000000, source_row=fx.INVOICES[1])
    one = functions.call(settings, view, "미수금", "INV-2", when=TODAY)
    assert one["value"] == 0 and one["key"] == "INV-2"


def test_call_test_and_list(store, backend):
    view = fx.seed(store)
    settings = Settings.from_env()
    registry.save(view, MIUSU)
    with pytest.raises(ObjectError) as e:
        functions.call(settings, view, "미수금", "INV-2")
    assert e.value.code == "function_not_active"
    # a draft can be tested by name, an unsaved spec too
    out = functions.test(settings, view, "미수금", ["INV-2", "INV-3"], when=TODAY)
    assert [r["value"] for r in out["results"]] == [6000000, 2000000] and out["errors"] == []
    unsaved = functions.test(
        settings, view, {**MIUSU, "name": "새것", "expr": "청구금액 * 2"}, ["INV-1"]
    )
    assert unsaved["results"][0]["value"] == 9000000
    bad = functions.test(settings, view, {**MIUSU, "expr": "청구금액 +"}, ["INV-1"])
    assert bad["results"] == [] and bad["errors"][0]["code"] == "syntax_error"
    with pytest.raises(ObjectError) as e:
        functions.test(settings, view, "미수금", ["NOPE"])
    assert e.value.code == "unknown_object"
    with pytest.raises(ObjectError):
        functions.test(settings, view, "미수금", [])
    registry.set_status(view, "function", "미수금", "active")
    assert functions.call(settings, view, "미수금", "INV-3", type_name="청구")["value"] == 2000000
    with pytest.raises(ObjectError) as e:
        functions.call(settings, view, "미수금", "INV-3", type_name="계약")
    assert e.value.code == "wrong_object_type"
    with pytest.raises(ObjectError) as e:
        functions.call(settings, view, "없음", "INV-3")
    assert e.value.code == "unknown_function"
    listed = functions.list_active(view)
    assert [f["name"] for f in listed] == ["미수금"] and listed[0]["object_type"] == INV
    assert functions.list_active(view, "계약") == []
    # parameters: given, defaulted, refused
    registry.save(
        view,
        {
            **MIUSU,
            "name": "초과",
            "returns": "bool",
            "expr": "청구금액 > param.기준",
            "params": [{"name": "기준", "type": "number", "default": 5000000}],
        },
    )
    registry.set_status(view, "function", "초과", "active")
    assert functions.call(settings, view, "초과", "INV-2")["value"] is True
    assert (
        functions.call(settings, view, "초과", "INV-2", params={"기준": "10000000"})["value"]
        is False
    )
    with pytest.raises(ObjectError) as e:
        functions.call(settings, view, "초과", "INV-2", params={"x": 1})
    assert e.value.code == "unknown_param"


def test_link_budget_is_an_error_per_value(store, backend, monkeypatch):
    view = fx.seed(store)
    _activate_all(view)
    monkeypatch.setattr(functions, "MAX_LINK_QUERIES", 0)
    from ontology.objects import service

    settings = Settings.from_env()
    cons = service.search(settings, view, CON)["items"]
    values = functions.computed(settings, view, view.object_types().get(CON), cons, when=TODAY)
    assert values[0]["담당자"]["error"]["code"] == "link_budget"


def test_approval_when_without_a_role_says_what_to_choose():
    action = {
        "kind": "action",
        "name": "a",
        "label": "A",
        "object_type": "invoices",
        "effects": [{"set": "x", "value": "1", "operational": True}],
        "approval": {"role": "none", "when": "true"},
    }
    errors = specs.validate(specs.parse_spec(action), specs.LogicContext(SCHEMA))
    found = [e for e in errors if e["path"] == "approval.when"]
    assert found and "choose reviewer or owner" in found[0]["message"]
