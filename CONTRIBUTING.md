# Contributing

The most useful contribution right now is an adapter for a framework we don't
cover yet. This page is the whole guide.

## What an adapter is

An adapter delivers a session verdict through a framework's own retry channel.
Every adapter is the same three steps:

1. Normalize the agent's output to a list of entities (`as_entities`).
2. Validate through the session — `session.try_commit(...)` for outputs,
   `session.guarded(...)` for tool calls with side effects.
3. On rejection, hand the repair prompt to whatever the framework already
   retries on — a raised exception (Pydantic AI's `ModelRetry`), an
   error-flagged tool result (Anthropic), a `role: "tool"` message (OpenAI), …

There is no base class to implement, because the retry channel is different in
every framework. The two shipped adapters are the reference:

- [`src/lore/adapters/pydantic_ai.py`](src/lore/adapters/pydantic_ai.py) —
  output validation, rejection raised as an exception.
- [`src/lore/adapters/anthropic.py`](src/lore/adapters/anthropic.py) —
  tool guarding, rejection returned as an error tool result.

## The contract

- **A rejection leaves zero trace.** Use `try_commit` or `guarded()` — never
  `propose()` without a matching commit or rollback.
- **The repair prompt goes through the retry channel.** The whole point is
  that the model sees it and corrects itself; don't swallow it or log it.
- **Side effects run after validation, before commit.** That is exactly the
  ordering `session.guarded()` gives you — use it for anything with effects.
- **Import the framework SDK lazily**, inside the function, and raise an
  `ImportError` that names the extra: `pip install 'lore[<framework>]'`.
  If the adapter only produces plain dicts and tuples, don't import the SDK
  at all (the Anthropic adapter does this).
- **Stay under ~50 lines.** If you need more, the missing piece belongs in
  core — open an issue instead.

## Template

```python
"""Acme adapter: plug a Session into Acme's output validator."""

from __future__ import annotations

from typing import Sequence

from ..schema import Entity
from ..session import Session
from . import as_entities


def validate_output(session: Session, output: Entity | Sequence[Entity]):
    """Ok → commit and return the output; rejected → raise Acme's retry error."""
    try:
        from acme import RetryError
    except ImportError as exc:
        raise ImportError(
            "the acme adapter needs acme installed: pip install 'lore[acme]'"
        ) from exc

    verdict = session.try_commit(*as_entities(output))
    if verdict.ok:
        return output
    raise RetryError(verdict.repair_prompt())
```

Add the extra to `pyproject.toml` under `[project.optional-dependencies]`.

## Tests

Mirror `tests/test_adapter_*.py`. Four things to cover:

1. A valid output commits — its facts land in `session.facts`.
2. A rejected output surfaces the repair prompt in the retry channel.
3. A rejected output leaves the session unchanged — `session.facts` is
   identical and `session.staged_facts == ()`.
4. If the SDK is optional, start the file with `pytest.importorskip(...)`.
