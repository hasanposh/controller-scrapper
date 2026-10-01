# Controller.com Aircraft Listings Scraper v2

Scrapes all aircraft listings from [controller.com](https://www.controller.com/) with full details, images, and videos.

## What It Does

- Collects all ~5,400 aircraft listings from search pages (parallel tabs for speed)
- Visits each detail page for full specs (Avionics, Engine Notes, Interior, Exterior, etc.)
- Downloads all images AND videos into organized folders
- Outputs clean CSV/Excel matching the sample format
- Tracks failed pages/listings for retry
- Saves raw JSON first - everything preserved for re-processing

## Requirements

```
pip install playwright pandas openpyxl
python -m playwright install chromium
```

## Usage

```bash
# Test run (5 listings)
python scraper.py --test 5

# Full run (all ~5,400 listings, ~1.5 hours)
python scraper.py

# Resume interrupted run
python scraper.py --resume

# Re-attempt only failed detail pages
python scraper.py --retry-failed

# Disable parallel collection (single tab)
python scraper.py --no-parallel

# Skip specific phases
python scraper.py --skip-images
python scraper.py --skip-videos
python scraper.py --skip-details
python scraper.py --skip-csv
```

## Output Structure

```
output/
  raw_listings.json            # RAW complete data (everything preserved)
  controller_listings.csv      # CSV derived from JSON
  controller_listings.xlsx     # Excel derived from JSON
  images/
    {listing_id}/
      image_01.jpg
      image_02.jpg
      video_01.mp4             # Downloaded videos
      video_01.webm
  progress.json                # Checkpoint + failed IDs tracking
```

### raw_listings.json (per listing)

```json
{
  "258165347": {
    "Id": 258165347,
    "ListingTitle": "2009 EMBRAER PHENOM 100",
    "Specs": [...],
    "ListingImageModel": {...},
    "ListingLocation": {...},

    "_detail_raw": {
      "body_text": "Full unprocessed page text...",
      "detail_url": "https://www.controller.com/listing/for-sale/...",
      "scraped_at": "2026-09-18T20:39:43"
    },
    "_detail_parsed": { "Avionics/Radios": "...", ... },
    "_detail_images": ["https://media.sandhills.com/..."],
    "_detail_videos": ["https://media.sandhills.com/streaming/video/..."]
  }
}
```

## Output Columns (42 total)

Model Name, Category, Aircraft Location, Location, Company Name, Phone Number,
Year, Manufacturer, Model, Serial Number, Registration #, Condition, Description,
Total Time, Total Landings, Airframe Notes, Engine Maintenance Program,
Engine 1 Serial Number, Engine 1 Time, Engine 1 Cycles, Engine 1 Notes,
Engine 2 Serial Number, Engine 2 Time, Engine 2 Cycles, Engine 2 Notes,
Avionics/Radios, Additional Equipment, Exterior Notes, Interior Notes,
Inspection Status, Image Link, Image Link.1 - Image Link.8,
Video Link, Video Link.1, Video Link.2

## Error Handling

- **Cloudflare blocks**: Auto-detected, waits 15s and retries up to 3x per page
- **Failed pages**: Logged in `progress.json` → `failed_pages: [12, 45]`
- **Failed detail pages**: Tracked in `progress.json` → `failed_ids: ["257725197"]`
- **Retry**: Run `python scraper.py --retry-failed` to re-attempt only failed ones

## Speed

| Phase | Time | Notes |
|-------|------|-------|
| Collection | ~2 min | 3 parallel tabs |
| Detail scraping | ~45 min | 5,400 pages |
| Image download | ~15 min | 8 parallel workers |
| Video download | ~10 min | 4 parallel workers |
| **Total** | **~1.5 hours** | |

## Notes

- Uses Playwright with Cloudflare bypass (headless=False)
- Saves raw JSON after every page (Phase 1) and every 50 listings (Phase 2)
- If anything is missed, check `raw_listings.json` - everything is preserved there
