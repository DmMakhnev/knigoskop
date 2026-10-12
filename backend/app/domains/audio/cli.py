import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.audio_models import TTSProfile
from app.db.session import get_engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed-audio-mock", "voices-genvoice"])
    args = parser.parse_args()
    if args.command == "voices-genvoice":
        import json

        from app.core.config import settings
        from app.core.errors import AppError
        from app.integrations.tts import genvoice_public_voices

        try:
            voices = genvoice_public_voices(
                {
                    "protocol": "genvoice",
                    "base_url": settings.tts_base_url,
                    "host": settings.tts_env_provider_host,
                    "encrypted_secret": None,
                }
            )
        except AppError as exc:
            parser.exit(1, f"{exc.code}: {exc.message}\n")
        print(json.dumps(voices, ensure_ascii=False))
        return
    with Session(get_engine()) as db:
        if not db.scalar(select(TTSProfile).where(TTSProfile.protocol == "mock")):
            db.add(
                TTSProfile(
                    name="Демонстрационные сигналы (не речь)",
                    protocol="mock",
                    model="mock-signals-v1",
                    voices=["demo"],
                    price_micros_per_1000_chars=0,
                    currency="RUB",
                )
            )
            db.commit()
    print("Mock audio profile configured")


if __name__ == "__main__":
    main()
