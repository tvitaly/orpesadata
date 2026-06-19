#!/usr/bin/env python3
"""
Builds a deterministic ZIP bundle and a JSON manifest for the Oropesa Bus app.

The Android app can later fetch cache_manifest.json, compare bundle.sha256 with
its stored value, and download orpesadata_bundle.zip only when the hash changes.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(os.environ.get("GITHUB_WORKSPACE", ".")).resolve()
DIST = ROOT / "dist"
BUNDLE_NAME = "orpesadata_bundle.zip"
MANIFEST_NAME = "cache_manifest.json"
INCLUDE_DIRS = ["data", "media"]

# ZIP timestamps are fixed so the same files produce the same ZIP hash.
ZIP_FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)


def sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_value(args: List[str], default: str = "") -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return default


def should_include(path: Path) -> bool:
    if not path.is_file():
        return False
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("."):
        return False
    if "/." in rel:
        return False
    return True


def collect_files() -> List[Path]:
    files: List[Path] = []
    missing_dirs: List[str] = []

    for folder in INCLUDE_DIRS:
        folder_path = ROOT / folder
        if not folder_path.exists():
            missing_dirs.append(folder)
            continue
        for path in folder_path.rglob("*"):
            if should_include(path):
                files.append(path)

    if not files:
        raise SystemExit(
            "No files found for the app bundle. Expected folders: "
            + ", ".join(INCLUDE_DIRS)
            + (f". Missing: {', '.join(missing_dirs)}" if missing_dirs else "")
        )

    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix())


def build_bundle(files: List[Path], bundle_path: Path) -> List[Dict[str, object]]:
    manifest_files: List[Dict[str, object]] = []

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            file_hash = sha256_bytes(data)

            info = zipfile.ZipInfo(rel)
            info.date_time = ZIP_FIXED_DATE_TIME
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)

            manifest_files.append(
                {
                    "path": rel,
                    "sha256": file_hash,
                    "sizeBytes": len(data),
                }
            )

    return manifest_files


def content_hash_for_files(manifest_files: List[Dict[str, object]]) -> str:
    h = hashlib.sha256()
    for item in manifest_files:
        h.update(str(item["path"]).encode("utf-8"))
        h.update(b"\0")
        h.update(str(item["sha256"]).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def write_index(manifest: Dict[str, object], index_path: Path) -> None:
    bundle = manifest["bundle"]
    assert isinstance(bundle, dict)
    repo = html.escape(str(manifest.get("repository", "")))
    commit = html.escape(str(manifest.get("commitSha", ""))[:12])
    sha = html.escape(str(bundle.get("sha256", "")))
    size = int(bundle.get("sizeBytes", 0))
    files = int(bundle.get("fileCount", 0))

    index_path.write_text(
        f"""<!doctype html>
<html lang=\"es\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Oropesa Bus app cache</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 24px; line-height: 1.45; color: #1f2933; }}
    code {{ background: #f2f4f7; padding: 2px 5px; border-radius: 5px; word-break: break-all; }}
    .card {{ max-width: 860px; border: 1px solid #d8dee7; border-radius: 14px; padding: 20px; }}
    a {{ color: #0b63ce; }}
  </style>
</head>
<body>
  <main class=\"card\">
    <h1>Oropesa Bus app cache</h1>
    <p>Static bundle for the Android app.</p>
    <p><strong>Repository:</strong> {repo}</p>
    <p><strong>Commit:</strong> <code>{commit}</code></p>
    <p><strong>Files in ZIP:</strong> {files}</p>
    <p><strong>ZIP size:</strong> {size} bytes</p>
    <p><strong>ZIP SHA-256:</strong><br><code>{sha}</code></p>
    <p>
      <a href=\"{MANIFEST_NAME}\">Open manifest</a><br>
      <a href=\"{BUNDLE_NAME}\">Download ZIP bundle</a>
    </p>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )


def main() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    bundle_path = DIST / BUNDLE_NAME
    manifest_path = DIST / MANIFEST_NAME

    files = collect_files()
    manifest_files = build_bundle(files, bundle_path)

    bundle_sha = sha256_file(bundle_path)
    content_sha = content_hash_for_files(manifest_files)
    bundle_size = bundle_path.stat().st_size

    repository = os.environ.get("GITHUB_REPOSITORY") or git_value(["config", "--get", "remote.origin.url"])
    branch = os.environ.get("GITHUB_REF_NAME") or git_value(["rev-parse", "--abbrev-ref", "HEAD"])
    commit_sha = os.environ.get("GITHUB_SHA") or git_value(["rev-parse", "HEAD"])
    run_id = os.environ.get("GITHUB_RUN_ID", "")

    manifest: Dict[str, object] = {
        "_helpRu": "Автоматически создано GitHub Actions. Приложение проверяет bundle.sha256. Если он изменился, нужно скачать orpesadata_bundle.zip и обновить локальный кеш.",
        "schemaVersion": 1,
        "generatedAtUtc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repository": repository,
        "branch": branch,
        "commitSha": commit_sha,
        "githubRunId": run_id,
        "bundle": {
            "fileName": BUNDLE_NAME,
            "relativeUrl": BUNDLE_NAME,
            "sha256": bundle_sha,
            "contentSha256": content_sha,
            "sizeBytes": bundle_size,
            "fileCount": len(manifest_files),
            "includedFolders": INCLUDE_DIRS,
        },
        "files": manifest_files,
        "appHints": {
            "manifestPath": MANIFEST_NAME,
            "bundlePath": BUNDLE_NAME,
            "compareField": "bundle.sha256",
            "downloadField": "bundle.relativeUrl",
        },
    }

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    write_index(manifest, DIST / "index.html")

    print(f"Built {bundle_path} ({bundle_size} bytes)")
    print(f"Built {manifest_path}")
    print(f"bundle.sha256={bundle_sha}")


if __name__ == "__main__":
    main()
