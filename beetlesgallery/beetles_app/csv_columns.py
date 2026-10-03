"""
Column names in uploaded CSV/Excel files: trimmed, without a byte-order mark, and with old names mapped to new ones,
so files made before a rename still work.
"""
LEGACY_COLUMNS = {
    "alternative_id": "alias_id",   # renamed: the contributor's own ID for the record (#379)
}


def modern_columns(columns):
    """The cleaned column names, with any legacy name replaced by the current one."""
    out = []
    for c in columns:
        name = str(c).strip().lstrip("﻿")
        out.append(LEGACY_COLUMNS.get(name, name))
    return out
