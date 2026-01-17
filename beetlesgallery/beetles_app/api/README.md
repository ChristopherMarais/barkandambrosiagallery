# Beetles Gallery REST API

A read-only REST API for accessing beetle specimen data, taxonomy information, and associated images.

## Base URL

```
http://localhost:8000/api/v1/
```

In production, replace `localhost:8000` with your actual domain.

---

## Endpoints Overview

### 1. Species (Taxonomy Reference)

- **GET** `/api/v1/species/` - List all species from taxonomy CSV
- **GET** `/api/v1/species/?subfamily=Platypodinae` - Filter by subfamily
- **GET** `/api/v1/species/?genus=Austroplatypus` - Filter by genus
- **GET** `/api/v1/species/?tribe=Platypodini` - Filter by tribe

### 2. Beetles (Specimen Records)

- **GET** `/api/v1/beetles/` - List all beetle specimens (with images only)
- **GET** `/api/v1/beetles/?subfamily=Platypodinae` - Filter by subfamily
- **GET** `/api/v1/beetles/{uuid}/` - Get single beetle specimen

### 3. Image Assets

- **GET** `/api/v1/image-assets/` - List all image files
- **GET** `/api/v1/image-assets/{uuid}/` - Get single image metadata
- **GET** `/api/v1/image-assets/{uuid}/download/` - Download original image file

---

## Detailed Documentation

### 1. Species Endpoint

**Purpose**: Query the taxonomy reference CSV (`valid_species.csv`) to get all species matching your filters. Returns complete taxonomy data for each species, with associated images included when available.

#### Features:

- Returns **ALL species** from the CSV (whether they have images or not)
- Includes **all 12 taxonomy fields** from the reference file
- For species with images, includes full specimen and image data in the `images` array
- Species without images have `"images": []` (empty array)

#### Supported Filters:

- `subfamily` - e.g., `Platypodinae`, `Scolytinae`
- `genus` - e.g., `Austroplatypus`, `Dendroctonus`
- `tribe` - e.g., `Platypodini`
- `species` - e.g., `incompertus`

Filters can be combined: `?subfamily=Platypodinae&genus=Austroplatypus`

#### Examples:

**Get all Platypodinae species:**

```bash
GET /api/v1/species/?subfamily=Platypodinae
```

**Get a specific genus:**

```bash
GET /api/v1/species/?genus=Austroplatypus
```

#### Response Structure:

```json
[
  {
    "valid_species_id": "4093",
    "scientificName": "Austroplatypus incompertus",
    "scientificNameAuthority": "(Schedl, 1968)",
    "subfamily": "Platypodinae",
    "tribe": "Platypodini",
    "subtribe": "",
    "genus": "Austroplatypus",
    "species": "incompertus",
    "subspecies": "",
    "authority": "Schedl",
    "authorityYear": "1968",
    "originalGenus": "Platypus",
    "images": [
      {
        "id": "f24e1be6-fac9-4b38-b5d1-fd5eaff4ce5e",
        "aspect": "dorsal",
        "depicts_specimen": "museumsvictoria-018888",
        "depicts_valid_name_id": "4093",
        "collection_country": null,
        "specimen_sex": null,
        "specimen_type_status": "syntype of Platypus incompertus",
        "image_asset": {
          "id": "81d4f037-aebe-4223-9fc1-202a4451d273",
          "image_sha256": "4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342",
          "photographer": "Lucinda Gibson",
          "image_institution": "Museums Victoria, Melbourne, Victoria, Australia",
          "photo_usage_statement": "Copyright Museums Victoria / CC BY...",
          "image_file": "/media/originals/4a/d7/4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342.jpg",
          "thumb_small": "/media/thumbnails/4a/d7/4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342_96.webp",
          "image_width": 3000,
          "image_height": 2000,
          "image_size_bytes": 626688,
          "created_at": "2026-01-14T22:54:13.241637Z"
        }
      },
      {
        "id": "53e1177e-632c-4c48-8663-fcec331201d7",
        "aspect": "lateral",
        "image_asset": { ... }
      }
    ]
  },
  {
    "valid_species_id": "3",
    "scientificName": "Mecopelmus zeteki",
    "subfamily": "Platypodinae",
    "genus": "Mecopelmus",
    "images": []
  }
]
```

#### Response Fields:

**Taxonomy fields (from CSV):**

- `valid_species_id` - Unique species identifier
- `scientificName` - Full scientific name
- `scientificNameAuthority` - Authority citation
- `subfamily` - Subfamily classification
- `tribe` - Tribe classification
- `subtribe` - Subtribe (if applicable)
- `genus` - Genus name
- `species` - Species epithet
- `subspecies` - Subspecies (if applicable)
- `authority` - Author name
- `authorityYear` - Year described
- `originalGenus` - Original genus name

**Images array:**

- Empty `[]` if no images
- Contains beetle specimen objects (see Beetles endpoint below) with nested image data

---

### 2. Beetles Endpoint

**Purpose**: Get beetle specimen records from the database. Only returns specimens that have associated images.

#### Features:

- Returns specimen metadata (collection location, sex, type status, etc.)
- Includes taxonomy data looked up from CSV (subfamily, genus, tribe, scientific name)
- Always includes full nested `image_asset` object with image metadata

#### Supported Filters:

- `subfamily` - Filter by subfamily (looks up IDs from CSV)

#### Examples:

**Get all beetles:**

```bash
GET /api/v1/beetles/
```

**Filter by subfamily:**

```bash
GET /api/v1/beetles/?subfamily=Platypodinae
```

**Get single beetle:**

```bash
GET /api/v1/beetles/f24e1be6-fac9-4b38-b5d1-fd5eaff4ce5e/
```

#### Response Structure:

```json
[
  {
    "id": "f24e1be6-fac9-4b38-b5d1-fd5eaff4ce5e",
    "subfamily": "Platypodinae",
    "genus": "Austroplatypus",
    "tribe": "Platypodini",
    "scientific_name": "Austroplatypus incompertus",
    "aspect": "dorsal",
    "depicts_specimen": "museumsvictoria-018888",
    "depicts_valid_name_id": "4093",
    "depicts_described_name_id": "11239",
    "depicts_name_verbatim": "Platypus incompertus",
    "collection_country": null,
    "collection_stateProvince": null,
    "specimen_sex": null,
    "specimen_type_status": "syntype of Platypus incompertus",
    "specimen_notes": null,
    "alternative_id": "lrXdUvwycEGw",
    "last_updated_at": "2026-01-14T22:54:13.243392Z",
    "update_notes": null,
    "last_updated_by": null,
    "image_asset": {
      "id": "81d4f037-aebe-4223-9fc1-202a4451d273",
      "image_sha256": "4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342",
      "full_path_at_import": "VictoriaMuseum\\Platypus incompertus-syntype-1018888\\...",
      "photographer": "Lucinda Gibson",
      "image_institution": "Museums Victoria, Melbourne, Victoria, Australia",
      "image_email": null,
      "photo_usage_statement": "Copyright Museums Victoria / CC BY...",
      "image_date_taken": null,
      "image_notes": null,
      "image_has_multiple_individuals": false,
      "resolution_in_ppmm": null,
      "image_size_bytes": 626688,
      "image_file": "/media/originals/4a/d7/4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342.jpg",
      "thumb_small": "/media/thumbnails/4a/d7/4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342_96.webp",
      "image_width": 3000,
      "image_height": 2000,
      "thumb_width": 96,
      "thumb_height": 96,
      "created_at": "2026-01-14T22:54:13.241637Z",
      "updated_at": "2026-01-14T22:54:13.241649Z"
    }
  }
]
```

#### Response Fields:

**Specimen fields:**

- `id` - UUID of specimen record
- `depicts_specimen` - Specimen identifier
- `depicts_valid_name_id` - Species ID (links to CSV)
- `depicts_described_name_id` - Original description ID
- `depicts_name_verbatim` - Name as written on label
- `aspect` - View of specimen (dorsal, lateral, ventral, label, etc.)
- `collection_country` - Country where collected
- `collection_stateProvince` - State/province
- `specimen_sex` - Sex (male, female, unknown)
- `specimen_type_status` - Type status (holotype, paratype, syntype, etc.)
- `specimen_notes` - Additional notes
- `alternative_id` - Alternative identifier

**Taxonomy fields (from CSV lookup):**

- `subfamily`, `genus`, `tribe`, `scientific_name`

**Image asset:**

- Nested object with full image metadata (see Image Assets section)

---

### 3. Image Assets Endpoint

**Purpose**: Get metadata about physical image files and download original images.

#### Examples:

**List all images:**

```bash
GET /api/v1/image-assets/
```

**Get single image metadata:**

```bash
GET /api/v1/image-assets/81d4f037-aebe-4223-9fc1-202a4451d273/
```

**Download original image file:**

```bash
GET /api/v1/image-assets/81d4f037-aebe-4223-9fc1-202a4451d273/download/
```

#### Response Structure:

```json
[
  {
    "id": "81d4f037-aebe-4223-9fc1-202a4451d273",
    "image_sha256": "4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342",
    "full_path_at_import": "VictoriaMuseum\\Platypus incompertus-syntype-1018888\\...",
    "image_institution": "Museums Victoria, Melbourne, Victoria, Australia",
    "photographer": "Lucinda Gibson",
    "image_email": null,
    "photo_usage_statement": "Copyright Museums Victoria / CC BY (Attribution 4.0 International)",
    "image_date_taken": null,
    "image_notes": null,
    "image_has_multiple_individuals": false,
    "resolution_in_ppmm": null,
    "image_size_bytes": 626688,
    "image_file": "/media/originals/4a/d7/4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342.jpg",
    "thumb_small": "/media/thumbnails/4a/d7/4ad71a139f6d25301f9b8f7d434dee867464d1a4e26e9e26d401593be2693342_96.webp",
    "image_width": 3000,
    "image_height": 2000,
    "thumb_width": 96,
    "thumb_height": 96,
    "created_at": "2026-01-14T22:54:13.241637Z",
    "updated_at": "2026-01-14T22:54:13.241649Z"
  }
]
```

#### Response Fields:

**File identification:**

- `id` - UUID of image asset
- `image_sha256` - SHA256 hash (for deduplication)
- `full_path_at_import` - Original file path

**Copyright/Provenance:**

- `photographer` - Photographer name
- `image_institution` - Institution owning the image
- `image_email` - Contact email
- `photo_usage_statement` - Copyright/license terms
- `image_date_taken` - Date photo was taken
- `image_notes` - Additional notes

**Technical metadata:**

- `image_width`, `image_height` - Dimensions in pixels
- `image_size_bytes` - File size in bytes
- `resolution_in_ppmm` - Resolution (pixels per millimeter)
- `image_has_multiple_individuals` - Multiple beetles in one image?

**File paths:**

- `image_file` - Path to original image (JPEG/TIFF)
- `thumb_small` - Path to 96px thumbnail (WebP)
- `thumb_width`, `thumb_height` - Thumbnail dimensions

**Timestamps:**

- `created_at` - When record was created
- `updated_at` - Last modification time

---

## Common Use Cases

### Get all species in a subfamily with their images

```bash
GET /api/v1/species/?subfamily=Platypodinae
```

Returns all Platypodinae species (1,326 total). Species with images will have data in the `images` array, species without images will have `images: []`.

### Get only specimens with images for a subfamily

```bash
GET /api/v1/beetles/?subfamily=Platypodinae
```

Returns only beetle specimens that have associated images.

### Download an image file

```bash
GET /api/v1/image-assets/81d4f037-aebe-4223-9fc1-202a4451d273/download/
```

Returns the original image file as a binary stream (JPEG or TIFF).

### Search for a specific genus

```bash
GET /api/v1/species/?genus=Dendroctonus
```
