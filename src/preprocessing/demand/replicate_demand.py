"""Create demand-file or demand-directory variants with replicated requests.

For every selected request CSV, rows with ``rq_pv == 1`` are retained once.
Every other row is repeated a configurable number of times.  The result is
sorted by ``rq_time`` and receives fresh contiguous integer request IDs, so
it can be used directly by FleetPy.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd


DEFAULT_DEMAND_ROOT = Path(__file__).resolve().parents[3] / "data" / "demand"


def _is_background_pv(rq_pv: pd.Series) -> pd.Series:
    """Return whether each value represents a fixed-background PV request."""
    return pd.to_numeric(rq_pv, errors="coerce").eq(1)


def replicate_request_dataframe(
    demand_df: pd.DataFrame,
    copies: int,
    id_start: int = 0,
) -> pd.DataFrame:
    """Replicate non-background rows and assign sorted, contiguous request IDs.

    ``copies`` is the total number of output events per non-background row.
    A background row (``rq_pv == 1``) always produces exactly one output row.
    """
    if copies < 1:
        raise ValueError("copies must be at least 1.")
    if "rq_time" not in demand_df.columns:
        raise ValueError("A request CSV must contain an 'rq_time' column.")

    rq_times = pd.to_numeric(demand_df["rq_time"], errors="coerce")
    if rq_times.isna().any():
        invalid_rows = demand_df.index[rq_times.isna()].tolist()
        raise ValueError(f"'rq_time' must be numeric; invalid rows: {invalid_rows[:10]}")

    ordered_demand_df = demand_df.copy()
    ordered_demand_df["_replication_source_order"] = range(len(ordered_demand_df))
    background_mask = (
        _is_background_pv(ordered_demand_df["rq_pv"])
        if "rq_pv" in ordered_demand_df.columns
        else pd.Series(False, index=demand_df.index)
    )
    background_rows = ordered_demand_df.loc[background_mask]
    non_background_rows = ordered_demand_df.loc[~background_mask]
    replicated_rows = non_background_rows.loc[non_background_rows.index.repeat(copies)]

    result = pd.concat([background_rows, replicated_rows], ignore_index=True)
    result["_replication_sort_time"] = pd.to_numeric(result["rq_time"], errors="raise")
    result.sort_values(
        ["_replication_sort_time", "_replication_source_order"],
        kind="mergesort",
        inplace=True,
    )
    result.drop(columns=["_replication_sort_time", "_replication_source_order"], inplace=True)
    result.reset_index(drop=True, inplace=True)
    result["request_id"] = range(id_start, id_start + len(result))
    return result


def _is_request_csv(csv_path: Path) -> bool:
    """Return whether a CSV is a demand request file rather than an auxiliary CSV."""
    return "rq_time" in pd.read_csv(csv_path, nrows=0).columns


def replicate_demand_file(
    source_file: Path | str,
    copies: int,
    output_file: Path | str | None = None,
    id_start: int = 0,
) -> Path:
    """Replicate one request CSV.

    The default output is a sibling named ``<source-stem>_<copies>.csv``.
    """
    source_file = Path(source_file)
    if not source_file.is_file():
        raise FileNotFoundError(f"Demand file does not exist: {source_file}")
    if source_file.suffix.lower() != ".csv" or not _is_request_csv(source_file):
        raise ValueError(f"Demand file must be a CSV containing 'rq_time': {source_file}")
    if copies < 1:
        raise ValueError("copies must be at least 1.")

    output_file = (
        Path(output_file)
        if output_file is not None
        else source_file.with_name(f"{source_file.stem}_{copies}{source_file.suffix}")
    )
    if output_file.exists():
        raise FileExistsError(f"Output file already exists: {output_file}")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    replicated = replicate_request_dataframe(pd.read_csv(source_file), copies, id_start)
    replicated.to_csv(output_file, index=False)
    return output_file


def replicate_demand_directory(
    source_dir: Path | str,
    copies: int,
    output_dir: Path | str | None = None,
    id_start: int = 0,
) -> Path:
    """Copy a demand directory and replicate each request CSV within it.

    Auxiliary files are copied unchanged.  The default output directory is a
    sibling named ``<source-name>_<copies>``.
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Demand directory does not exist: {source_dir}")
    if copies < 1:
        raise ValueError("copies must be at least 1.")

    output_dir = Path(output_dir) if output_dir is not None else source_dir.with_name(f"{source_dir.name}_{copies}")
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")

    output_dir.mkdir(parents=True)
    try:
        for source_path in source_dir.rglob("*"):
            relative_path = source_path.relative_to(source_dir)
            target_path = output_dir / relative_path
            if source_path.is_dir():
                target_path.mkdir(exist_ok=True)
            elif source_path.suffix.lower() == ".csv" and _is_request_csv(source_path):
                target_path.parent.mkdir(parents=True, exist_ok=True)
                replicated = replicate_request_dataframe(pd.read_csv(source_path), copies, id_start)
                replicated.to_csv(target_path, index=False)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, target_path)
    except Exception:
        shutil.rmtree(output_dir)
        raise
    return output_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replicate non-background request rows in a FleetPy demand CSV or directory."
    )
    parser.add_argument(
        "demand_path",
        help="Demand CSV or directory path; a missing relative path is also looked up beneath data/demand.",
    )
    parser.add_argument(
        "--copies",
        type=int,
        required=True,
        help="Total output events per row where rq_pv is not 1.",
    )
    parser.add_argument(
        "--output-path",
        "--output-dir",
        dest="output_path",
        help=(
            "Output CSV or directory. Defaults to <input-stem>_<copies>.csv for a CSV "
            "and <input-name>_<copies> for a directory."
        ),
    )
    parser.add_argument(
        "--id-start",
        type=int,
        default=0,
        help="First regenerated request_id (default: 0).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    source_path = Path(args.demand_path)
    if not source_path.exists():
        candidate_path = DEFAULT_DEMAND_ROOT / source_path
        if candidate_path.exists():
            source_path = candidate_path

    if source_path.is_file():
        output_path = replicate_demand_file(
            source_path,
            copies=args.copies,
            output_file=args.output_path,
            id_start=args.id_start,
        )
        print(f"Created replicated demand file: {output_path}")
    elif source_path.is_dir():
        output_path = replicate_demand_directory(
            source_path,
            copies=args.copies,
            output_dir=args.output_path,
            id_start=args.id_start,
        )
        print(f"Created replicated demand directory: {output_path}")
    else:
        raise FileNotFoundError(f"Demand CSV or directory does not exist: {source_path}")


if __name__ == "__main__":
    main()
