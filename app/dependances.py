"""Authentification MVP par clés d'API (agents, partenaires, admin)."""

from __future__ import annotations

import hmac

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.erreurs import ErreurMetier
from app.models import Agent, Partenaire
from app.securite import hacher_cle_api


def _non_autorise() -> ErreurMetier:
    return ErreurMetier("NON_AUTORISE", "Authentification requise.", 401)


def exiger_admin(x_cle_admin: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_cle_admin, settings.cle_admin):
        raise _non_autorise()


def agent_courant(x_cle_agent: str = Header(default=""),
                  db: Session = Depends(get_db)) -> Agent:
    agent = db.scalar(select(Agent).where(Agent.cle_api_hash == hacher_cle_api(x_cle_agent)))
    if agent is None or not agent.actif:
        raise _non_autorise()
    return agent


def partenaire_courant(x_cle_partenaire: str = Header(default=""),
                       db: Session = Depends(get_db)) -> Partenaire:
    p = db.scalar(select(Partenaire).where(
        Partenaire.cle_api_hash == hacher_cle_api(x_cle_partenaire)))
    if p is None or not p.actif:
        raise _non_autorise()
    return p
