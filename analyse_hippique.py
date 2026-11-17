#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 — Moteur d'analyse factuelle PMU sécurisé V1.0.

Lit exclusivement data/publication_courses.json.
Produit data/publication_analyses.json après validation stricte.
Aucun pronostic, classement, FIGÉ, résultat ou donnée hippique inventés.
Python 3.10+ ; bibliothèque standard.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCE = DATA / "publication_courses.json"
DEST = DATA / "publication_analyses.json"
PARIS = ZoneInfo("Europe/Paris")
VERSION = "X1000-ANALYSE-FACTUELLE-1.0"


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def parse_instant(value):
    if not nonempty(value) or not re.search(r"(?:Z|[+-]\d\d:\d\d)$", value):
        raise ValueError("Horodatage sans fuseau explicite")
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Horodatage ISO invalide") from exc


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError("Publication non objet")
    if payload.get("statut") not in ("OK", "PARTIEL"):
        raise ValueError("Publication PMU non validée")
    date = payload.get("date")
    if not nonempty(date) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("Date de programme absente ou invalide")
    try:
        dt.date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("Date impossible") from exc
    if not isinstance(payload.get("courses"), list) or not payload["courses"]:
        raise ValueError("Aucune course PMU publiée")
    if not isinstance(payload.get("erreurs"), list):
        raise ValueError("Liste des erreurs absente")
    if payload["statut"] == "OK" and payload["erreurs"]:
        raise ValueError("Statut OK incohérent")
    parse_instant(payload.get("collecte_a"))
    return date


def validate_course(course, date):
    if not isinstance(course, dict):
        raise ValueError("Course non objet")
    cid = course.get("id")
    if not nonempty(cid) or not re.fullmatch(re.escape(date) + r"-R[1-9]\d*-C[1-9]\d*", cid):
        raise ValueError("Identifiant course invalide")
    if course.get("localisation_france_verifiee") is not True:
        raise ValueError(f"{cid}: France non vérifiée")
    if course.get("depart_verifie") is not True:
        raise ValueError(f"{cid}: départ non vérifié")
    if not nonempty(course.get("discipline")):
        raise ValueError(f"{cid}: discipline manquante")
    depart = parse_instant(course.get("depart"))
    if depart.astimezone(PARIS).date().isoformat() != date:
        raise ValueError(f"{cid}: date de départ incohérente")
    horses = course.get("partants")
    if not isinstance(horses, list) or not horses:
        raise ValueError(f"{cid}: participants absents")
    if not isinstance(course.get("non_partants"), list):
        raise ValueError(f"{cid}: non-partants absents")
    if type(course.get("nombre_participants")) is not int or course["nombre_participants"] != len(horses):
        raise ValueError(f"{cid}: nombre de participants incohérent")
    seen, np = set(), set()
    for horse in horses:
        if not isinstance(horse, dict):
            raise ValueError(f"{cid}: participant non objet")
        num = horse.get("numero")
        if type(num) is not int or num <= 0 or num in seen or not nonempty(horse.get("nom")) or type(horse.get("non_partant")) is not bool:
            raise ValueError(f"{cid}: participant invalide ou doublon")
        seen.add(num)
        if horse["non_partant"]:
            np.add(num)
    declared = set()
    for item in course["non_partants"]:
        if not isinstance(item, dict) or type(item.get("numero")) is not int or item["numero"] not in np or item["numero"] in declared:
            raise ValueError(f"{cid}: liste de non-partants incohérente")
        declared.add(item["numero"])
    if declared != np or type(course.get("nombre_partants")) is not int or course["nombre_partants"] != len(seen) - len(np) or course["nombre_partants"] < 1:
        raise ValueError(f"{cid}: décompte des partants incohérent")
    return cid


FIELDS = {
    "musique": "Musique",
    "jockey_driver": "Jockey ou driver",
    "entraineur": "Entraîneur",
    "age": "Âge",
    "sexe": "Sexe",
    "poids": "Poids",
    "corde": "Corde",
    "distance": "Distance",
    "gains": "Gains",
}


def analyze(payload, raw):
    date = validate(payload)
    output = []
    seen = set()
    for course in payload["courses"]:
        cid = validate_course(course, date)
        if cid in seen:
            raise ValueError(f"Course en doublon : {cid}")
        seen.add(cid)
        participants = []
        for horse in sorted(course["partants"], key=lambda h: h["numero"]):
            facts = {}
            for key, title in FIELDS.items():
                value = horse.get(key)
                if type(value) in (int, float) and not isinstance(value, bool):
                    facts[key] = value
                elif nonempty(value):
                    facts[key] = value.strip()
            missing = [title for key, title in FIELDS.items() if key not in facts]
            participants.append({
                "numero": horse["numero"],
                "nom": horse["nom"].strip(),
                "non_partant": horse["non_partant"],
                "faits_disponibles": facts,
                "champs_non_renseignes": missing,
                "evaluation_sportive": "NON_EVALUE",
                "motif": "Données descriptives uniquement ; aucune méthode prédictive validée",
            })
        output.append({
            "id": cid,
            "nom": course.get("nom") if nonempty(course.get("nom")) else None,
            "hippodrome": course.get("hippodrome") if nonempty(course.get("hippodrome")) else None,
            "discipline": course["discipline"],
            "depart": course["depart"],
            "nombre_partants": course["nombre_partants"],
            "chevaux": participants,
            "selection": None,
            "fige": None,
            "resultat": None,
            "statut": "ANALYSE_DESCRIPTIVE",
        })
    return {
        "version": VERSION,
        "date": date,
        "source": "data/publication_courses.json",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "collecte_a": payload["collecte_a"],
        "analyse_a": dt.datetime.now(dt.timezone.utc).isoformat(),
        "statut": "DESCRIPTIF_SANS_PRONOSTIC",
        "courses": output,
    }


def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".x1000-", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def self_test():
    date = "2026-11-01"
    sample = {"date": date, "statut": "OK", "erreurs": [], "collecte_a": "2026-11-01T08:00:00+01:00", "courses": [{"id": date + "-R1-C1", "localisation_france_verifiee": True, "depart_verifie": True, "discipline": "TEST", "depart": "2026-11-01T14:00:00+01:00", "nombre_participants": 1, "nombre_partants": 1, "non_partants": [], "partants": [{"numero": 1, "nom": "CHEVAL_TEST", "non_partant": False}]}]}
    result = analyze(sample, b"test")
    assert result["courses"][0]["selection"] is None
    assert result["courses"][0]["chevaux"][0]["evaluation_sportive"] == "NON_EVALUE"
    sample["courses"][0]["localisation_france_verifiee"] = False
    try:
        analyze(sample, b"test")
    except ValueError:
        pass
    else:
        raise AssertionError("Course hors France acceptée")
    print("SELF-TEST OK — données synthétiques utilisées uniquement en mémoire ; aucun fichier écrit")


def main():
    parser = argparse.ArgumentParser(description=VERSION)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not SOURCE.is_file():
        print("ERREUR : data/publication_courses.json absent ; collecte PMU préalable nécessaire", file=sys.stderr)
        return 1
    try:
        raw = SOURCE.read_bytes()
        payload = json.loads(raw)
        result = analyze(payload, raw)
        if not args.dry_run:
            atomic_write(DEST, result)
        print(json.dumps({"statut": result["statut"], "date": result["date"], "courses": len(result["courses"]), "chevaux": sum(len(c["chevaux"]) for c in result["courses"]), "source_sha256": result["source_sha256"], "dry_run": args.dry_run}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
