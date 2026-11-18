#!/usr/bin/env python3
"""X1000 - Raccordement controle M1-M5 et arbitre.

Lecture seule des decisions existantes.
Aucune simulation, aucun pronostic invente.
Aucun FIGE, archive ou historique modifie.
Aucun commit, push ou deploiement.

Entrees :
  sortie/publication_courses.json
  X1000_ENTREES_MOTEURS/M1.json ... M5.json
  X1000_ENTREES_MOTEURS/X1000.json

Sortie :
  sortie/publication_moteurs.json

La sortie n'est creee que si toutes les
verifications sont satisfaites.
"""

import json
import os
import re
import sys
import tempfile

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)
TODAY = NOW.date().isoformat()

COURSES = ROOT / "sortie/publication_courses.json"
ENTREES = Path(
    os.environ.get(
        "X1000_ENTREES_MOTEURS",
        str(ROOT / "entrees_moteurs")
    )
).resolve()

DEST = ROOT / "sortie/publication_moteurs.json"

MOTEURS = ("M1", "M2", "M3", "M4", "M5")

ID_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$"
)


def erreur(message):
    raise RuntimeError(message)


def lire(path):
    if not path.is_file():
        erreur(f"SOURCE_ABSENTE: {path}")

    if not path.stat().st_size:
        erreur(f"SOURCE_VIDE: {path}")

    try:
        data = json.loads(
            path.read_text(encoding="utf-8")
        )
    except (ValueError, UnicodeError) as exc:
        erreur(f"JSON_INVALIDE: {path}: {exc}")

    if not isinstance(data, dict):
        erreur(f"FORMAT_INVALIDE: {path}")

    return data


def date_valide(value):
    if not isinstance(value, str):
        return False

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return False

    return (
        dt.tzinfo is not None
        and dt.astimezone(PARIS).date().isoformat()
        == TODAY
    )


def verifier_classement(decision, actifs, contexte):
    classement = decision.get("classement")

    if not isinstance(classement, list):
        erreur(f"CLASSEMENT_ABSENT: {contexte}")

    if len(classement) != 5:
        erreur(f"CLASSEMENT_NON_CINQ: {contexte}")

    if any(
        type(numero) is not int or numero <= 0
        for numero in classement
    ):
        erreur(f"NUMERO_INVALIDE: {contexte}")

    if len(set(classement)) != 5:
        erreur(f"DOUBLON_CLASSEMENT: {contexte}")

    if not set(classement).issubset(actifs):
        erreur(f"CHEVAL_NON_PARTANT: {contexte}")

    return classement


def verifier_decision(data, cid, actifs, origine):
    if data.get("course_id") != cid:
        erreur(f"COURSE_INCOHERENTE: {origine}")

    classement = verifier_classement(
        data, actifs, origine
    )

    analyse = data.get("analyse")

    if not isinstance(analyse, str):
        erreur(f"ANALYSE_ABSENTE: {origine}")

    if len(analyse.strip()) < 30:
        erreur(f"ANALYSE_INSUFFISANTE: {origine}")

    source = data.get("source")

    if not isinstance(source, str) or not source.strip():
        erreur(f"PROVENANCE_ABSENTE: {origine}")

    date = data.get("date")

    if not date_valide(date):
        erreur(f"HORODATAGE_INVALIDE: {origine}")

    statut = data.get("statut")

    if not isinstance(statut, str) or not statut.strip():
        erreur(f"STATUT_ABSENT: {origine}")

    pari = data.get("pari")

    if pari is not None and not isinstance(pari, str):
        erreur(f"PARI_INVALIDE: {origine}")

    return {
        "classement": classement,
        "analyse": analyse.strip(),
        "justification": analyse.strip(),
        "source": source.strip(),
        "date": date,
        "statut": statut.strip(),
        "pari": pari.strip() if pari else None,
    }


def verifier_courses():
    document = lire(COURSES)

    if document.get("schema") != "x1000-pmu-france-v1":
        erreur("SCHEMA_PMU_INVALIDE")

    if document.get("date_pmu") != TODAY:
        erreur("DATE_PMU_OBSOLETE")

    if document.get("source") != "PMU TurfInfo":
        erreur("SOURCE_PMU_INVALIDE")

    liste = document.get("courses")

    if not isinstance(liste, list) or not liste:
        erreur("AUCUNE_COURSE_PMU")

    resultat = {}

    for course in liste:
        if not isinstance(course, dict):
            erreur("COURSE_MAL_FORMEE")

        cid = course.get("course_id")

        if not isinstance(cid, str):
            erreur("ID_COURSE_ABSENT")

        if not ID_RE.fullmatch(cid):
            erreur(f"ID_COURSE_INVALIDE: {cid}")

        if cid[:10] != TODAY or cid in resultat:
            erreur(f"DATE_OU_DOUBLON: {cid}")

        if course.get("pays_verifie") != "FR":
            erreur(f"HORS_FRANCE: {cid}")

        if not course.get("hippodrome"):
            erreur(f"HIPPODROME_ABSENT: {cid}")

        depart = course.get("depart")

        if not date_valide(depart):
            erreur(f"DEPART_INVALIDE: {cid}")

        partants = course.get("partants")

        if not isinstance(partants, list) or not partants:
            erreur(f"PARTANTS_ABSENTS: {cid}")

        actifs = set()
        numeros = set()

        for cheval in partants:
            if not isinstance(cheval, dict):
                erreur(f"PARTANT_MAL_FORME: {cid}")

            numero = cheval.get("numero")

            if type(numero) is not int or numero <= 0:
                erreur(f"NUMERO_INVALIDE: {cid}")

            if numero in numeros:
                erreur(f"DOUBLON_PARTANT: {cid}")

            numeros.add(numero)

            if not isinstance(cheval.get("nom"), str):
                erreur(f"NOM_ABSENT: {cid}")

            if not cheval["nom"].strip():
                erreur(f"NOM_VIDE: {cid}")

            if type(cheval.get("non_partant")) is not bool:
                erreur(f"STATUT_PARTANT_INVALIDE: {cid}")

            if not cheval["non_partant"]:
                actifs.add(numero)

        if len(actifs) < 5:
            print("COURSE_NON_ELIGIBLE_TOP5:", cid)
            continue

        resultat[cid] = {
            "hippodrome": course["hippodrome"],
            "depart": depart,
            "actifs": actifs,
        }

    if not resultat:
        erreur("AUCUNE_COURSE_ELIGIBLE")

    return resultat


def lire_sorties(nom):
    path = ENTREES / f"{nom}.json"
    data = lire(path)

    if data.get("moteur") != nom:
        erreur(f"IDENTITE_SOURCE_INVALIDE: {nom}")

    decisions = data.get("decisions")

    if not isinstance(decisions, list):
        erreur(f"DECISIONS_ABSENTES: {nom}")

    resultat = {}

    for decision in decisions:
        if not isinstance(decision, dict):
            erreur(f"DECISION_MAL_FORMEE: {nom}")

        cid = decision.get("course_id")

        if not isinstance(cid, str):
            erreur(f"COURSE_ID_ABSENT: {nom}")

        if cid in resultat:
            erreur(f"DECISION_DUPLIQUEE: {nom}: {cid}")

        resultat[cid] = decision

    return resultat


def construire():
    courses = verifier_courses()

    sources = {
        nom: lire_sorties(nom)
        for nom in MOTEURS
    }

    arbitrages = lire_sorties("X1000")

    # Les six sources doivent correspondre
    # exactement aux memes courses.
    ensembles = [
        set(decisions)
        for decisions in (
            *sources.values(),
            arbitrages,
        )
    ]

    reference = ensembles[0]

    if not reference:
        erreur("AUCUNE_DECISION_REELLE")

    if any(ids != reference for ids in ensembles):
        erreur("SOURCES_MOTEURS_NON_ALIGNEES")

    if not reference.issubset(courses):
        erreur("COURSE_NON_VERIFIEE")

    publications = []

    for cid in sorted(reference):
        course = courses[cid]
        actifs = course["actifs"]

        moteurs = {}

        for nom in MOTEURS:
            moteurs[nom] = verifier_decision(
                sources[nom][cid],
                cid,
                actifs,
                nom
            )

        arbitre = verifier_decision(
            arbitrages[cid],
            cid,
            actifs,
            "X1000"
        )

        publications.append({
            "course_id": cid,
            "hippodrome": course["hippodrome"],
            "depart": course["depart"],
            "moteurs": moteurs,
            "arbitre_x1000": arbitre,
        })

    # Le tableau officiel attend une liste
    # courses et un arbitre central racine.
    # Aucun arbitrage global n'est deduit
    # automatiquement des decisions par course.
    document = {
        "schema": "x1000-moteurs-v1",
        "date_pmu": TODAY,
        "publie_a": NOW.isoformat(),
        "source": "Sorties M1-M5 et X1000 fournies",
        "courses": publications,
        "arbitre_x1000": None,
    }

    return document


def enregistrer(document):
    DEST.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # Protection : aucune publication existante
    # n'est remplacee automatiquement.
    if DEST.exists():
        erreur(
            "DESTINATION_EXISTANTE: "
            "remplacement automatique interdit"
        )

    contenu = json.dumps(
        document,
        ensure_ascii=False,
        indent=2
    ) + "\n"

    # Fichier temporaire dans le meme dossier.
    fd, nom = tempfile.mkstemp(
        prefix=".x1000-moteurs-",
        suffix=".tmp",
        dir=str(DEST.parent)
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(contenu)

        # Creation exclusive : ne remplace jamais
        # un fichier deja present.
        os.link(nom, DEST)
    finally:
        Path(nom).unlink(missing_ok=True)


def main():
    print("=== X1000 RACCORDEMENT M1-M5 ===")
    print("DATE:", TODAY)
    print("ENTREES:", ENTREES)
    print("DESTINATION:", DEST)

    document = construire()
    enregistrer(document)

    print("COURSES:", len(document["courses"]))
    print("M1_M5_ET_X1000_STRUCTURE_VALIDEE")
    print("AUCUNE_DECISION_INVENTEE")
    print("FIGES_NON_AUTHENTIFIES")
    print("AUCUN_DEPLOIEMENT")
    print("AUCUN_COMMIT_OU_PUSH")
    print("RACCORDEMENT_JSON_PREPARE")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ECHEC_RACCORDEMENT:", exc, file=sys.stderr)
        sys.exit(1)
