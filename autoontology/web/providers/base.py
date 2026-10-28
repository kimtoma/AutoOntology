# SPDX-License-Identifier: MIT
"""Provider protocol, the schema helpers shared by the chat providers and the machinery of
the subscription-CLI providers (``claude_cli.py``, ``codex_cli.py``)."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from autoontology.web.providers.openai_compat import ChatRequest, ChatResponse, ProviderError

logger = logging.getLogger("autoontology.providers")

#: One deadline per request: semaphore wait + first CLI call + the schema re-ask.
DEFAULT_TIMEOUT_S = 120.0
#: The schema re-ask is attempted only when at least this much of the deadline is left.
MIN_REASK_S = 60.0
#: Per-call workdirs live in the system temp dir (never in the repo), with this prefix.
WORKDIR_PREFIX = "autoontology-provider-"
#: Leftover workdirs older than this are removed at service start.
STALE_WORKDIR_S = 3600.0
DEFAULT_MAX_INFLIGHT = 2
STATUS_CACHE_S = 30.0
STATUS_TIMEOUT_S = 20.0
#: Env of a parent Claude Code / Codex session (when AutoOntology itself runs inside one).
_STRIP_ENV_RE = re.compile(
    r"^(CLAUDECODE|CLAUDE_CODE_.*|CLAUDE_PID|CLAUDE_EFFORT|"
    r"CLAUDE_PLUGIN_.*|CODEX_THREAD_ID|CODEX_SANDBOX.*)$"
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)


@dataclass
class ProviderStatus:
    name: str
    #: The endpoint exists and answers.
    available: bool
    #: Credentials accepted (always True for Ollama, which needs none; the CLI login for
    #: claude/codex).
    logged_in: bool
    detail: str = ""
    remedy: str = ""
    models: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.available and self.logged_in

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "available": self.available,
            "logged_in": self.logged_in,
            "detail": self.detail,
            "remedy": self.remedy,
            "models": list(self.models),
        }


@runtime_checkable
class ChatProvider(Protocol):
    name: str

    async def complete(self, request: ChatRequest) -> ChatResponse: ...

    def status(self) -> ProviderStatus: ...

    def models(self) -> list[str]: ...


def _home_dirs() -> list[str]:
    """The user's home directory spellings (``$HOME``, ``Path.home()``, the password
    entry and their real paths), longest first."""
    raw = [os.environ.get("HOME", ""), str(Path.home())]
    try:
        import pwd

        raw.append(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        pass
    homes: set[str] = set()
    for home in raw:
        for spelling in (home, os.path.realpath(home) if home else ""):
            spelling = spelling.rstrip("/")
            if spelling:  # never "/" (it would turn every path into "~")
                homes.add(spelling)
    return sorted(homes, key=len, reverse=True)


def scrub(text: str, limit: int = 400) -> str:
    """CLI output for an error message: account e-mails and ids masked, the home directory
    shown as ``~`` (no user name in status payloads), capped."""
    from autoontology.web.api import mask

    text = _UUID_RE.sub("<id>", _EMAIL_RE.sub("<email>", text or ""))
    for home in _home_dirs():
        # not a sibling such as /Users/name2 or /Users/name.old
        text = re.sub(re.escape(home) + r"(?![A-Za-z0-9_-]|\.[A-Za-z0-9_-])", "~", text)
    text = mask(text.strip())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def provider_timeout_s() -> float:
    raw = os.environ.get("AUTOONTOLOGY_PROVIDER_TIMEOUT_S", "").strip()
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT_S
    except ValueError:
        return DEFAULT_TIMEOUT_S


def provider_max_inflight() -> int:
    raw = os.environ.get("AUTOONTOLOGY_PROVIDER_MAX_INFLIGHT", "").strip()
    try:
        return max(1, int(raw)) if raw else DEFAULT_MAX_INFLIGHT
    except ValueError:
        return DEFAULT_MAX_INFLIGHT


def cli_env(strip_keys: tuple[str, ...] = ()) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items() if not _STRIP_ENV_RE.match(k) and k not in strip_keys
    }
    env["NO_COLOR"] = "1"
    return env


def parse_json_answer(text: str) -> Any:
    """The model's JSON answer (```json fences tolerated). Raises ValueError."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[A-Za-z0-9]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    return json.loads(t)


#: Bounds of a caller-supplied JSON schema (it is validated in the service process).
MAX_SCHEMA_BYTES = 128 * 1024
MAX_SCHEMA_DEPTH = 64
MAX_SCHEMA_NODES = 20_000
_REF_KEYS = ("$ref", "$dynamicRef", "$recursiveRef")


def _walk_schema(schema: Any) -> None:
    stack: list[tuple[Any, int]] = [(schema, 0)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if depth > MAX_SCHEMA_DEPTH:
            raise ProviderError(
                400,
                "invalid_schema",
                f"the JSON schema nests deeper than {MAX_SCHEMA_DEPTH} levels",
            )
        if nodes > MAX_SCHEMA_NODES:
            raise ProviderError(
                400, "invalid_schema", f"the JSON schema has more than {MAX_SCHEMA_NODES} nodes"
            )
        if isinstance(node, dict):
            for key in _REF_KEYS:
                ref = node.get(key)
                if ref is not None and not (isinstance(ref, str) and ref.startswith("#")):
                    raise ProviderError(
                        400,
                        "invalid_schema",
                        f"{key} {str(ref)[:120]!r} is not allowed: only local "
                        "references into the schema itself ('#...')",
                    )
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)


def check_schema(schema: Any) -> None:
    """Refuse (400 ``invalid_schema``) a caller schema that is not a valid JSON Schema, is
    too large/deep, or references anything outside itself. Called before any provider call,
    so a bad schema never costs a CLI call nor reads files/URLs in the service."""
    import jsonschema

    if not isinstance(schema, dict):
        raise ProviderError(400, "invalid_schema", "the JSON schema must be an object")
    size = len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
    if size > MAX_SCHEMA_BYTES:
        raise ProviderError(
            400, "invalid_schema", f"the JSON schema is {size} bytes (max {MAX_SCHEMA_BYTES})"
        )
    _walk_schema(schema)
    _refuse_cycles(schema)
    cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
    try:
        cls.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ProviderError(
            400, "invalid_schema", f"not a valid JSON schema: {exc.message[:300]}"
        ) from None


def schema_problem(value: Any, schema: dict[str, Any]) -> str | None:
    """First validation error of *value* against *schema* (None = valid). An invalid or
    unsafe schema raises :class:`ProviderError` (400): it never counts as "valid".

    Validation runs in a terminable worker process (:mod:`autoontology.schema_worker`) with
    a hard budget (``AUTOONTOLOGY_SCHEMA_VALIDATION_TIMEOUT_S``, 2 s): a backtracking
    ``pattern`` or runaway recursion is killed and answered 400 ``schema_too_complex``.
    Blocking: async callers run it in a thread."""
    from autoontology.procworker import WorkerDied

    check_schema(schema)
    budget = validation_timeout_s()
    try:
        worker = _validators().take()
    except (OSError, TimeoutError, WorkerDied) as exc:
        raise ProviderError(
            503,
            "provider_unavailable",
            f"cannot start the schema validator: {type(exc).__name__}",
        ) from exc
    keep = False
    try:
        worker.send({"schema": schema, "value": value})
        answer = worker.read(budget)
        keep = True
    except TimeoutError:
        raise ProviderError(
            400,
            "schema_too_complex",
            f"validating the answer against the JSON schema took longer than "
            f"{budget:g}s (e.g. a backtracking `pattern`); simplify the schema",
        ) from None
    except WorkerDied:
        raise ProviderError(
            400, "schema_too_complex", "the schema validator died on this schema"
        ) from None
    finally:
        if keep:
            _validators().give_back(worker)
        else:
            worker.kill()
    if answer.get("event") == "error":
        raise ProviderError(
            400,
            str(answer.get("code") or "invalid_schema"),
            str(answer.get("message") or "schema validation failed"),
        )
    return answer.get("problem")


DEFAULT_VALIDATION_TIMEOUT_S = 2.0
_VALIDATORS: Any = None
_VALIDATORS_LOCK = threading.Lock()


def validation_timeout_s() -> float:
    raw = os.environ.get("AUTOONTOLOGY_SCHEMA_VALIDATION_TIMEOUT_S", "").strip()
    try:
        return max(0.1, float(raw)) if raw else DEFAULT_VALIDATION_TIMEOUT_S
    except ValueError:
        return DEFAULT_VALIDATION_TIMEOUT_S


def _validators():
    global _VALIDATORS
    from autoontology.procworker import LineWorker, WorkerPool

    with _VALIDATORS_LOCK:
        if _VALIDATORS is None:
            _VALIDATORS = WorkerPool(lambda: LineWorker("autoontology.schema_worker"), keep=4)
        return _VALIDATORS


_IN_PLACE_LISTS = ("allOf", "anyOf", "oneOf")
_IN_PLACE_ONE = ("not", "if", "then", "else")


def _refuse_cycles(schema: dict[str, Any]) -> None:
    """Refuse local references that loop without consuming any part of the instance
    (``{"$ref": "#"}``, a ``$defs`` ring through ``allOf``/``anyOf``...): they recurse until
    RecursionError. A reference reached through ``properties``/``items``/... is fine (the
    instance is finite), e.g. a tree schema."""
    nodes: dict[tuple, dict] = {}
    resource_of: dict[tuple, tuple] = {}
    anchors: dict[tuple[tuple, str], tuple] = {}
    known: set[tuple] = set()
    stack: list[tuple[tuple, Any, tuple]] = [((), schema, ())]
    while stack:
        path, node, resource = stack.pop()
        known.add(path)
        if isinstance(node, dict):
            if isinstance(node.get("$id"), str) and path:
                resource = path
            nodes[path] = node
            resource_of[path] = resource
            for key in ("$anchor", "$dynamicAnchor"):
                if isinstance(node.get(key), str):
                    anchors[(resource, node[key])] = path
            for k, v in node.items():
                stack.append(((*path, k), v, resource))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append(((*path, i), v, resource))

    def resolve(path: tuple, ref: str) -> tuple | None:
        resource = resource_of[path]
        fragment = ref[1:]
        if not fragment:
            return resource
        if fragment.startswith("/"):
            from urllib.parse import unquote

            target: tuple = resource
            for part in fragment[1:].split("/"):
                part = unquote(part).replace("~1", "/").replace("~0", "~")
                if (*target, part) in known:
                    target = (*target, part)
                elif part.isdigit() and (*target, int(part)) in known:
                    target = (*target, int(part))
                else:
                    return None  # unresolvable: validation reports it
            return target if target in nodes else None
        return anchors.get((resource, fragment))

    def edges(path: tuple) -> list[tuple]:
        node = nodes[path]
        out: list[tuple] = []
        for k in _IN_PLACE_LISTS:
            if isinstance(node.get(k), list):
                out += [(*path, k, i) for i in range(len(node[k]))]
        out += [(*path, k) for k in _IN_PLACE_ONE if isinstance(node.get(k), dict)]
        if isinstance(node.get("dependentSchemas"), dict):
            out += [(*path, "dependentSchemas", k) for k in node["dependentSchemas"]]
        for k in _REF_KEYS:
            if isinstance(node.get(k), str):
                target = resolve(path, node[k])
                if target is not None:
                    out.append(target)
        return [p for p in out if p in nodes]

    state: dict[tuple, int] = {}  # 1 = on the DFS stack, 2 = done
    for start in nodes:
        if state.get(start):
            continue
        work = [(start, iter(edges(start)))]
        state[start] = 1
        while work:
            path, it = work[-1]
            nxt = next(it, None)
            if nxt is None:
                state[path] = 2
                work.pop()
            elif state.get(nxt) == 1:
                raise ProviderError(
                    400,
                    "invalid_schema",
                    "the JSON schema has a cyclic reference that consumes no "
                    f"part of the answer (at #/{'/'.join(map(str, nxt))})",
                )
            elif not state.get(nxt):
                state[nxt] = 1
                work.append((nxt, iter(edges(nxt))))


@dataclass
class CLIResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float


class CLIProvider:
    """Common parts: binary lookup, temp workdir, timeout, concurrency, schema re-ask."""

    name = "cli"
    bin_env = ""
    default_bin = ""
    login_remedy = ""
    #: Env keys removed from the child (API keys that would bypass the login).
    strip_keys: tuple[str, ...] = ()
    #: The CLI limits output tokens itself (Claude: CLAUDE_CODE_MAX_OUTPUT_TOKENS); without
    #: such a limit (Codex has none) the answer is cut to max_tokens here.
    native_max_tokens = False

    def __init__(self, home: Path, model_list: list[str]) -> None:
        self.home = Path(home)
        self._models = list(dict.fromkeys(model_list))
        self._sems: dict[int, asyncio.Semaphore] = {}
        self._sem_lock = threading.Lock()
        self._status: tuple[float, ProviderStatus] | None = None
        self.last_test: dict[str, Any] | None = None

    # --- plumbing ----------------------------------------------------------------------

    def binary(self) -> str | None:
        configured = os.environ.get(self.bin_env, "").strip()
        if configured:
            return configured if Path(configured).is_file() else shutil.which(configured)
        return shutil.which(self.default_bin)

    def require_binary(self) -> str:
        path = self.binary()
        if not path:
            raise ProviderError(
                503,
                "cli_not_found",
                f"{self.default_bin} CLI not found on PATH (set {self.bin_env})",
                f"install the {self.default_bin} CLI and log in ({self.login_remedy})",
            )
        return path

    def models(self) -> list[str]:
        return list(self._models)

    def _semaphore(self) -> asyncio.Semaphore:
        loop = id(asyncio.get_running_loop())
        with self._sem_lock:
            sem = self._sems.get(loop)
            if sem is None:
                sem = self._sems[loop] = asyncio.Semaphore(provider_max_inflight())
            return sem

    def workdir(self) -> Path:
        """An empty per-call directory outside the repo (system temp dir)."""
        return Path(tempfile.mkdtemp(prefix=f"{WORKDIR_PREFIX}{self.name}-"))

    def child_env(self) -> dict[str, str]:
        return cli_env(self.strip_keys)

    async def run(
        self,
        argv: list[str],
        *,
        stdin: str,
        cwd: Path,
        timeout: float | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> CLIResult:
        """Run *argv* (no shell) with *stdin*; kills the whole group on timeout or when the
        caller is cancelled (client disconnect)."""
        timeout = provider_timeout_s() if timeout is None else timeout
        start = time.monotonic()
        try:
            # child_env may touch files under a lock (codex: the private CODEX_HOME); an
            # OSError there is "cannot start" (503), like one from the spawn itself
            env = {**(await asyncio.to_thread(self.child_env)), **(extra_env or {})}
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.E2BIG:
                raise ProviderError(
                    413,
                    "request_too_large",
                    f"the {self.name} request does not fit the command line (schema too large)",
                ) from exc
            raise ProviderError(
                503,
                "provider_unavailable",
                f"cannot start {self.default_bin}: {type(exc).__name__}",
                self.login_remedy,
            ) from exc
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin.encode("utf-8")), max(timeout, 0.1)
            )
        except asyncio.TimeoutError:
            _kill_group(proc.pid)
            await proc.wait()
            raise ProviderError(
                504,
                "upstream_timeout",
                f"{self.name} CLI did not answer within the request deadline "
                f"({provider_timeout_s():.0f}s, AUTOONTOLOGY_PROVIDER_TIMEOUT_S)",
            ) from None
        except asyncio.CancelledError:
            # client gone: kill the CLI and reap it before giving the slot back
            _kill_group(proc.pid)
            with contextlib.suppress(Exception):
                await asyncio.shield(proc.wait())
            raise
        return CLIResult(
            proc.returncode or 0,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
            time.monotonic() - start,
        )

    # --- the call ------------------------------------------------------------------

    async def call(
        self, request: ChatRequest, *, timeout: float
    ) -> ChatResponse:  # pragma: no cover
        raise NotImplementedError

    async def complete(self, request: ChatRequest) -> ChatResponse:
        """One request = one deadline (``AUTOONTOLOGY_PROVIDER_TIMEOUT_S``) covering the wait
        for a slot, the call and the schema re-ask; kept below upstream's
        ``LLM_INVOKE_TIMEOUT_S`` (``config.cli_llm_limits``)."""
        start = time.monotonic()
        deadline = start + provider_timeout_s()

        def left() -> float:
            return deadline - time.monotonic()

        sem = self._semaphore()
        try:
            await asyncio.wait_for(sem.acquire(), max(left(), 0.1))
        except asyncio.TimeoutError:
            raise ProviderError(
                504,
                "upstream_timeout",
                f"no free {self.name} slot within the request deadline "
                f"({provider_timeout_s():.0f}s; "
                f"AUTOONTOLOGY_PROVIDER_MAX_INFLIGHT={provider_max_inflight()})",
            ) from None
        try:
            resp = await self.call(request, timeout=left())
            self._enforce_max_tokens(resp, request)
            # an answer cut at the caller's budget is returned as is with finish_reason
            # "length" (OpenAI semantics): no schema re-ask, no normalisation
            if request.structured and resp.finish_reason != "length":
                problem = await self._problem_async(resp.content, request)
                if problem is not None:
                    if left() < MIN_REASK_S:
                        raise ProviderError(
                            422,
                            "schema_mismatch",
                            f"{self.name} answer does not match the schema "
                            f"({problem}); {max(left(), 0):.0f}s of the "
                            "deadline left, too little for a re-ask",
                        )
                    logger.info(
                        "%s answer failed the schema (%s); asking once more", self.name, problem
                    )
                    retry = request.with_extra_user(
                        "Your previous answer did not validate against the required JSON "
                        f"schema: {problem}. Previous answer:\n{resp.content[:4000]}\n\n"
                        "Answer again with only the corrected JSON."
                    )
                    second = await self.call(retry, timeout=left())
                    self._enforce_max_tokens(second, request)  # the budget holds on retries
                    second.prompt_tokens += resp.prompt_tokens
                    second.completion_tokens += resp.completion_tokens
                    if resp.cost_usd is not None or second.cost_usd is not None:
                        second.cost_usd = (resp.cost_usd or 0.0) + (second.cost_usd or 0.0)
                    resp = second
                    if resp.finish_reason != "length":
                        problem = await self._problem_async(resp.content, request)
                        if problem is not None:
                            raise ProviderError(
                                422,
                                "schema_mismatch",
                                f"{self.name} answer does not match the "
                                f"schema after one retry: {problem}",
                            )
                if resp.finish_reason != "length":
                    value = parse_json_answer(resp.content)
                    # with a budget: compact, so normalising never grows the answer past it
                    resp.content = (
                        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                        if request.max_tokens
                        else json.dumps(value, ensure_ascii=False)
                    )
                    self._enforce_max_tokens(resp, request)  # the final serialized content
        finally:
            sem.release()
        resp.duration_s = time.monotonic() - start
        return resp

    def _enforce_max_tokens(self, resp: ChatResponse, request: ChatRequest) -> None:
        """Honour ``max_tokens`` for a CLI without a native limit: an answer longer than the
        budget (tiktoken cl100k count) is cut to it with ``finish_reason`` "length". The CLI
        still generated it (the deadline bounds that); the caller never gets more."""
        if not request.max_tokens or self.native_max_tokens or resp.finish_reason:
            return
        from autoontology.unstructured.chunker import _encoding

        enc = _encoding()
        # special-token spellings (<|endoftext|>) in the answer are ordinary text here
        ids = enc.encode(resp.content, disallowed_special=())
        if len(ids) > request.max_tokens:
            resp.content = enc.decode(ids[: request.max_tokens])
            resp.finish_reason = "length"
            resp.completion_tokens = request.max_tokens

    def _problem(self, content: str, request: ChatRequest) -> str | None:
        try:
            value = parse_json_answer(content)
        except ValueError:
            return f"not valid JSON: {content[:120]!r}"
        if request.mode in ("json_object", "tool") and not isinstance(value, dict):
            return "the answer must be a JSON object"
        return schema_problem(value, request.schema or {})

    async def _problem_async(self, content: str, request: ChatRequest) -> str | None:
        # validation blocks (worker round trip, up to its budget): never on the event loop
        return await asyncio.to_thread(self._problem, content, request)

    # --- status --------------------------------------------------------------------

    def login_check(self, binary: str) -> ProviderStatus:  # pragma: no cover
        raise NotImplementedError

    def status(self, *, refresh: bool = False) -> ProviderStatus:
        now = time.monotonic()
        if not refresh and self._status and now - self._status[0] < STATUS_CACHE_S:
            return self._status[1]
        binary = self.binary()
        if not binary:
            st = ProviderStatus(
                self.name,
                False,
                False,
                f"{self.default_bin} CLI not found on PATH",
                f"install the {self.default_bin} CLI, then {self.login_remedy}",
                self.models(),
            )
        else:
            try:
                st = self.login_check(binary)
            except (OSError, subprocess.SubprocessError) as exc:
                st = ProviderStatus(
                    self.name,
                    False,
                    False,
                    f"{self.default_bin} status failed: {type(exc).__name__}",
                    self.login_remedy,
                    self.models(),
                )
        self._status = (now, st)
        return st

    def run_sync(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=STATUS_TIMEOUT_S,
            env=self.child_env(),
            stdin=subprocess.DEVNULL,
            check=False,
        )


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def remove_workdir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def sweep_workdirs(home: Path, *, older_than: float = STALE_WORKDIR_S) -> int:
    """Remove leftover per-call workdirs (a killed service): temp-dir ones older than
    *older_than* and every legacy ``<home>/run/providers/*``. Returns how many."""
    removed = 0
    now = time.time()
    for path in Path(tempfile.gettempdir()).glob(f"{WORKDIR_PREFIX}*"):
        try:
            if path.is_dir() and now - path.stat().st_mtime > older_than:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    legacy = Path(home) / "run" / "providers"
    if legacy.is_dir():
        for path in legacy.iterdir():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed += 1
    return removed
