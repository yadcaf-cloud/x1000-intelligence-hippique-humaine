#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 PMU 2.7 — collecte France, sans pronostic ni modification M1-M5.

Python 3.10+. --self-test ne fait aucun appel reseau ni ecriture depot.
--dry-run effectue la collecte sans ecrire. Seuls deux JSON de data/
sont susceptibles d'etre ecrits en execution normale.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
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

VERSION = "X1000-PMU-SECURISE-2.8"
PARIS = ZoneInfo("Europe/Paris")
DATA = Path(__file__).resolve().parent / "data"
BASE = os.environ.get("PMU_API_BASE", "https://online.turfinfo.api.pmu.fr/rest/client/7").rstrip("/")
MAX_BYTES = 12_000_000


def now():
    return dt.datetime.now(PARIS).isoformat(timespec="seconds")


def txt(v):
    return "" if v is None or isinstance(v, (dict, list, bool)) else str(v).strip()


def pick(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if obj.get(k) is not None:
            return obj[k]
    return None


def num(v):
    s = txt(v)
    return int(s) if re.fullmatch(r"\d+", s) else None


def decimal(v):
    try:
        f = float(txt(v).replace(",", "."))
        return f if math.isfinite(f) else None
    except (ValueError, OverflowError):
        return None


def label(v):
    if isinstance(v, dict):
        v = pick(v, "libelle", "nom", "name", "code", "libelleCourt")
    return txt(v)


def norm(v):
    return unicodedata.normalize("NFKD", label(v)).encode("ascii", "ignore").decode().upper().strip()


def iso_date(s):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise ValueError("Date attendue AAAA-MM-JJ")
    return dt.date.fromisoformat(s).strftime("%d%m%Y")


def api_base():
    try:
        u = urlsplit(BASE)
        port = u.port
    except ValueError as exc:
        raise ValueError("PMU_API_BASE invalide") from exc
    if (u.scheme != "https" or not u.hostname or u.username or u.password
            or u.query or u.fragment or port not in (None, 443)):
        raise ValueError("PMU_API_BASE doit etre HTTPS sans identifiants ni parametres")


def fetch(path):
    api_base()
    if not re.fullmatch(r"[A-Za-z0-9/_-]+", path):
        raise ValueError("Chemin API interdit")
    url = BASE + "/" + path
    error = "inconnue"
    for attempt in range(3):
        retry = True
        try:
            req = Request(url, headers={"Accept": "application/json", "Accept-Encoding": "identity",
                                        "User-Agent": "X1000-PMU/2.7"})
            with urlopen(req, timeout=15) as response:
                # Refuser une redirection hors du domaine configure.
                actual = urlsplit(response.geturl())
                expected = urlsplit(BASE)
                if actual.scheme != "https" or actual.hostname != expected.hostname or actual.port not in (None, 443):
                    raise ValueError("Redirection vers un domaine non autorise")
                data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Reponse trop volumineuse")
            obj = json.loads(data.decode("utf-8-sig"))
            if not isinstance(obj, (list, dict)):
                raise ValueError("JSON non structure")
            return obj
        except HTTPError as exc:
            error = f"HTTP {exc.code}: {exc.reason}"
            retry = exc.code in (408, 429, 500, 502, 503, 504)
            exc.close()
        except (URLError, TimeoutError, OSError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        except (ValueError, UnicodeError) as exc:
            error, retry = str(exc), False
        if not retry:
            break
        if attempt < 2:
            time.sleep(0.5 * (2 ** attempt))
    raise RuntimeError(f"API {path}: {error}")


def unwrap_list(obj, keys):
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return []
    for k in keys:
        if isinstance(obj.get(k), list):
            return obj[k]
    for wrapper in ("programme", "data", "result", "resultat"):
        nested = obj.get(wrapper)
        if isinstance(nested, (dict, list)):
            found = unwrap_list_shallow(nested, keys)
            if found is not None:
                return found
    return []


def unwrap_list_shallow(obj, keys):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), list):
                return obj[k]
    return None


def france(reunion):
    country = pick(reunion, "codePays", "pays", "countryCode", "country")
    if isinstance(country, dict):
        country = pick(country, "code", "codePays", "nom", "libelle")
    return norm(country) in ("FR", "FRA", "FRANCE")


def depart(value, date):
    if isinstance(value, bool) or value is None:
        return None
    s = txt(value)
    try:
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


def non_partant(item):
    flag = pick(item, "nonPartant", "non_partant")
    if flag is True or (isinstance(flag, int) and not isinstance(flag, bool) and flag == 1) or (isinstance(flag, str) and flag.strip().lower() in ("true", "oui", "yes", "1")):
        return True
    return norm(pick(item, "statut", "statutParticipant", "status")).replace("-", "_").replace(" ", "_") in ("NP", "NON_PARTANT", "NONPARTANT")


def participant(item):
    if not isinstance(item, dict):
        return None
    n = num(pick(item, "numPmu", "numero", "num", "numeroPmu"))
    name = txt(pick(item, "nom", "nomCheval", "name"))
    if not n or not name:
        return None
    np = non_partant(item)
    return {"numero": n, "nom": name, "non_partant": np,
            "statut": "NON_PARTANT" if np else "PARTANT",
            "jockey_driver": label(pick(item, "driver", "jockey", "nomJockey", "nomDriver")) or None,
            "entraineur": label(pick(item, "entraineur", "nomEntraineur")) or None,
            "musique": txt(pick(item, "musique", "musiqueCheval")) or None,
            "age": num(item.get("age")), "sexe": txt(item.get("sexe")) or None,
            "gains": decimal(pick(item, "gainsParticipant", "gains", "gain")), "source": "PMU"}


def report(date, courses, errors, warnings):
    status = "PARTIEL" if courses and errors else "OK" if courses else "ECHEC" if errors else "AUCUNE_COURSE"
    return {"version": VERSION, "date": date, "collecte_a": now(), "source": BASE,
            "statut": status, "courses": courses, "erreurs": errors,
            "avertissements": warnings,
            "statistiques": {"courses": len(courses),
                             "partants": sum(c["nombre_partants"] for c in courses),
                             "non_partants": sum(len(c["non_partants"]) for c in courses)}}


def collect(date):
    errors, warnings, courses, seen = [], [], [], set()
    try:
        programme = fetch("programme/" + iso_date(date))
    except (RuntimeError, ValueError) as exc:
        return report(date, [], [str(exc)], [])
    reunions = unwrap_list(programme, ("reunions", "réunions", "reunion", "listeReunions"))
    if not reunions:
        return report(date, [], ["Aucune reunion exploitable; structure=" +
                                 (",".join(programme.keys())[:300] if isinstance(programme, dict) else "liste vide")], [])
    for reunion in reunions:
        if not isinstance(reunion, dict):
            errors.append("Reunion invalide")
            continue
        nr = num(pick(reunion, "numOfficiel", "numReunion", "numero"))
        if not nr:
            errors.append("Numero de reunion absent")
            continue
        if not france(reunion):
            warnings.append(f"R{nr}: pays France non confirme, exclusion")
            continue
        races = unwrap_list(reunion, ("courses", "listeCourses"))
        if not races:
            warnings.append(f"R{nr}: aucune course dans le programme")
        for race in races:
            if not isinstance(race, dict):
                errors.append(f"R{nr}: course invalide")
                continue
            nc = num(pick(race, "numOrdre", "numCourse", "numero"))
            if not nc:
                errors.append(f"R{nr}: numero de course absent")
                continue
            cid = f"{date}-R{nr}-C{nc}"
            if cid in seen:
                errors.append(f"{cid}: doublon")
                continue
            seen.add(cid)
            discipline = label(pick(race, "discipline", "typeCourse", "specialite"))
            start = depart(pick(race, "heureDepart", "depart", "heureDepartPrevue"), date)
            if not discipline or not start or start[:10] != date:
                errors.append(f"{cid}: discipline ou depart invalide")
                continue
            try:
                details = fetch(f"programme/{iso_date(date)}/R{nr}/C{nc}/participants")
            except (RuntimeError, ValueError) as exc:
                errors.append(f"{cid}: {exc}")
                continue
            raw = unwrap_list(details, ("participants", "partants", "listeParticipants"))
            if not raw:
                errors.append(f"{cid}: participants absents ou format non reconnu")
                continue
            people = {}
            invalid = False
            for item in raw:
                p = participant(item)
                if p is None or p["numero"] in people:
                    invalid = True
                    break
                people[p["numero"]] = p
            if invalid:
                errors.append(f"{cid}: participant invalide ou numero en doublon")
                continue
            ordered = [people[n] for n in sorted(people)]
            absent = [{"numero": p["numero"], "nom": p["nom"]} for p in ordered if p["non_partant"]]
            active = len(ordered) - len(absent)
            if active < 1:
                errors.append(f"{cid}: aucun partant actif")
                continue
            courses.append({"id": cid, "reunion": nr, "course": nc,
                            "hippodrome": label(pick(reunion, "hippodrome", "nomHippodrome")),
                            "localisation_france_verifiee": True,
                            "nom": label(pick(race, "libelle", "nom", "libelleCourse")),
                            "discipline": discipline, "depart": start, "depart_verifie": True,
                            "nombre_participants": len(ordered), "nombre_partants": active,
                            "non_partants": absent, "partants": ordered, "source": "PMU",
                            "collecte_a": now(), "statut": "PARTANTS_CHARGES"})
    courses.sort(key=lambda c: (c["depart"], c["id"]))
    return report(date, courses, errors, warnings)


def can_publish(new, destination):
    if new["statut"] != "OK" or not new["courses"]:
        return False, "Collecte vide, incomplete ou en erreur"
    if not destination.exists():
        return True, "Premiere publication"
    try:
        old = json.loads(destination.read_text(encoding="utf-8"))
        if not isinstance(old, dict) or not isinstance(old.get("courses"), list):
            return False, "Ancienne publication de format inconnu"
        old_date = txt(old.get("date"))
        iso_date(old_date)
        if old_date > new["date"]:
            return False, "Refus de revenir a une date anterieure"
        if old_date < new["date"]:
            return True, "Nouvelle journee"
        previous = {}
        for c in old["courses"]:
            if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("partants"), list) or c["id"] in previous:
                return False, "Ancienne publication incoherente"
            previous[c["id"]] = c
        current = {c["id"]: c for c in new["courses"]}
        if not previous.keys() <= current.keys():
            return False, "Course precedemment publiee disparue"
        for cid, c in previous.items():
            old_people = {}
            for p in c["partants"]:
                if not isinstance(p, dict) or not num(p.get("numero")) or num(p["numero"]) in old_people:
                    return False, f"{cid}: ancien participant invalide"
                old_people[num(p["numero"])] = p
            fresh = {p["numero"]: p for p in current[cid]["partants"]}
            if not old_people.keys() <= fresh.keys():
                return False, f"{cid}: ancien participant disparu"
            for n, p in old_people.items():
                if txt(p.get("nom")) != fresh[n]["nom"]:
                    return False, f"{cid}: identite du cheval modifiee"
                if p.get("non_partant") is True and fresh[n]["non_partant"] is False:
                    return False, f"{cid}: non-partant precedemment confirme redevenu partant"
            if txt(c.get("depart")) and txt(c.get("depart")) != txt(current[cid].get("depart")):
                return False, f"{cid}: horaire de depart modifie; validation manuelle requise"
        return True, "Mise a jour autorisee"
    except (OSError, ValueError, TypeError, KeyError):
        return False, "Ancienne publication illisible ou incoherente"


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".x1000-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(obj, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def self_test():
    assert iso_date("2026-11-01") == "01112026"
    assert unwrap_list({"programme": {"reunions": [{"numOfficiel": 1}]}}, ("reunions",)) == [{"numOfficiel": 1}]
    assert france({"pays": {"code": "FRA"}})
    assert not france({"hippodrome": "Paris"})
    assert not france({"pays": "BELGIQUE"})
    assert participant({"numPmu": 2, "nom": "TEST", "nonPartant": True})["non_partant"]
    assert non_partant({"nonPartant": " true "})
    assert not non_partant({"nonPartant": False})
    assert num(True) is None
    assert depart("14:30", "2026-11-01").startswith("2026-11-01T14:30")
    assert not can_publish(report("2026-11-01", [], [], []), Path("/nonexistent"))[0]
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "test.json"
        atomic_json(path, {"ok": True})
        assert json.loads(path.read_text(encoding="utf-8"))["ok"]
    print("SELF-TEST OK — aucun reseau, aucune modification du depot")


def main():
    parser = argparse.ArgumentParser(description="X1000 - collecte PMU France securisee")
    parser.add_argument("--date", default=dt.datetime.now(PARIS).date().isoformat())
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        iso_date(args.date)
    except ValueError:
        parser.error("--date doit etre AAAA-MM-JJ")
    result = collect(args.date)
    destination = DATA / "publication_courses.json"
    allowed, reason = can_publish(result, destination)
    if not args.dry_run:
        try:
            if allowed:
                atomic_json(destination, result)
            atomic_json(DATA / "diagnostic_collecte_pmu.json", result)
        except OSError as exc:
            print(f"Erreur d'ecriture: {exc}", file=sys.stderr)
            return 1
    print(json.dumps({"version": VERSION, "date": args.date,
                      "statut": result["statut"], "publication_autorisee": allowed,
                      "motif": reason, "dry_run": args.dry_run,
                      "erreurs": result["erreurs"], "avertissements": result["avertissements"],
                      "statistiques": result["statistiques"]}, ensure_ascii=False, indent=2))
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())
