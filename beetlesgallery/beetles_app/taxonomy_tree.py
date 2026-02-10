"""
taxonomy_tree – build and serve the taxonomy browsing tree.

The tree is derived from valid_species.csv and stored as
``reference/taxonomy_tree.json`` in default_storage so it is generated once
and served cheaply.

Structure (mirrors the taxonomy_response example):

    [                                   ← top-level array, one entry per subfamily
      {
        "id": "subfamily-<slug>",
        "name": "<Subfamily>",
        "level": "subfamily",
        "speciesCount": <int>,          ← total leaf species inside
        "children": [                   ← tribes
          {
            "id": "tribe-<slug>",
            "name": "<Tribe>",
            "level": "tribe",
            "speciesCount": <int>,
            "children": [               ← genera
              {
                "id": "genus-<slug>",
                "name": "<Genus>",
                "level": "genus",
                "speciesCount": <int>,
                "children": [           ← species (leaves)
                  {
                    "id": "species-<valid_species_id>",
                    "name": "<species epithet>",
                    "level": "species",
                    "parent": "<Genus>",
                    "speciesCount": 1,
                    "scientific_name": "<Genus> <species> [<subspecies>]",
                    "species_id": "<valid_species_id>",
                    "subspecies": "<subspecies or empty string>"
                  }
                ]
              }
            ]
          }
        ]
      }
    ]

Rows where subfamily/tribe are empty are collected under a synthetic
"Unclassified" node at each level so nothing is silently dropped.

Regeneration
------------
Call ``rebuild()`` whenever valid_species.csv or described_names.csv is
republished.  The admin views and management commands already do this via the
hooks added to ``species_ref.publish_from_file`` and
``described_names_ref.publish_from_file``.

A standalone management command ``build_taxonomy_tree`` is also available for
manual / one-off generation.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage

from . import species_ref  # only for _load_all_rows(); no circular risk

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_PATH = "reference/taxonomy_tree.json"
_VERSION_KEY = "taxonomy_tree:version"          # cache key – set after write
_PLACEHOLDER = "Unclassified"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def rebuild() -> int:
    """
    Regenerate taxonomy_tree.json from the current valid_species.csv rows.
    Returns the number of leaf-level species in the tree.
    Writes the JSON to default_storage and bumps the cache version key.
    """
    rows = species_ref._load_all_rows()
    tree, species_count = _build_tree(rows)

    # Ensure the directory exists on local storage (no-op on S3/cloud)
    try:
        full_path = default_storage.path(_PATH)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
    except NotImplementedError:
        pass

    # Write JSON
    payload = json.dumps(tree, ensure_ascii=False, indent=2).encode("utf-8")
    if default_storage.exists(_PATH):
        default_storage.delete(_PATH)
    with default_storage.open(_PATH, "wb") as fh:
        fh.write(payload)

    # Bump version so get_tree() callers know to re-read
    cache.set(_VERSION_KEY, str(species_count), None)

    return species_count


def get_tree() -> list[dict] | None:
    """
    Read and return the taxonomy tree from storage.
    Returns None if the file doesn't exist yet.
    """
    if not default_storage.exists(_PATH):
        return None

    with default_storage.open(_PATH, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Tree builder (pure, no I/O – easy to unit-test)
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Very lightweight slug: lowercase, spaces → hyphens."""
    return name.strip().lower().replace(" ", "-")


def _build_tree(rows: list[dict]) -> tuple[list[dict], int]:
    """
    Build the nested tree structure from a flat list of valid_species rows.

    Returns (tree, total_species_count).
    """
    # ---------------------------------------------------------------
    # 1. Group rows into subfamily → tribe → genus → species
    # ---------------------------------------------------------------
    # Nested defaultdicts:  subfamily -> tribe -> genus -> [row, ...]
    grouped: dict[str, dict[str, dict[str, list[dict]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for row in rows:
        vid = (row.get("valid_species_id") or "").strip()
        if not vid:
            continue

        subfamily = (row.get("subfamily") or "").strip() or _PLACEHOLDER
        tribe     = (row.get("tribe")     or "").strip() or _PLACEHOLDER
        genus     = (row.get("genus")     or "").strip() or _PLACEHOLDER

        grouped[subfamily][tribe][genus].append(row)

    # ---------------------------------------------------------------
    # 2. Build the tree bottom-up, sorting alphabetically at every level
    # ---------------------------------------------------------------
    tree: list[dict] = []
    total_species = 0

    for subfamily in sorted(grouped.keys()):
        tribes_dict = grouped[subfamily]
        subfamily_node: dict = {
            "id": f"subfamily-{_slugify(subfamily)}",
            "name": subfamily,
            "level": "subfamily",
            "speciesCount": 0,      # filled after children are built
            "children": [],
        }

        for tribe in sorted(tribes_dict.keys()):
            genera_dict = tribes_dict[tribe]
            tribe_node: dict = {
                "id": f"tribe-{_slugify(tribe)}",
                "name": tribe,
                "level": "tribe",
                "speciesCount": 0,
                "children": [],
            }

            for genus in sorted(genera_dict.keys()):
                species_rows = genera_dict[genus]
                genus_node: dict = {
                    "id": f"genus-{_slugify(genus)}",
                    "name": genus,
                    "level": "genus",
                    "speciesCount": 0,
                    "children": [],
                }

                # Sort species alphabetically by species epithet, then by id
                for sp_row in sorted(species_rows, key=lambda r: ((r.get("species") or "").strip().lower(), r.get("valid_species_id", ""))):
                    vid       = sp_row["valid_species_id"].strip()
                    species   = (sp_row.get("species")   or "").strip()
                    subspecies = (sp_row.get("subspecies") or "").strip()

                    # Build the display scientific name
                    sci_parts = [genus]
                    if species:
                        sci_parts.append(species)
                    if subspecies:
                        sci_parts.append(subspecies)
                    scientific_name = " ".join(sci_parts)

                    # The leaf "name" is just the species epithet
                    # (matches the example: "name": "zeteki")
                    leaf_name = species or vid  # fallback to id if epithet missing

                    leaf: dict = {
                        "id": f"species-{vid}",
                        "name": leaf_name,
                        "level": "species",
                        "parent": genus,
                        "speciesCount": 1,
                        "scientific_name": scientific_name,
                        "species_id": vid,
                    }
                    if subspecies:
                        leaf["subspecies"] = subspecies

                    genus_node["children"].append(leaf)

                genus_node["speciesCount"] = len(genus_node["children"])
                tribe_node["children"].append(genus_node)

            tribe_node["speciesCount"] = sum(g["speciesCount"] for g in tribe_node["children"])
            subfamily_node["children"].append(tribe_node)

        subfamily_node["speciesCount"] = sum(t["speciesCount"] for t in subfamily_node["children"])
        total_species += subfamily_node["speciesCount"]
        tree.append(subfamily_node)

    return tree, total_species
