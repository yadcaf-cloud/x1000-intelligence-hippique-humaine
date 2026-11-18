#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 - M1 : moteur statistique independant.

Analyse des donnees hippiques verifiees.
Aucun acces aux FIGEs, archives ou pronostics officiels.
Aucune publication ni modification de fichier.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

MOTEUR = "M1"
VERSION = "1.0"


def nombre(valeur, nom):
    if isinstance(valeur, bool):
        raise ValueError(f"{nom}: booleen interdit")
    try:
        resultat = float(valeur)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{nom}: nombre invalide") from exc
    if not math.isfinite(resultat):
        raise ValueError(f"{nom}: valeur non finie")
    return resultat


def calculer_score(cheval):
    """Score indicatif sur 100, selon les donnees fournies."""
    criteres = {
        "regularite": 0.30,
        "performances": 0.25,
        "distance": 0.20,
        "terrain": 0.15,
        "forme": 0.10,
    }

    score = 0.0
    for critere, poids in criteres.items():
        if critere not in cheval:
            raise ValueError(
                f"Cheval {cheval.get('numero')}: "
                f"critere absent: {critere}"
            )
        valeur = nombre(cheval[critere], critere)
        if not 0 <= valeur <= 100:
            raise ValueError(
                f"{critere}: valeur attendue entre 0 et 100"
            )
        score += valeur * poids

    return round(score, 3)


def analyser_course(course):
    if not isinstance(course, dict):
        raise ValueError("Course invalide")

    identifiant = course.get("course_id")
    if not isinstance(identifiant, str) or not identifiant:
        raise ValueError("course_id obligatoire")

    if course.get("source_verifiee") is not True:
        raise ValueError("Source non verifiee")

    if not course.get("source"):
        raise ValueError("Reference source obligatoire")

    partants = course.get("partants")
    if not isinstance(partants, list) or len(partants) < 5:
        raise ValueError("Au moins cinq partants requis")

    classement = []
    numeros = set()

    for cheval in partants:
        if not isinstance(cheval, dict):
            raise ValueError("Partant invalide")

        numero = cheval.get("numero")
        if isinstance(numero, bool) or not isinstance(numero, int):
            raise ValueError("Numero de cheval invalide")
        if numero <= 0 or numero in numeros:
            raise ValueError("Numero absent, invalide ou duplique")

        numeros.add(numero)

        if cheval.get("non_partant") is True:
            continue

        classement.append({
            "numero": numero,
            "nom": str(cheval.get("nom", "")),
            "score": calculer_score(cheval),
        })

    if len(classement) < 5:
        raise ValueError("Moins de cinq partants actifs")

    classement.sort(
        key=lambda x: (-x["score"], x["numero"])
    )

    return {
        "moteur": MOTEUR,
        "version": VERSION,
        "course_id": identifiant,
        "source": course["source"],
        "classement": classement[:5],
        "statut": "ANALYSE_INDICATIVE",
        "pari": None,
        "fige": False,
        "publication_officielle": False,
    }


def self_test():
    chevaux = []
    for numero in range(1, 7):
        chevaux.append({
            "numero": numero,
            "nom": f"TEST_{numero}",
            "regularite": 50 + numero,
            "performances": 50 + numero,
            "distance": 50 + numero,
            "terrain": 50 + numero,
            "forme": 50 + numero,
        })

    resultat = analyser_course({
        "course_id": "TEST-M1",
        "source_verifiee": True,
        "source": "DONNEES_SYNTHETIQUES_TEST",
        "partants": chevaux,
    })

    assert len(resultat["classement"]) == 5
    assert resultat["classement"][0]["numero"] == 6
    assert resultat["fige"] is False
    print("M1 SELF-TEST OK")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    if args.input is None:
        parser.error("--input requis ou --self-test")

    try:
        with args.input.open(encoding="utf-8") as fichier:
            course = json.load(fichier)
        resultat = analyser_course(course)
        print(json.dumps(
            resultat,
            ensure_ascii=False,
            indent=2,
        ))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"ERREUR M1: {exc}\n")


if __name__ == "__main__":
    main()
