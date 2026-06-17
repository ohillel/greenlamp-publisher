"""
Bulk price checker used by Or's "Price Check" tool.

Given an arbitrary pasted list of URLs/domains, fetches PressWhizz +
Links.me prices for each (Links.me always looked up under the "mstone"
catalog, since these are ad-hoc lookups not tied to a specific client).
Does not touch the per-article scraping flow in prices.py.

Sites are checked sequentially (not in parallel) — running multiple
Playwright instances against the same PressWhizz/Links.me account at once
causes session conflicts, where most concurrent logins fail and return
null. One site at a time is slower (~30-60s each) but reliable.
"""
from .prices import fetch_prices
from .linksme import _normalize_domain

DEFAULT_LINKSME_CLIENT = "mstone"


def check_prices_bulk(urls: list[str]) -> list[dict]:
    """
    Returns a list of
        {"original_url": <as typed>, "domain": <normalized>,
         "price_presswhizz": <int|None>, "price_linksme": <int|None>}
    in the same order as the input. A site that errors yields None for that
    price rather than failing the whole batch.
    """
    domains = [_normalize_domain(u) for u in urls]
    results = []

    for u, domain in zip(urls, domains):
        result = {"original_url": u, "domain": domain, "price_presswhizz": None, "price_linksme": None}
        try:
            fetched = fetch_prices(domain, DEFAULT_LINKSME_CLIENT)
            result["price_presswhizz"] = fetched.get("presswhizz")
            result["price_linksme"] = fetched.get("linksme")
        except Exception as e:
            print(f"  [bulk_price_check] error for {domain!r}: {e}")
        results.append(result)

    return results
