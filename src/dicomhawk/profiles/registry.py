class ModalityRegistry:
    """Central registry for modality personality profiles."""
    
    def __init__(self):
        self._profiles = {}

    def register(self, profile):
        """Register a new profile."""
        name = profile.display_name.strip().lower()
        self._profiles[name] = profile

    def get(self, name: str | None):
        """Retrieve a profile by name."""
        if name is None:
            return None
        return self._profiles.get(name.strip().lower())

    def list_names(self):
        """Return a list of all registered profile names."""
        return sorted(self._profiles.keys())

_registry = ModalityRegistry()

def register_profile(profile):
    _registry.register(profile)

def get_profile(name: str | None):
    return _registry.get(name)

def list_profile_names():
    return _registry.list_names()
