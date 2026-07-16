"""_permissions tests — TCC probes must never raise, only degrade to DENIED."""

import ctypes

from pysar.backend import _permissions


class _FakeIOKit:
    def __init__(self, check=_permissions.GRANTED, request=True):
        self._check = check
        self._request = request
        self.check_calls = 0
        self.request_calls = 0

    def IOHIDCheckAccess(self, kind):
        self.check_calls += 1
        return self._check

    def IOHIDRequestAccess(self, kind):
        self.request_calls += 1
        return self._request


def _with_fake(monkeypatch, fake):
    monkeypatch.setattr(_permissions, "_lib", fake)


def test_status_granted(monkeypatch):
    _with_fake(monkeypatch, _FakeIOKit(check=_permissions.GRANTED))
    assert _permissions.input_monitoring_status() == _permissions.GRANTED


def test_status_unknown_means_never_asked(monkeypatch):
    _with_fake(monkeypatch, _FakeIOKit(check=_permissions.UNKNOWN))
    assert _permissions.input_monitoring_status() == _permissions.UNKNOWN


def test_status_failure_reads_as_denied(monkeypatch):
    class Boom:
        def IOHIDCheckAccess(self, kind):
            raise OSError("no IOKit here")

    _with_fake(monkeypatch, Boom())
    assert _permissions.input_monitoring_status() == _permissions.DENIED


def test_request_returns_current_verdict(monkeypatch):
    fake = _FakeIOKit(request=False)
    _with_fake(monkeypatch, fake)
    assert _permissions.request_input_monitoring() is False
    assert fake.request_calls == 1


def test_request_failure_is_false_not_raise(monkeypatch):
    class Boom:
        def IOHIDRequestAccess(self, kind):
            raise OSError("sandboxed")

    _with_fake(monkeypatch, Boom())
    assert _permissions.request_input_monitoring() is False


def test_iokit_binding_signatures(monkeypatch):
    """The lazy loader must type the two C symbols before first use."""
    calls = {}

    class FakeLib:
        def __getattr__(self, name):
            return calls.setdefault(name, _Sym())

    class _Sym:
        restype = None
        argtypes = None

        def __call__(self, *a):
            return 0

    monkeypatch.setattr(_permissions, "_lib", None)
    monkeypatch.setattr(ctypes, "CDLL", lambda path: FakeLib())
    assert _permissions.input_monitoring_status() == _permissions.GRANTED
    assert calls["IOHIDCheckAccess"].restype is ctypes.c_int
    assert calls["IOHIDRequestAccess"].argtypes == [ctypes.c_int]


def test_ensure_accessibility_missing_framework_is_false(monkeypatch):
    """On CI (or non-mac) the ApplicationServices import fails → False, no raise."""
    import builtins

    real_import = builtins.__import__

    def _no_ax(name, *a, **kw):
        if name == "ApplicationServices":
            raise ImportError("not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_ax)
    assert _permissions.ensure_accessibility() is False
