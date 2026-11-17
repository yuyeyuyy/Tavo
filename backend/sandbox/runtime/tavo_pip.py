"""Install only administrator-pinned wheels through the UDS package proxy."""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import httpx
from tavo_sandbox.http import EgressError, call


def main():
    if len(sys.argv) < 3 or sys.argv[1] != "install":
        raise SystemExit(
            "Usage: python -m tavo_pip install package==version [dependency==version ...]"
        )
    available = {p["name"].lower().replace("_", "-"): p for p in call("/packages", {})}
    chosen = []
    for requirement in sys.argv[2:]:
        name, separator, version = requirement.partition("==")
        package = available.get(name.lower().replace("_", "-"))
        if not separator or not package or package["version"] != version:
            raise EgressError("POLICY_DENIED")
        chosen.append(package)
    maximum = int(os.environ["TAVO_INSTALL_BYTES"])
    max_files = int(os.environ["TAVO_INSTALL_FILES"])
    total, entries = 0, 0
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        wheels = []
        for package in chosen:
            # Wheel filenames must preserve Python/ABI/platform tags; ask the archive for its
            # original canonical filename through the proxy's allowlisted package metadata.
            with httpx.Client(
                transport=httpx.HTTPTransport(uds=os.environ["TAVO_EGRESS_SOCKET"]),
                trust_env=False,
                timeout=120,
            ) as client:
                with client.stream(
                    "POST",
                    "http://egress/wheel",
                    json={"name": package["name"], "version": package["version"]},
                ) as response:
                    if response.status_code != 200:
                        raise EgressError("NETWORK_DENIED")
                    target = Path(directory) / (package["name"] + ".download")
                    hashed = hashlib.sha256()
                    with target.open("wb") as stream:
                        for chunk in response.iter_bytes(16384):
                            total += len(chunk)
                            if total > maximum:
                                raise EgressError("RESOURCE_LIMIT")
                            hashed.update(chunk)
                            stream.write(chunk)
            if hashed.hexdigest() != package["sha256"]:
                raise EgressError("POLICY_DENIED")
            with zipfile.ZipFile(target) as archive:
                metadata = []
                for info in archive.infolist():
                    entries += 1
                    total += info.file_size
                    part = PurePosixPath(info.filename)
                    if (
                        total > maximum
                        or entries > max_files
                        or part.is_absolute()
                        or ".." in part.parts
                        or "\\" in info.filename
                        or (info.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise EgressError("RESOURCE_LIMIT")
                    if info.filename.endswith(".dist-info/WHEEL"):
                        metadata.append(archive.read(info).decode())
                if len(metadata) != 1:
                    raise EgressError("POLICY_DENIED")
                filename = package["filename"]
                if not filename.endswith(".whl") or any(
                    c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.+-"
                    for c in filename
                ):
                    raise EgressError("POLICY_DENIED")
                wheel = Path(directory) / filename
                target.rename(wheel)
                wheels.append(str(wheel))
        site = Path(directory) / "site"
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                "--disable-pip-version-check",
                "--target",
                str(site),
                *wheels,
            ],
            check=True,
            timeout=int(os.environ["TAVO_INSTALL_TIMEOUT"]),
        )
        destination = Path("/workspace/.deps")
        if destination.is_symlink():
            raise EgressError("POLICY_DENIED")
        # These are task files. The manager validates their total size/symlinks and snapshots
        # the complete workspace before committing; there is no host/site-packages write.
        destination.mkdir(exist_ok=True)
        shutil.copytree(site, destination, dirs_exist_ok=True, symlinks=False)
    print("Approved task dependencies installed into /workspace/.deps")


if __name__ == "__main__":
    main()
