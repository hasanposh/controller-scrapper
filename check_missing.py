"""Check if the 17 missing URLs still exist on the site."""
import asyncio
import json
import re
import urllib.request
import urllib.error

MISSING_IDS = [
    "247637619", "254785901", "260446243",
    "260516599", "260517759", "260518081", "260518643", "260519003",
    "260519631", "260520199", "260520783", "260521093", "260521565",
    "260523105", "260524507", "260526039", "260528521",
]

BASE_URL = "https://www.controller.com"

def check_url(url):
    """Check if URL returns 200 or redirect to listing."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }
    try:
        req = urllib.request.Request(url, headers=headers, method='HEAD')
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status, resp.url
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except Exception as e:
        return 0, str(e)


def check_url_get(url):
    """GET request to check status and title."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode('utf-8', errors='replace')
        title_match = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else "No title"
        return resp.status, title, resp.url
    except urllib.error.HTTPError as e:
        return e.code, str(e), url
    except Exception as e:
        return 0, str(e), url


def main():
    # Load URL file
    with open('all ulrs.txt', 'r') as f:
        urls = [l.strip() for l in f if l.strip()]

    url_by_id = {}
    for url in urls:
        m = re.search(r'/listing/for-sale/(\d+)/', url)
        if m:
            url_by_id[m.group(1)] = url

    print(f"Checking {len(MISSING_IDS)} missing URLs...\n")

    still_exist = []
    removed = []
    errors = []

    for lid in MISSING_IDS:
        url = url_by_id.get(lid)
        if not url:
            print(f"  {lid}: NOT FOUND in URL file")
            continue

        status, title, final_url = check_url_get(url)

        if status == 200 and "just a moment" not in title.lower() and "404" not in title.lower():
            print(f"  {lid}: EXISTS (HTTP {status}) - {title[:60]}")
            still_exist.append((lid, url, title))
        elif status == 404 or "404" in title.lower() or "not found" in title.lower():
            print(f"  {lid}: REMOVED (HTTP {status}) - {title[:60]}")
            removed.append((lid, url))
        elif "just a moment" in title.lower():
            print(f"  {lid}: CLOUDFLARE BLOCKED - cannot verify")
            errors.append((lid, url, "Cloudflare"))
        else:
            print(f"  {lid}: HTTP {status} - {title[:80]}")
            errors.append((lid, url, f"HTTP {status}"))

    print(f"\n{'='*60}")
    print(f"SUMMARY:")
    print(f"  Still exist (can be scraped): {len(still_exist)}")
    print(f"  Removed/delisted:             {len(removed)}")
    print(f"  Blocked/error:                {len(errors)}")

    if still_exist:
        print(f"\nExisting URLs to re-scrape:")
        for lid, url, title in still_exist:
            print(f"  {lid}: {url}")

    if removed:
        print(f"\nRemoved URLs (delisted):")
        for lid, url in removed:
            print(f"  {lid}: {url}")

    if errors:
        print(f"\nErrors (could not verify):")
        for lid, url, reason in errors:
            print(f"  {lid}: {reason} - {url}")

    # Save results
    results = {
        "still_exist": [(lid, url) for lid, url, _ in still_exist],
        "removed": removed,
        "errors": [(lid, url, r) for lid, url, r in errors],
    }
    with open('output/missing_check_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to output/missing_check_results.json")


if __name__ == "__main__":
    main()
