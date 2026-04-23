"""Composition root: wire every runtime dependency, then hand off to `app.run_loop`.

Kept deliberately separate from `app.py` so tests can instantiate a TickContext
with fakes and call `app.run_loop` without touching production wiring.
"""

from __future__ import annotations

import logging

from towerwatch import app, config
from towerwatch.clients import grafana as grafana_mod
from towerwatch.clients import loki as loki_mod
from towerwatch.lifecycle import RuntimeState, configure_logging, install_signal_handlers
from towerwatch.scheduling import Scheduler
from towerwatch.tick import TickContext

log = logging.getLogger("towerwatch")


def compose_root() -> tuple[TickContext, RuntimeState]:
    """Instantiate and wire every collaborator needed for one process lifetime."""
    try:
        from towerwatch import credentials
    except ImportError as e:
        print(
            "ERROR: credentials.py not found. "
            "Copy credentials.py.example to credentials.py and fill in values."
        )
        raise SystemExit(1) from e

    state = RuntimeState()
    configure_logging()
    install_signal_handlers(state)

    loki = loki_mod.LokiClient.from_config(config, credentials)
    grafana = grafana_mod.GrafanaClient.from_config(config, credentials)
    scheduler = Scheduler.from_config(config)
    ctx = TickContext(grafana=grafana, loki=loki, scheduler=scheduler)
    return ctx, state


def main() -> None:
    ctx, state = compose_root()
    try:
        app.run_loop(ctx, state)
    except KeyboardInterrupt:
        log.info("Stopped by user")
    except Exception as e:
        log.critical("Fatal error: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    main()
