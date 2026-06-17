"""
Bulk price checker used by Or's "Price Check" tool.

Given an arbitrary pasted list of URLs/domains, fetches PressWhizz +
Links.me prices for each (Links.me always looked up under the "apiiro"
catalog, since these are ad-hoc lookups not tied to a specific client).
Does not touch the per-article scraping flow in prices.py.

Sites are checked in parallel batches of BATCH_SIZE — running every site
fully sequentially is too slow (~30-60s each), but running everything at
once causes session conflicts on PressWhizz/Links.me. Each batch runs
concurrently and the next batch only starts once the current one finishes.
"""
import concurrent.futures
from .prices import fetch_prices
from .linksme import _normalize_domain

DEFAULT_LINKSME_CLIENT = "apiiro"
BATCH_SIZE = 3


def _check_one(u: str, domain: str) -> dict:
    result = {"original_url": u, "domain": domain, "price_presswhizz": None, "price_linksme": None}
    try:
        fetched = fetch_prices(domain, DEFAULT_LINKSME_CLIENT)
        result["price_presswhizz"] = fetched.get("presswhizz")
        result["price_linksme"] = fetched.get("linksme")
    except Exception as e:
        print(f"  [bulk_price_check] error for {domain!r}: {e}")
    return result


def check_prices_bulk(urls: list[str]) -> list[dict]:
    """
    Returns a list of
        {"original_url": <as typed>, "domain": <normalized>,
         "price_presswhizz": <int|None>, "price_linksme": <int|None>}
    in the same order as the input. A site that errors yields None for that
    price rather than failing the whole batch.
    """
    domains = [_normalize_domain(u) for u in urls]
    pairs = list(zip(urls, domains))
    results = []

    for i in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[i:i + BATCH_SIZE]
        # Not using `with ThreadPoolExecutor(...) as executor:` — its __exit__
        # calls shutdown(wait=True) which is fine here since we want to wait
        # for the whole batch anyway, but explicit shutdown keeps behavior
        # consistent with the rest of the scraper code.
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=BATCH_SIZE)
        try:
            futures = [executor.submit(_check_one, u, domain) for u, domain in batch]
            results.extend(f.result() for f in futures)
        finally:
            executor.shutdown(wait=True)

    return results
