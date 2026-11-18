#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 - Test controle execution M1-M5 V1.1.

Entree : JSON de course enrichi avec les notes M1-M5.
Sortie : bilan JSON sur la sortie standard.

Aucune note manquante inventee.
Aucun FIGE, archive, pari ou pronostic officiel produit.
Aucune ecriture effectuee par ce coordinateur.

Attention : les moteurs executes sont du code Python.
Leur absence d'effets secondaires doit etre verifiee
separement. -I ne constitue pas un bac a sable.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MOTEURS = ("M1", "M2", "M3", "M4", "M5")

ID_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})-R[1-9]\d*-C[1-9]\d*$"
)


def exiger(condition, message):
    if not condition:
        raise ValueError(message)


def lire_course(chemin):
    exiger(
        chemin.is_file(),
        "FICHIER_COURSE_ABSENT",
    )

    exiger(
        chemin.stat().st_size > 0,
        "FICHIER_COURSE_VIDE",
    )

    course = json.loads(
        chemin.read_text(encoding="utf-8"),
        parse_constant=lambda x: (_ for _ in ()).throw(
            ValueError(f"CONSTANTE_JSON_INTERDITE: {x}")
        ),
    )

    exiger(
        isinstance(course, dict),
        "COURSE_INVALIDE",
    )

    cid = course.get("course_id")

    exiger(
        isinstance(cid, str),
        "COURSE_ID_ABSENT",
    )

    match = ID_RE.fullmatch(cid)

    exiger(
        match is not None,
        "COURSE_ID_INVALIDE",
    )

    try:
        date.fromisoformat(match.group(1))
    except ValueError as exc:
        raise ValueError(
            "DATE_COURSE_INVALIDE"
        ) from exc

    exiger(
        course.get("pays_course") == "FR",
        "COURSE_HORS_FRANCE",
    )

    exiger(
        course.get("source_verifiee") is True,
        "ATTESTATION_SOURCE_ABSENTE",
    )

    for champ in ("source", "hippodrome"):
        valeur = course.get(champ)

        exiger(
            isinstance(valeur, str)
            and bool(valeur.strip()),
            f"{champ.upper()}_ABSENT",
        )

    partants = course.get("partants")

    exiger(
        isinstance(partants, list)
        and len(partants) >= 5,
        "PARTANTS_INSUFFISANTS",
    )

    numeros = set()
    actifs = set()

    for cheval in partants:
        exiger(
            isinstance(cheval, dict),
            "PARTANT_INVALIDE",
        )

        numero = cheval.get("numero")

        exiger(
            type(numero) is int
            and numero > 0,
            "NUMERO_PARTANT_INVALIDE",
        )

        exiger(
            numero not in numeros,
            f"PARTANT_DUPLIQUE: {numero}",
        )

        numeros.add(numero)

        nom = cheval.get("nom")

        exiger(
            isinstance(nom, str)
            and bool(nom.strip()),
            f"NOM_PARTANT_ABSENT: {numero}",
        )

        non_partant = cheval.get(
            "non_partant", False
        )

        exiger(
            type(non_partant) is bool,
            f"STATUT_PARTANT_INVALIDE: {numero}",
        )

        if not non_partant:
            actifs.add(numero)

    exiger(
        len(actifs) >= 5,
        "MOINS_DE_CINQ_PARTANTS_ACTIFS",
    )

    return course, actifs


def valider_classement(nom, resultat, course, actifs):
    exiger(
        isinstance(resultat, dict),
        f"{nom}: SORTIE_NON_OBJET",
    )

    exiger(
        resultat.get("moteur") == nom,
        f"{nom}: IDENTITE_INVALIDE",
    )

    exiger(
        resultat.get("course_id")
        == course["course_id"],
        f"{nom}: COURSE_DIFFERENTE",
    )

    exiger(
        resultat.get("source")
        == course["source"],
        f"{nom}: SOURCE_DIFFERENTE",
    )

    exiger(
        resultat.get("fige") is False,
        f"{nom}: FIGE_NON_AUTORISE",
    )

    exiger(
        resultat.get("publication_officielle") is False,
        f"{nom}: PUBLICATION_NON_AUTORISEE",
    )

    exiger(
        resultat.get("pari") is None,
        f"{nom}: PARI_NON_AUTORISE",
    )

    classement = resultat.get("classement")

    exiger(
        isinstance(classement, list)
        and len(classement) == 5,
        f"{nom}: CLASSEMENT_NON_CINQ",
    )

    numeros = []
    scores = []

    for ligne in classement:
        exiger(
            isinstance(ligne, dict),
            f"{nom}: LIGNE_INVALIDE",
        )

        numero = ligne.get("numero")

        exiger(
            type(numero) is int
            and numero in actifs,
            f"{nom}: CHEVAL_INCONNU_OU_NON_PARTANT",
        )

        score = ligne.get("score")

        exiger(
            type(score) in (int, float)
            and math.isfinite(score)
            and 0 <= score <= 100,
            f"{nom}: SCORE_INVALIDE",
        )

        numeros.append(numero)
        scores.append(score)

    exiger(
        len(set(numeros)) == 5,
        f"{nom}: DOUBLONS_CLASSEMENT",
    )

    exiger(
        all(
            scores[i] >= scores[i + 1]
            for i in range(4)
        ),
        f"{nom}: SCORES_NON_TRIES",
    )

    version = resultat.get("version")

    exiger(
        isinstance(version, str)
        and bool(version.strip()),
        f"{nom}: VERSION_ABSENTE",
    )

    return {
        "statut": "EXECUTION_REUSSIE",
        "version": version,
        "classement": numeros,
        "scores": scores,
        "fige": False,
        "publication_officielle": False,
    }


def executer_moteur(nom, chemin, course, actifs):
    script = ROOT / "moteurs" / (
        f"{nom.lower()}.py"
    )

    exiger(
        script.is_file(),
        f"{nom}: FICHIER_MOTEUR_ABSENT",
    )

    try:
        processus = subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                str(script),
                "--input",
                str(chemin),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"{nom}: DELAI_EXECUTION_DEPASSE"
        ) from exc

    exiger(
        processus.returncode == 0,
        f"{nom}: EXECUTION_ECHOUEE: "
        f"{processus.stderr.strip()[:500]}",
    )

    try:
        resultat = json.loads(
            processus.stdout,
            parse_constant=lambda x: (
                (_ for _ in ()).throw(
                    ValueError(
                        f"CONSTANTE_INTERDITE: {x}"
                    )
                )
            ),
        )
    except (
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        raise ValueError(
            f"{nom}: SORTIE_JSON_INVALIDE"
        ) from exc

    return valider_classement(
        nom,
        resultat,
        course,
        actifs,
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "X1000 - Test execution controlee M1-M5"
        )
    )

    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Fichier JSON de course enrichie",
    )

    args = parser.parse_args()
    chemin = args.input.resolve(strict=True)

    course, actifs = lire_course(chemin)

    resultats = {}
    erreurs = {}

    for nom in MOTEURS:
        try:
            resultats[nom] = executer_moteur(
                nom,
                chemin,
                course,
                actifs,
            )
        except (ValueError, OSError) as exc:
            erreurs[nom] = str(exc)

    tous_reussis = len(resultats) == len(MOTEURS)

    bilan = {
        "systeme": "X1000",
        "version": "1.1",
        "course_id": course["course_id"],
        "hippodrome": course["hippodrome"],
        "source_declaree": course["source"],
        "verification_externe_effectuee": False,
        "moteurs": resultats,
        "erreurs": erreurs,
        "moteurs_reussis": len(resultats),
        "moteurs_attendus": len(MOTEURS),
        "coordination": "CONTROLE_TECHNIQUE",
        "arbitrage_x1000_effectue": False,
        "raccordement_pmu_certifie": False,
        "publication_officielle": False,
        "fige": False,
        "statut": (
            "CINQ_MOTEURS_EXECUTES"
            if tous_reussis
            else "ECHEC_PARTIEL_OU_TOTAL"
        ),
    }

    print(
        json.dumps(
            bilan,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )

    return 0 if tous_reussis else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        ValueError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"ERREUR X1000: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
