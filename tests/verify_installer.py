#!/usr/bin/env python3
"""Black-box qualification for the standalone trg installer."""

import hashlib
import os
import pathlib
import platform
import stat
import subprocess
import tarfile
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
STABLE_VERSION = "v0.10.0"


def fail(message: str) -> None:
    raise AssertionError(message)


def target_name() -> str:
    os_name = {"Darwin": "macos", "Linux": "linux"}.get(platform.system())
    arch_name = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x64",
        "amd64": "x64",
    }.get(platform.machine())
    if (os_name, arch_name) not in (("macos", "arm64"), ("linux", "x64")):
        fail(f"unsupported installer qualification host: {platform.system()} {platform.machine()}")
    return f"{os_name}-{arch_name}"


def make_release(release_dir: pathlib.Path, version: str = STABLE_VERSION) -> pathlib.Path:
    target = target_name()
    archive_name = f"trg-{version}-{target}.tar.gz"
    payload_root = release_dir / f"trg-{version}-{target}"
    payload_root.mkdir(parents=True, exist_ok=True)
    binary = payload_root / "trg"
    ver_clean = version.lstrip("v")
    binary.write_text(f"#!/bin/sh\necho 'trg {ver_clean} (installer fixture)'\n", encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    archive = release_dir / archive_name
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(payload_root, arcname=payload_root.name)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sums_file = release_dir / "SHA256SUMS"
    existing_sums = sums_file.read_text(encoding="utf-8") if sums_file.exists() else ""
    sums_file.write_text(
        f"{existing_sums}{digest}  {archive_name}\n", encoding="utf-8"
    )
    return archive


def base_env(home: pathlib.Path, release_dir: pathlib.Path, fake_bin: pathlib.Path, version: str = None) -> dict:
    env = os.environ.copy()
    for key in ("INSTALL_DIR", "TRG_SYSTEM_INSTALL_DIR", "VERSION"):
        env.pop(key, None)
    env.update(
        {
            "HOME": str(home),
            "SHELL": "/bin/zsh",
            "TRG_RELEASE_BASE_URL": release_dir.as_uri(),
            "PATH": f"{fake_bin}:{env.get('PATH', '')}",
            "TRG_SUDO_LOG": str(home / "sudo-was-called"),
        }
    )
    if version is not None:
        env["VERSION"] = version
    return env


def run_installer(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", str(INSTALLER), *args],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def assert_success(result: subprocess.CompletedProcess, label: str) -> None:
    if result.returncode != 0:
        fail(
            f"{label} failed with exit {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="trg-installer-verify-") as tmp_name:
        tmp = pathlib.Path(tmp_name)
        release_dir = tmp / "release"
        release_dir.mkdir()
        archive = make_release(release_dir)

        fake_bin = tmp / "fake-bin"
        fake_bin.mkdir()
        sudo = fake_bin / "sudo"
        sudo.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$TRG_SUDO_LOG\"\n"
            "[ \"${TRG_FAKE_SUDO_ALLOW:-0}\" = 1 ] || exit 97\n"
            "case \"$1\" in\n"
            "  mkdir)\n"
            "    last=''\n"
            "    for arg in \"$@\"; do last=\"$arg\"; done\n"
            "    chmod u+w \"$last\"\n"
            "    exec \"$@\"\n"
            "    ;;\n"
            "  install) exec \"$@\" ;;\n"
            "  *) exit 98 ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        sudo.chmod(0o755)

        # Default mode is user-local, never invokes sudo, and configures both
        # zsh startup paths needed by interactive and login/GUI-launched shells.
        default_home = tmp / "default-home"
        default_home.mkdir()
        env = base_env(default_home, release_dir, fake_bin)
        first = run_installer(env)
        assert_success(first, "default user installation")
        installed = default_home / ".local" / "bin" / "trg"
        if not installed.is_file() or not os.access(installed, os.X_OK):
            fail("default installation did not create an executable in ~/.local/bin")
        default_bin_out = subprocess.check_output([str(installed)]).decode("utf-8")
        if f"trg {STABLE_VERSION.lstrip('v')}" not in default_bin_out:
            fail(f"default installation installed wrong version: {default_bin_out}")
        if (default_home / "sudo-was-called").exists():
            fail("default installation unexpectedly invoked sudo")
        for profile_name in (".zprofile", ".zshrc"):
            profile = default_home / profile_name
            content = profile.read_text(encoding="utf-8")
            if '$HOME/.local/bin' not in content:
                fail(f"{profile_name} was not configured for ~/.local/bin")
        if "cannot change the environment of this running shell" not in first.stdout:
            fail("installer did not explain current-process PATH limitations")
        if "restart GUI applications such as Codex" not in first.stdout:
            fail("installer did not explain GUI application restart requirements")

        # Explicit VERSION override downloads the requested version
        override_version = "v0.9.0"
        make_release(release_dir, override_version)
        override_home = tmp / "override-home"
        override_home.mkdir()
        override_env = base_env(override_home, release_dir, fake_bin, version=override_version)
        override_run = run_installer(override_env)
        assert_success(override_run, "version override installation")
        override_installed = override_home / ".local" / "bin" / "trg"
        override_output = subprocess.check_output([str(override_installed)]).decode("utf-8")
        if "trg 0.9.0" not in override_output:
            fail(f"version override failed, expected trg 0.9.0, got: {override_output}")

        # Reinstallation is idempotent: no duplicate managed PATH blocks.
        second = run_installer(env)
        assert_success(second, "idempotent reinstallation")
        for profile_name in (".zprofile", ".zshrc"):
            content = (default_home / profile_name).read_text(encoding="utf-8")
            if content.count('$HOME/.local/bin') != 2:
                fail(f"{profile_name} PATH block was duplicated")

        # Explicit destinations neither invoke sudo nor rewrite shell profiles.
        custom_home = tmp / "custom-home"
        custom_home.mkdir()
        custom_dir = tmp / "custom-bin"
        custom_env = base_env(custom_home, release_dir, fake_bin)
        custom = run_installer(custom_env, "--install-dir", str(custom_dir))
        assert_success(custom, "custom-directory installation")
        if not (custom_dir / "trg").is_file():
            fail("custom installation did not install trg")
        if (custom_home / ".zprofile").exists() or (custom_home / ".zshrc").exists():
            fail("custom installation unexpectedly modified shell profiles")
        if (custom_home / "sudo-was-called").exists():
            fail("custom installation unexpectedly invoked sudo")

        # System mode is explicit, targets the system prefix, and does not
        # rewrite per-user profiles. The override keeps this test unprivileged.
        system_home = tmp / "system-home"
        system_home.mkdir()
        system_dir = tmp / "system-bin"
        system_dir.mkdir()
        system_dir.chmod(0o555)
        system_env = base_env(system_home, release_dir, fake_bin)
        system_env["TRG_SYSTEM_INSTALL_DIR"] = str(system_dir)
        system_env["TRG_FAKE_SUDO_ALLOW"] = "1"
        system = run_installer(system_env, "--system")
        assert_success(system, "explicit system installation")
        if not (system_dir / "trg").is_file():
            fail("explicit system mode did not install into the system prefix")
        if (system_home / ".zprofile").exists() or (system_home / ".zshrc").exists():
            fail("system installation unexpectedly modified user shell profiles")
        sudo_log = system_home / "sudo-was-called"
        if os.geteuid() == 0:
            if sudo_log.exists():
                fail("root system installation unexpectedly invoked sudo")
        else:
            sudo_calls = sudo_log.read_text(encoding="utf-8").splitlines()
            if len(sudo_calls) != 2:
                fail(f"system mode made unexpected sudo calls: {sudo_calls}")
            if not sudo_calls[0].startswith("mkdir -p "):
                fail(f"first sudo call was not destination creation: {sudo_calls[0]}")
            if not sudo_calls[1].startswith("install -m 0755 "):
                fail(f"second sudo call was not the verified binary install: {sudo_calls[1]}")

        # A bad checksum must fail closed before anything is installed.
        bad_home = tmp / "bad-checksum-home"
        bad_home.mkdir()
        bad_release = tmp / "bad-release"
        bad_release.mkdir()
        bad_archive = bad_release / archive.name
        bad_archive.write_bytes(archive.read_bytes())
        (bad_release / "SHA256SUMS").write_text(
            f"{'0' * 64}  {archive.name}\n", encoding="utf-8"
        )
        bad_env = base_env(bad_home, bad_release, fake_bin)
        bad = run_installer(bad_env)
        if bad.returncode == 0 or "SHA-256 mismatch" not in bad.stderr:
            fail("checksum mismatch did not fail closed with a clear diagnostic")
        if (bad_home / ".local" / "bin" / "trg").exists():
            fail("checksum-mismatched binary was installed")

        # An explicit invalid destination is an error, never a silent fallback.
        invalid_home = tmp / "invalid-destination-home"
        invalid_home.mkdir()
        parent_file = tmp / "not-a-directory"
        parent_file.write_text("fixture", encoding="utf-8")
        invalid_env = base_env(invalid_home, release_dir, fake_bin)
        invalid = run_installer(
            invalid_env, "--install-dir", str(parent_file / "child")
        )
        if invalid.returncode == 0:
            fail("invalid explicit destination unexpectedly succeeded")
        if (invalid_home / ".local" / "bin" / "trg").exists():
            fail("invalid explicit destination silently fell back to ~/.local/bin")

        # Conflicting destination modes fail before any download or mutation.
        conflict = run_installer(
            base_env(tmp / "unused-home", release_dir, fake_bin),
            "--system",
            "--install-dir",
            str(tmp / "conflict-bin"),
        )
        if conflict.returncode == 0 or "cannot be combined" not in conflict.stderr:
            fail("conflicting system/custom modes were not rejected")

        invalid_version_env = base_env(tmp / "version-home", release_dir, fake_bin)
        invalid_version_env["VERSION"] = "../../not-a-release"
        invalid_version = run_installer(invalid_version_env)
        if invalid_version.returncode == 0 or "Invalid VERSION" not in invalid_version.stderr:
            fail("unsafe VERSION value was not rejected before download")

    print("[INSTALLER-VERIFY] PASS: user-local, PATH, checksum, and failure semantics")


if __name__ == "__main__":
    main()
