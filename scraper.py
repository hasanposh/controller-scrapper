"""
Controller.com Aircraft Listings Scraper v3
============================================
Uses Playwright async API for parallel collection + robust error handling.

Usage:
    python scraper.py                  # Full run (~1.5 hours)
    python scraper.py --test 10        # Test run with 10 listings
    python scraper.py --resume         # Resume interrupted run
    python scraper.py --retry-failed   # Re-attempt only failed detail pages
    python scraper.py --no-parallel    # Single-tab collection
"""

import asyncio
import json
import re
import os
import csv
import time
import argparse
import urllib.request
import aiohttp
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from playwright.async_api import async_playwright

BASE_URL = "https://www.controller.com"
SEARCH_URL = BASE_URL + "/listings/for-sale/aircraft/all"
OUTPUT_DIR = Path("output")
IMAGES_DIR = OUTPUT_DIR / "images"
RAW_DATA_FILE = OUTPUT_DIR / "raw_listings.json"
PROGRESS_FILE = OUTPUT_DIR / "progress.json"
CSV_FILE = OUTPUT_DIR / "controller_listings.csv"
XLSX_FILE = OUTPUT_DIR / "controller_listings.xlsx"

OUTPUT_COLUMNS = [
    "Listing ID", "Model Name", "Category", "Aircraft Location", "Location",
    "Company Name", "Phone Number", "Year", "Manufacturer", "Model",
    "Serial Number", "Registration #", "Condition", "Description",
    "Total Time", "Total Landings", "Airframe Notes",
    "Engine Maintenance Program", "Engine 1 Serial Number", "Engine 1 Time",
    "Engine 1 Cycles", "Engine 1 Notes",
    "Engine 2 Serial Number", "Engine 2 Time", "Engine 2 Cycles",
    "Engine 2 Notes", "Avionics/Radios", "Additional Equipment",
    "Exterior Notes", "Interior Notes", "Inspection Status",
    "Image Link", "Image Link.1", "Image Link.2", "Image Link.3",
    "Image Link.4", "Image Link.5", "Image Link.6", "Image Link.7",
    "Image Link.8",
    "Video Link", "Video Link.1", "Video Link.2",
]


def setup_dirs():
    OUTPUT_DIR.mkdir(exist_ok=True)
    IMAGES_DIR.mkdir(exist_ok=True)


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def is_cloudflare_challenge(html, title=""):
    if "Just a moment" in title:
        return True
    if "challenge" in html.lower()[:2000]:
        return True
    if "cf-browser-verification" in html.lower()[:5000]:
        return True
    return False


def extract_listings_from_page(html):
    idx = html.find('"Listings":[')
    if idx < 0:
        idx = html.find('"Listings": [')
    if idx < 0:
        return None
    arr_start = idx + len('"Listings":')
    while arr_start < len(html) and html[arr_start] in ' \t\n\r':
        arr_start += 1
    if html[arr_start] != '[':
        return None
    depth = 0
    i = arr_start
    in_string = False
    escape = False
    while i < len(html):
        c = html[i]
        if escape:
            escape = False
        elif c == '\\':
            escape = True
        elif c == '"' and not escape:
            in_string = not in_string
        elif not in_string:
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth == 0:
                    return json.loads(html[arr_start:i + 1])
        i += 1
    return None


def extract_detail_data(text):
    lines = text.split('\n')
    lines = [l.strip() for l in lines if l.strip()]
    field_labels = {
        "Year", "Manufacturer", "Model", "Serial Number",
        "Registration #", "Condition", "Description",
        "Total Time", "Total Landings", "Aircraft Location",
        "Airframe Notes", "Engine Maintenance Program",
        "Engine 1 Serial Number", "Engine 1 Time", "Engine 1 Cycles",
        "Engine 1 Notes", "Engine 2 Serial Number", "Engine 2 Time",
        "Engine 2 Cycles", "Engine 2 Notes",
        "Avionics/Radios", "Additional Equipment",
        "Exterior Notes", "Interior Notes", "Inspection Status",
    }
    skip_headers = {
        "General", "Airframe", "Engine Program",
        "Engine 1", "Engine 2", "Avionics", "Exterior", "Interior",
    }
    result = {}
    current_label = None
    current_value_parts = []
    for line in lines:
        if line in skip_headers:
            continue
        elif line in field_labels:
            if current_label:
                val = '\n'.join(current_value_parts).strip()
                if val:
                    result[current_label] = val
            current_label = line
            current_value_parts = []
        elif current_label is not None:
            current_value_parts.append(line)
    if current_label:
        val = '\n'.join(current_value_parts).strip()
        if val:
            result[current_label] = val
    return result


def extract_detail_images_from_html(html):
    urls = []
    seen = set()
    for match in re.finditer(r'data-fullscreen="([^"]*media\.sandhills\.com/img\.axd[^"]*)"', html):
        url = match.group(1).replace('&amp;', '&')
        id_match = re.search(r'id=(\d+)', url)
        if id_match:
            img_id = id_match.group(1)
            if img_id not in seen:
                seen.add(img_id)
                urls.append(url)
    if not urls:
        for match in re.finditer(r'<img[^>]+src="([^"]*media\.sandhills\.com/img\.axd[^"]*)"', html):
            url = match.group(1).replace('&amp;', '&')
            id_match = re.search(r'id=(\d+)', url)
            if id_match:
                img_id = id_match.group(1)
                if img_id not in seen:
                    seen.add(img_id)
                    url = re.sub(r'&w=\d+', '&w=0', url)
                    url = re.sub(r'&h=\d+', '&h=0', url)
                    urls.append(url)
    return urls


def extract_videos_from_html(html):
    urls = []
    seen = set()
    for match in re.finditer(r'<(?:video|source)[^>]+src="([^"]+)"', html):
        url = match.group(1).replace('&amp;', '&')
        if url in seen:
            continue
        if any(skip in url.lower() for skip in ['icon', 'logo', 'pixel', 'track', '1x1', 'thumb', 'poster']):
            continue
        if any(ext in url.lower() for ext in ['.mp4', '.webm', '.mov', '.avi', '.m3u8']):
            seen.add(url)
            urls.append(url)
    for match in re.finditer(r'"(https?://[^"]*\.mp4[^"]*)"', html):
        url = match.group(1).replace('&amp;', '&')
        if url not in seen and not any(skip in url.lower() for skip in ['thumb', 'poster', 'icon']):
            seen.add(url)
            urls.append(url)
    for match in re.finditer(r'"(https?://media\.sandhills\.com/streaming/video/[^"]*)"', html):
        url = match.group(1).replace('&amp;', '&')
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def download_file(url, filepath, retries=3, log_prefix=""):
    is_video = any(ext in url.lower() for ext in ['.mp4', '.webm', '.mov', '.avi', '.m3u8'])
    timeout = 180 if is_video else 90
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Referer': 'https://www.controller.com/',
                'Accept': '*/*',
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if not is_video and len(data) < 100:
                    raise Exception(f"Response too small ({len(data)} bytes)")
                with open(filepath, 'wb') as f:
                    f.write(data)
                return True
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
    return False


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {"phase": "collect", "collected_ids": [], "page": 1,
            "scraped_ids": [], "failed_ids": [], "failed_pages": []}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f)


def save_data(data):
    with open(RAW_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_data():
    if RAW_DATA_FILE.exists():
        with open(RAW_DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


# =============================================================================
# PHASE 1: Async collection
# =============================================================================
async def async_fetch_page(page, page_num):
    """Fetch a search page and extract listings. Returns (listings, total, status)."""
    url = f"{SEARCH_URL}?PageSize=28&Page={page_num}"
    await page.goto(url, timeout=60000, wait_until='domcontentloaded')
    await asyncio.sleep(3)

    title = await page.title()
    html = await page.content()

    if is_cloudflare_challenge(html, title):
        return None, None, "cloudflare"

    listings = extract_listings_from_page(html)
    if not listings:
        return None, None, "empty"

    total_match = re.search(r'"ListingsCount":\s*(\d+)', html)
    total = int(total_match.group(1)) if total_match else None
    return listings, total, "ok"


async def async_collect_worker(context, page_nums, results, failed_pages):
    """One worker: opens ONE tab, processes its assigned pages sequentially."""
    page = await context.new_page()
    try:
        for page_num in page_nums:
            for attempt in range(3):
                try:
                    listings, total, status = await async_fetch_page(page, page_num)

                    if status == "cloudflare":
                        wait = 15 * (attempt + 1)
                        log(f"  Page {page_num}: CF challenge, waiting {wait}s (attempt {attempt+1}/3)")
                        await asyncio.sleep(wait)
                        continue

                    if status == "empty":
                        if attempt < 2:
                            log(f"  Page {page_num}: Empty, retrying in 5s...")
                            await asyncio.sleep(5)
                            continue
                        else:
                            if page_num not in failed_pages:
                                failed_pages.append(page_num)
                            log(f"  Page {page_num}: Empty after 3 attempts, skipping")
                            break

                    if listings:
                        for listing in listings:
                            lid = str(listing['Id'])
                            results[lid] = listing
                        log(f"  Page {page_num}: {len(listings)} listings OK")
                        await asyncio.sleep(0.5)
                        break

                except Exception as e:
                    if attempt < 2:
                        log(f"  Page {page_num}: Error ({e}), retrying...")
                        await asyncio.sleep(5)
                    else:
                        if page_num not in failed_pages:
                            failed_pages.append(page_num)
                        log(f"  Page {page_num}: Failed after 3 attempts")
    finally:
        await page.close()


async def async_scrape_listing_pages(context, progress, max_listings=0):
    """Phase 1: Collect all listing IDs using 3 parallel async tabs."""
    first_page = progress.get("page", 1)

    # Get total count from page 1
    temp_page = await context.new_page()
    total_listings = None
    try:
        _, total, _ = await async_fetch_page(temp_page, first_page)
        if total:
            total_listings = total
    except Exception:
        pass
    await temp_page.close()

    if total_listings:
        total_pages = (total_listings // 28) + 1
        log(f"Total listings: {total_listings}, pages: {total_pages}")
    else:
        total_pages = 200
        log("Could not determine total, assuming 200 pages")

    all_pages = list(range(first_page, total_pages + 1))

    # Split across 3 workers
    workers = 3
    page_chunks = [[] for _ in range(workers)]
    for i, pg in enumerate(all_pages):
        page_chunks[i % workers].append(pg)

    existing_data = load_data()
    results = dict(existing_data)
    failed_pages = list(progress.get("failed_pages", []))

    log(f"Phase 1: {len(all_pages)} pages across {workers} async tabs")

    tasks = [
        asyncio.create_task(
            async_collect_worker(context, chunk, results, failed_pages)
        )
        for chunk in page_chunks if chunk
    ]

    await asyncio.gather(*tasks)

    # Filter out pages past the end (they would be empty by design)
    if total_listings:
        max_valid_page = (total_listings // 28) + 1
        failed_pages = [p for p in failed_pages if p <= max_valid_page]

    collected_ids = set(results.keys())
    progress["phase"] = "details"
    progress["page"] = total_pages + 1
    progress["collected_ids"] = list(collected_ids)
    progress["failed_pages"] = failed_pages
    save_progress(progress)
    save_data(results)

    log(f"Phase 1 complete: {len(results)} listings, {len(failed_pages)} failed pages")
    return results


async def async_scrape_listing_single(context, progress, max_listings=0):
    """Phase 1: Single-tab collection."""
    page = await context.new_page()
    total_listings = None
    page_num = progress.get("page", 1)
    collected = {}
    collected_ids = set(progress.get("collected_ids", []))
    failed_pages = list(progress.get("failed_pages", []))

    existing_data = load_data()
    collected.update(existing_data)

    log(f"Phase 1 (single-tab): starting from page {page_num}")

    while True:
        for attempt in range(3):
            try:
                listings, total, status = await async_fetch_page(page, page_num)

                if status == "cloudflare":
                    wait = 15 * (attempt + 1)
                    log(f"Page {page_num}: CF challenge, waiting {wait}s...")
                    await asyncio.sleep(wait)
                    continue

                if status == "empty":
                    if attempt < 2:
                        log(f"Page {page_num}: Empty, retrying in 5s...")
                        await asyncio.sleep(5)
                        continue
                    else:
                        if page_num not in failed_pages:
                            failed_pages.append(page_num)
                        break

                if total and total_listings is None:
                    total_listings = total
                    log(f"Total listings: {total_listings}")

                # Success
                new_count = 0
                for listing in listings:
                    lid = str(listing['Id'])
                    if lid not in collected:
                        collected[lid] = listing
                        collected_ids.add(lid)
                        new_count += 1

                log(f"Page {page_num}: {len(listings)} listings ({new_count} new), total: {len(collected)}")
                break

            except Exception as e:
                if attempt < 2:
                    log(f"Page {page_num}: Error, retrying ({e})")
                    await asyncio.sleep(5)
                else:
                    if page_num not in failed_pages:
                        failed_pages.append(page_num)

        # Check if done
        if total_listings and page_num > (total_listings // 28 + 1):
            log("Reached last page")
            break

        if max_listings > 0 and len(collected) >= max_listings:
            log(f"Test mode: collected {len(collected)} listings")
            break

        # Save after EVERY page
        progress["page"] = page_num
        progress["collected_ids"] = list(collected_ids)
        progress["failed_pages"] = failed_pages
        save_progress(progress)
        save_data(collected)

        page_num += 1
        await asyncio.sleep(1)

    await page.close()

    progress["phase"] = "details"
    progress["page"] = page_num
    progress["collected_ids"] = list(collected_ids)
    progress["failed_pages"] = failed_pages
    save_progress(progress)
    save_data(collected)

    log(f"Phase 1 complete: {len(collected)} listings, {len(failed_pages)} failed pages")
    return collected


# =============================================================================
# PHASE 2: Async detail pages
# =============================================================================
async def async_scrape_detail_pages(context, listings, progress, retry_failed=False):
    """Phase 2: Visit each detail page for full specs."""
    scraped_ids = set(progress.get("scraped_ids", []))
    failed_ids = set(progress.get("failed_ids", []))
    all_ids = list(listings.keys())

    if retry_failed:
        remaining = [lid for lid in all_ids if lid in failed_ids and lid not in scraped_ids]
        log(f"Phase 2 (retry): {len(remaining)} failed listings to re-attempt")
    else:
        remaining = [lid for lid in all_ids if lid not in scraped_ids]
        log(f"Phase 2: {len(remaining)} detail pages to scrape")

    if not remaining:
        log("Nothing to scrape in Phase 2")
        return listings

    workers = 5
    batch_size = 50
    total_failed = 0

    for batch_start in range(0, len(remaining), batch_size):
        batch = remaining[batch_start:batch_start + batch_size]

        pages = []
        for _ in range(min(workers, len(batch))):
            pages.append(await context.new_page())

        async def scrape_one(lid, page):
            listing = listings[lid]
            detail_url = BASE_URL + listing.get("DetailUrl", "")

            for attempt in range(3):
                try:
                    await page.goto(detail_url, timeout=60000, wait_until='domcontentloaded')
                    await asyncio.sleep(2)

                    title = await page.title()
                    html = await page.content()

                    if is_cloudflare_challenge(html, title):
                        log(f"  {lid}: CF challenge, waiting 15s...")
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
                        "scraped_at": datetime.now().isoformat(),
                    }
                    listing["_detail_parsed"] = detail
                    listing["_detail_images"] = detail_images
                    listing["_detail_videos"] = detail_videos

                    scraped_ids.add(lid)
                    failed_ids.discard(lid)
                    return True

                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(3 * (attempt + 1))

            failed_ids.add(lid)
            return False

        sem = asyncio.Semaphore(workers)

        async def bounded(lid, page):
            async with sem:
                return await scrape_one(lid, page)

        tasks = [bounded(batch[i], pages[i % len(pages)]) for i in range(len(batch))]
        await asyncio.gather(*tasks)

        for p in pages:
            await p.close()

        done = batch_start + len(batch)
        total_failed = len(failed_ids)
        log(f"Detail progress: {done}/{len(remaining)} (failed: {total_failed})")

        progress["scraped_ids"] = list(scraped_ids)
        progress["failed_ids"] = list(failed_ids)
        save_progress(progress)
        save_data(listings)

        await asyncio.sleep(0.5)

    log(f"Phase 2 complete: {len(scraped_ids)} scraped, {total_failed} total failed")
    return listings


# =============================================================================
# PHASE 3: Download media (async with aiohttp)
# =============================================================================
async def async_download_images(img_tasks):
    if not img_tasks:
        return
    log(f"Downloading {len(img_tasks)} images (async, 30 workers)...")
    downloaded = 0
    failed = 0
    sem = asyncio.Semaphore(30)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
    }
    connector = aiohttp.TCPConnector(limit=30, limit_per_host=30, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(headers=headers, connector=connector, timeout=timeout) as session:
        async def download_one(url, filepath):
            nonlocal downloaded, failed
            async with sem:
                for attempt in range(3):
                    try:
                        async with session.get(url) as resp:
                            if resp.status != 200:
                                raise Exception(f"HTTP {resp.status}")
                            data = await resp.read()
                            if len(data) < 100:
                                raise ResponseTooSmall(len(data))
                            with open(filepath, 'wb') as f:
                                f.write(data)
                            downloaded += 1
                            return
                    except Exception:
                        if attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))
                failed += 1

        class ResponseTooSmall(Exception):
            pass

        tasks = [download_one(url, fp) for url, fp, lid in img_tasks]
        total = len(tasks)
        done = 0
        batch_size = 100
        for i in range(0, total, batch_size):
            batch = tasks[i:i+batch_size]
            await asyncio.gather(*batch)
            done = min(i + batch_size, total)
            log(f"Images: {done}/{total} ({downloaded} ok, {failed} failed)")
            await asyncio.sleep(1)

    log(f"Images: {downloaded} downloaded, {failed} failed")


async def download_all_media(listings):
    img_tasks = []
    vid_tasks = []

    for lid, listing in listings.items():
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

        detail_videos = listing.get("_detail_videos", [])
        for i, url in enumerate(detail_videos):
            if 'youtube' in url or 'vimeo' in url:
                continue
            ext = ".webm" if ".webm" in url.lower() else ".mp4"
            filepath = listing_dir / f"video_{i + 1:02d}{ext}"
            if not filepath.exists():
                vid_tasks.append((url, filepath, lid))

    if img_tasks:
        await async_download_images(img_tasks)

    if vid_tasks:
        log(f"Downloading {len(vid_tasks)} videos (async, 5 workers)...")
        downloaded = 0
        failed = 0
        sem = asyncio.Semaphore(5)
        connector = aiohttp.TCPConnector(limit=5, limit_per_host=5, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        async with aiohttp.ClientSession(headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Referer': 'https://www.controller.com/',
            'Accept': '*/*',
        }, connector=connector, timeout=timeout) as session:
            async def download_video(url, filepath):
                nonlocal downloaded, failed
                async with sem:
                    for attempt in range(3):
                        try:
                            async with session.get(url) as resp:
                                if resp.status != 200:
                                    raise Exception(f"HTTP {resp.status}")
                                data = await resp.read()
                                if len(data) < 500:
                                    raise Exception(f"Too small ({len(data)} bytes)")
                                with open(filepath, 'wb') as f:
                                    f.write(data)
                                downloaded += 1
                                return
                        except Exception:
                            if attempt < 2:
                                await asyncio.sleep(3 * (attempt + 1))
                    failed += 1

            vid_batch = [download_video(url, fp) for url, fp, lid in vid_tasks]
            for i in range(0, len(vid_batch), 10):
                await asyncio.gather(*vid_batch[i:i+10])
        log(f"Videos: {downloaded} downloaded, {failed} failed")


# =============================================================================
# PHASE 4: Build CSV/Excel
# =============================================================================
def build_csv(listings):
    rows = []
    for lid, listing in listings.items():
        detail = listing.get("_detail_parsed", {})
        detail_images = listing.get("_detail_images", [])
        detail_videos = listing.get("_detail_videos", [])

        if not detail_images:
            img_model = listing.get("ListingImageModel", {})
            detail_images = img_model.get("ImageUrl", [])

        norm_images = []
        for url in detail_images:
            url = re.sub(r'&w=\d+', '&w=0', url)
            url = re.sub(r'&h=\d+', '&h=0', url)
            norm_images.append(url)

        loc = listing.get("ListingLocation", {})
        aircraft_loc = loc.get("FormattedLocation", "")
        dealer_loc = listing.get("DealerLocation", "")
        specs = {s["Key"]: s["Value"] for s in listing.get("Specs", [])}

        row = {
            "Listing ID": lid,
            "Model Name": listing.get("ListingTitle", ""),
            "Category": listing.get("CategoryName", ""),
            "Aircraft Location": aircraft_loc,
            "Location": dealer_loc,
            "Company Name": listing.get("Dealer", ""),
            "Phone Number": listing.get("RetailPhoneNumber", ""),
            "Year": listing.get("Year", ""),
            "Manufacturer": listing.get("ManufacturerName", ""),
            "Model": listing.get("Model", ""),
            "Serial Number": listing.get("SerialNumber", ""),
            "Registration #": detail.get("Registration #", specs.get("Registration #", "")),
            "Condition": listing.get("Condition", ""),
            "Description": listing.get("Description", ""),
            "Total Time": detail.get("Total Time", specs.get("Total Time", "")),
            "Total Landings": detail.get("Total Landings", specs.get("Total Landings", "")),
            "Airframe Notes": detail.get("Airframe Notes", ""),
            "Engine Maintenance Program": detail.get("Engine Maintenance Program",
                                                      specs.get("Engine Maintenance Program", "")),
            "Engine 1 Serial Number": detail.get("Engine 1 Serial Number", ""),
            "Engine 1 Time": detail.get("Engine 1 Time", specs.get("Engine 1 Time", "")),
            "Engine 1 Cycles": detail.get("Engine 1 Cycles", specs.get("Engine 1 Cycles", "")),
            "Engine 1 Notes": detail.get("Engine 1 Notes", ""),
            "Engine 2 Serial Number": detail.get("Engine 2 Serial Number", ""),
            "Engine 2 Time": detail.get("Engine 2 Time", specs.get("Engine 2 Time", "")),
            "Engine 2 Cycles": detail.get("Engine 2 Cycles", specs.get("Engine 2 Cycles", "")),
            "Engine 2 Notes": detail.get("Engine 2 Notes", ""),
            "Avionics/Radios": detail.get("Avionics/Radios", ""),
            "Additional Equipment": detail.get("Additional Equipment", ""),
            "Exterior Notes": detail.get("Exterior Notes", ""),
            "Interior Notes": detail.get("Interior Notes", ""),
            "Inspection Status": detail.get("Inspection Status", ""),
        }
        for i in range(9):
            col = "Image Link" if i == 0 else f"Image Link.{i}"
            row[col] = norm_images[i] if i < len(norm_images) else ""
        for i in range(3):
            col = "Video Link" if i == 0 else f"Video Link.{i}"
            row[col] = detail_videos[i] if i < len(detail_videos) else ""
        rows.append(row)

    with open(CSV_FILE, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    log(f"CSV saved: {CSV_FILE} ({len(rows)} rows)")

    try:
        import pandas as pd
        df = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
        df.to_excel(XLSX_FILE, index=False, engine='openpyxl')
        log(f"Excel saved: {XLSX_FILE}")
    except ImportError:
        log("pandas not available, Excel not generated")

    log(f"Full raw data in: {RAW_DATA_FILE}")
    return rows


# =============================================================================
# MAIN
# =============================================================================
async def async_main(args):
    setup_dirs()

    if args.resume or args.retry_failed:
        progress = load_progress()
        if args.retry_failed:
            progress["phase"] = "details"
    else:
        progress = {"phase": "collect", "collected_ids": [], "page": 1,
                     "scraped_ids": [], "failed_ids": [], "failed_pages": []}

    log("Starting Controller.com scraper v3 (async)")
    if args.test:
        log(f"Test mode: {args.test} listings")
    if args.retry_failed:
        log(f"Retry mode: {len(progress.get('failed_ids', []))} failed IDs")

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

        # CF clearance
        init_page = await context.new_page()
        log("Getting Cloudflare clearance...")
        await init_page.goto(BASE_URL, timeout=60000, wait_until='domcontentloaded')
        await asyncio.sleep(5)
        log(f"CF clearance done, title: {await init_page.title()}")
        await init_page.close()

        # Phase 1
        if progress["phase"] == "collect":
            if args.no_parallel or args.test > 0:
                listings = await async_scrape_listing_single(context, progress, max_listings=args.test)
            else:
                listings = await async_scrape_listing_pages(context, progress, max_listings=args.test)
        else:
            listings = load_data()

        if args.test > 0:
            test_ids = list(listings.keys())[:args.test]
            listings = {k: listings[k] for k in test_ids}
            log(f"Test mode: using {len(listings)} listings")

        # Phase 2
        if not args.skip_details:
            listings = await async_scrape_detail_pages(context, listings, progress,
                                                       retry_failed=args.retry_failed)

        await browser.close()

    # Phase 3
    if not (args.skip_images and args.skip_videos):
        await download_all_media(listings)

    # Phase 4
    if not args.skip_csv:
        build_csv(listings)

    # Summary
    progress = load_progress()
    failed = progress.get("failed_ids", [])
    failed_pages = progress.get("failed_pages", [])
    if failed:
        log(f"WARNING: {len(failed)} failed detail pages. Run: python scraper.py --retry-failed")
    if failed_pages:
        log(f"WARNING: {len(failed_pages)} failed collection pages: {failed_pages}")

    log("Done!")


def main():
    parser = argparse.ArgumentParser(description='Controller.com Scraper v3')
    parser.add_argument('--test', type=int, default=0, help='Test mode: scrape N listings')
    parser.add_argument('--resume', action='store_true', help='Resume from last checkpoint')
    parser.add_argument('--retry-failed', action='store_true', help='Re-attempt only failed pages')
    parser.add_argument('--no-parallel', action='store_true', help='Single-tab collection')
    parser.add_argument('--skip-details', action='store_true', help='Skip detail scraping')
    parser.add_argument('--skip-images', action='store_true', help='Skip image download')
    parser.add_argument('--skip-videos', action='store_true', help='Skip video download')
    parser.add_argument('--skip-csv', action='store_true', help='Skip CSV generation')
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
