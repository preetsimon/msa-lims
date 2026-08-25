"""The furnace batch state machine and the furnace-position guard."""

from __future__ import annotations

import pytest

from msa_lims.domain.batch_lifecycle import (
    FurnacePositionError,
    bulk_crucible_status,
    check_batch_transition,
    check_crucible_transition,
    check_position,
)
from msa_lims.domain.enums import BatchStatus, CrucibleStatus, Role
from msa_lims.domain.lifecycle import InsufficientRoleError, TransitionNotAllowedError


class TestTheHappyPath:
    def test_a_batch_walks_from_pending_to_completed(self) -> None:
        check_batch_transition(
            source=BatchStatus.PENDING, target=BatchStatus.CHARGING, role=Role.PREP_TECH
        )
        check_batch_transition(
            source=BatchStatus.CHARGING, target=BatchStatus.IN_FUSION, role=Role.ANALYST
        )
        check_batch_transition(
            source=BatchStatus.IN_FUSION, target=BatchStatus.FUSED, role=Role.ANALYST
        )
        check_batch_transition(
            source=BatchStatus.FUSED, target=BatchStatus.IN_CUPELLATION, role=Role.ANALYST
        )
        check_batch_transition(
            source=BatchStatus.CUPELLED, target=BatchStatus.COMPLETED, role=Role.ANALYST
        )


class TestRefusals:
    def test_a_batch_cannot_skip_a_stage(self) -> None:
        with pytest.raises(TransitionNotAllowedError):
            check_batch_transition(
                source=BatchStatus.PENDING, target=BatchStatus.IN_FUSION, role=Role.ANALYST
            )

    def test_a_batch_cannot_move_backward(self) -> None:
        with pytest.raises(TransitionNotAllowedError):
            check_batch_transition(
                source=BatchStatus.FUSED, target=BatchStatus.IN_FUSION, role=Role.ANALYST
            )

    def test_a_client_role_may_not_advance_a_batch(self) -> None:
        with pytest.raises(InsufficientRoleError):
            check_batch_transition(
                source=BatchStatus.PENDING, target=BatchStatus.CHARGING, role=Role.CLIENT
            )

    def test_moving_to_the_same_status_is_refused(self) -> None:
        with pytest.raises(TransitionNotAllowedError, match="already"):
            check_batch_transition(
                source=BatchStatus.CHARGING, target=BatchStatus.CHARGING, role=Role.ANALYST
            )


class TestBulkCrucibleStatus:
    def test_fusion_completing_moves_crucibles_to_fused(self) -> None:
        assert bulk_crucible_status(BatchStatus.FUSED) is CrucibleStatus.FUSED

    def test_cupellation_completing_moves_crucibles_to_cupelled(self) -> None:
        assert bulk_crucible_status(BatchStatus.CUPELLED) is CrucibleStatus.CUPELLED

    def test_charging_does_not_bulk_update_crucibles(self) -> None:
        """Charging is a per-crucible act with its own write path."""
        assert bulk_crucible_status(BatchStatus.CHARGING) is None

    def test_completing_the_batch_does_not_bulk_update_crucibles(self) -> None:
        """Completing a batch closes it; it performs no physical act on any
        crucible. Parting and weighing are hand-driven per-crucible moves."""
        assert bulk_crucible_status(BatchStatus.COMPLETED) is None


class TestCrucibleTransitions:
    def test_parting_moves_a_cupelled_crucible_to_parted(self) -> None:
        check_crucible_transition(source=CrucibleStatus.CUPELLED, target=CrucibleStatus.PARTED)

    def test_weighing_moves_a_parted_crucible_to_weighed(self) -> None:
        check_crucible_transition(source=CrucibleStatus.PARTED, target=CrucibleStatus.WEIGHED)

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (CrucibleStatus.CHARGED, CrucibleStatus.PARTED),
            (CrucibleStatus.FUSED, CrucibleStatus.PARTED),
            (CrucibleStatus.CUPELLED, CrucibleStatus.WEIGHED),
            (CrucibleStatus.PARTED, CrucibleStatus.PARTED),
            (CrucibleStatus.EMPTY, CrucibleStatus.REJECTED),
        ],
    )
    def test_every_other_move_is_refused(
        self, source: CrucibleStatus, target: CrucibleStatus
    ) -> None:
        with pytest.raises(TransitionNotAllowedError):
            check_crucible_transition(source=source, target=target)

    def test_there_is_no_way_back_from_weighed(self) -> None:
        with pytest.raises(TransitionNotAllowedError):
            check_crucible_transition(source=CrucibleStatus.WEIGHED, target=CrucibleStatus.CUPELLED)


class TestFurnacePosition:
    def test_a_position_inside_the_tray_is_accepted(self) -> None:
        check_position(row=6, col=6, rows=6, cols=6)

    def test_row_zero_is_refused(self) -> None:
        with pytest.raises(FurnacePositionError, match="row"):
            check_position(row=0, col=1, rows=6, cols=6)

    def test_a_row_past_the_tray_is_refused(self) -> None:
        with pytest.raises(FurnacePositionError, match="row"):
            check_position(row=7, col=1, rows=6, cols=6)

    def test_a_column_past_the_tray_is_refused(self) -> None:
        with pytest.raises(FurnacePositionError, match="column"):
            check_position(row=1, col=7, rows=6, cols=6)
