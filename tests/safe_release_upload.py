#!/usr/bin/env python3
"""
safe_release_upload.py - Fail-Safe, Non-Clobbering Release Asset Publisher

Enforces:
1. Exactly four mandatory deliverables (四件套):
   - trg-{tag}-linux-x64.tar.gz
   - trg-{tag}-macos-arm64.tar.gz
   - trg-{version}.tar.gz (certified application source archive)
   - SHA256SUMS (verifying all three archives match their disk digests)
2. Remote release must be a Draft (isDraft == True); refusing upload to published releases.
3. Network/auth errors are differentiated from 'Release not found'.
4. Comprehensive pre-upload conflict check: all existing assets are verified first.
   If ANY asset differs in SHA-256, aborts immediately before uploading any file.
5. Missing assets are uploaded without --clobber.
6. Post-upload verification ensures all four assets are remotely attached and release remains Draft.
"""

import argparse
import hashlib
import json
import os
import pathlib
import shlex
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


def parse_sha256sums(sums_path: pathlib.Path) -> dict[str, str]:
    mapping = {}
    with open(sums_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                digest = parts[0]
                fname = parts[-1].lstrip("*")
                fname = pathlib.Path(fname).name
                mapping[fname] = digest
    return mapping


MANDATORY_DELIVERABLES_TEMPLATES = [
    "trg-{tag}-linux-x64.tar.gz",
    "trg-{tag}-macos-arm64.tar.gz",
    "trg-{tag}-windows-x64.zip",
    "trg-{tag}-windows-arm64.zip",
    "trg-{ver}.tar.gz",
    "SHA256SUMS"
]


def verify_local_deliverables_set(
    dist_dir: pathlib.Path,
    tag: str,
    required_templates: list[str] | None = None
) -> dict[str, pathlib.Path]:
    ver = tag.lstrip("v")
    templates = required_templates or MANDATORY_DELIVERABLES_TEMPLATES
    expected_names = [t.format(tag=tag, ver=ver) for t in templates]

    files_by_name = {}
    missing = []
    for name in expected_names:
        p = dist_dir / name
        if not p.is_file():
            missing.append(name)
        else:
            files_by_name[name] = p

    if missing:
        raise ValueError(f"Missing mandatory deliverable(s) in {dist_dir}: {', '.join(missing)}")

    # Verify SHA256SUMS covers all archives and matches on-disk digests
    sums_map = parse_sha256sums(files_by_name["SHA256SUMS"])
    archives = [n for n in expected_names if n != "SHA256SUMS"]

    for arch_name in archives:
        if arch_name not in sums_map:
            raise ValueError(f"SHA256SUMS missing entry for mandatory archive '{arch_name}'")
        actual_digest = sha256_file(files_by_name[arch_name])
        listed_digest = sums_map[arch_name]
        if actual_digest.lower() != listed_digest.lower():
            raise ValueError(
                f"Digest mismatch in SHA256SUMS for '{arch_name}': "
                f"disk={actual_digest}, listed={listed_digest}"
            )

    log(f"Verified {len(archives)} archives and SHA256SUMS in {dist_dir}")
    return files_by_name


def verify_local_four_piece_set(dist_dir: pathlib.Path, tag: str) -> dict[str, pathlib.Path]:
    return verify_local_deliverables_set(dist_dir, tag)


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def execute_safe_upload(
    tag: str,
    dist_dir: pathlib.Path,
    notes_file: pathlib.Path | None = None,
    gh_cmd: str = "gh"
):
    log(f"Initiating safe release upload sequence for tag: {tag}")
    files = verify_local_four_piece_set(dist_dir, tag)
    gh_base = shlex.split(gh_cmd)

    # 1. Inspect remote release state
    view_proc = run_cmd(gh_base + ["release", "view", tag, "--json", "isDraft,assets,tagName"])

    if view_proc.returncode != 0:
        err_lower = view_proc.stderr.lower()
        is_not_found = (
            "release not found" in err_lower or
            "could not find release" in err_lower or
            "not found" in err_lower or
            "404" in err_lower
        )
        if not is_not_found:
            log(f"CRITICAL ERROR: Querying release {tag} failed due to network or authentication error:")
            log(f"  STDERR: {view_proc.stderr.strip()}")
            sys.exit(1)

        log(f"Release {tag} does not exist. Creating draft release...")
        create_cmd = gh_base + ["release", "create", tag, "--draft", "--verify-tag", "--title", tag]
        if notes_file and notes_file.exists():
            create_cmd.extend(["--notes-file", str(notes_file)])
        else:
            create_cmd.extend(["--notes", f"Release {tag}"])

        create_proc = run_cmd(create_cmd)
        if create_proc.returncode != 0:
            log(f"CRITICAL ERROR: Failed to create draft release {tag}: {create_proc.stderr}")
            sys.exit(1)
        log(f"Draft release {tag} successfully created.")

        view_proc = run_cmd(gh_base + ["release", "view", tag, "--json", "isDraft,assets,tagName"])
        if view_proc.returncode != 0:
            log(f"CRITICAL ERROR: Unable to view newly created release {tag}: {view_proc.stderr}")
            sys.exit(1)

    rel_info = json.loads(view_proc.stdout)

    # 2. Strict Draft Gate: Must be a draft!
    if not rel_info.get("isDraft", False):
        log(f"CRITICAL ERROR: Release {tag} is already published (isDraft=False)!")
        log("Uploading assets to a published release is strictly prohibited. Aborting!")
        sys.exit(1)

    # If notes_file provided, ensure notes are updated in draft
    if notes_file and notes_file.exists():
        log(f"Updating draft release notes from {notes_file}...")
        edit_proc = run_cmd(gh_base + ["release", "edit", tag, "--notes-file", str(notes_file)])
        if edit_proc.returncode != 0:
            log(f"CRITICAL ERROR: Failed to update draft release notes: {edit_proc.stderr}")
            sys.exit(1)

    existing_assets = {a["name"]: a for a in rel_info.get("assets", [])}
    log(f"Existing remote assets: {list(existing_assets.keys())}")

    # 3. Phase 1: Pre-Upload Conflict Check across ALL existing files
    with tempfile.TemporaryDirectory(prefix="trg-release-check-") as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        for fname, local_path in files.items():
            if fname in existing_assets:
                log(f"Pre-check: Verifying existing remote asset '{fname}'...")
                dl_proc = run_cmd(gh_base + ["release", "download", tag, "-p", fname, "-D", str(tmp_path)])
                if dl_proc.returncode != 0:
                    log(f"CRITICAL ERROR: Failed to download existing asset '{fname}' for verification: {dl_proc.stderr}")
                    sys.exit(1)

                downloaded_path = tmp_path / fname
                remote_sha = sha256_file(downloaded_path)
                local_sha = sha256_file(local_path)
                downloaded_path.unlink()

                if local_sha.lower() != remote_sha.lower():
                    log(f"CRITICAL ERROR: SHA-256 conflict detected for '{fname}'!")
                    log(f"  Local  SHA-256: {local_sha}")
                    log(f"  Remote SHA-256: {remote_sha}")
                    log("Automated clobber is strictly prohibited. Aborting release without uploading!")
                    sys.exit(1)
                log(f"  Remote '{fname}' digest matches local ({local_sha}). Skipped.")

    # 4. Phase 2: Upload missing assets (only if Phase 1 had zero conflicts)
    for fname, local_path in files.items():
        if fname not in existing_assets:
            local_sha = sha256_file(local_path)
            log(f"Uploading new asset '{fname}' ({local_sha})...")
            up_proc = run_cmd(gh_base + ["release", "upload", tag, str(local_path)])
            if up_proc.returncode != 0:
                log(f"CRITICAL ERROR: Failed to upload '{fname}': {up_proc.stderr}")
                sys.exit(1)
            log(f"  Successfully uploaded '{fname}'")

    # 5. Phase 3: Final state verification
    final_view = run_cmd(gh_base + ["release", "view", tag, "--json", "isDraft,assets"])
    if final_view.returncode != 0:
        log(f"CRITICAL ERROR: Failed to query release after upload: {final_view.stderr}")
        sys.exit(1)

    final_info = json.loads(final_view.stdout)
    if not final_info.get("isDraft", False):
        log(f"CRITICAL ERROR: Release {tag} unexpectedly lost draft status!")
        sys.exit(1)

    final_asset_names = {a["name"] for a in final_info.get("assets", [])}
    for fname in files.keys():
        if fname not in final_asset_names:
            log(f"CRITICAL ERROR: Deliverable '{fname}' not found in final release assets!")
            sys.exit(1)

    log(f"All {len(files)} mandatory deliverables safely verified and staged on draft release {tag}!")


def main():
    parser = argparse.ArgumentParser(description="Fail-Safe GitHub Release Asset Upload")
    parser.add_argument("--tag", required=True, help="Tag name (e.g. v0.15.0)")
    parser.add_argument("--dist-dir", default="dist", help="Directory containing assets to upload")
    parser.add_argument("--notes-file", help="Path to release notes markdown file")
    parser.add_argument("--gh-cmd", default="gh", help="Path to gh executable")
    args = parser.parse_args()

    dist_dir = pathlib.Path(args.dist_dir).resolve()
    if not dist_dir.exists():
        log(f"Error: Dist directory {dist_dir} does not exist!")
        sys.exit(1)

    notes_file = pathlib.Path(args.notes_file).resolve() if args.notes_file else None
    execute_safe_upload(args.tag, dist_dir, notes_file, args.gh_cmd)


if __name__ == "__main__":
    main()
