#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 PHOENIX - M5 V1.1 : analyse critique independante.

Les notes 0-100 sont fournies par une analyse amont documentee.
Aucune cote ou donnee PMU n'est recuperee ou inventee ici.
Lecture seule : aucun FIGE, archive, pronostic ou fichier modifie.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

MOTEUR = "M5"
VERSION = "1.1"
COURSE_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})-R([1-9]\d*)-C([1-9]\d*)$")
CRITERES = {
    "potentiel_cache": 0.30,
    "valeur_sportive": 0.25,
    "fragilite_favoris": 0.15,
    "aptitude_sous_estimee": 0.20,
    "potentiel_surprise": 0.10,
}


def texte(valeur, nom):
    if not isinstance(valeur, str) or not valeur.strip():
        raise ValueError(f"{nom}: texte non vide requis")
    return valeur.strip()


def nombre(valeur, nom):
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise ValueError(f"{nom}: nombre JSON requis")
    try:
        resultat = float(valeur)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{nom}: nombre invalide") from exc
    if not math.isfinite(resultat) or not 0 <= resultat <= 100:
        raise ValueError(f"{nom}: valeur finie entre 0 et 100 requise")
    return resultat


def verifier_course(course):
    if not isinstance(course, dict):
        raise ValueError("Structure course invalide")
    identifiant = texte(course.get("course_id"), "course_id")
    match = COURSE_ID.fullmatch(identifiant)
    if not match:
        raise ValueError("Format course_id attendu: YYYY-MM-DD-Rx-Cy")
    try:
        date.fromisoformat(match.group(1))
    except ValueError as exc:
        raise ValueError("Date de course invalide") from exc
    if course.get("source_verifiee") is not True:
        raise ValueError("Attestation de verification de source absente")
    source = texte(course.get("source"), "source")
    if course.get("pays_course") != "FR":
        raise ValueError("Course hors France ou pays non confirme")
    hippodrome = texte(course.get("hippodrome"), "hippodrome")
    partants = course.get("partants")
    if not isinstance(partants, list) or len(partants) < 5:
        raise ValueError("Au moins cinq partants requis")
    return identifiant, source, hippodrome, partants


def calculer_score(cheval):
    if not isinstance(cheval, dict):
        raise ValueError("Cheval invalide")
    score = 0.0
    for critere, poids in CRITERES.items():
        if critere not in cheval:
            raise ValueError(f"Critere M5 manquant: {critere}")
        score += nombre(cheval[critere], critere) * poids
    return round(score, 3)


def analyser_course(course):
    identifiant, source, hippodrome, partants = verifier_course(course)
    classement = []
    numeros = set()
    for cheval in partants:
        if not isinstance(cheval, dict):
            raise ValueError("Partant invalide")
        numero = cheval.get("numero")
        if type(numero) is not int or numero <= 0:
            raise ValueError("Numero de cheval invalide")
        if numero in numeros:
            raise ValueError(f"Numero duplique: {numero}")
        numeros.add(numero)
        nom = texte(cheval.get("nom"), f"nom cheval {numero}")
        non_partant = cheval.get("non_partant", False)
        if type(non_partant) is not bool:
            raise ValueError(f"Statut non_partant invalide: {numero}")
        if non_partant:
            continue
        classement.append({
            "numero": numero,
            "nom": nom,
            "score": calculer_score(cheval),
        })
    if len(classement) < 5:
        raise ValueError("Moins de cinq partants actifs")
    classement.sort(key=lambda c: (-c["score"], c["numero"]))
    return {
        "moteur": MOTEUR,
        "version": VERSION,
        "course_id": identifiant,
        "hippodrome": hippodrome,
        "source": source,
        "classement": classement[:5],
        "nombre_partants_actifs": len(classement),
        "statut": "ANALYSE_CRITIQUE_INDICATIVE_NON_PUBLIEE",
        "verification_externe_effectuee": False,
        "pari": None,
        "fige": False,
        "publication_officielle": False,
    }


def self_test():
    chevaux = [
        {"numero": i, "nom": f"TEST_M5_{i}",
         **{critere: 50 + i for critere in CRITERES}}
        for i in range(1, 7)
    ]
    course = {
        "course_id": "2026-11-18-R1-C1",
        "source_verifiee": True,
        "source": "DONNEES_SYNTHETIQUES_TEST",
        "pays_course": "FR",
        "hippodrome": "HIPPODROME_TEST",
        "partants": chevaux,
    }
    resultat = analyser_course(course)
    assert [c["numero"] for c in resultat["classement"]] == [6, 5, 4, 3, 2]
    assert resultat["moteur"] == "M5"
    assert resultat["fige"] is False
    assert resultat["publication_officielle"] is False
    assert resultat["verification_externe_effectuee"] is False
    assert abs(sum(CRITERES.values()) - 1.0) < 1e-12

    def doit_echouer(invalide):
        try:
            analyser_course(invalide)
        except ValueError:
            return
        raise AssertionError("Donnees invalides acceptees")

    for champ, valeur in (
        ("source_verifiee", False),
        ("source_verifiee", 1),
        ("source", ""),
        ("pays_course", "GB"),
        ("course_id", "INVALIDE"),
        ("course_id", "2026-02-30-R1-C1"),
        ("hippodrome", ""),
    ):
        doit_echouer({**course, champ: valeur})
    doublons = [dict(c) for c in chevaux]
    doublons[1]["numero"] = 1
    doit_echouer({**course, "partants": doublons})
    for valeur in (101, -1, True, "80", float("nan"), float("inf"), None):
        invalides = [dict(c) for c in chevaux]
        invalides[0]["potentiel_cache"] = valeur
        doit_echouer({**course, "partants": invalides})
    manquants = [dict(c) for c in chevaux]
    del manquants[0]["valeur_sportive"]
    doit_echouer({**course, "partants": manquants})
    non_partants = [dict(c) for c in chevaux]
    non_partants[0]["non_partant"] = True
    non_partants[1]["non_partant"] = True
    doit_echouer({**course, "partants": non_partants})
    un_non_partant = [dict(c) for c in chevaux]
    un_non_partant[0]["non_partant"] = True
    assert [c["numero"] for c in analyser_course({**course, "partants": un_non_partant})["classement"]] == [6, 5, 4, 3, 2]
    print("M5 SELF-TEST OK")
    print("M5 CLASSEMENT ET VALIDATIONS OK")
    print("M5 LECTURE SEULE OK")


def main():
    parser = argparse.ArgumentParser(description="X1000 - M5 critique independant")
    groupe = parser.add_mutually_exclusive_group(required=True)
    groupe.add_argument("--self-test", action="store_true")
    groupe.add_argument("--input", type=Path)
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            with args.input.open(encoding="utf-8") as fichier:
                course = json.load(fichier)
            print(json.dumps(analyser_course(course), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, AssertionError) as exc:
        print(f"ERREUR M5: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
