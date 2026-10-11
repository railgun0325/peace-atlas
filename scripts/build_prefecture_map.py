"""Build an offline, simplified statistical map from the attributed N03 data.

python scripts/build_prefecture_map.py --source /path/data.geojson --output /path/japan_prefectures.json
The source SHA is pinned; the geometry is simplified, never drawn from guesses.
"""
import argparse
import hashlib
import json
from pathlib import Path

from shapely.geometry import shape, box


def build(source):
    raw = source.read_bytes()
    expected = "c823cde901bb077cd3f861632bbcdd3676283d9e6f30c76b0d0fab330ebf157e"
    assert hashlib.sha256(raw).hexdigest() == expected, "unexpected source version"
    records = []
    for feature in json.loads(raw)["features"]:
        # Remote islands east of 147E are omitted at this display scale. Tokyo
        # remains represented by its mainland geometry and nearby islands.
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        geometry = geometry.intersection(box(122, 23.5, 147, 46.5)).simplify(.018, preserve_topology=True)
        paths = []
        polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
        for polygon in polygons:
            if polygon.geom_type != "Polygon" or polygon.area < .00015:
                continue
            for ring in [polygon.exterior, *polygon.interiors]:
                points = [(round((lon - 122) * 29 + 15, 2), round((46.5 - lat) * 24 + 12, 2)) for lon, lat in ring.coords]
                paths.append("M" + "L".join(f"{x},{y}" for x, y in points) + "Z")
        center = geometry.representative_point()
        records.append({"code": feature["properties"]["PREF_CODE"], "name": feature["properties"]["N03_001"], "path": "".join(paths),
                        "label": [round((center.x - 122) * 29 + 15, 2), round((46.5 - center.y) * 24 + 12, 2)]})
    assert len(records) == 47 and len({r["name"] for r in records}) == 47 and all(r["path"] for r in records)
    return {"viewBox": "0 0 760 590", "prefectures": records,
            "attribution": "国土数値情報（行政区域データ）2026（国土交通省）を加工して作成。表示用に海岸線を簡略化し、遠方の小島を省略。",
            "source": "https://nlftp.mlit.go.jp/ksj/gml/datalist/KsjTmplt-N03-2026.html",
            "derived_download": "https://geo.maderaojen.me/datasets/jp-prefectures/releases/v1/2026-08-04.1/data.geojson",
            "source_sha256": expected, "license": "CC BY 4.0", "license_url": "https://nlftp.mlit.go.jp/ksj/other/agreement.html"}


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    args.output.write_text(json.dumps(build(args.source), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
