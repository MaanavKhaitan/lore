"""LoreAirlineEnv: the tau3 airline environment with lore guarding its
write tools. Registered as domain ``airline_lore``.

Guard scoping — the part that keeps tau2's evaluator correct:

- The guard fires ONLY for calls flowing through ``get_response`` (the path
  used by the live orchestrator AND by ``set_state``'s trajectory replay).
  Rejections raise; ``get_response`` converts them into
  ``ToolMessage(error=True)`` whose content is the repair prompt.
- The evaluator's *gold* environment applies gold actions via direct
  ``make_tool_call`` and initialization via ``run_env_function_call`` —
  neither goes through ``get_response``, so both bypass the guard.
- Trajectory replay must reproduce live guard decisions deterministically:
  ``set_state`` is split into an init phase (after which the lore session is
  rebuilt from the DB) and a replay phase (guard active). ``get_user_details``
  — the authentication trigger — is reported as mutating so the replay
  re-fires it and authentication state is rebuilt.
"""

from tau2.domains.airline.environment import (
    get_environment as get_stock_environment,
)
from tau2.domains.airline.environment import (
    get_tasks as airline_get_tasks,
)
from tau2.domains.airline.environment import (
    get_tasks_split as airline_get_tasks_split,
)
from tau2.environment.environment import Environment

from .actions import (
    ActionMapper,
    AUTH_TOOL,
    WRITE_TOOLS,
    seed_from_db,
    sync_new_certificates,
)
from ..world import guard as lore_guard

DOMAIN_NAME = "airline_lore"


class LoreRejection(Exception):
    """Raised on a guard reject; the message is the repair prompt."""


class LoreConsistencyError(RuntimeError):
    """The environment changed but lore could not record it; abort the run."""


class LoreAirlineEnv(Environment):
    def __init__(self, stock: Environment):
        super().__init__(
            domain_name=DOMAIN_NAME,
            policy=stock.policy,
            tools=stock.tools,
            user_tools=stock.user_tools,
        )
        self._guarding = False  # True only inside get_response
        self.lore_session = None
        self.lore_mapper = None
        self.lore_log = []  # [(tool, [violation dicts])] for this sim
        self._rebuild_session()

    # -- session lifecycle -----------------------------------------------

    def _db_dump(self) -> dict:
        return self.tools.db.model_dump()

    def _rebuild_session(self):
        seeds = seed_from_db(self._db_dump())
        self.lore_session = lore_guard.session(seed=seeds,
                                               validate_seed=False)
        self.lore_mapper = ActionMapper(self._db_dump)
        self.lore_log = []
        self._consistency_error = None

    def set_state(self, initialization_data, initialization_actions,
                  message_history, strict: bool = True):
        # phase 1: apply initialization only (guard idle: not get_response)
        super().set_state(initialization_data, initialization_actions, [],
                          strict=strict)
        # the lore world starts at the task's initial DB state
        self._rebuild_session()
        # phase 2: replay the trajectory with the guard active, so guarded
        # rejections reproduce byte-identically for strict comparison
        if message_history:
            super().set_state(None, None, message_history, strict=strict)

    # -- guard scoping ------------------------------------------------------

    def get_response(self, message):
        if self._consistency_error is not None:
            raise self._consistency_error
        self._guarding = True
        try:
            response = super().get_response(message)
            # tau2 catches ordinary tool exceptions and returns error messages.
            # A post-effect failure is fatal, not something the agent can retry.
            if self._consistency_error is not None:
                raise self._consistency_error
            return response
        finally:
            self._guarding = False

    def _is_mutating_tool(self, tool_name: str) -> bool:
        if tool_name == AUTH_TOOL:
            return True  # replay must re-fire authentication
        return super()._is_mutating_tool(tool_name)

    # -- the guard ----------------------------------------------------------

    def make_tool_call(self, tool_name: str, requestor="assistant", **kwargs):
        if not (self._guarding and requestor == "assistant"):
            return super().make_tool_call(tool_name, requestor=requestor,
                                          **kwargs)

        if tool_name == AUTH_TOOL:
            result = super().make_tool_call(tool_name, requestor=requestor,
                                            **kwargs)
            # successful lookup == user id obtained and verified
            verdict = self.lore_session.try_commit(
                self.lore_mapper.auth_entity(kwargs["user_id"]))
            if not verdict.ok:
                raise LoreRejection(verdict.repair_prompt())
            return result

        if tool_name not in WRITE_TOOLS:
            return super().make_tool_call(tool_name, requestor=requestor,
                                          **kwargs)

        try:
            entities = self.lore_mapper.map_tool_call(tool_name, kwargs)
        except Exception as e:
            raise LoreRejection(
                f"Cannot validate {tool_name}; no action was taken: {e}"
            ) from e

        verdict = self.lore_session.check(*entities)
        self.lore_log.append({
            "tool": tool_name,
            "ok": verdict.ok,
            "violations": [
                {"check": v.check, "severity": v.severity,
                 "message": v.message}
                for v in verdict.violations
            ],
        })
        if not verdict.ok:
            raise LoreRejection(verdict.repair_prompt())

        result = super().make_tool_call(tool_name, requestor=requestor,
                                        **kwargs)

        # The external effect has happened. Any failure to mirror it makes
        # this run unusable; do not continue with a stale policy graph.
        try:
            if tool_name == "book_reservation":
                rid = getattr(result, "reservation_id", None) or result.get(
                    "reservation_id")
                if not rid:
                    raise ValueError("booking returned no reservation_id")
                entities = self.lore_mapper.map_tool_call(
                    tool_name, kwargs, new_reservation_id=rid)
            if tool_name == "send_certificate":
                sync_new_certificates(self.lore_session, self._db_dump(),
                                      kwargs["user_id"])
            commit = self.lore_session.try_commit(*entities)
            if not commit.ok:
                raise LoreConsistencyError(commit.repair_prompt())
        except Exception as e:
            self._consistency_error = LoreConsistencyError(
                f"Lore state diverged after {tool_name}; abort this run: {e}"
            )
            raise self._consistency_error from e
        return result


def get_environment(db=None, solo_mode: bool = False) -> Environment:
    if solo_mode:
        raise ValueError("airline_lore does not support solo mode")
    return LoreAirlineEnv(get_stock_environment(db=db))


def register(registry=None):
    """Register the guarded domain + the stock airline tasks under
    ``airline_lore``. Idempotent."""
    if registry is None:
        from tau2.registry import registry
    if DOMAIN_NAME not in registry.get_info().domains:
        registry.register_domain(get_environment, DOMAIN_NAME)
        registry.register_tasks(airline_get_tasks, DOMAIN_NAME,
                                airline_get_tasks_split)
    return registry
