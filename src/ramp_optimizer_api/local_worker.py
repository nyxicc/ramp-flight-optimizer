"""One local supervisor process; one killable child per claimed job."""

import argparse
import multiprocessing
import os
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select

from ramp_optimizer_api.job_services import JobService
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.errors import PersistenceError
from ramp_optimizer_persistence.jobs import claim_job
from ramp_optimizer_persistence.models import OptimizationJobRow
from ramp_optimizer_persistence.repositories import get_operational_day
from ramp_optimizer_persistence.settings import DatabaseSettings


def solve_child(connection, day, config, timeout):
    # A supervisor crash must not leave an unbounded orphan solver.
    watchdog = threading.Timer(timeout, lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        from ramp_optimizer import optimize_flight_assignments
        from ramp_optimizer.execution import observer
        from ramp_optimizer_api.mapping import optimization_result_to_response

        def checkpoint(progress, result):
            connection.send(
                (
                    "checkpoint",
                    progress,
                    optimization_result_to_response(result).model_dump(mode="json")
                    if result
                    else None,
                )
            )

        token = observer.set(checkpoint)
        try:
            result = optimize_flight_assignments(day, config)
        finally:
            observer.reset(token)
        connection.send(("result", optimization_result_to_response(result).model_dump(mode="json")))
    except Exception:
        connection.send(("error", "SOLVER_EXECUTION_FAILED"))
    finally:
        watchdog.cancel()
        connection.close()


class LocalWorker:
    def __init__(self, service: JobService, *, target=solve_child):
        self.service = service
        self.target = target

    def recover_expired(self) -> None:
        # Never execute abandoned work again. Mark it terminal only after its
        # original wall deadline plus process-cleanup allowance has expired.
        with self.service.sessions() as session:
            rows = list(
                session.scalars(
                    select(OptimizationJobRow).where(
                        OptimizationJobRow.status.in_(("RUNNING", "CANCELLING"))
                    )
                )
            )
        for row in rows:
            if (
                row.started_at
                and (
                    datetime.now(timezone.utc) - datetime.fromisoformat(row.started_at)
                ).total_seconds()
                > row.timeout_seconds + 30
            ):
                self.service.finish(
                    row.id, row.worker_token or "", "FAILED", error_code="WORKER_LOST"
                )

    def run_once(self) -> bool:
        self.recover_expired()
        with self.service.sessions.begin() as session:
            row = claim_job(session)
            if row is None:
                return False
            job_id, token, timeout = row.id, row.worker_token or "", row.timeout_seconds
            snapshot_id = row.operational_day_id
        try:
            with self.service.sessions() as session:
                snapshot = get_operational_day(session, snapshot_id)
        except PersistenceError:
            self.service.finish(job_id, token, "FAILED", error_code="INPUT_INTEGRITY_FAILED")
            return True
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        child = context.Process(
            target=self.target, args=(sender, snapshot.day, snapshot.config, timeout), daemon=True
        )
        started = time.monotonic()
        try:
            child.start()
            sender.close()
            while True:
                if self.service.get(job_id).status == "CANCELLING":
                    child.terminate()
                    child.join()
                    self.service.finish(job_id, token, "CANCELLED")
                    break
                if time.monotonic() - started >= timeout:
                    child.terminate()
                    child.join()
                    self.service.finish(job_id, token, "TIMED_OUT", error_code="WALL_TIMEOUT")
                    break
                if receiver.poll(0.05):
                    try:
                        message = receiver.recv()
                    except EOFError:
                        child.join(timeout=1)
                        self.service.finish(
                            job_id,
                            token,
                            "TIMED_OUT" if child.exitcode == 124 else "FAILED",
                            error_code="WALL_TIMEOUT"
                            if child.exitcode == 124
                            else "WORKER_PROCESS_EXITED",
                        )
                        break
                    if message[0] == "checkpoint":
                        self.service.checkpoint(job_id, token, message[1], message[2])
                    elif message[0] == "result":
                        self.service.finish(job_id, token, "SUCCEEDED", message[1])
                        break
                    else:
                        self.service.finish(
                            job_id, token, "FAILED", error_code="SOLVER_EXECUTION_FAILED"
                        )
                        break
                elif not child.is_alive():
                    self.service.finish(
                        job_id,
                        token,
                        "TIMED_OUT" if child.exitcode == 124 else "FAILED",
                        error_code="WALL_TIMEOUT"
                        if child.exitcode == 124
                        else "WORKER_PROCESS_EXITED",
                    )
                    break
        except BaseException:
            if child.pid and child.is_alive():
                child.terminate()
                child.join()
            self.service.finish(job_id, token, "FAILED", error_code="WORKER_INTERRUPTED")
            raise
        finally:
            if child.pid:
                if child.is_alive():
                    child.terminate()
                child.join(timeout=5)
                if child.is_alive():
                    child.kill()
                    child.join()
                child.close()
            sender.close()
            receiver.close()
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Local background optimization worker")
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued job, then exit"
    )
    args = parser.parse_args()
    engine = create_database_engine(DatabaseSettings.from_environment().database_url)
    worker = LocalWorker(JobService(make_session_factory(engine)))
    try:
        while True:
            worked = worker.run_once()
            if args.once:
                break
            if not worked:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
