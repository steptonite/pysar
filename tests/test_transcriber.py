"""transcriber.py tests — no real HTTP calls, requests is mocked throughout."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from pysar.transcriber import is_alive, transcribe, transcribe_segments


def _wav_bytes() -> bytes:
    """A minimal valid WAV header. Contents don't matter — requests is mocked."""
    return b"RIFF" + b"\x00" * 40


def test_transcribe_connection_error_returns_friendly_message():
    with patch("pysar.transcriber.requests.post") as mock_post:
        mock_post.side_effect = requests.exceptions.ConnectionError()
        text, err = transcribe(_wav_bytes(), mode="ru")

    assert text is None
    assert err is not None
    assert "make whisper" in err


def test_transcribe_success_returns_text():
    with patch("pysar.transcriber.requests.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"text": "  hello, world  "}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        text, err = transcribe(_wav_bytes(), mode="en")

    assert err is None
    assert text == "hello, world"


def test_transcribe_empty_text_returns_none_none():
    with patch("pysar.transcriber.requests.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"text": "   "}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        text, err = transcribe(_wav_bytes(), mode="ru")

    assert text is None
    assert err is None


def test_transcribe_segments_requests_verbose_json_and_preserves_text():
    with patch("pysar.transcriber.requests.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {
            "segments": [
                {
                    "start": 1.25,
                    "end": 2.5,
                    "text": "  exact words  ",
                    "no_speech_prob": 0.15,
                }
            ]
        }
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        segments, err = transcribe_segments(_wav_bytes(), mode="uk", prompt="names")

    assert err is None
    assert segments == [{"start": 1.25, "end": 2.5, "text": "exact words", "no_speech_prob": 0.15}]
    assert mock_post.call_args.kwargs["data"]["response_format"] == "verbose_json"
    assert mock_post.call_args.kwargs["data"]["prompt"] == "names"


def test_transcribe_segments_unstructured_response_signals_safe_fallback():
    with patch("pysar.transcriber.requests.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"text": "all words"}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp
        segments, err = transcribe_segments(_wav_bytes(), mode="en")
    assert segments is None and err is None


@pytest.mark.parametrize("mode", ["ru", "en", "translate", "ja", "ar"])
def test_transcribe_passes_mode_specific_language(mode):
    with patch("pysar.transcriber.requests.post") as mock_post:
        resp = MagicMock()
        resp.json.return_value = {"text": "ok"}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        transcribe(_wav_bytes(), mode=mode)

        sent_data = mock_post.call_args.kwargs["data"]
        assert "language" in sent_data, f"Mode {mode} did not forward `language`"


def test_is_alive_returns_false_on_connection_error():
    with patch("pysar.transcriber.requests.get") as mock_get:
        mock_get.side_effect = requests.exceptions.ConnectionError()
        assert is_alive() is False


def test_is_alive_returns_true_on_any_response():
    with patch("pysar.transcriber.requests.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        assert is_alive() is True
