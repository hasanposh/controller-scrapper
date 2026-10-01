"""Generate CSV/Excel from existing raw_listings.json without scraping."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper import build_csv, load_data, log, setup_dirs

setup_dirs()
log("Loading raw_listings.json...")
listings = load_data()
log(f"Loaded {len(listings)} listings")
build_csv(listings)
log("Done! Check output/controller_listings.csv and .xlsx")
