"""
Orchestrator: fetch prices from both PressWhizz and Links.me concurrently.
"""
import concurrent.futures
from . import presswhizz, linksme

# Hard ceiling per site. Native threads can't be force-killed once started,
# but bounding future.result() ensures fetch_prices() — and therefore the
# /api/prices request and the background task that calls it — always returns
# within this window instead of hanging the spinner forever. Any thread still
# running past this point keeps going in the background and is abandoned;
# its result is simply not waited for.
PER_SITE_TIMEOUT_SECONDS = 60


def fetch_prices(magazine_domain: str, client_name: str, debug: bool = False) -> dict:
    """
    Returns:
        {
            "presswhizz": <int or None>,
            "linksme":    <int or None>,
            "errors":     { "presswhizz": "...", "linksme": "..." }   # only on failure
        }
    """
    result = {"presswhizz": None, "linksme": None, "errors": {}}

    def run_presswhizz():
        return presswhizz.get_price(magazine_domain, debug=debug)

    def run_linksme():
        return linksme.get_price(magazine_domain, client_name, debug=debug)

    # Deliberately not using `with ThreadPoolExecutor(...) as executor:` —
    # the context manager's __exit__ calls shutdown(wait=True), which would
    # block on a still-running thread regardless of the per-future timeout
    # below, defeating the whole point of bounding the wait. shutdown(wait=False)
    # lets us return promptly and abandon any thread still stuck past its timeout.
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        pw_future = executor.submit(run_presswhizz)
        lm_future = executor.submit(run_linksme)

        for site, future in [("presswhizz", pw_future), ("linksme", lm_future)]:
            try:
                result[site] = future.result(timeout=PER_SITE_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                result["errors"][site] = f"timed out after {PER_SITE_TIMEOUT_SECONDS}s"
                print(f"  [{site}] ERROR: timed out after {PER_SITE_TIMEOUT_SECONDS}s")
            except Exception as e:
                result["errors"][site] = str(e)
                print(f"  [{site}] ERROR: {e}")
    finally:
        executor.shutdown(wait=False)

    if not result["errors"]:
        del result["errors"]

    return result
