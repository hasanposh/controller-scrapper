"""Re-check 403 URLs using Playwright to bypass Cloudflare."""
import asyncio
import json
import re
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper import async_playwright, load_data, save_data, log, setup_dirs, BASE_URL, extract_detail_data, extract_detail_images_from_html, extract_videos_from_html, is_cloudflare_challenge
from datetime import datetime

IDS_TO_CHECK = [
    "254785901", "260446243",
    "260516599", "260517759", "260518081", "260518643", "260519003",
    "260519631", "260520783", "260521093", "260521565",
    "260523105", "260524507", "260526039", "260528521",
]

URLS = {
    "247637619": "https://www.controller.com/listing/for-sale/247637619/1992-gulfstream-givsp-jet-aircraft",
    "254785901": "https://www.controller.com/listing/for-sale/254785901/2024-piper-m700-turboprop-aircraft",
    "260446243": "https://www.controller.com/listing/for-sale/260446243/redbird-td2-flight-simulators",
    "260516599": "https://www.controller.com/listing/for-sale/260516599/2018-cubcrafters-xcub-piston-single-aircraft",
    "260517759": "https://www.controller.com/listing/for-sale/260517759/2005-cirrus-sr22-g2-piston-single-aircraft",
    "260518081": "https://www.controller.com/listing/for-sale/260518081/1992-dassault-falcon-50-4-jet-aircraft",
    "260518643": "https://www.controller.com/listing/for-sale/260518643/1984-cessna-421c-piston-twin-aircraft",
    "260519003": "https://www.controller.com/listing/for-sale/260519003/2020-robinson-r66-turbine-marine-turbine-helicopters",
    "260519631": "https://www.controller.com/listing/for-sale/260519631/1993-beechcraft-a36-bonanza-piston-single-aircraft",
    "260520199": "https://www.controller.com/listing/for-sale/260520199/1969-cessna-172-skyhawk-piston-single-aircraft",
    "260520783": "https://www.controller.com/listing/for-sale/260520783/1982-beechcraft-king-air-b200-turboprop-aircraft",
    "260521093": "https://www.controller.com/listing/for-sale/260521093/1976-piper-cherokee-140-piston-single-aircraft",
    "260521565": "https://www.controller.com/listing/for-sale/260521565/1980-cessna-414a-piston-twin-aircraft",
    "260523105": "https://www.controller.com/listing/for-sale/260523105/1972-piper-seneca-piston-twin-aircraft",
    "260524507": "https://www.controller.com/listing/for-sale/260524507/2008-cirrus-sr22-g3-turbo-piston-single-aircraft",
    "260526039": "https://www.controller.com/listing/for-sale/260526039/2019-cirrus-sr22-g6-piston-single-aircraft",
    "260528521": "https://www.controller.com/listing/for-sale/260528521/2003-eurocopter-as350b-3-turbine-helicopters",
}

ALL_IDS = sorted(URLS.keys())


async def main():
    setup_dirs()
    listings = load_data()
    new_data = {}

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
        found = 0
        not_found = 0
        blocked = 0

        for lid in ALL_IDS:
            url = URLS[lid]
            for attempt in range(3):
                try:
                    log(f"  Checking {lid} (attempt {attempt+1})...")
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
                    page_url = page.url

                    if "404" in title.lower() or "not found" in title.lower() or "page not found" in body_text.lower()[:500]:
                        log(f"  {lid}: 404 - REMOVED")
                        not_found += 1
                        break

                    if "just a moment" in title.lower():
                        log(f"  {lid}: Still blocked by CF")
                        blocked += 1
                        break

                    # Page loaded - check for listing data
                    detail = extract_detail_data(body_text)
                    detail_images = extract_detail_images_from_html(html)
                    detail_videos = extract_videos_from_html(html)

                    # Build listing object
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

                    # Try to get more data from page
                    try:
                        spec_data = await page.evaluate('''() => {
                            try {
                                const el = document.querySelector('[data-listing-id]');
                                const jsonLd = document.querySelectorAll('script[type="application/ld+json"]');
                                let specs = {};
                                jsonLd.forEach(s => { try { Object.assign(specs, JSON.parse(s.textContent)); } catch(e) {} });
                                return specs;
                            } catch(e) { return {}; }
                        }''')
                        if spec_data:
                            for k, v in spec_data.items():
                                if k not in ("_detail_raw", "_detail_parsed", "_detail_images", "_detail_videos"):
                                    listing[k] = v
                    except Exception:
                        pass

                    log(f"  {lid}: FOUND - {listing.get('ListingTitle', title)[:60]}")
                    new_data[lid] = listing
                    found += 1
                    break

                except Exception as e:
                    log(f"  {lid}: Error ({e})")
                    if attempt < 2:
                        await asyncio.sleep(5)

        await browser.close()

    log(f"\nResults: {found} found, {not_found} removed, {blocked} still blocked")

    if new_data:
        listings.update(new_data)
        save_data(listings)
        log(f"Added {len(new_data)} new listings. Total: {len(listings)}")

        with open('output/rechecked_ids.json', 'w') as f:
            json.dump(list(new_data.keys()), f)
        log("Saved rechecked_ids.json")

    return found, not_found, blocked


if __name__ == "__main__":
    asyncio.run(main())
