"""Load a profile from a JSON file into the DB via the profile-ui API.

Usage
-----
    python scripts/load_test_profile.py
    python scripts/load_test_profile.py --file data/my_profile.json
    python scripts/load_test_profile.py --reset   # deletes existing profile first

The FastAPI backend must be running before you call this script:
    python scripts/run_api.py
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

BASE_URL = "http://localhost:8000"


def _request(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        print(f"HTTP {exc.code} from {method} {path}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Cannot reach {url}: {exc.reason}", file=sys.stderr)
        print("Is the backend running?  python scripts/run_api.py", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load a profile JSON file into the DB via PUT /profile-ui/data."
    )
    parser.add_argument(
        "--file", default="data/test_profile.json",
        help="Path to the profile JSON file (default: data/test_profile.json)"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Delete the existing profile (and all its matches/cover letters) before loading"
    )
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as f:
        profile_data = json.load(f)

    if args.reset:
        _request("DELETE", "/profile-ui/data")
        print("Existing profile deleted.")

    result = _request("PUT", "/profile-ui/data", profile_data)
    print(f"Profile loaded: ID={result['profile_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
