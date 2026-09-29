# Gorée Island — Backend (API v3)

Backend de l'application **Gorée Island** : billetterie en ligne de la traversée Dakar ⇄ Gorée et de l'entrée du site historique, contrôle des billets par QR code (y compris hors ligne), services sur l'île (circuits, audio-guide géolocalisé, marketplace de guides, restaurants et hôtels).

Stack : **FastAPI · SQLAlchemy 2 · PostgreSQL 16 · Ed25519 (cryptography)**. Implémente le modèle décrit dans `architecture_billetterie_v3.md`.

---

## Démarrage rapide

```bash
cp .env.example .env            # puis changer les secrets
docker compose up --build       # PostgreSQL + API
```

- Documentation interactive (Swagger) : http://localhost:8000/docs
- Santé : http://localhost:8000/sante

Sans Docker :

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg://goree:goree@localhost:5432/goree
uvicorn app.main:app --reload
```

## Tests

```bash
pytest                                           # SQLite en mémoire, aucune dépendance
TEST_DATABASE_URL=postgresql+psycopg://goree:goree@localhost:5432/goree_test pytest
```

26 tests couvrent les règles métier de bout en bout, via l'API. Ils passent sur SQLite comme sur PostgreSQL 16.

---

## Les deux parcours

| | Parcours 1 — Chaloupe | Parcours 2 — Entrée du site |
|---|---|---|
| Endpoint | `POST /api/v3/chaloupe/commandes` | `POST /api/v3/site/commandes` |
| Tarif | **Unique**, selon trajet × passagers | National (réduit) ou international |
| Identité | **Aucune** : nom + téléphone | National : `POST /api/v3/identite/verifications` (CNI + selfie) |
| Anti-fraude | — | 1 billet national par CNI et par jour (index unique partiel) |
| Accompagnants | Jusqu'à 10 passagers sur un billet | National : enfants seulement · International : adultes et enfants |
| QR | ALLER (Dakar) + RETOUR (Gorée, libre dans la journée) | ENTRÉE (arrivée sur l'île) |

### Exemple : acheter une traversée

```bash
curl -X POST localhost:8000/api/v3/chaloupe/commandes -H 'Content-Type: application/json' -d '{
  "nom": "Moussa Sow", "telephone": "+221770000000", "date_visite": "2026-10-03",
  "trajet": "ALLER_RETOUR", "passagers": 2, "moyen_paiement": "WAVE" }'
```

### Exemple : entrée nationale avec deux enfants

```bash
# 1) vérification d'identité (en dev, les « images » sont du JSON, voir FournisseurSimule)
curl -X POST localhost:8000/api/v3/identite/verifications \
  -F 'image_cni=@cni.json' -F 'selfie=@selfie.json'
# → { "verification_id": "…", "cni_4_derniers": "4567", … }

# 2) achat
curl -X POST localhost:8000/api/v3/site/commandes -H 'Content-Type: application/json' -d '{
  "nom": "Fatou Ndiaye", "telephone": "+221771234567", "date_visite": "2026-10-03",
  "verification_id": "…", "enfants": 2, "pack": "ESSENTIEL", "moyen_paiement": "WAVE" }'
```

---

## Sécurité

- **QR = jeton signé Ed25519** (`GI3.<charge>.<signature>`). La charge contient uniquement `coupon_id | poste | date`, sans aucune donnée personnelle. Les applis agents reçoivent la **clé publique** dans le manifeste : elles vérifient les billets hors ligne mais ne peuvent pas en fabriquer.
- **CNI** : seules sont conservées une empreinte HMAC-SHA256 salée, les 4 derniers chiffres et une photo de contrôle **chiffrée au repos** (Fernet). L'image de la carte et le selfie ne sont jamais écrits sur disque.
- **Ordre du scan** : signature → poste → date → jeton révoqué → déjà utilisé. Un refus ne modifie jamais le statut du coupon, ce qui empêche l'erreur de guichet de « brûler » un billet.
- **Hors ligne** : `GET /api/v3/agents/manifeste` avant la prise de poste, puis `POST /api/v3/scan/sync` au retour du réseau. Les doubles validations sur deux appareils remontent dans `GET /api/v3/admin/conflits`.
- **Paiement** : webhook signé HMAC, idempotent. Un paiement échoué annule les billets et libère le quota CNI.
- **Mode invité** : un visiteur accède à son billet avec le couple (numéro, téléphone). Un téléphone erroné renvoie 404, pour ne pas révéler l'existence du billet.

## Carte des endpoints

| Rôle | Endpoints |
|---|---|
| Visiteur | `GET /rotations` · `POST /chaloupe/commandes` · `POST /identite/verifications` · `POST /site/commandes` · `GET /billets/{n}` · `POST /billets/{n}/report` · `POST /billets/{n}/retour` · `GET /circuits` · `POST /audio/position` · `GET /partenaires` · `POST /reservations` · `POST /avis` |
| Paiement | `POST /paiements/webhook` (en-tête `X-Signature`) |
| Agent (`X-Cle-Agent`) | `POST /scan` · `GET /agents/manifeste` · `POST /scan/sync` · `GET /agents/photos/{id}` |
| Partenaire (`X-Cle-Partenaire`) | `POST /reservations/{id}/confirmer` · `POST /reservations/{id}/annuler` |
| Admin (`X-Cle-Admin`) | `POST /admin/agents` · `POST /admin/rotations` · `POST /admin/rotations/{id}/retard` · `POST /admin/partenaires` · `POST /admin/partenaires/{id}/licence-verifiee` · `GET /admin/caisses` · `GET /admin/conflits` · `POST /admin/maintenance/expirer-commandes` |

Tous les chemins sont préfixés par `/api/v3`. Les erreurs métier renvoient `{"code": "QUOTA_CNI", "message": "…"}` : le code est stable, et l'app mobile s'en sert pour choisir l'écran à afficher.

## Structure

```
app/
  main.py              point d'entrée, gestion des erreurs, création des tables
  config.py            variables d'environnement
  models.py            tables SQLAlchemy (billet, coupon, vérification, réservation…)
  schemas.py           contrat JSON (Pydantic)
  securite.py          Ed25519, HMAC CNI, chiffrement des photos, clés d'API
  tarifs.py            grilles tarifaires (VALEURS PROVISOIRES)
  services/
    billetterie.py     les deux parcours, paiement, report, annonce de retour
    identite.py        OCR + vivant + visage, derrière une interface fournisseur
    paiement.py        agrégateur de paiement + webhook
    scan.py            scan en ligne, manifeste hors ligne, synchronisation
    services_ile.py    audio-guide, marketplace en séquestre, avis, retards, caisses
  routes/
    visiteurs.py       API publique (appli visiteur)
    pro.py             agents, partenaires, admin
tests/                 26 tests de bout en bout
```

## Avant la production

1. **Tarifs officiels** dans `app/tarifs.py` : toutes les valeurs actuelles sont provisoires.
2. **Fournisseur d'identité réel** (KYC opérant au Sénégal) en remplacement de `FournisseurSimule`, et **format du NIN CEDEAO** à confirmer.
3. **Agrégateur de paiement** (PayDunya, CinetPay…) dans `services/paiement.py`, avec la même logique de paiement bloqué pour la marketplace.
4. **Autorisation CDP** pour le traitement biométrique (loi 2008-12), à valider avec un juriste.
5. **Migrations Alembic** à la place de `create_all`, **OAuth2/JWT** et appareils agents enrôlés à la place des clés d'API.
6. **Envoi SMS/push** réel pour les notifications de retard (la table `notification` est déjà alimentée), ainsi que le WebSocket temps réel.
7. **Coordonnées GPS** des étapes relevées sur place, et contenus audio validés par des historiens.
8. **Génération du billet PDF** et passes Apple/Google Wallet.
