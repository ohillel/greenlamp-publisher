"""
Orchestrator: one live scrape (PressWhizz) plus a catalog read for the rest.

Links.me, PRNews and Collaborator are no longer visited. Their prices come
from publisher_prices, synced from a Google Sheet per source — see
price_catalog.py. That removes three browser/API round trips from every
lookup along with every failure mode they brought: logins, session
restoration, changed markup, rate limits and the 60s timeouts.

PressWhizz has no sheet, so it alone is still fetched live.
"""
import concurrent.futures
from . import presswhizz, price_catalog

# Hard ceiling for the live scrape. Native threads can't be force-killed once
# started, but bounding future.result() ensures fetch_prices() — and therefore
# the /api/prices request and the background task that calls it — always
# returns within this window instead of hanging the spinner forever. Any
# thread still running past this point keeps going in the background and is
# abandoned; its result is simply not waited for.
PER_SITE_TIMEOUT_SECONDS = 60


def fetch_prices(magazine_domain: str, client_name: str, debug: bool = False) -> dict:
    """
    Returns:
        {
            "presswhizz":   <int or None>,     # USD, scraped live
            "linksme":      <float or None>,   # EUR, from the catalog
            "prnews":       <float or None>,   # USD, from the catalog
            "collaborator": <float or None>,   # EUR, from the catalog
            "errors":       { "<source>": "..." }   # only on failure
        }

    `client_name` is accepted for call-site compatibility and no longer used:
    it only ever selected a Links.me project, and Links.me is now a table read.
    """
    result = {
        "presswhizz": None, "linksme": None, "prnews": None,
        "collaborator": None, "errors": {},
    }

    # The catalog read is a single indexed query — fast enough to do inline,
    # and it must not be abandoned by the scrape's timeout.
    try:
        result.update(price_catalog.lookup(magazine_domain))
    except Exception as e:
        result["errors"]["catalog"] = str(e)
        print(f"  [price_catalog] ERROR: {e}")

    # Deliberately not using `with ThreadPoolExecutor(...) as executor:` —
    # the context manager's __exit__ calls shutdown(wait=True), which would
    # block on a still-running thread regardless of the per-future timeout
    # below, defeating the whole point of bounding the wait. shutdown(wait=False)
    # lets us return promptly and abandon any thread still stuck past its timeout.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        pw_future = executor.submit(presswhizz.get_price, magazine_domain, debug)
        try:
            result["presswhizz"] = pw_future.result(timeout=PER_SITE_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            result["errors"]["presswhizz"] = f"timed out after {PER_SITE_TIMEOUT_SECONDS}s"
            print(f"  [presswhizz] ERROR: timed out after {PER_SITE_TIMEOUT_SECONDS}s")
        except Exception as e:
            result["errors"]["presswhizz"] = str(e)
            print(f"  [presswhizz] ERROR: {e}")
    finally:
        executor.shutdown(wait=False)

    if not result["errors"]:
        del result["errors"]

    return result
