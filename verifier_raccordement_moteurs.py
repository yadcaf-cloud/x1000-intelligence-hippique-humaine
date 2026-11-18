#!/usr/bin/env python3
"""X1000 - Validation bloquante du raccordement M1-M5.

Lecture seule des sources PMU et moteurs.
Aucun pronostic cree, aucun FIGE modifie.
Aucun commit, push ou deploiement.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
TODAY = datetime.now(
    ZoneInfo("Europe/Paris")
).date().isoformat()

COURSES = ROOT / "sortie/publication_courses.json"
SOURCES = [
    ROOT / "data/publication_moteurs.json",
    ROOT / "sortie/publication_moteurs.json",
]

MOTEURS = ("M1", "M2", "M3", "M4", "M5")
ID_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$"
)


def erreur(message):
    print("::error::" + message, file=sys.stderr)
    raise SystemExit(1)


def lire(path):
    if not path.is_file():
        erreur(f"FICHIER_ABSENT: {path}")
    if not path.stat().st_size:
        erreur(f"FICHIER_VIDE: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        erreur(f"JSON_INVALIDE: {path}: {exc}")


def verifier_courses():
    data = lire(COURSES)

    if not isinstance(data, dict):
        erreur("FORMAT_COURSES_INVALIDE")

    if data.get("schema") != "x1000-pmu-france-v1":
        erreur("SCHEMA_COURSES_INVALIDE")

    if data.get("date_pmu") != TODAY:
        erreur("DATE_PMU_OBSOLETE")

    if data.get("source") != "PMU TurfInfo":
        erreur("SOURCE_PMU_NON_CONFORME")

    courses = data.get("courses")

    if not isinstance(courses, list) or not courses:
        erreur("AUCUNE_COURSE_REELLE")

    resultat = {}

    for course in courses:
        if not isinstance(course, dict):
            erreur("COURSE_MAL_FORMEE")

        cid = course.get("course_id")

        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            erreur("IDENTIFIANT_COURSE_INVALIDE")

        if cid[:10] != TODAY or cid in resultat:
            erreur("DATE_OU_DOUBLON_COURSE")

        if course.get("pays_verifie") != "FR":
            erreur(f"COURSE_HORS_FRANCE: {cid}")

        partants = course.get("partants")

        if not isinstance(partants, list) or not partants:
            erreur(f"PARTANTS_ABSENTS: {cid}")

        actifs = set()
        numeros = set()

        for cheval in partants:
            if not isinstance(cheval, dict):
                erreur(f"PARTANT_MAL_FORME: {cid}")

            numero = cheval.get("numero")

            if (
                type(numero) is not int
                or numero <= 0
                or numero in numeros
            ):
                erreur(f"NUMERO_PARTANT_INVALIDE: {cid}")

            if not isinstance(cheval.get("nom"), str):
                erreur(f"NOM_PARTANT_ABSENT: {cid}")

            if not cheval["nom"].strip():
                erreur(f"NOM_PARTANT_VIDE: {cid}")

            if type(cheval.get("non_partant")) is not bool:
                erreur(f"STATUT_PARTANT_INVALIDE: {cid}")

            numeros.add(numero)

            if not cheval["non_partant"]:
                actifs.add(numero)

        if not actifs:
            erreur(f"AUCUN_PARTANT_ACTIF: {cid}")

        resultat[cid] = actifs

    print(f"COURSES_PMU_OK: {len(resultat)}")
    return resultat


def verifier_moteurs(courses):
    presentes = [p for p in SOURCES if p.is_file()]

    if not presentes:
        erreur(
            "SOURCE_M1_M5_ABSENTE: "
            "publication_moteurs.json introuvable. "
            "Publication interdite."
        )

    if len(presentes) == 2:
        if presentes[0].read_bytes() != presentes[1].read_bytes():
            erreur("PUBLICATIONS_MOTEURS_CONTRADICTOIRES")

    data = lire(presentes[0])

    if not isinstance(data, dict):
        erreur("FORMAT_PUBLICATION_MOTEURS_INVALIDE")

    if data.get("schema") != "x1000-moteurs-v1":
        erreur("SCHEMA_MOTEURS_INVALIDE")

    if data.get("date_pmu") != TODAY:
        erreur("DATE_MOTEURS_OBSOLETE")

    moteurs = data.get("moteurs")

    if not isinstance(moteurs, dict):
        erreur("MOTEURS_ABSENTS")

    if set(moteurs) != set(MOTEURS):
        erreur("CINQ_MOTEURS_NON_IDENTIFIES")

    for nom in MOTEURS:
        moteur = moteurs[nom]

        if not isinstance(moteur, dict):
            erreur(f"{nom}_FORMAT_INVALIDE")

        classement = moteur.get("classement")

        if classement is None:
            print(f"{nom}: AUCUN_CLASSEMENT_PUBLIE")
            continue

        if not isinstance(classement, list):
            erreur(f"{nom}_CLASSEMENT_INVALIDE")

        if len(classement) != 5:
            erreur(f"{nom}_CLASSEMENT_NON_CINQ")

        if any(
            type(n) is not int or n <= 0
            for n in classement
        ):
            erreur(f"{nom}_NUMEROS_INVALIDES")

        if len(set(classement)) != 5:
            erreur(f"{nom}_DOUBLON_CLASSEMENT")

        cid = moteur.get("course_id")

        if cid not in courses:
            erreur(f"{nom}_COURSE_NON_VERIFIEE")

        if not set(classement).issubset(courses[cid]):
            erreur(f"{nom}_CHEVAL_NON_PARTANT")

        analyse = moteur.get("analyse")

        if not isinstance(analyse, str):
            erreur(f"{nom}_ANALYSE_ABSENTE")

        if len(analyse.strip()) < 30:
            erreur(f"{nom}_ANALYSE_INSUFFISANTE")

        source = moteur.get("source")

        if not isinstance(source, str) or not source.strip():
            erreur(f"{nom}_PROVENANCE_ABSENTE")

        print(f"{nom}: CLASSEMENT_STRUCTURELLEMENT_VALIDE")

    if not isinstance(data.get("coordination"), dict):
        erreur("COORDINATION_X1000_ABSENTE")

    print("M1_M5_STRUCTURE_OK")
    print("FIGES_NON_AUTHENTIFIES")
    print("AUCUN_DEPLOIEMENT_AUTORISE")


def main():
    print("=== X1000 CONTROLE RACCORDEMENT M1-M5 ===")
    print("DATE:", TODAY)

    courses = verifier_courses()
    verifier_moteurs(courses)

    print("CONTROLE_RACCORDEMENT_TERMINE")


if __name__ == "__main__":
    main()
