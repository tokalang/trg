#!/usr/bin/env python3
"""
verify_registry_catalog.py - Strict JSON-aware Toka Registry Catalog Verifier

Validates:
1. Package name == 'trg' located in packages list.
2. Package-level version and latest_version match expected version.
3. versions list contains exact entry for expected version.
4. Exact tarball_url, sha256, source tag, and repository.
5. Historical version entries (e.g. 0.14.0, 0.13.1) remain present and unaltered.
"""

import argparse
import json
import pathlib
import sys
import urllib.request


def log(msg: str):
    print(f"[REGISTRY-VERIFY] {msg}", flush=True)


KNOWN_HISTORICAL_VERSIONS = {
    "0.14.0": "ddf767934d8d14262aefb0ebcb6e2e6a4d5fa3ec643e069da9803fb1bae2a468",
    "0.13.1": "915fabf6090bd82871844209088d940fa71179e4138c5286c2a3fdf5babb28b0",
    "0.11.1": "25c381c42ffe0c4de6c33258856caacff8f488f9a234b38a31036a973f4bebca"
}


def load_catalog(source: str) -> dict:
    if source.startswith("http://") or source.startswith("https://"):
        log(f"Fetching remote catalog from {source}...")
        req = urllib.request.Request(source, headers={"User-Agent": "trg-registry-verifier/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")
            return json.loads(data)
    else:
        path = pathlib.Path(source).resolve()
        log(f"Reading local catalog from {path}...")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


def verify_catalog(catalog_data: dict, expected_version: str, expected_sha: str | None = None) -> dict:
    packages = catalog_data.get("packages", [])
    trg_pkg = None
    for pkg in packages:
        if pkg.get("name") == "trg":
            trg_pkg = pkg
            break

    if not trg_pkg:
        raise ValueError("Package 'trg' not found in registry catalog packages list")

    pkg_ver = trg_pkg.get("version")
    pkg_latest = trg_pkg.get("latest_version")

    log(f"Checking package-level versions: version='{pkg_ver}', latest_version='{pkg_latest}'")
    if pkg_ver != expected_version:
        raise ValueError(f"Package-level version '{pkg_ver}' != expected '{expected_version}'")
    if pkg_latest != expected_version:
        raise ValueError(f"Package-level latest_version '{pkg_latest}' != expected '{expected_version}'")

    versions_list = trg_pkg.get("versions", [])
    versions_map = {v.get("version"): v for v in versions_list}

    if expected_version not in versions_map:
        raise ValueError(f"Version '{expected_version}' not found in 'trg' versions list")

    target_entry = versions_map[expected_version]
    expected_tag = f"v{expected_version}"
    expected_tarball_url = f"https://github.com/tokalang/trg/releases/download/{expected_tag}/trg-{expected_version}.tar.gz"

    log(f"Verifying target entry for {expected_version}:")
    actual_url = target_entry.get("tarball_url")
    if actual_url != expected_tarball_url:
        raise ValueError(f"tarball_url mismatch: got '{actual_url}', expected '{expected_tarball_url}'")

    actual_sha = target_entry.get("sha256")
    if expected_sha:
        if not actual_sha or actual_sha.lower() != expected_sha.lower():
            raise ValueError(f"sha256 mismatch for {expected_version}: got '{actual_sha}', expected '{expected_sha}'")
    log(f"  tarball_url: {actual_url}")
    log(f"  sha256: {actual_sha}")

    source_obj = target_entry.get("source", {})
    actual_tag = source_obj.get("tag")
    actual_repo = source_obj.get("repository")
    if actual_tag != expected_tag:
        raise ValueError(f"source.tag mismatch: got '{actual_tag}', expected '{expected_tag}'")
    if actual_repo != "https://github.com/tokalang/trg":
        raise ValueError(f"source.repository mismatch: got '{actual_repo}', expected 'https://github.com/tokalang/trg'")
    log(f"  source: repo={actual_repo}, tag={actual_tag}")

    # Verify historical integrity
    log("Verifying historical versions integrity...")
    for h_ver, h_sha in KNOWN_HISTORICAL_VERSIONS.items():
        if h_ver not in versions_map:
            raise ValueError(f"Historical version '{h_ver}' is missing from catalog!")
        existing_sha = versions_map[h_ver].get("sha256", "").lower()
        if existing_sha != h_sha.lower():
            raise ValueError(
                f"Historical version '{h_ver}' SHA256 corrupted! "
                f"got '{existing_sha}', expected '{h_sha}'"
            )
        log(f"  Historical {h_ver}: verified ({existing_sha[:16]}...)")

    log(f"Catalog successfully verified: package 'trg' version {expected_version} valid and historical versions intact.")
    return {
        "status": "PASS",
        "package": "trg",
        "version": pkg_ver,
        "latest_version": pkg_latest,
        "tarball_url": actual_url,
        "sha256": actual_sha,
        "historical_versions_checked": list(KNOWN_HISTORICAL_VERSIONS.keys())
    }


def main():
    parser = argparse.ArgumentParser(description="Toka Registry Catalog Verifier")
    parser.add_argument("--catalog", required=True, help="Local path or URL to catalog.json")
    parser.add_argument("--expected-version", default="0.14.1", help="Expected version string")
    parser.add_argument("--expected-sha", help="Expected source archive SHA256")
    args = parser.parse_args()

    try:
        data = load_catalog(args.catalog)
        res = verify_catalog(data, args.expected_version, args.expected_sha)
        print(json.dumps(res, indent=2))
    except Exception as e:
        log(f"VERIFICATION FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
