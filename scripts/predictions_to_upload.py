#!/usr/bin/env python
"""
Turn your model's output into the website's "Model predictions" upload file (boxes + predictions in one file).

    python scripts/predictions_to_upload.py my_predictions.csv download.csv valid_species.csv described_names.csv out/

Edit the SETTINGS block below to match your file's column names, then run it. It writes:
    out/upload_01.csv, upload_02.csv ...   the files to upload (each under the site's 50 MB limit)
    out/label_map.csv                      how each of your labels was matched (check it once)
    out/unmatched_labels.csv               labels it could not match: add them to label_overrides.csv
Needs pandas.
"""
import re
import sys
from pathlib import Path

import pandas as pd

# ------------------------------------------------------------------ SETTINGS: edit to match your file
MODEL_NAME = "ibbi-0.3.1"          # shown on the site as the model's name
MODEL_VERSION = "2026-10"          # uploading the same name + version again replaces those predictions

IMAGE_COLUMN = "image"             # file name or image_id of the photo; the part before the extension is used
BOX_FORMAT = "xywh_fraction"       # "xywh_fraction": x, y (top-left), width, height as 0-1
                                   # "cxcywh_fraction": YOLO style, centre x, centre y, width, height as 0-1
                                   # "xyxy_pixels": x1, y1, x2, y2 in pixels (needs the WIDTH/HEIGHT columns)
BOX_COLUMNS = ["x", "y", "w", "h"]
WIDTH_COLUMN, HEIGHT_COLUMN = "img_width", "img_height"   # only for xyxy_pixels

# Your label and probability column for each rank (None when your file does not have that rank)
RANKS = {
    "subfamily": ("subfamily", "subfamily_prob"),
    "tribe": ("tribe", "tribe_prob"),
    "genus": ("genus", "genus_prob"),
    "species": ("species", "species_prob"),      # required: "Genus species" (underscores are fine)
}
# Runners-up for the species, best first (leave empty if you have none)
TOP_K = [("species_2", "species_2_prob"), ("species_3", "species_3_prob")]

ONLY_UNVALIDATED = True            # skip images the gallery already marks as validated
MIN_SPECIES_CONFIDENCE = 0.0       # drop boxes the model is less sure of than this
# ------------------------------------------------------------------

MAX_BYTES = 45 * 1024 * 1024
UPPER = ["subfamily", "tribe", "genus"]


def norm(name):
    """'Xyleborus_affinis Eichhoff, 1868' -> 'xyleborus affinis'"""
    words = re.sub(r"[_\s]+", " ", str(name or "")).strip().split()
    return " ".join(words[:2]).lower()


def prob(series):
    s = pd.to_numeric(series, errors="coerce")
    s = s.where(s <= 1, s / 100)        # 87 means 87 %
    return s.clip(0, 1).round(4)


def species_lookup(species_csv, synonyms_csv, overrides_csv):
    sp = pd.read_csv(species_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    lookup, how = {}, {}
    for g, s, vid in zip(sp["genus"], sp["species"], sp["valid_species_id"]):
        if g and s and vid:
            lookup.setdefault(norm(f"{g} {s}"), vid)
            how.setdefault(norm(f"{g} {s}"), "species list")
    syn = pd.read_csv(synonyms_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for name, vid in zip(syn["describedScientificName"], syn["name_valid_species_id"]):
        if vid and norm(name) not in lookup:
            lookup[norm(name)] = vid
            how[norm(name)] = "synonym"
    if overrides_csv.exists():   # model_label,valid_species_id written by you for what is left
        for label, vid in pd.read_csv(overrides_csv, dtype=str, keep_default_na=False).values[:, :2]:
            lookup[norm(label)] = vid
            how[norm(label)] = "your override"
    ranks = {rank: {v.lower(): v for v in sp[rank] if v} for rank in UPPER if rank in sp}
    return lookup, how, ranks


def to_fraction_boxes(df):
    a, b, c, d = (pd.to_numeric(df[col], errors="coerce") for col in BOX_COLUMNS)
    if BOX_FORMAT == "xywh_fraction":
        x, y, w, h = a, b, c, d
    elif BOX_FORMAT == "cxcywh_fraction":
        x, y, w, h = a - c / 2, b - d / 2, c, d
    elif BOX_FORMAT == "xyxy_pixels":
        W, H = (pd.to_numeric(df[col], errors="coerce") for col in (WIDTH_COLUMN, HEIGHT_COLUMN))
        x, y, w, h = a / W, b / H, (c - a) / W, (d - b) / H
    else:
        sys.exit(f"Unknown BOX_FORMAT {BOX_FORMAT!r}")
    x, y = x.clip(0, 1), y.clip(0, 1)
    w, h = pd.concat([w, 1 - x], axis=1).min(axis=1), pd.concat([h, 1 - y], axis=1).min(axis=1)
    return x.round(6), y.round(6), w.round(6), h.round(6)


def main(pred_csv, download_csv, species_csv, synonyms_csv, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    pred = pd.read_csv(pred_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    download = pd.read_csv(download_csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    lookup, how, ranks = species_lookup(species_csv, synonyms_csv, out / "label_overrides.csv")

    # 1. Which gallery image each row is
    pred["image_id"] = pred[IMAGE_COLUMN].map(lambda p: Path(str(p).replace("\\", "/")).stem)
    images = download.drop_duplicates("image_id").set_index("image_id")
    known = pred["image_id"].isin(images.index)
    print(f"{len(pred):,} rows; {(~known).sum():,} are on images not in the download (left out).")
    pred = pred[known]
    if ONLY_UNVALIDATED:
        validated = images["is_validated"].str.lower().isin(["true", "t", "1"])
        before = len(pred)
        pred = pred[~pred["image_id"].map(validated)]
        print(f"Left out {before - len(pred):,} rows on validated images.")

    # 2. Your species labels -> species-list ids
    label_col, prob_col = RANKS["species"]
    labels = pd.Series(sorted(pred[label_col].unique()))
    table = pd.DataFrame({"model_label": labels, "valid_species_id": labels.map(lambda l: lookup.get(norm(l), "")),
                          "matched_by": labels.map(lambda l: how.get(norm(l), ""))})
    table.to_csv(out / "label_map.csv", index=False)
    missing = table[table["valid_species_id"] == ""]
    missing[["model_label"]].assign(valid_species_id="").to_csv(out / "unmatched_labels.csv", index=False)
    print(f"Species labels: {len(table) - len(missing)} of {len(table)} matched"
          + (f"; {len(missing)} not matched (see unmatched_labels.csv)." if len(missing) else "."))

    rows = pd.DataFrame({"record_id": "", "image_id": pred["image_id"]})
    rows["bbox_x"], rows["bbox_y"], rows["bbox_width"], rows["bbox_height"] = to_fraction_boxes(pred)
    rows["valid_species_id"] = pred[label_col].map(lambda l: lookup.get(norm(l), ""))
    rows["confidence"] = prob(pred[prob_col])

    # 3. Higher ranks: names as in the species list, both columns or neither
    for rank in UPPER:
        cols = RANKS.get(rank)
        if not cols or cols[0] not in pred:
            continue
        names = pred[cols[0]].map(lambda v: ranks.get(rank, {}).get(str(v).strip().lower(), ""))
        conf = prob(pred[cols[1]])
        rows[rank] = names
        rows[f"{rank}_confidence"] = conf.where(names != "", "")

    # 4. Runners-up as "id:confidence;..."
    def top_k(r):
        cells, primary = [], lookup.get(norm(r[label_col]), "")
        for name_col, p_col in TOP_K:
            vid = lookup.get(norm(r.get(name_col, "")), "")
            if vid and vid != primary and vid not in [c.split(":")[0] for c in cells] and r.get(p_col, ""):
                cells.append(f"{vid}:{min(1.0, max(0.0, float(r[p_col]) / (100 if float(r[p_col]) > 1 else 1))):.4f}")
        return ";".join(cells)
    rows["top_k"] = pred.apply(top_k, axis=1) if TOP_K and TOP_K[0][0] in pred else ""
    rows["model_name"], rows["model_version"] = MODEL_NAME, MODEL_VERSION

    # 5. Keep what the site will accept
    bad_box = (rows["bbox_width"] <= 0) | (rows["bbox_height"] <= 0) | rows[["bbox_x", "bbox_y"]].isna().any(axis=1)
    keep = (rows["valid_species_id"] != "") & rows["confidence"].notna() & ~bad_box
    keep &= rows["confidence"] >= MIN_SPECIES_CONFIDENCE
    print(f"Left out {(~keep).sum():,} rows (unmatched species, missing confidence, empty box or below the minimum).")
    rows = rows[keep].sort_values("image_id")

    # 6. Files under the size limit, never splitting an image's rows
    part, start, size, paths = 1, 0, 0, []
    lines = rows.to_csv(index=False, header=False).splitlines()
    ids = rows["image_id"].tolist()
    for i, line in enumerate(lines + [None]):
        end = line is None or (size + len(line) > MAX_BYTES and ids[i] != ids[i - 1])
        if end and i > start:
            path = out / f"upload_{part:02d}.csv"
            rows.iloc[start:i].to_csv(path, index=False)
            paths.append(path)
            part, start, size = part + 1, i, 0
        if line is not None:
            size += len(line) + 1
    print(f"{len(rows):,} boxes on {rows['image_id'].nunique():,} images, in {len(paths)} file(s):")
    for p in paths:
        print(f"  {p}  ({p.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 6:
        sys.exit(__doc__)
    main(*sys.argv[1:])
