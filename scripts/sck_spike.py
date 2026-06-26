"""Spike: prove ScreenCaptureKit delivers system audio (+ mic) into Python.

Captures ~6 s of system audio and microphone via one SCStream, decodes the
CMSampleBuffers to float32 PCM, prints the real on-the-wire format, and writes
/tmp/sck_system.wav + /tmp/sck_mic.wav so we can listen and confirm.

Throwaway — not shipped. Run: venv/bin/python scripts/sck_spike.py
"""

import threading
import time
import wave

import CoreMedia as CM
import libdispatch
import numpy as np
import objc
import ScreenCaptureKit as SC
from Foundation import NSDate, NSObject, NSRunLoop

DURATION = 6.0
kIsFloat = 1 << 0
kIsNonInterleaved = 1 << 5


_dbg = {"done": False}


def asbd_of(sbuf):
    """Return (sample_rate, channels, bits, flags) from a buffer's ASBD.

    PyObjC hands the AudioStreamBasicDescription back as a plain tuple in the C
    struct's field order: (mSampleRate, mFormatID, mFormatFlags, mBytesPerPacket,
    mFramesPerPacket, mBytesPerFrame, mChannelsPerFrame, mBitsPerChannel, ...)."""
    fmt = CM.CMSampleBufferGetFormatDescription(sbuf)
    a = CM.CMAudioFormatDescriptionGetStreamBasicDescription(fmt)
    if not _dbg["done"]:
        _dbg["done"] = True
        print("ASBD repr:", type(a).__name__, repr(a))
    if isinstance(a, (tuple, list)):
        return float(a[0]), int(a[6]), int(a[7]), int(a[2])
    return (
        float(a.mSampleRate),
        int(a.mChannelsPerFrame),
        int(a.mBitsPerChannel),
        int(a.mFormatFlags),
    )


def pcm_from_sample_buffer(sbuf):
    """CMSampleBuffer (LPCM) -> (mono float32 ndarray, sample_rate). Handles both
    interleaved and non-interleaved float32, mono or multi-channel."""
    sr, ch, _bits, flags = asbd_of(sbuf)

    bb = CM.CMSampleBufferGetDataBuffer(sbuf)
    if bb is None:
        return np.zeros(0, np.float32), sr
    length = int(CM.CMBlockBufferGetDataLength(bb))
    status, data = CM.CMBlockBufferCopyDataBytes(bb, 0, length, None)
    if status != 0 or data is None:
        return np.zeros(0, np.float32), sr

    if not (flags & kIsFloat):
        return np.zeros(0, np.float32), sr  # spike only handles float
    arr = np.frombuffer(bytes(data), dtype=np.float32)
    if ch <= 1:
        return arr.copy(), sr
    if flags & kIsNonInterleaved:
        # planar: [c0 c0 ... ][c1 c1 ...]; average the channels we actually got
        per = arr.size // ch
        planes = arr[: per * ch].reshape(ch, per)
        return planes.mean(axis=0).astype(np.float32), sr
    # interleaved: [f0c0 f0c1 f1c0 ...]
    frames = arr.size // ch
    return arr[: frames * ch].reshape(frames, ch).mean(axis=1).astype(np.float32), sr


class Out(NSObject):
    def init(self):
        self = objc.super(Out, self).init()
        self.sys_chunks = []
        self.mic_chunks = []
        self.sys_sr = 48000
        self.mic_sr = 48000
        self.printed = set()
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, kind):
        if not CM.CMSampleBufferIsValid(sbuf):
            return
        mono, sr = pcm_from_sample_buffer(sbuf)
        tag = (
            "audio"
            if kind == SC.SCStreamOutputTypeAudio
            else ("mic" if kind == SC.SCStreamOutputTypeMicrophone else f"other({kind})")
        )
        if tag not in self.printed:
            self.printed.add(tag)
            sr2, ch2, bits2, flags2 = asbd_of(sbuf)
            print(
                f"[{tag}] sr={sr2} ch={ch2} bits={bits2} flags={flags2:#x} "
                f"-> {mono.size} mono samples/buf"
            )
        if kind == SC.SCStreamOutputTypeAudio:
            self.sys_sr = sr
            self.sys_chunks.append(mono)
        elif kind == SC.SCStreamOutputTypeMicrophone:
            self.mic_sr = sr
            self.mic_chunks.append(mono)

    def stream_didStopWithError_(self, stream, error):
        print("stream stopped:", error)


def write_wav(path, mono, sr):
    if mono is None or mono.size == 0:
        print("no data for", path)
        return
    peak = float(np.max(np.abs(mono))) or 1.0
    i16 = (np.clip(mono / peak * 0.95, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(i16.tobytes())
    print(f"wrote {path}  {mono.size / sr:.1f}s  peak={peak:.4f}")


def main():
    content_box = {}
    done = threading.Event()

    def got(content, error):
        content_box["c"] = content
        content_box["e"] = error
        done.set()

    SC.SCShareableContent.getShareableContentWithCompletionHandler_(got)
    deadline = NSDate.dateWithTimeIntervalSinceNow_(5.0)
    while not done.is_set() and NSDate.date().compare_(deadline) < 0:
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            "kCFRunLoopDefaultMode", NSDate.dateWithTimeIntervalSinceNow_(0.05)
        )
    content = content_box.get("c")
    if content is None:
        print("no shareable content:", content_box.get("e"))
        return
    displays = content.displays()
    if not displays:
        print("no displays")
        return
    display = displays[0]

    cfg = SC.SCStreamConfiguration.alloc().init()
    cfg.setCapturesAudio_(True)
    cfg.setExcludesCurrentProcessAudio_(True)
    cfg.setCaptureMicrophone_(True)
    cfg.setWidth_(2)
    cfg.setHeight_(2)

    filt = SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
    out = Out.alloc().init()
    stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, out)

    q = libdispatch.dispatch_queue_create(b"pysar.sck.spike", None)
    for kind in (SC.SCStreamOutputTypeAudio, SC.SCStreamOutputTypeMicrophone):
        ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(out, kind, q, None)
        print("addStreamOutput", kind, "ok" if ok else f"FAIL {err}")

    start_done = threading.Event()
    start_err = {}

    def started(error):
        start_err["e"] = error
        start_done.set()

    stream.startCaptureWithCompletionHandler_(started)
    start_done.wait(5)
    print("start error:", start_err.get("e"))

    print(f"capturing {DURATION}s — play some audio + talk...")
    time.sleep(DURATION)

    stop_done = threading.Event()
    stream.stopCaptureWithCompletionHandler_(lambda e: stop_done.set())
    stop_done.wait(5)

    sys_all = np.concatenate(out.sys_chunks) if out.sys_chunks else np.zeros(0, np.float32)
    mic_all = np.concatenate(out.mic_chunks) if out.mic_chunks else np.zeros(0, np.float32)
    print(f"system: {sys_all.size} samples @ {out.sys_sr}  | mic: {mic_all.size} @ {out.mic_sr}")
    write_wav("/tmp/sck_system.wav", sys_all, out.sys_sr)
    write_wav("/tmp/sck_mic.wav", mic_all, out.mic_sr)


if __name__ == "__main__":
    main()
