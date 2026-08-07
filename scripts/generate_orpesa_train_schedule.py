#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo


STATION_ID = "65304"
TIMEZONE = "Europe/Madrid"

# Нас интересуют только поезда, которые реально являются
# региональными поездами для Orpesa.
ALLOWED_SERVICES = {
    "REGIONAL",
    "REG.EXP.",
}

DEFAULT_DAYS = 3

# Для признания двух GTFS-записей одним физическим поездом
# они должны совпасть минимум в трёх stop/time точках.
MIN_SHARED_TIMED_STOPS_FOR_DUPLICATE = 3


REQUIRED_FILES = {
    "agency.txt",
    "calendar.txt",
    "calendar_dates.txt",
    "routes.txt",
    "stop_times.txt",
    "stops.txt",
    "trips.txt",
}


WEEKDAY_FIELDS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def clean(value: Optional[str]) -> str:
    return (value or "").strip()


def read_gtfs_csv(
    zf: zipfile.ZipFile,
    name: str,
) -> List[Dict[str, str]]:

    raw = zf.read(name).decode("utf-8-sig")

    reader = csv.DictReader(
        io.StringIO(raw)
    )

    rows: List[Dict[str, str]] = []

    for row in reader:

        cleaned: Dict[str, str] = {}

        for key, value in row.items():

            if key is None:
                continue

            cleaned[
                clean(key)
            ] = clean(value)

        rows.append(cleaned)

    return rows


def parse_gtfs_time(
    value: str,
) -> Tuple[int, int, int, int]:

    parts = value.split(":")

    if len(parts) != 3:
        raise ValueError(
            f"Invalid GTFS time: {value!r}"
        )

    hour, minute, second = map(
        int,
        parts,
    )

    day_offset, hour_of_day = divmod(
        hour,
        24,
    )

    seconds_of_day = (
        hour_of_day * 3600
        + minute * 60
        + second
    )

    return (
        day_offset,
        hour_of_day,
        minute,
        seconds_of_day,
    )


def normalize_gtfs_time(
    value: str,
) -> str:

    parts = value.split(":")

    if len(parts) != 3:
        return value

    hour, minute, second = map(
        int,
        parts,
    )

    return (
        f"{hour:02d}:"
        f"{minute:02d}:"
        f"{second:02d}"
    )


def hhmm(
    value: str,
) -> str:

    _, hour, minute, _ = parse_gtfs_time(
        value
    )

    return f"{hour:02d}:{minute:02d}"


def simplify_destination(
    raw_name: str,
) -> str:

    name = raw_name.strip()

    lower = name.casefold()

    if (
        "valència" in lower
        or "valencia" in lower
    ):
        return "València"

    if (
        "vinaròs" in lower
        or "vinaros" in lower
    ):
        return "Vinaròs"

    if "tortosa" in lower:
        return "Tortosa"

    if "barcelona" in lower:
        return "Barcelona"

    if (
        "castelló" in lower
        or "castellon" in lower
        or "castellón" in lower
    ):
        return "Castelló"

    # Если Renfe когда-нибудь добавит новую реальную
    # конечную станцию, мы её не теряем.
    return name


def active_service_ids(
    service_date: date,
    calendar_rows: Sequence[Dict[str, str]],
    calendar_date_rows: Sequence[Dict[str, str]],
) -> Set[str]:

    ymd = service_date.strftime(
        "%Y%m%d"
    )

    weekday_field = WEEKDAY_FIELDS[
        service_date.weekday()
    ]

    active: Set[str] = set()

    for row in calendar_rows:

        start_date = row.get(
            "start_date",
            "",
        )

        end_date = row.get(
            "end_date",
            "",
        )

        if (
            start_date
            and end_date
            and start_date <= ymd <= end_date
            and row.get(
                weekday_field
            ) == "1"
        ):
            active.add(
                row["service_id"]
            )

    # calendar_dates имеет приоритет
    # над обычным календарём.
    for row in calendar_date_rows:

        if row.get("date") != ymd:
            continue

        service_id = row.get(
            "service_id",
            "",
        )

        exception_type = row.get(
            "exception_type",
            "",
        )

        if exception_type == "1":

            active.add(
                service_id
            )

        elif exception_type == "2":

            active.discard(
                service_id
            )

    return active


@dataclass(frozen=True)
class Candidate:

    service_date: date

    actual_date: date

    seconds_of_day: int

    departure: str

    arrival: str

    direction: str

    trip_id: str

    train_number: str

    route_id: str

    service_id: str

    service: str

    origin_stop_id: str

    origin_raw: str

    destination_stop_id: str

    destination_raw: str

    stop_count: int

    orpesa_stop_sequence: int

    timed_stops: frozenset[
        Tuple[str, str, str]
    ]


def determine_direction(
    trip_stop_times: Sequence[Dict[str, str]],
    station_index: int,
    stop_by_id: Dict[str, Dict[str, str]],
) -> str:

    station = stop_by_id[
        STATION_ID
    ]

    station_lat = float(
        station["stop_lat"]
    )

    # Нормальный случай:
    # смотрим следующую станцию после Orpesa.
    if station_index + 1 < len(
        trip_stop_times
    ):

        next_stop = stop_by_id[
            trip_stop_times[
                station_index + 1
            ]["stop_id"]
        ]

        next_lat = float(
            next_stop["stop_lat"]
        )

        if next_lat > station_lat:
            return "north"

        return "south"

    # Запасной вариант, если Orpesa
    # вдруг станет конечной.
    if station_index > 0:

        previous_stop = stop_by_id[
            trip_stop_times[
                station_index - 1
            ]["stop_id"]
        ]

        previous_lat = float(
            previous_stop["stop_lat"]
        )

        if previous_lat > station_lat:
            return "south"

        return "north"

    return "unknown"


def are_same_physical_train(
    a: Candidate,
    b: Candidate,
) -> bool:

    # Разные календарные дни
    # объединять нельзя.
    if a.actual_date != b.actual_date:
        return False

    # В Orpesa время должно совпадать.
    if a.seconds_of_day != b.seconds_of_day:
        return False

    # Противоположные направления
    # никогда не являются дублями.
    #
    # Это защищает, например,
    # два реальных поезда в 08:19.
    if a.direction != b.direction:
        return False

    # Сравниваем реальные станции и времена.
    shared = len(
        a.timed_stops.intersection(
            b.timed_stops
        )
    )

    return (
        shared
        >= MIN_SHARED_TIMED_STOPS_FOR_DUPLICATE
    )


def connected_components(
    candidates: Sequence[Candidate],
) -> List[List[Candidate]]:

    remaining = set(
        range(
            len(candidates)
        )
    )

    components: List[
        List[Candidate]
    ] = []

    while remaining:

        start = remaining.pop()

        stack = [
            start
        ]

        component_indexes = {
            start
        }

        while stack:

            current = stack.pop()

            linked = []

            for other in list(
                remaining
            ):

                if are_same_physical_train(
                    candidates[current],
                    candidates[other],
                ):

                    linked.append(
                        other
                    )

            for other in linked:

                remaining.remove(
                    other
                )

                component_indexes.add(
                    other
                )

                stack.append(
                    other
                )

        components.append(
            [
                candidates[index]
                for index
                in sorted(
                    component_indexes
                )
            ]
        )

    return components


def primary_candidate(
    component: Sequence[Candidate],
) -> Candidate:

    # Если один и тот же физический поезд
    # представлен как REG.EXP. + REGIONAL,
    # основной записью делаем REG.EXP.
    #
    # Именно поэтому:
    #
    # 18096 + 38096
    #
    # превращается в поезд до Barcelona,
    # а не в короткий вариант до Vinaròs.

    return sorted(
        component,
        key=lambda candidate: (
            (
                0
                if candidate.service
                == "REG.EXP."
                else 1
            ),
            -candidate.stop_count,
            candidate.trip_id,
        ),
    )[0]


def unique_preserving_order(
    values: Iterable[str],
) -> List[str]:

    seen: Set[str] = set()

    result: List[str] = []

    for value in values:

        if (
            value
            and value not in seen
        ):

            seen.add(
                value
            )

            result.append(
                value
            )

    return result


def trip_json(
    component: Sequence[Candidate],
) -> Dict[str, object]:

    primary = primary_candidate(
        component
    )

    related = sorted(
        component,
        key=lambda candidate: (
            (
                0
                if candidate.trip_id
                == primary.trip_id
                else 1
            ),
            (
                0
                if candidate.service
                == "REG.EXP."
                else 1
            ),
            candidate.train_number,
            candidate.trip_id,
        ),
    )

    return {

        # Плановые данные для интерфейса.

        "departure":
            primary.departure,

        "arrival":
            primary.arrival,

        "destination":
            simplify_destination(
                primary.destination_raw
            ),

        # Точное оригинальное название Renfe.

        "destinationRaw":
            primary.destination_raw,

        "destinationStopId":
            primary.destination_stop_id,

        "direction":
            primary.direction,

        # Главный GTFS trip_id.

        "tripId":
            primary.trip_id,

        # Все связанные trip_id.
        #
        # Они понадобятся Android,
        # когда будем искать realtime.

        "relatedTripIds":
            unique_preserving_order(
                candidate.trip_id
                for candidate
                in related
            ),

        # Номер поезда Renfe.

        "trainNumber":
            primary.train_number,

        "relatedTrainNumbers":
            unique_preserving_order(
                candidate.train_number
                for candidate
                in related
            ),

        # Тип сервиса.

        "service":
            primary.service,

        "relatedServices":
            unique_preserving_order(
                candidate.service
                for candidate
                in related
            ),

        # route_id.

        "routeId":
            primary.route_id,

        "relatedRouteIds":
            unique_preserving_order(
                candidate.route_id
                for candidate
                in related
            ),

        # service_id.

        "serviceId":
            primary.service_id,

        # Дата старта рейса в формате GTFS-Realtime.
        #
        # Это важно:
        # один tripId может быть активен
        # больше одного календарного дня.

        "startDate":
            primary.service_date.strftime(
                "%Y%m%d"
            ),

        "relatedServiceIds":
            unique_preserving_order(
                candidate.service_id
                for candidate
                in related
            ),

        # Дополнительные технические данные.

        "originRaw":
            primary.origin_raw,

        "originStopId":
            primary.origin_stop_id,

        "orpesaStopSequence":
            primary.orpesa_stop_sequence,
    }


def build_candidates_for_service_date(
    service_date: date,
    target_dates: Set[date],
    active_services: Set[str],
    route_by_id: Dict[
        str,
        Dict[str, str],
    ],
    trip_rows: Sequence[
        Dict[str, str]
    ],
    stop_by_id: Dict[
        str,
        Dict[str, str],
    ],
    stop_times_by_trip: Dict[
        str,
        List[Dict[str, str]],
    ],
) -> List[Candidate]:

    result: List[
        Candidate
    ] = []

    for trip in trip_rows:

        if (
            trip.get(
                "service_id"
            )
            not in active_services
        ):
            continue

        route = route_by_id.get(
            trip.get(
                "route_id",
                "",
            )
        )

        if not route:
            continue

        service = route.get(
            "route_short_name",
            "",
        )

        # Ключевой фильтр:
        # любые Intercity, ALVIA и т.д.
        # полностью игнорируются.

        if service not in ALLOWED_SERVICES:
            continue

        trip_id = trip.get(
            "trip_id",
            "",
        )

        trip_stop_times = (
            stop_times_by_trip.get(
                trip_id
            )
        )

        if not trip_stop_times:
            continue

        station_index: Optional[
            int
        ] = None

        for (
            index,
            stop_time,
        ) in enumerate(
            trip_stop_times
        ):

            if (
                stop_time.get(
                    "stop_id"
                )
                == STATION_ID
            ):

                station_index = index

                break

        if station_index is None:
            continue

        station_time = (
            trip_stop_times[
                station_index
            ]
        )

        departure_raw = (
            station_time.get(
                "departure_time"
            )
            or station_time.get(
                "arrival_time"
            )
        )

        arrival_raw = (
            station_time.get(
                "arrival_time"
            )
            or station_time.get(
                "departure_time"
            )
        )

        if (
            not departure_raw
            or not arrival_raw
        ):
            continue

        (
            day_offset,
            _,
            _,
            seconds_of_day,
        ) = parse_gtfs_time(
            departure_raw
        )

        # GTFS позволяет время:
        #
        # 24:15
        # 25:10
        #
        # поэтому фактическая календарная дата
        # может отличаться от service_date.

        actual_date = (
            service_date
            + timedelta(
                days=day_offset
            )
        )

        if (
            actual_date
            not in target_dates
        ):
            continue

        direction = determine_direction(
            trip_stop_times,
            station_index,
            stop_by_id,
        )

        first_stop = (
            trip_stop_times[0]
        )

        last_stop = (
            trip_stop_times[-1]
        )

        timed_stops = frozenset(
            (
                stop_time.get(
                    "stop_id",
                    "",
                ),
                normalize_gtfs_time(
                    stop_time.get(
                        "arrival_time",
                        "",
                    )
                ),
                normalize_gtfs_time(
                    stop_time.get(
                        "departure_time",
                        "",
                    )
                ),
            )
            for stop_time
            in trip_stop_times
            if stop_time.get(
                "stop_id"
            )
        )

        try:

            orpesa_stop_sequence = int(
                station_time.get(
                    "stop_sequence",
                    str(
                        station_index + 1
                    ),
                )
            )

        except ValueError:

            orpesa_stop_sequence = (
                station_index + 1
            )

        result.append(
            Candidate(

                service_date=
                    service_date,

                actual_date=
                    actual_date,

                seconds_of_day=
                    seconds_of_day,

                departure=
                    hhmm(
                        departure_raw
                    ),

                arrival=
                    hhmm(
                        arrival_raw
                    ),

                direction=
                    direction,

                trip_id=
                    trip_id,

                train_number=
                    trip.get(
                        "trip_short_name",
                        "",
                    ),

                route_id=
                    trip.get(
                        "route_id",
                        "",
                    ),

                service_id=
                    trip.get(
                        "service_id",
                        "",
                    ),

                service=
                    service,

                origin_stop_id=
                    first_stop.get(
                        "stop_id",
                        "",
                    ),

                origin_raw=
                    stop_by_id[
                        first_stop[
                            "stop_id"
                        ]
                    ][
                        "stop_name"
                    ],

                destination_stop_id=
                    last_stop.get(
                        "stop_id",
                        "",
                    ),

                destination_raw=
                    stop_by_id[
                        last_stop[
                            "stop_id"
                        ]
                    ][
                        "stop_name"
                    ],

                stop_count=
                    len(
                        trip_stop_times
                    ),

                orpesa_stop_sequence=
                    orpesa_stop_sequence,

                timed_stops=
                    timed_stops,
            )
        )

    return result


def build_schedule(
    gtfs_zip: Path,
    start_date: date,
    days: int,
) -> Dict[str, object]:

    if days < 1:
        raise ValueError(
            "--days must be >= 1"
        )

    with zipfile.ZipFile(
        gtfs_zip,
        "r",
    ) as zf:

        names = set(
            zf.namelist()
        )

        missing = sorted(
            REQUIRED_FILES - names
        )

        if missing:

            raise RuntimeError(
                "GTFS ZIP is missing required files: "
                + ", ".join(
                    missing
                )
            )

        agency_rows = read_gtfs_csv(
            zf,
            "agency.txt",
        )

        calendar_rows = read_gtfs_csv(
            zf,
            "calendar.txt",
        )

        calendar_date_rows = (
            read_gtfs_csv(
                zf,
                "calendar_dates.txt",
            )
        )

        route_rows = read_gtfs_csv(
            zf,
            "routes.txt",
        )

        stop_time_rows = read_gtfs_csv(
            zf,
            "stop_times.txt",
        )

        stop_rows = read_gtfs_csv(
            zf,
            "stops.txt",
        )

        trip_rows = read_gtfs_csv(
            zf,
            "trips.txt",
        )

    route_by_id = {
        row["route_id"]: row
        for row
        in route_rows
    }

    stop_by_id = {
        row["stop_id"]: row
        for row
        in stop_rows
    }

    if STATION_ID not in stop_by_id:

        raise RuntimeError(
            "Station stop_id="
            f"{STATION_ID} "
            "not found in stops.txt"
        )

    stop_times_by_trip: Dict[
        str,
        List[Dict[str, str]],
    ] = defaultdict(list)

    for row in stop_time_rows:

        stop_times_by_trip[
            row["trip_id"]
        ].append(
            row
        )

    for trip_id in stop_times_by_trip:

        stop_times_by_trip[
            trip_id
        ].sort(
            key=lambda row: int(
                row.get(
                    "stop_sequence",
                    "0",
                )
                or "0"
            )
        )

    target_dates = {
        (
            start_date
            + timedelta(
                days=offset
            )
        )
        for offset
        in range(
            days
        )
    }

    candidates_by_date: Dict[
        date,
        List[Candidate],
    ] = defaultdict(list)

    # Смотрим также предыдущую service_date,
    # потому что поезд может иметь GTFS-время
    # больше 24:00.

    first_service_date = (
        start_date
        - timedelta(
            days=1
        )
    )

    last_target_date = (
        start_date
        + timedelta(
            days=days - 1
        )
    )

    service_date = (
        first_service_date
    )

    while (
        service_date
        <= last_target_date
    ):

        active_services = (
            active_service_ids(
                service_date,
                calendar_rows,
                calendar_date_rows,
            )
        )

        candidates = (
            build_candidates_for_service_date(
                service_date=
                    service_date,

                target_dates=
                    target_dates,

                active_services=
                    active_services,

                route_by_id=
                    route_by_id,

                trip_rows=
                    trip_rows,

                stop_by_id=
                    stop_by_id,

                stop_times_by_trip=
                    stop_times_by_trip,
            )
        )

        for candidate in candidates:

            candidates_by_date[
                candidate.actual_date
            ].append(
                candidate
            )

        service_date += timedelta(
            days=1
        )

    days_json: List[
        Dict[str, object]
    ] = []

    for current_date in sorted(
        target_dates
    ):

        day_candidates = sorted(
            candidates_by_date.get(
                current_date,
                [],
            ),
            key=lambda candidate: (
                candidate.seconds_of_day,
                candidate.direction,
                candidate.trip_id,
            ),
        )

        # Сначала объединяем потенциальные
        # совпадения по:
        #
        # время + направление.
        #
        # Потом уже проверяем реальные
        # общие станции/времена.

        coarse_groups: Dict[
            Tuple[int, str],
            List[Candidate],
        ] = defaultdict(list)

        for candidate in day_candidates:

            coarse_groups[
                (
                    candidate.seconds_of_day,
                    candidate.direction,
                )
            ].append(
                candidate
            )

        merged_components: List[
            List[Candidate]
        ] = []

        for key in sorted(
            coarse_groups
        ):

            merged_components.extend(
                connected_components(
                    coarse_groups[
                        key
                    ]
                )
            )

        merged_components.sort(
            key=lambda component: (
                primary_candidate(
                    component
                ).seconds_of_day,
                (
                    0
                    if primary_candidate(
                        component
                    ).direction
                    == "south"
                    else 1
                ),
                primary_candidate(
                    component
                ).trip_id,
            )
        )

        trips_json = [
            trip_json(
                component
            )
            for component
            in merged_components
        ]

        days_json.append(
            {
                "date":
                    current_date.isoformat(),

                "trips":
                    trips_json,
            }
        )

    station = stop_by_id[
        STATION_ID
    ]

    if agency_rows:

        agency_name = (
            agency_rows[0].get(
                "agency_name",
                "Renfe Operadora",
            )
        )

    else:

        agency_name = (
            "Renfe Operadora"
        )

    now = datetime.now(
        ZoneInfo(
            TIMEZONE
        )
    ).replace(
        microsecond=0
    ).isoformat()

    return {

        "schemaVersion":
            1,

        "generatedAt":
            now,

        "station": {

            "id":
                STATION_ID,

            "name":
                station.get(
                    "stop_name",
                    "Orpesa",
                ),

            "latitude":
                float(
                    station[
                        "stop_lat"
                    ]
                ),

            "longitude":
                float(
                    station[
                        "stop_lon"
                    ]
                ),
        },

        "timezone":
            TIMEZONE,

        "source":
            agency_name,

        "serviceFilter":
            sorted(
                ALLOWED_SERVICES
            ),

        "days":
            days_json,
    }


def parse_iso_date(
    value: str,
) -> date:

    try:

        return date.fromisoformat(
            value
        )

    except ValueError as exc:

        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; "
            "expected YYYY-MM-DD"
        ) from exc


def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Generate Orpesa "
            "REGIONAL/REG.EXP. "
            "schedule from Renfe GTFS."
        )
    )

    parser.add_argument(
        "--gtfs",
        required=True,
        type=Path,
        help=(
            "Path to Renfe "
            "google_transit.zip"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "train/"
            "orpesa_train_schedule.json"
        ),
        help=(
            "Output JSON path"
        ),
    )

    parser.add_argument(
        "--start-date",
        type=parse_iso_date,
        default=None,
        help=(
            "First calendar date "
            "(YYYY-MM-DD). "
            "Default: today in "
            "Europe/Madrid."
        ),
    )

    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=(
            "Number of calendar "
            "days to generate. "
            f"Default: {DEFAULT_DAYS}"
        ),
    )

    args = parser.parse_args()

    if not args.gtfs.is_file():

        print(
            "ERROR: GTFS ZIP not found: "
            f"{args.gtfs}",
            file=sys.stderr,
        )

        return 2

    start_date = (
        args.start_date
        or datetime.now(
            ZoneInfo(
                TIMEZONE
            )
        ).date()
    )

    try:

        schedule = build_schedule(
            args.gtfs,
            start_date,
            args.days,
        )

    except Exception as exc:

        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )

        return 1

    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    args.output.write_text(
        json.dumps(
            schedule,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        f"Wrote: {args.output}"
    )

    for day in schedule["days"]:

        print(
            f"{day['date']}: "
            f"{len(day['trips'])} trains"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )