"""Explicit, idempotent offline setup; preserves existing published configuration."""

import argparse

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.ai_models import Provider, ProviderModel, TaskModelRoute
from app.db.session import get_engine
from app.db.summary_models import PromptPublication, PromptTemplate, PromptVersion
from app.domains.jobs.schemas import Capabilities, RoutePolicy
from app.domains.summaries.prompts import DEFAULT_BODY, VARIABLE_SCHEMA
from app.domains.summaries.schemas import TASKS


def seed(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(606060)"))
    provider = Provider(
        name="Демонстрация резюме (mock)", protocol="mock", base_url="", host=""
    )
    db.add(provider)
    db.flush()
    models = []
    for name in ("mock-summary-v1", "mock-validation-v1"):
        model = ProviderModel(
            provider_id=provider.id,
            model_identifier=name,
            capabilities=Capabilities(max_context_tokens=1000000).model_dump(),
            pricing={},
        )
        db.add(model)
        db.flush()
        models.append(model)
    needed = False
    for task in TASKS:
        if not db.scalar(
            select(TaskModelRoute).where(TaskModelRoute.task_type == task)
        ):
            needed = True
            db.add(
                TaskModelRoute(
                    task_type=task,
                    primary_model_id=models[task == "validation"].id,
                    config_version=1,
                    policy=RoutePolicy(max_output_tokens=4096).model_dump(),
                )
            )
        template = db.scalar(
            select(PromptTemplate).where(PromptTemplate.task_type == task)
        )
        if not template:
            template = PromptTemplate(task_type=task, name=task)
            db.add(template)
            db.flush()
            version = PromptVersion(
                template_id=template.id,
                version=1,
                body=DEFAULT_BODY,
                variables_schema=VARIABLE_SCHEMA,
                status="published",
            )
            db.add(version)
            db.flush()
            db.add(PromptPublication(template_id=template.id, version_id=version.id))
    if not needed:
        for model in models:
            db.delete(model)
        db.flush()
        db.delete(provider)
    db.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed-summary-mock"])
    parser.parse_args()
    with Session(get_engine()) as db:
        seed(db)
    print(
        "Offline summary routes/prompts configured; existing configuration preserved."
    )


if __name__ == "__main__":
    main()
