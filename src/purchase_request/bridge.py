"""Own the pinned CrewAI Flow sync-call seam without abandoning native HTTP work."""

import asyncio
import threading


class NativeAbort(BaseException):
    """Carry an original failure through CrewAI's Exception-based retry/repair loops."""

    def __init__(self, original):
        self.original = original
        super().__init__(type(original).__name__ + ": " + str(original))


class NativeBridge:
    def __init__(self, receipt):
        self.loop = asyncio.get_running_loop()
        self.receipt = receipt
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.calls = set()
        self.futures = set()
        self.tasks = set()

    def invoke(self, function, *args, **kwargs):
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            raise RuntimeError("native sync bridge cannot block its owner event loop")
        done = threading.Event()
        with self.lock:
            if self.stopping.is_set():
                raise RuntimeError("native bridge is closed")
            self.calls.add(done)

        async def execute():
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                if self.stopping.is_set():
                    raise asyncio.CancelledError("native bridge closing")
                self.receipt.guard()
                return await function(*args, **kwargs)
            finally:
                self.tasks.discard(task)

        future = asyncio.run_coroutine_threadsafe(execute(), self.loop)
        with self.lock:
            self.futures.add(future)
        try:
            return future.result()
        except BaseException as error:
            raise NativeAbort(error) from error
        finally:
            with self.lock:
                self.futures.discard(future)
                self.calls.discard(done)
            done.set()

    async def close(self):
        self.stopping.set()
        with self.lock:
            futures = list(self.futures)
        for future in futures:
            future.cancel()
        await asyncio.sleep(0)
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        deadline = self.loop.time() + 5
        while True:
            with self.lock:
                active = bool(self.calls or self.futures)
            if not active and not self.tasks:
                return
            if self.loop.time() >= deadline:
                raise RuntimeError("native bridge calls did not finish cleanup")
            await asyncio.sleep(0.005)
