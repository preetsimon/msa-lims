"""Reference data a fresh deployment needs to be usable, idempotently.

``make seed`` runs this module. It registers the smallest useful set of
reference data through the **same service layer the API uses** — not raw
INSERTs — so a seeded row carries an honest audit event naming how it arrived,
and every validation rule submission intake enforces was already applied here.

Safe to re-run: each item is looked up before it is created, and existing rows
are left exactly as they are. Seeding never amends or overwrites.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from msa_lims.clients.service import ClientInput, ClientService, ProjectInput, ProjectService
from msa_lims.db.models import Client, DrillHole, LabUser, Project
from msa_lims.db.session import session_scope
from msa_lims.domain.enums import Role
from msa_lims.drill_holes.service import DrillHoleInput, DrillHoleService

#: The actor every seeded row is attributed to. A distinct subject (not any
#: real person's) so a report can tell seeded provenance from human work, and
#: provisioning it through the same first-sight rule the request dependency
#: uses keeps ``audit_event.actor_id`` satisfied honestly.
SEED_SUBJECT = "seed@system.msa.invalid"
SEED_NAME = "MSA LIMS seed"


def _seed_user(session: Session) -> LabUser:
    user = session.scalar(select(LabUser).where(LabUser.subject == SEED_SUBJECT))
    if user is None:
        user = LabUser(
            subject=SEED_SUBJECT,
            email=SEED_SUBJECT,
            full_name=SEED_NAME,
            role=Role.LAB_MANAGER,
        )
        session.add(user)
        session.flush()
    return user


def main() -> None:
    """Register the demo client, its project, and one drill hole."""
    with session_scope() as session:
        seeder = _seed_user(session)

        client = session.scalar(select(Client).where(Client.code == "DEMO"))
        if client is None:
            client = ClientService(session).create(
                ClientInput(
                    code="DEMO",
                    name="Demo Mining Co",
                    contact_person="Demo Geologist",
                ),
                registered_by=seeder,
                actor_role=Role.LAB_MANAGER,
            )
            print(f"seeded client {client.code} ({client.name})")
        else:
            print(f"client {client.code} already exists")

        project = session.scalar(
            select(Project).where(Project.client_id == client.id, Project.name == "Demo Project")
        )
        if project is None:
            project = ProjectService(session).create(
                ProjectInput(
                    client_id=client.id,
                    name="Demo Project",
                    description="Reference data created by `make seed`.",
                    location="Red Lake, ON",
                    start_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
                ),
                registered_by=seeder,
                actor_role=Role.LAB_MANAGER,
            )
            print(f"seeded project {project.name!r} under {client.code}")
        else:
            print("project 'Demo Project' already exists")

        hole = session.scalar(
            select(DrillHole).where(
                DrillHole.project_id == project.id, DrillHole.hole_id == "DEMO-24-001"
            )
        )
        if hole is None:
            hole = DrillHoleService(session).create(
                DrillHoleInput(
                    project_id=project.id,
                    hole_id="DEMO-24-001",
                    utm_zone="17U",
                    total_depth_m=Decimal("150.00"),
                    dip_degrees=Decimal("-90"),
                    drilling_method="diamond_core",
                ),
                registered_by=seeder,
                actor_role=Role.LAB_MANAGER,
            )
            print(f"seeded drill hole {hole.hole_id}")
        else:
            print("drill hole 'DEMO-24-001' already exists")


if __name__ == "__main__":
    main()
