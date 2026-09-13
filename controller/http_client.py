import requests
from requests.adapters import HTTPAdapter, Retry


def make_session() -> requests.Session:
    """Build a `requests.Session` with retry/backoff defaults for polling APIs."""
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
