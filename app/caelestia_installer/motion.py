"""Nexus-style page motion, translated from Caelestia's QML to GTK.

Reference: modules/nexus/Pages.qml and plugin/src/Caelestia/Config/tokens.hpp
in caelestia-dots/shell. Fade out (DefaultEffects, 200ms), then fade/translate
in (SlowEffects, 300ms, +/-28px). No spring or perpetual animation.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Graphene, Gtk  # noqa: E402


def effects_curve(progress: float, slow: bool = False) -> float:
    """Evaluate upstream's cubic Bezier by solving x(t), not treating x as t."""
    if progress <= 0.0:
        return 0.0
    if progress >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(24):
        t = (lo + hi) / 2.0
        x = 3 * (1 - t) ** 2 * t * 0.34 + 3 * (1 - t) * t * t * 0.34 + t ** 3
        if x < progress:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2.0
    y1 = 0.88 if slow else 0.8
    return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t + t ** 3


class PageTransition(Adw.Bin):
    """Keep pages alive while animating their shared viewport.

    A new request replaces the pending destination; no stale timeout can take
    navigation back to an older page. TimedAnimation respects GTK's animation
    setting, uses the frame clock and does no work after settling.
    """

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__()
        self.set_child(child)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._offset = 0.0
        self._pending = None
        self._phase = "idle"
        self._direction = 1
        self._start_opacity = 1.0
        self._animation = Adw.TimedAnimation.new(
            self, 0.0, 1.0, 200,
            Adw.CallbackAnimationTarget.new(self._frame),
        )
        self._animation.set_easing(Adw.Easing.LINEAR)
        self._animation.connect("done", self._done)
        self.connect("unmap", self._unmap)
        self.connect("map", self._map)

    def navigate(self, commit, direction: int) -> None:
        self._animation.pause()
        self._direction = direction
        self._pending = commit
        if not self.get_mapped():
            self._settle()
            return
        self._start_opacity = self.get_child().get_opacity()
        self._phase = "out"
        self._animation.set_duration(200)
        self._animation.play()

    def _frame(self, value: float) -> None:
        if self._phase == "out":
            opacity = self._start_opacity * (1.0 - effects_curve(value))
        elif self._phase == "in":
            opacity = effects_curve(value, slow=True)
            self._offset = self._direction * 28.0 * (1.0 - opacity)
        else:
            return
        self.get_child().set_opacity(opacity)
        self.queue_draw()

    def _done(self, _animation) -> None:
        if self._phase == "out":
            commit, self._pending = self._pending, None
            if commit:
                commit()
            self._enter()
        else:
            self._settle()

    def _enter(self) -> None:
        self._phase = "in"
        self._animation.set_duration(300)
        self._animation.play()

    def _settle(self) -> None:
        commit, self._pending = self._pending, None
        self._phase = "idle"
        self._offset = 0.0
        self.get_child().set_opacity(1.0)
        if commit:
            commit()
        self.queue_draw()

    def _unmap(self, _widget) -> None:
        self._animation.pause()
        self._settle()

    def _map(self, _widget) -> None:
        pass

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:  # noqa: N802
        snapshot.save()
        snapshot.translate(Graphene.Point().init(0.0, self._offset))
        self.snapshot_child(self.get_child(), snapshot)
        snapshot.restore()
