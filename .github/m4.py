"""M4 — X1000 Intelligence Hippique Humaine.
Forme récente, ferrure, préparation et informations tardives.
Python >= 3.10, bibliothèque standard, aucun accès réseau ni pari réel.
Aucun FIGÉ, commit ou déploiement automatique.
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
from zoneinfo import ZoneInfo

ENGINE = "M4"
ROOT = Path(__file__).resolve().parent / "data_m4"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$")
FILES = frozenset({
    "courses.json", "selections.json", "figes.json",
    "audits.json", "import_diagnostic.json"
})
WEIGHTS = {
    "forme_recente": .25,
    "derniere_performance": .16,
    "fraicheur": .12,
    "preparation": .12,
    "ferrure": .12,
    "evolution_condition": .13,
    "information_tardive": .10,
}
FLAGS = (
    "partants_confirmes", "np_confirmes", "horaire_confirme",
    "formule_confirmee", "source_france_confirmee"
)


def now():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("date ISO 8601 avec fuseau obligatoire")
    try:
        parsed = dt.datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("date ISO 8601 invalide") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("fuseau horaire obligatoire")
    return parsed.astimezone(dt.timezone.utc)


def read_json(path):
    def reject(value):
        raise ValueError("constante JSON interdite : " + value)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("clé JSON dupliquée : " + key)
            result[key] = value
        return result

    if Path(path).is_symlink():
        raise ValueError("lien symbolique interdit")
    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        parse_constant=reject,
        object_pairs_hook=unique
    )


def load(name, default):
    if name not in FILES:
        raise ValueError("fichier non autorisé")
    path = ROOT / name
    return read_json(path) if path.exists() or path.is_symlink() else default


def save(name, value):
    if name not in FILES:
        raise ValueError("fichier non autorisé")
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / name
    if path.is_symlink():
        raise ValueError("lien symbolique interdit")
    fd, tmp = tempfile.mkstemp(prefix=".m4-", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(
                value, stream, ensure_ascii=False,
                indent=2, sort_keys=True, allow_nan=False
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ValueError("lien symbolique interdit")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


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

    french_date = depart.astimezone(
        ZoneInfo("Europe/Paris")
    ).date().isoformat()
    if cid[:10] != french_date:
        raise ValueError("date française incohérente")
    if course.get("pays") != "FR":
        raise ValueError("course disputée en France uniquement")
    if (
        not isinstance(course.get("hippodrome"), str)
        or not course["hippodrome"].strip()
    ):
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
        if (
            not isinstance(horse.get("nom"), str)
            or not horse["nom"].strip()
        ):
            raise ValueError("nom obligatoire")
        if type(horse.get("non_partant", False)) is not bool:
            raise ValueError("non_partant doit être booléen")
        for key in WEIGHTS:
            value = horse.get(key)
            if value is not None and (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 100
            ):
                raise ValueError(key + " doit être entre 0 et 100")
        seen.add(number)
    return depart


def active(course):
    return [
        horse for horse in course["partants"]
        if not horse.get("non_partant", False)
    ]


def score(horse):
    known = {
        key: float(horse[key])
        for key in WEIGHTS if horse.get(key) is not None
    }
    weighted = sum(
        WEIGHTS[key] * value for key, value in known.items()
    )
    coverage = sum(WEIGHTS[key] for key in known)
    return (
        round(weighted + 50 * (1 - coverage), 3),
        {key: known.get(key) for key in WEIGHTS},
        len(known)
    )


def analyze(course):
    validate_course(course)
    horses = active(course)
    if len(horses) < 5:
        raise ValueError("moins de cinq partants actifs")
    result = []
    for horse in horses:
        value, indicators, count = score(horse)
        result.append({
            "numero": horse["numero"],
            "nom": horse["nom"],
            "score": value,
            "indicateurs": indicators,
            "criteres_renseignes": count
        })
    result.sort(
        key=lambda h: (
            -h["score"], -h["criteres_renseignes"], h["numero"]
        )
    )
    return result


def find_course(cid):
    valid_id(cid)
    matches = [
        course
        for course in as_list(load("courses.json", []), "courses.json")
        if isinstance(course, dict)
        and course.get("course_id") == cid
    ]
    if len(matches) != 1:
        raise ValueError("course absente ou dupliquée")
    validate_course(matches[0])
    return matches[0]


def digest(body):
    raw = json.dumps(
        body, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_verification(check, stamp):
    if (
        not isinstance(check, dict)
        or any(check.get(flag) is not True for flag in FLAGS)
    ):
        raise ValueError("contrôles pré-FIGÉ incomplets")
    if (
        not isinstance(check.get("source"), str)
        or not check["source"].strip()
    ):
        raise ValueError("source de vérification obligatoire")
    for field in ("verification_a", "source_verifiee_a"):
        age = (stamp - parse_time(check.get(field))).total_seconds()
        if not 0 <= age <= 600:
            raise ValueError(
                field + " doit dater de moins de dix minutes"
            )


def check_selection(chosen, cid, stamp, depart, allowed=None):
    if (
        not isinstance(chosen, dict)
        or chosen.get("moteur") != ENGINE
        or chosen.get("course_id") != cid
    ):
        raise ValueError("sélection M4 invalide")
    if chosen.get("statut") != "PRESELECTION_NON_FIGEE":
        raise ValueError("statut de sélection invalide")

    ranking = chosen.get("classement")
    if (
        not isinstance(ranking, list)
        or len(ranking) != 5
        or any(type(n) is not int or n <= 0 for n in ranking)
        or len(set(ranking)) != 5
    ):
        raise ValueError("classement invalide")
    if allowed is not None and not set(ranking) <= allowed:
        raise ValueError("classement contenant un non-partant")

    created = parse_time(chosen.get("cree_a"))
    if created > stamp or created >= depart:
        raise ValueError("sélection créée après FIGÉ ou départ")

    bet = chosen.get("pari_theorique")
    if (
        not isinstance(bet, dict)
        or bet.get("formule") != "Simple placé"
        or bet.get("numeros") != [ranking[0]]
    ):
        raise ValueError("pari théorique incohérent")
    return ranking


def verify_archives():
    archives = as_list(load("figes.json", []), "figes.json")
    versions, times, departures = {}, {}, {}

    for item in archives:
        if not isinstance(item, dict):
            raise ValueError("archive invalide")
        body = {
            key: value
            for key, value in item.items()
            if key != "sha256"
        }
        if digest(body) != item.get("sha256"):
            raise ValueError("empreinte FIGÉ invalide")

        cid = valid_id(item.get("course_id"))
        if (
            item.get("moteur") != ENGINE
            or item.get("statut") != "FIGE_AVANT_DEPART"
        ):
            raise ValueError("moteur ou statut FIGÉ invalide")
        if (
            type(item.get("version")) is not int
            or item["version"] != versions.get(cid, 0) + 1
        ):
            raise ValueError("version FIGÉ invalide")

        stamp = parse_time(item.get("fige_a"))
        depart = parse_time(item.get("depart"))
        if stamp >= depart or (
            cid in times and stamp < times[cid]
        ):
            raise ValueError("chronologie FIGÉ invalide")
        if cid in departures and depart != departures[cid]:
            raise ValueError("départ incohérent entre versions")

        check_selection(
            item.get("selection"), cid, stamp, depart
        )
        check_verification(item.get("verification"), stamp)
        versions[cid] = item["version"]
        times[cid] = stamp
        departures[cid] = depart

    return {
        "moteur": ENGINE,
        "archives_valides": len(archives)
    }


def select(cid):
    course = find_course(cid)
    depart = parse_time(course["depart"])
    if now() >= depart:
        raise ValueError("présélection interdite après départ")

    ranked = analyze(course)
    if not any(h["criteres_renseignes"] for h in ranked):
        raise ValueError("aucune information M4 exploitable")

    stamp = now()
    if stamp >= depart:
        raise ValueError("départ atteint pendant l'analyse")

    ranking = [horse["numero"] for horse in ranked[:5]]
    record = {
        "moteur": ENGINE,
        "course_id": cid,
        "cree_a": stamp.isoformat(),
        "classement": ranking,
        "analyse": (
            "M4 analyse la forme récente, la dernière performance, "
            "la fraîcheur, la préparation, la ferrure, "
            "l'évolution de condition et les informations tardives. "
            "Les valeurs absentes restent neutres."
        ),
        "pari_theorique": {
            "formule": "Simple placé",
            "numeros": [ranking[0]]
        },
        "scores": ranked,
        "statut": "PRESELECTION_NON_FIGEE"
    }
    records = as_list(
        load("selections.json", []), "selections.json"
    )
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

    candidates = [
        item
        for item in as_list(
            load("selections.json", []), "selections.json"
        )
        if isinstance(item, dict)
        and item.get("course_id") == cid
        and item.get("moteur") == ENGINE
    ]
    if not candidates:
        raise ValueError("aucune présélection M4")

    chosen = candidates[-1]
    archives = as_list(
        load("figes.json", []), "figes.json"
    )
    if any(
        item.get("course_id") == cid
        and parse_time(item["depart"]) != depart
        for item in archives
    ):
        raise ValueError(
            "horaire modifié depuis un FIGÉ précédent"
        )

    stamp = now()
    check_selection(
        chosen, cid, stamp, depart,
        {horse["numero"] for horse in active(course)}
    )
    check_verification(check, stamp)

    if stamp >= depart or now() >= depart:
        raise ValueError("départ atteint avant FIGÉ")

    version = 1 + sum(
        item["course_id"] == cid for item in archives
    )
    body = {
        "moteur": ENGINE,
        "course_id": cid,
        "version": version,
        "statut": "FIGE_AVANT_DEPART",
        "fige_a": stamp.isoformat(),
        "depart": course["depart"],
        "selection": chosen,
        "verification": check
    }
    archives.append({
        **body,
        "sha256": digest(body)
    })
    if now() >= depart:
        raise ValueError(
            "départ atteint avant enregistrement FIGÉ"
        )
    save("figes.json", archives)
    return {
        "moteur": ENGINE,
        "course_id": cid,
        "version": version,
        "statut": "FIGE_AVANT_DEPART"
    }


def audit(cid, arrival, source):
    course = find_course(cid)
    if now() < parse_time(course["depart"]):
        raise ValueError("audit interdit avant départ")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source officielle obligatoire")
    if (
        not isinstance(arrival, str)
        or not re.fullmatch(
            r"[0-9]+(?:\s*,\s*[0-9]+){4,}",
            arrival.strip()
        )
    ):
        raise ValueError("arrivée attendue : 3,5,1,7,2")

    actual = [
        int(part.strip()) for part in arrival.split(",")
    ]
    known = {
        horse["numero"] for horse in course["partants"]
    }
    if (
        len(set(actual)) != len(actual)
        or any(n <= 0 or n not in known for n in actual)
    ):
        raise ValueError("arrivée invalide")

    verify_archives()
    picks = [
        item for item in as_list(
            load("figes.json", []), "figes.json"
        )
        if item["course_id"] == cid
    ]
    if not picks:
        raise ValueError("aucun FIGÉ M4 à auditer")

    latest = picks[-1]
    ranking = latest["selection"]["classement"]
    audits = as_list(
        load("audits.json", []), "audits.json"
    )
    if any(
        isinstance(item, dict)
        and item.get("course_id") == cid
        and item.get("version_figee") == latest["version"]
        for item in audits
    ):
        raise ValueError(
            "audit déjà présent pour cette version"
        )

    record = {
        "moteur": ENGINE,
        "course_id": cid,
        "audite_a": now().isoformat(),
        "source_officielle": source,
        "verification_externe_automatique": False,
        "arrivee": actual,
        "classement_fige": ranking,
        "version_figee": latest["version"],
        "top5_detectes": len(
            set(ranking) & set(actual[:5])
        ),
        "ordre_exact": ranking == actual[:5],
        "gain": None
    }
    audits.append(record)
    save("audits.json", audits)
    return record


def import_json(path):
    incoming = as_list(
        read_json(path), "import"
    )
    for course in incoming:
        validate_course(course)
        if now() >= parse_time(course["depart"]):
            raise ValueError(
                "import après départ interdit"
            )

    ids = [course["course_id"] for course in incoming]
    if len(ids) != len(set(ids)):
        raise ValueError(
            "courses dupliquées dans l'import"
        )

    existing = as_list(
        load("courses.json", []), "courses.json"
    )
    by_id = {}
    for course in existing:
        validate_course(course)
        if course["course_id"] in by_id:
            raise ValueError(
                "courses locales dupliquées"
            )
        by_id[course["course_id"]] = course

    protected = set()
    for name in (
        "selections.json", "figes.json", "audits.json"
    ):
        for item in as_list(load(name, []), name):
            if (
                isinstance(item, dict)
                and isinstance(item.get("course_id"), str)
            ):
                protected.add(item["course_id"])

    for course in incoming:
        cid = course["course_id"]
        if (
            cid in by_id
            and by_id[cid] != course
            and cid in protected
        ):
            raise ValueError(
                "modification d'une course utilisée interdite"
            )
        by_id[cid] = course

    if any(
        now() >= parse_time(course["depart"])
        for course in incoming
    ):
        raise ValueError(
            "départ atteint pendant l'import"
        )

    save("courses.json", list(by_id.values()))
    diagnostic = {
        "moteur": ENGINE,
        "importe_a": now().isoformat(),
        "courses_recues": len(incoming)
    }
    save("import_diagnostic.json", diagnostic)
    return diagnostic


def self_test():
    sample = {
        "course_id": "2099-01-01-R1-C1",
        "depart": "2099-01-01T14:00:00+01:00",
        "pays": "FR",
        "hippodrome": "Hippodrome fictif",
        "partants": [
            {
                "numero": i,
                "nom": f"Cheval {i}",
                "forme_recente": 50 + i,
                "ferrure": 60
            }
            for i in range(1, 7)
        ]
    }

    ranked = analyze(sample)
    if len(ranked) != 6 or ranked[0]["numero"] != 6:
        raise ValueError("échec classement M4")

    for invalid in (
        "2026-02-30-R1-C1",
        "2026-01-01-R0-C1"
    ):
        try:
            valid_id(invalid)
        except ValueError:
            pass
        else:
            raise ValueError(
                "identifiant invalide accepté"
            )

    bad_cases = (
        {
            **sample,
            "partants": sample["partants"][:4]
        },
        {
            **sample,
            "pays": "GB"
        },
        {
            **sample,
            "partants": [
                {
                    **sample["partants"][0],
                    "ferrure": 101
                }
            ] + sample["partants"][1:]
        }
    )
    for bad in bad_cases:
        try:
            analyze(bad)
        except ValueError:
            pass
        else:
            raise ValueError(
                "course invalide acceptée"
            )

    return {
        "moteur": ENGINE,
        "self_test": "OK",
        "archives": verify_archives()
    }


def main():
    parser = argparse.ArgumentParser(
        description="M4 X1000 — informations récentes"
    )
    group = parser.add_mutually_exclusive_group(
        required=True
    )
    group.add_argument(
        "--self-test", action="store_true"
    )
    group.add_argument(
        "--verify-archives", action="store_true"
    )
    group.add_argument(
        "--import-json", metavar="FICHIER"
    )
    group.add_argument(
        "--select", metavar="COURSE_ID"
    )
    group.add_argument(
        "--lock", metavar="COURSE_ID"
    )
    group.add_argument(
        "--audit", metavar="COURSE_ID"
    )
    parser.add_argument(
        "--verification-json", metavar="FICHIER"
    )
    parser.add_argument(
        "--arrival", metavar="NUMEROS"
    )
    parser.add_argument("--source-officielle")
    parser.add_argument(
        "--resultat-verifie", action="store_true"
    )
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
                parser.error(
                    "--verification-json obligatoire avec --lock"
                )
            result = lock(
                args.lock, args.verification_json
            )
        else:
            if not (
                args.arrival
                and args.source_officielle
                and args.resultat_verifie
            ):
                parser.error(
                    "--arrival, --source-officielle et "
                    "--resultat-verifie obligatoires"
                )
            result = audit(
                args.audit,
                args.arrival,
                args.source_officielle
            )
        print(json.dumps(
            result, ensure_ascii=False, indent=2
        ))
    except (
        ValueError, OSError, KeyError,
        TypeError, OverflowError
    ) as exc:
        parser.exit(
            1, f"ERREUR M4 : {exc}\n"
        )


if __name__ == "__main__":
    main()
