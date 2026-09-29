"""
Paiement via un agrégateur (PayDunya / CinetPay…) routant vers Wave,
Orange Money et la carte.

Flux réel :
  1. création de la commande (statut EN_ATTENTE_PAIEMENT) ;
  2. `initier()` renvoie une URL de paiement que l'app ouvre ;
  3. le prestataire appelle notre webhook, signé HMAC ;
  4. le webhook confirme → billets activés, ou échec → billets annulés.

En mode `PAIEMENT_SIMULE=true`, l'étape 3 est court-circuitée : le
paiement est confirmé immédiatement (développement, démo, tests).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from app.config import settings
from app.models import Commande


@dataclass
class SessionPaiement:
    reference: str
    url_paiement: str | None


def initier(commande: Commande) -> SessionPaiement:
    reference = f"PAY-{secrets.token_hex(6).upper()}"
    if settings.paiement_simule:
        return SessionPaiement(reference, None)
    # Ici : appel HTTP à l'agrégateur avec montant, moyen, reference, URL de retour.
    return SessionPaiement(reference,
                           f"https://paiement.exemple/checkout/{reference}")


def signer_webhook(corps: bytes) -> str:
    return hmac.new(settings.secret_webhook_paiement.encode(), corps,
                    hashlib.sha256).hexdigest()


def webhook_authentique(corps: bytes, signature: str) -> bool:
    return hmac.compare_digest(signer_webhook(corps), signature or "")
