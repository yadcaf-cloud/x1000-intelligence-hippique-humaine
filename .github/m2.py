"""M2 — X1000 Intelligence Hippique Humaine.
Analyse tactique indépendante, Python >= 3.10, bibliothèque standard.
Aucun réseau, pari réel, FIGÉ automatique ni déploiement.
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

ENGINE = "M2"
ROOT = Path(__file__).resolve().parent / "data_m2"
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$")
FILES = {"courses.json", "selections.json", "figes.json", "audits.json", "import_diagnostic.json"}
WEIGHTS = {"depart": .12, "placement": .18, "rythme": .20, "strategie": .18,
           "acceleration": .14, "adaptation_parcours": .12, "risque_parcours": .06}
FLAGS = ("partants_confirmes", "np_confirmes", "horaire_confirme",
         "formule_confirmee", "source_france_confirmee")


def now():
    return dt.datetime.now(dt.timezone.utc)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("date ISO 8601 avec fuseau obligatoire")
    try:
        t = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("date ISO 8601 invalide") from exc
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("fuseau horaire obligatoire")
    return t.astimezone(dt.timezone.utc)


def read_json(path):
    return json.loads(
        Path(path).read_text(encoding="utf-8"),
        parse_constant=lambda x: (_ for _ in ()).throw(
            ValueError("constante JSON interdite: " + x)
        ),
    )


def load(name, default):
    if name not in FILES:
        raise ValueError("fichier non autorisé")
    p = ROOT / name
    if p.is_symlink():
        raise ValueError("lien symbolique interdit pour les données M2")
    return read_json(p) if p.exists() else default


def save(name, data):
    if name not in FILES:
        raise ValueError("fichier non autorisé")
    ROOT.mkdir(parents=True, exist_ok=True)
    if (ROOT / name).is_symlink():
        raise ValueError("lien symbolique interdit pour les données M2")
    fd, tmp = tempfile.mkstemp(prefix=".m2-", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data, f, ensure_ascii=False, indent=2,
                sort_keys=True, allow_nan=False
            )
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, ROOT / name)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def as_list(value, label):
    if not isinstance(value, list):
        raise ValueError(f"{label} doit être une liste")
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
    local_date = dt.datetime.fromisoformat(
        course["depart"].replace("Z", "+00:00")
    ).date()
    if cid[:10] != local_date.isoformat():
        raise ValueError("date locale et course_id incohérents")
    if course.get("pays") != "FR":
        raise ValueError("course physiquement disputée en France uniquement")
    if not isinstance(course.get("hippodrome"), str) or not course["hippodrome"].strip():
        raise ValueError("hippodrome manquant")
    horses = as_list(course.get("partants"), "partants")
    if len(horses) < 5:
        raise ValueError("cinq partants identifiés minimum")
    seen = set()
    for h in horses:
        if not isinstance(h, dict):
            raise ValueError("partant invalide")
        n = h.get("numero")
        if type(n) is not int or n <= 0 or n in seen:
            raise ValueError("numéro de partant invalide ou dupliqué")
        if not isinstance(h.get("nom"), str) or not h["nom"].strip():
            raise ValueError("nom de partant obligatoire")
        if type(h.get("non_partant", False)) is not bool:
            raise ValueError("non_partant doit être booléen")
        for key in WEIGHTS:
            v = h.get(key)
            if v is not None and (
                type(v) not in (int, float)
                or not math.isfinite(v)
                or not 0 <= v <= 100
            ):
                raise ValueError(f"{key} doit être entre 0 et 100")
        seen.add(n)
    return depart


def active(course):
    return [h for h in course["partants"] if not h.get("non_partant", False)]


def score(horse):
    known = {
        k: float(horse[k])
        for k in WEIGHTS if horse.get(k) is not None
    }
    # risque_parcours : 100 = risque maximal, donc score inversé.
    adjusted = {
        k: (100 - v if k == "risque_parcours" else v)
        for k, v in known.items()
    }
    total = sum(adjusted[k] * WEIGHTS[k] for k in adjusted)
    coverage = sum(WEIGHTS[k] for k in adjusted)
    return (
        round(total + 50 * (1 - coverage), 3),
        {k: known.get(k) for k in WEIGHTS},
        len(known),
    )


def analyze(course):
    validate_course(course)
    horses = active(course)
    if len(horses) < 5:
        raise ValueError("moins de cinq partants actifs")
    ranked = []
    for h in horses:
        value, details, count = score(h)
        ranked.append({
            "numero": h["numero"],
            "nom": h["nom"],
            "score": value,
            "indicateurs": details,
            "criteres_renseignes": count,
        })
    ranked.sort(
        key=lambda x: (
            -x["score"], -x["criteres_renseignes"], x["numero"]
        )
    )
    return ranked


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


def digest(body):
    raw = json.dumps(
        body, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_archives():
    archives = as_list(load("figes.json", []), "figes.json")
    versions, times = {}, {}
    for item in archives:
        if not isinstance(item, dict):
            raise ValueError("archive invalide")
        body = {k: v for k, v in item.items() if k != "sha256"}
        if digest(body) != item.get("sha256"):
            raise ValueError("empreinte FIGÉ invalide")
        cid = valid_id(item.get("course_id"))
        if item.get("moteur") != ENGINE or item.get("statut") != "FIGE_AVANT_DEPART":
            raise ValueError("moteur ou statut FIGÉ invalide")
        if (
            type(item.get("version")) is not int
            or item["version"] != versions.get(cid, 0) + 1
        ):
            raise ValueError("version FIGÉ incohérente")
        stamp = parse_time(item.get("fige_a"))
        depart = parse_time(item.get("depart"))
        if stamp >= depart or (
            cid in times and stamp < times[cid]
        ):
            raise ValueError("chronologie FIGÉ invalide")
        chosen = item.get("selection")
        if (
            not isinstance(chosen, dict)
            or chosen.get("moteur") != ENGINE
            or chosen.get("course_id") != cid
        ):
            raise ValueError("sélection FIGÉ invalide")
        ranking = chosen.get("classement")
        if (
            not isinstance(ranking, list)
            or len(ranking) != 5
            or any(type(n) is not int or n <= 0 for n in ranking)
            or len(set(ranking)) != 5
        ):
            raise ValueError("classement FIGÉ invalide")
        created = parse_time(chosen.get("cree_a"))
        if created > stamp or created >= depart:
            raise ValueError("sélection postérieure au FIGÉ ou au départ")
        if chosen.get("statut") != "PRESELECTION_NON_FIGEE":
            raise ValueError("statut de sélection FIGÉ invalide")
        verification = item.get("verification")
        if (
            not isinstance(verification, dict)
            or any(verification.get(f) is not True for f in FLAGS)
        ):
            raise ValueError("contrôles de FIGÉ archivés incomplets")
        versions[cid], times[cid] = item["version"], stamp
    return {"moteur": ENGINE, "archives_valides": len(archives)}


def select(cid):
    course = find_course(cid)
    depart = parse_time(course["depart"])
    if now() >= depart:
        raise ValueError("présélection interdite après départ")
    ranked = analyze(course)
    if all(h["criteres_renseignes"] == 0 for h in ranked):
        raise ValueError("aucune information tactique exploitable")
    stamp = now()
    if stamp >= depart:
        raise ValueError("départ atteint pendant l'analyse")
    ranking = [h["numero"] for h in ranked[:5]]
    record = {
        "moteur": ENGINE,
        "course_id": cid,
        "cree_a": stamp.isoformat(),
        "classement": ranking,
        "analyse": (
            "M2 étudie le départ, le placement, le rythme, la stratégie, "
            "l'accélération, l'adaptation au parcours et les risques tactiques. "
            "Les critères manquants sont neutres et identifiables."
        ),
        "pari_theorique": {
            "formule": "Simple placé",
            "numeros": [ranking[0]],
        },
        "scores": ranked,
        "statut": "PRESELECTION_NON_FIGEE",
    }
    selections = as_list(load("selections.json", []), "selections.json")
    if now() >= depart:
        raise ValueError("départ atteint avant enregistrement")
    selections.append(record)
    save("selections.json", selections)
    return record


def lock(cid, verification_path):
    course = find_course(cid)
    depart = parse_time(course["depart"])
    if now() >= depart:
        raise ValueError("FIGÉ interdit après départ")
    check = read_json(verification_path)
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
        age = (now() - parse_time(check.get(field))).total_seconds()
        if not 0 <= age <= 600:
            raise ValueError(
                f"{field} doit dater de moins de dix minutes"
            )
    verify_archives()
    selections = as_list(load("selections.json", []), "selections.json")
    candidates = [
        s for s in selections
        if isinstance(s, dict)
        and s.get("course_id") == cid
        and s.get("moteur") == ENGINE
    ]
    if not candidates:
        raise ValueError("aucune présélection M2")
    chosen = candidates[-1]
    ranking = chosen.get("classement")
    active_ids = {h["numero"] for h in active(course)}
    if (
        chosen.get("statut") != "PRESELECTION_NON_FIGEE"
        or not isinstance(ranking, list)
        or len(ranking) != 5
        or any(type(n) is not int for n in ranking)
        or len(set(ranking)) != 5
        or not set(ranking) <= active_ids
    ):
        raise ValueError("classement invalide ou non-partant")
    created = parse_time(chosen.get("cree_a"))
    if created >= depart or created > now():
        raise ValueError("date de présélection invalide")
    bet = chosen.get("pari_theorique")
    if (
        not isinstance(bet, dict)
        or bet.get("formule") != "Simple placé"
        or bet.get("numeros") != [ranking[0]]
    ):
        raise ValueError("pari théorique incohérent")
    stamp = now()
    if stamp >= depart:
        raise ValueError("départ atteint avant FIGÉ")
    archives = as_list(load("figes.json", []), "figes.json")
    version = 1 + sum(a["course_id"] == cid for a in archives)
    body = {
        "moteur": ENGINE,
        "course_id": cid,
        "version": version,
        "statut": "FIGE_AVANT_DEPART",
        "fige_a": stamp.isoformat(),
        "depart": course["depart"],
        "selection": chosen,
        "verification": check,
    }
    if now() >= depart:
        raise ValueError("départ atteint avant enregistrement du FIGÉ")
    archives.append({**body, "sha256": digest(body)})
    save("figes.json", archives)
    return {
        "moteur": ENGINE,
        "course_id": cid,
        "version": version,
        "statut": body["statut"],
    }


def audit(cid, arrival, source):
    course = find_course(cid)
    if now() < parse_time(course["depart"]):
        raise ValueError("audit interdit avant départ")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source officielle obligatoire")
    try:
        actual = [int(s.strip()) for s in arrival.split(",")]
    except (ValueError, AttributeError) as exc:
        raise ValueError("arrivée attendue : 3,5,1,7,2") from exc
    known = {h["numero"] for h in course["partants"]}
    if (
        len(actual) < 5
        or len(set(actual)) != len(actual)
        or any(n <= 0 or n not in known for n in actual)
    ):
        raise ValueError("arrivée invalide")
    verify_archives()
    picks = [
        a for a in load("figes.json", [])
        if a["course_id"] == cid
    ]
    if not picks:
        raise ValueError("aucun FIGÉ à auditer")
    latest = picks[-1]
    ranking = latest["selection"]["classement"]
    record = {
        "moteur": ENGINE,
        "course_id": cid,
        "audite_a": now().isoformat(),
        "source_officielle": source,
        "arrivee": actual,
        "classement_fige": ranking,
        "version_figee": latest["version"],
        "top5_detectes": len(set(ranking) & set(actual[:5])),
        "ordre_exact": ranking == actual[:5],
        "gain": None,
    }
    audits = as_list(load("audits.json", []), "audits.json")
    if any(
        isinstance(a, dict)
        and a.get("course_id") == cid
        and a.get("version_figee") == latest["version"]
        for a in audits
    ):
        raise ValueError("audit déjà présent pour cette version")
    audits.append(record)
    save("audits.json", audits)
    return record


def import_json(path):
    incoming = as_list(read_json(path), "import")
    for c in incoming:
        validate_course(c)
        if now() >= parse_time(c["depart"]):
            raise ValueError("import d'une course commencée interdit")
    ids = [c["course_id"] for c in incoming]
    if len(ids) != len(set(ids)):
        raise ValueError("courses dupliquées dans l'import")
    existing = as_list(load("courses.json", []), "courses.json")
    by_id = {}
    for c in existing:
        validate_course(c)
        if c["course_id"] in by_id:
            raise ValueError("courses locales dupliquées")
        by_id[c["course_id"]] = c
    for c in incoming:
        cid = c["course_id"]
        if cid in by_id and by_id[cid] != c:
            for name in (
                "selections.json", "figes.json", "audits.json"
            ):
                records = as_list(load(name, []), name)
                if any(
                    isinstance(r, dict)
                    and r.get("course_id") == cid
                    for r in records
                ):
                    raise ValueError(
                        "modification d'une course déjà utilisée interdite"
                    )
        by_id[cid] = c
    if any(
        now() >= parse_time(c["depart"]) for c in incoming
    ):
        raise ValueError("départ atteint pendant l'import")
    save("courses.json", list(by_id.values()))
    result = {
        "moteur": ENGINE,
        "importe_a": now().isoformat(),
        "courses_recues": len(incoming),
    }
    save("import_diagnostic.json", result)
    return result


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
                "rythme": 50 + i,
                "risque_parcours": 20,
            }
            for i in range(1, 7)
        ],
    }
    ranked = analyze(sample)
    if len(ranked) != 6 or ranked[0]["numero"] != 6:
        raise ValueError("échec classement tactique")
    try:
        analyze({
            **sample,
            "partants": sample["partants"][:4],
        })
    except ValueError:
        pass
    else:
        raise ValueError("échec contrôle partants")
    if score({"risque_parcours": 100})[0] >= score({
        "risque_parcours": 0
    })[0]:
        raise ValueError("échec inversion risque")
    for invalid in (
        "2026-02-30-R1-C1",
        "2026-01-01-R0-C1",
    ):
        try:
            valid_id(invalid)
        except ValueError:
            pass
        else:
            raise ValueError("identifiant invalide accepté")
    return {
        "moteur": ENGINE,
        "self_test": "OK",
        "archives": verify_archives(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="M2 X1000 — tactique hippique autonome"
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
                parser.error(
                    "--verification-json obligatoire avec --lock"
                )
            result = lock(args.lock, args.verification_json)
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
                args.audit, args.arrival, args.source_officielle
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (
        ValueError, OSError, KeyError, TypeError, OverflowError
    ) as exc:
        parser.exit(1, f"ERREUR M2 : {exc}\n")


if __name__ == "__main__":
    main()
