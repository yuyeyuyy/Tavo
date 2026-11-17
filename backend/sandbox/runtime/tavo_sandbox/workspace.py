"""Fixed trusted volume operations. Never extracts a tar or follows user symlinks."""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath


class WorkspaceFailure(Exception):
    pass


def manifest(root, limits, *, legacy=False):
    root = Path(root)
    result, total = {}, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        if legacy and Path(directory) == root and ".tavo_versions" in dirs:
            dirs.remove(".tavo_versions")
        for name in dirs + files:
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (
                stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)
            ):
                raise WorkspaceFailure("POLICY_DENIED")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise WorkspaceFailure("POLICY_DENIED")
            if stat.S_ISDIR(info.st_mode):
                result[str(path.relative_to(root))] = [0, "directory"]
                if len(result) > limits["workspace_max_files"]:
                    raise WorkspaceFailure("RESOURCE_LIMIT")
        for name in files:
            path = Path(directory) / name
            key = str(path.relative_to(root))
            if len(key.encode()) > 1024 or len(result) >= limits["workspace_max_files"]:
                raise WorkspaceFailure("RESOURCE_LIMIT")
            hashed, size = hashlib.sha256(), 0
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                while data := stream.read(65536):
                    size += len(data)
                    total += len(data)
                    if size > limits["max_file_bytes"] or total > limits["workspace_max_bytes"]:
                        raise WorkspaceFailure("RESOURCE_LIMIT")
                    hashed.update(data)
            result[key] = [size, hashed.hexdigest()]
    digest = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return result, digest


def baseline(root, revision):
    if revision is None:
        return Path(root)
    if not re.fullmatch(r"[a-f0-9]{32}", revision):
        raise WorkspaceFailure("POLICY_DENIED")
    path = Path(root) / ".tavo_versions" / revision
    if path.is_symlink() or path.parent.is_symlink() or not path.is_dir():
        raise WorkspaceFailure("SANDBOX_ERROR")
    return path


def copy_files(source, destination, entries):
    destination = Path(destination)
    destination.mkdir(exist_ok=True, parents=True)
    for name in entries:
        path = destination / name
        if entries[name][1] == "directory":
            path.mkdir(exist_ok=True, parents=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(source) / name, path, follow_symlinks=False)
        os.chmod(path, (Path(source) / name).stat().st_mode & 0o777)


def operate(value, source=Path("/source"), work=Path("/workspace")):
    if value["action"] == "gc":
        for revision in value["remove_revisions"]:
            if not re.fullmatch(r"[a-f0-9]{32}", revision):
                raise WorkspaceFailure("POLICY_DENIED")
            path = Path(source) / ".tavo_versions" / revision
            if path.is_symlink() or path.parent.is_symlink():
                raise WorkspaceFailure("POLICY_DENIED")
            if path.exists():
                shutil.rmtree(path)
        return {"ok": True}
    limits = value["limits"]
    revision = value.get("revision")
    original = baseline(source, revision)
    old, _ = manifest(original, limits, legacy=revision is None)
    action = value["action"]
    if action == "initialize":
        if any(work.iterdir()):
            return {"ok": True}  # existing task working copy; never overwrite its partial files
        copy_files(original, work, old)
        return {"ok": True}
    new, fingerprint = manifest(work, limits)
    deleted = len(old.keys() - new.keys())
    changed = sum(name in new and old[name] != new[name] for name in old)
    result = {
        "digest": fingerprint,
        "deleted": deleted,
        "changed": changed,
        "files": len(new),
        "bytes": sum(item[0] for item in new.values()),
        "deletedPaths": sorted(old.keys() - new.keys())[:128],
        "changedPaths": sorted(name for name in old if name in new and old[name] != new[name])[
            :128
        ],
    }
    if action == "inspect":
        return result
    if action != "commit" or fingerprint != value["expected_digest"]:
        raise WorkspaceFailure("POLICY_DENIED")
    target_revision = value["new_revision"]
    if not re.fullmatch(r"[a-f0-9]{32}", target_revision):
        raise WorkspaceFailure("POLICY_DENIED")
    parent = Path(source) / ".tavo_versions"
    if parent.is_symlink():
        raise WorkspaceFailure("POLICY_DENIED")
    parent.mkdir(exist_ok=True)
    destination = parent / target_revision
    destination.mkdir()  # no overwrite; DB pointer changes only after the copy and fence checks
    copy_files(work, destination, new)
    return result


def write_chunk(value, root=Path("/workspace")):
    parts = PurePosixPath(value["path"]).parts
    if (
        not parts
        or PurePosixPath(value["path"]).is_absolute()
        or ".." in parts
        or "\\" in value["path"]
    ):
        raise WorkspaceFailure("POLICY_DENIED")
    opened = [os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    temporary = ".tavo-write-" + os.urandom(16).hex()
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=opened[-1])
            except FileExistsError:
                pass
            opened.append(
                os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=opened[-1])
            )
        previous = b""
        try:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=opened[-1])
        except FileNotFoundError:
            fd = None
        if fd is not None:
            with os.fdopen(fd, "rb") as stream:
                if (
                    not stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
                    or os.fstat(stream.fileno()).st_nlink != 1
                ):
                    raise WorkspaceFailure("POLICY_DENIED")
                previous = stream.read(value["max_file_bytes"] + 1)
            if len(previous) > value["max_file_bytes"]:
                raise WorkspaceFailure("RESOURCE_LIMIT")
            if hashlib.sha256(previous).hexdigest() != value.get("expectedSha256"):
                raise WorkspaceFailure("REVISION_CONFLICT")
        if value["offset"] not in {0, len(previous)}:
            raise WorkspaceFailure("REVISION_CONFLICT")
        data = (previous if value["offset"] else b"") + value["content"].encode()
        if len(data) > value["max_file_bytes"]:
            raise WorkspaceFailure("RESOURCE_LIMIT")
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=opened[-1],
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, parts[-1], src_dir_fd=opened[-1], dst_dir_fd=opened[-1])
        return {
            "sha256": hashlib.sha256(data).hexdigest(),
            "offset": len(data),
            "path": value["path"],
        }
    finally:
        for fd in reversed(opened):
            os.close(fd)


def main():
    try:
        value = json.loads(sys.stdin.buffer.read(1048576))
        result = write_chunk(value) if value["action"] == "write" else operate(value)
        print(json.dumps(result, separators=(",", ":")))
    except WorkspaceFailure as error:
        print(json.dumps({"errorCode": str(error)}))
    except (OSError, ValueError, KeyError):
        print('{"errorCode":"SANDBOX_ERROR"}')


if __name__ == "__main__":
    main()
