#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 - Controle de coherence PMU et M1-M5.

Compare une course enrichie avec la collecte PMU locale.
Lecture seule. Aucun pronostic, FIGE, archive ou
fichier officiel modifie.

Ce controle ne certifie pas l'authenticite externe PMU.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PARIS = ZoneInfo("Europe/Paris")
ID_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})-R[1-9]\d*-C[1-9]\d*$"
)


def exiger(condition, message):
    if not condition:
        raise ValueError(message)


def charger(chemin):
    exiger(chemin.is_file(), f"FICHIER_ABSENT: {chemin}")
    exiger(chemin.stat().st_size > 0, "FICHIER_VIDE")
    exiger(
        chemin.stat().st_size <= 5 * 1024 * 1024,
        "FICHIER_TROP_VOLUMINEUX",
    )

    def sans_doublons(paires):
        objet = {}
        for cle, valeur in paires:
            exiger(cle not in objet, f"CLE_JSON_DUPLIQUEE: {cle}")
            objet[cle] = valeur
        return objet

    def constante_interdite(valeur):
        raise ValueError(f"CONSTANTE_JSON_INVALIDE: {valeur}")

    document = json.loads(
        chemin.read_text(encoding="utf-8"),
        object_pairs_hook=sans_doublons,
        parse_constant=constante_interdite,
    )
    exiger(isinstance(document, dict), "OBJET_JSON_ATTENDU")
    return document


def identifiant(cid):
    exiger(isinstance(cid, str), "COURSE_ID_ABSENT")
    match = ID_RE.fullmatch(cid)
    exiger(match is not None, "COURSE_ID_INVALIDE")
    date.fromisoformat(match.group(1))
    return match.group(1)


def indexer_partants(partants, contexte):
    exiger(isinstance(partants, list), f"{contexte}: PARTANTS_ABSENTS")
    resultat = {}

    for cheval in partants:
        exiger(isinstance(cheval, dict), f"{contexte}: PARTANT_INVALIDE")

        numero = cheval.get("numero")
        exiger(
            type(numero) is int and numero > 0,
            f"{contexte}: NUMERO_INVALIDE",
        )
        exiger(numero not in resultat, f"{contexte}: DOUBLON_{numero}")

        nom = cheval.get("nom")
        exiger(
            isinstance(nom, str) and nom.strip(),
            f"{contexte}: NOM_ABSENT_{numero}",
        )

        np = cheval.get("non_partant")
        exiger(
            type(np) is bool,
            f"{contexte}: STATUT_NON_PARTANT_INVALIDE_{numero}",
        )

        resultat[numero] = {
            "nom": nom.strip().casefold(),
            "non_partant": np,
        }

    return resultat


def normaliser_depart(valeur):
    exiger(isinstance(valeur, str), "DEPART_ABSENT")
    try:
        instant = datetime.fromisoformat(
            valeur.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("DEPART_INVALIDE") from exc

    exiger(
        instant.tzinfo is not None
        and instant.utcoffset() is not None,
        "FUSEAU_DEPART_ABSENT",
    )
    return instant.astimezone(PARIS)


def verifier(source, enrichie):
    exiger(
        source.get("schema") == "x1000-pmu-france-v1",
        "SCHEMA_SOURCE_INVALIDE",
    )
    exiger(
        source.get("source") == "PMU TurfInfo",
        "PROVENANCE_COLLECTE_INVALIDE",
    )

    cid = enrichie.get("course_id")
    jour = identifiant(cid)

    exiger(
        source.get("date_pmu") == jour,
        "DATE_SOURCE_DIFFERENTE",
    )
    exiger(
        enrichie.get("pays_course") == "FR",
        "PAYS_ENRICHI_NON_FR",
    )
    exiger(
        enrichie.get("source_verifiee") is True,
        "ATTESTATION_SOURCE_ABSENTE",
    )

    courses = source.get("courses")
    exiger(isinstance(courses, list), "COURSES_SOURCE_ABSENTES")

    correspondances = [
        course for course in courses
        if isinstance(course, dict)
        and course.get("course_id") == cid
    ]

    exiger(
        len(correspondances) == 1,
        "COURSE_ABSENTE_OU_DUPLIQUEE",
    )

    originale = correspondances[0]

    exiger(
        originale.get("pays_verifie") == "FR",
        "PAYS_SOURCE_NON_FR",
    )

    hippodrome = originale.get("hippodrome")
    hippodrome_enrichi = enrichie.get("hippodrome")

    exiger(
        isinstance(hippodrome, str)
        and isinstance(hippodrome_enrichi, str)
        and hippodrome.strip().casefold()
        == hippodrome_enrichi.strip().casefold(),
        "HIPPODROME_DIFFERENT",
    )

    partants_source = indexer_partants(
        originale.get("partants"), "SOURCE"
    )
    partants_enrichis = indexer_partants(
        enrichie.get("partants"), "ENRICHI"
    )

    exiger(
        partants_source == partants_enrichis,
        "PARTANTS_NOMS_OU_STATUTS_DIFFERENTS",
    )

    actifs = sum(
        not cheval["non_partant"]
        for cheval in partants_source.values()
    )
    exiger(actifs >= 5, "MOINS_DE_CINQ_PARTANTS_ACTIFS")

    depart_source = normaliser_depart(
        originale.get("depart")
    )

    if "depart" in enrichie:
        depart_enrichi = normaliser_depart(
            enrichie["depart"]
        )
        exiger(
            depart_source == depart_enrichi,
            "HORAIRE_DEPART_DIFFERENT",
        )

    return {
        "systeme": "X1000",
        "course_id": cid,
        "hippodrome": hippodrome,
        "depart": depart_source.isoformat(),
        "nombre_partants": len(partants_source),
        "nombre_actifs": actifs,
        "coherence_locale": "VALIDEE",
        "source_collecte_declaree": "PMU TurfInfo",
        "authenticite_externe_certifiee": False,
        "notes_moteurs_verifiees": False,
        "autorisation_publication": False,
        "fige": False,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "sortie/publication_courses.json",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
    )
    args = parser.parse_args()

    source = charger(args.source.resolve())
    enrichie = charger(args.input.resolve())

    bilan = verifier(source, enrichie)

    print(json.dumps(
        bilan,
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"ECHEC_CONTROLE_PMU: {exc}", file=sys.stderr)
        sys.exit(1)
