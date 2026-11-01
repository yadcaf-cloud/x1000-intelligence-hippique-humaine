#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 — Collecteur PMU sécurisé 2.1.

Ne collecte que les courses dont le pays est explicitement confirmé FR.
Ne crée ni pronostic, ni résultat, ni FIGÉ. Bibliothèque standard Python 3.10+.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

VERSION = "X1000-PMU-SECURISE-2.1"
PARIS = ZoneInfo("Europe/Paris")
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BASE = os.environ.get("PMU_API_BASE", "https://online.turfinfo.api.pmu.fr/rest/client/7").rstrip("/")
TIMEOUT = 15
RETRIES = 3
MAX_BYTES = 12_000_000
RETRYABLE = {408, 429, 500, 502, 503, 504}


def now():
    return dt.datetime.now(PARIS).isoformat(timespec="seconds")


def text(value):
    return str(value).strip() if value is not None and not isinstance(value, (dict, list, bool)) else ""


def integer(value):
    if isinstance(value, bool):
        return None
    try:
        s = text(value)
        return int(s) if re.fullmatch(r"[+-]?\d+", s) else None
    except (ValueError, OverflowError):
        return None


def number(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(text(value).replace(",", "."))
        return n if math.isfinite(n) else None
    except (ValueError, OverflowError):
        return None


def first(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        if obj.get(key) is not None:
            return obj[key]
    return None


def array(obj, *keys):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in keys:
            if isinstance(obj.get(key), list):
                return obj[key]
    return []


def label(value):
    if isinstance(value, dict):
        value = first(value, "libelle", "nom", "name", "code")
    return text(value)


def validate_base():
    p = urlsplit(BASE)
    if (p.scheme != "https" or not p.hostname or p.username or p.password
            or p.query or p.fragment or p.port not in (None, 443)):
        raise ValueError("PMU_API_BASE doit etre une URL HTTPS valide sans identifiants")


def request_json(path):
    validate_base()
    if not re.fullmatch(r"[A-Za-z0-9/_-]+", path):
        raise ValueError("Chemin API invalide")
    url = BASE + "/" + path.lstrip("/")
    last = None
    for attempt in range(RETRIES):
        try:
            req = Request(url, headers={"User-Agent": "X1000-PMU/2.1", "Accept": "application/json"})
            with urlopen(req, timeout=TIMEOUT) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError("Reponse API trop volumineuse")
                obj = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(obj, (dict, list)):
                    raise ValueError("Structure JSON inattendue")
                return obj
        except HTTPError as exc:
            last = exc
            if exc.code not in RETRYABLE:
                break
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
        if attempt < RETRIES - 1:
            time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.2))
    raise RuntimeError(f"Echec API {path}: {type(last).__name__}: {last}")


def france_verified(reunion):
    country = first(reunion, "codePays", "pays", "countryCode", "country")
    if isinstance(country, dict):
        country = first(country, "code", "codePays", "nom", "libelle")
    normalized = unicodedata.normalize("NFKD", text(country)).encode("ascii", "ignore").decode().upper()
    return normalized in {"FR", "FRA", "FRANCE", "FRANCAIS", "FRANCAISE"}


def departure(value, date):
    if value is None or isinstance(value, bool):
        return None
    try:
        s = text(value)
        if re.fullmatch(r"\d{10}|\d{13}", s):
            stamp = int(s) / (1000 if len(s) == 13 else 1)
            d = dt.datetime.fromtimestamp(stamp, dt.timezone.utc)
        elif re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", s):
            d = dt.datetime.fromisoformat(date + "T" + s).replace(tzinfo=PARIS)
        else:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=PARIS)
        return d.astimezone(PARIS).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


def non_starter(item):
    flag = first(item, "nonPartant", "non_partant")
    if flag is True or flag == 1 or (isinstance(flag, str) and flag.lower().strip() in {"true", "oui", "yes", "1"}):
        return True
    status = re.sub(r"[\s-]+", "_", text(first(item, "statut", "statutParticipant", "status")).upper())
    return status in {"NP", "NON_PARTANT", "NONPARTANT"}


def participant(item):
    if not isinstance(item, dict):
        return None
    num = integer(first(item, "numPmu", "numero", "num", "numeroPmu"))
    name = text(first(item, "nom", "nomCheval", "name"))
    if num is None or num < 1 or not name:
        return None
    np = non_starter(item)
    return {
        "numero": num, "nom": name, "non_partant": np,
        "statut": "NON_PARTANT" if np else "PARTANT",
        "jockey_driver": label(first(item, "driver", "jockey", "nomJockey", "nomDriver")) or None,
        "entraineur": label(first(item, "entraineur", "nomEntraineur")) or None,
        "musique": text(first(item, "musique", "musiqueCheval")) or None,
        "age": integer(item.get("age")), "sexe": text(item.get("sexe")) or None,
        "gains": number(first(item, "gainsParticipant", "gains", "gain")), "source": "PMU"
    }


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".x1000-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def report(date, courses, errors, warnings, status):
    return {
        "version": VERSION, "date": date, "collecte_a": now(), "source": BASE,
        "statut": status, "courses": courses, "erreurs": errors,
        "avertissements": warnings,
        "statistiques": {
            "courses": len(courses),
            "partants": sum(c["nombre_partants"] for c in courses),
            "non_partants": sum(len(c["non_partants"]) for c in courses)
        }
    }


def collect(date):
    ymd = date.replace("-", "")
    errors, warnings, courses, seen = [], [], [], set()
    try:
        programme = request_json(f"programme/{ymd}")
    except (RuntimeError, ValueError) as exc:
        return report(date, [], [str(exc)], [], "ECHEC")
    reunions = array(programme, "reunions", "réunions")
    if not reunions:
        return report(date, [], ["Programme sans liste de reunions exploitable"], [], "ECHEC")
    for reunion in reunions:
        if not isinstance(reunion, dict):
            errors.append("Reunion de format invalide")
            continue
        nr = integer(first(reunion, "numOfficiel", "numReunion", "numero"))
        if nr is None or nr < 1:
            errors.append("Reunion sans numero valide")
            continue
        if not france_verified(reunion):
            warnings.append(f"R{nr}: pays FR non confirme, reunion exclue")
            continue
        hippodrome = label(first(reunion, "hippodrome", "nomHippodrome"))
        race_list = array(reunion, "courses")
        if not race_list:
            warnings.append(f"R{nr}: aucune course dans le programme")
        for course in race_list:
            if not isinstance(course, dict):
                errors.append(f"R{nr}: course de format invalide")
                continue
            nc = integer(first(course, "numOrdre", "numCourse", "numero"))
            if nc is None or nc < 1:
                errors.append(f"R{nr}: course sans numero valide")
                continue
            cid = f"{date}-R{nr}-C{nc}"
            if cid in seen:
                errors.append(f"{cid}: doublon de course")
                continue
            seen.add(cid)
            try:
                details = request_json(f"programme/{ymd}/R{nr}/C{nc}/participants")
            except (RuntimeError, ValueError) as exc:
                errors.append(f"{cid}: {exc}")
                continue
            raw = array(details, "participants", "partants")
            if not raw:
                errors.append(f"{cid}: participants absents ou format non reconnu")
                continue
            by_num, invalid = {}, False
            for item in raw:
                p = participant(item)
                if p is None:
                    invalid = True
                    continue
                num = p["numero"]
                if num in by_num and by_num[num] != p:
                    invalid = True
                    continue
                by_num[num] = p
            if invalid or not by_num:
                errors.append(f"{cid}: participant invalide ou conflit de numero")
                continue
            people = [by_num[k] for k in sorted(by_num)]
            np = [{"numero": p["numero"], "nom": p["nom"]} for p in people if p["non_partant"]]
            start = departure(first(course, "heureDepart", "depart", "heureDepartPrevue"), date)
            courses.append({
                "id": cid, "reunion": nr, "course": nc, "hippodrome": hippodrome,
                "localisation_france_verifiee": True,
                "nom": label(first(course, "libelle", "nom", "libelleCourse")),
                "depart": start, "depart_verifie": start is not None,
                "nombre_participants": len(people), "nombre_partants": len(people) - len(np),
                "non_partants": np, "partants": people,
                "source": "PMU", "collecte_a": now(), "statut": "PARTANTS_CHARGES"
            })
    courses.sort(key=lambda c: (c["depart"] or "9999", c["id"]))
    status = "OK" if courses and not errors and not warnings else ("PARTIEL" if courses else ("ECHEC" if errors else "AUCUNE_COURSE"))
    return report(date, courses, errors, warnings, status)


def can_publish(new, path):
    if not new["courses"]:
        return False, "Collecte vide"
    # Toute erreur de collecte rend le jeu potentiellement incomplet.
    if new["erreurs"] or new["statut"] not in {"OK", "PARTIEL"}:
        return False, "Collecte avec erreurs ou statut non publiable"
    if not path.exists():
        return True, "Premiere publication"
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "Publication existante illisible"
    if not isinstance(old, dict) or not isinstance(old.get("courses"), list):
        return False, "Publication existante de format inconnu"
    old_date = text(old.get("date"))
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", old_date):
        return False, "Date de publication existante invalide"
    if old_date > new["date"]:
        return False, "Refus de remplacer une publication plus recente"
    if old_date < new["date"]:
        return True, "Nouvelle journee"
    old_by_id = {}
    for c in old["courses"]:
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("partants"), list):
            return False, "Ancienne publication non verifiable"
        if c["id"] in old_by_id:
            return False, "Doublons dans ancienne publication"
        old_by_id[c["id"]] = c
    new_by_id = {c["id"]: c for c in new["courses"]}
    if not old_by_id.keys() <= new_by_id.keys():
        return False, "Courses precedentes absentes"
    for cid, old_course in old_by_id.items():
        old_nums = set()
        for p in old_course["partants"]:
            if not isinstance(p, dict) or integer(p.get("numero")) is None:
                return False, f"{cid}: ancienne liste invalide"
            old_nums.add(integer(p["numero"]))
        new_nums = {p["numero"] for p in new_by_id[cid]["partants"]}
        if not old_nums <= new_nums:
            return False, f"{cid}: participants precedents absents"
    return True, "Publication autorisee"


def self_test():
    assert integer("12") == 12 and integer("2.5") is None
    assert number("2,5") == 2.5 and number("nan") is None
    assert france_verified({"pays": {"code": "FRA"}})
    assert not france_verified({"hippodrome": "Paris"})
    assert non_starter({"statut": "NON_PARTANT"})
    assert not non_starter({"statut": "PARTANT"})
    assert participant({"numPmu": 3, "nom": "TEST", "nonPartant": True})["non_partant"]
    assert departure("14:30", "2026-11-01")
    assert departure("1762000000000", "2026-11-01")
    with tempfile.TemporaryDirectory() as folder:
        p = Path(folder) / "test.json"
        atomic_json(p, {"ok": True})
        assert json.loads(p.read_text(encoding="utf-8"))["ok"]
        sample = report("2026-11-01", [], [], [], "AUCUNE_COURSE")
        assert not can_publish(sample, p)[0]
        good = report("2026-11-01", [{"id": "2026-11-01-R1-C1", "nombre_partants": 1, "non_partants": [], "partants": [{"numero": 1}]}], [], [], "OK")
        assert can_publish(good, Path(folder) / "absent.json")[0]
        atomic_json(p, good)
        older = dict(good, date="2026-10-31")
        assert not can_publish(older, p)[0]
    print("SELF-TEST OK : aucun reseau, aucune publication reelle")


def main():
    parser = argparse.ArgumentParser(description="X1000 - collecte PMU France verifiee")
    parser.add_argument("--date", default=dt.datetime.now(PARIS).date().isoformat())
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        selected = dt.date.fromisoformat(args.date)
        if selected.isoformat() != args.date:
            raise ValueError
    except ValueError:
        parser.error("--date doit etre AAAA-MM-JJ")
    try:
        result = collect(args.date)
    except (ValueError, OSError) as exc:
        print(f"Erreur de collecte : {exc}", file=sys.stderr)
        return 1
    destination = DATA / "publication_courses.json"
    allowed, reason = can_publish(result, destination)
    if not args.dry_run:
        try:
            if allowed:
                atomic_json(destination, result)
                print(f"Publication : {destination}")
            else:
                print(f"Publication refusee : {reason}", file=sys.stderr)
            # Le diagnostic ne touche jamais aux pronostics, resultats ou FIGEs.
            atomic_json(DATA / "diagnostic_collecte_pmu.json", result)
        except OSError as exc:
            print(f"Erreur d'ecriture : {exc}", file=sys.stderr)
            return 1
    else:
        print("DRY-RUN : aucune ecriture")
    print(json.dumps({"date": args.date, "statut": result["statut"], "publication_autorisee": allowed,
                      "motif": reason, "dry_run": args.dry_run, **result["statistiques"]}, ensure_ascii=False))
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())
