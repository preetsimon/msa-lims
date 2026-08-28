"""Closed vocabularies shared across the system.

These are the words the lab uses, and they are enums rather than strings so a
typo is a failure at import time instead of a row that never matches a filter.
Their string values are persisted, so renaming a value is a migration.
"""

from __future__ import annotations

from enum import Enum


class SampleType(Enum):
    """What physically arrived at the lab.

    The distinction is not cosmetic. A core sample has a depth interval and
    belongs to a drill hole; a stream sediment has coordinates and belongs to
    nothing. A pulp arrives already prepared and skips the crushing stages
    entirely, which is why the prep workflow branches on this.
    """

    CORE = "core"
    RC_CHIP = "rc_chip"
    SOIL = "soil"
    STREAM_SEDIMENT = "stream_sediment"
    ROCK_CHIP = "rock_chip"
    PULP = "pulp"


class SampleStatus(Enum):
    """Where a sample is in its life at the lab.

    Progression is Received → InPrep → ReadyForAssay → InAssay → Assayed →
    Reported. Rejected is terminal and reachable from anywhere before Assayed,
    because a sample can be found unusable at any bench. The legal moves live in
    :mod:`msa_lims.domain.lifecycle`, not here — an enum that also knows the
    transitions becomes the place every rule accidentally lands.
    """

    RECEIVED = "received"
    IN_PREP = "in_prep"
    READY_FOR_ASSAY = "ready_for_assay"
    IN_ASSAY = "in_assay"
    ASSAYED = "assayed"
    REPORTED = "reported"
    REJECTED = "rejected"


class PrepStage(Enum):
    """One step of sample preparation, in the order they are performed."""

    PRIMARY_CRUSH = "primary_crush"
    SECONDARY_CRUSH = "secondary_crush"
    SPLIT = "split"
    PULVERIZE = "pulverize"
    SIEVE = "sieve"


class BatchStatus(Enum):
    """Where a fire assay batch is in the furnace cycle.

    These are physical states of a tray of crucibles, not workflow conveniences:
    once a batch is InFusion it is inside a 1000 °C furnace and nothing about it
    can be edited.
    """

    PENDING = "pending"
    CHARGING = "charging"
    IN_FUSION = "in_fusion"
    FUSED = "fused"
    IN_CUPELLATION = "in_cupellation"
    CUPELLED = "cupelled"
    COMPLETED = "completed"


class CrucibleStatus(Enum):
    """The state of one assay unit within a batch."""

    EMPTY = "empty"
    CHARGED = "charged"
    FUSED = "fused"
    CUPELLED = "cupelled"
    PARTED = "parted"
    WEIGHED = "weighed"
    REJECTED = "rejected"


class QcMaterialType(Enum):
    """What kind of control was inserted into a batch.

    The LIMS records the *insertion* — which material went into which position.
    Judging the result is QC Sentinel's job; there is deliberately no verdict
    vocabulary in this system.
    """

    CRM = "crm"
    BLANK = "blank"
    COARSE_BLANK = "coarse_blank"
    FIELD_DUPLICATE = "field_duplicate"
    PREP_DUPLICATE = "prep_duplicate"
    PULP_DUPLICATE = "pulp_duplicate"


#: The three ``QcMaterialType`` members that are **not** stock materials: a
#: duplicate does not come from a jar, it re-inserts an existing *sample* into
#: its own extra crucible. They live here so a crucible's ``insertion_type``
#: column can carry exactly this vocabulary and nothing else — a crucible can
#: hold a duplicate of a sample, never "a CRM of a sample."
class DuplicateInsertionType(Enum):
    FIELD_DUPLICATE = "field_duplicate"
    PREP_DUPLICATE = "prep_duplicate"
    PULP_DUPLICATE = "pulp_duplicate"


#: The stock materials, for gates that must distinguish "scooped from a jar"
#: from "re-inserted an existing sample."
MATERIAL_TYPES: frozenset[QcMaterialType] = frozenset(
    {QcMaterialType.CRM, QcMaterialType.BLANK, QcMaterialType.COARSE_BLANK}
)


class Element(Enum):
    """An analyte measured by multi-element ICP analysis.

    The string values are IUPAC symbols — the same labels an instrument export
    uses in its header row, and the same labels a certificate must carry. The
    set is a reasonable ICP-MS suite for gold-ore geochemistry; extending it is
    a code change, not a data migration, because the enum is the closed
    vocabulary a typo-free import depends on.
    """

    AG = "Ag"
    AL = "Al"
    AS = "As"
    AU = "Au"
    B = "B"
    BA = "Ba"
    BE = "Be"
    BI = "Bi"
    CA = "Ca"
    CD = "Cd"
    CO = "Co"
    CR = "Cr"
    CU = "Cu"
    FE = "Fe"
    GA = "Ga"
    GE = "Ge"
    HG = "Hg"
    IN = "In"
    K = "K"
    LA = "La"
    LI = "Li"
    MG = "Mg"
    MN = "Mn"
    MO = "Mo"
    NA = "Na"
    NB = "Nb"
    NI = "Ni"
    P = "P"
    PB = "Pb"
    PD = "Pd"
    PT = "Pt"
    RE = "Re"
    S = "S"
    SB = "Sb"
    SC = "Sc"
    SE = "Se"
    SN = "Sn"
    SR = "Sr"
    TA = "Ta"
    TE = "Te"
    TH = "Th"
    TI = "Ti"
    TL = "Tl"
    U = "U"
    V = "V"
    W = "W"
    Y = "Y"
    YB = "Yb"
    ZN = "Zn"
    ZR = "Zr"


class AssayMethod(Enum):
    """How the gold finish was determined.

    ``GRAVIMETRIC`` weighs the bead directly and is the referee method for high
    grade; ``AAS`` and ``ICP_MS`` dissolve the bead and read it against a
    calibration, which is faster but saturates. The re-assay rule that sends
    material above a threshold to gravimetric depends on this distinction.
    """

    FIRE_ASSAY_AAS = "fire_assay_aas"
    FIRE_ASSAY_GRAVIMETRIC = "fire_assay_gravimetric"
    FIRE_ASSAY_ICP_MS = "fire_assay_icp_ms"


class DigestMethod(Enum):
    """How a sample was taken into solution for multi-element work.

    Aqua regia is a partial digest — it will not fully liberate elements locked
    in silicate lattices — so a low Cr or Al by aqua regia is not the same
    statement as a low Cr by four-acid. The certificate must name the digest.
    """

    AQUA_REGIA = "aqua_regia"
    FOUR_ACID = "four_acid"
    PEROXIDE_FUSION = "peroxide_fusion"


class InstrumentType(Enum):
    ATOMIC_ABSORPTION = "aas"
    ICP_MS = "icp_ms"
    ICP_OES = "icp_oes"
    XRF = "xrf"
    MICROBALANCE = "microbalance"
    CRUSHER = "crusher"
    PULVERIZER = "pulverizer"
    FURNACE = "furnace"


class InstrumentStatus(Enum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    OUT_OF_SERVICE = "out_of_service"
    RETIRED = "retired"


class MatrixType(Enum):
    """The mineralogical character of a sample charge — what it is made of,
    for the purpose of choosing a flux.

    Independent of :class:`SampleType`, which classifies the physical medium
    a sample arrived as (core, soil, pulp, ...). A silicate-hosted soil and a
    silicate-hosted core take the same flux; a sulfide-rich core and an
    oxide-rich core do not, despite both being "core." Nothing in this schema
    infers a matrix from a sample type or a lithology code — a technician
    assigns it when charging a crucible, the same way they would read it off
    a geologist's log.
    """

    SILICATE = "silicate"
    SULFIDE = "sulfide"
    OXIDE = "oxide"
    CARBONATE = "carbonate"
    CARBONACEOUS = "carbonaceous"


class Role(Enum):
    """Authorisation tiers.

    Mirrors QC Sentinel's tiering, for the same reason: the person who produced
    a result must not be the only person who signs the certificate reporting it.
    ``CLIENT`` is read-only and scoped to its own submissions.
    """

    PREP_TECH = "prep_tech"
    ANALYST = "analyst"
    SUPERVISOR = "supervisor"
    LAB_MANAGER = "lab_manager"
    CLIENT = "client"


#: Roles that may enter and amend analytical results, and roles that may sign a
#: certificate of analysis. Kept next to the enum so the two tiers are read
#: together. Signing is a manager's power because it is the act that makes a
#: number the lab's public statement.
MAY_ENTER_RESULTS: frozenset[Role] = frozenset({Role.ANALYST, Role.SUPERVISOR, Role.LAB_MANAGER})
MAY_SIGN_CERTIFICATE: frozenset[Role] = frozenset({Role.LAB_MANAGER})

#: Roles that may register or amend the lab's client and project accounts.
#: Deliberately narrower than the sample-lifecycle's bench roles: a prep
#: technician or analyst works material through the lab but does not set up
#: billing relationships or drilling programs.
MAY_MANAGE_ACCOUNTS: frozenset[Role] = frozenset({Role.SUPERVISOR, Role.LAB_MANAGER})

#: Roles that may define lab process configuration — flux recipes, and
#: (later) instrument and method setup. Same two roles as
#: ``MAY_MANAGE_ACCOUNTS`` today, but a different authority: a billing
#: relationship and a furnace recipe are not the same decision, and a lab that
#: later splits "accounts manager" from "technical supervisor" should not have
#: to hunt down every call site that conflated the two under one name.
MAY_CONFIGURE_LAB: frozenset[Role] = frozenset({Role.SUPERVISOR, Role.LAB_MANAGER})
