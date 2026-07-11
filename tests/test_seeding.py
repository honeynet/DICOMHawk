import pytest
from pydicom import dcmread

from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from honeytoken.injector import new_honeytoken_injector
from seeding.seeder import new_seeder


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


def test_seed_with_honeytoken_tags_exactly_one_instance(repo):
    injector = new_honeytoken_injector("https://honey.example.com", None)
    seeder = new_seeder(repo, honeytoken=injector)
    stored = seeder._seed_fallback(seeder._locations[0], "CT", "test-epoch")
    assert stored > 0

    tagged = [url for url in _stored_retrieve_urls(repo) if url is not None]
    assert len(tagged) == 1
    assert tagged[0].startswith("https://honey.example.com/")


def test_seed_run_reset_replants_honeytoken_on_reseed(repo):
    """Without resetting the per-run flag, reseeding the same fixed fallback files
    would silently overwrite the tagged file with an untagged copy (same
    SOPInstanceUIDs -> same on-disk filenames) -- seed() resets the flag every
    run specifically to prevent the bait from disappearing on reseed."""
    injector = new_honeytoken_injector("https://honey.example.com", None)
    seeder = new_seeder(repo, honeytoken=injector)
    loc = seeder._locations[0]

    seeder._seed_fallback(loc, "CT", "epoch-1")
    assert len([u for u in _stored_retrieve_urls(repo) if u]) == 1

    # Reseed without resetting the flag: the previously-tagged file is overwritten
    # by an untagged copy, and nothing new gets tagged -- the bait is gone.
    seeder._seed_fallback(loc, "CT", "epoch-2")
    assert len([u for u in _stored_retrieve_urls(repo) if u]) == 0

    # A fresh seed() run resets the flag, so the next reseed plants a tag again.
    seeder._honeytoken_planted = False
    seeder._seed_fallback(loc, "CT", "epoch-3")
    assert len([u for u in _stored_retrieve_urls(repo) if u]) == 1
