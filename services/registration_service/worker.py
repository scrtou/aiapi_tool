from __future__ import annotations

import signal
import threading
import time

from services.registration_service.service import RegistrationService


def main():
    service = RegistrationService()
    service.recover_incomplete_tasks()
    service.start_worker()

    stop_event = threading.Event()

    def handle_signal(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while not stop_event.is_set():
            stop_event.wait(1)
    finally:
        service.stop_worker()


if __name__ == "__main__":
    main()
