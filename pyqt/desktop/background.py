"""后台长任务登记表（v6 3.3）：长耗时构建（>3min）转后台，主对话不阻塞。

- 只做轻量登记与完成事件（线程安全 queue，GUI 定时器消费渲染系统卡片），
  不引入线程池等重依赖（轻量化原则）。
- 低内存模式下调用方应不起后台任务，直接串行执行（少进程）。
"""
import datetime
import queue
import threading
import uuid

BG_THRESHOLD_S = 180.0  # 3 分钟：超过视为长耗时任务


class BackgroundJobs:
    def __init__(self):
        self._jobs: dict = {}
        self._lock = threading.Lock()
        self.events: "queue.Queue" = queue.Queue()  # GUI 定时器排空（线程安全）

    def register(self, label: str) -> str:
        job_id = uuid.uuid4().hex[:8]
        with self._lock:
            # 有界表：超限淘汰最早的 finished 任务，防长时间运行无限增长
            if len(self._jobs) >= 200:
                finished = [k for k, v in self._jobs.items() if v["status"] != "running"]
                for k in finished[: max(1, len(finished) - 100)]:
                    self._jobs.pop(k, None)
            self._jobs[job_id] = {
                "label": str(label)[:120],
                "status": "running",
                "started_at": datetime.datetime.now().strftime("%H:%M:%S"),
                "finished_at": "",
            }
        self.events.put({"type": "bg_started", "job_id": job_id, "label": label})
        return job_id

    def finish(self, job_id: str, ok: bool, output_tail: str = "") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "done" if ok else "error"
            job["finished_at"] = datetime.datetime.now().strftime("%H:%M:%S")
        self.events.put({"type": "bg_done", "job_id": job_id, "ok": ok,
                         "output": str(output_tail)[-800:]})

    def snapshot(self) -> list:
        with self._lock:
            return [{"id": k, **v} for k, v in self._jobs.items()]

    def active(self) -> int:
        with self._lock:
            return sum(1 for v in self._jobs.values() if v["status"] == "running")

    def drain_events(self) -> list:
        """非阻塞排空事件（GUI 定时器调用）；每 tick 最多 200 条防一次排空冻结 UI。"""
        out = []
        for _ in range(200):
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out


_BG: BackgroundJobs = None
_BG_LOCK = threading.Lock()


def get_bg_jobs() -> BackgroundJobs:
    global _BG
    with _BG_LOCK:
        if _BG is None:
            _BG = BackgroundJobs()
        return _BG
