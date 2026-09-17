"""
A single worker that runs jobs one at a time, in the order they arrive.

Submitting returns at once with a run id whose row says "queued"; the
worker flips it to running and then done or failed. Callers that need the
outcome poll the run row (the Discord bot does this for /ask).
"""
import asyncio
import logging

from agent import runner, store

logger = logging.getLogger(__name__)


class JobQueue:
    def __init__(self, cfg):
        self.cfg = cfg
        self._queue = asyncio.Queue()
        self._task = None
        self.current = None

    def start(self):
        self._task = asyncio.create_task(self._worker())

    def submit(self, job, params=None, trigger="api"):
        run_id = store.create_run(job, trigger=trigger, params=params, status="queued")
        self._queue.put_nowait((run_id, job, params or {}, trigger))
        logger.info("queued %s as %s (%s)", job, run_id, trigger)
        return run_id

    @property
    def size(self):
        return self._queue.qsize()

    async def _worker(self):
        while True:
            run_id, job, params, trigger = await self._queue.get()
            self.current = run_id
            try:
                await runner.run_job(self.cfg, job, params=params, trigger=trigger, run_id=run_id)
            except Exception:
                logger.exception("worker: run %s crashed", run_id)
            finally:
                self.current = None
                self._queue.task_done()
