# License: AGPL-3.0-only (LICENSE).
"""The rules of the Home Assistant Marketplace and of the HACS action that the repository's own files decide.

Rules under test (https://contributors.home-assistant.io/marketplace/reference/validation/, repository-structure/
and hacs-json/):
- hacs.json is in the root, holds only the keys of the HACS schema, each of its type, with a name, and the minimum
  Home Assistant version as YYYY.M.P.
- README.md is in the root (the information file).
- custom_components/ holds exactly one integration folder (the Marketplace installs the first), named as the domain
  of its manifest.json.
- The domain uses only lowercase letters, digits, underscores and hyphens; the manifest has domain, name, version,
  documentation, issue_tracker and codeowners (a list); the version is a plain semantic version.
- brand/icon.png is a square PNG (the brands check), icon@2x.png twice its size.
- The integration folder stays far below the Marketplace's download limits (100 MiB, 50,000 files).
- The workflows run the HACS action for an integration with no ignored check, and hassfest.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HACS_KEYS = {
    "name": str,
    "content_in_root": bool,
    "country": (str, list),
    "filename": str,
    "hacs": str,
    "hide_default_branch": bool,
    "homeassistant": str,
    "persistent_directory": str,
    "render_readme": bool,
    "zip_release": bool,
}
ADDRESS = "[repository " + "address]"  # the address placeholder of the working repository, filled at release


def _integration() -> Path:
    folders = [p for p in (REPO / "custom_components").iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    assert len(folders) == 1, folders
    return folders[0]


def _manifest() -> dict:
    return json.loads((_integration() / "manifest.json").read_text(encoding="utf-8"))


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR", path
    return struct.unpack(">II", data[16:24])


def test_hacs_json_holds_only_schema_keys_of_their_types():
    hacs = json.loads((REPO / "hacs.json").read_text(encoding="utf-8"))
    for key, value in hacs.items():
        assert key in HACS_KEYS, key
        assert isinstance(value, HACS_KEYS[key]), key
    assert hacs["name"] == _manifest()["name"]
    assert re.fullmatch(r"\d{4}\.\d{1,2}\.\d+", hacs["homeassistant"])
    assert not hacs.get("zip_release") and not hacs.get("content_in_root")


def test_the_information_file_is_in_the_root():
    assert (REPO / "README.md").is_file()


def test_one_integration_folder_named_as_its_domain():
    manifest = _manifest()
    assert _integration().name == manifest["domain"]
    assert re.fullmatch(r"[a-z0-9_-]+", manifest["domain"])


def test_the_manifest_has_every_key_the_marketplace_reads():
    manifest = _manifest()
    for key in ("domain", "name", "version", "documentation", "issue_tracker", "codeowners"):
        assert manifest.get(key), key
    assert isinstance(manifest["codeowners"], list)
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    for key in ("documentation", "issue_tracker"):
        assert manifest[key].startswith((ADDRESS, "https://")), key


def test_the_brand_icon_is_a_square_png():
    brand = _integration() / "brand"
    width, height = _png_size(brand / "icon.png")
    assert width == height >= 256
    assert _png_size(brand / "icon@2x.png") == (2 * width, 2 * height)


def test_the_integration_folder_is_far_below_the_download_limits():
    files = [p for p in _integration().rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    assert len(files) < 50_000 and sum(p.stat().st_size for p in files) < 100 * 1024 * 1024


def test_the_workflows_run_the_hacs_action_without_ignored_checks_and_hassfest():
    texts = "\n".join(p.read_text(encoding="utf-8") for p in sorted((REPO / ".github" / "workflows").glob("*.y*ml")))
    assert re.search(r'uses:\s*"?hacs/action@', texts) and re.search(r'category:\s*"?integration"?', texts)
    assert not re.search(r"^\s*ignore:", texts, re.MULTILINE)
    assert re.search(r'uses:\s*"?home-assistant/actions/hassfest@', texts)
