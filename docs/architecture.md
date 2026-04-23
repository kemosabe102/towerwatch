# Architecture

Towerwatch is a long-running monitoring process. It wakes up every 60 seconds, runs a handful of network probes, ships metrics to Grafana Cloud, and ships structured logs to Loki. When the network drops, it buffers logs to disk and posts an outage annotation back to Grafana when service returns.

This document is the map — read it before [`design.md`](design.md) (the per-function reference) and before jumping into code. Every design decision below is live in `src/towerwatch/`.

## Table of contents

1. [One diagram, one tick](#one-diagram-one-tick)
2. [Package layout](#package-layout)
3. [Composition root](#composition-root)
4. [Protocols over ABCs](#protocols-over-abcs)
5. [`TickContext` as a parameter object](#tickcontext-as-a-parameter-object)
6. [Clients as I/O adapters](#clients-as-io-adapters)
7. [Testing without MagicMock](#testing-without-magicmock)
8. [Circular imports and `TYPE_CHECKING`](#circular-imports-and-type_checking)
9. [What would evolve next](#what-would-evolve-next)

## One diagram, one tick

```
                       main.compose_root()
                              │
                              │ wires once at startup
                              ▼
                        ┌─────────────┐
                        │ TickContext │
                        └──────┬──────┘
                               │
                               ▼
                    app.run_loop(ctx, state)
                       (every 60 s)
                               │
        ┌──────────────────────┴─────────────────────────┐
        │                                                │
        ▼                                                ▼
  collect_probes(ctx)                             push_batch(ctx, …)
        │                                                │
        │ for probe in probes/:                          │ flush batch to
        │   result = probe.measure(clock)                 │ GrafanaClient;
        │   fields |= result.fields                       │ on success, clear;
        │                                                │ on failure, drop
        ▼                                                ▼ (do not buffer —
   update_connection_state(ctx, state, …)             truthful uptime).
        │                                                │
        │ events.connection_down / restored …            │
        ▼                                                ▼
   LokiClient.push(...) — WARN only in prod;    GrafanaClient.post_annotation
                                              (sticky outage marker)
        │
        ▼
   buffer to /opt/towerwatch/data/buffer.jsonl
        on HTTP failure; flush on reconnect.
```

Three hard invariants that this repo guards with its life — they're in [`CLAUDE.md`](../CLAUDE.md) and [`README.md`](../README.md):

1. **Metric units are `_ms`, not seconds.** Prometheus convention says seconds; our dashboards query `_ms`. Don't normalise.
2. **`LOKI_PUSH_LEVEL = "WARN"` in production.** `INFO` floods Loki and burns the ~230 MB/month data budget.
3. **Buffer is capped at 256 KB.** The data partition is 1 GB; raising the cap without thinking is how a single-byte bug becomes a wedged Pi.

## Package layout

```
src/towerwatch/
├── main.py                 # compose_root() + main() — the entry point
├── app.py                  # run_loop(ctx, state) — the 60 s loop body
├── config.py               # constants + load_build_version (pure)
├── clock.py                # Clock Protocol + SystemClock
├── lifecycle.py            # RuntimeState, signal handlers, logging setup
├── events.py               # LOG_EVENT_* emitters — stable keys for dashboards
├── scheduling.py           # Scheduler for infrequent probes
├── startup.py              # partition guard, outage classifier, marker IO
├── tick.py                 # TickContext, collect_probes, push_batch
├── credentials.py          # gitignored — shipped by deploy
├── clients/
│   ├── grafana.py          # GrafanaClient (metrics push + annotations)
│   └── loki.py             # LokiClient (log push + disk buffer)
└── probes/
    ├── base.py             # Probe Protocol, ProbeResult dataclass
    └── {ping, dns, tcp, http, gateway, m6, ookla}.py
tests/                      # pytest suite (hand-written fakes, no MagicMock)
pi/bench/                   # failure-mode harness (clock skew, network loss …)
```

Two things to notice:

- **`main.py` and `app.py` are split on purpose.** `main` composes; `app` runs. A test can build a fake `TickContext` and call `app.run_loop` with no reference to production wiring. (This is the *composition root* pattern — see §3.)
- **Orchestration files live at the package root, not under an `orchestration/` subpackage.** Splitting 5 files into a folder adds nesting without clarity. Subpackages exist where the boundary is meaningful (`clients/` for outbound I/O, `probes/` for the plug-in contract).

## Composition root

The pattern: **every production dependency is instantiated exactly once, at the entry point, and passed down.** No globals, no service locator, no DI framework.

```python
# src/towerwatch/main.py
def compose_root() -> tuple[TickContext, RuntimeState]:
    from towerwatch import credentials
    state = RuntimeState()
    configure_logging()
    install_signal_handlers(state)

    loki     = LokiClient.from_config(config, credentials)
    grafana  = GrafanaClient.from_config(config, credentials)
    scheduler = Scheduler.from_config(config)
    ctx = TickContext(grafana=grafana, loki=loki, scheduler=scheduler)
    return ctx, state


def main() -> None:
    ctx, state = compose_root()
    app.run_loop(ctx, state)
```

Why this matters for testing: `app.run_loop(fake_ctx, fake_state)` works without monkeypatching `loki_client.LOKI_URL` or stubbing `requests.post` globally. The test builds exactly the graph it wants.

Why it reads well for a reviewer: a single function says what the program is made of. No magic. Trace it top-down and you've seen every moving part.

Why no DI container: the whole graph is ~6 objects. A container like `dependency-injector` or `punq` buys nothing at this size and costs a layer of indirection. If the graph grew to 30+ objects with dynamic resolution, that calculus flips.

## Protocols over ABCs

Python 3.8+ gives you [`typing.Protocol`](https://peps.python.org/pep-0544/) for structural typing. In this codebase, every injectable seam is a Protocol:

```python
# src/towerwatch/clock.py
class Clock(Protocol):
    def time(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def time(self) -> float: return time.time()
    def sleep(self, seconds: float) -> None: time.sleep(seconds)
```

```python
# src/towerwatch/probes/base.py
class Probe(Protocol):
    def measure(self, clock: Clock) -> ProbeResult: ...
```

`SystemClock` doesn't inherit from `Clock` — it *structurally satisfies* it. Same for the probes: `PingProbe`, `DNSProbe`, etc. don't declare inheritance from `Probe`; the typechecker derives conformance from shape. A `FakeClock` in `tests/fakes.py` is 20 lines, no base class, and fits everywhere `Clock` is expected.

**When would I use an ABC instead?** When the base class carries shared behavior (`abstractmethod` plus a concrete `__init_subclass__` hook, a template-method pattern). For pure contracts, Protocol is the right tool — it keeps production classes decoupled from test doubles.

## `TickContext` as a parameter object

```python
# src/towerwatch/tick.py
@dataclass
class TickContext:
    grafana: Any = None
    loki: Any = None
    scheduler: Any = None
    events: Any = events_mod
    clock: Clock = field(default_factory=_default_clock)
```

A common Python lint says "bundling dependencies into a context object is a smell." That's true — *when the context leaks beyond the orchestration layer*. Here it doesn't. `TickContext` exists because one tick genuinely needs five collaborators, every tick. The alternatives:

- Pass all five as positional params — `push_batch(grafana, loki, scheduler, events, clock, state, batch, …)`. Ugly and error-prone.
- Build a singleton registry — that's a service locator, which is the smell people are warning about.

The context object splits the difference: explicit construction at the compose root, single threading through orchestration code, no surprise state.

**The guard-rail:** `TickContext` is used in `tick.py` and `app.py` only. Probes and clients take their collaborators individually. If you find yourself importing `TickContext` inside `probes/http.py`, something has gone wrong.

## Clients as I/O adapters

The `clients/` subpackage groups outbound HTTP. It's not full [hexagonal architecture](https://alistair.cockburn.us/hexagonal-architecture/) — no `Port` interfaces, no adapter registration. It's just the observation that *everything in `clients/` talks to the network and nothing else does* (probes do too, but they're classified by the measurement they make, not by the transport).

This yields two nice properties:

1. A reader scanning the package tree knows where to look for "what URLs do we hit?" — `src/towerwatch/clients/`.
2. Tests for `tick.push_batch` pass a `FakeGrafana` and a `FakeLoki` and know that's a complete I/O double. No hunting for ambient `requests.post` calls.

The production clients have a classmethod `from_config(cfg, creds)` — a [named constructor](https://wiki.c2.com/?NamedConstructor). It's the seam between "here is the full graph of module-level config constants and credentials" and "here is a self-contained object you can construct with five strings in a test."

## Testing without MagicMock

`tests/fakes.py` contains hand-written test doubles: `FakeClock`, `FakeSession`, `FakeResponse`, `FakeGrafana`, `FakeLoki`, `FakeEvents`, `FakeSignal`, `FakeSubprocess`. They are dataclasses with recorded-call lists. Example:

```python
# tests/fakes.py (shape)
class FakeLoki:
    def __init__(self):
        self.pushed: list[tuple[str, str, dict | None]] = []
    def push(self, level, message, extra=None):
        self.pushed.append((level, message, extra))
    def flush(self) -> int:
        return 0
```

```python
# tests/test_events.py
def test_connection_down_event_shape():
    loki = FakeLoki()
    events.connection_down(loki, reason="icmp_loss_100pct")
    level, msg, extra = loki.pushed[-1]
    assert level == "WARN"
    assert extra["event"] == config.LOG_EVENT_CONNECTION_DOWN
```

No `MagicMock`. No `mocker.patch('...')`. Why?

- **Fakes are readable.** Anyone can open `tests/fakes.py` and understand every interaction the tests can observe.
- **Fakes fail loudly on drift.** If the real `LokiClient` grows a `flush_sync` method, the fake doesn't — so the test that exercises `flush_sync` has to update the fake, which makes the design pressure explicit.
- **No monkeypatching at import time.** `conftest.py` puts `src/` on `sys.path` and materialises a credentials stub if missing. That's it.

The cost: adding a new collaborator requires writing a fake for it. The benefit: you never chase a test failure that turns out to be a patched-out method nobody noticed.

## Circular imports and `TYPE_CHECKING`

Early refactors in this project hit the classic Python pain: `grafana.py` wanted to push a log entry on failure, and `loki.py` wanted to hit the outage-annotation endpoint, so each imported the other at module load time. The original fix was a `_LazyLokiSink` singleton holding forward-declared references. It worked but creaked.

Restructuring into `clients/` plus proper type-annotated interfaces cleared most of the cycle. What remains is handled the idiomatic way:

- Runtime imports only when actually needed — e.g. `clients/grafana.py` imports `from towerwatch.clients.loki import push_log` *inside* the error path, not at module top.
- Type-only references use `from typing import TYPE_CHECKING` and an `if TYPE_CHECKING:` block. The names exist at typecheck time (pyright sees them) but aren't touched at runtime (no cycle).

When should you use `TYPE_CHECKING`? Only when a true runtime cycle exists. Using it preemptively turns into a forest of quoted annotations that nobody can refactor safely. Prefer shape changes that break the cycle honestly.

## What would evolve next

This document is a snapshot of "done enough for 7k LOC." If the app grew, the next moves would be:

| If… | Then… |
|---|---|
| The probe count doubles, or config touches every module | Introduce a typed `Config` dataclass (replacing the kwargs-with-defaults pattern) so callers can see what's tunable without grepping `config.py`. |
| Credentials need to differ per environment | Move secrets from `credentials.py` to env vars loaded via a typed `Secrets` model. Systemd drop-ins own the vars; `credentials.py.example` becomes `credentials.env.example`. |
| The service gains a REST API or a secondary daemon | Promote `compose_root()` into a `build_container()` function and let each daemon pick the slice it needs. At that point a lightweight DI container (`punq`, no framework) might pay for itself. |
| The bench harness fully integrates with the main package | Move `pi/bench/` to `tests/bench/` or `bench/`, give it its own `[project.optional-dependencies.bench]` entry, and drop the namespace-package dance. |
| Probes need shared retry/backoff logic | Extract a `TransportPolicy` protocol (e.g., `retries`, `timeout_ms`, `on_failure`), compose it into each probe, and test the policy once. |

None of these is urgent. All of them are **structural moves that preserve the invariants in `CLAUDE.md`** — never a metric rename, never a `LOG_EVENT_*` change, never a larger buffer.

The rule: the observable behavior of this service is a contract with Grafana dashboards and LogQL alerts. Architecture changes happen below that contract.
