"""The user-facing DSL: ``Entity``, ``Relation[...]``, ``relation()``, ``one_of()``,
and the ``Lore`` registry.

Pydantic models *are* the lore carrier: users decorate models they already
have. Axioms live where their subject lives — per-field options inline via
``relation()``/``one_of()``, class-level disjointness via the
``lore_disjoint_with`` class attribute, cross-cutting predicates via
``@lore.rule``. Python inheritance is the subclass hierarchy (read from
``__mro__`` at compile/grounding time).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Callable, ClassVar, Literal, Sequence

import pydantic

if TYPE_CHECKING:
    from .compile import Guard

Severity = Literal["reject", "flag"]


class LoreError(Exception):
    """Raised for lore definition/compile errors and session misuse.

    Modeled on Pydantic's philosophy: a broken lore fails loudly at
    compile time, never silently at check time.
    """


class Entity(pydantic.BaseModel):
    """Base class for all lore entities.

    Every entity has an ``id``; relation fields reference their target **by id
    string**, never by nested object or free-text name (unresolved ids surface
    as existence violations — that is a feature, not an extraction failure).

    Declare disjointness by assigning ``lore_disjoint_with = [OtherClass]`` in
    a subclass body (declared ``ClassVar`` here so Pydantic never mistakes the
    bare assignment for a field).
    """

    lore_disjoint_with: ClassVar[Sequence["type[Entity] | str"]] = ()

    id: str


@dataclass(frozen=True)
class _RelTarget:
    """Annotation marker carrying a relation's target class (or forward-ref name)."""

    target: type[Entity] | str


class Relation:
    """Typing-only marker: ``Relation[Order]`` (or ``Relation["Order"]`` as a
    forward reference) annotates a field as a relation whose value is the
    target entity's id string.
    """

    def __class_getitem__(cls, target: type[Entity] | str) -> Any:
        # Annotated[str, ...] keeps pydantic's runtime type as plain str while
        # preserving the target in FieldInfo.metadata for compile-time introspection.
        return Annotated[str, _RelTarget(target)]


def relation(*, max_per_target: int | None = None, severity: Severity = "reject") -> Any:
    """Field options for a ``Relation[...]`` field, used as its default value::

        refunds: Relation[Order] = relation(max_per_target=1)

    ``max_per_target=1`` means "a given target may be pointed to by at most one
    subject via this relation" — OWL calls this *inverse-functional*. (Plain
    OWL-functional, "at most one object per subject", holds for every relation
    field: values are scalar ids, and the ``single_value`` check rejects
    re-asserting a committed subject with a different target.) The explicit
    kwarg avoids the jargon trap.
    """
    return pydantic.Field(
        json_schema_extra={
            "lore": {"kind": "relation", "max_per_target": max_per_target, "severity": severity}
        }
    )


def one_of(*allowed: str, severity: Severity = "reject") -> Any:
    """Enum-style constraint on a scalar field, used as its default value::

        status: str = one_of("paid", "shipped", "refunded")

    Deliberately *not* a ``Literal`` type: a bad value must survive Pydantic
    construction so the lore layer can reject it with a repair prompt.
    """
    return pydantic.Field(
        json_schema_extra={"lore": {"kind": "one_of", "one_of": list(allowed), "severity": severity}}
    )


@dataclass(frozen=True)
class _RawRule:
    """A rule as registered by ``@lore.rule``; the target is resolved at compile."""

    name: str
    fn: Callable[..., Any]
    message: str
    severity: Severity
    target: type[Entity] | str  # first-parameter annotation, possibly a forward-ref string


class Lore:
    """Registry of entity classes and rules. ``compile()`` self-checks the
    lore and returns a :class:`~lore.compile.Guard`.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._classes: dict[str, type[Entity]] = {}
        self._rules: list[_RawRule] = []

    def entity(self, cls: type[Entity]) -> type[Entity]:
        """Class decorator registering an ``Entity`` subclass with this lore.

        Disjointness is declared on the class as
        ``lore_disjoint_with = [OtherClass, ...]`` (classes or name strings).
        """
        if not (isinstance(cls, type) and issubclass(cls, Entity)):
            raise LoreError(
                f"@{self.name}.entity expects a subclass of lore.Entity, got {cls!r}"
            )
        existing = self._classes.get(cls.__name__)
        if existing is not None and existing is not cls:
            raise LoreError(
                f"a different class named {cls.__name__!r} is already registered "
                f"with lore {self.name!r}; class names must be unique"
            )
        self._classes[cls.__name__] = cls
        return cls

    def rule(self, *, message: str, severity: Severity = "reject"):
        """Decorator registering an arbitrary-Python rule — the escape hatch.

        The rule function takes ``(obj, graph)``; the type annotation of the
        first parameter says which entity class the rule targets (subclass
        instances fire it too). ``graph`` offers ``get(node_id)`` and
        ``incoming(node_id, predicate)`` over the session graph. Return truthy
        for "satisfied". ``message`` is a format template rendered with the
        instance as ``{obj}``, e.g. ``"Refund {obj.id} exceeds the order total."``
        """

        def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
            params = list(inspect.signature(fn).parameters.values())
            if len(params) != 2:
                raise LoreError(
                    f"rule {fn.__name__!r} must take exactly (obj, graph), "
                    f"got {len(params)} parameter(s)"
                )
            target = params[0].annotation
            if target is inspect.Parameter.empty:
                raise LoreError(
                    f"rule {fn.__name__!r}: the first parameter needs a type annotation "
                    "naming the entity class the rule targets"
                )
            self._rules.append(
                _RawRule(name=fn.__name__, fn=fn, message=message, severity=severity, target=target)
            )
            return fn

        return decorate

    def compile(self) -> "Guard":
        """Self-check the lore and return a compiled :class:`Guard`."""
        from .compile import compile_lore

        return compile_lore(self)
