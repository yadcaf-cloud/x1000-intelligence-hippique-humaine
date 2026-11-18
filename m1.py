#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 M1 - Analyse statistique independante, lecture seule.

Les cinq notes sont des indicateurs normalises fournis par une source amont.
La presence d'une attestation de verification n'est pas une preuve externe.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import date
from pathlib import Path

MOTEUR = "M1"
VERSION = "1.1"
COURSE_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})-R([1-9]\d*)-C([1-9]\d*)$")
CRITERES = {
    "regularite": 0.30,
    "performances": 0.25,
    "distance": 0.20,
    "terrain": 0.15,
    "forme": 0.10,
}


def nombre(valeur, nom):
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        raise ValueError(f"{nom}: nombre JSON requis")
    resultat = float(valeur)
    if not math.isfinite(resultat) or not 0 <= resultat <= 100:
        raise ValueError(f"{nom}: valeur finie entre 0 et 100 requise")
    return resultat


def texte(valeur, nom):
    if not isinstance(valeur, str) or not valeur.strip():
        raise ValueError(f"{nom}: texte non vide requis")
    return valeur.strip()


def calculer_score(cheval):
    if not isinstance(cheval, dict):
        raise ValueError("Cheval invalide")
    return round(sum(nombre(cheval.get(c), c) * poids for c, poids in CRITERES.items()), 3)


def analyser_course(course):
    if not isinstance(course, dict):
        raise ValueError("Course invalide")
    identifiant = texte(course.get("course_id"), "course_id")
    match = COURSE_ID.fullmatch(identifiant)
    if not match:
        raise ValueError("course_id: format YYYY-MM-DD-Rx-Cy requis")
    try:
        date.fromisoformat(match.group(1))
    except ValueError as exc:
        raise ValueError("course_id: date invalide") from exc

    if course.get("source_verifiee") is not True:
        raise ValueError("Attestation source_verifiee absente")
    source = texte(course.get("source"), "source")
    # La verification effective de la source et du lieu revient au collecteur.
    if course.get("pays_course") != "FR":
        raise ValueError("pays_course doit etre FR, verifie en amont")
    hippodrome = texte(course.get("hippodrome"), "hippodrome")

    partants = course.get("partants")
    if not isinstance(partants, list) or len(partants) < 5:
        raise ValueError("Au moins cinq partants requis")
    numeros = set()
    classement = []
    for cheval in partants:
        if not isinstance(cheval, dict):
            raise ValueError("Partant invalide")
        numero = cheval.get("numero")
        if type(numero) is not int or numero <= 0 or numero in numeros:
            raise ValueError("Numero absent, invalide ou duplique")
        numeros.add(numero)
        nom = texte(cheval.get("nom"), f"nom du cheval {numero}")
        non_partant = cheval.get("non_partant", False)
        if type(non_partant) is not bool:
            raise ValueError(f"non_partant invalide pour {numero}")
        if non_partant:
            continue
        classement.append({"numero": numero, "nom": nom, "score": calculer_score(cheval)})
    if len(classement) < 5:
        raise ValueError("Moins de cinq partants actifs")
    classement.sort(key=lambda x: (-x["score"], x["numero"]))
    return {
        "moteur": MOTEUR,
        "version": VERSION,
        "course_id": identifiant,
        "hippodrome": hippodrome,
        "source": source,
        "verification_externe_effectuee": False,
        "classement": classement[:5],
        "statut": "ANALYSE_INDICATIVE_NON_PUBLIEE",
        "pari": None,
        "fige": False,
        "publication_officielle": False,
    }


def self_test():
    base = {
        "course_id": "2026-11-18-R1-C1",
        "source_verifiee": True,
        "source": "DONNEES_SYNTHETIQUES_TEST",
        "pays_course": "FR",
        "hippodrome": "HIPPODROME_TEST",
        "partants": [
            {"numero": i, "nom": f"TEST_{i}", **{c: 50 + i for c in CRITERES}}
            for i in range(1, 7)
        ],
    }
    resultat = analyser_course(base)
    assert [x["numero"] for x in resultat["classement"]] == [6, 5, 4, 3, 2]
    assert resultat["fige"] is False
    assert resultat["publication_officielle"] is False
    assert resultat["verification_externe_effectuee"] is False
    for champ, valeur in [("source_verifiee", False), ("pays_course", "GB")]:
        invalide = {**base, champ: valeur}
        try:
            analyser_course(invalide)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Validation absente: {champ}")
    print("M1 SELF-TEST OK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
    except (OSError, ValueError, TypeError, AssertionError) as exc:
        parser.exit(1, f"ERREUR M1: {exc}\n")


if __name__ == "__main__":
    main()
