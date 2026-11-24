# SPDX-License-Identifier: MIT
"""The expression language of functions, action conditions and workflow filters.

Text is tokenized and parsed by hand (recursive descent) into an AST, type-checked
against a :class:`~autoontology.logic.schema.Schema` (:func:`compile_expr`) and run by
closures over plain values. Nothing is ever handed to Python's ``eval``/``exec``/
``compile``: an identifier only ever names a field, a link, a parameter, a builtin or a
defined function, or it is an error.

Grammar (keywords any case; ``#`` starts a comment)::

    expr    := or
    or      := and ("or" and)*
    and     := not ("and" not)*
    not     := "not" not | cmp
    cmp     := add [("=="|"="|"!="|"<>"|"<"|"<="|">"|">=") add
                    | ["not"] "in" "[" [expr ("," expr)*] "]"]
    add     := mul (("+"|"-") mul)*
    mul     := unary (("*"|"/"|"%") unary)*
    unary   := "-" unary | primary
    primary := NUMBER | STRING | "true" | "false" | "null" | "(" expr ")"
             | path ["(" [expr ("," expr)* ["where" expr]] ")"]
    path    := name ("." name)*
    name    := IDENT (Unicode letters, digits, _) | `back-quoted name`

Names: ``field`` (a column by name or label), ``link.field`` / ``link.link.field``
(outgoing links), ``param.x``, ``fn.name(...)`` or ``name(...)`` (another function of the
same object type, evaluated on the current object). Incoming links are used only in
aggregates: ``count(link [where cond])``, ``exists(link [where cond])``,
``sum(link.field [where cond])``, ``min(link.field)`` / ``max(link.field)``; ``cond`` is
checked on the linked object.

Builtins: ``today()``, ``date("2026-01-31")``, ``days_between(a, b)`` (b - a in days),
``add_days(d, n)``, ``coalesce(a, b, ...)``, ``if(c, a, b)``, ``len``, ``lower``,
``upper``, ``contains(text, part)``, ``round(x[, digits])``, ``abs``, ``min``/``max`` of
two or more values, ``text(x)``, ``number(x)``. ``date - date`` is a number of days,
``date +/- number`` a date, ``text + text`` concatenates.

Nulls: arithmetic and ordering with null are null; ``==``/``!=`` compare null as a value
(``x == null``); ``and``/``or``/``not`` are three-valued (``false and null`` is false);
``if`` takes a null condition as false.

Limits: :data:`MAX_SOURCE_CHARS`, :data:`MAX_DEPTH`, :data:`MAX_NODES`,
:data:`MAX_CALL_DEPTH`, and per evaluation :data:`STEP_BUDGET` steps (the link fetch
budget is the loader's).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from autoontology.logic.schema import LinkInfo, Schema, TypeInfo

MAX_SOURCE_CHARS = 4000
MAX_STRING_CHARS = 1000
MAX_NAME_CHARS = 200
MAX_NUMBER_CHARS = 40
MAX_DEPTH = 32
MAX_NODES = 400
MAX_LIST_ITEMS = 100
MAX_PATH = 4
#: function -> function calls in one evaluation, at most
MAX_CALL_DEPTH = 8
#: steps (nodes run) per evaluation of one object
STEP_BUDGET = 20_000
#: text values are cut off with an error past this
MAX_TEXT_VALUE = 10_000
MAX_ABS_NUMBER = 1e30

#: a property name usable as a bare identifier
NAME_LIKE = re.compile(r"^[^\W\d]\w{0,62}$")

KEYWORDS = ("and", "or", "not", "in", "true", "false", "null", "where")
TYPES = ("number", "text", "bool", "date", "null", "any")
AGGREGATES = ("count", "exists", "sum", "min", "max")


class ExprError(Exception):
    """A syntax, name or type error, at character offset *pos* of the source."""

    def __init__(self, code: str, message: str, pos: int | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.pos = code, message, pos

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.pos is not None:
            out["pos"] = self.pos
        return out


class EvalError(Exception):
    """A failure while evaluating (division by zero, a bad source value, a budget)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


# --- tokens -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    kind: str  # num | str | name | op | kw | end
    value: Any
    pos: int
    quoted: bool = False


_OPS2 = ("==", "!=", "<>", "<=", ">=")
_OPS1 = "=<>+-*/%()[],."


def _name_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _name_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def tokenize(text: str) -> list[Token]:
    if not isinstance(text, str):
        raise ExprError("invalid_expression", "an expression is text", 0)
    if len(text) > MAX_SOURCE_CHARS:
        raise ExprError("too_long", f"an expression is at most {MAX_SOURCE_CHARS} characters", 0)
    out: list[Token] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        start = i
        if ch.isdigit() and ch.isascii():
            while i < n and text[i].isascii() and text[i].isdigit():
                i += 1
            if i + 1 < n and text[i] == "." and text[i + 1].isascii() and text[i + 1].isdigit():
                i += 1
                while i < n and text[i].isascii() and text[i].isdigit():
                    i += 1
            raw = text[start:i]
            if len(raw) > MAX_NUMBER_CHARS:
                raise ExprError("number_too_long", "a number literal is too long", start)
            if i < n and _name_char(text[i]):
                raise ExprError("invalid_number", f"invalid number {text[start : i + 1]!r}", start)
            out.append(Token("num", float(raw) if "." in raw else int(raw), start))
            continue
        if ch in "'\"":
            quote, i, buf = ch, i + 1, []
            while True:
                if i >= n:
                    raise ExprError("unterminated_string", "the text literal is not closed", start)
                c = text[i]
                if c == quote:
                    i += 1
                    break
                if c == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                    i += 2
                    continue
                buf.append(c)
                i += 1
                if len(buf) > MAX_STRING_CHARS:
                    raise ExprError(
                        "string_too_long",
                        f"a text literal is at most {MAX_STRING_CHARS} characters",
                        start,
                    )
            out.append(Token("str", "".join(buf), start))
            continue
        if ch == "`":
            end = text.find("`", i + 1)
            if end < 0:
                raise ExprError("unterminated_name", "the `quoted name` is not closed", start)
            name = text[i + 1 : end]
            if not name.strip() or "\n" in name or len(name) > MAX_NAME_CHARS:
                raise ExprError("invalid_name", "invalid `quoted name`", start)
            out.append(Token("name", name, start, quoted=True))
            i = end + 1
            continue
        if _name_start(ch):
            while i < n and _name_char(text[i]):
                i += 1
            word = text[start:i]
            if len(word) > MAX_NAME_CHARS:
                raise ExprError("invalid_name", "a name is too long", start)
            if word.lower() in KEYWORDS:
                out.append(Token("kw", word.lower(), start))
            else:
                out.append(Token("name", word, start))
            continue
        two = text[i : i + 2]
        if two in _OPS2:
            out.append(Token("op", "!=" if two == "<>" else two, start))
            i += 2
            continue
        if ch in _OPS1:
            out.append(Token("op", "==" if ch == "=" else ch, start))
            i += 1
            continue
        raise ExprError("unexpected_character", f"unexpected character {ch!r}", start)
    out.append(Token("end", None, n))
    return out


# --- AST --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    pos: int


@dataclass(frozen=True)
class Lit(Node):
    value: Any
    type: str


@dataclass(frozen=True)
class Path(Node):
    parts: tuple[str, ...]


@dataclass(frozen=True)
class Unary(Node):
    op: str
    operand: Node


@dataclass(frozen=True)
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass(frozen=True)
class InList(Node):
    left: Node
    items: tuple[Node, ...]
    negate: bool


@dataclass(frozen=True)
class Call(Node):
    func: tuple[str, ...]
    args: tuple[Node, ...]
    where: Node | None = None


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens, self.i, self.depth, self.nodes = tokens, 0, 0, 0

    @property
    def tok(self) -> Token:
        return self.tokens[self.i]

    def _next(self) -> Token:
        tok = self.tokens[self.i]
        self.i += 1
        return tok

    def _is(self, kind: str, value: Any = None) -> bool:
        t = self.tok
        return t.kind == kind and (value is None or t.value == value)

    def _expect(self, kind: str, value: Any, what: str) -> Token:
        if not self._is(kind, value):
            raise ExprError("syntax_error", f"expected {what}, found {self._show()}", self.tok.pos)
        return self._next()

    def _show(self) -> str:
        t = self.tok
        if t.kind == "end":
            return "end of expression"
        if t.kind == "str":
            return "a text literal"
        return repr(str(t.value))

    def _node(self, node: Node) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ExprError("too_complex", f"an expression has at most {MAX_NODES} parts", node.pos)
        return node

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExprError(
                "too_deep", f"an expression nests at most {MAX_DEPTH} deep", self.tok.pos
            )

    def parse(self) -> Node:
        node = self.expr()
        if not self._is("end"):
            raise ExprError("syntax_error", f"unexpected {self._show()}", self.tok.pos)
        return node

    def expr(self) -> Node:
        self._enter()
        try:
            return self.or_()
        finally:
            self.depth -= 1

    def or_(self) -> Node:
        left = self.and_()
        while self._is("kw", "or"):
            pos = self._next().pos
            left = self._node(Binary(pos, "or", left, self.and_()))
        return left

    def and_(self) -> Node:
        left = self.not_()
        while self._is("kw", "and"):
            pos = self._next().pos
            left = self._node(Binary(pos, "and", left, self.not_()))
        return left

    def not_(self) -> Node:
        if self._is("kw", "not"):
            pos = self._next().pos
            self._enter()
            try:
                return self._node(Unary(pos, "not", self.not_()))
            finally:
                self.depth -= 1
        return self.cmp()

    def cmp(self) -> Node:
        left = self.add()
        t = self.tok
        if t.kind == "op" and t.value in ("==", "!=", "<", "<=", ">", ">="):
            self._next()
            node = self._node(Binary(t.pos, t.value, left, self.add()))
            nt = self.tok
            if nt.kind == "op" and nt.value in ("==", "!=", "<", "<=", ">", ">="):
                raise ExprError("syntax_error", "comparisons do not chain: use and", nt.pos)
            return node
        negate = False
        if (
            self._is("kw", "not")
            and self.tokens[self.i + 1].kind == "kw"
            and (self.tokens[self.i + 1].value == "in")
        ):
            self._next()
            negate = True
        if self._is("kw", "in"):
            pos = self._next().pos
            self._expect("op", "[", "'[' after in")
            items: list[Node] = []
            if not self._is("op", "]"):
                items.append(self.expr())
                while self._is("op", ","):
                    self._next()
                    items.append(self.expr())
                    if len(items) > MAX_LIST_ITEMS:
                        raise ExprError(
                            "too_complex", f"a list has at most {MAX_LIST_ITEMS} items", pos
                        )
            self._expect("op", "]", "']'")
            return self._node(InList(pos, left, tuple(items), negate))
        return left

    def add(self) -> Node:
        left = self.mul()
        while self.tok.kind == "op" and self.tok.value in ("+", "-"):
            t = self._next()
            left = self._node(Binary(t.pos, t.value, left, self.mul()))
        return left

    def mul(self) -> Node:
        left = self.unary()
        while self.tok.kind == "op" and self.tok.value in ("*", "/", "%"):
            t = self._next()
            left = self._node(Binary(t.pos, t.value, left, self.unary()))
        return left

    def unary(self) -> Node:
        if self._is("op", "-"):
            pos = self._next().pos
            self._enter()
            try:
                return self._node(Unary(pos, "-", self.unary()))
            finally:
                self.depth -= 1
        return self.primary()

    def primary(self) -> Node:
        t = self.tok
        if t.kind == "num":
            self._next()
            return self._node(Lit(t.pos, t.value, "number"))
        if t.kind == "str":
            self._next()
            return self._node(Lit(t.pos, t.value, "text"))
        if t.kind == "kw" and t.value in ("true", "false"):
            self._next()
            return self._node(Lit(t.pos, t.value == "true", "bool"))
        if t.kind == "kw" and t.value == "null":
            self._next()
            return self._node(Lit(t.pos, None, "null"))
        if self._is("op", "("):
            self._next()
            inner = self.expr()
            self._expect("op", ")", "')'")
            return inner
        if t.kind == "name":
            parts = [self._next().value]
            while self._is("op", "."):
                self._next()
                if self.tok.kind != "name":
                    raise ExprError(
                        "syntax_error",
                        f"expected a name after '.', found {self._show()}",
                        self.tok.pos,
                    )
                parts.append(self._next().value)
                if len(parts) > MAX_PATH + 1:
                    raise ExprError("path_too_long", "a path has too many parts", t.pos)
            if self._is("op", "("):
                return self._call(t.pos, tuple(parts))
            return self._node(Path(t.pos, tuple(parts)))
        raise ExprError("syntax_error", f"unexpected {self._show()}", t.pos)

    def _call(self, pos: int, func: tuple[str, ...]) -> Node:
        self._expect("op", "(", "'('")
        args: list[Node] = []
        where: Node | None = None
        if not self._is("op", ")"):
            args.append(self.expr())
            while self._is("op", ","):
                self._next()
                args.append(self.expr())
                if len(args) > MAX_LIST_ITEMS:
                    raise ExprError("too_complex", "too many arguments", pos)
            if self._is("kw", "where"):
                self._next()
                where = self.expr()
        self._expect("op", ")", "')' or ','")
        return self._node(Call(pos, func, tuple(args), where))


def parse(text: str) -> Node:
    """The AST of *text*; :class:`ExprError` with the position of the first problem."""
    tokens = tokenize(text)
    if len(tokens) > MAX_NODES * 3:
        raise ExprError("too_complex", "the expression is too long", 0)
    return _Parser(tokens).parse()


# --- runtime ----------------------------------------------------------------------------


@dataclass
class Obj:
    """An object at evaluation time: its type and values (source row + edits)."""

    type: TypeInfo
    values: dict[str, Any]


class Loader(Protocol):
    def follow(self, obj: Obj, link: LinkInfo) -> list[Obj]:
        """The objects *link* reaches from *obj* (out: at most one)."""
        ...


@dataclass
class EvalContext:
    today: dt.date
    loader: Loader | None = None
    #: the compiled function a call runs (only active ones, at execution)
    function: Callable[[str], Compiled | None] = lambda name: None
    step_budget: int = STEP_BUDGET
    steps: int = 0
    stack: list[str] = field(default_factory=list)

    def step(self) -> None:
        self.steps += 1
        if self.steps > self.step_budget:
            raise EvalError("budget_exceeded", "the evaluation took too many steps")


@dataclass
class Env:
    obj: Obj
    params: dict[str, Any]
    ctx: EvalContext


Run = Callable[[Env], Any]


@dataclass
class Compiled:
    """A checked expression: its value ``type``, the closure that runs it, the defined
    functions it calls and the links its paths and aggregates follow first."""

    type: str
    run: Run
    functions: set[str] = field(default_factory=set)
    links: set[str] = field(default_factory=set)
    source: str = ""

    def evaluate(self, obj: Obj, ctx: EvalContext, params: dict[str, Any] | None = None) -> Any:
        return self.run(Env(obj, params or {}, ctx))


def type_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dt.date):
        return "date"
    return "text"


def parse_date(value: str) -> dt.date | None:
    text = value.strip()
    try:
        if len(text) >= 10 and (len(text) == 10 or text[10] in "T "):
            return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None
    return None


def _number(value: Any) -> int | float:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value) or abs(value) > MAX_ABS_NUMBER:
            raise EvalError("number_out_of_range", "a number is out of range")
        if value.is_integer() and abs(value) < 2**53:
            return int(value)
    elif isinstance(value, int) and abs(value) > MAX_ABS_NUMBER:
        raise EvalError("number_out_of_range", "a number is out of range")
    return value


def coerce(value: Any, kind: str, what: str = "value") -> Any:
    """A source value (JSON from the reader or the edit layer) as *kind*."""
    if value is None:
        return None
    if kind == "any":
        if isinstance(value, (bool, int, float, str)):
            return _number(value) if type_of(value) == "number" else value
        return str(value)
    if kind == "text":
        if isinstance(value, bool):
            return "true" if value else "false"
        return value if isinstance(value, str) else str(value)
    if kind == "number":
        if isinstance(value, bool):
            raise EvalError("bad_value", f"{what} is not a number")
        if isinstance(value, (int, float)):
            return _number(value)
        text = str(value).strip().replace(",", "")
        if text == "":
            return None
        try:
            return _number(float(text) if any(c in text for c in ".eE") else int(text))
        except (ValueError, OverflowError):
            raise EvalError("bad_value", f"{what} is not a number: {str(value)[:40]!r}") from None
    if kind == "date":
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        if str(value).strip() == "":
            return None
        parsed = parse_date(str(value))
        if parsed is None:
            raise EvalError("bad_value", f"{what} is not a date: {str(value)[:40]!r}")
        return parsed
    if kind == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "t", "yes", "y", "1"):
            return True
        if text in ("false", "f", "no", "n", "0"):
            return False
        if text == "":
            return None
        raise EvalError("bad_value", f"{what} is not true/false: {str(value)[:40]!r}")
    return value


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _text(v: str) -> str:
    if len(v) > MAX_TEXT_VALUE:
        raise EvalError("text_too_long", f"a text value is longer than {MAX_TEXT_VALUE}")
    return v


def _days(n: Any) -> dt.timedelta:
    if not _is_num(n) or abs(n) > 1_000_000:
        raise EvalError("type_error", "days must be a number")
    return dt.timedelta(days=int(round(n)))


def _arith(op: str, a: Any, b: Any) -> Any:
    if a is None or b is None:
        return None
    try:
        if op == "+":
            if _is_num(a) and _is_num(b):
                return _number(a + b)
            if isinstance(a, str) and isinstance(b, str):
                return _text(a + b)
            if isinstance(a, dt.date) and _is_num(b):
                return a + _days(b)
            if _is_num(a) and isinstance(b, dt.date):
                return b + _days(a)
        elif op == "-":
            if _is_num(a) and _is_num(b):
                return _number(a - b)
            if isinstance(a, dt.date) and isinstance(b, dt.date):
                return (a - b).days
            if isinstance(a, dt.date) and _is_num(b):
                return a - _days(b)
        elif _is_num(a) and _is_num(b):
            if op == "*":
                return _number(a * b)
            if b == 0:
                raise EvalError("division_by_zero", "division by zero")
            if op == "/":
                return _number(a / b)
            return _number(a % b)
    except OverflowError:
        raise EvalError("number_out_of_range", "a number is out of range") from None
    raise EvalError("type_error", f"cannot apply {op} to {type_of(a)} and {type_of(b)}")


def _equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    ta, tb = type_of(a), type_of(b)
    return ta == tb and a == b


def _compare(op: str, a: Any, b: Any) -> Any:
    if op == "==":
        return _equal(a, b)
    if op == "!=":
        return not _equal(a, b)
    if a is None or b is None:
        return None
    ta, tb = type_of(a), type_of(b)
    if ta != tb or ta == "bool":
        raise EvalError("type_error", f"cannot order {ta} and {tb}")
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _bool(v: Any, what: str = "a condition") -> bool | None:
    if v is None or isinstance(v, bool):
        return v
    raise EvalError("type_error", f"{what} is {type_of(v)}, not bool")


# --- compiler ---------------------------------------------------------------------------


@dataclass
class FunctionSig:
    """A defined function as a call target: its object type, result and parameters."""

    name: str
    object_type: str
    returns: str
    params: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Scope:
    schema: Schema
    type: TypeInfo
    params: dict[str, str] = field(default_factory=dict)
    functions: Callable[[str], FunctionSig | None] = lambda name: None


def unify(a: str, b: str) -> str | None:
    """The common type of *a* and *b* (None: incompatible)."""
    if a == b:
        return a
    if a == "null":
        return b
    if b == "null":
        return a
    if "any" in (a, b):
        return "any"
    return None


def fits(actual: str, wanted: str) -> bool:
    return actual in (wanted, "null", "any") or wanted == "any"


class _Compiler:
    def __init__(self, scope: Scope) -> None:
        self.scope = scope
        self.functions: set[str] = set()
        self.links: set[str] = set()

    def err(self, node: Node, code: str, message: str) -> ExprError:
        return ExprError(code, message, node.pos)

    def compile(self, node: Node, scope: Scope) -> tuple[str, Run]:
        if isinstance(node, Lit):
            return self._lit(node)
        if isinstance(node, Path):
            return self._path(node, scope)
        if isinstance(node, Unary):
            return self._unary(node, scope)
        if isinstance(node, Binary):
            return self._binary(node, scope)
        if isinstance(node, InList):
            return self._in(node, scope)
        if isinstance(node, Call):
            return self._call(node, scope)
        raise self.err(node, "invalid_expression", "unknown expression")  # pragma: no cover

    def _lit(self, node: Lit) -> tuple[str, Run]:
        value = node.value

        def run(env: Env) -> Any:
            env.ctx.step()
            return value

        return node.type, run

    # names -----------------------------------------------------------------------

    def _path(self, node: Path, scope: Scope) -> tuple[str, Run]:
        parts = node.parts
        if parts[0] == "param" and len(parts) == 2:
            name = parts[1]
            if name not in scope.params:
                known = ", ".join(scope.params) or "none"
                raise self.err(
                    node, "unknown_param", f"no parameter {name!r} (parameters: {known})"
                )
            kind = scope.params[name]

            def run_param(env: Env) -> Any:
                env.ctx.step()
                return coerce(env.params.get(name), kind, f"param.{name}")

            return kind, run_param
        if parts[0] == "fn" and len(parts) == 2:
            return self._fn_call(Call(node.pos, parts, ()), scope, parts[1])
        if len(parts) == 1:
            found = scope.type.field(parts[0])
            if found is not None:
                return self._field(found)
            if scope.functions(parts[0]) is not None:
                return self._fn_call(Call(node.pos, parts, ()), scope, parts[0])
            if scope.type.link(parts[0], scope.schema):
                raise self.err(
                    node,
                    "link_not_value",
                    f"{parts[0]!r} is a link: name a field of it ({parts[0]}.field) or use "
                    "count/exists/sum/min/max",
                )
            raise self.err(node, "unknown_name", self._unknown(parts[0], scope.type))
        links, target = self._links(node, scope, parts[:-1], single=True)
        found = target.field(parts[-1])
        if found is None:
            raise self.err(node, "unknown_name", self._unknown(parts[-1], target))
        column, kind = found
        what = ".".join(parts)

        def run_link(env: Env) -> Any:
            env.ctx.step()
            objs = _walk(env, [env.obj], links)
            if not objs:
                return None
            return coerce(objs[0].values.get(column), kind, what)

        return kind, run_link

    def _unknown(self, name: str, t: TypeInfo) -> str:
        fields = ", ".join(t.field_names()[:20])
        return f"{t.label} has no field {name!r} (fields: {fields})"

    def _field(self, found: tuple[str, str]) -> tuple[str, Run]:
        column, kind = found

        def run(env: Env) -> Any:
            env.ctx.step()
            return coerce(env.obj.values.get(column), kind, column)

        return kind, run

    def _links(
        self, node: Node, scope: Scope, names: tuple[str, ...], *, single: bool
    ) -> tuple[list[LinkInfo], TypeInfo]:
        current = scope.type
        out: list[LinkInfo] = []
        for i, name in enumerate(names):
            found = current.link(name, scope.schema)
            if not found:
                kind = "field" if current.field(name) else "link"
                raise self.err(
                    node,
                    "unknown_link",
                    f"{current.label} has no link {name!r}"
                    + (" (it is a field, not a link)" if kind == "field" else ""),
                )
            if len(found) > 1:
                raise self.err(
                    node,
                    "ambiguous_link",
                    f"{name!r} could be the links {', '.join(lk.name for lk in found)}: "
                    "name one of them",
                )
            link = found[0]
            target = scope.schema.types.get(link.target)
            if target is None or target.status != "active":
                raise self.err(
                    node,
                    "link_target_inactive",
                    f"link {name!r} leads to {link.target}, which is not an active object type",
                )
            if single and link.direction != "out":
                raise self.err(
                    node,
                    "link_many",
                    f"{name!r} reaches many {target.label} objects: use count/exists/sum/min/max",
                )
            if i == 0:
                self.links.add(link.name)
            out.append(link)
            current = target
        return out, current

    # operators --------------------------------------------------------------------

    def _unary(self, node: Unary, scope: Scope) -> tuple[str, Run]:
        kind, inner = self.compile(node.operand, scope)
        if node.op == "not":
            if not fits(kind, "bool"):
                raise self.err(node, "type_error", f"not needs a bool, got {kind}")

            def run_not(env: Env) -> Any:
                env.ctx.step()
                v = _bool(inner(env))
                return None if v is None else not v

            return "bool", run_not
        if not fits(kind, "number"):
            raise self.err(node, "type_error", f"'-' needs a number, got {kind}")

        def run_neg(env: Env) -> Any:
            env.ctx.step()
            v = inner(env)
            if v is None:
                return None
            if not _is_num(v):
                raise EvalError("type_error", f"'-' needs a number, got {type_of(v)}")
            return -v

        return "number" if kind != "any" else "any", run_neg

    def _date_literal(self, node: Node, other: str) -> tuple[str, Run] | None:
        """A text literal compared with a date is that date."""
        if other == "date" and isinstance(node, Lit) and node.type == "text":
            parsed = parse_date(node.value)
            if parsed is None:
                raise self.err(node, "invalid_date", f"{node.value!r} is not a date (YYYY-MM-DD)")

            def run(env: Env) -> Any:
                env.ctx.step()
                return parsed

            return "date", run
        return None

    def _binary(self, node: Binary, scope: Scope) -> tuple[str, Run]:
        op = node.op
        lt, left = self.compile(node.left, scope)
        rt, right = self.compile(node.right, scope)
        if op in ("and", "or"):
            for kind, side in ((lt, "left"), (rt, "right")):
                if not fits(kind, "bool"):
                    raise self.err(
                        node, "type_error", f"{op} needs bools, the {side} side is {kind}"
                    )
            if op == "and":

                def run_and(env: Env) -> Any:
                    env.ctx.step()
                    a = _bool(left(env))
                    if a is False:
                        return False
                    b = _bool(right(env))
                    if b is False:
                        return False
                    return None if a is None or b is None else True

                return "bool", run_and

            def run_or(env: Env) -> Any:
                env.ctx.step()
                a = _bool(left(env))
                if a is True:
                    return True
                b = _bool(right(env))
                if b is True:
                    return True
                return None if a is None or b is None else False

            return "bool", run_or
        if op in ("==", "!=", "<", "<=", ">", ">="):
            fixed = self._date_literal(node.right, lt)
            if fixed:
                rt, right = fixed
            fixed = self._date_literal(node.left, rt)
            if fixed:
                lt, left = fixed
            common = unify(lt, rt)
            if common is None:
                raise self.err(node, "type_error", f"cannot compare {lt} with {rt}")
            if op not in ("==", "!=") and common == "bool":
                raise self.err(node, "type_error", f"{op} does not order bools")

            def run_cmp(env: Env) -> Any:
                env.ctx.step()
                return _compare(op, left(env), right(env))

            return "bool", run_cmp
        result = self._arith_type(node, op, lt, rt)

        def run_arith(env: Env) -> Any:
            env.ctx.step()
            return _arith(op, left(env), right(env))

        return result, run_arith

    def _arith_type(self, node: Node, op: str, lt: str, rt: str) -> str:
        if "any" in (lt, rt):
            return "any"
        a = lt if lt != "null" else rt
        b = rt if rt != "null" else lt
        if a == "null":
            return "null"
        table = {
            ("+", "number", "number"): "number",
            ("+", "text", "text"): "text",
            ("+", "date", "number"): "date",
            ("+", "number", "date"): "date",
            ("-", "number", "number"): "number",
            ("-", "date", "date"): "number",
            ("-", "date", "number"): "date",
        }
        if op in ("*", "/", "%"):
            if a == b == "number":
                return "number"
        elif (op, a, b) in table:
            return table[(op, a, b)]
        raise self.err(node, "type_error", f"cannot apply {op} to {lt} and {rt}")

    def _in(self, node: InList, scope: Scope) -> tuple[str, Run]:
        lt, left = self.compile(node.left, scope)
        items: list[Run] = []
        for item in node.items:
            fixed = self._date_literal(item, lt)
            it, run = fixed if fixed else self.compile(item, scope)
            if unify(lt, it) is None:
                raise self.err(item, "type_error", f"cannot compare {lt} with {it} in the list")
            items.append(run)
        negate = node.negate

        def run_in(env: Env) -> Any:
            env.ctx.step()
            value = left(env)
            found = any(_equal(value, item(env)) for item in items)
            return found != negate

        return "bool", run_in

    # calls ------------------------------------------------------------------------

    def _args(self, node: Call, scope: Scope) -> list[tuple[str, Run]]:
        return [self.compile(a, scope) for a in node.args]

    def _arity(self, node: Call, name: str, low: int, high: int | None = None) -> None:
        n = len(node.args)
        high = low if high is None else high
        if n < low or (high >= 0 and n > high):
            want = str(low) if low == high else (f"{low}+" if high < 0 else f"{low}-{high}")
            raise self.err(node, "wrong_arguments", f"{name}() takes {want} arguments, got {n}")

    def _want(self, node: Call, name: str, args: list[tuple[str, Run]], kinds: list[str]) -> None:
        for i, ((kind, _), want) in enumerate(zip(args, kinds, strict=False)):
            if not fits(kind, want):
                raise self.err(
                    node.args[i], "type_error", f"{name}() argument {i + 1} is {kind}, not {want}"
                )

    def _call(self, node: Call, scope: Scope) -> tuple[str, Run]:
        if len(node.func) == 2 and node.func[0] == "fn":
            return self._fn_call(node, scope, node.func[1])
        if len(node.func) != 1:
            raise self.err(node, "unknown_function", f"no function {'.'.join(node.func)!r}")
        name = node.func[0]
        lname = name.lower()
        if node.where is not None and lname not in AGGREGATES:
            raise self.err(node, "syntax_error", "where is for count/exists/sum/min/max")
        if lname in AGGREGATES and self._is_aggregate(node, scope, lname):
            return self._aggregate(node, scope, lname)
        builtin = getattr(self, f"_b_{lname}", None)
        if builtin is not None:
            return builtin(node, scope)
        if scope.functions(name) is not None:
            return self._fn_call(node, scope, name)
        raise self.err(node, "unknown_function", f"no function {name!r}")

    def _is_aggregate(self, node: Call, scope: Scope, name: str) -> bool:
        if name in ("count", "exists", "sum") or node.where is not None:
            return True
        # min/max of one link path is an aggregate; of several values the scalar min/max
        if len(node.args) != 1 or not isinstance(node.args[0], Path):
            return False
        parts = node.args[0].parts
        return (
            len(parts) >= 2
            and parts[0] not in ("param", "fn")
            and bool(scope.type.link(parts[0], scope.schema))
        )

    def _aggregate(self, node: Call, scope: Scope, name: str) -> tuple[str, Run]:
        if len(node.args) != 1 or not isinstance(node.args[0], Path):
            raise self.err(
                node, "wrong_arguments", f"{name}() takes one link path, e.g. {name}(link)"
            )
        parts = node.args[0].parts
        needs_field = name in ("sum", "min", "max")
        link_parts = parts[:-1] if needs_field else parts
        if not link_parts:
            raise self.err(node, "wrong_arguments", f"{name}() takes link.field")
        links, target = self._links(node.args[0], scope, link_parts, single=False)
        column, kind = "", "number"
        if needs_field:
            found = target.field(parts[-1])
            if found is None:
                raise self.err(node.args[0], "unknown_name", self._unknown(parts[-1], target))
            column, kind = found
            if name == "sum" and not fits(kind, "number"):
                raise self.err(
                    node, "type_error", f"sum() needs a number field, {column} is {kind}"
                )
            if kind == "bool":
                raise self.err(node, "type_error", f"{name}() does not order bools")
        where: Run | None = None
        if node.where is not None:
            wkind, where = self.compile(
                node.where, Scope(scope.schema, target, scope.params, scope.functions)
            )
            if not fits(wkind, "bool"):
                raise self.err(node.where, "type_error", f"where needs a bool, got {wkind}")
        what = ".".join(parts)

        def run(env: Env) -> Any:
            env.ctx.step()
            objs = _walk(env, [env.obj], links)
            if where is not None:
                kept = []
                for o in objs:
                    if _bool(where(Env(o, env.params, env.ctx)), "where") is True:
                        kept.append(o)
                objs = kept
            if name == "count":
                return len(objs)
            if name == "exists":
                return bool(objs)
            values = [coerce(o.values.get(column), kind, what) for o in objs]
            values = [v for v in values if v is not None]
            if name == "sum":
                total: Any = 0
                for v in values:
                    total = _arith("+", total, v)
                return total
            if not values:
                return None
            try:
                return min(values) if name == "min" else max(values)
            except TypeError:
                raise EvalError("type_error", f"{name}() over mixed values") from None

        result = {"count": "number", "exists": "bool", "sum": "number"}.get(name, kind)
        return result, run

    def _fn_call(self, node: Call, scope: Scope, name: str) -> tuple[str, Run]:
        sig = scope.functions(name)
        if sig is None:
            raise self.err(node, "unknown_function", f"no function {name!r}")
        if sig.object_type != scope.type.name:
            raise self.err(
                node,
                "wrong_object_type",
                f"function {name!r} is defined on {sig.object_type}, not {scope.type.label}",
            )
        args = self._args(node, scope)
        if len(args) > len(sig.params):
            raise self.err(node, "wrong_arguments", f"{name}() takes {len(sig.params)} arguments")
        for i, ((kind, _), (pname, ptype)) in enumerate(zip(args, sig.params, strict=False)):
            if not fits(kind, ptype):
                raise self.err(
                    node.args[i], "type_error", f"{name}() parameter {pname} is {ptype}, got {kind}"
                )
        self.functions.add(name)
        pnames = [p for p, _ in sig.params]
        runs = [r for _, r in args]

        def run(env: Env) -> Any:
            env.ctx.step()
            ctx = env.ctx
            if name in ctx.stack:
                raise EvalError("cycle", f"function {name} calls itself")
            if len(ctx.stack) >= MAX_CALL_DEPTH:
                raise EvalError(
                    "call_depth", f"functions call each other more than {MAX_CALL_DEPTH} deep"
                )
            target = ctx.function(name)
            if target is None:
                raise EvalError("function_unavailable", f"function {name} is not active")
            params = dict(zip(pnames, (r(env) for r in runs), strict=False))
            ctx.stack.append(name)
            try:
                return target.run(Env(env.obj, params, ctx))
            finally:
                ctx.stack.pop()

        return sig.returns, run

    # builtins ---------------------------------------------------------------------

    def _simple(
        self,
        node: Call,
        scope: Scope,
        name: str,
        kinds: list[str],
        result: str,
        fn: Callable[..., Any],
        low: int | None = None,
    ) -> tuple[str, Run]:
        self._arity(node, name, len(kinds) if low is None else low, len(kinds))
        args = self._args(node, scope)
        self._want(node, name, args, kinds)
        runs = [r for _, r in args]
        coercions = kinds

        def run(env: Env) -> Any:
            env.ctx.step()
            values = [r(env) for r in runs]
            for v, k in zip(values, coercions, strict=False):
                if v is not None and k != "any" and type_of(v) != k:
                    raise EvalError("type_error", f"{name}() got {type_of(v)}, not {k}")
            return fn(*values)

        return result, run

    def _b_today(self, node: Call, scope: Scope) -> tuple[str, Run]:
        self._arity(node, "today", 0)

        def run(env: Env) -> Any:
            env.ctx.step()
            return env.ctx.today

        return "date", run

    def _b_date(self, node: Call, scope: Scope) -> tuple[str, Run]:
        self._arity(node, "date", 1)
        arg = node.args[0]
        if isinstance(arg, Lit) and arg.type == "text" and parse_date(arg.value) is None:
            raise self.err(arg, "invalid_date", f"{arg.value!r} is not a date (YYYY-MM-DD)")
        kind, inner = self.compile(arg, scope)
        if not fits(kind, "text") and kind != "date":
            raise self.err(arg, "type_error", f"date() takes text, got {kind}")

        def run(env: Env) -> Any:
            env.ctx.step()
            return coerce(inner(env), "date", "date()")

        return "date", run

    def _b_days_between(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._simple(
            node,
            scope,
            "days_between",
            ["date", "date"],
            "number",
            lambda a, b: None if a is None or b is None else (b - a).days,
        )

    def _b_add_days(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._simple(
            node,
            scope,
            "add_days",
            ["date", "number"],
            "date",
            lambda d, n: None if d is None or n is None else d + _days(n),
        )

    def _b_len(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._simple(
            node, scope, "len", ["text"], "number", lambda s: None if s is None else len(s)
        )

    def _b_lower(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._simple(
            node, scope, "lower", ["text"], "text", lambda s: None if s is None else s.lower()
        )

    def _b_upper(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._simple(
            node, scope, "upper", ["text"], "text", lambda s: None if s is None else s.upper()
        )

    def _b_contains(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._simple(
            node,
            scope,
            "contains",
            ["text", "text"],
            "bool",
            lambda s, p: None if s is None or p is None else p in s,
        )

    def _b_abs(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._simple(
            node, scope, "abs", ["number"], "number", lambda x: None if x is None else abs(x)
        )

    def _b_round(self, node: Call, scope: Scope) -> tuple[str, Run]:
        def rnd(x: Any, digits: Any = 0) -> Any:
            if x is None:
                return None
            d = int(digits or 0)
            if not -10 <= d <= 10:
                raise EvalError("bad_value", "round() digits must be between -10 and 10")
            return _number(round(x, d) if d else round(x))

        return self._simple(node, scope, "round", ["number", "number"], "number", rnd, low=1)

    def _b_text(self, node: Call, scope: Scope) -> tuple[str, Run]:
        self._arity(node, "text", 1)
        _, inner = self.compile(node.args[0], scope)

        def run(env: Env) -> Any:
            env.ctx.step()
            v = inner(env)
            if isinstance(v, dt.date):
                return v.isoformat()
            return coerce(v, "text")

        return "text", run

    def _b_number(self, node: Call, scope: Scope) -> tuple[str, Run]:
        self._arity(node, "number", 1)
        kind, inner = self.compile(node.args[0], scope)
        if kind in ("date", "bool"):
            raise self.err(node, "type_error", f"number() takes text or a number, got {kind}")

        def run(env: Env) -> Any:
            env.ctx.step()
            return coerce(inner(env), "number", "number()")

        return "number", run

    def _b_coalesce(self, node: Call, scope: Scope) -> tuple[str, Run]:
        self._arity(node, "coalesce", 1, -1)
        args = self._args(node, scope)
        common = "null"
        for i, (kind, _) in enumerate(args):
            nxt = unify(common, kind)
            if nxt is None:
                raise self.err(node.args[i], "type_error", f"coalesce() mixes {common} and {kind}")
            common = nxt
        runs = [r for _, r in args]

        def run(env: Env) -> Any:
            env.ctx.step()
            for r in runs:
                v = r(env)
                if v is not None:
                    return v
            return None

        return common, run

    def _b_if(self, node: Call, scope: Scope) -> tuple[str, Run]:
        self._arity(node, "if", 3)
        (ck, cond), (ak, a), (bk, b) = self._args(node, scope)
        if not fits(ck, "bool"):
            raise self.err(node.args[0], "type_error", f"if() condition is {ck}, not bool")
        common = unify(ak, bk)
        if common is None:
            raise self.err(node, "type_error", f"if() branches are {ak} and {bk}")

        def run(env: Env) -> Any:
            env.ctx.step()
            return a(env) if _bool(cond(env), "if() condition") is True else b(env)

        return common, run

    def _minmax(self, node: Call, scope: Scope, name: str) -> tuple[str, Run]:
        self._arity(node, name, 2, -1)
        args = self._args(node, scope)
        common = "null"
        for i, (kind, _) in enumerate(args):
            nxt = unify(common, kind)
            if nxt is None or nxt == "bool":
                raise self.err(node.args[i], "type_error", f"{name}() cannot order {kind}")
            common = nxt
        runs = [r for _, r in args]

        def run(env: Env) -> Any:
            env.ctx.step()
            values = [v for v in (r(env) for r in runs) if v is not None]
            if not values:
                return None
            if len({type_of(v) for v in values}) > 1:
                raise EvalError("type_error", f"{name}() over mixed values")
            return min(values) if name == "min" else max(values)

        return common, run

    def _b_min(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._minmax(node, scope, "min")

    def _b_max(self, node: Call, scope: Scope) -> tuple[str, Run]:
        return self._minmax(node, scope, "max")


def _walk(env: Env, objs: list[Obj], links: list[LinkInfo]) -> list[Obj]:
    loader = env.ctx.loader
    for link in links:
        if loader is None:
            raise EvalError("no_loader", "links cannot be followed here")
        nxt: list[Obj] = []
        for o in objs:
            env.ctx.step()
            nxt.extend(loader.follow(o, link))
        objs = nxt
    return objs


BUILTINS: tuple[str, ...] = tuple(
    [*sorted(n[3:] for n in dir(_Compiler) if n.startswith("_b_")), "count", "exists", "sum"]
)


def compile_expr(text: Any, scope: Scope) -> Compiled:
    """*text* parsed and checked in *scope*; :class:`ExprError` when it does not."""
    if isinstance(text, bool):
        text = "true" if text else "false"
    elif isinstance(text, (int, float)):
        text = str(text)
    node = parse(text)
    compiler = _Compiler(scope)
    kind, run = compiler.compile(node, scope)
    return Compiled(kind, run, compiler.functions, compiler.links, text)


def to_json(value: Any) -> Any:
    """An evaluated value as JSON (dates ISO text)."""
    if isinstance(value, dt.date):
        return value.isoformat()
    return value
