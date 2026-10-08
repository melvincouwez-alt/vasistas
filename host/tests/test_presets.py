from vasistas import power


def test_presets_round_trip():
    assert power.mode_index({}) == 1  # config vierge : Équilibré
    for i in range(len(power.MODES)):
        cfg = {"user": "x"}
        power.apply_mode(cfg, i)
        assert power.mode_index(cfg) == i
        assert cfg["user"] == "x"
    cfg = {}
    power.apply_mode(cfg, 2)
    cfg["timer"] = "never"
    assert power.mode_index(cfg) is None


def test_apply_preset_restart_keys():
    assert power.apply_mode({}, 1) == []
    assert power.apply_mode({}, 0) == ["vsync"]
    assert power.machine_index({}) == 1 and power.machine_index({"resources": "performance"}) == 2


def test_capture_message():
    assert power.capture_message({}, "battery") == {"t": "capture", "occluded_ms": 1000, "timer_ms": 0}
    assert power.capture_message({}, "balanced")["timer_ms"] == 1
    assert power.capture_message({"timer": "always"}, "battery")["timer_ms"] == 1
    assert power.capture_message({"timer": "never", "occluded_ms": 250}, "performance") == \
        {"t": "capture", "occluded_ms": 250, "timer_ms": 0}
    assert power.capture_message({"occluded_ms": 7}, "balanced")["occluded_ms"] == 500
