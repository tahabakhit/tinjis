"""Strict JSON decoding.

Tinjis refuses duplicate JSON object keys instead of the standard last-wins
behaviour. A duplicated key is the classic way an authored file can mean one
thing to a human reviewer and another to the parser, so it is always an error.

``json.loads`` also accepts the non-standard ``NaN``, ``Infinity``, and
``-Infinity`` literals by default. No Tinjis field accepts a float at all, but
refusing them at the decoder keeps a later float-valued field from silently
arriving as a non-finite value.
"""

from __future__ import annotations

import json
from collections.abc import Callable


def _object_pairs_hook(where: str, error: type) -> Callable:
    def hook(pairs):
        out: dict = {}
        for key, value in pairs:
            if key in out:
                raise error(f"{where} has a duplicate JSON key: {key!r}")
            out[key] = value
        return out

    return hook


def loads(text: str, *, where: str, error: type = ValueError):
    """Decode one JSON document, refusing duplicate keys and non-finite floats.

    ``where`` names the document in every message, and ``error`` is the
    exception type the caller wants (always a :class:`tinjis.errors.TinjisError`
    subclass in this package). A ``ValueError`` other than ``error`` is wrapped
    in ``error`` so callers see one failure type per document.
    """
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_pairs_hook(where, error),
            parse_constant=_reject_constant(error, where),
        )
    except error:
        raise
    except (ValueError, UnicodeDecodeError) as exc:
        raise error(f"invalid JSON in {where}: {exc}") from exc


def _reject_constant(error: type, where: str):
    def reject(value: str):
        raise error(f"{where} contains the non-standard JSON literal {value!r}")

    return reject


def dumps_stable(value) -> str:
    """A deterministic, key-sorted JSON encoding with no non-finite floats.

    Used only for Tinjis-owned bookkeeping (the ownership record and the intent
    journal), never for authored input. Sorting keys makes the bytes stable, so
    rewriting unchanged content produces an identical file and recovery stays
    idempotent at the byte level. ``allow_nan=False`` keeps a non-finite float
    from entering a record this package later reads strictly.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
