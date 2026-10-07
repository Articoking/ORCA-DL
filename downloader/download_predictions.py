"""Download a folder from the public ORCA-DL OneDrive share.

Usage:
    python download_predictions.py                      # all of predictions_init_with_godas
    python download_predictions.py --leads 1-6,12       # only some lead times
    python download_predictions.py --remote stat --out ../stat_remote

Anonymous access to OneDrive shares now requires a guest ("Badger") token,
which is obtained here the same way the OneDrive web client does.

Files already present locally with the right size are skipped. Interrupted
downloads are kept as `<name>.part` and resumed on the next run.
"""
import argparse
import base64
import os
import re
import time
from collections.abc import Iterator

import requests

SHARE_URL = "https://1drv.ms/f/c/49d761d10f0b201d/Emi9scIyaWBCrNTgRo6t12oBLnF2qGDRGj0M7-g0ekRM1A"
API = "https://my.microsoftpersonalcontent.com/_api/v2.0"
BADGER_URL = "https://api-badgerp.svc.ms/v1.0/token"
BADGER_APP_ID = "5cbed6ac-a083-4e14-b191-b4ba07653de2"
CHUNK = 8 * 1024 * 1024
SEGMENT = 100 * 1024 * 1024  # bytes per Range request
MAX_RETRIES = 8
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAD_RE = re.compile(r"lead_(\d+)\.nc$")


class Session:
    """HTTP session authenticated with an anonymous OneDrive guest token.

    Attributes
    ----------
    http : requests.Session
        Underlying session carrying the ``Authorization`` header.
    """

    def __init__(self) -> None:
        """Create the session and fetch a first guest token."""
        self.http = requests.Session()
        self.refresh_token()

    def refresh_token(self) -> None:
        """Fetch a new guest ("Badger") token and set it on the session.

        Raises
        ------
        requests.HTTPError
            If the token service refuses the request.
        """
        r = self.http.post(BADGER_URL, json={"appId": BADGER_APP_ID}, timeout=60)
        r.raise_for_status()
        self.http.headers.update({"Authorization": f"Badger {r.json()['token']}", "Prefer": "autoredeem"})

    def get(self, url: str, **kwargs) -> requests.Response:
        """Send a GET request, retrying on transient errors.

        Expired tokens (HTTP 401) are refreshed, throttling and server errors
        (HTTP 429/5xx) and connection failures are retried with exponential
        backoff.

        Parameters
        ----------
        url : str
            URL to request.
        **kwargs
            Extra arguments passed to ``requests.Session.get`` (e.g. ``headers``,
            ``stream``).

        Returns
        -------
        requests.Response
            The successful response.

        Raises
        ------
        requests.HTTPError
            On a non-retryable HTTP error (e.g. 404).
        RuntimeError
            If all ``MAX_RETRIES`` attempts fail.
        """
        for attempt in range(MAX_RETRIES):
            try:
                r = self.http.get(url, timeout=120, **kwargs)
                if r.status_code == 401:
                    self.refresh_token()
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    wait = int(r.headers.get("Retry-After", 2 ** attempt))
                    print(f"  HTTP {r.status_code}, retrying in {wait}s")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                print(f"  {type(e).__name__}, retrying in {2 ** attempt}s")
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Giving up on {url}")


def list_children(s: Session, drive_id: str, item_id: str) -> Iterator[dict]:
    """Iterate over the direct children of a remote folder.

    Parameters
    ----------
    s : Session
        Authenticated session.
    drive_id : str
        OneDrive drive identifier.
    item_id : str
        Identifier of the folder to list.

    Yields
    ------
    dict
        OneDrive ``driveItem`` metadata of each child (file or folder).
    """
    url = f"{API}/drives/{drive_id}/items/{item_id}/children?$top=1000"
    while url:
        data = s.get(url).json()
        yield from data["value"]
        url = data.get("@odata.nextLink")


def walk(s: Session, drive_id: str, item_id: str, rel: str = "") -> Iterator[tuple[str, dict]]:
    """Recursively iterate over every file below a remote folder.

    Parameters
    ----------
    s : Session
        Authenticated session.
    drive_id : str
        OneDrive drive identifier.
    item_id : str
        Identifier of the folder to walk.
    rel : str, optional
        Path prefix prepended to the yielded relative paths.

    Yields
    ------
    tuple of (str, dict)
        Path of the file relative to the starting folder, and its
        ``driveItem`` metadata.
    """
    for child in list_children(s, drive_id, item_id):
        path = os.path.join(rel, child["name"])
        if "folder" in child:
            yield from walk(s, drive_id, child["id"], path)
        else:
            yield path, child


def parse_leads(spec: str) -> set[int]:
    """Parse a lead time selection such as ``"1-6,12,24"``.

    Parameters
    ----------
    spec : str
        Comma-separated list of lead times (in months) and inclusive ranges.

    Returns
    -------
    set of int
        Selected lead times.

    Raises
    ------
    argparse.ArgumentTypeError
        If ``spec`` is malformed.
    """
    leads = set()
    try:
        for part in filter(None, spec.replace(" ", "").split(",")):
            if "-" in part:
                lo, hi = map(int, part.split("-"))
                leads.update(range(lo, hi + 1))
            else:
                leads.add(int(part))
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid lead time selection: '{spec}'")
    return leads


def keep_file(rel: str, leads: set[int] | None) -> bool:
    """Decide whether a remote file matches the lead time selection.

    Parameters
    ----------
    rel : str
        Relative path of the remote file.
    leads : set of int or None
        Selected lead times, or ``None`` to keep everything.

    Returns
    -------
    bool
        ``True`` if the file should be downloaded. Files not named
        ``lead_XX.nc`` are always kept.
    """
    m = LEAD_RE.search(os.path.basename(rel))
    return leads is None or m is None or int(m.group(1)) in leads


def download(s: Session, drive_id: str, item: dict, dest: str) -> None:
    """Download one file, resuming from a previous ``.part`` file if present.

    The file is fetched in ``SEGMENT``-sized byte ranges, because OneDrive
    answers HTTP 500 to requests covering the whole of a large (~1.5 GB) file.
    Data is appended to ``dest + ".part"``, which is renamed to ``dest`` once
    its size matches the remote file.

    Parameters
    ----------
    s : Session
        Authenticated session.
    drive_id : str
        OneDrive drive identifier.
    item : dict
        ``driveItem`` metadata of the file (must contain ``id`` and ``size``).
    dest : str
        Local destination path.

    Raises
    ------
    RuntimeError
        If the server ignores the byte range, or if a segment still fails
        after ``MAX_RETRIES`` consecutive attempts.
    """
    size = item["size"]
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    url = f"{API}/drives/{drive_id}/items/{item['id']}/content"

    done = os.path.getsize(part) if os.path.exists(part) else 0
    if done > size:
        os.remove(part)
        done = 0

    failures = 0
    t0, start = time.time(), done
    with open(part, "ab") as f:
        while done < size:
            end = min(done + SEGMENT, size) - 1
            try:
                with s.get(url, headers={"Range": f"bytes={done}-{end}"}, stream=True) as r:
                    if r.status_code != 206:
                        raise RuntimeError(f"Server ignored the byte range for {dest} (HTTP {r.status_code})")
                    for chunk in r.iter_content(CHUNK):
                        f.write(chunk)
                        done += len(chunk)
                        rate = (done - start) / max(time.time() - t0, 1e-6) / 1e6
                        print(f"\r  {done / 1e6:9.1f} / {size / 1e6:.1f} MB  ({rate:.1f} MB/s)", end="", flush=True)
                failures = 0
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                failures += 1
                if failures > MAX_RETRIES:
                    raise RuntimeError(f"Failed to download {dest}") from e
                print(f"\n  {type(e).__name__}, resuming in {2 ** failures}s")
                time.sleep(2 ** failures)
    print()
    os.replace(part, dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remote", default="predictions_init_with_godas",
                        help="folder inside ORCA-DL-release, e.g. 'stat' or 'predictions_init_with_godas/tos'")
    parser.add_argument("--out", default=None, help="local destination (default: <repo root>/<remote>)")
    parser.add_argument("--leads", type=parse_leads, default=None,
                        help="lead times to download, e.g. '1-6,12,24' (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="only list what would be downloaded")
    args = parser.parse_args()
    out = args.out or os.path.join(REPO_ROOT, args.remote)

    s = Session()
    token = "u!" + base64.urlsafe_b64encode(SHARE_URL.encode()).decode().rstrip("=")
    root = s.get(f"{API}/shares/{token}/driveItem").json()
    drive_id = root["parentReference"]["driveId"]

    folder = root
    for name in filter(None, args.remote.split("/")):
        matches = [c for c in list_children(s, drive_id, folder["id"]) if c["name"] == name]
        if not matches:
            raise SystemExit(f"'{name}' not found in remote folder '{folder['name']}'")
        folder = matches[0]

    print(f"Listing {args.remote} ...")
    files = sorted((rel, item) for rel, item in walk(s, drive_id, folder["id"]) if keep_file(rel, args.leads))
    todo = []
    for rel, item in files:
        dest = os.path.join(out, rel)
        if os.path.exists(dest) and os.path.getsize(dest) == item["size"]:
            continue
        todo.append((rel, item))
    total = sum(i["size"] for _, i in todo)
    print(f"{len(files)} files selected, {len(files) - len(todo)} already present, "
          f"{len(todo)} to download ({total / 1e9:.1f} GB) into {out}")
    if args.dry_run:
        for rel, item in todo:
            print(f"  {rel}  ({item['size'] / 1e6:.1f} MB)")
        return

    for n, (rel, item) in enumerate(todo, 1):
        print(f"[{n}/{len(todo)}] {rel}")
        download(s, drive_id, item, os.path.join(out, rel))
    print("Done.")


if __name__ == "__main__":
    main()
