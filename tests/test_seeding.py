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
from seeding.names import _age_at, _patch_location, faker_pools
from seeding.osm import OsmClient
from seeding.procedures import load_procedures, procedure_pool
from seeding.seeder import new_seeder

_LOCATIONS = load_locations(None)
_PROCEDURES = load_procedures(None)
_POOLS = (("Patient^Male",), ("Patient^Female",), ("Doctor^One",))


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


def test_patient_age_is_recomputed_from_the_patched_dates():
    """Source ages come from TCIA and contradict the DOB and study date we overwrite."""
    source = next(iter(load_fallback_datasets("CT")))
    assert source.PatientAge == "075Y"

    patched = _patch_location(deepcopy(source), _LOCATIONS[0], *_POOLS, "test-epoch")

    assert patched.PatientAge == _age_at(patched.PatientBirthDate, patched.StudyDate)
    assert patched.PatientAge != source.PatientAge


def test_patient_age_is_not_invented_when_the_source_lacks_it():
    source = deepcopy(next(iter(load_fallback_datasets("CT"))))
    del source.PatientAge

    patched = _patch_location(source, _LOCATIONS[0], *_POOLS, "test-epoch")

    assert "PatientAge" not in patched


def test_study_description_is_generated_when_absent():
    source = next(iter(load_fallback_datasets("CT")))
    assert not getattr(source, "StudyDescription", "")

    patched = _patch_location(
        deepcopy(source), _LOCATIONS[0], *_POOLS, "test-epoch", _PROCEDURES
    )

    assert patched.StudyDescription
    assert patched.StudyDescription in procedure_pool(
        _PROCEDURES, patched.Modality, patched.BodyPartExamined
    )


def test_study_description_is_stable_for_a_study_and_rotates_by_epoch():
    """A description that changed on reload would expose the worklist as generated."""
    source = next(iter(load_fallback_datasets("CT")))
    first = _patch_location(
        deepcopy(source), _LOCATIONS[0], *_POOLS, "epoch-a", _PROCEDURES
    ).StudyDescription
    again = _patch_location(
        deepcopy(source), _LOCATIONS[0], *_POOLS, "epoch-a", _PROCEDURES
    ).StudyDescription
    rotated = {
        _patch_location(
            deepcopy(source), _LOCATIONS[0], *_POOLS, f"epoch-{n}", _PROCEDURES
        ).StudyDescription
        for n in range(12)
    }

    assert first == again
    assert len(rotated) > 1


def test_study_description_never_overwrites_a_supplied_one():
    source = deepcopy(next(iter(load_fallback_datasets("CT"))))
    source.StudyDescription = "ATTACKER SUPPLIED"

    patched = _patch_location(source, _LOCATIONS[0], *_POOLS, "test-epoch", _PROCEDURES)

    assert patched.StudyDescription == "ATTACKER SUPPLIED"


def test_study_description_is_absent_without_a_procedure_pool():
    source = next(iter(load_fallback_datasets("CT")))

    patched = _patch_location(deepcopy(source), _LOCATIONS[0], *_POOLS, "test-epoch")

    assert not getattr(patched, "StudyDescription", "")


def test_procedure_pool_matches_the_body_part_it_is_given():
    chest = procedure_pool(_PROCEDURES, "CT", "CHEST")
    head = procedure_pool(_PROCEDURES, "CT", "HEAD")

    assert chest != head
    assert all("CHEST" in entry.upper() for entry in chest)
    assert all("HEAD" in entry.upper() or "NECK" in entry.upper() for entry in head)


@pytest.mark.parametrize(
    "modality,body_part",
    [("CT", "NO_SUCH_PART"), ("NO_SUCH_MODALITY", "CHEST"), ("", ""), (None, None)],
)
def test_procedure_pool_falls_back_for_unknown_keys(modality, body_part):
    assert procedure_pool(_PROCEDURES, modality, body_part)


def test_procedure_pool_lookup_is_case_insensitive():
    assert procedure_pool(_PROCEDURES, "ct", "chest") == procedure_pool(
        _PROCEDURES, "CT", "CHEST"
    )


def test_procedures_file_without_a_default_is_rejected(tmp_path):
    broken = tmp_path / "procedures.json"
    broken.write_text('{"CT": {"CHEST": ["CT CHEST"]}}')

    # A malformed custom file must degrade to the packaged defaults, not crash the seeder.
    assert load_procedures(str(broken)) == _PROCEDURES


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


class _StubClient:
    """Stands in for TCIA so the progress contract can be checked without downloading."""

    def __init__(self, series_count):
        self.series = [{"SeriesInstanceUID": f"1.2.{n}"} for n in range(series_count)]

    def get_series(self, collection, modality):
        return self.series

    def get_sop_uids(self, series_uid):
        return []

    def download_image(self, series_uid, sop_uid):
        return None


def test_seed_reports_progress_once_per_series(repo):
    # A series is minutes of silent downloading; without this the CLI looks hung.
    seeder = new_seeder(repo)
    seeder._client = _StubClient(5)
    seen = []

    seeder.seed("COLL", 3, 2, "CT", "epoch", on_progress=lambda *args: seen.append(args))

    assert [index for index, _total, _stored in seen] == [1, 2, 3]
    assert {total for _index, total, _stored in seen} == {3}


def test_seed_progress_totals_never_exceed_the_available_series(repo):
    # max_series is an upper bound, so a shorter collection must not report "1/3".
    seeder = new_seeder(repo)
    seeder._client = _StubClient(2)
    seen = []

    seeder.seed("COLL", 3, 2, "CT", "epoch", on_progress=lambda *args: seen.append(args))

    assert {total for _index, total, _stored in seen} == {2}


def test_seed_without_a_progress_callback_still_works(repo):
    seeder = new_seeder(repo)
    seeder._client = _StubClient(2)
    assert seeder.seed("COLL", 1, 1, "CT", "epoch") > 0


def test_name_pools_are_stable_for_one_locale_and_epoch():
    # Unseeded Faker gave a fresh pool per call, so one PatientID drifted to a new name each re-seed.
    assert faker_pools("en_US", "2026W31") == faker_pools("en_US", "2026W31")


def test_name_pools_rotate_with_the_epoch():
    assert faker_pools("en_US", "2026W31") != faker_pools("en_US", "2026W32")


def _stored_identities(repo):
    return sorted(
        (str(ds.PatientID), str(ds.PatientName))
        for ds in (
            dcmread(f, force=True) for f in repo.storage.storage_dir.iterdir()
        )
    )


def test_reseeding_keeps_one_patient_id_on_one_name(repo):
    seeder = new_seeder(repo)
    loc = seeder._locations[0]
    seeder._seed_fallback(loc, "CT", "2026W31")
    first = _stored_identities(repo)

    seeder._seed_fallback(loc, "CT", "2026W31")
    assert _stored_identities(repo) == first
    assert len({pid for pid, _name in first}) == len(set(first))
