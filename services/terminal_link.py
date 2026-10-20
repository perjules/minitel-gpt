"""Serial availability supervision; an idle or switched-off terminal is normal."""
import logging
import time

log = logging.getLogger(__name__)


class Unavailable(Exception):
    """Only transport operations may raise this; unrelated bugs still fail loudly."""


def supervise(open_terminal, session, *, sleep=time.sleep, clock=time.monotonic):
    attempts = 0
    outage = None
    summary = 0
    delay_index = 0
    retired = None
    delays = (5, 10, 30, 60)

    while True:
        # A discarded HTTP response has no UART access. Do not accumulate workers
        # or overlap tool operations with a newly created interactive session.
        if retired is not None and retired.is_alive():
            sleep(1)
            continue
        retired = None
        terminal = None
        opened = clock()

        def activity(opened=opened):
            nonlocal outage, attempts, delay_index
            # Opening the adapter, or one successful write, is not recovery.
            # Require sustained successful I/O to avoid flapping log floods.
            if outage is not None and clock() - opened >= 30:
                log.info("Serial I/O stable again after %d attempts over %.0fs",
                         attempts, clock() - outage)
                outage, attempts, delay_index = None, 0, 0

        try:
            terminal = open_terminal()
            terminal.link_activity = activity
            session(terminal)
            return
        except Unavailable as error:
            now = clock()
            attempts += 1
            if outage is None:
                outage = summary = now
                log.warning("Terminal link unavailable (%s); retrying quietly", error)
            elif now - summary >= 300:
                log.warning("Terminal link still unavailable: %d attempts over %.0fs (%s)",
                            attempts, now - outage, error)
                summary = now
        finally:
            if terminal is not None:
                pending = getattr(terminal, "_pending", None)
                if pending is not None:
                    retired = pending[0]
                terminal.close()
        sleep(delays[delay_index])
        delay_index = min(delay_index + 1, len(delays) - 1)
