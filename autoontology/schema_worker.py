# SPDX-License-Identifier: MIT
"""JSON Schema validation worker: ``python -m autoontology.schema_worker``.

The service validates model answers against caller schemas here:
``pattern`` regexes can backtrack for minutes and deep schemas can exhaust recursion, and
neither can be interrupted inside the service. Protocol (:mod:`autoontology.procworker`):
request ``{"schema", "value"}`` -> ``{"event": "result", "problem": str | null}`` or
``{"event": "error", "code", "message"}``. References are never retrieved.
"""

from __future__ import annotations

from typing import Any


def _refuse(uri: str):
    import referencing.exceptions

    raise referencing.exceptions.NoSuchResource(ref=uri)


def problem(value: Any, schema: dict[str, Any]) -> str | None:
    import jsonschema
    import referencing

    cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
    validator = cls(schema, registry=referencing.Registry(retrieve=_refuse))
    errors = sorted(validator.iter_errors(value), key=lambda e: list(e.path))
    if not errors:
        return None
    first = errors[0]
    where = "/".join(str(p) for p in first.absolute_path) or "(root)"
    more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
    return f"at {where}: {first.message}{more}"


def main() -> int:
    import jsonschema
    import referencing.exceptions

    from autoontology.procworker import serve

    def handle(req: dict[str, Any], emit) -> None:
        try:
            emit({"event": "result", "problem": problem(req["value"], req["schema"])})
        except RecursionError:
            emit(
                {
                    "event": "error",
                    "code": "schema_too_complex",
                    "message": "validating against the schema recursed too deeply",
                }
            )
        except (
            referencing.exceptions.Unresolvable,
            jsonschema.exceptions.RefResolutionError,
        ) as exc:
            emit(
                {
                    "event": "error",
                    "code": "invalid_schema",
                    "message": f"the JSON schema has an unresolvable reference: {exc}"[:400],
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported, the worker survives
            emit(
                {
                    "event": "error",
                    "code": "invalid_schema",
                    "message": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            )

    return serve(handle)


if __name__ == "__main__":
    raise SystemExit(main())
