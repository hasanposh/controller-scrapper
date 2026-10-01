"""
Fast async downloader with 30 workers + media count validation.
Uses only high-res images (w=0&h=0).
Downloads both images AND videos via aiohttp (no ThreadPoolExecutor).

Usage:
    python fast_download.py              # Download all missing
    python fast_download.py --validate   # Check counts only
    python fast_download.py --workers 50 # Custom concurrency
"""
import asyncio
import aiohttp
import json
import re
import os
import sys
import time
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraper import load_data, log, setup_dirs, IMAGES_DIR

WORKERS_IMAGES = 20
WORKERS_VIDEOS = 3


def count_media_in_html(html):
    """Extract photo/video counts from mc-thumb-toggles HTML."""
    photos = 0
    videos = 0
    for m in re.finditer(r'mc-toggle-photos.*?<span>\((\d+)\)', html):
        photos = int(m.group(1))
    for m in re.finditer(r'mc-toggle-videos.*?<span>\((\d+)\)', html):
        videos = int(m.group(1))
    return photos, videos


def ensure_high_res(url):
    url = re.sub(r'&w=\d+', '&w=0', url)
    url = re.sub(r'&h=\d+', '&h=0', url)
    return url


def get_expected_images(listing):
    detail_images = listing.get("_detail_images", [])
    if not detail_images:
        img_model = listing.get("ListingImageModel", {})
        detail_images = img_model.get("ImageUrl", [])
    return [ensure_high_res(u) for u in detail_images]


def get_expected_videos(listing):
    return listing.get("_detail_videos", [])


def validate_counts(listings):
    total_photos_expected = 0
    total_videos_expected = 0
    listings_with_no_images = 0

    for lid, listing in listings.items():
        imgs = get_expected_images(listing)
        vids = get_expected_videos(listing)
        total_photos_expected += len(imgs)
        total_videos_expected += len(vids)
        if not imgs:
            listings_with_no_images += 1

    total_files_on_disk = 0
    for lid in listings:
        listing_dir = IMAGES_DIR / str(lid)
        if listing_dir.exists():
            total_files_on_disk += len(list(listing_dir.iterdir()))

    log(f"Listings: {len(listings)}")
    log(f"Expected images: {total_photos_expected}")
    log(f"Expected videos: {total_videos_expected}")
    log(f"Expected total: {total_photos_expected + total_videos_expected}")
    log(f"Files on disk: {total_files_on_disk}")
    log(f"Listings with no images: {listings_with_no_images}")
    return total_photos_expected, total_videos_expected, total_files_on_disk


async def download_all_fast(listings, workers_img=WORKERS_IMAGES, workers_vid=WORKERS_VIDEOS):
    img_tasks = []
    vid_tasks = []

    for lid, listing in listings.items():
        listing_dir = IMAGES_DIR / str(lid)
        listing_dir.mkdir(exist_ok=True)

        for i, url in enumerate(get_expected_images(listing)):
            ext = ".png" if "png" in url.lower() else ".jpg"
            filepath = listing_dir / f"image_{i + 1:02d}{ext}"
            if not filepath.exists():
                img_tasks.append((url, filepath, lid))

        for i, url in enumerate(get_expected_videos(listing)):
            if 'youtube' in url or 'vimeo' in url:
                continue
            ext = ".webm" if ".webm" in url.lower() else ".mp4"
            filepath = listing_dir / f"video_{i + 1:02d}{ext}"
            if not filepath.exists():
                vid_tasks.append((url, filepath, lid))

    total = len(img_tasks) + len(vid_tasks)
    if total == 0:
        log("All files present! Nothing to download.")
        return

    log(f"Missing: {len(img_tasks)} images + {len(vid_tasks)} videos = {total} total")
    log(f"Using {workers_img} image workers, {workers_vid} video workers")

    start_time = time.time()
    downloaded = 0
    failed = 0
    skipped = 0

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Referer': 'https://www.controller.com/',
        'Accept': '*/*',
    }
    connector = aiohttp.TCPConnector(limit=workers_img + workers_vid, limit_per_host=30, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=300, connect=30)

    sem_img = asyncio.Semaphore(workers_img)
    sem_vid = asyncio.Semaphore(workers_vid)

    async with aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout) as session:
        last_errors = []

        async def download_one(url, filepath, semaphore, is_video=False):
            nonlocal downloaded, failed, skipped
            async with semaphore:
                for attempt in range(3):
                    try:
                        async with session.get(url) as resp:
                            if resp.status == 404 or resp.status == 400:
                                skipped += 1
                                return
                            if resp.status != 200:
                                raise Exception(f"HTTP {resp.status}")
                            data = await resp.read()
                            min_size = 500 if is_video else 100
                            if len(data) < min_size:
                                raise Exception(f"Too small ({len(data)} bytes)")
                            with open(filepath, 'wb') as f:
                                f.write(data)
                            downloaded += 1
                            return
                    except asyncio.TimeoutError:
                        if attempt < 2:
                            await asyncio.sleep(3 * (attempt + 1))
                    except Exception as e:
                        if len(last_errors) < 10:
                            last_errors.append(f"{type(e).__name__}: {e}")
                        if attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))
                failed += 1

        batch_size = 200
        for i in range(0, len(img_tasks), batch_size):
            batch = img_tasks[i:i + batch_size]
            tasks = [download_one(url, fp, sem_img, is_video=False) for url, fp, lid in batch]
            await asyncio.gather(*tasks)
            done = min(i + batch_size, total)
            elapsed = time.time() - start_time
            rate = downloaded / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            log(f"Images: {done}/{total} | ok={downloaded} fail={failed} skip={skipped} | {rate:.1f}/s | ETA {int(eta)}s")
            if last_errors:
                log(f"  Errors: {last_errors[-3:]}")
                last_errors.clear()
            await asyncio.sleep(0.5)

        for i in range(0, len(vid_tasks), batch_size):
            batch = vid_tasks[i:i + batch_size]
            tasks = [download_one(url, fp, sem_vid, is_video=True) for url, fp, lid in batch]
            await asyncio.gather(*tasks)
            done = min(i + batch_size, total)
            elapsed = time.time() - start_time
            rate = downloaded / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            log(f"Videos: {done}/{total} | ok={downloaded} fail={failed} skip={skipped} | {rate:.1f}/s | ETA {int(eta)}s")
            if last_errors:
                log(f"  Errors: {last_errors[-3:]}")
                last_errors.clear()
            await asyncio.sleep(0.5)

    elapsed = time.time() - start_time
    log(f"Done in {elapsed:.1f}s: {downloaded} downloaded, {failed} failed, {skipped} skipped (404)")
    log(f"Average rate: {downloaded/elapsed:.1f} files/sec")


def main():
    parser = argparse.ArgumentParser(description='Fast async downloader')
    parser.add_argument('--validate', action='store_true', help='Validate media counts only')
    parser.add_argument('--workers', type=int, default=WORKERS_IMAGES, help='Image worker count')
    parser.add_argument('--video-workers', type=int, default=WORKERS_VIDEOS, help='Video worker count')
    args = parser.parse_args()

    setup_dirs()
    listings = load_data()
    log(f"Loaded {len(listings)} listings")

    if args.validate:
        validate_counts(listings)
        return

    validate_counts(listings)
    asyncio.run(download_all_fast(listings, args.workers, args.video_workers))
    validate_counts(listings)


if __name__ == "__main__":
    main()
