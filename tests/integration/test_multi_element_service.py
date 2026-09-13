"""Multi-element ICP result entry, against a real Postgres session.

Two properties that matter most: results are genuinely append-only (never
UPDATE, never DELETE — proven against the restricted role directly, matching
``test_append_only.py``'s pattern), and a supersession chain cannot branch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from msa_lims.db.models import Client, LabUser, Sample, Submission
from msa_lims.domain.enums import (
    DigestMethod,
    Element,
    Role,
    SampleStatus,
    SampleType,
)
from msa_lims.domain.lifecycle import InsufficientRoleError, TransitionNotAllowedError
from msa_lims.domain.units import Unit
from msa_lims.multi_element.service import (
    ElementResult,
    MultiElementImportInput,
    MultiElementResultError,
    MultiElementService,
    SampleNotFoundError,
    current_results,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def analyst(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-analyst-mer-1",
        email="mer1@lab.test",
        full_name="A. Nalyst",
        role=Role.ANALYST,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def supervisor(app_session: Session) -> LabUser:
    user = LabUser(
        subject="sub-supervisor-mer-1",
        email="sup-mer1@lab.test",
        full_name="S. Upervisor",
        role=Role.SUPERVISOR,
    )
    app_session.add(user)
    app_session.flush()
    return user


@pytest.fixture
def a_sample(app_session: Session) -> Sample:
    client = Client(code="MSA", name="MSA Test Mining Co")
    app_session.add(client)
    app_session.flush()

    submission = Submission(
        submission_number="SUB-2026-9101",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    app_session.add(submission)
    app_session.flush()

    sample = Sample(
        sample_id="MSA-24-SO-00501",
        submission_id=submission.id,
        sample_type=SampleType.SOIL,
        status=SampleStatus.IN_ASSAY,
    )
    app_session.add(sample)
    app_session.flush()
    return sample


@pytest.fixture
def reported_sample(app_session: Session) -> Sample:
    """A sample already at REPORTED status — second digests must be refused."""
    client = Client(code="MSA2", name="MSA Test Mining Co 2")
    app_session.add(client)
    app_session.flush()

    submission = Submission(
        submission_number="SUB-2026-9102",
        client_id=client.id,
        received_at=datetime(2026, 8, 24, tzinfo=UTC),
    )
    app_session.add(submission)
    app_session.flush()

    sample = Sample(
        sample_id="MSA-24-SO-00502",
        submission_id=submission.id,
        sample_type=SampleType.SOIL,
        status=SampleStatus.REPORTED,
    )
    app_session.add(sample)
    app_session.flush()
    return sample


def import_input(**overrides: object) -> MultiElementImportInput:
    defaults: dict[str, object] = {
        "sample_id": 1,
        "digest_method": DigestMethod.AQUA_REGIA,
        "method_notes": None,
        "analysed_at": datetime(2026, 8, 24, 9, 0, tzinfo=UTC),
        "results": [
            ElementResult(
                element=Element.CU,
                grade_value=Decimal("120.5"),
                grade_unit=Unit.PPM,
                detection_limit=Decimal("0.5"),
            ),
        ],
    }
    defaults.update(overrides)
    return MultiElementImportInput(**defaults)  # type: ignore[arg-type]


class TestImportingResults:
    def test_a_valid_import_writes_rows(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = MultiElementService(app_session)
        rows = service.import_results(
            import_input(sample_id=a_sample.id),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert len(rows) == 1
        assert rows[0].element == Element.CU
        assert rows[0].grade_value == Decimal("120.5")
        assert rows[0].grade_unit == "ppm"
        assert rows[0].grade_censored is False

    def test_importing_moves_the_sample_to_assayed(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = MultiElementService(app_session)
        service.import_results(
            import_input(sample_id=a_sample.id),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert a_sample.status is SampleStatus.ASSAYED

    def test_a_second_digest_does_not_revert_a_reported_sample(
        self, app_session: Session, analyst: LabUser, reported_sample: Sample
    ) -> None:
        """F2 regression: a four-acid digest arriving after the aqua-regia
        certificate must be refused, not walk REPORTED back to ASSAYED."""
        service = MultiElementService(app_session)
        with pytest.raises(TransitionNotAllowedError):
            service.import_results(
                import_input(
                    sample_id=reported_sample.id,
                    digest_method=DigestMethod.FOUR_ACID,
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_received_sample_is_refused(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        client = Client(code="RCV", name="Receive Client")
        app_session.add(client)
        app_session.flush()
        submission = Submission(
            submission_number="SUB-2026-9103",
            client_id=client.id,
            received_at=datetime(2026, 8, 24, tzinfo=UTC),
        )
        app_session.add(submission)
        app_session.flush()
        sample = Sample(
            sample_id="MSA-24-SO-00503",
            submission_id=submission.id,
            sample_type=SampleType.SOIL,
            status=SampleStatus.RECEIVED,
        )
        app_session.add(sample)
        app_session.flush()

        service = MultiElementService(app_session)
        with pytest.raises(TransitionNotAllowedError):
            service.import_results(
                import_input(sample_id=sample.id),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_rejected_sample_is_refused(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        client = Client(code="REJ", name="Reject Client")
        app_session.add(client)
        app_session.flush()
        submission = Submission(
            submission_number="SUB-2026-9104",
            client_id=client.id,
            received_at=datetime(2026, 8, 24, tzinfo=UTC),
        )
        app_session.add(submission)
        app_session.flush()
        sample = Sample(
            sample_id="MSA-24-SO-00504",
            submission_id=submission.id,
            sample_type=SampleType.SOIL,
            status=SampleStatus.REJECTED,
        )
        app_session.add(sample)
        app_session.flush()

        service = MultiElementService(app_session)
        with pytest.raises(TransitionNotAllowedError):
            service.import_results(
                import_input(sample_id=sample.id),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_an_analyst_may_not_import(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = MultiElementService(app_session)
        with pytest.raises(InsufficientRoleError):
            service.import_results(
                import_input(sample_id=a_sample.id),
                analyst=analyst,
                actor_role=Role.PREP_TECH,
            )

    def test_a_reading_below_its_detection_limit_is_stored_censored(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = MultiElementService(app_session)
        rows = service.import_results(
            import_input(
                sample_id=a_sample.id,
                results=[
                    ElementResult(
                        element=Element.AS,
                        grade_value=Decimal("0.002"),
                        grade_unit=Unit.PPM,
                        detection_limit=Decimal("0.010"),
                    ),
                ],
            ),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert len(rows) == 1
        assert rows[0].grade_censored is True
        assert rows[0].grade_value == Decimal("0.010")  # stored as the limit

    def test_a_concentration_unit_is_refused_as_a_grade_unit(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = MultiElementService(app_session)
        with pytest.raises(MultiElementResultError, match="mass fraction"):
            service.import_results(
                import_input(
                    sample_id=a_sample.id,
                    results=[
                        ElementResult(
                            element=Element.CU,
                            grade_value=Decimal("0.7125"),
                            grade_unit=Unit.MG_PER_L,
                        ),
                    ],
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )

    def test_a_duplicate_element_refuses_the_whole_import(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = MultiElementService(app_session)
        with pytest.raises(MultiElementResultError, match="duplicate"):
            service.import_results(
                import_input(
                    sample_id=a_sample.id,
                    results=[
                        ElementResult(
                            element=Element.CU,
                            grade_value=Decimal("100"),
                            grade_unit=Unit.PPM,
                        ),
                        ElementResult(
                            element=Element.CU,
                            grade_value=Decimal("200"),
                            grade_unit=Unit.PPM,
                        ),
                    ],
                ),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )
        app_session.flush()
        # Prove zero rows were written — all-or-nothing.
        rows = current_results(app_session, a_sample.id)
        assert rows == []

    def test_an_unknown_sample_is_refused(
        self, app_session: Session, analyst: LabUser
    ) -> None:
        service = MultiElementService(app_session)
        with pytest.raises(SampleNotFoundError):
            service.import_results(
                import_input(sample_id=999_999),
                analyst=analyst,
                actor_role=Role.ANALYST,
            )


class TestSupersedingAResult:
    def test_a_corrected_reading_supersedes_the_old_one(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """F1 regression: the supersession chain must execute without raising."""
        service = MultiElementService(app_session)
        service.import_results(
            import_input(sample_id=a_sample.id),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        result = service.supersede(
            sample_id=a_sample.id,
            element=Element.CU,
            digest_method=DigestMethod.AQUA_REGIA,
            new_value=Decimal("125.0"),
            new_unit=Unit.PPM,
            analysed_at=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
            reason="re-analysis with updated calibration",
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert result.supersedes_id is not None
        assert result.grade_value == Decimal("125.0")

        # The old row is no longer the head.
        heads = current_results(app_session, a_sample.id)
        assert len(heads) == 1
        assert heads[0].id == result.id

    def test_a_chain_cannot_branch(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """Supersessions form a linear chain: each correction targets the
        current head.  A second supersession of the *same* original row is
        impossible because the original is no longer the head."""
        service = MultiElementService(app_session)
        service.import_results(
            import_input(sample_id=a_sample.id),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        # First correction targets the original head.
        first = service.supersede(
            sample_id=a_sample.id,
            element=Element.CU,
            digest_method=DigestMethod.AQUA_REGIA,
            new_value=Decimal("125.0"),
            new_unit=Unit.PPM,
            analysed_at=datetime(2026, 8, 25, 10, 0, tzinfo=UTC),
            reason="first correction",
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        # Second correction targets the first (new head), forming a chain.
        second = service.supersede(
            sample_id=a_sample.id,
            element=Element.CU,
            digest_method=DigestMethod.AQUA_REGIA,
            new_value=Decimal("130.0"),
            new_unit=Unit.PPM,
            analysed_at=datetime(2026, 8, 26, 10, 0, tzinfo=UTC),
            reason="second correction",
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        # The chain is linear: second -> first -> original.
        assert second.supersedes_id == first.id
        assert first.supersedes_id is not None

        # Only the latest head is returned.
        heads = current_results(app_session, a_sample.id)
        assert len(heads) == 1
        assert heads[0].id == second.id

    def test_current_results_returns_one_head_per_element_and_digest(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        service = MultiElementService(app_session)
        service.import_results(
            import_input(sample_id=a_sample.id),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        heads = current_results(app_session, a_sample.id)
        assert len(heads) == 1
        assert heads[0].element == Element.CU
        assert heads[0].digest_method == DigestMethod.AQUA_REGIA

    def test_the_stored_analysed_at_is_the_one_the_caller_gave(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """F3 regression: the corrected reading must carry the caller's
        timestamp, not the analyst's signup date."""
        service = MultiElementService(app_session)
        service.import_results(
            import_input(sample_id=a_sample.id),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        target_date = datetime(2026, 8, 25, 14, 30, tzinfo=UTC)
        result = service.supersede(
            sample_id=a_sample.id,
            element=Element.CU,
            digest_method=DigestMethod.AQUA_REGIA,
            new_value=Decimal("125.0"),
            new_unit=Unit.PPM,
            analysed_at=target_date,
            reason="calibration update",
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        assert result.analysed_at == target_date


class TestAppendOnly:
    def test_the_restricted_role_cannot_update_or_delete(
        self, app_session: Session, analyst: LabUser, a_sample: Sample
    ) -> None:
        """The append-only grant tier: a prep_tech cannot UPDATE or DELETE
        a multi_element_result row."""
        service = MultiElementService(app_session)
        service.import_results(
            import_input(sample_id=a_sample.id),
            analyst=analyst,
            actor_role=Role.ANALYST,
        )
        app_session.flush()

        # Grab the row id directly.
        from sqlalchemy import text

        row = app_session.execute(
            text("SELECT id FROM multi_element_result LIMIT 1")
        ).fetchone()
        assert row is not None
        row_id = row[0]

        # Attempt UPDATE — must fail.
        with pytest.raises(ProgrammingError):
            app_session.execute(
                text("UPDATE multi_element_result SET grade_value = 999 WHERE id = :id"),
                {"id": row_id},
            )
        app_session.rollback()

        # Attempt DELETE — must fail.
        with pytest.raises(ProgrammingError):
            app_session.execute(
                text("DELETE FROM multi_element_result WHERE id = :id"),
                {"id": row_id},
            )
        app_session.rollback()
