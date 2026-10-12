"""Explicit local setup: python -m app.domains.jobs.cli seed-mock."""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.ai_models import Provider, ProviderModel, TaskModelRoute
from app.db.session import get_engine
from app.domains.jobs.schemas import Capabilities, RoutePolicy


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed-mock"])
    parser.parse_args()
    with Session(get_engine()) as db:
        # Serialize across invocations using the database's advisory lock (PostgreSQL).
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy import text

            db.execute(text("SELECT pg_advisory_xact_lock(505050)"))
        route = db.scalar(
            select(TaskModelRoute)
            .where(TaskModelRoute.task_type == "diagnostic")
            .limit(1)
        )
        if route:
            print("Diagnostic route already configured; unchanged.")
            return
        provider = Provider(
            name="Локальный mock LLM", protocol="mock", base_url="", host=""
        )
        db.add(provider)
        db.flush()
        model = ProviderModel(
            provider_id=provider.id,
            model_identifier="mock-structured-v1",
            capabilities=Capabilities().model_dump(),
            pricing={},
        )
        db.add(model)
        db.flush()
        db.add(
            TaskModelRoute(
                task_type="diagnostic",
                primary_model_id=model.id,
                config_version=1,
                policy=RoutePolicy().model_dump(),
            )
        )
        db.commit()
        print("Offline diagnostic route configured. No API keys or book text required.")


if __name__ == "__main__":
    main()
