"""The sample state machine.

A sample's status is the lab's answer to "where is my material?", and it is the
one field clients phone about. Everything here is pure — it knows what a
transition requires, not how to perform one; no session, no clock — so the whole
model can be tested as a table of inputs and expected refusals.

The states::

    Received ──► InPrep ──► ReadyForAssay ──► InAssay ──► Assayed ──► Reported
        │           │             │              │
        └───────────┴─────────────┴──────────────┴────────► Rejected

Two rules are worth reading twice.

**A pulp skips preparation.** Material that arrives already pulverised goes
straight from Received to ReadyForAssay. Forcing it through the crushing states
would put a fictional prep record on a sample nobody prepared — and prep records
are evidence in a contamination investigation.

**Reported is terminal, and Rejected is not reachable from it.** Once a
certificate has gone to the client the sample's disposition is a matter of
public record. Correcting it is a new, amended certificate, not a status change
that quietly rewrites what the lab already said.
"""

from __future__ import annotations

from dataclasses import dataclass

from msa_lims.domain.enums import Role, SampleStatus, SampleType

#: Everyone who works material through the lab. The client role is absent
#: throughout: a client can see a sample's status but never set it.
BENCH_ROLES: frozenset[Role] = frozenset(
    {Role.PREP_TECH, Role.ANALYST, Role.SUPERVISOR, Role.LAB_MANAGER}
)
#: Rejecting received material is a supervisor's call. It ends the sample's life
#: at the lab and usually means telling a client their sample is unusable.
MAY_REJECT: frozenset[Role] = frozenset({Role.SUPERVISOR, Role.LAB_MANAGER})


class LifecycleError(RuntimeError):
    """Base class for refusals, so a caller can catch the whole family."""


class TransitionNotAllowedError(LifecycleError):
    """The requested move is not legal from the current status."""


class InsufficientRoleError(LifecycleError):
    """The actor's role does not permit this move."""


class ReasonRequiredError(LifecycleError):
    """A move that ends or reverses a sample's progress needs a reason."""


@dataclass(frozen=True, slots=True)
class Transition:
    source: SampleStatus
    target: SampleStatus
    allowed_roles: frozenset[Role]
    requires_reason: bool = False
    #: Sample types this move applies to. ``None`` means every type. Used for
    #: the pulp shortcut, which is legal only for material that arrived as pulp.
    limited_to_types: frozenset[SampleType] | None = None
    description: str = ""


#: The complete set of legal moves.
TRANSITIONS: tuple[Transition, ...] = (
    Transition(
        SampleStatus.RECEIVED,
        SampleStatus.IN_PREP,
        BENCH_ROLES,
        description="start preparation",
    ),
    Transition(
        SampleStatus.RECEIVED,
        SampleStatus.READY_FOR_ASSAY,
        BENCH_ROLES,
        limited_to_types=frozenset({SampleType.PULP}),
        description="accept a pulp that needs no preparation",
    ),
    Transition(
        SampleStatus.IN_PREP,
        SampleStatus.READY_FOR_ASSAY,
        BENCH_ROLES,
        description="finish preparation",
    ),
    Transition(
        SampleStatus.READY_FOR_ASSAY,
        SampleStatus.IN_ASSAY,
        BENCH_ROLES,
        description="charge into a batch",
    ),
    Transition(
        SampleStatus.IN_ASSAY,
        SampleStatus.ASSAYED,
        BENCH_ROLES,
        description="record a result",
    ),
    # Back to the bench for a re-assay. Requires a reason because it contradicts
    # a result the lab already has, and the reason is what a later investigation
    # reads to understand why the first number was not trusted.
    Transition(
        SampleStatus.ASSAYED,
        SampleStatus.READY_FOR_ASSAY,
        frozenset({Role.SUPERVISOR, Role.LAB_MANAGER}),
        requires_reason=True,
        description="return for re-assay",
    ),
    Transition(
        SampleStatus.ASSAYED,
        SampleStatus.REPORTED,
        frozenset({Role.LAB_MANAGER}),
        description="issue on a certificate",
    ),
)

#: Rejection is legal from every status before a result exists. It is defined
#: separately rather than as seven near-identical rows, because the thing that
#: varies is only the source.
_REJECTABLE_FROM: frozenset[SampleStatus] = frozenset(
    {
        SampleStatus.RECEIVED,
        SampleStatus.IN_PREP,
        SampleStatus.READY_FOR_ASSAY,
        SampleStatus.IN_ASSAY,
    }
)


def legal_targets(status: SampleStatus, sample_type: SampleType) -> frozenset[SampleStatus]:
    """Every status this sample could legally move to, ignoring who is asking.

    Used to render the buttons a page should offer. Authorisation is checked
    again in :func:`check_transition`; this is for building the menu, not for
    deciding the move.
    """
    targets = {
        transition.target
        for transition in TRANSITIONS
        if transition.source is status and _applies_to(transition, sample_type)
    }
    if status in _REJECTABLE_FROM:
        targets.add(SampleStatus.REJECTED)
    return frozenset(targets)


def check_transition(
    *,
    source: SampleStatus,
    target: SampleStatus,
    sample_type: SampleType,
    role: Role,
    reason: str | None = None,
) -> None:
    """Raise unless this actor may move this sample from ``source`` to ``target``.

    Returns nothing on success. The caller performs the move; this decides
    whether it may, which is why nothing here touches a database.
    """
    if source is target:
        raise TransitionNotAllowedError(f"the sample is already {source.value}")

    if target is SampleStatus.REJECTED:
        _check_rejection(source, role, reason)
        return

    transition = _find(source, target, sample_type)
    if transition is None:
        raise TransitionNotAllowedError(
            f"a sample cannot go from {source.value} to {target.value}"
            + _pulp_hint(source, target, sample_type)
        )
    if role not in transition.allowed_roles:
        raise InsufficientRoleError(
            f"{role.value} may not {transition.description}; "
            f"this needs {_role_list(transition.allowed_roles)}"
        )
    if transition.requires_reason and not (reason and reason.strip()):
        raise ReasonRequiredError(f"moving a sample to {target.value} requires a reason")


def _check_rejection(source: SampleStatus, role: Role, reason: str | None) -> None:
    if source not in _REJECTABLE_FROM:
        raise TransitionNotAllowedError(
            f"a sample that is {source.value} cannot be rejected; "
            "a result already exists, so this is an amended certificate, not a rejection"
        )
    if role not in MAY_REJECT:
        raise InsufficientRoleError(
            f"{role.value} may not reject a sample; this needs {_role_list(MAY_REJECT)}"
        )
    if not (reason and reason.strip()):
        raise ReasonRequiredError("rejecting a sample requires a reason")


def _find(source: SampleStatus, target: SampleStatus, sample_type: SampleType) -> Transition | None:
    for transition in TRANSITIONS:
        if (
            transition.source is source
            and transition.target is target
            and _applies_to(transition, sample_type)
        ):
            return transition
    return None


def _applies_to(transition: Transition, sample_type: SampleType) -> bool:
    return transition.limited_to_types is None or sample_type in transition.limited_to_types


def _pulp_hint(source: SampleStatus, target: SampleStatus, sample_type: SampleType) -> str:
    """Explain the one refusal whose reason is not obvious from the state names."""
    if (
        source is SampleStatus.RECEIVED
        and target is SampleStatus.READY_FOR_ASSAY
        and sample_type is not SampleType.PULP
    ):
        return f"; only a pulp may skip preparation, and this is {sample_type.value}"
    return ""


def _role_list(roles: frozenset[Role]) -> str:
    return " or ".join(sorted(role.value for role in roles))
