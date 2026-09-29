"""Moteur SQLAlchemy, session et dépendance FastAPI `get_db`."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


class Base(DeclarativeBase):
    """Classe mère de tous les modèles ORM."""


def creer_engine(url: str):
    if url.startswith("sqlite"):
        # SQLite en mémoire (tests) : une seule connexion partagée.
        return create_engine(url, connect_args={"check_same_thread": False},
                             poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True)


engine = creer_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Une session par requête HTTP, fermée quoi qu'il arrive."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
