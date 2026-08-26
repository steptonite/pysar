"""CoreAudio probes (26.08.2026).

These talk to the real audio system, so they assert on CONTRACT, not on values:
which device this Mac is using and whether something is playing while the suite
runs are both none of the test's business. What must hold is that a probe either
answers with the right shape or says None — because the watchdog treats None as
"no evidence" and anything else as grounds to restart a live capture."""

from pysar import audiodev


def test_fourcc_matches_the_apple_constants():
    # kAudioHardwarePropertyDefaultOutputDevice / …DeviceIsRunningSomewhere
    assert audiodev._fourcc("dOut") == 0x644F7574
    assert audiodev._fourcc("gone") == 0x676F6E65
    assert audiodev._fourcc("glob") == 0x676C6F62


def test_default_output_device_is_an_id_or_none():
    dev = audiodev.default_output_device()
    assert dev is None or (isinstance(dev, int) and dev > 0)


def test_output_is_running_is_a_bool_or_none():
    val = audiodev.output_is_running()
    assert val is None or isinstance(val, bool)


def test_unknown_device_is_reported_as_none_not_as_zero():
    """kAudioObjectUnknown is 0. Returning it as an ID would make the watchdog
    compare 0 against a real device and 'detect' a switch on every tick."""
    assert audiodev._u32(0, audiodev._DEFAULT_OUTPUT) in (None, 0)
    assert audiodev.default_output_device() != 0


def test_probes_degrade_to_none_when_coreaudio_is_unavailable(monkeypatch):
    """No CoreAudio (a non-mac CI box, a stripped runtime) must be survivable:
    the app loses the extra witness, it does not lose the meeting."""
    monkeypatch.setattr(audiodev, "_lib", lambda: None)
    assert audiodev._u32(1, audiodev._DEFAULT_OUTPUT) is None
    assert audiodev.default_output_device() is None
    assert audiodev.output_is_running() is None


def test_output_is_running_accepts_an_explicit_device():
    """The watchdog already knows the device — it must not pay for a second
    lookup on every 3-second tick."""
    calls = []
    monkey = audiodev._u32

    def spy(obj, sel):
        calls.append((obj, sel))
        return monkey(obj, sel)

    audiodev._u32 = spy
    try:
        audiodev.output_is_running(42)
    finally:
        audiodev._u32 = monkey
    assert calls == [(42, audiodev._RUNNING_SOMEWHERE)]
