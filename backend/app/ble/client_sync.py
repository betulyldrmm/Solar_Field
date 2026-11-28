"""PC-side history sync, independent of the BLE library (testable without Bluetooth).

A `transport` only has to implement:
    async def request(self, req: HistoryRequest, timeout_s: float) -> bytes   # reassembled batch
and raise TimeoutError / HistoryError on failure. `ChunkTransport` below does
the chunk collection + selective resend; subclasses only provide `_write`
(BLE write in pc_receiver/ble_receiver.py, an in-process fake in tests).

Received records are stored in the PC's own SQLite (same schema as the device),
so the normal FastAPI backend + React UI on the PC show them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.ble.history import (
    MAX_RECORDS_PER_BATCH,
    MAX_RESEND_INDICES,
    HistoryError,
    HistoryRequest,
    Reassembler,
    encode_resend,
    parse_batch,
)
from app.storage.database import Database

log = logging.getLogger("sfs.sync")


class Transport(Protocol):
    async def request(self, req: HistoryRequest, timeout_s: float) -> bytes: ...


class ChunkTransport:
    """Collects notification chunks for one request; asks again for missing ones.

    Subclasses implement `_write(data)` and call `feed(chunk)` for every
    HISTORY_DATA notification."""

    quiet_s = 0.6          # no chunk for this long -> burst is over
    resend_rounds = 10

    def __init__(self) -> None:
        self._r: Reassembler | None = None
        self._activity = asyncio.Event()

    async def _write(self, data: bytes) -> None:
        raise NotImplementedError

    def feed(self, chunk: bytes | bytearray) -> None:
        if self._r is not None:
            self._r.add(chunk)
            self._activity.set()

    async def _wait_quiet(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        while self._r is not None and not self._r.complete and loop.time() < deadline:
            self._activity.clear()
            try:
                wait = min(self.quiet_s, max(0.0, deadline - loop.time()))
                await asyncio.wait_for(self._activity.wait(), wait)
            except TimeoutError:
                return

    async def request(self, req: HistoryRequest, timeout_s: float) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        self._r = r = Reassembler(req.nonce)
        await self._write(req.encode())
        for _ in range(self.resend_rounds + 1):
            await self._wait_quiet(deadline)
            if r.complete:
                return r.data()
            if loop.time() >= deadline:
                break
            if r.count is None:          # nothing arrived: send the request again
                await self._write(req.encode())
                continue
            missing = r.missing
            for k in range(0, len(missing), MAX_RESEND_INDICES):
                await self._write(encode_resend(req.nonce, missing[k:k + MAX_RESEND_INDICES]))
        raise TimeoutError(f"missing chunks {r.missing[:10]}")


@dataclass
class SyncResult:
    received: int = 0
    stored: int = 0          # new rows (duplicates are ignored by UNIQUE(boot_id, sequence))
    batches: int = 0
    retries: int = 0
    up_to_date: bool = False


class SyncState:
    """Last device record id already received, per device address (JSON file)."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self._data: dict[str, int] = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self._data = {}

    def get(self, device: str) -> int:
        return int(self._data.get(device, 0))

    def set(self, device: str, last_id: int) -> None:
        self._data[device] = last_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # atomic: never a half-written state file


class HistorySync:
    def __init__(self, db: Database, state: SyncState, device: str, *,
                 chunk_size: int = 20, batch_records: int = 50, max_retries: int = 4,
                 timeout_s: float = 15.0):
        self.db = db
        self.state = state
        self.device = device
        self.chunk_size = chunk_size
        self.batch_records = min(batch_records, MAX_RECORDS_PER_BATCH)
        self.max_retries = max_retries
        self.timeout_s = timeout_s

    async def run(self, transport: Transport) -> SyncResult:
        res = SyncResult()
        since = self.state.get(self.device)
        while True:
            batch = None
            for attempt in range(self.max_retries + 1):
                req = HistoryRequest(random.randrange(1, 0xFFFF), since, self.batch_records, self.chunk_size)
                try:
                    batch = parse_batch(await transport.request(req, self.timeout_s))
                    break
                except (TimeoutError, HistoryError) as e:
                    res.retries += 1
                    log.warning("batch since=%d failed (%s), retry %d/%d",
                                since, e, attempt + 1, self.max_retries)
            if batch is None:
                raise HistoryError(f"giving up after {self.max_retries} retries (since_id={since})")

            if batch.device_max_id < since:
                # Device database was recreated (ids restarted) -> start over; duplicates are ignored.
                log.warning("device ids restarted (%d < %d): full resync", batch.device_max_id, since)
                since = 0
                self.state.set(self.device, 0)
                continue
            if not batch.records:
                res.up_to_date = True
                return res

            res.batches += 1
            for rid, m in batch.records:
                res.received += 1
                if self.db.insert_measurement(m) is not None:
                    res.stored += 1
                since = max(since, rid)
            self.state.set(self.device, since)  # commit progress after each good batch
            log.info("synced up to id %d / %d", since, batch.device_max_id)
