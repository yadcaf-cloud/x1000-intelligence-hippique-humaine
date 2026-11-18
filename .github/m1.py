"""M1 — X1000 Intelligence Hippique Humaine.
Moteur statistique indépendant. Python >= 3.10, bibliothèque standard.
Aucun réseau, pari réel, FIGÉ automatique, commit ou déploiement.
Les données fournies doivent être vérifiées en amont par la coordination.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

ENGINE = "M1"
ROOT = Path(__file__).resolve().parent / "data_m1"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$")
ALLOWED = frozenset({"courses.json", "selections.json", "figes.json", "audits.json", "import_diagnostic.json"})
WEIGHTS = {"forme": .25, "regularite": .20, "chrono": .15,
           "distance": .15, "terrain": .10, "opposition": .10, "valeur_cachee": .05}
FLAGS = ("partants_confirmes", "np_confirmes", "horaire_confirme",
         "formule_confirmee", "source_france_confirmee")


def now():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("date ISO 8601 avec fuseau obligatoire")
    try:
        value = value.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date ISO 8601 invalide") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("fuseau horaire obligatoire")
    return parsed.astimezone(dt.timezone.utc)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load(name, default):
    if name not in ALLOWED:
        raise ValueError("fichier non autorisé")
    path = ROOT / name
    return read_json(path) if path.exists() else default


def save(name, value):
    if name not in ALLOWED:
        raise ValueError("fichier non autorisé")
    ROOT.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".m1-", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, ROOT / name)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def require_list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} doit être une liste")
    return value


def valid_id(course_id):
    if not isinstance(course_id, str) or not ID_RE.fullmatch(course_id):
        raise ValueError("course_id invalide")
    try:
        dt.date.fromisoformat(course_id[:10])
    except ValueError as exc:
        raise ValueError("date du course_id invalide") from exc
    return course_id


def validate_course(course):
    if not isinstance(course, dict):
        raise ValueError("course invalide")
    course_id = valid_id(course.get("course_id"))
    depart = parse_time(course.get("depart"))
    local_date = dt.datetime.fromisoformat(course["depart"].replace("Z", "+00:00")).date()
    if local_date.isoformat() != course_id[:10]:
        raise ValueError("date locale et course_id incohérents")
    if course.get("pays") != "FR":
        raise ValueError("course physiquement disputée en France uniquement")
    if not isinstance(course.get("hippodrome"), str) or not course["hippodrome"].strip():
        raise ValueError("hippodrome obligatoire")
    horses = require_list(course.get("partants"), "partants")
    if len(horses) < 5:
        raise ValueError("au moins cinq partants identifiés requis")
    seen = set()
    for horse in horses:
        if not isinstance(horse, dict):
            raise ValueError("partant invalide")
        number = horse.get("numero")
        if type(number) is not int or number <= 0 or number in seen:
            raise ValueError("numéro de partant invalide ou dupliqué")
        if not isinstance(horse.get("nom"), str) or not horse["nom"].strip():
            raise ValueError("nom de partant obligatoire")
        if type(horse.get("non_partant", False)) is not bool:
            raise ValueError("non_partant doit être booléen")
        for key in WEIGHTS:
            if key in horse and horse[key] is not None:
                value = horse[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError(f"{key} doit être numérique entre 0 et 100")
        seen.add(number)
    return depart


def active(course):
    return [h for h in course["partants"] if not h.get("non_partant", False)]


def score(horse):
    known = {key: float(horse[key]) for key in WEIGHTS if horse.get(key) is not None}
    details = {key: known.get(key) for key in WEIGHTS}
    if not known:
        return 50.0, details, 0
    weighted = sum(known[key] * WEIGHTS[key] for key in known)
    coverage = sum(WEIGHTS[key] for key in known)
    # Le score conserve une base neutre pour les critères absents.
    result = weighted + 50.0 * (1.0 - coverage)
    return round(result, 3), details, len(known)


def analyze(course):
    validate_course(course)
    horses = active(course)
    if len(horses) < 5:
        raise ValueError("moins de cinq partants actifs")
    ranked = []
    for horse in horses:
        value, details, known = score(horse)
        ranked.append({"numero": horse["numero"], "nom": horse["nom"],
                       "score": value, "indicateurs": details,
                       "criteres_renseignes": known})
    ranked.sort(key=lambda item: (-item["score"], -item["criteres_renseignes"], item["numero"]))
    return ranked


def find_course(course_id):
    valid_id(course_id)
    courses = require_list(load("courses.json", []), "courses.json")
    matches = [c for c in courses if isinstance(c, dict) and c.get("course_id") == course_id]
    if len(matches) != 1:
        raise ValueError("course absente ou dupliquée")
    validate_course(matches[0])
    return matches[0]


def digest(body):
    payload = json.dumps(body, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_archives():
    archives = require_list(load("figes.json", []), "figes.json")
    versions = {}
    previous_times = {}
    for item in archives:
        if not isinstance(item, dict):
            raise ValueError("archive FIGÉ invalide")
        body = {k: v for k, v in item.items() if k != "sha256"}
        if digest(body) != item.get("sha256"):
            raise ValueError("empreinte SHA-256 FIGÉ invalide")
        course_id = valid_id(item.get("course_id"))
        if item.get("moteur") != ENGINE or item.get("statut") != "FIGE_AVANT_DEPART":
            raise ValueError("moteur ou statut FIGÉ invalide")
        expected = versions.get(course_id, 0) + 1
        if type(item.get("version")) is not int or item["version"] != expected:
            raise ValueError("version FIGÉ incohérente")
        locked_at = parse_time(item.get("fige_a"))
        depart = parse_time(item.get("depart"))
        if locked_at >= depart or (course_id in previous_times and locked_at < previous_times[course_id]):
            raise ValueError("chronologie FIGÉ invalide")
        selection = item.get("selection")
        if not isinstance(selection, dict) or selection.get("course_id") != course_id or selection.get("moteur") != ENGINE:
            raise ValueError("sélection FIGÉ invalide")
        ranking = selection.get("classement")
        if not isinstance(ranking, list) or len(ranking) != 5 or any(type(n) is not int or n <= 0 for n in ranking) or len(set(ranking)) != 5:
            raise ValueError("classement FIGÉ invalide")
        if parse_time(selection.get("cree_a")) > locked_at:
            raise ValueError("sélection créée après le FIGÉ")
        versions[course_id] = expected
        previous_times[course_id] = locked_at
    return {"moteur": ENGINE, "archives_valides": len(archives)}


def select(course_id):
    course = find_course(course_id)
    if now() >= parse_time(course["depart"]):
        raise ValueError("présélection interdite après départ")
    ranked = analyze(course)
    top = ranked[:5]
    if all(h["criteres_renseignes"] == 0 for h in ranked):
        raise ValueError("aucune donnée statistique exploitable pour M1")
    stamp = now()
    if stamp >= parse_time(course["depart"]):
        raise ValueError("départ atteint pendant l'analyse")
    record = {
        "moteur": ENGINE, "course_id": course_id, "cree_a": stamp.isoformat(),
        "classement": [h["numero"] for h in top],
        "analyse": ("Classement M1 fondé sur les critères statistiques disponibles : "
                    "forme, régularité, chronos, distance, terrain, opposition et valeur cachée. "
                    "Les critères absents restent neutres et sont signalés dans les scores."),
        "pari_theorique": {"formule": "Simple placé", "numeros": [top[0]["numero"]]},
        "scores": ranked, "statut": "PRESELECTION_NON_FIGEE",
    }
    records = require_list(load("selections.json", []), "selections.json")
    records.append(record)
    save("selections.json", records)
    return record


def lock(course_id, verification_path):
    course = find_course(course_id)
    depart = parse_time(course["depart"])
    if now() >= depart:
        raise ValueError("FIGÉ interdit après départ")
    verification = read_json(verification_path)
    if not isinstance(verification, dict) or any(verification.get(flag) is not True for flag in FLAGS):
        raise ValueError("vérifications pré-FIGÉ incomplètes")
    if not isinstance(verification.get("source"), str) or not verification["source"].strip():
        raise ValueError("source de vérification obligatoire")
    for field in ("verification_a", "source_verifiee_a"):
        age = (now() - parse_time(verification.get(field))).total_seconds()
        if not 0 <= age <= 600:
            raise ValueError(f"{field} doit dater de moins de 10 minutes")
    verify_archives()
    selections = require_list(load("selections.json", []), "selections.json")
    candidates = [s for s in selections if isinstance(s, dict) and s.get("course_id") == course_id and s.get("moteur") == ENGINE]
    if not candidates:
        raise ValueError("aucune présélection M1")
    chosen = candidates[-1]
    ranking = chosen.get("classement")
    active_ids = {h["numero"] for h in active(course)}
    if (chosen.get("statut") != "PRESELECTION_NON_FIGEE" or not isinstance(ranking, list)
            or len(ranking) != 5 or any(type(n) is not int for n in ranking)
            or len(set(ranking)) != 5 or not set(ranking) <= active_ids):
        raise ValueError("classement de présélection invalide ou non-partant")
    created = parse_time(chosen.get("cree_a"))
    if created >= depart or created > now():
        raise ValueError("date de présélection invalide")
    bet = chosen.get("pari_theorique")
    if not isinstance(bet, dict) or bet.get("formule") != "Simple placé" or bet.get("numeros") != [ranking[0]]:
        raise ValueError("formule théorique incohérente")
    stamp = now()
    if stamp >= depart:
        raise ValueError("départ atteint avant FIGÉ")
    archives = require_list(load("figes.json", []), "figes.json")
    version = 1 + sum(a["course_id"] == course_id for a in archives)
    body = {"moteur": ENGINE, "course_id": course_id, "version": version,
            "statut": "FIGE_AVANT_DEPART", "fige_a": stamp.isoformat(),
            "depart": course["depart"], "selection": chosen, "verification": verification}
    archives.append({**body, "sha256": digest(body)})
    save("figes.json", archives)
    return {"moteur": ENGINE, "course_id": course_id, "version": version, "statut": body["statut"]}


def audit(course_id, arrival, source):
    course = find_course(course_id)
    if now() < parse_time(course["depart"]):
        raise ValueError("audit interdit avant départ")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source officielle obligatoire")
    try:
        actual = [int(s.strip()) for s in arrival.split(",")]
    except (ValueError, AttributeError) as exc:
        raise ValueError("arrivée attendue : 3,5,1,7,2") from exc
    known = {h["numero"] for h in course["partants"]}
    if len(actual) < 5 or len(actual) != len(set(actual)) or any(n not in known for n in actual):
        raise ValueError("arrivée invalide ou numéros inconnus")
    verify_archives()
    archives = load("figes.json", [])
    picks = [a for a in archives if a["course_id"] == course_id]
    if not picks:
        raise ValueError("aucun FIGÉ M1 pour cette course")
    pick = picks[-1]["selection"]["classement"]
    record = {"moteur": ENGINE, "course_id": course_id, "audite_a": now().isoformat(),
              "source_officielle": source, "arrivee": actual, "classement_fige": pick,
              "version_figee": picks[-1]["version"],
              "top5_detectes": len(set(pick) & set(actual[:5])),
              "ordre_exact": pick == actual[:5], "gain": None}
    audits = require_list(load("audits.json", []), "audits.json")
    if any(isinstance(a, dict) and a.get("course_id") == course_id and a.get("version_figee") == record["version_figee"] for a in audits):
        raise ValueError("audit déjà présent pour cette version FIGÉE")
    audits.append(record)
    save("audits.json", audits)
    return record


def import_json(path):
    data = require_list(read_json(path), "import")
    if len({c.get("course_id") for c in data if isinstance(c, dict)}) != len(data):
        raise ValueError("courses dupliquées dans l'import")
    for course in data:
        validate_course(course)
        if now() >= parse_time(course["depart"]):
            raise ValueError("import d'une course déjà commencée interdit")
    existing = require_list(load("courses.json", []), "courses.json")
    by_id = {}
    for course in existing:
        validate_course(course)
        course_id = course["course_id"]
        if course_id in by_id:
            raise ValueError("courses locales dupliquées")
        by_id[course_id] = course
    for course in data:
        course_id = course["course_id"]
        if course_id in by_id and by_id[course_id] != course:
            # Ne jamais changer silencieusement une course déjà sélectionnée ou FIGÉE.
            for name in ("selections.json", "figes.json", "audits.json"):
                records = require_list(load(name, []), name)
                if any(isinstance(r, dict) and r.get("course_id") == course_id for r in records):
                    raise ValueError("course déjà utilisée : modification interdite")
        by_id[course_id] = course
    save("courses.json", list(by_id.values()))
    diagnostic = {"moteur": ENGINE, "importe_a": now().isoformat(), "courses_recues": len(data)}
    save("import_diagnostic.json", diagnostic)
    return diagnostic


def self_test():
    sample = {"course_id": "2099-01-01-R1-C1", "depart": "2099-01-01T14:00:00+01:00",
              "pays": "FR", "hippodrome": "Hippodrome de test",
              "partants": [{"numero": i, "nom": f"Cheval {i}", "forme": 50 + i} for i in range(1, 7)]}
    ranked = analyze(sample)
    if len(ranked) != 6 or ranked[0]["numero"] != 6:
        raise ValueError("échec du classement M1")
    bad = dict(sample, partants=sample["partants"][:4])
    try:
        analyze(bad)
    except ValueError:
        pass
    else:
        raise ValueError("échec du contrôle des partants")
    if digest({"a": 1}) != digest({"a": 1}):
        raise ValueError("échec SHA-256")
    return {"moteur": ENGINE, "self_test": "OK", "archives": verify_archives()}


def main():
    parser = argparse.ArgumentParser(description="M1 X1000 — analyse statistique autonome")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self-test", action="store_true")
    group.add_argument("--verify-archives", action="store_true")
    group.add_argument("--import-json", metavar="FICHIER")
    group.add_argument("--select", metavar="COURSE_ID")
    group.add_argument("--lock", metavar="COURSE_ID")
    group.add_argument("--audit", metavar="COURSE_ID")
    parser.add_argument("--verification-json", metavar="FICHIER")
    parser.add_argument("--arrival", metavar="NUMEROS")
    parser.add_argument("--source-officielle")
    parser.add_argument("--resultat-verifie", action="store_true")
    args = parser.parse_args()
    try:
        if args.self_test:
            result = self_test()
        elif args.verify_archives:
            result = verify_archives()
        elif args.import_json:
            result = import_json(args.import_json)
        elif args.select:
            result = select(args.select)
        elif args.lock:
            if not args.verification_json:
                parser.error("--verification-json obligatoire avec --lock")
            result = lock(args.lock, args.verification_json)
        else:
            if not (args.arrival and args.source_officielle and args.resultat_verifie):
                parser.error("--arrival, --source-officielle et --resultat-verifie obligatoires")
            result = audit(args.audit, args.arrival, args.source_officielle)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        parser.exit(1, f"ERREUR M1 : {exc}\n")


if __name__ == "__main__":
    main()
