"""The user-facing DSL: ``Entity``, ``Relation[...]``, ``relation()``, ``one_of()``,
and the ``Ontology`` registry.

Pydantic models *are* the ontology carrier: users decorate models they already
have. Axioms live where their subject lives — per-field options inline via
``relation()``/``one_of()``, class-level disjointness via the
``ontic_disjoint_with`` class attribute, cross-cutting predicates via
``@ont.rule``. Python inheritance is the subclass hierarchy (read from
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


class OntologyError(Exception):
    """Raised for ontology definition/compile errors and session misuse.

    Modeled on Pydantic's philosophy: a broken ontology fails loudly at
    compile time, never silently at check time.
    """


class Entity(pydantic.BaseModel):
    """Base class for all ontology entities.

    Every entity has an ``id``; relation fields reference their target **by id
    string**, never by nested object or free-text name (unresolved ids surface
    as existence violations — that is a feature, not an extraction failure).

    Declare disjointness by assigning ``ontic_disjoint_with = [OtherClass]`` in
    a subclass body (declared ``ClassVar`` here so Pydantic never mistakes the
    bare assignment for a field).
    """

    ontic_disjoint_with: ClassVar[Sequence["type[Entity] | str"]] = ()

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
    OWL-functional, "at most one object per subject", is automatic here because
    scalar relation fields hold a single id.) The explicit kwarg avoids the
    jargon trap.
    """
    return pydantic.Field(
        json_schema_extra={
            "ontic": {"kind": "relation", "max_per_target": max_per_target, "severity": severity}
        }
    )


def one_of(*allowed: str, severity: Severity = "reject") -> Any:
    """Enum-style constraint on a scalar field, used as its default value::

        status: str = one_of("paid", "shipped", "refunded")

    Deliberately *not* a ``Literal`` type: a bad value must survive Pydantic
    construction so the ontology layer can reject it with a repair prompt.
    """
    return pydantic.Field(
        json_schema_extra={"ontic": {"kind": "one_of", "one_of": list(allowed), "severity": severity}}
    )


@dataclass(frozen=True)
class _RawRule:
    """A rule as registered by ``@ont.rule``; the target is resolved at compile."""

    name: str
    fn: Callable[..., Any]
    message: str
    severity: Severity
    target: type[Entity] | str  # first-parameter annotation, possibly a forward-ref string


class Ontology:
    """Registry of entity classes and rules. ``compile()`` self-checks the
    ontology and returns a :class:`~ontic.compile.Guard`.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._classes: dict[str, type[Entity]] = {}
        self._rules: list[_RawRule] = []

    def entity(self, cls: type[Entity]) -> type[Entity]:
        """Class decorator registering an ``Entity`` subclass with this ontology.

        Disjointness is declared on the class as
        ``ontic_disjoint_with = [OtherClass, ...]`` (classes or name strings).
        """
        if not (isinstance(cls, type) and issubclass(cls, Entity)):
            raise OntologyError(
                f"@{self.name}.entity expects a subclass of ontic.Entity, got {cls!r}"
            )
        existing = self._classes.get(cls.__name__)
        if existing is not None and existing is not cls:
            raise OntologyError(
                f"a different class named {cls.__name__!r} is already registered "
                f"with ontology {self.name!r}; class names must be unique"
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
                raise OntologyError(
                    f"rule {fn.__name__!r} must take exactly (obj, graph), "
                    f"got {len(params)} parameter(s)"
                )
            target = params[0].annotation
            if target is inspect.Parameter.empty:
                raise OntologyError(
                    f"rule {fn.__name__!r}: the first parameter needs a type annotation "
                    "naming the entity class the rule targets"
                )
            self._rules.append(
                _RawRule(name=fn.__name__, fn=fn, message=message, severity=severity, target=target)
            )
            return fn

        return decorate

    def compile(self) -> "Guard":
        """Self-check the ontology and return a compiled :class:`Guard`."""
        from .compile import compile_ontology

        return compile_ontology(self)
