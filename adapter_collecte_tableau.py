#!/usr/bin/env python3
"""X1000 - Adaptateur PMU vers tableau GitHub Pages.

Source : sortie/publication_courses.json
Sortie : sortie/recherche_courses_du_jour.json

Aucun pronostic, FIGE ou audit historique modifie.
Aucun commit, push ou deploiement.
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "sortie/publication_courses.json"
DEST = ROOT / "sortie/recherche_courses_du_jour.json"

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)
TODAY = NOW.date().isoformat()

ID_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$"
)


def erreur(message):
    raise ValueError(message)


def lire_source():
    if not SRC.is_file():
        erreur(f"SOURCE_ABSENTE: {SRC}")

    with SRC.open(encoding="utf-8") as fichier:
        data = json.load(fichier)

    if not isinstance(data, dict):
        erreur("SOURCE_NON_OBJET")

    if data.get("schema") != "x1000-pmu-france-v1":
        erreur("SCHEMA_INVALIDE")

    if data.get("date_pmu") != TODAY:
        erreur("DATE_PMU_INVALIDE")

    if data.get("source") != "PMU TurfInfo":
        erreur("SOURCE_NON_VERIFIEE")

    courses = data.get("courses")
    if not isinstance(courses, list) or not courses:
        erreur("AUCUNE_COURSE_COLLECTEE")

    return data, courses


def date_depart(value):
    if not isinstance(value, str):
        erreur("DEPART_ABSENT")

    try:
        depart = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        erreur("DEPART_MAL_FORME")

    if depart.tzinfo is None:
        erreur("DEPART_SANS_FUSEAU")

    if depart.astimezone(PARIS).date().isoformat() != TODAY:
        erreur("DEPART_HORS_DATE")

    return depart.isoformat()


def adapter_course(course):
    if not isinstance(course, dict):
        erreur("COURSE_MAL_FORMEE")

    cid = course.get("course_id")
    if not isinstance(cid, str) or not ID_PATTERN.fullmatch(cid):
        erreur("IDENTIFIANT_INVALIDE")

    if not cid.startswith(TODAY + "-"):
        erreur("COURSE_HORS_DATE")

    if course.get("pays_verifie") != "FR":
        erreur("COURSE_HORS_FRANCE")

    hippodrome = course.get("hippodrome")
    if not isinstance(hippodrome, str) or not hippodrome.strip():
        erreur("HIPPODROME_ABSENT")

    depart = date_depart(course.get("depart"))
    partants = course.get("partants")

    if not isinstance(partants, list) or not partants:
        erreur("PARTANTS_ABSENTS")

    numeros = set()
    chevaux = []

    for cheval in partants:
        if not isinstance(cheval, dict):
            erreur("PARTANT_MAL_FORME")

        numero = cheval.get("numero")
        nom = cheval.get("nom")
        np = cheval.get("non_partant")

        if (
            type(numero) is not int
            or numero <= 0
            or numero in numeros
            or not isinstance(nom, str)
            or not nom.strip()
            or type(np) is not bool
        ):
            erreur("PARTANT_INVALIDE")

        numeros.add(numero)

        chevaux.append({
            "numero": numero,
            "nom": nom.strip(),
            "non_partant": np
        })

    actifs = sum(not c["non_partant"] for c in chevaux)

    if actifs == 0:
        erreur("AUCUN_PARTANT_ACTIF")

    nom_course = course.get("nom_course")

    return {
        "id": cid,
        "course_id": cid,
        "date": TODAY,
        "hippodrome": hippodrome.strip(),
        "nom": nom_course or "Course PMU",
        "depart": depart,
        "depart_source": depart,
        "depart_verifie": True,
        "discipline": course.get("discipline"),
        "distance": course.get("distance"),
        "pays_verifie": "FR",
        "localisation_france_verifiee": True,
        "partants": chevaux,
        "nombre_partants": actifs,
        "non_partants": [
            c["numero"] for c in chevaux
            if c["non_partant"]
        ],
        "source": "PMU TurfInfo",
        "statut": "CANDIDATE_A_ANALYSER"
    }


def main():
    source, courses = lire_source()

    resultat = []
    identifiants = set()

    for course in courses:
        item = adapter_course(course)
        cid = item["course_id"]

        if cid in identifiants:
            erreur(f"DOUBLON_COURSE: {cid}")

        identifiants.add(cid)
        resultat.append(item)

    document = {
        "schema": "x1000-candidates-v1",
        "date": TODAY,
        "recherche_a": NOW.isoformat(),
        "collecte_a": NOW.isoformat(),
        "source": "PMU TurfInfo",
        "statut": "OK",
        "courses_candidates": resultat,
        "diagnostics": source.get("diagnostics", [])[:100]
    }

    DEST.parent.mkdir(parents=True, exist_ok=True)

    temporaire = DEST.with_suffix(".json.tmp")
    temporaire.write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )
    temporaire.replace(DEST)

    print("X1000_ADAPTATION_OK")
    print("DATE:", TODAY)
    print("COURSES:", len(resultat))
    print("DESTINATION:", DEST)
    print("AUCUN_PRONOSTIC_CREE")
    print("AUCUN_FIGE_MODIFIE")
    print("AUCUNE_ARCHIVE_MODIFIEE")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ECHEC_ADAPTATION:", exc, file=sys.stderr)
        sys.exit(1)
