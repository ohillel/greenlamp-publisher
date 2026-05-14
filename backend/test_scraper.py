"""
CLI test runner for the scraper.

Usage:
    python3 test_scraper.py --domain walla.co.il --client "Acme Corp"
    python3 test_scraper.py --domain walla.co.il --client "Acme Corp" --debug
    python3 test_scraper.py --site presswhizz --domain walla.co.il --debug
    python3 test_scraper.py --site linksme --domain walla.co.il --client "Acme Corp" --debug
"""
import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))

from scraper import presswhizz, linksme
from scraper.prices import fetch_prices

parser = argparse.ArgumentParser()
parser.add_argument("--domain",  required=True, help="Magazine domain, e.g. walla.co.il")
parser.add_argument("--client",  default="",    help="Client name (required for linksme)")
parser.add_argument("--site",    default="both", choices=["presswhizz", "linksme", "both"])
parser.add_argument("--debug",   action="store_true", help="Save screenshots to scraper/debug_screenshots/")
args = parser.parse_args()

print(f"\nDomain  : {args.domain}")
print(f"Client  : {args.client or '(not set)'}")
print(f"Site    : {args.site}")
print(f"Debug   : {args.debug}")
print("-" * 40)

if args.site == "presswhizz":
    print("\n[PressWhizz]")
    price = presswhizz.get_price(args.domain, debug=args.debug)
    print(f"  → price: {price}")

elif args.site == "linksme":
    if not args.client:
        print("ERROR: --client is required for linksme")
        sys.exit(1)
    print("\n[Links.me]")
    price = linksme.get_price(args.domain, args.client, debug=args.debug)
    print(f"  → price: {price}")

else:
    if not args.client:
        print("ERROR: --client is required when --site=both")
        sys.exit(1)
    print("\n[Both sites — running in parallel]")
    result = fetch_prices(args.domain, args.client, debug=args.debug)
    print(f"\nResult: {result}")

if args.debug:
    screenshots = list((Path(__file__).parent / "scraper" / "debug_screenshots").glob("*.png"))
    print(f"\nDebug screenshots saved ({len(screenshots)} files):")
    for s in sorted(screenshots):
        print(f"  {s.name}")
