"""IR remote key-map building (pure)."""
from config import RemoteConfig
from app.remote import RemoteActions, build_key_map


class RecordingActions(RemoteActions):
    def __init__(self):
        self.calls = []

    def toggle_arm(self):
        self.calls.append("arm")

    def toggle_fire_enabled(self):
        self.calls.append("fire")

    def center(self):
        self.calls.append("center")

    def jog(self, axis, direction):
        self.calls.append(("jog", axis, direction))


def test_key_map_dispatches_actions():
    cfg = RemoteConfig()
    actions = RecordingActions()
    km = build_key_map(cfg, actions)
    km[cfg.key_toggle_arm]()
    km[cfg.key_center]()
    km[cfg.key_pan_left]()
    km[cfg.key_tilt_up]()
    assert actions.calls == ["arm", "center", ("jog", "pan", -1), ("jog", "tilt", +1)]


def test_key_map_covers_all_configured_keys():
    cfg = RemoteConfig()
    km = build_key_map(cfg, RecordingActions())
    for key in (cfg.key_toggle_arm, cfg.key_enable_fire, cfg.key_center,
                cfg.key_pan_left, cfg.key_pan_right, cfg.key_tilt_up, cfg.key_tilt_down):
        assert key in km
