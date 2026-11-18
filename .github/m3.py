"""M3 — X1000 Intelligence Hippique Humaine.
Profils, aptitudes, jockeys et entraîneurs. Python >= 3.10.
Bibliothèque standard. Aucun réseau, pari réel ou FIGÉ automatique.
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

ENGINE = "M3"
ROOT = Path(__file__).resolve().parent / "data_m3"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$")
FILES = frozenset({"courses.json", "selections.json", "figes.json", "audits.json", "import_diagnostic.json"})
WEIGHTS = {"aptitude_distance": .23, "aptitude_terrain": .18, "aptitude_corde": .12,
           "jockey": .16, "entraineur": .14, "association": .10, "profil_course": .07}
FLAGS = ("partants_confirmes", "np_confirmes", "horaire_confirme",
         "formule_confirmee", "source_france_confirmee")


def now():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("date ISO 8601 avec fuseau obligatoire")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("date ISO 8601 invalide") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("fuseau horaire obligatoire")
    return result.astimezone(dt.timezone.utc)


def read_json(path):
    def reject(value):
        raise ValueError("constante JSON interdite : " + value)
    def no_duplicates(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("clé JSON dupliquée : " + key)
            obj[key] = value
        return obj
    return json.loads(Path(path).read_text(encoding="utf-8"),
                      parse_constant=reject, object_pairs_hook=no_duplicates)


def load(name, default):
    if name not in FILES:
        raise ValueError("fichier non autorisé")
    path = ROOT / name
    if path.is_symlink():
        raise ValueError("lien symbolique interdit")
    return read_json(path) if path.exists() else default


def save(name, value):
    if name not in FILES:
        raise ValueError("fichier non autorisé")
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / name
    if path.is_symlink():
        raise ValueError("lien symbolique interdit")
    fd, temp = tempfile.mkstemp(prefix=".m3-", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True,
                      indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def as_list(value, label):
    if not isinstance(value, list):
        raise ValueError(label + " doit être une liste")
    return value


def valid_id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError("course_id invalide")
    try:
        dt.date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError("date course_id invalide") from exc
    return value


def validate_course(course):
    if not isinstance(course, dict):
        raise ValueError("course invalide")
    cid = valid_id(course.get("course_id"))
    depart = parse_time(course.get("depart"))
    if cid[:10] != dt.datetime.fromisoformat(course["depart"].replace("Z", "+00:00")).date().isoformat():
        raise ValueError("date locale incohérente")
    if course.get("pays") != "FR":
        raise ValueError("course physiquement disputée en France uniquement")
    if not isinstance(course.get("hippodrome"), str) or not course["hippodrome"].strip():
        raise ValueError("hippodrome obligatoire")
    horses = as_list(course.get("partants"), "partants")
    if len(horses) < 5:
        raise ValueError("au moins cinq partants identifiés")
    seen = set()
    for horse in horses:
        if not isinstance(horse, dict):
            raise ValueError("partant invalide")
        number = horse.get("numero")
        if type(number) is not int or number <= 0 or number in seen:
            raise ValueError("numéro invalide ou dupliqué")
        if not isinstance(horse.get("nom"), str) or not horse["nom"].strip():
            raise ValueError("nom de cheval obligatoire")
        if type(horse.get("non_partant", False)) is not bool:
            raise ValueError("non_partant doit être booléen")
        for key in WEIGHTS:
            value = horse.get(key)
            if value is not None and (type(value) not in (int, float) or
                                      not math.isfinite(value) or not 0 <= value <= 100):
                raise ValueError(key + " doit être entre 0 et 100")
        seen.add(number)
    return depart


def active(course):
    return [horse for horse in course["partants"] if not horse.get("non_partant", False)]


def score(horse):
    known = {key: float(horse[key]) for key in WEIGHTS if horse.get(key) is not None}
    value = sum(known[key] * WEIGHTS[key] for key in known)
    coverage = sum(WEIGHTS[key] for key in known)
    return round(value + 50 * (1 - coverage), 3), {key: known.get(key) for key in WEIGHTS}, len(known)


def analyze(course):
    validate_course(course)
    horses = active(course)
    if len(horses) < 5:
        raise ValueError("moins de cinq partants actifs")
    ranked = []
    for horse in horses:
        value, indicators, count = score(horse)
        ranked.append({"numero": horse["numero"], "nom": horse["nom"], "score": value,
                       "indicateurs": indicators, "criteres_renseignes": count})
    ranked.sort(key=lambda horse: (-horse["score"], -horse["criteres_renseignes"], horse["numero"]))
    return ranked


def find_course(cid):
    valid_id(cid)
    matches = [course for course in as_list(load("courses.json", []), "courses.json")
               if isinstance(course, dict) and course.get("course_id") == cid]
    if len(matches) != 1:
        raise ValueError("course absente ou dupliquée")
    validate_course(matches[0])
    return matches[0]


def digest(body):
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_verification(check, stamp):
    if not isinstance(check, dict) or any(check.get(flag) is not True for flag in FLAGS):
        raise ValueError("contrôles pré-FIGÉ incomplets")
    if not isinstance(check.get("source"), str) or not check["source"].strip():
        raise ValueError("source de vérification obligatoire")
    for field in ("verification_a", "source_verifiee_a"):
        age = (stamp - parse_time(check.get(field))).total_seconds()
        if not 0 <= age <= 600:
            raise ValueError(field + " doit dater de moins de dix minutes")


def verify_archives():
    archives = as_list(load("figes.json", []), "figes.json")
    versions, times = {}, {}
    for item in archives:
        if not isinstance(item, dict):
            raise ValueError("archive invalide")
        body = {key: value for key, value in item.items() if key != "sha256"}
        if digest(body) != item.get("sha256"):
            raise ValueError("empreinte FIGÉ invalide")
        cid = valid_id(item.get("course_id"))
        if item.get("moteur") != ENGINE or item.get("statut") != "FIGE_AVANT_DEPART":
            raise ValueError("moteur ou statut FIGÉ invalide")
        if type(item.get("version")) is not int or item["version"] != versions.get(cid, 0) + 1:
            raise ValueError("version FIGÉ invalide")
        stamp, depart = parse_time(item.get("fige_a")), parse_time(item.get("depart"))
        if stamp >= depart or (cid in times and stamp < times[cid]):
            raise ValueError("chronologie FIGÉ invalide")
        chosen = item.get("selection")
        if not isinstance(chosen, dict) or chosen.get("moteur") != ENGINE or chosen.get("course_id") != cid:
            raise ValueError("sélection FIGÉ invalide")
        ranking = chosen.get("classement")
        if (not isinstance(ranking, list) or len(ranking) != 5 or
                any(type(n) is not int or n <= 0 for n in ranking) or len(set(ranking)) != 5):
            raise ValueError("classement FIGÉ invalide")
        created = parse_time(chosen.get("cree_a"))
        if created > stamp or created >= depart or chosen.get("statut") != "PRESELECTION_NON_FIGEE":
            raise ValueError("sélection FIGÉ incohérente")
        check_verification(item.get("verification"), stamp)
        versions[cid], times[cid] = item["version"], stamp
    return {"moteur": ENGINE, "archives_valides": len(archives)}


def select(cid):
    course = find_course(cid)
    depart = parse_time(course["depart"])
    if now() >= depart:
        raise ValueError("présélection interdite après départ")
    ranked = analyze(course)
    if not any(horse["criteres_renseignes"] for horse in ranked):
        raise ValueError("aucune donnée M3 exploitable")
    stamp = now()
    if stamp >= depart:
        raise ValueError("départ atteint pendant l'analyse")
    ranking = [horse["numero"] for horse in ranked[:5]]
    record = {"moteur": ENGINE, "course_id": cid, "cree_a": stamp.isoformat(),
              "classement": ranking,
              "analyse": ("M3 examine les aptitudes à la distance, au terrain et à la corde, "
                          "le jockey, l'entraîneur, l'association et le profil de course. "
                          "Les critères absents restent neutres et sont identifiés."),
              "pari_theorique": {"formule": "Simple placé", "numeros": [ranking[0]]},
              "scores": ranked, "statut": "PRESELECTION_NON_FIGEE"}
    records = as_list(load("selections.json", []), "selections.json")
    if now() >= depart:
        raise ValueError("départ atteint avant enregistrement")
    records.append(record)
    save("selections.json", records)
    return record


def lock(cid, verification_path):
    course = find_course(cid)
    depart = parse_time(course["depart"])
    if now() >= depart:
        raise ValueError("FIGÉ interdit après départ")
    check = read_json(verification_path)
    check_verification(check, now())
    verify_archives()
    candidates = [item for item in as_list(load("selections.json", []), "selections.json")
                  if isinstance(item, dict) and item.get("course_id") == cid and item.get("moteur") == ENGINE]
    if not candidates:
        raise ValueError("aucune présélection M3")
    chosen = candidates[-1]
    ranking = chosen.get("classement")
    active_ids = {horse["numero"] for horse in active(course)}
    if (chosen.get("statut") != "PRESELECTION_NON_FIGEE" or not isinstance(ranking, list)
            or len(ranking) != 5 or any(type(n) is not int for n in ranking)
            or len(set(ranking)) != 5 or not set(ranking) <= active_ids):
        raise ValueError("classement invalide ou non-partant")
    created = parse_time(chosen.get("cree_a"))
    if created >= depart or created > now():
        raise ValueError("date de sélection invalide")
    bet = chosen.get("pari_theorique")
    if not isinstance(bet, dict) or bet.get("formule") != "Simple placé" or bet.get("numeros") != [ranking[0]]:
        raise ValueError("pari théorique incohérent")
    archives = as_list(load("figes.json", []), "figes.json")
    stamp = now()
    if stamp >= depart:
        raise ValueError("départ atteint avant FIGÉ")
    check_verification(check, stamp)
    version = 1 + sum(item["course_id"] == cid for item in archives)
    body = {"moteur": ENGINE, "course_id": cid, "version": version,
            "statut": "FIGE_AVANT_DEPART", "fige_a": stamp.isoformat(),
            "depart": course["depart"], "selection": chosen, "verification": check}
    if now() >= depart:
        raise ValueError("départ atteint avant enregistrement FIGÉ")
    archives.append({**body, "sha256": digest(body)})
    save("figes.json", archives)
    return {"moteur": ENGINE, "course_id": cid, "version": version, "statut": body["statut"]}


def audit(cid, arrival, source):
    course = find_course(cid)
    if now() < parse_time(course["depart"]):
        raise ValueError("audit interdit avant départ")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source officielle obligatoire")
    try:
        actual = [int(part.strip()) for part in arrival.split(",")]
    except (ValueError, AttributeError) as exc:
        raise ValueError("arrivée attendue : 3,5,1,7,2") from exc
    known = {horse["numero"] for horse in course["partants"]}
    if len(actual) < 5 or len(set(actual)) != len(actual) or any(n <= 0 or n not in known for n in actual):
        raise ValueError("arrivée invalide")
    verify_archives()
    picks = [item for item in load("figes.json", []) if item["course_id"] == cid]
    if not picks:
        raise ValueError("aucun FIGÉ M3 à auditer")
    latest = picks[-1]
    ranking = latest["selection"]["classement"]
    record = {"moteur": ENGINE, "course_id": cid, "audite_a": now().isoformat(),
              "source_officielle": source, "verification_externe_automatique": False,
              "arrivee": actual, "classement_fige": ranking, "version_figee": latest["version"],
              "top5_detectes": len(set(ranking) & set(actual[:5])),
              "ordre_exact": ranking == actual[:5], "gain": None}
    audits = as_list(load("audits.json", []), "audits.json")
    if any(isinstance(item, dict) and item.get("course_id") == cid and
           item.get("version_figee") == latest["version"] for item in audits):
        raise ValueError("audit déjà présent pour cette version")
    audits.append(record)
    save("audits.json", audits)
    return record


def import_json(path):
    incoming = as_list(read_json(path), "import")
    for course in incoming:
        validate_course(course)
        if now() >= parse_time(course["depart"]):
            raise ValueError("import après départ interdit")
    ids = [course["course_id"] for course in incoming]
    if len(set(ids)) != len(ids):
        raise ValueError("courses dupliquées dans l'import")
    existing = as_list(load("courses.json", []), "courses.json")
    by_id = {}
    for course in existing:
        validate_course(course)
        if course["course_id"] in by_id:
            raise ValueError("courses locales dupliquées")
        by_id[course["course_id"]] = course
    protected = set()
    for name in ("selections.json", "figes.json", "audits.json"):
        for item in as_list(load(name, []), name):
            if isinstance(item, dict) and isinstance(item.get("course_id"), str):
                protected.add(item["course_id"])
    for course in incoming:
        cid = course["course_id"]
        if cid in by_id and by_id[cid] != course and cid in protected:
            raise ValueError("modification d'une course utilisée interdite")
        by_id[cid] = course
    if any(now() >= parse_time(course["depart"]) for course in incoming):
        raise ValueError("départ atteint pendant import")
    save("courses.json", list(by_id.values()))
    diagnostic = {"moteur": ENGINE, "importe_a": now().isoformat(), "courses_recues": len(incoming)}
    save("import_diagnostic.json", diagnostic)
    return diagnostic


def self_test():
    sample = {"course_id": "2099-01-01-R1-C1", "depart": "2099-01-01T14:00:00+01:00",
              "pays": "FR", "hippodrome": "Hippodrome fictif",
              "partants": [{"numero": i, "nom": f"Cheval {i}", "aptitude_distance": 50 + i,
                            "jockey": 60} for i in range(1, 7)]}
    ranked = analyze(sample)
    if len(ranked) != 6 or ranked[0]["numero"] != 6:
        raise ValueError("échec classement M3")
    for invalid in ("2026-02-30-R1-C1", "2026-01-01-R0-C1"):
        try:
            valid_id(invalid)
        except ValueError:
            pass
        else:
            raise ValueError("identifiant invalide accepté")
    for bad in ({**sample, "partants": sample["partants"][:4]},
                {**sample, "pays": "GB"},
                {**sample, "partants": [{**sample["partants"][0], "aptitude_distance": 101}] + sample["partants"][1:]}):
        try:
            analyze(bad)
        except ValueError:
            pass
        else:
            raise ValueError("course invalide acceptée")
    return {"moteur": ENGINE, "self_test": "OK", "archives": verify_archives()}


def main():
    parser = argparse.ArgumentParser(description="M3 X1000 — profils et aptitudes")
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
        parser.exit(1, f"ERREUR M3 : {exc}\n")


if __name__ == "__main__":
    main()
