"""Retards, audio-guide, marketplace en séquestre, avis vérifiés, caisses."""

from __future__ import annotations

from datetime import datetime, time, timezone

from tests.conftest import (ADMIN, acheter_chaloupe, acheter_site, creer_agent, jour)
from app.services.billetterie import aujourd_hui

MAISON_DES_ESCLAVES = {"lat": 14.66705, "lon": -17.39840}


def _rotation(client, depart="Dakar", heure=10):
    quand = datetime.combine(aujourd_hui(), time(heure), tzinfo=timezone.utc)
    r = client.post("/api/v3/admin/rotations", headers=ADMIN, json={
        "nom": f"{heure}h00 {depart}", "depart": depart,
        "heure_prevue": quand.isoformat(), "capacite": 350})
    return r.json()["id"]


def _entrer(client, billet):
    site = creer_agent(client, "ENTREE_SITE")
    assert client.post("/api/v3/scan", json={"jeton": billet["coupons"][0]["jeton"]},
                       headers=site).json()["autorise"]


def test_retard_ne_notifie_que_la_chaloupe(client):
    rot = _rotation(client)
    acheter_chaloupe(client, telephone="+221770000010", rotation_id=rot)
    acheter_chaloupe(client, telephone="+221770000011", rotation_id=rot)
    acheter_site(client, telephone="+393400000000")       # entrée seule : pas notifiée
    r = client.post(f"/api/v3/admin/rotations/{rot}/retard", json={"minutes": 15},
                    headers=ADMIN)
    assert r.json() == {"notifies": 2}
    assert client.get("/api/v3/rotations").json()[0]["retard_minutes"] == 15


def test_annonce_de_retour_indicative(client):
    retour = _rotation(client, "Gorée", 16)
    billet = acheter_chaloupe(client, passagers=3)["billets"][0]
    r = client.post(f"/api/v3/billets/{billet['numero']}/retour",
                    json={"telephone": "+221770000001", "rotation_id": retour})
    assert r.json()["retours_annonces"] == 3


def test_audio_guide_conditions(client):
    decouverte = acheter_site(client, telephone="+221700000001").json()["billets"][0]
    essentiel = acheter_site(client, telephone="+221700000002",
                             pack="ESSENTIEL").json()["billets"][0]

    def position(billet, tel, langue="en"):
        return client.post("/api/v3/audio/position", json={
            "numero": billet["numero"], "telephone": tel, "langue": langue,
            **MAISON_DES_ESCLAVES})

    assert position(essentiel, "+221700000002").json()["code"] == "PAS_ENTRE"
    _entrer(client, essentiel)
    _entrer(client, decouverte)
    r = position(essentiel, "+221700000002").json()
    assert r["etape"] == "Maison des Esclaves" and r["piste"].endswith("_en.mp3")
    assert r["rappel_memoriel"] is True
    assert position(decouverte, "+221700000001").json()["code"] == "PACK_INSUFFISANT"


def _partenaire(client, type_, nom, licence=None, verifier=False):
    p = client.post("/api/v3/admin/partenaires", headers=ADMIN, json={
        "type": type_, "nom": nom, "licence": licence}).json()
    if verifier:
        client.post(f"/api/v3/admin/partenaires/{p['id']}/licence-verifiee", headers=ADMIN)
    return p


def test_marketplace_sequestre_avis_et_caisses(client):
    tel = "+12025550100"
    billet = acheter_site(client, telephone=tel, pack="PREMIUM").json()["billets"][0]
    guide = _partenaire(client, "GUIDE", "Moussa F.", "GT-DK-0421", verifier=True)
    faux_guide = _partenaire(client, "GUIDE", "Non déclaré")
    resto = _partenaire(client, "RESTAURANT", "Chez Anta")

    # Le guide sans licence vérifiée est invisible et non réservable.
    visibles = [p["nom"] for p in client.get("/api/v3/partenaires").json()]
    assert "Non déclaré" not in visibles and "Moussa F." in visibles

    def reserver(pid, montant, description="Prestation"):
        return client.post("/api/v3/reservations", json={
            "numero": billet["numero"], "telephone": tel, "partenaire_id": pid,
            "description": description, "montant": montant, "moyen_paiement": "CARTE"})

    assert reserver(faux_guide["id"], 5_000).json()["code"] == "LICENCE_NON_VERIFIEE"
    res_guide = reserver(guide["id"], 15_000, "Circuit Mémoire 2 h").json()
    res_resto = reserver(resto["id"], 8_000, "2 × Thiéboudienne 13h").json()
    assert res_guide["statut"] == "EN_SEQUESTRE"

    # Avis impossible avant d'être entré sur l'île.
    avis = {"numero": billet["numero"], "telephone": tel,
            "reservation_id": res_guide["reservation_id"], "note": 5, "texte": "Top"}
    assert client.post("/api/v3/avis", json=avis).json()["code"] == "PAS_ENTRE"
    _entrer(client, billet)
    assert client.post("/api/v3/avis", json=avis).json()["code"] == \
        "PRESTATION_NON_CONSOMMEE"

    # Le guide confirme avec SA clé ; le restaurant annule.
    c = client.post(f"/api/v3/reservations/{res_guide['reservation_id']}/confirmer",
                    headers={"X-Cle-Partenaire": guide["cle_api"]}).json()
    assert c == {"statut": "LIBEREE", "net_partenaire": 13_200, "commission": 1_800}
    # Un partenaire ne peut pas confirmer la réservation d'un autre.
    assert client.post(f"/api/v3/reservations/{res_resto['reservation_id']}/confirmer",
                       headers={"X-Cle-Partenaire": guide["cle_api"]}).status_code == 404
    client.post(f"/api/v3/reservations/{res_resto['reservation_id']}/annuler",
                headers={"X-Cle-Partenaire": resto["cle_api"]})

    assert client.post("/api/v3/avis", json=avis).status_code == 201
    assert client.post("/api/v3/avis", json=avis).json()["code"] == "AVIS_EXISTANT"

    # Caisses : chaque franc est à sa place.
    acheter_chaloupe(client, passagers=2)
    k = client.get("/api/v3/admin/caisses", headers=ADMIN).json()
    b, m = k["billetterie"], k["marketplace"]
    assert b["caisse_bateau"] == 3_000 and b["caisse_site"] == 3_000 and b["packs"] == 5_000
    assert b["caisse_bateau"] + b["caisse_site"] + b["packs"] == b["encaisse"]
    assert m["a_reverser_partenaires"] + m["commissions"] + m["sequestre"] \
        + m["rembourse"] == m["encaisse"] == 23_000
    assert k["caisse_plateforme"] == 5_000 + 1_800


def test_marketplace_reservee_au_premium(client):
    tel = "+221700000003"
    billet = acheter_site(client, telephone=tel, pack="ESSENTIEL").json()["billets"][0]
    resto = _partenaire(client, "RESTAURANT", "Chez Anta")
    r = client.post("/api/v3/reservations", json={
        "numero": billet["numero"], "telephone": tel, "partenaire_id": resto["id"],
        "description": "Yassa", "montant": 3_500, "moyen_paiement": "WAVE"})
    assert r.status_code == 403 and r.json()["code"] == "PACK_INSUFFISANT"


def test_admin_protege(client):
    assert client.get("/api/v3/admin/caisses").status_code == 401
