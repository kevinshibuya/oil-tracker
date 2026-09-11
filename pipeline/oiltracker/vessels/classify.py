"""Size class from reported dimensions, and the estimates that follow from it.

The class boxes come from q13 section 2, with the boundaries made explicit. They
are disjoint by length, so no vessel matches two.
"""

from dataclasses import dataclass
from typing import Literal

SizeClass = Literal["mr", "panamax_lr1", "aframax", "suezmax", "vlcc", "unknown"]
ParticularsSource = Literal["curated", "estimated"]

# Class midpoint scantling draught in metres, from q13 section 2.
SCANTLING_MIDPOINT: dict[SizeClass, float] = {
    "mr": 12.0,
    "panamax_lr1": 13.0,
    "aframax": 14.75,
    "suezmax": 16.75,
    "vlcc": 21.25,
}

DWT_BLOCK_COEFFICIENT = 0.70
TPC_COEFFICIENT = 0.0088


@dataclass(frozen=True)
class Particulars:
    """What a hull of this size can carry, and how far it sinks per tonne."""

    dwt_t: float
    tpc_t_per_cm: float
    scantling_draught_m: float
    source: ParticularsSource


def size_class(length_m: float | None, width_m: float | None) -> SizeClass:
    """The tanker class a hull of these dimensions belongs to."""
    if length_m is None or width_m is None:
        return "unknown"

    length, beam = length_m, width_m
    if 100 <= length < 200:
        return "mr"
    if 200 <= length < 235 and beam <= 33:
        return "panamax_lr1"
    if 235 <= length < 255 and 40 <= beam <= 46:
        return "aframax"
    if 255 <= length < 295 and 46 <= beam <= 52:
        return "suezmax"
    if length >= 300 and 56 <= beam <= 62:
        return "vlcc"
    return "unknown"


def estimate_particulars(
    length_m: float | None, width_m: float | None, cls: SizeClass
) -> Particulars | None:
    """Deadweight, tonnes per centimetre and scantling draught, from the box alone.

    None for an unknown class: a hull we cannot place is one we cannot size.
    """
    if cls == "unknown" or length_m is None or width_m is None:
        return None
    scantling = SCANTLING_MIDPOINT[cls]
    return Particulars(
        dwt_t=DWT_BLOCK_COEFFICIENT * length_m * width_m * scantling,
        tpc_t_per_cm=TPC_COEFFICIENT * length_m * width_m,
        scantling_draught_m=scantling,
        source="estimated",
    )
