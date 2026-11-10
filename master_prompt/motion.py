"""
Small GSAP-style tweens for tkinter.

GSAP is a JavaScript library, so it can't run in a tkinter window. This gives the same way of working:

    motion = Motion(widget, on_frame=redraw, scale=1.0, glow=0.0)
    motion.to("scale", 1.08, 0.18, "power2.out")
    motion.from_to("spin", 0, 360, 0.8, "none", repeat=-1)

A new tween on a value replaces the running one and starts from where the value is now
(GSAP's overwrite: "auto"), so hovering in and out quickly never jumps.
All values are redrawn together, once per frame, by a single ticker.
"""
from __future__ import annotations

import math
import time
from typing import Callable

Ease = Callable[[float], float]


def _power_out(p: float) -> Ease:
    return lambda t: 1 - (1 - t) ** p


def _power_in(p: float) -> Ease:
    return lambda t: t ** p


def _power_in_out(p: float) -> Ease:
    return lambda t: (2 ** (p - 1)) * t ** p if t < 0.5 else 1 - ((-2 * t + 2) ** p) / 2


def back_out(overshoot: float = 1.7) -> Ease:
    c3 = overshoot + 1
    return lambda t: 1 + c3 * (t - 1) ** 3 + overshoot * (t - 1) ** 2


def elastic_out(amplitude: float = 1.0, period: float = 0.4) -> Ease:
    def ease(t: float) -> float:
        if t <= 0 or t >= 1:
            return float(t >= 1)
        a = max(1.0, amplitude)
        s = period / (2 * math.pi) * math.asin(1 / a)
        return a * 2 ** (-10 * t) * math.sin((t - s) * (2 * math.pi) / period) + 1
    return ease


EASES: dict[str, Ease] = {
    "none": lambda t: t,
    "linear": lambda t: t,
    "power1.in": _power_in(2), "power1.out": _power_out(2), "power1.inOut": _power_in_out(2),
    "power2.in": _power_in(3), "power2.out": _power_out(3), "power2.inOut": _power_in_out(3),
    "power3.in": _power_in(4), "power3.out": _power_out(4), "power3.inOut": _power_in_out(4),
    "sine.inOut": lambda t: -(math.cos(math.pi * t) - 1) / 2,
    "expo.out": lambda t: 1.0 if t >= 1 else 1 - 2 ** (-10 * t),
    "back.out": back_out(),
    "elastic.out": elastic_out(),
}


def get_ease(ease: str | Ease) -> Ease:
    if callable(ease):
        return ease
    try:
        return EASES[ease]
    except KeyError:
        raise ValueError(f"Unknown ease: {ease!r}") from None


class Tween:
    """One value moving from start to end. Made by Motion.to / Motion.from_to."""

    def __init__(self, key: str, start: float, end: float, duration: float, ease: Ease, began: float, *,
                 repeat: int = 0, yoyo: bool = False, on_complete: Callable[[], None] | None = None):
        self.key, self.start, self.end = key, start, end
        self.duration = max(0.0, duration)
        self.ease = ease
        self.began = began
        self.repeat, self.yoyo = repeat, yoyo
        self.on_complete = on_complete
        self.done = False

    def value_at(self, now: float) -> float:
        """The value at this moment. Sets done once the last repeat has finished."""
        if self.duration == 0:
            self.done = True
            return self.end
        elapsed = max(0.0, now - self.began)
        cycle, local = divmod(elapsed, self.duration)
        if self.repeat >= 0 and cycle > self.repeat:
            self.done = True
            cycle, local = self.repeat, self.duration
        t = local / self.duration
        if self.yoyo and int(cycle) % 2 == 1:
            t = 1 - t
        return self.start + (self.end - self.start) * self.ease(t)


class Motion:
    """Animated values for one tkinter widget, redrawn together on each frame."""

    FRAME_MS = 16  # about 60 frames a second

    def __init__(self, widget, on_frame: Callable[[], None], *, clock: Callable[[], float] = time.perf_counter,
                 **values: float):
        self.widget = widget
        self.on_frame = on_frame
        self.clock = clock
        self.values: dict[str, float] = dict(values)
        self._tweens: dict[str, Tween] = {}
        self._job = None

    def __getitem__(self, key: str) -> float:
        return self.values[key]

    def set(self, key: str, value: float) -> None:
        """Jump to a value now (gsap.set). Stops any tween on it."""
        self._tweens.pop(key, None)
        self.values[key] = float(value)
        self._request()

    def to(self, key: str, value: float, duration: float, ease: str | Ease = "power2.out", **kw) -> Tween:
        return self.from_to(key, self.values.get(key, 0.0), value, duration, ease, **kw)

    def from_to(self, key: str, start: float, end: float, duration: float, ease: str | Ease = "power2.out", *,
                delay: float = 0.0, repeat: int = 0, yoyo: bool = False,
                on_complete: Callable[[], None] | None = None) -> Tween:
        """repeat=-1 repeats forever; yoyo plays every other repeat backwards."""
        tween = Tween(key, float(start), float(end), duration, get_ease(ease), self.clock() + delay,
                      repeat=repeat, yoyo=yoyo, on_complete=on_complete)
        self._tweens[key] = tween
        if delay <= 0:
            self.values[key] = float(start)
        self._request()
        return tween

    def kill(self, key: str | None = None) -> None:
        """Stop tweens where they are: one value, or all of them."""
        if key is None:
            self._tweens.clear()
        else:
            self._tweens.pop(key, None)

    def active(self, key: str) -> bool:
        return key in self._tweens

    def step(self) -> None:
        """Advance every tween to now and draw one frame. The ticker calls this."""
        now = self.clock()
        finished = []
        for key, tween in list(self._tweens.items()):
            if now < tween.began:
                continue
            self.values[key] = tween.value_at(now)
            if tween.done:
                finished.append(tween)
        for tween in finished:
            if self._tweens.get(tween.key) is tween:
                del self._tweens[tween.key]
        self.on_frame()
        for tween in finished:  # after drawing, so a callback can start the next tween
            if tween.on_complete:
                tween.on_complete()

    def _request(self) -> None:
        if self._job is None:
            try:
                self._job = self.widget.after(self.FRAME_MS, self._tick)
            except Exception:  # the widget is gone
                self._job = None

    def _tick(self) -> None:
        self._job = None
        self.step()
        if self._tweens:
            self._request()
