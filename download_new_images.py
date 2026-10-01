"""Download images for the 17 new listings + build Excel."""
import asyncio
import json
import os
import re
import sys
import aiohttp
from pathlib import Path
from scraper import load_data, save_data, log, setup_dirs, IMAGES_DIR

NEW_IDS = [
    "247637619", "254785901", "260446243",
    "260516599", "260517759", "260518081", "260518643", "260519003",
    "260519631", "260520199", "260520783", "260521093", "260521565",
    "260523105", "260524507", "260526039", "260528521",
]


async def download_images_for_new():
    setup_dirs()
    listings = load_data()

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Referer': 'https://www.controller.com/',
    }
    connector = aiohttp.TCPConnector(limit=15, limit_per_host=15)
    timeout = aiohttp.ClientTimeout(total=60)

    downloaded = 0
    skipped = 0
    failed = 0

    async with aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout) as session:
        for lid in NEW_IDS:
            listing = listings.get(lid)
            if not listing:
                log(f"  {lid}: NOT in data, skipping")
                continue

            detail_images = listing.get("_detail_images", [])
            if not detail_images:
                img_model = listing.get("ListingImageModel", {})
                detail_images = img_model.get("ImageUrl", [])

            if not detail_images:
                log(f"  {lid}: No images found")
                continue

            listing_dir = IMAGES_DIR / str(lid)
            listing_dir.mkdir(exist_ok=True)

            for i, url in enumerate(detail_images):
                ext = ".png" if "png" in url.lower() else ".jpg"
                filepath = listing_dir / f"image_{i + 1:02d}{ext}"
                if filepath.exists():
                    skipped += 1
                    continue

                for attempt in range(3):
                    try:
                        async with session.get(url) as resp:
                            if resp.status != 200:
                                raise Exception(f"HTTP {resp.status}")
                            data = await resp.read()
                            if len(data) < 100:
                                raise Exception(f"Too small ({len(data)} bytes)")
                            with open(filepath, 'wb') as f:
                                f.write(data)
                            downloaded += 1
                            break
                    except Exception:
                        if attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))
                        else:
                            failed += 1

            title = listing.get("ListingTitle", listing.get("_detail_parsed", {}).get("Year", "?"))
            log(f"  {lid}: {len(detail_images)} images ({sum(1 for p in listing_dir.iterdir())} on disk) - {title[:50]}")

    log(f"\nImages: {downloaded} downloaded, {skipped} existed, {failed} failed")


if __name__ == "__main__":
    asyncio.run(download_images_for_new())
