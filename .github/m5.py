"""M5 — X1000 Intelligence Hippique Humaine.

Analyse critique indépendante : faux favoris, valeurs cachées,
outsiders, fragilité et potentiel de surprise.

Python >= 3.10 ; Linux/Termux ; bibliothèque standard.
Aucun réseau, pari réel, FIGÉ automatique, commit ou déploiement.
Les archives historiques ne sont jamais réécrites.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

ENGINE = "M5"
ROOT = Path(__file__).resolve().parent / "data_m5"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$")
FILES = frozenset({
    "courses.json", "selections.json", "figes.json",
    "audits.json", "import_diagnostic.json"
})
WEIGHTS = {
    "valeur_cachee": .23,
    "potentiel_surprise": .17,
    "forme_sous_estimee": .15,
    "aptitude_cachee": .13,
    "regularite_cachee": .10,
    "risque_favori": .12,
    "fragilite": .10,
}
NEGATIVE = {"risque_favori", "fragilite"}
FLAGS = (
    "partants_confirmes", "np_confirmes", "horaire_confirme",
    "formule_confirmee", "source_france_confirmee"
)


def now():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("date ISO avec fuseau obligatoire")
    try:
        value = dt.datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("date ISO invalide") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("fuseau obligatoire")
    return value.astimezone(dt.timezone.utc)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(
        canonical(value).encode("utf-8")
    ).hexdigest()


def read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("lien symbolique interdit")

    def reject(value):
        raise ValueError("constante JSON interdite : " + value)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("clé JSON dupliquée : " + key)
            result[key] = value
        return result

    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject,
        object_pairs_hook=unique
    )


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

    fd, tmp = tempfile.mkstemp(prefix=".m5-", dir=ROOT)
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


@contextlib.contextmanager
def write_lock():
    """Verrou commun aux processus M5 coopératifs."""
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / ".m5-write.lock"
    if path.is_symlink():
        raise ValueError("verrou symbolique interdit")
    fd = os.open(
        path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


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
        raise ValueError("course hors de France")
    if not isinstance(course.get("hippodrome"), str) or not course["hippodrome"].strip():
        raise ValueError("hippodrome obligatoire")

    horses = as_list(course.get("partants"), "partants")
    if len(horses) < 5:
        raise ValueError("cinq partants minimum")

    seen = set()
    for horse in horses:
        if not isinstance(horse, dict):
            raise ValueError("partant invalide")
        number = horse.get("numero")
        if type(number) is not int or number <= 0 or number in seen:
            raise ValueError("numéro invalide ou dupliqué")
        seen.add(number)

        if not isinstance(horse.get("nom"), str) or not horse["nom"].strip():
            raise ValueError("nom obligatoire")
        if type(horse.get("non_partant", False)) is not bool:
            raise ValueError("non_partant invalide")

        for key in WEIGHTS:
            value = horse.get(key)
            if value is not None and (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not 0 <= value <= 100
            ):
                raise ValueError(key + " hors limites")

        cote = horse.get("cote")
        if cote is not None and (
            type(cote) not in (int, float)
            or not math.isfinite(cote)
            or cote <= 1
        ):
            raise ValueError("cote invalide")
    return depart


def active(course):
    return [
        h for h in course["partants"]
        if not h.get("non_partant", False)
    ]


def score(horse):
    total, coverage = 0.0, 0.0
    indicators = {}
    for key, weight in WEIGHTS.items():
        value = horse.get(key)
        indicators[key] = value
        if value is None:
            total += 50 * weight
        else:
            adjusted = 100 - value if key in NEGATIVE else value
            total += adjusted * weight
            coverage += weight
    return round(total, 3), round(coverage, 3), indicators


def analyze(course):
    validate_course(course)
    horses = active(course)
    if len(horses) < 5:
        raise ValueError("moins de cinq partants actifs")

    result = []
    for horse in horses:
        value, coverage, indicators = score(horse)
        cote = horse.get("cote")
        hidden = horse.get("valeur_cachee")
        risk = horse.get("risque_favori")
        signals = []

        if hidden is not None and hidden >= 70:
            signals.append("VALEUR_CACHEE")
        if cote is not None and cote >= 12 and hidden is not None and hidden >= 65:
            signals.append("OUTSIDER_A_ETUDIER")
        if cote is not None and cote <= 4 and risk is not None and risk >= 70:
            signals.append("FAVORI_FRAGILE")
        if horse.get("potentiel_surprise") is not None and horse["potentiel_surprise"] >= 75:
            signals.append("SURPRISE_POSSIBLE")

        result.append({
            "numero": horse["numero"],
            "nom": horse["nom"],
            "score": value,
            "couverture": coverage,
            "cote": cote,
            "signaux": signals,
            "indicateurs": indicators
        })

    result.sort(
        key=lambda h: (-h["score"], -h["couverture"], h["numero"])
    )
    return result


def find_course(cid):
    valid_id(cid)
    matches = [
        c for c in as_list(load("courses.json", []), "courses.json")
        if isinstance(c, dict) and c.get("course_id") == cid
    ]
    if len(matches) != 1:
        raise ValueError("course absente ou dupliquée")
    validate_course(matches[0])
    return matches[0]


def check_verification(check, stamp):
    if not isinstance(check, dict):
        raise ValueError("vérification invalide")
    if any(check.get(flag) is not True for flag in FLAGS):
        raise ValueError("contrôles pré-FIGÉ incomplets")
    if not isinstance(check.get("source"), str) or not check["source"].strip():
        raise ValueError("source obligatoire")
    for field in ("verification_a", "source_verifiee_a"):
        age = (stamp - parse_time(check.get(field))).total_seconds()
        if not 0 <= age <= 600:
            raise ValueError(field + " trop ancien ou futur")


def check_selection(selection, cid, stamp, depart, allowed=None):
    if not isinstance(selection, dict):
        raise ValueError("sélection invalide")
    if selection.get("moteur") != ENGINE or selection.get("course_id") != cid:
        raise ValueError("identité sélection invalide")
    if selection.get("statut") != "PRESELECTION_NON_FIGEE":
        raise ValueError("statut sélection invalide")

    ranking = selection.get("classement")
    if (
        not isinstance(ranking, list)
        or len(ranking) != 5
        or any(type(n) is not int or n <= 0 for n in ranking)
        or len(set(ranking)) != 5
    ):
        raise ValueError("top 5 invalide")
    if allowed is not None and not set(ranking) <= allowed:
        raise ValueError("non-partant dans le top 5")

    created = parse_time(selection.get("cree_a"))
    if created > stamp or created >= depart:
        raise ValueError("chronologie sélection invalide")

    bet = selection.get("pari_theorique")
    if not isinstance(bet, dict) or bet.get("formule") != "Simple placé" or bet.get("numeros") != [ranking[0]]:
        raise ValueError("pari théorique incohérent")
    return ranking


def verify_archives():
    archives = as_list(load("figes.json", []), "figes.json")
    versions, times, departures = {}, {}, {}

    for item in archives:
        if not isinstance(item, dict):
            raise ValueError("archive invalide")
        body = {k: v for k, v in item.items() if k != "sha256"}
        if digest(body) != item.get("sha256"):
            raise ValueError("empreinte FIGÉ invalide")

        cid = valid_id(item.get("course_id"))
        if item.get("moteur") != ENGINE or item.get("statut") != "FIGE_AVANT_DEPART":
            raise ValueError("statut ou moteur FIGÉ invalide")
        expected = versions.get(cid, 0) + 1
        if type(item.get("version")) is not int or item["version"] != expected:
            raise ValueError("version FIGÉ invalide")

        stamp = parse_time(item.get("fige_a"))
        depart = parse_time(item.get("depart"))
        if stamp >= depart or (cid in times and stamp < times[cid]):
            raise ValueError("chronologie FIGÉ invalide")
        if cid in departures and depart != departures[cid]:
            raise ValueError("horaire FIGÉ incohérent")

        check_selection(item.get("selection"), cid, stamp, depart)
        check_verification(item.get("verification"), stamp)

        versions[cid] = expected
        times[cid] = stamp
        departures[cid] = depart

    return {
        "moteur": ENGINE,
        "archives_valides": len(archives),
        "verification": "OK"
    }


def protected_ids():
    protected = set()
    for name in ("selections.json", "figes.json", "audits.json"):
        records = as_list(load(name, []), name)
        for item in records:
            if not isinstance(item, dict):
                raise ValueError(name + " contient une entrée invalide")
            cid = item.get("course_id")
            protected.add(valid_id(cid))
    return protected


def select(cid):
    with write_lock():
        course = find_course(cid)
        depart = parse_time(course["depart"])
        if now() >= depart:
            raise ValueError("présélection après départ interdite")

        ranked = analyze(course)
        if not any(h["couverture"] > 0 for h in ranked):
            raise ValueError("aucune information critique exploitable")

        stamp = now()
        ranking = [h["numero"] for h in ranked[:5]]
        record = {
            "moteur": ENGINE,
            "course_id": cid,
            "cree_a": stamp.isoformat(),
            "classement": ranking,
            "analyse": (
                "M5 examine les faux favoris, valeurs cachées, "
                "chevaux négligés, risques et surprises possibles. "
                "Les informations manquantes restent neutres."
            ),
            "pari_theorique": {
                "formule": "Simple placé",
                "numeros": [ranking[0]]
            },
            "scores": ranked,
            "statut": "PRESELECTION_NON_FIGEE"
        }

        records = as_list(load("selections.json", []), "selections.json")
        if now() >= depart:
            raise ValueError("départ atteint avant sauvegarde")
        records.append(record)
        save("selections.json", records)
        return record


def lock(cid, verification_path):
    with write_lock():
        course = find_course(cid)
        depart = parse_time(course["depart"])
        if now() >= depart:
            raise ValueError("FIGÉ après départ interdit")

        check = read_json(verification_path)
        verify_archives()
        candidates = [
            s for s in as_list(load("selections.json", []), "selections.json")
            if isinstance(s, dict)
            and s.get("course_id") == cid
            and s.get("moteur") == ENGINE
        ]
        if not candidates:
            raise ValueError("aucune présélection M5")

        chosen = candidates[-1]
        archives = as_list(load("figes.json", []), "figes.json")
        if any(
            a["course_id"] == cid and parse_time(a["depart"]) != depart
            for a in archives
        ):
            raise ValueError("horaire modifié depuis un FIGÉ")

        stamp = now()
        allowed = {h["numero"] for h in active(course)}
        check_selection(chosen, cid, stamp, depart, allowed)
        check_verification(check, stamp)
        if stamp >= depart:
            raise ValueError("départ atteint")

        version = 1 + sum(a["course_id"] == cid for a in archives)
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
        record = {**body, "sha256": digest(body)}
        if now() >= depart:
            raise ValueError("départ atteint avant sauvegarde")
        archives.append(record)
        save("figes.json", archives)
        return {
            "moteur": ENGINE,
            "course_id": cid,
            "version": version,
            "statut": "FIGE_AVANT_DEPART"
        }


def audit(cid, arrival, source):
    with write_lock():
        course = find_course(cid)
        if now() < parse_time(course["depart"]):
            raise ValueError("audit avant départ interdit")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source officielle obligatoire")
        if not isinstance(arrival, str) or not re.fullmatch(
            r"[0-9]+(?:\s*,\s*[0-9]+){4,}", arrival.strip()
        ):
            raise ValueError("arrivée attendue : 3,5,1,7,2")

        actual = [int(p.strip()) for p in arrival.split(",")]
        known = {h["numero"] for h in course["partants"]}
        if len(set(actual)) != len(actual) or not set(actual) <= known:
            raise ValueError("arrivée invalide")

        verify_archives()
        picks = [
            a for a in as_list(load("figes.json", []), "figes.json")
            if a["course_id"] == cid
        ]
        if not picks:
            raise ValueError("aucun FIGÉ M5")

        latest = picks[-1]
        ranking = latest["selection"]["classement"]
        audits = as_list(load("audits.json", []), "audits.json")
        if any(
            isinstance(a, dict)
            and a.get("course_id") == cid
            and a.get("version_figee") == latest["version"]
            for a in audits
        ):
            raise ValueError("audit déjà enregistré")

        record = {
            "moteur": ENGINE,
            "course_id": cid,
            "audite_a": now().isoformat(),
            "source_officielle": source,
            "verification_externe_automatique": False,
            "arrivee": actual,
            "classement_fige": ranking,
            "version_figee": latest["version"],
            "top5_detectes": len(set(ranking) & set(actual[:5])),
            "ordre_exact": ranking == actual[:5],
            "gain": None
        }
        audits.append(record)
        save("audits.json", audits)
        return record


def import_json(path):
    incoming = as_list(read_json(path), "import")
    with write_lock():
        for course in incoming:
            validate_course(course)
            if now() >= parse_time(course["depart"]):
                raise ValueError("import après départ interdit")

        ids = [c["course_id"] for c in incoming]
        if len(ids) != len(set(ids)):
            raise ValueError("doublons dans l'import")

        existing = as_list(load("courses.json", []), "courses.json")
        by_id = {}
        for course in existing:
            validate_course(course)
            cid = course["course_id"]
            if cid in by_id:
                raise ValueError("course locale dupliquée")
            by_id[cid] = course

        protected = protected_ids()
        verify_archives()

        for course in incoming:
            cid = course["course_id"]
            if cid in by_id and by_id[cid] != course:
                if cid in protected:
                    raise ValueError("course déjà utilisée : modification interdite")
                if now() >= parse_time(by_id[cid]["depart"]):
                    raise ValueError("course terminée : modification interdite")
            by_id[cid] = course

        if any(now() >= parse_time(c["depart"]) for c in incoming):
            raise ValueError("départ atteint pendant l'import")

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
                "valeur_cachee": 45 + i * 5,
                "potentiel_surprise": 40 + i * 5,
                "risque_favori": 20,
                "fragilite": 20,
                "cote": 2 + i * 3
            }
            for i in range(1, 7)
        ]
    }
    ranked = analyze(sample)
    if len(ranked) != 6 or ranked[0]["numero"] != 6:
        raise ValueError("échec classement M5")

    invalid_courses = [
        {**sample, "pays": "GB"},
        {**sample, "partants": sample["partants"][:4]},
        {**sample, "partants": [
            {**sample["partants"][0], "fragilite": 110},
            *sample["partants"][1:]
        ]},
        {**sample, "partants": [
            {**sample["partants"][0], "cote": float("nan")},
            *sample["partants"][1:]
        ]}
    ]
    for bad in invalid_courses:
        try:
            analyze(bad)
        except ValueError:
            pass
        else:
            raise ValueError("course invalide acceptée")

    for bad_id in ("2026-02-30-R1-C1", "2026-01-01-R0-C1"):
        try:
            valid_id(bad_id)
        except ValueError:
            pass
        else:
            raise ValueError("identifiant invalide accepté")

    return {
        "moteur": ENGINE,
        "self_test": "OK",
        "archives": verify_archives()
    }


def main():
    parser = argparse.ArgumentParser(
        description="M5 X1000 — analyse critique"
    )
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
            if not (
                args.arrival and args.source_officielle
                and args.resultat_verifie
            ):
                parser.error(
                    "--arrival, --source-officielle et "
                    "--resultat-verifie obligatoires"
                )
            result = audit(
                args.audit, args.arrival, args.source_officielle
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (
        ValueError, OSError, KeyError,
        TypeError, OverflowError
    ) as exc:
        parser.exit(1, f"ERREUR M5 : {exc}\n")


if __name__ == "__main__":
    main()
