# SPDX-License-Identifier: AGPL-3.0-or-later
"""The other end of `ChildRunner`'s pipe: the in-process runtime, in a process of its own.

Started as `python -m sift.kernel.ml.worker` by `ChildRunner` and by nothing else. It reads one
frame saying what it is for, then answers loads and runs until its input ends, which is what
happens the moment the parent lets go of it, so a worker never outlives the backend.

Its standard output is the pipe and carries frames alone (a stray print would be read as a
frame's length), so `main` sends everything else to standard error, which the backend's log
keeps, and logging is configured as the backend's is.

A device that dies underneath a session ends this process, deliberately: the parent is what
restarts it, and a fresh process is the only thing that gets a fresh context.
"""

from __future__ import annotations

# First: a crash as the runtime loads then leaves every thread's stack in the backend's log.
from sift.kernel import crash_record  # noqa: F401

# isort: split

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sift.kernel.config import Settings
from sift.kernel.log import configure_logging
from sift.kernel.ml import runtime, session
from sift.kernel.ml.child import DEVICES_FLAG, receive, send
from sift.kernel.ml.runtime import DeviceUnavailable, Loaded, Runner
from sift.kernel.ml.weights import WeightError, WeightStore

#: The exit code that says the device was lost, so a log reader can tell it from a crash.
DEVICE_LOST = 3


def configure_from(hello: dict[str, Any]) -> None:
    """The logging pipeline, as the parent has it. No file: the parent's rotating log is the
    parent's, and two processes rotating one file is how lines go missing."""
    configure_logging(
        str(hello.get("log_level", "INFO")),
        redact_personal=bool(hello.get("redact_personal", True)),
    )


def _answer(
    frame: dict[str, Any], runner: Runner, loaded: dict[str, Loaded], settings: Settings
) -> dict[str, Any]:
    op = frame.get("op")
    if op == "load":
        one = runner.load(frame["weight"])
        loaded[one.weight.id] = one
        return {"inputs": list(one.inputs), "outputs": list(one.outputs), "device": one.device}
    if op == "run":
        handle = loaded[str(frame["weight_id"])]
        return {"outputs": runner.run(handle, frame["blob"], outputs=frame.get("outputs"))}
    if op == "encode":
        ids, end = runner.encode(frame["weight"], str(frame["text"]))
        return {"ids": ids, "eos": end}
    if op == "devices":
        return {"devices": list(session.providers(settings))}
    if op == "unload":
        runner.unload()
        loaded.clear()
        return {"ok": True}
    return {"error": f"unknown request {op!r}", "kind": "other"}


def serve(
    stdin: Any, stdout: Any, *, configure: Callable[[dict[str, Any]], None] = configure_from
) -> int:
    """Answer frames until the input ends. Returns the exit code."""
    hello = receive(stdin)
    if hello is None or hello.get("op") != "hello":
        return 2
    configure(hello)
    settings = Settings(data_dir=Path(hello["data_dir"]), cache_dir=Path(hello["cache_dir"]))
    runner = Runner(
        WeightStore(settings, str(hello["namespace"])),
        hello["hardware"],
        device=str(hello["device"]),
        feature=str(hello["feature"]),
    )
    loaded: dict[str, Loaded] = {}
    send(stdout, {"ok": True})
    while True:
        frame = receive(stdin)
        if frame is None:
            return 0
        try:
            send(stdout, _answer(frame, runner, loaded, settings))
        except WeightError as error:
            send(stdout, {"error": str(error), "kind": "weight"})
        except DeviceUnavailable as error:
            send(stdout, {"error": str(error), "kind": "device"})
            return DEVICE_LOST
        except Exception as error:
            send(stdout, {"error": f"{type(error).__name__}: {error}", "kind": "other"})


def answer_devices(stdout: Any, settings: Settings) -> int:
    """The device question alone, for a child started to answer it and exit."""
    try:
        found = session.providers(settings)
    except Exception as error:
        send(stdout, {"error": f"{type(error).__name__}: {error}"})
        return 1
    send(stdout, {"devices": list(found)})
    return 0


def main(argv: list[str] | None = None) -> int:
    frames = sys.stdout.buffer
    # From here on, whatever anything prints goes to standard error. See the module head.
    sys.stdout = sys.stderr
    runtime.loader = session
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == [DEVICES_FLAG]:
        return answer_devices(frames, Settings(data_dir=Path(args[1]), cache_dir=Path(args[2])))
    return serve(sys.stdin.buffer, frames)


if __name__ == "__main__":
    raise SystemExit(main())
