"""Which primitive can actually gate the submission path (round-5 #6).

Spec v4 §2 proposes `_gate = threading.RLock()` "spanning the final check ->
HTTP dispatch", with /halt acquiring the same gate so it cannot interleave.
The reviewer asked whether that lock spans what the document claims, and
whether /halt can deadlock the loop.

These two tests answer it by running the primitives rather than reasoning about
them. They are kept as regression guards: they are the reason the gate is an
asyncio.Lock, and they will fail if anyone swaps it back.
"""
import asyncio
import threading

import pytest


@pytest.mark.asyncio
async def test_threading_rlock_does_not_exclude_coroutines_on_one_loop():
    """An RLock is reentrant PER THREAD, and every coroutine shares one thread.

    So /halt acquiring the "same gate" as an in-flight submission succeeds
    immediately instead of waiting. The gate is not merely deadlock-prone — in
    the ordinary case it provides no mutual exclusion at all, which is worse,
    because it looks like it does.
    """
    gate = threading.RLock()
    events: list[str] = []

    async def submitter():
        with gate:
            events.append("submit-enter")
            await asyncio.sleep(0.01)  # stands in for `await to_thread(place_order)`
            events.append("submit-exit")

    async def halter():
        await asyncio.sleep(0)  # let the submitter take the gate first
        with gate:
            events.append("halt-acquired")

    await asyncio.gather(submitter(), halter())

    # /halt lands INSIDE the submission's critical section.
    assert events == ["submit-enter", "halt-acquired", "submit-exit"]


@pytest.mark.asyncio
async def test_asyncio_lock_excludes_halt_until_the_submission_completes():
    """asyncio.Lock is per-task, so /halt suspends instead of walking straight in.

    It also cannot stall the loop: waiting on it yields, where a blocking
    acquire on the loop thread would freeze every other coroutine — including
    the one holding the gate, which is the deadlock the reviewer flagged.
    """
    gate = asyncio.Lock()
    events: list[str] = []

    async def submitter():
        async with gate:
            events.append("submit-enter")
            await asyncio.sleep(0.01)
            events.append("submit-exit")

    async def halter():
        await asyncio.sleep(0)
        async with gate:
            events.append("halt-acquired")

    await asyncio.gather(submitter(), halter())

    # /halt returns only once no submission is mid-flight — the spec's intent.
    assert events == ["submit-enter", "submit-exit", "halt-acquired"]
