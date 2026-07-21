import gzip

import pytest

from dicomhawk.storage import new_store


@pytest.fixture
def storage(tmp_path):
    return new_store(str(tmp_path / "traces"))


def test_jail_returns_the_right_directory(storage):
    assert storage.jail(safe=True) == str(storage.storage_dir)
    assert storage.jail(safe=False) == str(storage.quarantine_dir)


def test_path_for_resolves_under_the_right_root(storage):
    safe_path = storage.path_for(True, "1.2.3.dcm")
    quarantine_path = storage.path_for(False, "1.2.3.dcm")
    assert safe_path.parent == storage.storage_dir
    assert quarantine_path.parent == storage.quarantine_dir


def test_path_for_rejects_absolute_paths(storage):
    with pytest.raises(ValueError, match="Absolute paths not allowed"):
        storage.path_for(True, "/etc/passwd")


def test_path_for_rejects_traversal_escapes(storage):
    with pytest.raises(ValueError, match="Path traversal detected"):
        storage.path_for(True, "../../../etc/passwd")


def test_is_quarantined_true_only_for_quarantine_dir(storage):
    quarantine_file = storage.quarantine_dir / "a.dcm"
    storage_file = storage.storage_dir / "b.dcm"
    unrelated = "/tmp/not-in-jail/c.dcm"

    assert storage.is_quarantined(str(quarantine_file)) is True
    assert storage.is_quarantined(str(storage_file)) is False
    assert storage.is_quarantined(unrelated) is False


def test_temp_cleans_up_after_use(storage):
    with storage.temp() as path:
        path.write_bytes(b"raw payload")
        assert path.exists()
    assert not path.exists()


def test_temp_cleanup_is_safe_even_if_never_written(storage):
    with storage.temp() as path:
        pass  # never wrote to it
    assert not path.exists()


def test_compress_gzips_content_under_traces_dir(storage):
    with storage.temp() as path:
        path.write_bytes(b"attacker payload bytes")
        compressed = storage.compress(path)

    assert compressed.parent == storage.traces_dir
    assert compressed.suffix == ".gz"
    with gzip.open(compressed, "rb") as f:
        assert f.read() == b"attacker payload bytes"


def test_capture_preserves_exact_bytes_with_unique_names(storage):
    first = storage.capture(b"first\x00payload", suffix=".stow")
    second = storage.capture(b"second", suffix=".stow")
    assert first != second
    assert gzip.decompress(first.read_bytes()) == b"first\x00payload"
    assert gzip.decompress(second.read_bytes()) == b"second"


def test_capture_stream_does_not_require_buffering_whole_payload(storage):
    with storage.capture_stream(suffix=".request") as output:
        output.write(b"part-one")
        output.write(b"part-two")
    captured = list(storage.traces_dir.glob("*.request.gz"))
    assert len(captured) == 1
    assert gzip.decompress(captured[0].read_bytes()) == b"part-onepart-two"
