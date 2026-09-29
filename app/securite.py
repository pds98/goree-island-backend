"""
Briques de sécurité :

* Jetons QR signés en **Ed25519** : le serveur signe avec la clé privée,
  les téléphones des agents ne reçoivent que la clé publique. Un agent
  peut donc vérifier un billet hors ligne, mais jamais en fabriquer un.
* Empreinte des numéros de CNI : HMAC-SHA256 avec un sel secret.
* Chiffrement au repos des photos de contrôle (Fernet / AES-128-CBC + HMAC).
* Clés d'API des agents, partenaires et admin : stockées hachées.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey,
                                                               Ed25519PublicKey)

from app.config import settings
from app.models import PointControle

VERSION_JETON = "GI3"


# ---------------------------------------------------------------------------
#  Encodage base64url sans padding (compact dans un QR)
# ---------------------------------------------------------------------------

def b64(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).decode().rstrip("=")


def b64_decoder(texte: str) -> bytes:
    return base64.urlsafe_b64decode(texte + "=" * (-len(texte) % 4))


# ---------------------------------------------------------------------------
#  Clés Ed25519
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def cle_privee() -> Ed25519PrivateKey:
    """
    Ordre de priorité : variable d'environnement (production, injectée par
    le coffre), sinon fichier local généré au premier démarrage (dev).
    """
    if settings.cle_privee_jetons_pem:
        return serialization.load_pem_private_key(
            settings.cle_privee_jetons_pem.encode(), password=None)
    chemin = Path(settings.dossier_cles) / "ed25519_jetons.pem"
    if chemin.exists():
        return serialization.load_pem_private_key(chemin.read_bytes(), password=None)
    cle = Ed25519PrivateKey.generate()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(cle.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    chemin.chmod(0o600)
    return cle


def cle_publique() -> Ed25519PublicKey:
    return cle_privee().public_key()


def cle_publique_pem() -> str:
    """Distribuée aux applis agents dans le manifeste du jour."""
    return cle_publique().public_bytes(serialization.Encoding.PEM,
                                       serialization.PublicFormat.SubjectPublicKeyInfo
                                       ).decode()


# ---------------------------------------------------------------------------
#  Jetons QR
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ContenuJeton:
    coupon_id: str
    point: PointControle
    date_visite: date


def signer_jeton(coupon_id: str, point: PointControle, date_visite: date) -> str:
    """
    GI3.<charge>.<signature>
    charge = coupon_id | point de contrôle | AAAAMMJJ
    → aucune donnée personnelle dans le QR.
    """
    charge = f"{coupon_id}|{point.value}|{date_visite:%Y%m%d}".encode()
    signature = cle_privee().sign(charge)
    return f"{VERSION_JETON}.{b64(charge)}.{b64(signature)}"


def verifier_jeton(jeton: str, publique: Ed25519PublicKey | None = None) -> ContenuJeton | None:
    """Retourne le contenu si la signature est valide, sinon None."""
    try:
        version, charge_b64, sig_b64 = jeton.strip().split(".")
        if version != VERSION_JETON:
            return None
        charge = b64_decoder(charge_b64)
        (publique or cle_publique()).verify(b64_decoder(sig_b64), charge)
        coupon_id, point, jour = charge.decode().split("|")
        return ContenuJeton(coupon_id, PointControle(point),
                            datetime.strptime(jour, "%Y%m%d").date())
    except (ValueError, InvalidSignature, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
#  CNI et clés d'API
# ---------------------------------------------------------------------------

def hacher_cni(numero: str) -> str:
    """HMAC-SHA256 salé : impossible à recalculer sans le sel serveur."""
    return hmac.new(settings.sel_cni.encode(), numero.strip().encode(),
                    hashlib.sha256).hexdigest()


def generer_cle_api() -> tuple[str, str]:
    """Retourne (clé en clair à remettre UNE fois, empreinte à stocker)."""
    cle = secrets.token_urlsafe(32)
    return cle, hacher_cle_api(cle)


def hacher_cle_api(cle: str) -> str:
    return hashlib.sha256(cle.encode()).hexdigest()


# ---------------------------------------------------------------------------
#  Photos de contrôle chiffrées au repos
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if settings.cle_chiffrement_photos:
        return Fernet(settings.cle_chiffrement_photos.encode())
    chemin = Path(settings.dossier_cles) / "fernet_photos.key"
    if not chemin.exists():
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_bytes(Fernet.generate_key())
        chemin.chmod(0o600)
    return Fernet(chemin.read_bytes())


def stocker_photo(identifiant: str, contenu: bytes) -> str:
    chemin = Path(settings.dossier_photos) / f"{identifiant}.bin"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(_fernet().encrypt(contenu))
    return str(chemin)


def lire_photo(reference: str) -> bytes:
    return _fernet().decrypt(Path(reference).read_bytes())
