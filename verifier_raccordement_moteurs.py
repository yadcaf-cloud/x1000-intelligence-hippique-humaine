#!/usr/bin/env python3
"""X1000 - Controle general du raccordement PMU / M1-M5 / X1000.

Validation structurelle et chronologique des publications par course.
Lecture seule : aucun pronostic, FIGE, audit, commit ou deploiement.
Ne certifie pas l'authenticite des moteurs ni des sources externes.
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)
TODAY = NOW.date().isoformat()

COURSES = ROOT / "sortie/publication_courses.json"
SOURCES = (
    ROOT / "data/publication_moteurs.json",
    ROOT / "sortie/publication_moteurs.json",
)

MOTEURS = ("M1", "M2", "M3", "M4", "M5")
ID_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$"
)

TOLERANCE_HORLOGE = timedelta(minutes=5)


def exiger(condition, message):
    if not condition:
        raise ValueError(message)


def lire(path):
    exiger(path.is_file(), f"FICHIER_ABSENT: {path}")
    exiger(path.stat().st_size > 0, f"FICHIER_VIDE: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(
            f"JSON_INVALIDE: {path}: {exc}"
        ) from exc

    exiger(
        isinstance(data, dict),
        f"OBJET_JSON_ATTENDU: {path}"
    )
    return data


def horodatage(value, contexte):
    exiger(
        isinstance(value, str) and bool(value.strip()),
        f"HORODATAGE_ABSENT: {contexte}"
    )

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError(
            f"HORODATAGE_INVALIDE: {contexte}"
        ) from exc

    exiger(
        dt.tzinfo is not None and dt.utcoffset() is not None,
        f"FUSEAU_ABSENT: {contexte}"
    )

    return dt.astimezone(PARIS)


def date_du_jour(value, contexte, futur_interdit=False):
    dt = horodatage(value, contexte)

    exiger(
        dt.date().isoformat() == TODAY,
        f"DATE_OBSOLETE: {contexte}"
    )

    if futur_interdit:
        exiger(
            dt <= NOW + TOLERANCE_HORLOGE,
            f"HORODATAGE_FUTUR: {contexte}"
        )

    return dt


def instant(dt):
    return dt.astimezone(timezone.utc)


def verifier_courses():
    data = lire(COURSES)

    exiger(
        data.get("schema") == "x1000-pmu-france-v1",
        "SCHEMA_PMU_INVALIDE"
    )
    exiger(
        data.get("date_pmu") == TODAY,
        "DATE_PMU_OBSOLETE"
    )
    exiger(
        data.get("source") == "PMU TurfInfo",
        "SOURCE_PMU_INVALIDE"
    )

    liste = data.get("courses")
    exiger(
        isinstance(liste, list) and bool(liste),
        "COURSES_PMU_ABSENTES"
    )

    resultat = {}

    for course in liste:
        exiger(
            isinstance(course, dict),
            "COURSE_MAL_FORMEE"
        )

        cid = course.get("course_id")
        exiger(
            isinstance(cid, str)
            and ID_RE.fullmatch(cid) is not None,
            f"COURSE_ID_INVALIDE: {cid}"
        )
        exiger(
            cid.startswith(TODAY + "-"),
            f"COURSE_HORS_DATE: {cid}"
        )
        exiger(
            cid not in resultat,
            f"COURSE_DUPLIQUEE: {cid}"
        )
        exiger(
            course.get("pays_verifie") == "FR",
            f"COURSE_HORS_FRANCE: {cid}"
        )

        hippodrome = course.get("hippodrome")
        exiger(
            isinstance(hippodrome, str)
            and bool(hippodrome.strip()),
            f"HIPPODROME_ABSENT: {cid}"
        )

        depart = date_du_jour(
            course.get("depart"),
            f"DEPART {cid}"
        )

        partants = course.get("partants")
        exiger(
            isinstance(partants, list) and bool(partants),
            f"PARTANTS_ABSENTS: {cid}"
        )

        numeros = set()
        actifs = set()

        for cheval in partants:
            exiger(
                isinstance(cheval, dict),
                f"PARTANT_MAL_FORME: {cid}"
            )

            numero = cheval.get("numero")
            exiger(
                type(numero) is int
                and numero > 0
                and numero not in numeros,
                f"NUMERO_PARTANT_INVALIDE: {cid}"
            )
            numeros.add(numero)

            nom = cheval.get("nom")
            exiger(
                isinstance(nom, str) and bool(nom.strip()),
                f"NOM_PARTANT_ABSENT: {cid}"
            )

            np = cheval.get("non_partant")
            exiger(
                type(np) is bool,
                f"STATUT_PARTANT_INVALIDE: {cid}"
            )

            if not np:
                actifs.add(numero)

        resultat[cid] = {
            "hippodrome": hippodrome.strip(),
            "depart": depart,
            "actifs": actifs,
        }

    print("COURSES_PMU_STRUCTURE_OK:", len(resultat))
    return resultat


def verifier_decision(
    decision, cid, actifs, depart, nom, publie_a
):
    contexte = f"{nom} {cid}"

    exiger(
        isinstance(decision, dict),
        f"DECISION_MAL_FORMEE: {contexte}"
    )
    exiger(
        decision.get("course_id") in (None, cid),
        f"DECISION_COURSE_INCOHERENTE: {contexte}"
    )

    classement = decision.get("classement")
    exiger(
        isinstance(classement, list)
        and len(classement) == 5,
        f"CLASSEMENT_NON_CINQ: {contexte}"
    )
    exiger(
        all(type(n) is int and n > 0 for n in classement),
        f"NUMEROS_INVALIDES: {contexte}"
    )
    exiger(
        len(set(classement)) == 5,
        f"DOUBLONS_CLASSEMENT: {contexte}"
    )
    exiger(
        set(classement).issubset(actifs),
        f"CHEVAL_NON_PARTANT: {contexte}"
    )

    analyse = decision.get("analyse")
    exiger(
        isinstance(analyse, str)
        and len(analyse.strip()) >= 30,
        f"ANALYSE_INSUFFISANTE: {contexte}"
    )

    source = decision.get("source")
    exiger(
        isinstance(source, str) and bool(source.strip()),
        f"SOURCE_DECISION_ABSENTE: {contexte}"
    )

    date = date_du_jour(
        decision.get("date"),
        contexte,
        futur_interdit=True
    )

    exiger(
        instant(date) < instant(depart),
        f"DECISION_APRES_DEPART: {contexte}"
    )
    exiger(
        instant(date) <= instant(publie_a) + TOLERANCE_HORLOGE,
        f"DECISION_POSTERIEURE_PUBLICATION: {contexte}"
    )

    statut = decision.get("statut")
    exiger(
        isinstance(statut, str) and bool(statut.strip()),
        f"STATUT_ABSENT: {contexte}"
    )

    pari = decision.get("pari")
    exiger(
        pari is None or isinstance(pari, str),
        f"PARI_INVALIDE: {contexte}"
    )

    print(f"{cid} {nom}: STRUCTURE_ET_CHRONOLOGIE_OK")


def verifier_publication(courses):
    presentes = [p for p in SOURCES if p.is_file()]

    exiger(
        bool(presentes),
        "PUBLICATION_MOTEURS_ABSENTE"
    )

    if len(presentes) == 2:
        exiger(
            presentes[0].read_bytes() == presentes[1].read_bytes(),
            "PUBLICATIONS_MOTEURS_CONTRADICTOIRES"
        )

    data = lire(presentes[0])

    exiger(
        data.get("schema") == "x1000-moteurs-v1",
        "SCHEMA_MOTEURS_INVALIDE"
    )
    exiger(
        data.get("date_pmu") == TODAY,
        "DATE_MOTEURS_OBSOLETE"
    )

    publie_a = date_du_jour(
        data.get("publie_a"),
        "PUBLICATION_MOTEURS",
        futur_interdit=True
    )

    publications = data.get("courses")
    exiger(
        isinstance(publications, list)
        and bool(publications),
        "DECISIONS_PAR_COURSE_ABSENTES"
    )

    vus = set()

    for item in publications:
        exiger(
            isinstance(item, dict),
            "PUBLICATION_COURSE_MAL_FORMEE"
        )

        cid = item.get("course_id")
        exiger(
            isinstance(cid, str) and cid in courses,
            f"COURSE_NON_VERIFIEE: {cid}"
        )
        exiger(
            cid not in vus,
            f"DECISION_COURSE_DUPLIQUEE: {cid}"
        )
        vus.add(cid)

        reference = courses[cid]
        actifs = reference["actifs"]
        depart = reference["depart"]

        exiger(
            len(actifs) >= 5,
            f"MOINS_DE_CINQ_PARTANTS: {cid}"
        )

        hippodrome = item.get("hippodrome")
        exiger(
            isinstance(hippodrome, str)
            and hippodrome.strip() == reference["hippodrome"],
            f"HIPPODROME_INCOHERENT: {cid}"
        )

        depart_publie = date_du_jour(
            item.get("depart"),
            f"DEPART_PUBLICATION {cid}"
        )
        exiger(
            instant(depart_publie) == instant(depart),
            f"DEPART_INCOHERENT: {cid}"
        )

        moteurs = item.get("moteurs")
        exiger(
            isinstance(moteurs, dict)
            and set(moteurs) == set(MOTEURS),
            f"CINQ_MOTEURS_NON_IDENTIFIES: {cid}"
        )

        for nom in MOTEURS:
            verifier_decision(
                moteurs[nom],
                cid,
                actifs,
                depart,
                nom,
                publie_a
            )

        verifier_decision(
            item.get("arbitre_x1000"),
            cid,
            actifs,
            depart,
            "X1000",
            publie_a
        )

    # Le producteur actuel publie None au niveau global.
    # Les arbitrages reels sont conserves par course.
    exiger(
        data.get("arbitre_x1000") is None,
        "ARBITRE_GLOBAL_NON_PRIS_EN_CHARGE"
    )

    print(
        "PUBLICATION_STRUCTURE_VALIDEE:",
        len(vus),
        "course(s)"
    )
    print("PROVENANCE_DECLAREE_NON_AUTHENTIFIEE")
    print("EXECUTION_AUTONOME_NON_PROUVEE")
    print("FIGES_NON_AUTHENTIFIES")
    print("AUCUN_DEPLOIEMENT")


def main():
    print("=== X1000 CONTROLE RACCORDEMENT M1-M5 ===")
    print("DATE_PARIS:", TODAY)

    courses = verifier_courses()
    verifier_publication(courses)

    print("CONTROLE_RACCORDEMENT_TERMINE")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print("::error::" + str(exc), file=sys.stderr)
        sys.exit(1)
