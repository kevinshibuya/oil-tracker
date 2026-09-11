"""Size class from hull dimensions, and the estimates that follow."""

import pytest

from oiltracker.vessels.classify import SizeClass, estimate_particulars, size_class


@pytest.mark.parametrize(
    ("length_m", "width_m", "expected"),
    [
        (250.0, 43.0, "aframax"),
        (333.0, 60.0, "vlcc"),
        (255.0, 46.0, "suezmax"),
        (200.0, 32.0, "panamax_lr1"),
        # Between the suezmax and VLCC length boxes: no class claims it.
        (298.0, 50.0, "unknown"),
        (90.0, 15.0, "unknown"),
        (None, None, "unknown"),
        (250.0, None, "unknown"),
        (None, 43.0, "unknown"),
    ],
)
def test_size_class(length_m: float | None, width_m: float | None, expected: SizeClass) -> None:
    assert size_class(length_m, width_m) == expected


def test_a_vlcc_estimate_lands_near_the_published_figures() -> None:
    estimate = estimate_particulars(333.0, 60.0, "vlcc")

    assert estimate is not None
    assert estimate.dwt_t == pytest.approx(294_000, rel=0.05)
    assert estimate.tpc_t_per_cm == pytest.approx(176, rel=0.05)
    assert estimate.scantling_draught_m == 21.25
    assert estimate.source == "estimated"


def test_an_unknown_class_gets_no_estimate() -> None:
    assert estimate_particulars(298.0, 50.0, "unknown") is None
    assert estimate_particulars(None, None, "unknown") is None


def test_the_class_boxes_are_disjoint() -> None:
    """No hull may fall in two boxes: the boxes are separated by length."""
    for length in range(90, 400):
        for beam in range(10, 70):
            assert size_class(float(length), float(beam)) in {
                "mr",
                "panamax_lr1",
                "aframax",
                "suezmax",
                "vlcc",
                "unknown",
            }
