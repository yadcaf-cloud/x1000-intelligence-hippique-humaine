#!/usr/bin/env python3
"""Adaptateur de publication X1000, lecture seule des données collectées."""

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SRC = Path("sortie/publication_courses.json")
DEST = Path("sortie/recherche_courses_du_jour.json")

now = datetime.now(ZoneInfo("Europe/Paris"))
today = now.strftime("%Y-%m-%d")

if not SRC.is_file():
    raise SystemExit(
        "SOURCE_ABSENTE: sortie/publication_courses.json"
    )

data = json.loads(SRC.read_text(encoding="utf-8"))

if (
    data.get("schema") != "x1000-pmu-france-v1"
    or data.get("date_pmu") != today
):
    raise SystemExit("SCHEMA_OU_DATE_INVALIDE")

if data.get("source") != "PMU TurfInfo":
    raise SystemExit("SOURCE_NON_RECONNUE")

items = data.get("courses")

if not isinstance(items, list) or not items:
    raise SystemExit("AUCUNE_COURSE_COLLECTEE")

output = []
seen = set()

for c in items:
    if not isinstance(c, dict):
        raise SystemExit("COURSE_MAL_FORMEE")

    cid = c.get("course_id")

    if (
        not isinstance(cid, str)
        or not re.fullmatch(
            re.escape(today) + r"-R[1-9]\d*-C[1-9]\d*",
            cid
        )
        or cid in seen
    ):
        raise SystemExit("IDENTIFIANT_COURSE_INVALIDE")

    seen.add(cid)

    if (
        c.get("pays_verifie") != "FR"
        or not isinstance(c.get("hippodrome"), str)
        or not c["hippodrome"].strip()
    ):
        raise SystemExit("PAYS_OU_HIPPODROME_INVALIDE")

    depart = c.get("depart")

    if (
        not isinstance(depart, str)
        or not depart.startswith(today + "T")
    ):
        raise SystemExit("DEPART_INVALIDE")

    try:
        datetime.fromisoformat(depart)
    except ValueError:
        raise SystemExit("DATE_DEPART_MAL_FORMEE")

    horses = c.get("partants")

    if not isinstance(horses, list) or not horses:
        raise SystemExit("PARTANTS_ABSENTS")

    nums = set()
    cleaned = []

    for h in horses:
        if (
            not isinstance(h, dict)
            or type(h.get("numero")) is not int
            or h["numero"] <= 0
            or h["numero"] in nums
            or not isinstance(h.get("nom"), str)
            or not h["nom"].strip()
            or type(h.get("non_partant")) is not bool
        ):
            raise SystemExit("PARTANT_INVALIDE")

        nums.add(h["numero"])

        cleaned.append({
            "numero": h["numero"],
            "nom": h["nom"].strip(),
            "non_partant": h["non_partant"]
        })

    output.append({
        "course_id": cid,
        "hippodrome": c["hippodrome"].strip(),
        "date": today,
        "depart_source": depart,
        "distance": c.get("distance"),
        "discipline": c.get("discipline"),
        "nom_course": c.get("nom_course"),
        "pays_verifie": "FR",
        "partants": cleaned,
        "source": "PMU TurfInfo",
        "statut": "CANDIDATE_A_ANALYSER"
    })

result = {
    "schema": "x1000-candidates-v1",
    "date": today,
    "recherche_a": now.isoformat(),
    "source": "PMU TurfInfo",
    "statut": "COLLECTE_VALIDEE",
    "courses_candidates": output,
    "diagnostics": data.get("diagnostics", [])[:100]
}

DEST.parent.mkdir(parents=True, exist_ok=True)

DEST.write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8"
)

print(
    "ADAPTATEUR_OK",
    len(output),
    "courses candidates",
    DEST
)
