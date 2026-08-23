"""The sample state machine, as a table of moves and refusals."""

from __future__ import annotations

import pytest

from msa_lims.domain.enums import Role, SampleStatus, SampleType
from msa_lims.domain.lifecycle import (
    InsufficientRoleError,
    ReasonRequiredError,
    TransitionNotAllowedError,
    check_transition,
    legal_targets,
)


def allow(
    source: SampleStatus,
    target: SampleStatus,
    *,
    sample_type: SampleType = SampleType.CORE,
    role: Role = Role.ANALYST,
    reason: str | None = None,
) -> None:
    check_transition(
        source=source, target=target, sample_type=sample_type, role=role, reason=reason
    )


class TestTheHappyPath:
    def test_a_core_sample_walks_from_receipt_to_reported(self) -> None:
        allow(SampleStatus.RECEIVED, SampleStatus.IN_PREP)
        allow(SampleStatus.IN_PREP, SampleStatus.READY_FOR_ASSAY)
        allow(SampleStatus.READY_FOR_ASSAY, SampleStatus.IN_ASSAY)
        allow(SampleStatus.IN_ASSAY, SampleStatus.ASSAYED)
        allow(SampleStatus.ASSAYED, SampleStatus.REPORTED, role=Role.LAB_MANAGER)


class TestThePulpShortcut:
    def test_a_pulp_may_skip_preparation(self) -> None:
        allow(
            SampleStatus.RECEIVED,
            SampleStatus.READY_FOR_ASSAY,
            sample_type=SampleType.PULP,
        )

    def test_core_may_not_skip_preparation(self) -> None:
        with pytest.raises(TransitionNotAllowedError, match="only a pulp"):
            allow(SampleStatus.RECEIVED, SampleStatus.READY_FOR_ASSAY)

    def test_the_refusal_names_the_type_that_was_offered(self) -> None:
        with pytest.raises(TransitionNotAllowedError, match="soil"):
            allow(
                SampleStatus.RECEIVED,
                SampleStatus.READY_FOR_ASSAY,
                sample_type=SampleType.SOIL,
            )


class TestAuthorisation:
    def test_only_a_manager_may_report(self) -> None:
        with pytest.raises(InsufficientRoleError, match="lab_manager"):
            allow(SampleStatus.ASSAYED, SampleStatus.REPORTED, role=Role.ANALYST)

    def test_an_analyst_may_not_send_a_sample_back_for_re_assay(self) -> None:
        with pytest.raises(InsufficientRoleError):
            allow(
                SampleStatus.ASSAYED,
                SampleStatus.READY_FOR_ASSAY,
                role=Role.ANALYST,
                reason="RPD out of tolerance",
            )

    def test_a_supervisor_may(self) -> None:
        allow(
            SampleStatus.ASSAYED,
            SampleStatus.READY_FOR_ASSAY,
            role=Role.SUPERVISOR,
            reason="RPD out of tolerance",
        )

    def test_a_client_may_move_nothing(self) -> None:
        with pytest.raises(InsufficientRoleError):
            allow(SampleStatus.RECEIVED, SampleStatus.IN_PREP, role=Role.CLIENT)


class TestReasons:
    def test_a_re_assay_without_a_reason_is_refused(self) -> None:
        with pytest.raises(ReasonRequiredError):
            allow(
                SampleStatus.ASSAYED,
                SampleStatus.READY_FOR_ASSAY,
                role=Role.SUPERVISOR,
            )

    def test_whitespace_is_not_a_reason(self) -> None:
        with pytest.raises(ReasonRequiredError):
            allow(
                SampleStatus.ASSAYED,
                SampleStatus.READY_FOR_ASSAY,
                role=Role.SUPERVISOR,
                reason="   ",
            )


class TestRejection:
    def test_a_supervisor_may_reject_received_material(self) -> None:
        allow(
            SampleStatus.RECEIVED,
            SampleStatus.REJECTED,
            role=Role.SUPERVISOR,
            reason="bag split in transit, material contaminated",
        )

    def test_a_prep_tech_may_not(self) -> None:
        with pytest.raises(InsufficientRoleError):
            allow(
                SampleStatus.RECEIVED,
                SampleStatus.REJECTED,
                role=Role.PREP_TECH,
                reason="bag split",
            )

    def test_an_assayed_sample_cannot_be_rejected(self) -> None:
        """A result exists, so the correction is an amended certificate — not a
        status change that quietly rewrites what the lab already measured."""
        with pytest.raises(TransitionNotAllowedError, match="amended certificate"):
            allow(
                SampleStatus.ASSAYED,
                SampleStatus.REJECTED,
                role=Role.LAB_MANAGER,
                reason="client changed their mind",
            )

    def test_a_reported_sample_cannot_be_rejected(self) -> None:
        with pytest.raises(TransitionNotAllowedError):
            allow(
                SampleStatus.REPORTED,
                SampleStatus.REJECTED,
                role=Role.LAB_MANAGER,
                reason="anything",
            )


class TestIllegalMoves:
    def test_a_sample_cannot_skip_the_furnace(self) -> None:
        with pytest.raises(TransitionNotAllowedError):
            allow(SampleStatus.READY_FOR_ASSAY, SampleStatus.ASSAYED)

    def test_a_sample_cannot_move_to_where_it_already_is(self) -> None:
        with pytest.raises(TransitionNotAllowedError, match="already"):
            allow(SampleStatus.IN_PREP, SampleStatus.IN_PREP)

    def test_reported_is_terminal(self) -> None:
        for target in SampleStatus:
            if target is SampleStatus.REPORTED:
                continue
            with pytest.raises(TransitionNotAllowedError):
                allow(
                    SampleStatus.REPORTED,
                    target,
                    role=Role.LAB_MANAGER,
                    reason="a reason",
                )


class TestLegalTargets:
    def test_offers_the_pulp_shortcut_only_for_pulps(self) -> None:
        assert legal_targets(SampleStatus.RECEIVED, SampleType.PULP) == frozenset(
            {SampleStatus.IN_PREP, SampleStatus.READY_FOR_ASSAY, SampleStatus.REJECTED}
        )
        assert legal_targets(SampleStatus.RECEIVED, SampleType.CORE) == frozenset(
            {SampleStatus.IN_PREP, SampleStatus.REJECTED}
        )

    def test_a_reported_sample_offers_nothing(self) -> None:
        assert legal_targets(SampleStatus.REPORTED, SampleType.CORE) == frozenset()

    def test_a_rejected_sample_offers_nothing(self) -> None:
        assert legal_targets(SampleStatus.REJECTED, SampleType.CORE) == frozenset()

    def test_every_offered_target_is_actually_reachable_by_someone(self) -> None:
        """The menu and the gate must agree: a button that always refuses is
        worse than no button."""
        for status in SampleStatus:
            for sample_type in SampleType:
                for target in legal_targets(status, sample_type):
                    reachable = False
                    for role in Role:
                        try:
                            check_transition(
                                source=status,
                                target=target,
                                sample_type=sample_type,
                                role=role,
                                reason="a stated reason",
                            )
                            reachable = True
                            break
                        except (InsufficientRoleError, ReasonRequiredError):
                            continue
                        except TransitionNotAllowedError:
                            break
                    assert reachable, f"{status.value} → {target.value} is offered but unreachable"
