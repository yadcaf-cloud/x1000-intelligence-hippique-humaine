#!/usr/bin/env python3
"""X1000 - Preparation controlee du site PMU France.

Lecture seule des sources.
Aucune creation de FIGE, pronostic ou archive.
Aucun commit, push ou deploiement.
"""

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SORTIE = ROOT / "sortie"
SITE = ROOT / "site_pmu"

DATE = datetime.now(
    ZoneInfo("Europe/Paris")
).date().isoformat()

COURSES = SORTIE / "publication_courses.json"
CANDIDATS = SORTIE / "recherche_courses_du_jour.json"

# Sources autorisees pour une publication existante.
SOURCES_MOTEURS = (
    ROOT / "data" / "publication_moteurs.json",
    SORTIE / "publication_moteurs.json",
)

def charger(chemin):
    if not chemin.is_file():
        raise RuntimeError(
            f"FICHIER_ABSENT: {chemin}"
        )

    if not chemin.stat().st_size:
        raise RuntimeError(
            f"FICHIER_VIDE: {chemin}"
        )

    try:
        return json.loads(
            chemin.read_text(encoding="utf-8")
        )
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError(
            f"JSON_INVALIDE: {chemin}: {exc}"
        ) from exc


def verifier_courses(data):
    if not isinstance(data, dict):
        raise RuntimeError("FORMAT_COURSES_INVALIDE")

    if data.get("schema") != "x1000-pmu-france-v1":
        raise RuntimeError("SCHEMA_COURSES_INVALIDE")

    if data.get("date_pmu") != DATE:
        raise RuntimeError("DATE_COURSES_INVALIDE")

    if data.get("source") != "PMU TurfInfo":
        raise RuntimeError("SOURCE_COURSES_INVALIDE")

    courses = data.get("courses")

    if not isinstance(courses, list) or not courses:
        raise RuntimeError("AUCUNE_COURSE_VERIFIEE")

    identifiants = set()

    for course in courses:
        if not isinstance(course, dict):
            raise RuntimeError("COURSE_MAL_FORMEE")

        cid = course.get("course_id")

        if (
            not isinstance(cid, str)
            or not cid.startswith(DATE + "-R")
            or cid in identifiants
        ):
            raise RuntimeError(
                "IDENTIFIANT_COURSE_INVALIDE"
            )

        identifiants.add(cid)

        if (
            course.get("pays_verifie") != "FR"
            or course.get("date") != DATE
        ):
            raise RuntimeError(
                "COURSE_HORS_FRANCE_OU_DATE"
            )

        if not course.get("hippodrome"):
            raise RuntimeError("HIPPODROME_ABSENT")

        partants = course.get("partants")

        if not isinstance(partants, list) or not partants:
            raise RuntimeError("PARTANTS_ABSENTS")

    return identifiants


def verifier_candidats(data, identifiants):
    if not isinstance(data, dict):
        raise RuntimeError("FORMAT_CANDIDATS_INVALIDE")

    if (
        data.get("schema") != "x1000-candidates-v1"
        or data.get("date") != DATE
        or data.get("source") != "PMU TurfInfo"
    ):
        raise RuntimeError("CANDIDATS_NON_CONFORMES")

    candidats = data.get("courses_candidates")

    if not isinstance(candidats, list):
        raise RuntimeError("LISTE_CANDIDATS_INVALIDE")

    ids = []

    for candidat in candidats:
        if not isinstance(candidat, dict):
            raise RuntimeError("CANDIDAT_INVALIDE")

        ids.append(candidat.get("course_id"))

    if (
        len(ids) != len(identifiants)
        or set(ids) != identifiants
    ):
        raise RuntimeError(
            "RACCORDEMENT_CANDIDATS_INCOHERENT"
        )


def trouver_moteurs():
    for chemin in SOURCES_MOTEURS:
        if chemin.is_file():
            data = charger(chemin)

            if not isinstance(data, dict):
                raise RuntimeError(
                    "PUBLICATION_MOTEURS_FORMAT_INVALIDE"
                )

            # Validation structurelle minimale seulement.
            # Ne certifie pas l'authenticite des FIGEs.
            return chemin

    return None


def copier(source, destination):
    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )
    shutil.copyfile(source, destination)


def preparer():
    courses = charger(COURSES)
    candidats = charger(CANDIDATS)

    ids = verifier_courses(courses)
    verifier_candidats(candidats, ids)

    moteurs = trouver_moteurs()

    # Preparation isolee : ne touche pas aux
    # sources, FIGEs ou archives historiques.
    with tempfile.TemporaryDirectory(
        prefix="x1000-site-"
    ) as dossier:
        temporaire = Path(dossier) / "site_pmu"

        (temporaire / "data").mkdir(
            parents=True,
            exist_ok=True
        )
        (temporaire / "sortie").mkdir(
            parents=True,
            exist_ok=True
        )

        for nom in ("index.html", "tableau-pmu.html"):
            source = ROOT / nom

            if not source.is_file():
                raise RuntimeError(
                    f"PAGE_ABSENTE: {nom}"
                )

            copier(source, temporaire / nom)

        copier(
            COURSES,
            temporaire / "data"
            / "publication_courses.json"
        )

        copier(
            CANDIDATS,
            temporaire / "sortie"
            / "recherche_courses_du_jour.json"
        )

        statut_moteurs = "SOURCE_ABSENTE"

        if moteurs is not None:
            copier(
                moteurs,
                temporaire / "data"
                / "publication_moteurs.json"
            )
            statut_moteurs = "COPIE_NON_AUTHENTIFIEE"

        etat = {
            "date_pmu": DATE,
            "source": "PMU TurfInfo",
            "nombre_courses": len(ids),
            "statut_courses": "STRUCTURE_VALIDEE",
            "statut_moteurs": statut_moteurs,
            "publication_moteurs_presente": (
                moteurs is not None
            ),
            "authenticite_figes_verifiee": False,
            "deploiement_effectue": False,
        }

        (temporaire / "data"
         / "etat_publication.json").write_text(
            json.dumps(
                etat,
                ensure_ascii=False,
                indent=2
            ) + "\n",
            encoding="utf-8"
        )

        (temporaire / ".nojekyll").touch()

        # Remplace uniquement le dossier de
        # preparation genere par ce script.
        # Ne modifie aucune source historique.
        if SITE.exists():
            if not SITE.is_dir() or SITE.is_symlink():
                raise RuntimeError(
                    "DOSSIER_SITE_NON_CONFORME"
                )
            shutil.rmtree(SITE)

        shutil.copytree(temporaire, SITE)

    print("=== X1000 PREPARATION SITE ===")
    print("Date PMU :", DATE)
    print("Courses :", len(ids))
    print("Moteurs :", statut_moteurs)
    print("Dossier :", SITE)
    print("Aucun FIGE modifie")
    print("Aucune archive modifiee")
    print("Aucun pronostic cree")
    print("Aucun deploiement effectue")

    if moteurs is None:
        print(
            "ATTENTION : publication_moteurs.json "
            "absent des sources autorisees."
        )

    print("PREPARATION_TERMINEE")


if __name__ == "__main__":
    try:
        preparer()
    except Exception as exc:
        print("ECHEC_PREPARATION:", exc)
        raise SystemExit(1)
