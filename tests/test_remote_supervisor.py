"""IR remote supervisor — pure-logic tests (Mac).

Covers the testable seams that decide behaviour: the KEY_* -> intent map, the
intent -> web-POST plan, the forwarder's dispatch (incl. the state-dependent
ARM/POWER toggles), and the listener's key-event handling (autorepeat gating).
evdev is never imported here (the loop's reads are Pi-only).
"""
from config import RemoteConfig
from app.remote_supervisor import (
    IntentForwarder,
    RemoteSupervisor,
    build_intent_map,
    http_plan,
)


# --- build_intent_map -------------------------------------------------------

def test_intent_map_default_bindings():
    m = build_intent_map(RemoteConfig())
    assert m["KEY_STOP"] == "ESTOP"
    assert m["KEY_CHANNELUP"] == "ARM_TOGGLE"
    assert m["KEY_MODE"] == "TOGGLE_FIRE_ENABLE"
    assert m["KEY_HOMEPAGE"] == "HOME"
    assert m["KEY_PLAYPAUSE"] == "FIRE"
    assert m["KEY_NUMERIC_0"] == "POWER_TOGGLE"
    assert m["KEY_PREVIOUS"] == "JOG_PAN_NEG"
    assert m["KEY_NEXT"] == "JOG_PAN_POS"
    assert m["KEY_VOLUMEUP"] == "JOG_TILT_POS"
    assert m["KEY_VOLUMEDOWN"] == "JOG_TILT_NEG"


def test_intent_map_drops_unbound_keys():
    cfg = RemoteConfig(key_power="")  # operator disabled the power key
    m = build_intent_map(cfg)
    assert "" not in m
    assert "POWER_TOGGLE" not in m.values()


# --- http_plan (pure intent -> POST steps) ----------------------------------

def test_http_plan_estop_is_pump_off_then_disarm():
    assert http_plan("ESTOP") == [("/api/cmd", "pump_off"), ("/api/cmd", "disarm")]


def test_http_plan_oneshots():
    assert http_plan("TOGGLE_FIRE_ENABLE") == [("/api/cmd", "toggle_fire")]
    assert http_plan("HOME") == [("/api/cmd", "center")]
    assert http_plan("FIRE") == [("/api/cmd", "fire_now")]


def test_http_plan_jog_directions():
    assert http_plan("JOG_PAN_NEG") == [("/api/control-cmd", "left")]
    assert http_plan("JOG_PAN_POS") == [("/api/control-cmd", "right")]
    assert http_plan("JOG_TILT_POS") == [("/api/control-cmd", "up")]
    assert http_plan("JOG_TILT_NEG") == [("/api/control-cmd", "down")]


def test_http_plan_imperative_intents_have_no_static_plan():
    # ARM_TOGGLE / POWER_* need a live state read -> handled in dispatch(), not here.
    for intent in ("ARM_TOGGLE", "POWER_TOGGLE", "POWER_ON", "POWER_OFF", "NOPE"):
        assert http_plan(intent) == []


# --- IntentForwarder.dispatch ----------------------------------------------

class _RecordingForwarder(IntentForwarder):
    """Captures POST / systemctl side effects instead of performing them."""

    def __init__(self, cfg, state="Disabled", active=False):
        super().__init__(cfg)
        self.posts = []
        self.systemctl_calls = []
        self._state = state
        self._active = active

    def _post(self, path, body):
        self.posts.append((path, body))

    def _systemctl(self, action):
        self.systemctl_calls.append(action)

    def _turret_state(self):
        return self._state

    def _unit_active(self):
        return self._active


def test_dispatch_estop_posts_both_steps():
    fwd = _RecordingForwarder(RemoteConfig())
    fwd.dispatch("ESTOP")
    assert fwd.posts == [("/api/cmd", "pump_off"), ("/api/cmd", "disarm")]


def test_dispatch_arm_toggle_uses_live_state():
    armed = _RecordingForwarder(RemoteConfig(), state="Enabled")
    armed.dispatch("ARM_TOGGLE")
    assert armed.posts == [("/api/cmd", "disarm")]

    disarmed = _RecordingForwarder(RemoteConfig(), state="Disabled")
    disarmed.dispatch("ARM_TOGGLE")
    assert disarmed.posts == [("/api/cmd", "arm")]


def test_dispatch_arm_toggle_defaults_to_arm_when_app_unreachable():
    fwd = _RecordingForwarder(RemoteConfig(), state=None)  # app down
    fwd.dispatch("ARM_TOGGLE")
    assert fwd.posts == [("/api/cmd", "arm")]


def test_dispatch_power_toggle_follows_unit_state():
    running = _RecordingForwarder(RemoteConfig(), active=True)
    running.dispatch("POWER_TOGGLE")
    assert running.systemctl_calls == ["stop"]

    stopped = _RecordingForwarder(RemoteConfig(), active=False)
    stopped.dispatch("POWER_TOGGLE")
    assert stopped.systemctl_calls == ["start"]


def test_dispatch_power_explicit():
    fwd = _RecordingForwarder(RemoteConfig())
    fwd.dispatch("POWER_ON")
    fwd.dispatch("POWER_OFF")
    assert fwd.systemctl_calls == ["start", "stop"]


def test_base_url_from_config():
    fwd = IntentForwarder(RemoteConfig(forward_host="10.0.0.5", forward_port=9000))
    assert fwd.base_url == "http://10.0.0.5:9000"


# --- RemoteSupervisor._handle (autorepeat gating) ---------------------------

class _FakeEcodes:
    EV_KEY = 1

    def __init__(self, keymap):
        self.KEY = keymap


class _FakeEvent:
    def __init__(self, code, value, etype=1):
        self.code = code
        self.value = value
        self.type = etype


class _CountingForwarder:
    def __init__(self):
        self.dispatched = []

    def dispatch(self, intent):
        self.dispatched.append(intent)


def _supervisor():
    fwd = _CountingForwarder()
    sup = RemoteSupervisor(RemoteConfig(), forwarder=fwd)
    return sup, fwd


def test_handle_oneshot_fires_on_keydown_only():
    sup, fwd = _supervisor()
    ecodes = _FakeEcodes({30: "KEY_STOP"})  # ESTOP is a one-shot
    sup._handle(_FakeEvent(30, 1), ecodes)  # key down
    sup._handle(_FakeEvent(30, 2), ecodes)  # autorepeat (ignored)
    sup._handle(_FakeEvent(30, 0), ecodes)  # key up (ignored)
    assert fwd.dispatched == ["ESTOP"]


def test_handle_jog_repeats_on_hold():
    sup, fwd = _supervisor()
    ecodes = _FakeEcodes({40: "KEY_NEXT"})  # JOG_PAN_POS slews while held
    sup._handle(_FakeEvent(40, 1), ecodes)  # down
    sup._handle(_FakeEvent(40, 2), ecodes)  # held -> another step
    sup._handle(_FakeEvent(40, 2), ecodes)  # held -> another step
    sup._handle(_FakeEvent(40, 0), ecodes)  # up
    assert fwd.dispatched == ["JOG_PAN_POS", "JOG_PAN_POS", "JOG_PAN_POS"]


def test_handle_unmapped_key_is_ignored():
    sup, fwd = _supervisor()
    ecodes = _FakeEcodes({99: "KEY_NUMERIC_9"})  # not in the default map
    sup._handle(_FakeEvent(99, 1), ecodes)
    assert fwd.dispatched == []
