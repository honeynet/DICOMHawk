import pytest
from copy import deepcopy
from pathlib import Path

from pydicom import dcmread
from pydicom.uid import EncapsulatedPDFStorage

from dicomhawk.repository import QRError, new_repo
from dicomhawk.storage import new_store
from honeytoken.injector import new_honeytoken_injector
from seeding.fallback import load_fallback_datasets
from seeding.locations import load_locations
from seeding.names import _patch_location
from seeding.osm import OsmClient
from seeding.seeder import new_seeder

_LOCATIONS = load_locations(None)


@pytest.fixture
def repo(tmp_path):
    r = new_repo(None, new_store(str(tmp_path / "traces")))
    r.start()
    return r


def _stored_retrieve_urls(repo):
    return [
        dcmread(f, force=True).get("RetrieveURL")
        for f in repo.storage.storage_dir.iterdir()
    ]


def test_seed_without_honeytoken_tags_nothing(repo):
    seeder = new_seeder(repo)
    stored = seeder._seed_fallback(seeder._locations[0], "CT", "test-epoch")
    assert stored > 0
    assert all(url is None for url in _stored_retrieve_urls(repo))


def test_station_names_are_stable_but_vary_by_location():
    source = next(iter(load_fallback_datasets("CT")))
    pools = (("Patient^Male",), ("Patient^Female",), ("Doctor^One",))
    names = {
        _patch_location(deepcopy(source), location, *pools).StationName
        for location in _LOCATIONS
    }
    repeated = _patch_location(deepcopy(source), _LOCATIONS[0], *pools)
    assert len(names) > 1
    assert (
        repeated.StationName
        == _patch_location(deepcopy(source), _LOCATIONS[0], *pools).StationName
    )


def test_seed_with_honeytoken_tags_exactly_one_instance(repo):
    injector = new_honeytoken_injector("https://honey.example.com", None)
    seeder = new_seeder(repo, honeytoken=injector)
    stored = seeder._seed_fallback(seeder._locations[0], "CT", "test-epoch")
    assert stored > 0

    tagged = [url for url in _stored_retrieve_urls(repo) if url is not None]
    assert len(tagged) == 1
    assert tagged[0].startswith("https://honey.example.com/")


def test_seed_run_reset_replants_honeytoken_on_reseed(repo):
    injector = new_honeytoken_injector("https://honey.example.com", None)
    seeder = new_seeder(repo, honeytoken=injector)
    loc = seeder._locations[0]

    seeder._seed_fallback(loc, "CT", "epoch-1")
    assert len([u for u in _stored_retrieve_urls(repo) if u]) == 1

    # Reusing the per-run flag overwrites the bait without planting another.
    seeder._seed_fallback(loc, "CT", "epoch-2")
    assert len([u for u in _stored_retrieve_urls(repo) if u]) == 0

    # A fresh seed() run resets the flag, so the next reseed plants a tag again.
    seeder._honeytoken_planted = False
    seeder._seed_fallback(loc, "CT", "epoch-3")
    assert len([u for u in _stored_retrieve_urls(repo) if u]) == 1


def test_pdf_canary_becomes_a_coherent_dicom_document(tmp_path):
    pdf = tmp_path / "canary.pdf"
    pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")
    source = next(iter(load_fallback_datasets("CT")))
    result = new_honeytoken_injector("https://honey.example", str(pdf))(source)
    repeated = new_honeytoken_injector("https://honey.example", str(pdf))(source)

    assert result.SOPClassUID == EncapsulatedPDFStorage
    assert result.file_meta.MediaStorageSOPClassUID == result.SOPClassUID
    assert result.file_meta.MediaStorageSOPInstanceUID == result.SOPInstanceUID
    assert result.Modality == "DOC"
    assert "PixelData" not in result
    assert result.EncapsulatedDocument.startswith(b"%PDF")
    assert repeated.SOPInstanceUID == result.SOPInstanceUID
    assert repeated.SeriesInstanceUID == result.SeriesInstanceUID

    path = tmp_path / "roundtrip.dcm"
    result.save_as(path, enforce_file_format=True)
    assert dcmread(path).SOPClassUID == EncapsulatedPDFStorage


def test_missing_pdf_canary_fails_loudly(tmp_path):
    with pytest.raises(ValueError, match="Failed to read canary PDF"):
        new_honeytoken_injector(None, str(tmp_path / "missing.pdf"))


def test_failed_tagged_store_retries_canary_on_next_instance():
    class FlakyRepo:
        def __init__(self):
            self.tagged = []

        def store(self, ds, safe=False):
            self.tagged.append(ds.get("RetrieveURL") is not None)
            return QRError("disk full") if len(self.tagged) == 1 else None

    flaky = FlakyRepo()
    seeder = new_seeder(
        flaky, honeytoken=new_honeytoken_injector("https://honey.example", None)
    )
    stored = seeder._seed_fallback(seeder._locations[0], "CT", "epoch")
    assert stored > 0
    assert flaky.tagged[:2] == [True, True]
    assert flaky.tagged.count(True) == 2


def test_osm_cache_dir_env_redirects_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("DICOMHAWK_CACHE_DIR", str(tmp_path))
    assert OsmClient(city="Paris", country="FR")._cache == tmp_path / "osm.json"


def test_osm_cache_defaults_to_home_without_env(monkeypatch):
    monkeypatch.delenv("DICOMHAWK_CACHE_DIR", raising=False)
    assert OsmClient()._cache == Path.home() / ".cache" / "dicomhawk" / "osm.json"


def test_osm_explicit_cache_path_overrides_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DICOMHAWK_CACHE_DIR", str(tmp_path / "env"))
    assert (
        OsmClient(cache_path=str(tmp_path / "explicit.json"))._cache
        == tmp_path / "explicit.json"
    )
