#!/usr/bin/env python3
"""Inject a release version into the two local package-version records."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    rb"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    rb"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    rb"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?\Z"
)
SECTION = re.compile(rb"(?m)^[ \t]*\[([^\]\r\n]+)\][ \t]*(?:#.*)?(?:\r?$)")
PACKAGE_BLOCK = re.compile(rb"(?m)^[ \t]*\[\[package\]\][ \t]*(?:#.*)?(?:\r?$)")
STRING_KEY = re.compile(
    rb"(?m)^[ \t]*([A-Za-z0-9_-]+)[ \t]*=[ \t]*([\"'])([^\"']*)\2[ \t]*(?:#.*)?(?:\r?$)"
)


def _section(data: bytes, name: bytes) -> tuple[int, int]:
    headers = list(SECTION.finditer(data))
    matches = [header for header in headers if header.group(1).strip() == name]
    if len(matches) != 1:
        raise ValueError(f"Cargo.toml must contain exactly one [{name.decode()}] section")
    header = matches[0]
    next_header = next(
        (candidate.start() for candidate in headers if candidate.start() > header.start()),
        len(data),
    )
    return header.end(), next_header


def _key(section: bytes, key: bytes, filename: str) -> re.Match[bytes]:
    matches = [match for match in STRING_KEY.finditer(section) if match.group(1) == key]
    if len(matches) != 1:
        raise ValueError(f"{filename} must contain exactly one string key {key.decode()!r}")
    return matches[0]


def _manifest_version(data: bytes) -> tuple[bytes, tuple[int, int]]:
    start, end = _section(data, b"package")
    package = data[start:end]
    name = _key(package, b"name", "Cargo.toml")
    if name.group(3) != b"codex-switch":
        raise ValueError("Cargo.toml [package] name must be 'codex-switch'")
    version = _key(package, b"version", "Cargo.toml")
    value_start, value_end = (start + offset for offset in version.span(3))
    return version.group(3), (value_start, value_end)


def _lock_version(data: bytes) -> tuple[bytes, tuple[int, int]]:
    blocks = list(PACKAGE_BLOCK.finditer(data))
    candidates: list[tuple[int, int, re.Match[bytes]]] = []
    for index, block in enumerate(blocks):
        block_end = blocks[index + 1].start() if index + 1 < len(blocks) else len(data)
        content = data[block.end() : block_end]
        name = _key(content, b"name", "Cargo.lock")
        if name.group(3) == b"codex-switch":
            candidates.append((block.end(), block_end, name))
    local = [candidate for candidate in candidates if not any(
        match.group(1) == b"source"
        for match in STRING_KEY.finditer(data[candidate[0] : candidate[1]])
    )]
    if len(local) != 1:
        raise ValueError("Cargo.lock must contain exactly one local codex-switch package")
    block_start, block_end, _ = local[0]
    content = data[block_start:block_end]
    version = _key(content, b"version", "Cargo.lock")
    value_start, value_end = (block_start + offset for offset in version.span(3))
    return version.group(3), (value_start, value_end)


def _replace(data: bytes, span: tuple[int, int], value: bytes) -> bytes:
    start, end = span
    return data[:start] + value + data[end:]


def inject(root: Path, version: str) -> None:
    try:
        target = version.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("version must be ASCII SemVer") from error
    if SEMVER.fullmatch(target) is None:
        raise ValueError(f"invalid SemVer release version: {version!r}")

    manifest_path = root / "Cargo.toml"
    lock_path = root / "Cargo.lock"
    try:
        manifest = manifest_path.read_bytes()
        lockfile = lock_path.read_bytes()
    except OSError as error:
        raise ValueError(f"unable to read release inputs: {error}") from error

    manifest_version, manifest_span = _manifest_version(manifest)
    lock_version, lock_span = _lock_version(lockfile)
    if manifest_version != lock_version:
        raise ValueError(
            "Cargo.toml [package] version and Cargo.lock local codex-switch version must match"
        )

    new_manifest = _replace(manifest, manifest_span, target)
    new_lockfile = _replace(lockfile, lock_span, target)
    try:
        if new_manifest != manifest:
            manifest_path.write_bytes(new_manifest)
        if new_lockfile != lockfile:
            lock_path.write_bytes(new_lockfile)
    except OSError as error:
        raise ValueError(f"unable to write release inputs: {error}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release SemVer to inject")
    parser.add_argument("--root", type=Path, default=SCRIPT_ROOT, help="repository root")
    args = parser.parse_args()
    try:
        inject(args.root, args.version)
    except ValueError as error:
        parser.error(str(error))
    print(f"Injected release version {args.version} into Cargo.toml and Cargo.lock")


if __name__ == "__main__":
    main()
