import logging
import subprocess
import sys
import threading
import time
from typing import List, Optional, Sequence

from watchdog import events
from watchdog.observers import Observer
from watchdog.utils.patterns import match_any_paths

from .config import Config
from .constants import LOOP_DELAY, VERSION
from .parse import parse_arguments

logging.basicConfig(level=logging.INFO, format="[ptw] %(message)s")
logger = logging.getLogger(__name__)


class Trigger:
    value: float
    lock: threading.Lock

    def __init__(self):
        self.lock = threading.Lock()
        self.value = 0

    def emit(self):
        with self.lock:
            self.value = time.time()

    def is_empty(self):
        return self.value == 0

    def release(self):
        with self.lock:
            self.value = 0

    def check(self, delay: float):
        now = time.time()
        return self.value > 0 and now - self.value > delay


trigger = Trigger()


class EventHandler:
    EVENTS_WATCHED = {
        events.EVENT_TYPE_CREATED,
        events.EVENT_TYPE_DELETED,
        events.EVENT_TYPE_MODIFIED,
        events.EVENT_TYPE_MOVED,
    }

    def __init__(
        self,
        patterns: Optional[List[str]] = None,
        ignore_patterns: Optional[List[str]] = None,
    ):
        self._patterns = patterns or ["*.py"]
        self._ignore_patterns = ignore_patterns or []

    @property
    def patterns(self) -> List[str]:
        return self._patterns

    @property
    def ignore_patterns(self) -> List[str]:
        return self._ignore_patterns

    def _is_event_watched(self, event: events.FileSystemEvent) -> bool:
        if event.event_type not in self.EVENTS_WATCHED:
            return False

        paths = [event.src_path]
        if hasattr(event, "dest_path"):
            # For file moved type events we are also interested in the destination
            paths.append(event.dest_path)

        return match_any_paths(paths, self.patterns, self.ignore_patterns)

    def dispatch(self, event: events.FileSystemEvent) -> None:
        if self._is_event_watched(event):
            trigger.emit()
            logger.info(f"{event.src_path} {event.event_type}")
        else:
            logger.debug(f"IGNORED event: {event.event_type} src: {event.src_path}")


def clear_screen():
    sys.stdout.write("\033c")
    sys.stdout.flush()


def _invoke_runner(runner: str, args: Sequence[str], clear: bool) -> None:
    if clear:
        clear_screen()
    subprocess.run([runner, *args])


def main_loop(
    *, runner: str, runner_args: Sequence[str], delay: float, clear: bool
) -> None:
    if trigger.check(delay):
        _invoke_runner(runner, runner_args, clear=clear)

        trigger.release()

    time.sleep(LOOP_DELAY)


def run():
    namespace, runner_args = parse_arguments(sys.argv[1:])

    config = Config.create(namespace=namespace, extra_args=runner_args)

    event_handler = EventHandler(
        patterns=config.patterns, ignore_patterns=config.ignore_patterns
    )

    observer = Observer()

    observer.schedule(event_handler, config.path, recursive=True)
    observer.start()

    sys.stdout.write(f"pytest-watcher version {VERSION}\n")
    sys.stdout.write(f"Runner command: {config.runner} {' '.join(config.runner_args)}\n")
    sys.stdout.write(f"Waiting for file changes in {config.path.absolute()}\n")

    if config.now:
        trigger.emit()

    try:
        while True:
            main_loop(
                runner=config.runner,
                runner_args=config.runner_args,
                delay=config.delay,
                clear=config.clear,
            )
    finally:
        observer.stop()
        observer.join()
