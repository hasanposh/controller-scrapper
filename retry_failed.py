"""Retry only the 12 failed detail pages without affecting image downloads."""
import sys, os, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper import (
    async_playwright, async_scrape_detail_pages, load_progress, save_progress,
    load_data, save_data, log, setup_dirs, BASE_URL
)

async def retry_failed():
    setup_dirs()
    progress = load_progress()
    failed_ids = progress.get("failed_ids", [])
    if not failed_ids:
        log("No failed IDs to retry!")
        return

    log(f"Retrying {len(failed_ids)} failed detail pages...")
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
    log(f"Retry complete. Still failed: {len(still_failed)} IDs: {still_failed}")

asyncio.run(retry_failed())
