"""
Re-scrape detail pages for listings with broken sz=Cover images.
Fixes URLs by using data-fullscreen attributes (correct checksums).

Usage:
    python fix_cover_urls.py              # Fix all broken listings
    python fix_cover_urls.py --test 10    # Test with 10 listings
"""
import asyncio
import json
import re
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraper import (
    async_playwright, extract_detail_images_from_html, extract_videos_from_html,
    load_data, save_data, load_progress, save_progress, log, setup_dirs,
    BASE_URL, IMAGES_DIR
)
from pathlib import Path


def find_broken_listings(listings):
    broken = []
    for lid, listing in listings.items():
        detail_images = listing.get('_detail_images', [])
        cover_count = sum(1 for u in detail_images if 'sz=Cover' in u)
        if cover_count > 0:
            broken.append(lid)
    return broken


async def re_scrape_listings(broken_ids, listings, progress):
    workers = 5
    fixed = 0
    failed_ids = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080}
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        init_page = await context.new_page()
        log("Getting Cloudflare clearance...")
        await init_page.goto(BASE_URL, timeout=60000, wait_until='domcontentloaded')
        await asyncio.sleep(5)
        log(f"CF clearance done, title: {await init_page.title()}")
        await init_page.close()

        for i in range(0, len(broken_ids), workers):
            batch = broken_ids[i:i + workers]
            pages = [await context.new_page() for _ in batch]

            async def scrape_one(lid, page):
                listing = listings[lid]
                detail_url = BASE_URL + listing.get("DetailUrl", "")
                for attempt in range(3):
                    try:
                        await page.goto(detail_url, timeout=60000, wait_until='domcontentloaded')
                        await asyncio.sleep(2)

                        title = await page.title()
                        html = await page.content()

                        if "Just a moment" in title or "challenge" in html.lower()[:2000]:
                            log(f"  {lid}: CF challenge, waiting...")
                            await asyncio.sleep(15)
                            await page.goto(detail_url, timeout=60000, wait_until='domcontentloaded')
                            await asyncio.sleep(3)
                            html = await page.content()

                        detail_images = extract_detail_images_from_html(html)
                        detail_videos = extract_videos_from_html(html)

                        old_images = listing.get('_detail_images', [])
                        listing['_detail_images'] = detail_images
                        listing['_detail_videos'] = detail_videos

                        has_cover = any('sz=Cover' in u for u in detail_images)
                        log(f"  {lid}: {len(old_images)} -> {len(detail_images)} images (cover={has_cover})")
                        return True
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(5)
                        else:
                            log(f"  {lid}: FAILED: {e}")
                return False

            tasks = [scrape_one(batch[j], pages[j]) for j in range(len(batch))]
            results = await asyncio.gather(*tasks)

            for j, r in enumerate(results):
                if r:
                    fixed += 1
                else:
                    failed_ids.append(batch[j])

            for pg in pages:
                await pg.close()

            done = min(i + workers, len(broken_ids))
            log(f"Progress: {done}/{len(broken_ids)} (fixed={fixed} failed={len(failed_ids)})")
            save_data(listings)
            await asyncio.sleep(0.5)

        await browser.close()

    log(f"Done: {fixed} fixed, {len(failed_ids)} failed")
    if failed_ids:
        log(f"Failed IDs: {failed_ids[:20]}")
    return listings


def main():
    parser = argparse.ArgumentParser(description='Fix sz=Cover URLs')
    parser.add_argument('--test', type=int, default=0, help='Test with N listings')
    args = parser.parse_args()

    setup_dirs()
    listings = load_data()
    log(f"Loaded {len(listings)} listings")

    broken = find_broken_listings(listings)
    log(f"Found {len(broken)} listings with sz=Cover URLs")

    if not broken:
        log("Nothing to fix!")
        return

    if args.test > 0:
        broken = broken[:args.test]
        log(f"Test mode: {len(broken)} listings")

    progress = load_progress()
    asyncio.run(re_scrape_listings(broken, listings, progress))
    save_data(listings)
    log("Data saved. Run fast_download.py to download fixed images.")


if __name__ == "__main__":
    main()
