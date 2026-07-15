from dicomhawk.config import overlay_config


def test_missing_file_returns_defaults(tmp_path):
    defaults = {"a": {"x": 1}, "b": 2}
    cfg = overlay_config(defaults, str(tmp_path / "nope.yaml"))
    assert cfg == defaults
    assert cfg is not defaults  # a copy, not the same dict


def test_empty_file_returns_defaults(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("")
    defaults = {"a": {"x": 1}}
    assert overlay_config(defaults, str(path)) == defaults


def test_merges_dict_sections_per_key(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("a:\n  y: 99\n")
    defaults = {"a": {"x": 1, "y": 2}}
    cfg = overlay_config(defaults, str(path))
    assert cfg["a"] == {"x": 1, "y": 99}


def test_replaces_non_dict_sections_wholesale(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("a:\n  - 9\n")
    defaults = {"a": [1, 2, 3]}
    cfg = overlay_config(defaults, str(path))
    assert cfg["a"] == [9]


def test_does_not_mutate_the_defaults_dict(tmp_path):
    path = tmp_path / "cfg.yaml"
    path.write_text("a:\n  x: 2\n")
    defaults = {"a": {"x": 1}}
    overlay_config(defaults, str(path))
    assert defaults["a"]["x"] == 1
