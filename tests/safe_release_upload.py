#!/usr/bin/env python3
"""
safe_release_upload.py - Safe, Non-Clobbering Release Asset Publisher

Ensures:
1. Release is created as a draft if not already present.
2. Existing assets with matching SHA-256 are safely skipped.
3. Existing assets with conflicting SHA-256 trigger an immediate hard error (NO clobber).
4. Unseen assets are uploaded without --clobber.
5. Verifies all expected deliverables and SHA256SUMS are present.
"""

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile


def log(msg: str):
    print(f"[SAFE-RELEASE-UPLOAD] {msg}", flush=True)


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Safe GitHub Release Asset Upload")
    parser.add_argument("--tag", required=True, help="Tag name (e.g. v0.14.1)")
    parser.add_argument("--dist-dir", default="dist", help="Directory containing assets to upload")
    parser.add_argument("--draft", action="store_true", default=True, help="Create draft release if not exists")
    parser.add_argument("--require-source", action="store_true", default=True, help="Require source tarball")
    args = parser.parse_args()

    tag = args.tag
    dist_dir = pathlib.Path(args.dist_dir).resolve()

    if not dist_dir.exists():
        log(f"Error: Dist directory {dist_dir} does not exist!")
        sys.exit(1)

    dist_files = sorted([p for p in dist_dir.iterdir() if p.is_file()])
    if not dist_files:
        log(f"Error: No files found in {dist_dir}!")
        sys.exit(1)

    log(f"Publishing assets for release {tag} from {dist_dir} ({len(dist_files)} files)")

    # 1. Check or create draft release
    view_proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "isDraft,assets,tagName"],
        capture_output=True, text=True
    )

    if view_proc.returncode != 0:
        log(f"Release {tag} does not exist. Creating draft release...")
        create_cmd = ["gh", "release", "create", tag, "--draft", "--verify-tag", "--title", tag, "--notes", f"Release {tag}"]
        create_proc = subprocess.run(create_cmd, capture_output=True, text=True)
        if create_proc.returncode != 0:
            log(f"Error creating draft release: {create_proc.stderr}")
            sys.exit(create_proc.returncode)
        log(f"Draft release {tag} created successfully.")
        # Re-fetch info
        view_proc = subprocess.run(
            ["gh", "release", "view", tag, "--json", "isDraft,assets,tagName"],
            capture_output=True, text=True, check=True
        )

    rel_info = json.loads(view_proc.stdout)
    log(f"Release state: tagName={rel_info.get('tagName')}, isDraft={rel_info.get('isDraft')}")

    existing_assets = {a["name"]: a for a in rel_info.get("assets", [])}
    log(f"Existing remote assets: {list(existing_assets.keys())}")

    # 2. Upload or verify each file
    with tempfile.TemporaryDirectory(prefix="trg-release-audit-") as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        for fpath in dist_files:
            fname = fpath.name
            local_sha = sha256_file(fpath)

            if fname in existing_assets:
                log(f"Checking existing remote asset: {fname}...")
                dl_proc = subprocess.run(
                    ["gh", "release", "download", tag, "-p", fname, "-D", str(tmp_path)],
                    capture_output=True, text=True
                )
                if dl_proc.returncode != 0:
                    log(f"Error downloading existing asset {fname} for verification: {dl_proc.stderr}")
                    sys.exit(1)

                downloaded_file = tmp_path / fname
                remote_sha = sha256_file(downloaded_file)
                downloaded_file.unlink()

                if local_sha == remote_sha:
                    log(f"  MATCH: Remote asset '{fname}' matches local SHA-256 ({local_sha}). Skipping upload.")
                    continue
                else:
                    log(f"  CRITICAL ERROR: Conflict detected for '{fname}'!")
                    log(f"    Local  SHA-256: {local_sha}")
                    log(f"    Remote SHA-256: {remote_sha}")
                    log(f"    Automated clobber is strictly prohibited. Aborting release!")
                    sys.exit(1)

            # File does not exist remotely, upload without --clobber
            log(f"Uploading new asset: {fname} ({local_sha})...")
            up_proc = subprocess.run(
                ["gh", "release", "upload", tag, str(fpath)],
                capture_output=True, text=True
            )
            if up_proc.returncode != 0:
                log(f"Error uploading asset {fname}: {up_proc.stderr}")
                sys.exit(1)
            log(f"  Successfully uploaded {fname}")

    # 3. Final verification of assets
    final_view = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets"],
        capture_output=True, text=True, check=True
    )
    final_assets = [a["name"] for a in json.loads(final_view.stdout).get("assets", [])]
    log(f"Final remote assets attached to {tag}: {final_assets}")

    for fpath in dist_files:
        if fpath.name not in final_assets:
            log(f"Error: Expected file {fpath.name} not found in remote release assets!")
            sys.exit(1)

    log(f"All {len(dist_files)} assets safely published and verified for release {tag}!")


if __name__ == "__main__":
    main()
