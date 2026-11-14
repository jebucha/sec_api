"""Hot reload: debounced watchfiles watcher + SIGHUP (design D8, D13).

A reload fully builds and validates a new ``Ruleset`` before atomically
swapping the reference on the gateway state. In-flight requests already
captured the old reference, so they keep their ruleset. A failed reload
(emitting ``ruleset_reload_failed``) leaves the previous ruleset active.
"""

from __future__ import annotations

import asyncio
import signal
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import watchfiles

from ..events import ruleset_loaded_event, ruleset_reload_failed_event
from .loader import RulesetLoadError, load_rules

if TYPE_CHECKING:
    from ..app import Gateway

# Debounce editor bursts (a save produces multiple change events).
_WATCH_STEP_MS = 100
_DEBOUNCE_MS = 200


class RulesWatcher:
    """Watches the rules directory and SIGHUP; swaps ``gateway.ruleset``."""

    def __init__(self, gateway: Gateway, *, canary_values: list[str] | None = None) -> None:
        self._gateway = gateway
        self._rules_dir = Path(gateway.config.rules_dir)
        self._canaries = list(canary_values or [])
        self._writer = gateway.writer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def rules_dir(self) -> Path:
        return self._rules_dir

    def start(self) -> None:
        """Register SIGHUP on the running loop, then start the watcher thread.

        ``loop.add_signal_handler`` is the design D8 mechanism; off-loop or
        off-main-thread launches (tests) fall back to ``signal.signal``, and
        if that is unavailable too SIGHUP is simply not registered.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            try:
                loop.add_signal_handler(signal.SIGHUP, self.reload_now)
                self._loop = loop
            except (NotImplementedError, RuntimeError, ValueError, OSError):
                self._install_signal_fallback()
        else:
            self._install_signal_fallback()
        self._thread = threading.Thread(target=self._run, name="llmgw-rules-watcher", daemon=True)
        self._thread.start()

    def _install_signal_fallback(self) -> None:
        try:
            signal.signal(signal.SIGHUP, self._on_signal)
        except (ValueError, OSError):
            pass  # not the main thread (e.g. tests); SIGHUP is unavailable

    def stop(self) -> None:
        self._stop.set()
        if self._loop is not None:
            try:
                self._loop.remove_signal_handler(signal.SIGHUP)
            except (NotImplementedError, RuntimeError, ValueError, OSError):
                pass
            self._loop = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _on_signal(self, _signum: int, _frame: object) -> None:
        self.reload_now()

    def reload_now(self) -> None:
        """Build a new ruleset and swap the reference, or record the failure."""
        try:
            result = load_rules(
                self._rules_dir, event_sink=self._writer.emit, canary_values=self._canaries
            )
        except RulesetLoadError as exc:
            self._writer.emit(
                ruleset_reload_failed_event(
                    reason=str(exc), active_ruleset_hash=self._gateway.ruleset.hash
                )
            )
            return
        self._gateway.ruleset = result.ruleset  # atomic reference swap (D8)
        self._writer.emit(
            ruleset_loaded_event(
                ruleset_hash=result.ruleset.hash,
                accepted=result.accepted,
                rejected=result.rejected,
            )
        )

    def _run(self) -> None:
        # watchfiles.watch is a generator: iterate until stop_event is set.
        for _changes in watchfiles.watch(
            self._rules_dir,
            stop_event=self._stop,
            step=_WATCH_STEP_MS,
            debounce=_DEBOUNCE_MS,
        ):
            if self._stop.is_set():
                break
            self.reload_now()
