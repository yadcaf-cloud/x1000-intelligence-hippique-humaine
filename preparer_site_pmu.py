#!/usr/bin/env python3
"""Prépare un site PMU vérifié sans toucher aux pronostics."""
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "sortie/publication_courses.json"
CANDIDATS = ROOT / "sortie/recherche_courses_du_jour.json"
SITE = ROOT / "site_pmu"
TODAY = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()

def lire(path):
    if not path.is_file():
        raise SystemExit(f"FICHIER_ABSENT: {path}")
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    data = lire(SOURCE)
    candidats = lire(CANDIDATS)

    if data.get("schema") != "x1000-pmu-france-v1" or data.get("date_pmu") != TODAY:
        raise SystemExit("PUBLICATION_PMU_PERIMEE_OU_SCHEMA_INVALIDE")

    if candidats.get("schema") != "x1000-candidates-v1" or candidats.get("date") != TODAY:
        raise SystemExit("CANDIDATS_PERIMES_OU_SCHEMA_INVALIDE")

    if data.get("source") != "PMU TurfInfo" or candidats.get("source") != "PMU TurfInfo":
        raise SystemExit("SOURCE_NON_VERIFIEE")

    courses = data.get("courses")
    if not isinstance(courses, list) or not courses:
        raise SystemExit("AUCUNE_COURSE_VERIFIEE")

    ids = set()
    for c in courses:
        if not isinstance(c, dict) or c.get("pays_verifie") != "FR" or c.get("date") != TODAY:
            raise SystemExit("COURSE_HORS_FRANCE_OU_DATE")
        cid = c.get("course_id")
        if not isinstance(cid, str) or not cid.startswith(TODAY + "-R") or cid in ids:
            raise SystemExit("IDENTIFIANT_COURSE_INVALIDE")
        ids.add(cid)
        if not isinstance(c.get("partants"), list) or not c["partants"]:
            raise SystemExit("PARTANTS_ABSENTS")

    liste = candidats.get("courses_candidates")
    if not isinstance(liste, list) or len(liste) != len(ids) or {
        c.get("course_id") for c in liste if isinstance(c, dict)
    } != ids:
        raise SystemExit("RACCORDEMENT_CANDIDATS_INCOHERENT")

    try:
        publie = datetime.fromisoformat(
            data["date_publication"].replace("Z", "+00:00")
        )
    except (KeyError, ValueError, AttributeError):
        raise SystemExit("HORODATAGE_PUBLICATION_INVALIDE")

    if publie.tzinfo is None or publie.astimezone(
        ZoneInfo("Europe/Paris")
    ).date().isoformat() != TODAY:
        raise SystemExit("PUBLICATION_NON_ACTUELLE")

    (SITE / "data").mkdir(parents=True, exist_ok=True)
    (SITE / "sortie").mkdir(parents=True, exist_ok=True)

    for name in ("index.html", "tableau-pmu.html"):
        shutil.copyfile(ROOT / name, SITE / name)

    shutil.copyfile(SOURCE, SITE / "data/publication_courses.json")
    shutil.copyfile(CANDIDATS, SITE / "sortie/recherche_courses_du_jour.json")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    etat = {
        "date_pmu": TODAY,
        "date_publication": data["date_publication"],
        "courses": len(courses),
        "source": "PMU TurfInfo",
        "statut": "DONNEES_VERIFIEES"
    }
    (SITE / "data/etat_publication.json").write_text(
        json.dumps(etat, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    print(f"SITE_PRET: {len(courses)} courses du {TODAY}")
    print("Aucun FIGE, archive ou pronostic modifie")

if __name__ == "__main__":
    main()
