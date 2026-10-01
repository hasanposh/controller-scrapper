"""Retry downloading only images that are missing from disk (async, fast)."""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from scraper import load_data, log, setup_dirs, IMAGES_DIR, async_download_images

def retry_images():
    setup_dirs()
    listings = load_data()
    log(f"Loaded {len(listings)} listings, checking for missing images...")

    tasks = []
    for lid, listing in listings.items():
        listing_dir = IMAGES_DIR / str(lid)
        detail_images = listing.get("_detail_images", [])
        if not detail_images:
            img_model = listing.get("ListingImageModel", {})
            detail_images = img_model.get("ImageUrl", [])

        for i, url in enumerate(detail_images):
            ext = ".png" if "png" in url.lower() else ".jpg"
            filepath = listing_dir / f"image_{i + 1:02d}{ext}"
            if not filepath.exists():
                tasks.append((url, filepath, lid))

    if not tasks:
        log("All images present! Nothing to retry.")
        return

    log(f"Found {len(tasks)} missing images")
    asyncio.run(async_download_images(tasks))

if __name__ == "__main__":
    retry_images()
