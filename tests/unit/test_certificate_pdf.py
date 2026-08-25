"""Certificate of Analysis PDF rendering — pure, no session, no clock.

The one property that matters: rendering the same content twice produces
byte-identical output, which is what makes ``pdf_sha256`` a real content hash
rather than a label nobody checks.
"""

from __future__ import annotations

from datetime import UTC, datetime

from msa_lims.certificates.pdf import CertificateContent, CertifiedSample, render_pdf


def a_content(**overrides: object) -> CertificateContent:
    defaults: dict[str, object] = {
        "certificate_number": "COA-2026-0001",
        "client_name": "MSA Test Mining Co",
        "issued_at": datetime(2026, 8, 24, 14, 0, tzinfo=UTC),
        "issued_by_name": "Priya Manager",
        "samples": (
            CertifiedSample(
                sample_id="MSA-24-SO-00417",
                method="fire_assay_gravimetric",
                au_display="5.000 g/t",
            ),
        ),
    }
    defaults.update(overrides)
    return CertificateContent(**defaults)  # type: ignore[arg-type]


class TestByteDeterminism:
    def test_rendering_the_same_content_twice_is_byte_identical(self) -> None:
        content = a_content()
        assert render_pdf(content) == render_pdf(content)

    def test_holds_with_multiple_samples_and_a_censored_value(self) -> None:
        content = a_content(
            samples=(
                CertifiedSample("MSA-24-SO-00417", "fire_assay_gravimetric", "5.000 g/t"),
                CertifiedSample("MSA-24-SO-00418", "fire_assay_gravimetric", "<0.01 g/t"),
            )
        )
        assert render_pdf(content) == render_pdf(content)

    def test_holds_for_a_superseding_certificate(self) -> None:
        content = a_content(
            supersedes_number="COA-2026-0001",
            superseded_reason="corrected bead weight",
        )
        assert render_pdf(content) == render_pdf(content)

    def test_different_content_produces_different_bytes(self) -> None:
        """The other half of the determinism claim: it isn't determinism by
        accident of always returning the same bytes regardless of input."""
        first = render_pdf(a_content(certificate_number="COA-2026-0001"))
        second = render_pdf(a_content(certificate_number="COA-2026-0002"))
        assert first != second


class TestContent:
    def test_the_pdf_is_well_formed(self) -> None:
        pdf = render_pdf(a_content())
        assert pdf.startswith(b"%PDF-")
        assert pdf.rstrip().endswith(b"%%EOF")

    def test_many_samples_do_not_raise(self) -> None:
        """Exercises the page-break branch in render_pdf."""
        samples = tuple(
            CertifiedSample(f"MSA-24-SO-{i:05d}", "fire_assay_gravimetric", "1.000 g/t")
            for i in range(80)
        )
        pdf = render_pdf(a_content(samples=samples))
        assert pdf.startswith(b"%PDF-")
