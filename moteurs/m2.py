#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 PHOENIX - M2 V1.1 : lecture tactique independante.

Les notes tactiques 0-100 doivent etre calculees et justifiees en amont.
Aucune verification PMU en ligne, ecriture, publication ou FIGE.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

MOTEUR = "M2"
VERSION = "1.1"
COURSE_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})-R([1-9]\d*)-C([1-9]\d*)$")
CRITERES = {
    "aptitude_rythme": 0.25,
    "positionnement": 0.20,
    "adaptation_scenario": 0.20,
    "acceleration_finale": 0.20,
    "souplesse_tactique": 0.15,
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
    except (OverflowError, ValueError) as exc:
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
            raise ValueError(f"Critere tactique manquant: {critere}")
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
        "statut": "ANALYSE_TACTIQUE_INDICATIVE_NON_PUBLIEE",
        "verification_externe_effectuee": False,
        "pari": None,
        "fige": False,
        "publication_officielle": False,
    }


def self_test():
    chevaux = [
        {"numero": i, "nom": f"TEST_M2_{i}",
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
    assert resultat["moteur"] == "M2"
    assert resultat["fige"] is False
    assert resultat["publication_officielle"] is False
    assert resultat["verification_externe_effectuee"] is False

    def doit_echouer(invalide):
        try:
            analyser_course(invalide)
        except ValueError:
            return
        raise AssertionError("Donnees invalides acceptees")

    for champ, valeur in (
        ("source_verifiee", False),
        ("pays_course", "GB"),
        ("course_id", "INVALIDE"),
        ("course_id", "2026-02-30-R1-C1"),
        ("hippodrome", ""),
    ):
        doit_echouer({**course, champ: valeur})
    doublons = [dict(c) for c in chevaux]
    doublons[1]["numero"] = 1
    doit_echouer({**course, "partants": doublons})
    note_invalide = [dict(c) for c in chevaux]
    note_invalide[0]["aptitude_rythme"] = 101
    doit_echouer({**course, "partants": note_invalide})
    non_partants = [dict(c) for c in chevaux]
    non_partants[0]["non_partant"] = True
    non_partants[1]["non_partant"] = True
    doit_echouer({**course, "partants": non_partants})
    print("M2 SELF-TEST OK")
    print("M2 CLASSEMENT ET VALIDATIONS OK")
    print("M2 LECTURE SEULE OK")


def main():
    parser = argparse.ArgumentParser(description="X1000 - M2 tactique independant")
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
        print(f"ERREUR M2: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
