#!/usr/bin/env python3
"""Publish a metro-only static GTFS derived from Roma Mobilita's official feed."""
import csv
import hashlib
import io
import os
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile, ZIP_DEFLATED

SOURCE = "https://romamobilita.it/sites/default/files/rome_static_gtfs.zip"
OUTPUT = Path("dist/metro_static_gtfs.zip")
MAX_SOURCE = 64 * 1024 * 1024
MAX_OUTPUT = 8 * 1024 * 1024


def read_csv(archive, name):
    if name not in archive.namelist():
        return [], []
    with archive.open(name) as stream:
        with io.TextIOWrapper(stream, encoding="utf-8-sig", newline="") as text:
            reader = csv.DictReader(text)
            return reader.fieldnames or [], list(reader)


def write_csv(archive, name, fields, rows):
    if not fields:
        return
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n",
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    archive.writestr(name, output.getvalue().encode("utf-8"))


def main():
    request = Request(SOURCE, headers={"User-Agent": "AppMezzi-GTFS-Metro/1.0"})
    with urlopen(request, timeout=90) as response:
        data = response.read(MAX_SOURCE + 1)
    if len(data) > MAX_SOURCE:
        raise RuntimeError("Official GTFS exceeds source size limit")

    with ZipFile(io.BytesIO(data)) as source:
        routes_fields, routes = read_csv(source, "routes.txt")
        # GTFS route_type=1 is subway/metro.
        metro_routes = [r for r in routes if r.get("route_type", "").strip() == "1"]
        route_ids = {r["route_id"] for r in metro_routes}
        if not route_ids:
            raise RuntimeError("Official GTFS contains no metro routes")
        trips_fields, trips = read_csv(source, "trips.txt")
        trips = [t for t in trips if t.get("route_id") in route_ids]
        trip_ids = {t["trip_id"] for t in trips}
        if not trip_ids:
            raise RuntimeError("No metro trips found")
        times_fields, times = read_csv(source, "stop_times.txt")
        times = [t for t in times if t.get("trip_id") in trip_ids]
        stop_ids = {t["stop_id"] for t in times}
        if not stop_ids:
            raise RuntimeError("No metro stop times found")
        stops_fields, all_stops = read_csv(source, "stops.txt")
        # Retain parent stations to preserve the GTFS station hierarchy.
        by_stop_id = {s["stop_id"]: s for s in all_stops}
        pending = list(stop_ids)
        while pending:
            stop = by_stop_id.get(pending.pop())
            if stop:
                parent = stop.get("parent_station", "")
                if parent and parent not in stop_ids:
                    stop_ids.add(parent)
                    pending.append(parent)
        stops = [s for s in all_stops if s["stop_id"] in stop_ids]
        services = {t["service_id"] for t in trips if t.get("service_id")}
        shapes = {t["shape_id"] for t in trips if t.get("shape_id")}
        agencies = {r.get("agency_id") for r in metro_routes if r.get("agency_id")}

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        temp = OUTPUT.with_suffix(".tmp")
        with ZipFile(temp, "w", compression=ZIP_DEFLATED, compresslevel=9) as dest:
            for name, fields, rows in (
                ("agency.txt", *read_csv(source, "agency.txt")),
                ("routes.txt", routes_fields, metro_routes),
                ("trips.txt", trips_fields, trips),
                ("stop_times.txt", times_fields, times),
                ("stops.txt", stops_fields, stops),
            ):
                if name == "agency.txt" and agencies:
                    rows = [row for row in rows if row.get("agency_id") in agencies]
                write_csv(dest, name, fields, rows)
            for name, field, ids in (
                ("calendar.txt", "service_id", services),
                ("calendar_dates.txt", "service_id", services),
                ("shapes.txt", "shape_id", shapes),
                ("frequencies.txt", "trip_id", trip_ids),
            ):
                fields, rows = read_csv(source, name)
                write_csv(dest, name, fields, (r for r in rows if r.get(field) in ids))
            for name in ("feed_info.txt", "attributions.txt"):
                fields, rows = read_csv(source, name)
                write_csv(dest, name, fields, rows)

        if temp.stat().st_size > MAX_OUTPUT:
            temp.unlink()
            raise RuntimeError("Filtered metro feed exceeds app's 8 MiB limit")
        os.replace(temp, OUTPUT)
        digest = hashlib.md5(OUTPUT.read_bytes()).hexdigest()
        Path(str(OUTPUT) + ".md5").write_text(digest + "  metro_static_gtfs.zip\n", encoding="utf-8")
        print(f"Metro routes: {len(route_ids)}, trips: {len(trips)}, stops: {len(stops)}, bytes: {OUTPUT.stat().st_size}")


if __name__ == "__main__":
    main()
