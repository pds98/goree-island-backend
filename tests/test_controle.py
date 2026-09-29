"""Contrôle terrain : scan en ligne, erreurs de guichet, hors ligne, report."""

from __future__ import annotations

from datetime import datetime, timezone

from tests.conftest import (acheter_chaloupe, acheter_site, acheter_site_national,
                            creer_agent, jour)


def _scan(client, agent, jeton):
    return client.post("/api/v3/scan", json={"jeton": jeton}, headers=agent).json()


def test_parcours_complet_et_reutilisation(client):
    dakar = creer_agent(client, "EMBARCADERE_DAKAR")
    site = creer_agent(client, "ENTREE_SITE")
    goree = creer_agent(client, "EMBARCADERE_GOREE")
    chaloupe = acheter_chaloupe(client, passagers=2)["billets"][0]
    entree = acheter_site(client, pack="PREMIUM").json()["billets"][0]

    r = _scan(client, dakar, chaloupe["coupons"][0]["jeton"])
    assert r["autorise"] and r["billet"]["passagers"] == 2
    assert "aucune pièce" in r["billet"]["consigne"].lower()
    assert r["restants"] == ["Traversée RETOUR"]

    assert _scan(client, site, entree["coupons"][0]["jeton"])["autorise"]
    assert _scan(client, goree, chaloupe["coupons"][1]["jeton"])["autorise"]
    assert _scan(client, dakar, chaloupe["coupons"][0]["jeton"])["motif"] == "DEJA_UTILISE"


def test_mauvais_guichet_ne_brule_pas_le_coupon(client):
    site = creer_agent(client, "ENTREE_SITE")
    dakar = creer_agent(client, "EMBARCADERE_DAKAR")
    jeton = acheter_chaloupe(client)["billets"][0]["coupons"][0]["jeton"]
    assert _scan(client, site, jeton)["motif"] == "MAUVAIS_GUICHET"
    assert _scan(client, dakar, jeton)["autorise"]      # toujours valable


def test_agent_site_voit_photo_et_consigne_cni(client):
    site = creer_agent(client, "ENTREE_SITE")
    billet = acheter_site_national(client, enfants=2)["billets"][0]
    r = _scan(client, site, billet["coupons"][0]["jeton"])
    assert r["autorise"]
    assert r["billet"]["cni_4_derniers"] == "4567"
    assert "4567" in r["billet"]["consigne"]
    assert "parent titulaire" in r["billet"]["consigne_enfants"]
    photo = client.get(r["billet"]["photo_url"], headers=site)
    assert photo.status_code == 200 and photo.content == b"visage:fatou"
    # La photo n'est pas accessible à un agent d'embarcadère.
    dakar = creer_agent(client, "EMBARCADERE_DAKAR")
    assert client.get(r["billet"]["photo_url"], headers=dakar).status_code == 403


def test_qr_falsifie_et_hors_date(client):
    dakar = creer_agent(client, "EMBARCADERE_DAKAR")
    jeton = acheter_chaloupe(client)["billets"][0]["coupons"][0]["jeton"]
    v, charge, sig = jeton.split(".")
    falsifie = f"{v}.{charge[:-2]}AA.{sig}"
    assert _scan(client, dakar, falsifie)["motif"] == "SIGNATURE_INVALIDE"
    demain = acheter_chaloupe(client, date_visite=jour(1))["billets"][0]
    assert _scan(client, dakar, demain["coupons"][0]["jeton"])["motif"] == "HORS_DATE"


def test_scan_sans_cle_agent_refuse(client):
    r = client.post("/api/v3/scan", json={"jeton": "x"}, headers={"X-Cle-Agent": "faux"})
    assert r.status_code == 401


def test_report_revoque_l_ancien_qr(client):
    site = creer_agent(client, "ENTREE_SITE")
    cmd = acheter_site(client, telephone="+221761112233").json()
    billet = cmd["billets"][0]
    ancien = billet["coupons"][0]["jeton"]
    r = client.post(f"/api/v3/billets/{billet['numero']}/report",
                    json={"telephone": "+221761112233", "nouvelle_date": jour(1)})
    assert r.status_code == 200
    assert r.json()["coupons"][0]["jeton"] != ancien
    assert _scan(client, site, ancien)["motif"] == "JETON_REVOQUE"
    # Un seul report autorisé.
    r2 = client.post(f"/api/v3/billets/{billet['numero']}/report",
                     json={"telephone": "+221761112233", "nouvelle_date": jour(2)})
    assert r2.json()["code"] == "REPORT_DEJA_FAIT"


def test_billet_introuvable_avec_mauvais_telephone(client):
    numero = acheter_chaloupe(client)["billets"][0]["numero"]
    assert client.get(f"/api/v3/billets/{numero}",
                      params={"telephone": "+33000000"}).status_code == 404


def test_hors_ligne_manifeste_sync_et_conflit(client):
    tel_a = creer_agent(client, "EMBARCADERE_DAKAR", "Téléphone A")
    tel_b = creer_agent(client, "EMBARCADERE_DAKAR", "Téléphone B")
    billet = acheter_chaloupe(client)["billets"][0]
    jeton = billet["coupons"][0]["jeton"]

    manifeste = client.get("/api/v3/agents/manifeste", headers=tel_a).json()
    assert "BEGIN PUBLIC KEY" in manifeste["cle_publique_pem"]
    coupon_id = next(iter(manifeste["coupons"]))
    assert manifeste["coupons"][coupon_id]["numero"] == billet["numero"]

    # Le téléphone B, resté en ligne, valide le billet…
    assert _scan(client, tel_b, jeton)["autorise"]
    # …pendant que A l'a aussi validé hors ligne : la synchro révèle le conflit.
    sync = client.post("/api/v3/scan/sync", headers=tel_a, json={"scans": [
        {"coupon_id": coupon_id, "horodatage": datetime.now(timezone.utc).isoformat()}]})
    assert sync.json() == {"appliques": 0, "conflits": [billet["numero"]]}
    conflits = client.get("/api/v3/admin/conflits", headers={"X-Cle-Admin": "admin-test"})
    assert len(conflits.json()) == 1


def test_sync_applique_un_scan_hors_ligne(client):
    tel = creer_agent(client, "EMBARCADERE_DAKAR")
    acheter_chaloupe(client)
    coupon_id = next(iter(client.get("/api/v3/agents/manifeste", headers=tel)
                          .json()["coupons"]))
    r = client.post("/api/v3/scan/sync", headers=tel, json={"scans": [
        {"coupon_id": coupon_id, "horodatage": datetime.now(timezone.utc).isoformat()}]})
    assert r.json() == {"appliques": 1, "conflits": []}
    assert client.get("/api/v3/agents/manifeste", headers=tel).json()["coupons"] == {}
