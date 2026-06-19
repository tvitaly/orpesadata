#!/usr/bin/env python3
"""
Build GitHub Pages output for the Oropesa del Mar urban bus app data repository.

This script creates:
  dist/orpesadata_bundle.zip  - deterministic ZIP with data/ and media/
  dist/cache_manifest.json    - manifest with current bundle SHA-256
  dist/index.html             - simple public status page
  dist/privacy.html           - public privacy policy for Google Play

Important:
  The ZIP is deterministic. If data/ and media/ do not change, bundle.sha256
  should stay the same even after a new workflow run.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Iterable

REPO_NAME = "orpesadata"
PAGES_BASE_URL = "https://tvitaly.github.io/orpesadata/"
BUNDLE_FILE_NAME = "orpesadata_bundle.zip"
MANIFEST_FILE_NAME = "cache_manifest.json"
PRIVACY_FILE_NAME = "privacy.html"

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

INCLUDED_DIRS = ("data", "media")
IGNORED_FILE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
FIXED_ZIP_DATETIME = (1980, 1, 1, 0, 0, 0)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def iter_included_files() -> Iterable[Path]:
    for dirname in INCLUDED_DIRS:
        folder = ROOT / dirname
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.name in IGNORED_FILE_NAMES:
                continue
            if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
                continue
            yield path


def write_deterministic_zip(zip_path: Path, files: list[Path]) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(rel, FIXED_ZIP_DATETIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            # Stable Unix-like file permission: rw-r--r--
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())


def copy_privacy_page() -> None:
    src = ROOT / "legal" / "privacy_policy.html"
    dst = DIST / PRIVACY_FILE_NAME
    if src.exists():
        shutil.copyfile(src, dst)
    else:
        dst.write_text(
            "<!doctype html><html lang='es'><meta charset='utf-8'>"
            "<title>Política de privacidad</title>"
            "<h1>Política de privacidad</h1>"
            "<p>Privacy policy file not found: legal/privacy_policy.html</p>"
            "</html>",
            encoding="utf-8",
        )


def build_index(manifest: dict) -> None:
    generated = html.escape(manifest.get("generatedAtUtc", ""))
    bundle_sha = html.escape(manifest["bundle"]["sha256"])
    bundle_size = int(manifest["bundle"]["sizeBytes"])
    file_count = int(manifest["bundle"]["fileCount"])

    index = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Oropesa del Mar autobús urbano — app data</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #fbefe3;
      --card: #fff8f0;
      --text: #243034;
      --muted: #58676d;
      --line: #d9c8b6;
      --accent: #0b6ea8;
    }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }}
    main {{
      max-width: 860px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 22px;
      margin: 18px 0;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 28px;
    }}
    h2 {{
      margin: 0 0 10px;
      font-size: 20px;
    }}
    p {{
      margin: 8px 0;
    }}
    a {{
      color: var(--accent);
      font-weight: 650;
    }}
    code {{
      background: rgba(255,255,255,.65);
      border: 1px solid var(--line);
      padding: 2px 6px;
      border-radius: 6px;
      overflow-wrap: anywhere;
    }}
    .muted {{
      color: var(--muted);
    }}
    .sha {{
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Oropesa del Mar autobús urbano</h1>
    <p class="muted">Public app data, cache manifest and privacy policy.</p>

    <section class="card">
      <h2>App cache</h2>
      <p><a href="{MANIFEST_FILE_NAME}">cache_manifest.json</a></p>
      <p><a href="{BUNDLE_FILE_NAME}">orpesadata_bundle.zip</a></p>
      <p>Bundle files: <strong>{file_count}</strong></p>
      <p>Bundle size: <strong>{bundle_size} bytes</strong></p>
      <p>Bundle SHA-256:</p>
      <p class="sha"><code>{bundle_sha}</code></p>
    </section>

    <section class="card">
      <h2>Privacy policy / Política de privacidad</h2>
      <p><a href="{PRIVACY_FILE_NAME}">Open privacy policy</a></p>
      <p class="muted">This public URL can be used in Google Play Console.</p>
    </section>

    <section class="card">
      <h2>Status</h2>
      <p>Generated at: <code>{generated}</code></p>
      <p class="muted">If the content of <code>data/</code> and <code>media/</code> does not change, the bundle SHA-256 should stay the same.</p>
    </section>
  </main>
</body>
</html>
"""
    (DIST / "index.html").write_text(index, encoding="utf-8")


def main() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)

    files = list(iter_included_files())

    bundle_path = DIST / BUNDLE_FILE_NAME
    write_deterministic_zip(bundle_path, files)

    bundle_sha = sha256_file(bundle_path)
    generated_at = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    file_entries = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        file_entries.append(
            {
                "path": rel,
                "sizeBytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    manifest = {
        "schemaVersion": 2,
        "project": REPO_NAME,
        "generatedAtUtc": generated_at,
        "source": {
            "repository": "https://github.com/tvitaly/orpesadata",
            "branch": os.getenv("GITHUB_REF_NAME", "main"),
            "commit": os.getenv("GITHUB_SHA", ""),
            "runId": os.getenv("GITHUB_RUN_ID", ""),
        },
        "pages": {
            "baseUrl": PAGES_BASE_URL,
            "indexUrl": PAGES_BASE_URL,
            "manifestUrl": PAGES_BASE_URL + MANIFEST_FILE_NAME,
        },
        "legal": {
            "privacyPolicyUrl": PAGES_BASE_URL + PRIVACY_FILE_NAME,
            "privacyPolicyRelativeUrl": PRIVACY_FILE_NAME,
        },
        "bundle": {
            "fileName": BUNDLE_FILE_NAME,
            "relativeUrl": BUNDLE_FILE_NAME,
            "url": PAGES_BASE_URL + BUNDLE_FILE_NAME,
            "sha256": bundle_sha,
            "sizeBytes": bundle_path.stat().st_size,
            "fileCount": len(files),
            "includedDirectories": list(INCLUDED_DIRS),
        },
        "files": file_entries,
        "_helpRu": (
            "Приложение должно сравнивать bundle.sha256 с сохранённым локальным хешем. "
            "Если sha256 не изменился — ZIP скачивать не нужно. "
            "privacyPolicyUrl — публичная ссылка для Google Play и раздела политики в приложении."
        ),
    }

    (DIST / MANIFEST_FILE_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    copy_privacy_page()
    build_index(manifest)

    print("Built GitHub Pages output:")
    print(f"  {bundle_path.relative_to(ROOT)}")
    print(f"  {(DIST / MANIFEST_FILE_NAME).relative_to(ROOT)}")
    print(f"  {(DIST / PRIVACY_FILE_NAME).relative_to(ROOT)}")
    print(f"  {(DIST / 'index.html').relative_to(ROOT)}")
    print(f"Bundle SHA-256: {bundle_sha}")


if __name__ == "__main__":
    main()
