"""Re-scrape ALL listings with missing images for fresh URLs, download IMMEDIATELY in batches."""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from scraper import (
    async_playwright, extract_detail_images_from_html,
    extract_videos_from_html, extract_detail_data, is_cloudflare_challenge,
    load_data, save_data, log, setup_dirs,
    BASE_URL, IMAGES_DIR, async_download_images
)

BATCH_SIZE = 25

async def retry_all_missing_images():
    setup_dirs()
    listings = load_data()
    log(f"Loaded {len(listings)} listings")

    missing = []
    for lid, listing in listings.items():
        listing_dir = IMAGES_DIR / str(lid)
        detail_images = listing.get("_detail_images", [])
        if not detail_images:
            img_model = listing.get("ListingImageModel", {})
            detail_images = img_model.get("ImageUrl", [])
        has_missing = False
        for i, url in enumerate(detail_images):
            ext = ".png" if "png" in url.lower() else ".jpg"
            filepath = listing_dir / f"image_{i + 1:02d}{ext}"
            if not filepath.exists():
                has_missing = True
                break
        if has_missing:
            missing.append((lid, listing))

    log(f"Found {len(missing)} listings with missing images")
    log(f"Will re-scrape and download in batches of {BATCH_SIZE}")

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

        page = await context.new_page()
        total_rescraped = 0
        total_failed = 0
        total_downloaded = 0
        batch_img_tasks = []

        for idx, (lid, listing) in enumerate(missing):
            detail_url = BASE_URL + listing.get("DetailUrl", "")
            for attempt in range(3):
                try:
                    await page.goto(detail_url, timeout=60000, wait_until='domcontentloaded')
                    await asyncio.sleep(2)

                    title = await page.title()
                    html = await page.content()

                    if is_cloudflare_challenge(html, title):
                        log(f"  [{idx+1}/{len(missing)}] {lid}: CF challenge, waiting 15s...")
                        await asyncio.sleep(15)
                        await page.goto(detail_url, timeout=60000, wait_until='domcontentloaded')
                        await asyncio.sleep(3)
                        html = await page.content()

                    body_text = await page.evaluate('() => document.body.innerText')
                    detail = extract_detail_data(body_text)
                    detail_images = extract_detail_images_from_html(html)
                    detail_videos = extract_videos_from_html(html)

                    listing["_detail_raw"] = {
                        "body_text": body_text,
                        "detail_url": detail_url,
                    }
                    listing["_detail_parsed"] = detail
                    listing["_detail_images"] = detail_images
                    listing["_detail_videos"] = detail_videos

                    listing_dir = IMAGES_DIR / str(lid)
                    listing_dir.mkdir(exist_ok=True)
                    for i, url in enumerate(detail_images):
                        ext = ".png" if "png" in url.lower() else ".jpg"
                        filepath = listing_dir / f"image_{i + 1:02d}{ext}"
                        if not filepath.exists():
                            batch_img_tasks.append((url, filepath, lid))

                    total_rescraped += 1
                    await asyncio.sleep(1)
                    break
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(5)
                    else:
                        total_failed += 1
                        log(f"  [{idx+1}/{len(missing)}] {lid}: FAILED ({e})")

            if (idx + 1) % BATCH_SIZE == 0:
                save_data(listings)
                if batch_img_tasks:
                    log(f"  [{idx+1}/{len(missing)}] Scraped {total_rescraped}, downloading {len(batch_img_tasks)} images NOW...")
                    await async_download_images(batch_img_tasks)
                    total_downloaded += len(batch_img_tasks)
                    batch_img_tasks = []
                    log(f"  [{idx+1}/{len(missing)}] Running total: {total_downloaded} images downloaded")
                else:
                    log(f"  [{idx+1}/{len(missing)}] Scraped {total_rescraped}, no new images to download")

        await page.close()
        await browser.close()

    save_data(listings)
    log(f"Re-scrape done: {total_rescraped} ok, {total_failed} failed")

    if batch_img_tasks:
        log(f"Downloading final batch: {len(batch_img_tasks)} images...")
        await async_download_images(batch_img_tasks)
        total_downloaded += len(batch_img_tasks)

    log(f"Total images downloaded: {total_downloaded}")

    from scraper import build_csv
    build_csv(listings)
    log("All done!")

asyncio.run(retry_all_missing_images())
