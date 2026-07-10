import yaml


def overlay_config(defaults: dict, path: str) -> dict:
    """Overlay a YAML file onto defaults per-section; a missing/partial file returns defaults."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in defaults.items()}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return cfg
    for section, values in data.items():
        if isinstance(values, dict):
            cfg.setdefault(section, {}).update(values)
        else:
            cfg[section] = values
    return cfg
