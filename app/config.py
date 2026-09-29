"""
Configuration de l'application, lue depuis les variables d'environnement.

Toutes les valeurs sensibles (clés, sels, jetons d'API) viennent de
l'environnement ou d'un coffre (KMS / Vault) en production. Les valeurs par
défaut ci-dessous ne servent QU'au développement local et aux tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(nom: str, defaut: bool) -> bool:
    return os.getenv(nom, str(defaut)).strip().lower() in {"1", "true", "yes", "oui"}


@dataclass(frozen=True)
class Settings:
    # --- Base de données ---------------------------------------------------
    # PostgreSQL en production (docker-compose), SQLite pour les tests.
    database_url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL", "postgresql+psycopg://goree:goree@localhost:5432/goree"))

    # --- Secrets -----------------------------------------------------------
    # Sel secret utilisé pour hacher les numéros de CNI (HMAC-SHA256).
    sel_cni: str = field(default_factory=lambda: os.getenv(
        "SEL_CNI", "dev-sel-cni-a-remplacer"))
    # Clé privée Ed25519 (PEM) servant à signer les jetons QR. Si absente en
    # développement, une clé est générée et stockée dans `dossier_cles`.
    cle_privee_jetons_pem: str | None = field(default_factory=lambda: os.getenv(
        "CLE_PRIVEE_JETONS_PEM"))
    dossier_cles: str = field(default_factory=lambda: os.getenv(
        "DOSSIER_CLES", ".cles"))
    # Clé de chiffrement (Fernet) des photos de contrôle au repos.
    cle_chiffrement_photos: str | None = field(default_factory=lambda: os.getenv(
        "CLE_CHIFFREMENT_PHOTOS"))
    dossier_photos: str = field(default_factory=lambda: os.getenv(
        "DOSSIER_PHOTOS", ".photos"))

    # --- Authentification simple des agents et de l'admin -------------------
    # MVP : clés d'API par en-tête. À remplacer par OAuth2/JWT + appareils
    # enrôlés (révocables à distance) avant la mise en production.
    cle_admin: str = field(default_factory=lambda: os.getenv("CLE_ADMIN", "admin-dev"))

    # --- Paiement ------------------------------------------------------------
    # true : le paiement est confirmé immédiatement (dev / démo).
    # false : la commande reste EN_ATTENTE jusqu'au webhook du prestataire.
    paiement_simule: bool = field(default_factory=lambda: _bool("PAIEMENT_SIMULE", True))
    secret_webhook_paiement: str = field(default_factory=lambda: os.getenv(
        "SECRET_WEBHOOK_PAIEMENT", "dev-webhook-secret"))
    delai_expiration_commande_min: int = 15

    # --- Métier --------------------------------------------------------------
    commission_partenaire: float = 0.12
    duree_validite_verification_min: int = 30


settings = Settings()
