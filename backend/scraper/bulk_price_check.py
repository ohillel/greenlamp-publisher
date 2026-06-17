"""
Bulk price checker used by Or's "Price Check" tool.

Given an arbitrary pasted list of URLs/domains, fetches PressWhizz +
Links.me prices for each (Links.me always looked up under the "mstone"
catalog, since these are ad-hoc lookups not tied to a specific client).
Does not touch the per-article scraping flow in prices.py.
"""
import concurrent.futures
from . import presswhizz, linksme
from .linksme import _normalize_domain

DEFAULT_LINKSME_CLIENT     = "mstone"
PER_SITE_TIMEOUT_SECONDS   = 60
MAX_CONCURRENT_SITE_FETCHES = 4   # bounds how many headless browsers run at once


def check_prices_bulk(urls: list[str]) -> list[dict]:
    """
    Returns a list of
        {"original_url": <as typed>, "domain": <normalized>,
         "price_presswhizz": <int|None>, "price_linksme": <int|None>}
    in the same order as the input. A site that errors or times out yields
    None for that price rather than failing the whole batch.
    """
    domains = [_normalize_domain(u) for u in urls]
    results = [
        {"original_url": u, "domain": d, "price_presswhizz": None, "price_linksme": None}
        for u, d in zip(urls, domains)
    ]

    # Not using `with ThreadPoolExecutor(...) as executor:` — its __exit__
    # calls shutdown(wait=True), which would block on any still-running
    # thread regardless of the per-future timeout below. shutdown(wait=False)
    # lets us return promptly and abandon anything still stuck past its timeout.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_SITE_FETCHES)
    try:
        futures = []
        for i, domain in enumerate(domains):
            futures.append((i, "price_presswhizz", executor.submit(presswhizz.get_price, domain)))
            futures.append((i, "price_linksme", executor.submit(linksme.get_price, domain, DEFAULT_LINKSME_CLIENT)))

        # Bound each future individually (not via as_completed, which would
        # wait unboundedly for a stuck thread before yielding the next one).
        for i, field, future in futures:
            try:
                results[i][field] = future.result(timeout=PER_SITE_TIMEOUT_SECONDS)
            except Exception as e:
                print(f"  [bulk_price_check] {field} error for {domains[i]!r}: {e}")
                results[i][field] = None
    finally:
        executor.shutdown(wait=False)

    return results
