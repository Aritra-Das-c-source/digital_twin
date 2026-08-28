# configure_stations.py

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def get_dark_stations(available_station_ids: set[str]) -> set[str]:
    """
    Ask the user which stations should operate as DARK stations.

    DARK station  -> sensor_coverage = "NONE"
    LIGHT station -> sensor_coverage = "NORMAL"
    """

    print("\n" + "=" * 60)
    print("DIGITAL TWIN - DARK ZONE CONFIGURATION")
    print("=" * 60)

    print("\nAvailable stations:")
    print(", ".join(sorted(available_station_ids)))

    print(
        "\nEnter DARK stations separated by commas.\n"
        "Example: S04,S05,S09\n"
        "Press Enter if there are no dark stations."
    )

    dark_input = input("\nDark stations: ").strip()

    # No dark stations selected
    if not dark_input:
        return set()

    dark_stations = {
        station.strip()
        for station in dark_input.split(",")
        if station.strip()
    }

    # Validate station IDs
    invalid_stations = dark_stations - available_station_ids

    if invalid_stations:
        raise ValueError(
            "\nInvalid DARK station(s): "
            + ", ".join(sorted(invalid_stations))
            + "\nAvailable stations are: "
            + ", ".join(sorted(available_station_ids))
        )

    return dark_stations


def configure_sensor_coverage(
    stations: pd.DataFrame,
    dark_stations: set[str],
) -> pd.DataFrame:
    """
    Add/update sensor_coverage for every station.

    DARK  -> "NONE"
    LIGHT -> NORMAL
    """

    configured = stations.copy()

    configured["station_id"] = configured["station_id"].astype(str)

    configured["sensor_coverage"] = configured["station_id"].apply(
        lambda station_id: (
            "NONE"
            if station_id in dark_stations
            else "NORMAL"
        )
    )

    return configured


def print_configuration(
    stations: pd.DataFrame,
    dark_stations: set[str],
) -> None:

    print("\n" + "=" * 60)
    print("STATION CONFIGURATION")
    print("=" * 60)

    for row in stations.itertuples(index=False):

        station_id = str(row.station_id)

        if station_id in dark_stations:
            zone = "DARK"
            coverage = "NONE"
        else:
            zone = "LIGHT"
            coverage = "NORMAL"

        print(
            f"{station_id:<10} "
            f"Zone: {zone:<7} "
            f"Sensor coverage: {coverage}"
        )

    print("=" * 60)

    print(f"\nTotal stations : {len(stations)}")
    print(f"Dark stations  : {len(dark_stations)}")
    print(f"Light stations : {len(stations) - len(dark_stations)}")

    if dark_stations:
        print(
            "Dark station IDs:",
            ", ".join(sorted(dark_stations))
        )
    else:
        print("Dark station IDs: NONE")


def load_and_configure_stations(
    stations_csv: str | Path,
) -> tuple[pd.DataFrame, set[str]]:
    """
    Main Step-1 function.

    Returns:
        configured_stations
        dark_stations
    """

    stations_csv = Path(stations_csv)

    if not stations_csv.is_file():
        raise FileNotFoundError(
            f"stations.csv not found: {stations_csv}"
        )

    stations = pd.read_csv(stations_csv)

    if "station_id" not in stations.columns:
        raise ValueError(
            "stations.csv must contain a 'station_id' column."
        )

    # Normalize station IDs
    stations["station_id"] = (
        stations["station_id"]
        .astype(str)
        .str.strip()
    )

    # Check duplicate IDs
    duplicates = stations[
        stations["station_id"].duplicated()
    ]["station_id"].tolist()

    if duplicates:
        raise ValueError(
            f"Duplicate station IDs found: {duplicates}"
        )

    available_station_ids = set(
        stations["station_id"]
    )

    # Ask user which stations are dark
    dark_stations = get_dark_stations(
        available_station_ids
    )

    # Configure sensor coverage
    configured_stations = configure_sensor_coverage(
        stations,
        dark_stations,
    )

    print_configuration(
        configured_stations,
        dark_stations,
    )

    return configured_stations, dark_stations


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Step 1 of Digital Twin pipeline: "
            "configure Light and Dark stations."
        )
    )

    parser.add_argument(
        "--stations",
        required=True,
        help="Path to the original stations.csv",
    )

    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional path to save the configured station file. "
            "Example: configured_stations.csv"
        ),
    )

    args = parser.parse_args()

    configured_stations, dark_stations = (
        load_and_configure_stations(args.stations)
    )

    # Optional output CSV
    if args.output:

        output_path = Path(args.output)

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        configured_stations.to_csv(
            output_path,
            index=False,
        )

        print(
            f"\nConfigured station file written to: "
            f"{output_path}"
        )

    print("\nStep 1 complete.")

    print(
        "\nThe factory can now start with this configuration."
    )


if __name__ == "__main__":
    main()