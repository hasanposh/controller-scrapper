"""Fetch 6 missing URLs + remove 13 extras + rebuild Excel."""
import asyncio
import json
import re
import sys, os
import aiohttp
from pathlib import Path
from scraper import (
    async_playwright, async_scrape_detail_pages, load_data, save_data,
    log, setup_dirs, BASE_URL, extract_detail_data, extract_detail_images_from_html,
    extract_videos_from_html, is_cloudflare_challenge, IMAGES_DIR, build_csv
)
from datetime import datetime

MISSING_URLS = {
    "260532721": "https://www.controller.com/listing/for-sale/260532721/1997-dassault-falcon-900ex-jet-aircraft",
    "260535859": "https://www.controller.com/listing/for-sale/260535859/2002-cessna-turbo-182t-skylane-piston-single-aircraft",
    "260536425": "https://www.controller.com/listing/for-sale/260536425/1974-piper-archer-piston-single-aircraft",
    "260536585": "https://www.controller.com/listing/for-sale/260536585/1999-piper-seneca-v-piston-twin-aircraft",
    "260536625": "https://www.controller.com/listing/for-sale/260536625/2004-gulfstream-g400-jet-aircraft",
    "260537579": "https://www.controller.com/listing/for-sale/260537579/2012-piper-malibu-mirage-piston-single-aircraft",
}

EXTRA_IDS = [
    "246525083", "253749341", "255915613", "257591941", "257971211",
    "258039195", "258086813", "258781515", "259068205", "259466643",
    "259506561", "259721987", "259747295",
]


async def main():
    setup_dirs()
    listings = load_data()

    # 1) Remove 13 extras
    removed = 0
    for lid in EXTRA_IDS:
        if lid in listings:
            del listings[lid]
            removed += 1
    log(f"Removed {removed} extra IDs not in updated URLs")

    # 2) Fetch 6 missing via Playwright
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

        for lid, url in MISSING_URLS.items():
            for attempt in range(3):
                try:
                    log(f"  Fetching {lid} (attempt {attempt+1})...")
                    await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                    await asyncio.sleep(2)

                    title = await page.title()
                    html = await page.content()

                    if is_cloudflare_challenge(html, title):
                        log(f"  {lid}: CF challenge, waiting 15s...")
                        await asyncio.sleep(15)
                        await page.goto(url, timeout=60000, wait_until='domcontentloaded')
                        await asyncio.sleep(3)
                        title = await page.title()
                        html = await page.content()

                    body_text = await page.evaluate('() => document.body.innerText')

                    if "404" in title.lower() or "not found" in title.lower():
                        log(f"  {lid}: 404 - not found")
                        break

                    detail = extract_detail_data(body_text)
                    detail_images = extract_detail_images_from_html(html)
                    detail_videos = extract_videos_from_html(html)

                    listing = {
                        "Id": int(lid),
                        "DetailUrl": url.replace(BASE_URL, ""),
                        "ListingTitle": title.split(" For Sale ")[0].strip() if " For Sale " in title else title,
                    }
                    listing["_detail_raw"] = {
                        "body_text": body_text,
                        "detail_url": url,
                        "scraped_at": datetime.now().isoformat(),
                    }
                    listing["_detail_parsed"] = detail
                    listing["_detail_images"] = detail_images
                    listing["_detail_videos"] = detail_videos

                    log(f"  {lid}: OK - {listing['ListingTitle'][:60]}")
                    listings[lid] = listing
                    break

                except Exception as e:
                    log(f"  {lid}: Error ({e})")
                    if attempt < 2:
                        await asyncio.sleep(5)

        await browser.close()

    # 3) Download images for 6 new
    log("Downloading images for 6 new listings...")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'Referer': 'https://www.controller.com/',
    }
    connector = aiohttp.TCPConnector(limit=15, limit_per_host=15)
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout) as session:
        for lid in MISSING_URLS:
            listing = listings.get(lid)
            if not listing:
                continue
            detail_images = listing.get("_detail_images", [])
            if not detail_images:
                continue
            listing_dir = IMAGES_DIR / str(lid)
            listing_dir.mkdir(exist_ok=True)
            for i, img_url in enumerate(detail_images):
                ext = ".png" if "png" in img_url.lower() else ".jpg"
                filepath = listing_dir / f"image_{i+1:02d}{ext}"
                if filepath.exists():
                    continue
                for attempt in range(3):
                    try:
                        async with session.get(img_url) as resp:
                            if resp.status != 200:
                                raise Exception(f"HTTP {resp.status}")
                            data = await resp.read()
                            if len(data) < 100:
                                raise Exception(f"Too small")
                            with open(filepath, 'wb') as f:
                                f.write(data)
                            break
                    except Exception:
                        if attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))
            log(f"  {lid}: {sum(1 for _ in listing_dir.iterdir())} images on disk")

    # 4) Save + rebuild
    save_data(listings)
    log(f"Total listings: {len(listings)}")
    rows = build_csv(listings)
    log(f"Excel/CSV rebuilt with {len(rows)} rows")


if __name__ == "__main__":
    asyncio.run(main())
