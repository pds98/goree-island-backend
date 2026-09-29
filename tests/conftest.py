"""
Fixtures communes. Par défaut les tests tournent sur SQLite en mémoire
(aucun PostgreSQL requis). Pour les lancer sur PostgreSQL :
    TEST_DATABASE_URL=postgresql+psycopg://user@hote:port/base pytest
La variable d'environnement est posée AVANT l'import de l'application.
"""

from __future__ import annotations

import json
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="goree-tests-")
os.environ.update(DATABASE_URL=os.getenv("TEST_DATABASE_URL", "sqlite://"), DOSSIER_CLES=f"{_tmp}/cles",
                  DOSSIER_PHOTOS=f"{_tmp}/photos", CLE_ADMIN="admin-test",
                  PAIEMENT_SIMULE="true", SEED_DEMO="true")

from datetime import date, datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.services.billetterie import aujourd_hui  # noqa: E402

ADMIN = {"X-Cle-Admin": "admin-test"}


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:          # le lifespan recrée tables + circuits
        yield c


# --- Aides ------------------------------------------------------------------

def jour(decalage: int = 0) -> str:
    return (aujourd_hui() + timedelta(days=decalage)).isoformat()


def creer_agent(client, poste: str, nom: str = "Agent") -> dict:
    r = client.post("/api/v3/admin/agents", json={"nom": nom, "poste": poste}, headers=ADMIN)
    assert r.status_code == 201
    return {"X-Cle-Agent": r.json()["cle_api"]}


def acheter_chaloupe(client, telephone="+221770000001", passagers=1, **extra) -> dict:
    corps = {"nom": "Moussa Sow", "telephone": telephone, "date_visite": jour(),
             "trajet": "ALLER_RETOUR", "passagers": passagers, "moyen_paiement": "WAVE"}
    corps.update(extra)
    r = client.post("/api/v3/chaloupe/commandes", json=corps)
    assert r.status_code == 201, r.text
    return r.json()


def verifier_cni(client, numero="1751199204567", visage="fatou", selfie_visage=None,
                 vivant=True, expiration="2031-01-01"):
    carte = {"numero": numero, "nom": "NDIAYE Fatou", "visage": visage,
             "expiration": expiration}
    selfie = {"visage": selfie_visage or visage, "vivant": vivant}
    return client.post("/api/v3/identite/verifications", files={
        "image_cni": ("cni.jpg", json.dumps(carte).encode(), "image/jpeg"),
        "selfie": ("selfie.jpg", json.dumps(selfie).encode(), "image/jpeg")})


def acheter_site(client, telephone="+221770000002", verification_id=None, **extra):
    corps = {"nom": "Fatou Ndiaye", "telephone": telephone, "date_visite": jour(),
             "verification_id": verification_id, "moyen_paiement": "WAVE"}
    corps.update(extra)
    return client.post("/api/v3/site/commandes", json=corps)


def acheter_site_national(client, telephone="+221770000002", numero="1751199204567", **extra):
    v = verifier_cni(client, numero=numero)
    assert v.status_code == 201, v.text
    r = acheter_site(client, telephone, v.json()["verification_id"], **extra)
    assert r.status_code == 201, r.text
    return r.json()
