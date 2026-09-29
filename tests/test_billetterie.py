"""Les deux parcours d'achat et leurs règles tarifaires / anti-fraude."""

from __future__ import annotations

import json
from dataclasses import replace

from app.config import settings
from app.securite import b64_decoder
from app.services import paiement
from tests.conftest import (acheter_chaloupe, acheter_site, acheter_site_national, jour,
                            verifier_cni)


def charge_du_jeton(jeton: str) -> str:
    """Ce que n'importe quelle appli de lecture QR verrait en clair."""
    return b64_decoder(jeton.split(".")[1]).decode()


# --- Parcours 1 : chaloupe ----------------------------------------------------

def test_chaloupe_tarif_unique_sans_identite(client):
    cmd = acheter_chaloupe(client, passagers=3)
    billet = cmd["billets"][0]
    assert cmd["statut"] == "PAYEE"
    assert cmd["montant"] == 3 * 1_500                 # même prix pour tous
    assert billet["nationalite"] is None               # la nationalité n'existe pas ici
    assert [c["libelle"] for c in billet["coupons"]] == ["Traversée ALLER", "Traversée RETOUR"]
    assert all(c["statut"] == "VALIDE" for c in billet["coupons"])


def test_chaloupe_aller_simple_un_seul_coupon(client):
    cmd = acheter_chaloupe(client, trajet="ALLER_SIMPLE", passagers=2)
    assert cmd["montant"] == 2 * 750
    assert len(cmd["billets"][0]["coupons"]) == 1


def test_chaloupe_trop_de_passagers(client):
    r = client.post("/api/v3/chaloupe/commandes", json={
        "nom": "Groupe", "telephone": "+221770000009", "date_visite": jour(),
        "passagers": 11, "moyen_paiement": "WAVE"})
    assert r.status_code == 422 and r.json()["code"] == "PASSAGERS"


def test_qr_sans_donnee_personnelle(client):
    cmd = acheter_chaloupe(client)
    charge = charge_du_jeton(cmd["billets"][0]["coupons"][0]["jeton"])
    assert "Moussa" not in charge and "+221" not in charge
    assert charge.count("|") == 2                       # coupon_id | poste | date


# --- Parcours 2 : entrée du site ----------------------------------------------

def test_international_avec_accompagnants_et_pack(client):
    r = acheter_site(client, adultes_supp=1, enfants=1, pack="PREMIUM")
    assert r.status_code == 201
    corps = r.json()
    assert corps["montant"] == 2 * 3_000 + 1_500 + 5_000
    assert corps["billets"][0]["pack"] == "PREMIUM"
    assert corps["billets"][0]["nationalite"] == "INTERNATIONAL"


def test_national_verifie_avec_enfants(client):
    cmd = acheter_site_national(client, enfants=2, pack="ESSENTIEL")
    assert cmd["montant"] == 500 + 2 * 200 + 2_000
    assert cmd["billets"][0]["nationalite"] == "NATIONAL"


def test_quota_une_cni_par_jour(client):
    acheter_site_national(client)
    v = verifier_cni(client)                            # même CNI, même visage
    r = acheter_site(client, "+221780000000", v.json()["verification_id"])
    assert r.status_code == 409 and r.json()["code"] == "QUOTA_CNI"
    # …mais un autre jour, c'est permis
    v = verifier_cni(client)
    assert acheter_site(client, "+221780000000", v.json()["verification_id"],
                        date_visite=jour(1)).status_code == 201


def test_adulte_national_ne_peut_pas_accompagner(client):
    v = verifier_cni(client)
    r = acheter_site(client, verification_id=v.json()["verification_id"], adultes_supp=1)
    assert r.status_code == 422 and r.json()["code"] == "ADULTE_NATIONAL_ACCOMPAGNANT"


def test_fraudes_identite(client):
    assert verifier_cni(client, selfie_visage="autre").json()["code"] == "VISAGE_DIFFERENT"
    assert verifier_cni(client, vivant=False).json()["code"] == "SELFIE_NON_VIVANT"
    assert verifier_cni(client, expiration="2020-01-01").json()["code"] == "CNI_EXPIREE"
    assert verifier_cni(client, numero="12AB").json()["code"] == "CNI_ILLISIBLE"


def test_verification_non_reutilisable(client):
    v = verifier_cni(client).json()["verification_id"]
    assert acheter_site(client, verification_id=v).status_code == 201
    r = acheter_site(client, verification_id=v, date_visite=jour(1))
    assert r.json()["code"] == "VERIF_DEJA_UTILISEE"


# --- Paiement réel (webhook) ---------------------------------------------------

def _mode_webhook(monkeypatch):
    reel = replace(settings, paiement_simule=False)
    monkeypatch.setattr("app.services.billetterie.settings", reel)
    monkeypatch.setattr("app.services.paiement.settings", reel)


def _webhook(client, reference, statut, signature=None):
    corps = json.dumps({"reference": reference, "statut": statut}).encode()
    return client.post("/api/v3/paiements/webhook", content=corps,
                       headers={"X-Signature": signature or paiement.signer_webhook(corps),
                                "Content-Type": "application/json"})


def _reference(client, commande_id):
    from app.db import SessionLocal
    from app.models import Commande
    with SessionLocal() as db:
        return db.get(Commande, commande_id).reference_paiement


def test_paiement_echoue_libere_le_quota(client, monkeypatch):
    _mode_webhook(monkeypatch)
    v = verifier_cni(client).json()["verification_id"]
    cmd = acheter_site(client, verification_id=v).json()
    assert cmd["statut"] == "EN_ATTENTE_PAIEMENT" and cmd["url_paiement"]
    assert cmd["billets"][0]["coupons"][0]["statut"] == "EN_ATTENTE"

    ref = _reference(client, cmd["commande_id"])
    assert _webhook(client, ref, "ECHOUE", signature="fausse").status_code == 401
    assert _webhook(client, ref, "ECHOUE").json()["statut"] == "ECHOUEE"
    # Idempotence : un « REUSSI » tardif ne ressuscite pas la commande.
    assert _webhook(client, ref, "REUSSI").json()["statut"] == "ECHOUEE"

    # Le quota est libéré et la vérification peut resservir.
    cmd2 = acheter_site(client, verification_id=v).json()
    assert _webhook(client, _reference(client, cmd2["commande_id"]),
                    "REUSSI").json()["statut"] == "PAYEE"


# --- Mode démo d'identité (app mobile sans moteur biométrique) ----------------

def _verifier_demo(client, numero="1751199204567"):
    return client.post("/api/v3/identite/verifications",
                       data={"numero_cni": numero, "nom": "NDIAYE Fatou"},
                       files={"image_cni": ("cni.jpg", b"\xff\xd8photo-cni", "image/jpeg"),
                              "selfie": ("selfie.jpg", b"\xff\xd8vrai-selfie", "image/jpeg")})


def test_mode_demo_refuse_si_inactif(client):
    r = _verifier_demo(client)
    assert r.status_code == 403 and r.json()["code"] == "MODE_DEMO_INACTIF"


def test_mode_demo_selfie_devient_photo_de_controle(client, monkeypatch):
    monkeypatch.setattr("app.services.identite.settings", replace(settings, identite_demo=True))
    v = _verifier_demo(client).json()
    assert v["cni_4_derniers"] == "4567"
    billet = acheter_site(client, verification_id=v["verification_id"]).json()["billets"][0]
    from tests.conftest import creer_agent
    site = creer_agent(client, "ENTREE_SITE")
    scan = client.post("/api/v3/scan", json={"jeton": billet["coupons"][0]["jeton"]},
                       headers=site).json()
    assert client.get(scan["billet"]["photo_url"], headers=site).content == b"\xff\xd8vrai-selfie"
    # Le quota s'applique aussi en mode démo.
    v2 = _verifier_demo(client).json()
    assert acheter_site(client, verification_id=v2["verification_id"]).json()["code"] == "QUOTA_CNI"
