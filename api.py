#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 - API controlee M1-M5 V1.1.

Cinq moteurs independants.
API en lecture seule du depot.
Aucune modification des FIGEs, archives ou pronostics.
Aucun commit, push ou deploiement automatique.
"""

from __future__ import annotations

import importlib
import logging
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

VERSION = "1.1"
PARIS = ZoneInfo("Europe/Paris")
LOG = logging.getLogger("x1000.api")

ORIGINS = [
    "https://yadcaf-cloud.github.io",
]

MOTEURS = {
    f"M{i}": f"moteurs.m{i}"
    for i in range(1, 6)
}

COURSE_ID = re.compile(
    r"^(\d{4}-\d{2}-\d{2})-R[1-9]\d*-C[1-9]\d*$"
)

app = FastAPI(
    title="X1000 Intelligence Hippique API",
    version=VERSION,
    description="API controlee des moteurs M1-M5",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
    max_age=600,
)


class DemandeAnalyse(BaseModel):
    course: dict = Field(
        description="Course francaise documentee"
    )


def maintenant():
    return datetime.now(PARIS).isoformat()


def charger_moteur(nom: str):
    nom = nom.upper()

    if nom not in MOTEURS:
        raise HTTPException(
            status_code=404,
            detail="Moteur inconnu",
        )

    try:
        module = importlib.import_module(
            MOTEURS[nom]
        )
    except (ImportError, SyntaxError):
        LOG.exception("Chargement impossible: %s", nom)
        raise HTTPException(
            status_code=503,
            detail=f"{nom}: module indisponible",
        ) from None

    if not callable(
        getattr(module, "analyser_course", None)
    ):
        raise HTTPException(
            status_code=503,
            detail=f"{nom}: fonction analyse absente",
        )

    if getattr(module, "MOTEUR", None) != nom:
        raise HTTPException(
            status_code=503,
            detail=f"{nom}: identite incoherente",
        )

    return module


def verifier_course(course: dict):
    if not isinstance(course, dict):
        raise ValueError("Structure course invalide")

    identifiant = course.get("course_id")

    if not isinstance(identifiant, str):
        raise ValueError("course_id obligatoire")

    match = COURSE_ID.fullmatch(identifiant)

    if not match:
        raise ValueError(
            "Format attendu: YYYY-MM-DD-Rx-Cy"
        )

    try:
        jour = date.fromisoformat(match.group(1))
    except ValueError as exc:
        raise ValueError(
            "Date de course invalide"
        ) from exc

    if jour > datetime.now(PARIS).date():
        raise ValueError(
            "Course future: analyse non autorisee "
            "sans controle temporel adapte"
        )

    if course.get("pays_course") != "FR":
        raise ValueError(
            "Pays FR non confirme"
        )

    if course.get("source_verifiee") is not True:
        raise ValueError(
            "Attestation source absente"
        )

    source = course.get("source")

    if not isinstance(source, str) or not source.strip():
        raise ValueError(
            "Reference source obligatoire"
        )

    hippodrome = course.get("hippodrome")

    if (
        not isinstance(hippodrome, str)
        or not hippodrome.strip()
    ):
        raise ValueError(
            "Hippodrome obligatoire"
        )

    partants = course.get("partants")

    if not isinstance(partants, list):
        raise ValueError(
            "Liste des partants obligatoire"
        )

    if not 5 <= len(partants) <= 40:
        raise ValueError(
            "Nombre de partants hors limites"
        )

    numeros = set()
    actifs = 0

    for cheval in partants:
        if not isinstance(cheval, dict):
            raise ValueError(
                "Structure partant invalide"
            )

        numero = cheval.get("numero")

        if (
            type(numero) is not int
            or numero <= 0
            or numero in numeros
        ):
            raise ValueError(
                "Numero invalide ou duplique"
            )

        numeros.add(numero)

        if type(
            cheval.get("non_partant", False)
        ) is not bool:
            raise ValueError(
                "Statut non_partant invalide"
            )

        if cheval.get("non_partant") is not True:
            actifs += 1

    if actifs < 5:
        raise ValueError(
            "Moins de cinq partants actifs"
        )

    return identifiant


def executer_moteur(nom, course):
    module = charger_moteur(nom)

    try:
        resultat = module.analyser_course(
            course.copy()
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"{nom}: {exc}",
        ) from exc
    except Exception:
        LOG.exception("Erreur interne %s", nom)
        raise HTTPException(
            status_code=500,
            detail=f"{nom}: erreur interne",
        ) from None

    if not isinstance(resultat, dict):
        raise HTTPException(
            status_code=502,
            detail=f"{nom}: resultat invalide",
        )

    if resultat.get("moteur") != nom:
        raise HTTPException(
            status_code=502,
            detail=f"{nom}: identite resultat invalide",
        )

    if resultat.get("fige") is not False:
        raise HTTPException(
            status_code=502,
            detail=f"{nom}: statut FIGE interdit",
        )

    if resultat.get("publication_officielle") is not False:
        raise HTTPException(
            status_code=502,
            detail=f"{nom}: publication interdite",
        )

    classement = resultat.get("classement")

    if (
        not isinstance(classement, list)
        or len(classement) != 5
    ):
        raise HTTPException(
            status_code=502,
            detail=f"{nom}: classement invalide",
        )

    numeros = [
        c.get("numero")
        for c in classement
        if isinstance(c, dict)
    ]

    if (
        len(numeros) != 5
        or any(type(n) is not int for n in numeros)
        or len(set(numeros)) != 5
    ):
        raise HTTPException(
            status_code=502,
            detail=f"{nom}: classement incoherent",
        )

    return resultat


@app.get("/")
def accueil():
    return {
        "systeme": "X1000",
        "version": VERSION,
        "api": "active",
        "mode": "lecture_seule",
        "moteurs": list(MOTEURS),
        "publication_officielle": False,
    }


@app.get("/api/status")
def status():
    etats = {}

    for nom in MOTEURS:
        try:
            module = charger_moteur(nom)
            etats[nom] = {
                "statut": "charge",
                "version": getattr(
                    module, "VERSION", "inconnue"
                ),
                "analyse_verifiee": False,
            }
        except HTTPException:
            etats[nom] = {
                "statut": "indisponible",
                "analyse_verifiee": False,
            }

    disponibles = sum(
        x["statut"] == "charge"
        for x in etats.values()
    )

    return {
        "systeme": "X1000",
        "version": VERSION,
        "date": maintenant(),
        "api": "active",
        "moteurs": etats,
        "moteurs_charges": disponibles,
        "moteurs_attendus": 5,
        "coordination": "diagnostic_uniquement",
        "source_pmu_verifiee_en_ligne": False,
        "figes_modifies": False,
        "archives_modifiees": False,
    }


@app.get("/api/moteurs")
def moteurs():
    return status()["moteurs"]


@app.post("/api/analyser/{nom}")
def analyser(nom: str, demande: DemandeAnalyse):
    nom = nom.upper()

    if nom not in MOTEURS:
        raise HTTPException(
            status_code=404,
            detail="Moteur inconnu",
        )

    try:
        verifier_course(demande.course)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return executer_moteur(
        nom, demande.course
    )


@app.post("/api/analyser")
def analyser_tous(demande: DemandeAnalyse):
    try:
        identifiant = verifier_course(
            demande.course
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    resultats = {}

    for nom in MOTEURS:
        try:
            resultat = executer_moteur(
                nom, demande.course
            )
            resultats[nom] = {
                "statut": "analyse_disponible",
                "resultat": resultat,
            }
        except HTTPException as exc:
            resultats[nom] = {
                "statut": "analyse_indisponible",
                "code": exc.status_code,
                "erreur": exc.detail,
            }

    return {
        "systeme": "X1000",
        "course_id": identifiant,
        "date_analyse": maintenant(),
        "resultats_independants": resultats,
        "coordination": "resultats_non_arbitres",
        "arbitrage_officiel": None,
        "fige": False,
        "publication_officielle": False,
        "verification_pmu_en_ligne": False,
    }


@app.get("/api/courses")
def courses():
    return {
        "statut": "source_non_raccordee",
        "courses": [],
    }


@app.get("/api/selections")
def selections():
    return {
        "statut": "publication_non_raccordee",
        "selections": [],
    }


@app.get("/api/figes")
def figes():
    return {
        "statut": "archives_non_raccordees",
        "figes": [],
    }


@app.get("/api/audits")
def audits():
    return {
        "statut": "archives_non_raccordees",
        "audits": [],
    }
