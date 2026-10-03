#!/usr/bin/env python
"""
Turn a detector + classifier run (a folder such as
Gallery_backup/ml_predictions_db20260920_0911/M2e20__dinov3L336_npos_oe_supcon_sc0__gallery_op/)
into files the website takes as they are:

  1. boxes       -> CSVs for Data Management > Update Metadata (one new box per row).
  2. predictions -> CSV(s) for Data Management > Model predictions (species + per-rank confidence per box).

Both steps start from a gallery metadata download (Data Management > Download, the metadata CSV):

* The Update dialog wants every editable column, and it writes every cell it gets: a blank image cell
  (photographer, usage statement, ...) would clear that field for the whole image. So each row here is a
  copy of the record's (or, for a NEW record, a sibling's) row in the download, with only the box filled in.
* Predictions are attached to a specimen row (record_id), and a box the detector found on its own has
  no row until step 1 has been applied. So: download, run step 1, upload its files, download again, run step 2.

    python scripts/ml_predictions_to_uploads.py boxes RUN_FOLDER OUT_FOLDER --download before.csv
    python scripts/ml_predictions_to_uploads.py predictions RUN_FOLDER OUT_FOLDER --download after.csv \
        [--species-list species_list.csv]

Needs pandas (and pyarrow to read .parquet; without it the .csv.gz files are read instead).
"""
import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

BOX = ["bbox_x", "bbox_y", "bbox_width", "bbox_height"]
LEVELS = ["subfamily", "tribe", "genus", "species"]
UPDATE_MAX_BYTES = 9 * 1024 * 1024        # the site refuses update CSVs of 10 MB or more
PREDICTIONS_MAX_BYTES = 45 * 1024 * 1024  # and prediction CSVs of 50 MB or more
DIGITS = 6


# ---------------------------------------------------------------- reading the run folder

def read_table(folder, name, columns=None):
    folder = Path(folder)
    parquet = folder / f"{name}.parquet"
    try:
        import pyarrow  # noqa: F401
        if parquet.exists():
            return pd.read_parquet(parquet, columns=columns)
    except ImportError:
        pass
    gz = folder / f"{name}.csv.gz"
    if gz.exists():
        return pd.read_csv(gz, usecols=columns, dtype=str, keep_default_na=False)
    sys.exit(f"Neither {parquet.name} (with pyarrow installed) nor {gz.name} was found in {folder}.")


def text(value):
    """Database columns come as text ('t'/'f', '' for none); NaN and None are empty."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def truthy(value):
    return text(value).lower() in ("true", "t", "1", "yes")


def prob(value):
    """Float32 noise (1.0000003, -3e-07) clipped into 0-1, rounded for a smaller file."""
    return round(min(1.0, max(0.0, float(value))), 4)


def model_name_and_version(folder):
    """The model is the folder name; the version is the database backup it was run on (the parent folder)."""
    folder = Path(folder).resolve()
    model = folder.name
    version = folder.parent.name.replace("ml_predictions_", "")
    return model[:100], version[:50]


# ---------------------------------------------------------------- step 1: boxes

def clean_box(row):
    x, y, w, h = (min(1.0, max(0.0, float(row[c]))) for c in BOX)
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)   # keep it inside the image
    return [round(v, DIGITS) for v in (x, y, w, h)]


# Columns of the Update dialog (views.UPDATE_* ); a download has exactly these.
IMAGE_COLUMNS = ["image_institution", "photographer", "image_email", "photo_usage_statement", "resolution_in_ppmm",
                 "image_notes", "image_date_taken", "image_has_multiple_individuals", "is_validated"]
RECORD_COLUMNS = ["alias_id", "aspect", "depicts_specimen", "depicts_valid_name_id", "depicts_described_name_id",
                  "depicts_name_verbatim", "collection_country", "collection_stateProvince", "specimen_sex",
                  "specimen_type_status", "specimen_notes", "bbox_is_validated"]
UPDATE_COLUMNS = ["record_id", "image_id", *IMAGE_COLUMNS, *RECORD_COLUMNS, *BOX]


def read_download(path):
    dl = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    dl.columns = [{"alternative_id": "alias_id"}.get(c.strip(), c.strip()) for c in dl.columns]   # old name
    missing = [c for c in UPDATE_COLUMNS if c not in dl.columns]
    if missing:
        sys.exit(f"{path} is not a gallery metadata download: it has no {', '.join(missing)} column(s).")
    return dl


def plan_boxes(detections, download, min_conf=None):
    """
    One update row per detection that becomes a new box: on the image's own box-less record when it has one
    (the site refuses a second record next to an unboxed one), else as a NEW record. Detections that already
    overlap a database box (db_beetle_id) are left out: that record has its box.
    Returns (rows for the Update dialog with a detection_id column, detections skipped for having no record).
    """
    det = detections.copy()
    if min_conf is None:
        det = det[det["det_above_operating_conf"].map(truthy)]
    else:
        det = det[det["det_conf"].astype(float) >= min_conf]
    det = det[det["db_beetle_id"].map(text) == ""]

    by_image = {image_id: g for image_id, g in download.groupby("image_id", sort=False)}
    rows, skipped = [], 0
    det = det.assign(_conf=det["det_conf"].astype(float), _image=det["image_asset_id"].map(text))
    for image_id, group in det.sort_values(["_image", "_conf"], ascending=[True, False]).groupby("_image", sort=True):
        records = by_image.get(image_id)
        if records is None:
            skipped += len(group)   # no live record on this image in the download: nothing to copy the image from
            continue
        records = [{c: text(r[c]) for c in UPDATE_COLUMNS} for r in records.sort_values("record_id").to_dict("records")]
        free = [r for r in records if r["bbox_x"] == ""]
        for _, d in group.iterrows():
            if free:
                row = free.pop(0)
            else:
                row = dict(records[0], record_id="NEW")
                for c in RECORD_COLUMNS:       # a new specimen: the image's details, nothing of its neighbour's
                    row[c] = ""
            row.update(zip(BOX, clean_box(d)))
            rows.append({"detection_id": text(d["detection_id"]), **row})
    return pd.DataFrame(rows, columns=["detection_id", *UPDATE_COLUMNS]), skipped


def write_chunks(frame, out, stem, max_bytes, group=None):
    """Write CSVs under max_bytes, never splitting a group (an image's rows stay together)."""
    out.mkdir(parents=True, exist_ok=True)
    paths, start, size, part = [], 0, 0, 1
    header = len(",".join(frame.columns)) + 1
    sizes = frame.to_csv(index=False, header=False).splitlines()
    keys = frame[group].tolist() if group else list(range(len(frame)))
    cut = []
    for i, line in enumerate(sizes):
        if size + len(line) + 1 + header > max_bytes and i > start and keys[i] != keys[i - 1]:
            cut.append((start, i))
            start, size = i, 0
        size += len(line) + 1
    cut.append((start, len(frame)))
    for a, b in cut:
        path = out / f"{stem}_{part:02d}.csv"
        frame.iloc[a:b].to_csv(path, index=False)
        paths.append(path)
        part += 1
    return paths


def cmd_boxes(args):
    detections = read_table(args.run, "detections", [
        "detection_id", "image_asset_id", "det_conf", "det_above_operating_conf", "db_beetle_id", *BOX])
    download = read_download(args.download)
    plan, skipped = plan_boxes(detections, download, args.min_conf)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    plan[["detection_id", "record_id", "image_id", *BOX]].to_csv(out / "boxes_plan.csv", index=False)  # for step 2
    paths = write_chunks(plan.drop(columns=["detection_id"]), out, "boxes_update", UPDATE_MAX_BYTES, group="image_id")
    on_existing = (plan["record_id"] != "NEW").sum()
    print(f"{len(plan):,} boxes: {on_existing:,} on an existing box-less record, {len(plan) - on_existing:,} as NEW records,"
          f" on {plan['image_id'].nunique():,} images.")
    if skipped:
        print(f"Left out {skipped:,} boxes on images with no record in the download (deleted, or not downloaded).")
    print("Upload each of these with Data Management > Update Metadata (wait for each to finish):")
    for p in paths:
        print(f"  {p}  ({p.stat().st_size / 1e6:.1f} MB)")


# ---------------------------------------------------------------- step 2: predictions

def species_ids_by_name(path, detections):
    """'Genus species' -> valid_species_id, from a species list (or from the predictions themselves)."""
    names = {}
    if path:
        sp = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        sp.columns = [c.strip() for c in sp.columns]
        name_col = next((c for c in ("scientificName", "scientific_name", "taxonomy_scientific_name") if c in sp), None)
        for _, r in sp.iterrows():
            vid = text(r.get("valid_species_id"))
            if not vid:
                continue
            if r.get("genus") and r.get("species"):
                names.setdefault(f"{text(r['genus'])} {text(r['species'])}", vid)
            if name_col:
                names.setdefault(" ".join(text(r[name_col]).split()[:2]), vid)
    for name, vid in zip(detections["species_taxon"].map(text), detections["species_valid_species_id"].map(text)):
        if name and vid:
            names.setdefault(name, vid)
    return names


def top_k_cell(topk, names, primary):
    """The species top-3 as the site wants it: 'valid_species_id:confidence;...' (unknown names are left out)."""
    try:
        pairs = json.loads(text(topk) or "[]")
    except ValueError:
        return ""
    cells = []
    for name, p in pairs:
        vid = names.get(text(name))
        if vid and vid != primary:
            cells.append(f"{vid}:{prob(p)}")
    return ";".join(cells)


def match_new_records(plan, download):
    """record_id for each NEW box, found in the post-upload download by image and box."""
    dl = download[download["bbox_x"].map(text) != ""].copy()
    for c in BOX:
        dl[c] = dl[c].astype(float).round(DIGITS - 1)
    index = {}
    for _, r in dl.iterrows():
        index[(text(r["image_id"]), *(r[c] for c in BOX))] = text(r["record_id"])
    found = {}
    for _, p in plan[plan["record_id"] == "NEW"].iterrows():
        key = (text(p["image_id"]), *(round(float(p[c]), DIGITS - 1) for c in BOX))
        if key in index:
            found[p["detection_id"]] = index[key]
    return found


def cmd_predictions(args):
    detections = read_table(args.run, "detections")
    plan = pd.read_csv(Path(args.out) / "boxes_plan.csv", dtype=str, keep_default_na=False)
    download = pd.read_csv(args.download, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    model, version = model_name_and_version(args.run)

    record = {d: text(r) for d, r in zip(detections["detection_id"], detections["db_beetle_id"]) if text(r)}
    record.update({d: r for d, r in zip(plan["detection_id"], plan["record_id"]) if r != "NEW"})
    new = match_new_records(plan, download)
    record.update(new)
    missing_new = (plan["record_id"] == "NEW").sum() - len(new)

    names = species_ids_by_name(args.species_list, detections)
    rows, skipped_unrecognised = [], 0
    for _, d in detections.iterrows():
        rid = record.get(text(d["detection_id"]))
        if not rid:
            continue
        if not args.include_unrecognised and int(float(d["reported_depth"] or 0)) == 0:
            skipped_unrecognised += 1
            continue
        species = text(d["species_valid_species_id"])
        if not species:
            continue
        row = {
            "record_id": rid,
            "valid_species_id": species,
            "confidence": prob(d["species_prob"]),
            "model_name": model,
            "model_version": version,
            "top_k": top_k_cell(d.get("species_topk"), names, species),
        }
        for level in LEVELS[:3]:
            if text(d[f"{level}_taxon"]):
                row[level] = text(d[f"{level}_taxon"])
                row[f"{level}_confidence"] = prob(d[f"{level}_prob"])
            else:
                row[level] = row[f"{level}_confidence"] = ""
        rows.append(row)
    frame = pd.DataFrame(rows).drop_duplicates(subset=["record_id"], keep="first")
    paths = write_chunks(frame, Path(args.out), "predictions", PREDICTIONS_MAX_BYTES)
    print(f"{len(frame):,} predictions for model {model!r}, version {version!r}.")
    if skipped_unrecognised:
        print(f"Left out {skipped_unrecognised:,} 'unrecognised' detections (reported depth 0); --include-unrecognised keeps them.")
    if missing_new:
        print(f"WARNING: {missing_new:,} NEW boxes were not found in the download. Was every boxes_update file applied, "
              "and the download made after that?")
    print("Upload with Data Management > Model predictions (tick 'Check only' first):")
    for p in paths:
        print(f"  {p}  ({p.stat().st_size / 1e6:.1f} MB)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="step", required=True)
    b = sub.add_parser("boxes", help="update CSVs that add the detector's boxes")
    b.add_argument("run", help="the run folder (with detections.parquet)")
    b.add_argument("out", help="where to write the CSVs")
    b.add_argument("--download", required=True, help="a gallery metadata download made just before this upload")
    b.add_argument("--min-conf", type=float, default=None,
                   help="detector confidence to keep (default: the run's operating point, det_above_operating_conf)")
    b.set_defaults(func=cmd_boxes)
    p = sub.add_parser("predictions", help="the model predictions CSV, after the boxes are on the site")
    p.add_argument("run")
    p.add_argument("out", help="the folder used for the boxes step (it holds boxes_plan.csv)")
    p.add_argument("--download", required=True, help="a gallery metadata download made after the boxes were applied")
    p.add_argument("--species-list", help="a species list CSV (valid_species_id + genus/species or scientificName) "
                                          "so the runners-up (top_k) can be given as ids")
    p.add_argument("--include-unrecognised", action="store_true",
                   help="also upload detections the model reports as unrecognised (depth 0)")
    p.set_defaults(func=cmd_predictions)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
