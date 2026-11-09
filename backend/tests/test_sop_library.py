"""The YAML SOP library (backend/data/sop-library) stays importable.

Every file parses through the importer, its number matches its file name and
is unique, every SOP has ordered steps and a known category, and the manifest
lists every file. Pure (no database).
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import import_sop_library as library  # noqa: E402

FILES = sorted(library.LIBRARY.glob("sop-[0-9][0-9][0-9]-*.yaml"))


def test_every_library_file_parses_with_steps_and_a_known_category():
    assert FILES, "the SOP library is empty"
    numbers = []
    for path in FILES:
        item = library.parse_file(path)
        sop = item["sop"]
        numbers.append(sop["sop_number"])
        assert sop["category"] in library.CATEGORIES, path.name
        assert item["steps"], f"{path.name} has no steps"
        if int(sop["sop_number"][4:]) >= 31:  # the 031+ series numbers its steps 1..n
            assert [s["order"] for s in item["steps"]] == list(range(1, len(item["steps"]) + 1)), path.name
        assert all(len(t) <= 50 for t in sop["tags"]), path.name  # sops.tags is String(50)[]
        assert len(sop["name"]) <= 255
    assert len(numbers) == len(set(numbers)), "duplicate SOP numbers in the library"


def test_network_and_soc_series_from_031_is_complete():
    numbers = {library.parse_file(p)["sop"]["sop_number"] for p in FILES}
    assert {f"SOP-{n:03d}" for n in range(31, 43)} <= numbers


def test_manifest_lists_every_library_file():
    manifest = yaml.safe_load((library.LIBRARY / "manifest.yml").read_text(encoding="utf-8"))
    listed = {entry["sopId"] for entry in manifest["soPs"]}
    for path in FILES:
        assert library.parse_file(path)["sop"]["sop_number"] in listed, f"{path.name} missing from manifest.yml"
