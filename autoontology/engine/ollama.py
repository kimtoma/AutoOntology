"""Local Ollama setup for the ``ollama`` model profile (part of ``autoontology setup``).

The profile talks to an alias model, ``openai/qwen3:8b``, built from ``qwen3:8b`` with:

* ``PARAMETER num_ctx 16384`` (Ollama's default context is 4096), and
* a chat template whose Qwen3 thinking switch is forced off. Upstream cannot ask for
  that per request (ChatNVIDIA cannot send ``reasoning_effort``; ``think`` is ignored on
  ``/v1``), and hidden thinking costs ~200 tokens per call at ~19 tok/s.

The ``openai/`` prefix makes upstream build a ``ChatOpenAI`` client, the only path that
honours ``LLM_INVOKE_TIMEOUT_S`` (``auto_ontology/utils/llm_invoke.py:228-253``).
Upstream sends the model name verbatim and Ollama resolves the alias as-is.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_BASE_MODEL = "qwen3:8b"
DEFAULT_ALIAS = "openai/qwen3:8b"
DEFAULT_NUM_CTX = 16384
#: A no-think answer to the probe below is "pong" (~2 tokens); thinking costs 150+.
MAX_PROBE_COMPLETION_TOKENS = 8
PROBE_PROMPT = "Reply with the single word pong."
PROBE_TIMEOUT_S = 180.0

# Exact fragments of `ollama show qwen3:8b --template` that switch thinking on/off.
OLD1 = """{{- if and $.IsThinkSet (eq $i $lastUserIdx) }}
   {{- if $.Think -}}
      {{- " "}}/think
   {{- else -}}
      {{- " "}}/no_think
   {{- end -}}
{{- end }}<|im_end|>"""
NEW1 = """{{- if eq $i $lastUserIdx }}{{- " "}}/no_think{{- end }}<|im_end|>"""

OLD2 = """{{ if and $.IsThinkSet (not $.Think) -}}
<think>

</think>

{{ end -}}"""
NEW2 = """<think>

</think>

"""

Runner = Callable[..., subprocess.CompletedProcess]


class TemplatePatchError(RuntimeError):
    pass


class OllamaSetupError(RuntimeError):
    pass


def patch_no_think_template(template: str) -> str:
    """Return *template* with Qwen3 thinking disabled unconditionally.

    Raises :class:`TemplatePatchError` unless each switch occurs exactly once: anything
    else means the base model's template changed and the patch must be re-derived.
    """
    for label, old in (("user-turn /think switch", OLD1), ("empty <think> block", OLD2)):
        count = template.count(old)
        if count != 1:
            raise TemplatePatchError(
                f"base model template changed: expected the {label} exactly once, found "
                f"{count}. Re-derive the patch in autoontology/engine/ollama.py from "
                "`ollama show <base> --template`."
            )
    return template.replace(OLD1, NEW1).replace(OLD2, NEW2)


def build_modelfile(base: str, template: str, *, num_ctx: int) -> str:
    """Modelfile for the no-think alias (*template* is the unpatched base template)."""
    patched = patch_no_think_template(template)
    if '"""' in patched:
        raise TemplatePatchError('base template contains """; cannot embed it in a Modelfile')
    return f'FROM {base}\nPARAMETER num_ctx {num_ctx}\nTEMPLATE """{patched}"""\n'


def root_url(endpoint: str) -> str:
    return endpoint.rstrip("/").removesuffix("/v1")


def has_model(names: Iterable[str], model: str) -> bool:
    names = set(names)
    return model in names or f"{model}:latest" in names


def list_models(endpoint: str, client: httpx.Client | None = None) -> set[str]:
    url = f"{root_url(endpoint)}/api/tags"
    resp = (client or httpx).get(url, timeout=5.0)
    resp.raise_for_status()
    return {m.get("name", "") for m in resp.json().get("models", [])}


def chat_probe(endpoint: str, model: str, *, client: httpx.Client | None = None,
               timeout: float = PROBE_TIMEOUT_S) -> tuple[str, int]:
    """One OpenAI-compatible chat call; returns (answer, usage.completion_tokens)."""
    resp = (client or httpx).post(
        f"{root_url(endpoint)}/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": PROBE_PROMPT}],
              "max_tokens": 256, "temperature": 0},
        headers={"Authorization": "Bearer ollama"},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    answer = (body["choices"][0]["message"].get("content") or "").strip()
    return answer, int(body.get("usage", {}).get("completion_tokens", -1))


STRUCTURED_PROBE_PROMPT = "Return a JSON object whose field answer is the word pong."
STRUCTURED_PROBE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def structured_probe(endpoint: str, model: str, *, no_think: bool,
                     client: httpx.Client | None = None,
                     timeout: float = PROBE_TIMEOUT_S) -> tuple[dict | None, str, int]:
    """A json_schema chat call shaped like upstream's ChatOpenAI structured output, with
    the body the service's /v1 passthrough would forward (``prepare_chat_payload``).

    Returns (parsed object or None if the content is not bare JSON, raw content,
    usage.completion_tokens).
    """
    import json

    from autoontology.web.providers.ollama import prepare_chat_payload

    body = prepare_chat_payload({
        "model": model,
        "messages": [{"role": "user", "content": STRUCTURED_PROBE_PROMPT}],
        "max_tokens": 256,
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "Ping", "schema": STRUCTURED_PROBE_SCHEMA, "strict": True}},
    }, no_think=no_think)
    resp = (client or httpx).post(f"{root_url(endpoint)}/v1/chat/completions", json=body,
                                  headers={"Authorization": "Bearer ollama"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content = (data["choices"][0]["message"].get("content") or "").strip()
    tokens = int(data.get("usage", {}).get("completion_tokens", -1))
    try:
        parsed = json.loads(content)
    except ValueError:
        parsed = None
    return (parsed if isinstance(parsed, dict) else None), content, tokens


@dataclass(frozen=True)
class SetupResult:
    alias: str
    created: bool
    pulled: tuple[str, ...]
    answer: str
    completion_tokens: int


def _run(run: Runner, argv: list[str], **kw) -> subprocess.CompletedProcess:
    try:
        proc = run(argv, text=True, capture_output=True, **kw)
    except FileNotFoundError as exc:
        raise OllamaSetupError("`ollama` not found on PATH (install it from https://ollama.com)") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
        raise OllamaSetupError(f"`{' '.join(argv)}` failed: {detail[0]}")
    return proc


def _reachable_models(endpoint: str, client: httpx.Client | None) -> set[str]:
    try:
        return list_models(endpoint, client)
    except httpx.HTTPError as exc:
        raise OllamaSetupError(
            f"Ollama not reachable at {root_url(endpoint)} ({type(exc).__name__}); "
            "start it with `ollama serve`") from exc


def pull_models(endpoint: str, models: Iterable[str], *, run: Runner = subprocess.run,
                client: httpx.Client | None = None, echo: Callable[[str], None] = print,
                names: set[str] | None = None) -> list[str]:
    """``ollama pull`` each of *models* that Ollama does not have yet; returns those pulled."""
    if names is None:
        names = _reachable_models(endpoint, client)
    pulled: list[str] = []
    for model in dict.fromkeys(models):
        if not has_model(names, model):
            echo(f"pulling {model} ...")
            _run(run, ["ollama", "pull", model])
            pulled.append(model)
    return pulled


def setup_ollama(*, endpoint: str, base: str = DEFAULT_BASE_MODEL,
                 alias: str = DEFAULT_ALIAS, embed: str = "bge-m3",
                 num_ctx: int = DEFAULT_NUM_CTX, force: bool = False,
                 run: Runner = subprocess.run, client: httpx.Client | None = None,
                 echo: Callable[[str], None] = print) -> SetupResult:
    """Pull *base*/*embed* if missing, (re)build *alias*, then verify thinking is off."""
    names = _reachable_models(endpoint, client)
    pulled = pull_models(endpoint, [base, embed], run=run, client=client, echo=echo,
                         names=names)
    created = False
    if has_model(names, alias) and not force:
        echo(f"{alias} exists (use --force to rebuild); verifying")
    else:
        template = _run(run, ["ollama", "show", base, "--template"]).stdout
        modelfile = build_modelfile(base, template, num_ctx=num_ctx)
        with tempfile.TemporaryDirectory(prefix="autoontology-ollama-") as tmp:
            path = Path(tmp) / "Modelfile"
            path.write_text(modelfile, encoding="utf-8")
            echo(f"creating {alias} from {base} (num_ctx {num_ctx}, thinking off) ...")
            _run(run, ["ollama", "create", alias, "-f", str(path)])
        created = True
    try:
        answer, tokens = chat_probe(endpoint, alias, client=client)
    except httpx.HTTPError as exc:
        raise OllamaSetupError(f"verification call to {alias} failed: {exc}") from exc
    if tokens < 0 or tokens > MAX_PROBE_COMPLETION_TOKENS:
        raise OllamaSetupError(
            f"{alias} still thinks: completion_tokens {tokens} for {PROBE_PROMPT!r} "
            f"(max {MAX_PROBE_COMPLETION_TOKENS}); answer {answer[:60]!r}. "
            "Rebuild with --force.")
    return SetupResult(alias, created, tuple(pulled), answer, tokens)
