"""Create a fictional supervisor input through the existing input-management service.

Run migrations first. Set RAMP_OPTIMIZER_DATABASE_URL to an isolated demo database.
The scenario date is 2035-04-15; this script never changes existing input versions.
"""

from ramp_optimizer.sample_data import build_normal_scenario
from ramp_optimizer_api.input_schemas import DraftRequest, RevisionRequest
from ramp_optimizer_api.input_services import InputService
from ramp_optimizer_api.mapping import operational_day_to_request
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.settings import DatabaseSettings


def main() -> None:
    scenario = build_normal_scenario(solver_time_limit_seconds=10)
    engine = create_database_engine(DatabaseSettings.from_environment().database_url)
    service = InputService(make_session_factory(engine))
    try:
        if service.list(scenario.day.operational_date, 1, 0):
            print("Demo date already has inputs; leaving them unchanged.")
            return
        draft = service.create(
            scenario.day.operational_date,
            DraftRequest(idempotency_key="supervisor-demo-draft", reason="Fictional demo draft"),
        )
        version = service.create(
            scenario.day.operational_date,
            RevisionRequest(
                idempotency_key="supervisor-demo-input",
                expected_parent_hash=draft.content_hash,
                input=operational_day_to_request(scenario.day, scenario.config),
                reason="Fictional 24-flight demo; not live airline data",
            ),
            str(draft.id),
        )
        print(f"Fictional demo ready: {version.operational_date}, version {version.version_number}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
