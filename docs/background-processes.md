# Background processes

Short reference:
- **BackgroundProcessABC**: a unit of work the scheduler runs at a scheduled time.
- **BackgroundSchedulerImpl**: owns the heap of scheduled processes and drives the loop.
- **SchedulerHandleABC**: the per-process handle a process uses to push an earlier wakeup.
- **AsyncClockABC / TaskSpawnerABC / TaskHandleABC**: seams over `asyncio` so the scheduler is testable without a real running loop.
- **UserDisableProcessImpl / UserEnableProcessImpl**: the first two concrete processes; they execute pending `user_action` rows.

To keep it simple, this page only describes the lifecycle and the contracts.  The
concrete process list lives in
[`src/services/background_process/processes/`](../src/services/background_process/processes/).

## `attach_handles()` in short

One-shot seam between the scheduler and the registered processes. After
`register()` but before `start()`, it walks the registered list and binds a
`SchedulerHandleABC` to each process. The handle is what a process uses to
push an earlier wakeup via `wake_at(when, why)` without going through the
scheduler's public surface.

It exists separately from `register()` to break the cycle: the handle holds
a back-reference to the scheduler, and the process holds the handle. The
two-step `register` -> `attach_handles` -> `start` order guarantees the
background loop never calls `run()` on a process whose handle is still
`None`.

```mermaid
sequenceDiagram
    participant App as Construction Root
    participant Sched as BackgroundSchedulerImpl
    participant Proc as BackgroundProcessABC

    App->>Sched: register(process)
    App->>Sched: attach_handles()
    Note over Sched,Proc: handle = SchedulerHandleABC(owner=process)
    Sched->>Proc: attach_handle(handle)
    App->>Sched: start()
    Note over Sched: loop runs, process.handle is non-None
```

Most processes (e.g. `UserDisableProcessImpl`) never call `wake_at`; the
handle is only needed for the future share-expiry process, which is
woken from the share-creation path the moment a new share is inserted.

## Class diagram

```mermaid
classDiagram
    class BackgroundProcessABC {
        <<abstract>>
        +run() async
        +next_wakeup() async Optional~datetime~
        +attach_handle(handle) void
    }

    class SchedulerHandleABC {
        <<abstract>>
        +wake_at(when, why) async void
    }

    class BackgroundSchedulerABC {
        <<abstract>>
        +register(process) void
        +unregister(process) void
        +attach_handles() void
        +start() void
        +stop() async void
        +list_events() async Dict~datetime, ScheduledEvent~
    }

    class ScheduledEvent {
        +BackgroundProcessABC process
        +Dict why
    }

    class AsyncClockABC {
        <<abstract>>
        +sleep(seconds) async void
        +wait() async void
        +set() void
        +clear() void
        +is_set() bool
    }

    class TaskSpawnerABC {
        <<abstract>>
        +spawn(coro) TaskHandleABC
    }

    class TaskHandleABC {
        <<abstract>>
        +join() async void
        +cancel() void
        +is_cancelled() bool
    }

    class UserActionProcessABC {
        <<abstract>>
        +str action_name
    }

    class UserDisableProcessABC {
        +str action_name = "DisableUser"
    }

    class UserEnableProcessABC {
        +str action_name = "EnableUser"
    }

    class BackgroundSchedulerImpl {
        -AsyncClockABC _clock
        -TaskSpawnerABC _spawner
        -AsyncClockABC _stop_flag
        -Callable _get_now
        -List _heap
        -Dict _entries_by_process
        -List _registered
    }

    class AsyncClockAsyncio
    class TaskSpawnerAsyncio
    class TaskHandleAsyncio

    BackgroundProcessABC <|-- UserActionProcessABC
    UserActionProcessABC <|-- UserDisableProcessABC
    UserActionProcessABC <|-- UserEnableProcessABC
    BackgroundSchedulerABC <|-- BackgroundSchedulerImpl
    AsyncClockABC <|-- AsyncClockAsyncio
    TaskSpawnerABC <|-- TaskSpawnerAsyncio
    TaskHandleABC <|-- TaskHandleAsyncio

    BackgroundSchedulerImpl --> BackgroundProcessABC : registers
    BackgroundSchedulerImpl --> AsyncClockABC : uses
    BackgroundSchedulerImpl --> TaskSpawnerABC : uses
    BackgroundProcessABC --> SchedulerHandleABC : owns
```

## Lifecycle

```mermaid
sequenceDiagram
    participant Main as Construction Root
    participant Sched as BackgroundSchedulerImpl
    participant Proc as BackgroundProcessABC
    participant Clock as AsyncClockABC
    participant Task as TaskSpawnerABC

    Main->>Sched: register(process)
    Main->>Sched: attach_handles()
    Note over Sched,Proc: each process receives<br/>a SchedulerHandleABC bound to itself
    Main->>Sched: start()
    Sched->>Task: spawn(_run_loop())
    Sched->>Proc: next_wakeup()
    Proc-->>Sched: datetime | None
    loop while stop not signalled
        Sched->>Clock: sleep(delay)
        Clock-->>Sched: return
        Sched->>Proc: run()
        Proc-->>Sched: void
        Sched->>Proc: next_wakeup()
        Proc-->>Sched: datetime | None
    end
    Main->>Sched: stop()
    Sched->>Clock: stop_flag.set()
    Sched->>Task: cancel(sleep_task)
    Sched->>Task: join(loop_task)
```

## Pushing an earlier wakeup

The pull side (`next_wakeup`) seeds the heap; the push side
(`wake_at`) lets a process shorten its own pending sleep when
something external changes the schedule.

```mermaid
sequenceDiagram
    participant App as Request handler
    participant Sched as BackgroundSchedulerImpl
    participant Proc as BackgroundProcessABC
    participant Handle as SchedulerHandleABC

    App->>Proc: some side effect<br/>(e.g. new share created)
    Proc->>Handle: wake_at(earlier, why)
    Handle->>Sched: _schedule(process, when, why)
    alt earlier than current entry
        Sched->>Sched: replace heap entry
        Sched->>Sched: cancel current sleep_task
    else later than current entry
        Sched->>Sched: noop
    end
```

The `why` payload is a free-form `Dict[str, Any]` that must contain
at least `name` (e.g. `"DisableUser"`) and `description`
(human-readable prose), with any extras (`user_id`, `share_id`,
`action_id`, ...) for diagnosis.  The scheduler stores the most
recent `why` it received and surfaces it through `list_events()`.

## Register / unregister

```mermaid
sequenceDiagram
    participant App as Construction Root
    participant Sched as BackgroundSchedulerImpl
    participant Proc as BackgroundProcessABC

    App->>Sched: register(process)
    Note over Sched: stored in _registered<br/>placeholder heap entry
    App->>Sched: attach_handles()
    Note over Sched: handle bound to owner
    App->>Sched: start()
    Note over Sched: loop seeds wakeups
    App->>Sched: unregister(process)
    Note over Sched: removed from heap<br/>no more calls
```

`register` and `unregister` are observer-pattern style: they can
be called any time before `start`, and `unregister` may also be
called after `start` to drop a process from the running loop.