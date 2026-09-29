"""
Données de démonstration : circuits de l'île et pistes audio.

⚠️  Coordonnées GPS APPROXIMATIVES, à relever sur le terrain avant la mise
en production. Les contenus audio doivent être validés par des historiens.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Circuit, EtapeCircuit, PisteAudio

CIRCUITS = [
    {"nom": "Sur les pas de la mémoire", "theme": "Mémoire", "duree_min": 90,
     "ton_memoriel": True, "etapes": [
         ("Maison des Esclaves", 14.66711, -17.39837, ["fr", "en", "wo"]),
         ("Musée historique", 14.66568, -17.39700, ["fr", "en"]),
     ]},
    {"nom": "Montée au Castel", "theme": "Panorama", "duree_min": 60,
     "ton_memoriel": False, "etapes": [
         ("Plateau du Castel", 14.66470, -17.39930, ["fr", "en"]),
     ]},
    {"nom": "Gorée des artistes", "theme": "Culture", "duree_min": 60,
     "ton_memoriel": False, "etapes": [
         ("Ateliers des peintres sous-verre", 14.66640, -17.39790, ["fr"]),
     ]},
]


def charger_circuits(db: Session) -> None:
    if db.scalar(select(Circuit.id)):
        return
    for c in CIRCUITS:
        circuit = Circuit(nom=c["nom"], theme=c["theme"], duree_min=c["duree_min"],
                          ton_memoriel=c["ton_memoriel"])
        for ordre, (nom, lat, lon, langues) in enumerate(c["etapes"]):
            etape = EtapeCircuit(ordre=ordre, nom=nom, lat=lat, lon=lon)
            slug = nom.lower().replace(" ", "_")
            etape.pistes = [PisteAudio(langue=l, url=f"/audio/{slug}_{l}.mp3")
                            for l in langues]
            circuit.etapes.append(etape)
        db.add(circuit)
    db.commit()
