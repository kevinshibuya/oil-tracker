# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false, reportUnknownParameterType=false
# pyright: reportMissingTypeArgument=false
#
# As in oiltracker.ais.readers: pyarrow's compute stubs erase element types.
"""Cutting a real day of parquet down to a committable test fixture."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from oiltracker.ais.schema import TANKER_CODE_MAX, TANKER_CODE_MIN

SAMPLE_MODULI = (10, 25, 100, 0)
"""Keep one MMSI in N of the non-tankers, tightening until it fits. 0 keeps none."""


def make_fixture(parquet_path: Path, out_path: Path, max_bytes: int) -> int:
    """Write a smaller parquet: every tanker row, plus a deterministic sample of the rest.

    Returns the row count written. The sample tightens until the file fits, since
    the fixture is committed and its size is a contract rather than a preference.
    """
    table = pq.read_table(parquet_path)
    tankers = _is_tanker(table)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    for modulus in SAMPLE_MODULI:
        keep = tankers if modulus == 0 else pc.or_kleene(tankers, _every_nth_mmsi(table, modulus))
        # filter drops null mask entries, which is what an unknown vessel type means.
        kept = table.filter(keep)
        pq.write_table(kept, out_path, compression="zstd")
        if out_path.stat().st_size <= max_bytes:
            return kept.num_rows

    raise ValueError(
        f"{out_path} is {out_path.stat().st_size} bytes with tankers alone, "
        f"over the {max_bytes} limit"
    )


def _is_tanker(table: pa.Table) -> pa.Array | pa.ChunkedArray:
    """Either code in the tanker range. A null operand never matches, but a
    known code on the other side still does, so the or is Kleene.
    """
    return pc.or_kleene(
        _in_tanker_range(table.column("vessel_type")),
        _in_tanker_range(table.column("cargo")),
    )


def _in_tanker_range(column: pa.ChunkedArray) -> pa.Array | pa.ChunkedArray:
    low = pa.scalar(TANKER_CODE_MIN, type=pa.int16())
    high = pa.scalar(TANKER_CODE_MAX, type=pa.int16())
    return pc.and_(pc.greater_equal(column, low), pc.less_equal(column, high))


def _every_nth_mmsi(table: pa.Table, modulus: int) -> pa.Array | pa.ChunkedArray:
    """`mmsi % modulus == 0`. Arrow has no modulo kernel, so divide and subtract."""
    mmsi = table.column("mmsi")
    divisor = pa.scalar(modulus, type=pa.int32())
    remainder = pc.subtract(mmsi, pc.multiply(pc.divide(mmsi, divisor), divisor))
    return pc.equal(remainder, pa.scalar(0, type=pa.int32()))
