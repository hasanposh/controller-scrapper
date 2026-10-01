"""Re-scrape failed detail pages for fresh URLs, then immediately download their images."""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper import (
    async_playwright, async_scrape_detail_pages, load_progress, save_progress,
    load_data, save_data, log, setup_dirs, BASE_URL, IMAGES_DIR,
    async_download_images
)
import re

async def retry_failed_with_images():
    setup_dirs()
    progress = load_progress()
    failed_ids = progress.get("failed_ids", [])
    if not failed_ids:
        log("No failed detail IDs to retry!")
        return

    log(f"Re-scraping {len(failed_ids)} failed detail pages for fresh URLs...")
    listings = load_data()

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

        listings = await async_scrape_detail_pages(context, listings, progress, retry_failed=True)
        await browser.close()

    save_data(listings)
    progress = load_progress()
    still_failed = progress.get("failed_ids", [])
    log(f"Re-scrape complete. Still failed: {len(still_failed)}")

    # Now immediately download images for the newly scraped listings
    img_tasks = []
    for lid in failed_ids:
        if lid in still_failed:
            continue
        listing = listings.get(lid)
        if not listing:
            continue
        listing_dir = IMAGES_DIR / str(lid)
        listing_dir.mkdir(exist_ok=True)
        detail_images = listing.get("_detail_images", [])
        if not detail_images:
            img_model = listing.get("ListingImageModel", {})
            detail_images = img_model.get("ImageUrl", [])
        for i, url in enumerate(detail_images):
            ext = ".png" if "png" in url.lower() else ".jpg"
            filepath = listing_dir / f"image_{i + 1:02d}{ext}"
            if not filepath.exists():
                img_tasks.append((url, filepath, lid))

    if img_tasks:
        log(f"Immediately downloading {len(img_tasks)} fresh images...")
        await async_download_images(img_tasks)

    # Regenerate CSV
    from scraper import build_csv
    build_csv(listings)
    log("CSV/Excel regenerated. Done!")

asyncio.run(retry_failed_with_images())
