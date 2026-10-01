import json
from pathlib import Path

data = json.load(open('output/raw_listings.json','r',encoding='utf-8'))
progress = json.load(open('output/progress.json','r',encoding='utf-8'))
IMAGES_DIR = Path('output/images')

total_expected = 0
total_on_disk = 0
listings_with_all = 0
total_imgs = 0
total_vids = 0

for lid, listing in data.items():
    imgs = listing.get('_detail_images', [])
    vids = listing.get('_detail_videos', [])
    listing_dir = IMAGES_DIR / lid
    total_imgs += len(imgs)
    total_vids += len(vids)
    expected = len(imgs) + len(vids)
    on_disk = len(list(listing_dir.iterdir())) if listing_dir.exists() else 0
    total_expected += expected
    total_on_disk += on_disk
    if on_disk >= expected:
        listings_with_all += 1

scraped_ids = set(progress.get("scraped_ids", []))
failed_ids = set(progress.get("failed_ids", []))

print("=== DATA STATUS ===")
print("Total listings collected:", len(data))
print("Detail pages scraped:", len(scraped_ids))
print("Detail pages failed:", len(failed_ids))
print()
print("=== MEDIA STATUS ===")
print("Expected images:", total_imgs)
print("Expected videos:", total_vids)
print("Files on disk:", total_on_disk)
print("Listings with all media:", listings_with_all, "/", len(data))
print()
print("CSV:", Path("output/controller_listings.csv").exists())
print("Excel:", Path("output/controller_listings.xlsx").exists())
