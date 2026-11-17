#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""X1000 - Resultats et archives securises V1.1.

Import manuel de resultats attestes.
Aucune collecte automatique de resultats PMU.
Aucune verification reseau de la source officielle.

Garanties :
- Courses PMU France uniquement.
- Validation stricte des participants.
- Aucune arrivee inventee.
- Aucun pronostic ou FIGE.
- Archives SHA256 immuables.
- Anciennes versions conservees.
- Aucune modification M1-M5.
- Aucun acces au depot Phoenix.
- Tests sans reseau ni ecriture.
- Python 3.10+.
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
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


VERSION = "X1000-RESULTATS-ARCHIVES-1.1"

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

COURSES = DATA / "publication_courses.json"
ARCHIVES = DATA / "archives_resultats"

PARIS = ZoneInfo("Europe/Paris")

ID_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})-R([1-9]\d*)-C([1-9]\d*)$"
)

SHA_RE = re.compile(r"^[0-9a-f]{64}$")

MAX_JSON_BYTES = 2_000_000


def canonical(obj):
    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def read_json(path):
    if not path.is_file():
        raise ValueError(f"Fichier absent : {path}")

    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Fichier JSON trop volumineux")

    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)

    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Limite JSON depassee")

    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"Cle JSON dupliquee : {key}"
                )
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(
            f"Constante JSON interdite : {value}"
        )

    return json.loads(
        raw.decode("utf-8-sig"),
        object_pairs_hook=no_duplicates,
        parse_constant=reject_constant,
    )


def instant(value):
    if not isinstance(value, str):
        raise ValueError("Horodatage absent")

    if not re.search(
        r"(Z|[+-]\d{2}:\d{2})$",
        value,
    ):
        raise ValueError(
            "Fuseau horaire explicite obligatoire"
        )

    try:
        parsed = dt.datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError(
            "Horodatage ISO invalide"
        ) from exc

    if parsed.utcoffset() is None:
        raise ValueError("Fuseau absent")

    return parsed


def valid_course_id(cid):
    if not isinstance(cid, str):
        return False

    match = ID_RE.fullmatch(cid)

    if match is None:
        return False

    try:
        dt.date.fromisoformat(match.group(1))
    except ValueError:
        return False

    return True


def official_url(url):
    if not isinstance(url, str):
        return False

    if not url or any(
        ord(char) < 33 for char in url
    ):
        return False

    try:
        parsed = urlsplit(url)

        host = (parsed.hostname or "").lower()

        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and parsed.fragment == ""
            and (
                host == "pmu.fr"
                or host.endswith(".pmu.fr")
            )
        )

    except ValueError:
        return False


def validate_course(course):
    if not isinstance(course, dict):
        raise ValueError("Course non objet")

    cid = course.get("id")

    if not valid_course_id(cid):
        raise ValueError(
            "Identifiant de course invalide"
        )

    if course.get(
        "localisation_france_verifiee"
    ) is not True:
        raise ValueError(
            f"{cid}: France non verifiee"
        )

    if course.get("depart_verifie") is not True:
        raise ValueError(
            f"{cid}: depart non verifie"
        )

    depart = instant(course.get("depart"))

    expected_date = cid[:10]

    if (
        depart.astimezone(PARIS)
        .date()
        .isoformat()
        != expected_date
    ):
        raise ValueError(
            f"{cid}: date de depart incoherente"
        )

    participants = course.get("partants")

    if (
        not isinstance(participants, list)
        or not participants
    ):
        raise ValueError(
            f"{cid}: participants absents"
        )

    numbers = set()
    active = set()
    non_starters = set()

    for horse in participants:
        if not isinstance(horse, dict):
            raise ValueError(
                f"{cid}: participant invalide"
            )

        number = horse.get("numero")

        if (
            type(number) is not int
            or number <= 0
            or number in numbers
        ):
            raise ValueError(
                f"{cid}: numero invalide ou doublon"
            )

        if not isinstance(
            horse.get("nom"), str
        ) or not horse["nom"].strip():
            raise ValueError(
                f"{cid}: nom de cheval absent"
            )

        if type(
            horse.get("non_partant")
        ) is not bool:
            raise ValueError(
                f"{cid}: statut participant invalide"
            )

        numbers.add(number)

        if horse["non_partant"]:
            non_starters.add(number)
        else:
            active.add(number)

    if not active:
        raise ValueError(
            f"{cid}: aucun partant actif"
        )

    if (
        type(course.get("nombre_participants"))
        is not int
        or course["nombre_participants"]
        != len(numbers)
    ):
        raise ValueError(
            f"{cid}: nombre participants incoherent"
        )

    if (
        type(course.get("nombre_partants"))
        is not int
        or course["nombre_partants"]
        != len(active)
    ):
        raise ValueError(
            f"{cid}: nombre partants incoherent"
        )

    declared = course.get("non_partants")

    if not isinstance(declared, list):
        raise ValueError(
            f"{cid}: liste non-partants absente"
        )

    declared_numbers = []

    for item in declared:
        if not isinstance(item, dict):
            raise ValueError(
                f"{cid}: non-partant invalide"
            )

        number = item.get("numero")

        if type(number) is not int:
            raise ValueError(
                f"{cid}: numero non-partant invalide"
            )

        declared_numbers.append(number)

    if (
        len(declared_numbers)
        != len(set(declared_numbers))
        or set(declared_numbers) != non_starters
    ):
        raise ValueError(
            f"{cid}: non-partants incoherents"
        )

    return active


def load_courses():
    payload = read_json(COURSES)

    if not isinstance(payload, dict):
        raise ValueError(
            "Publication PMU non objet"
        )

    if payload.get("statut") != "OK":
        raise ValueError(
            "Collecte PMU non validee"
        )

    courses = payload.get("courses")

    if (
        not isinstance(courses, list)
        or not courses
    ):
        raise ValueError(
            "Aucune course PMU disponible"
        )

    indexed = {}

    for course in courses:
        validate_course(course)

        cid = course["id"]

        if cid in indexed:
            raise ValueError(
                f"Course dupliquee : {cid}"
            )

        indexed[cid] = course

    return indexed


def validate_result(
    candidate,
    courses,
    source_url,
    attestation,
):
    if not attestation:
        raise ValueError(
            "Attestation humaine obligatoire"
        )

    if not isinstance(candidate, dict):
        raise ValueError(
            "Resultat JSON non objet"
        )

    expected_keys = {
        "course_id",
        "arrivee",
        "statut",
        "source_url",
        "date_publication",
    }

    if set(candidate) != expected_keys:
        raise ValueError(
            "Champs du resultat non conformes"
        )

    cid = candidate.get("course_id")

    if not valid_course_id(cid):
        raise ValueError(
            "Identifiant course invalide"
        )

    if cid not in courses:
        raise ValueError(
            "Course absente de la publication PMU"
        )

    course = courses[cid]

    active = validate_course(course)

    if candidate.get("statut") != "OFFICIEL":
        raise ValueError(
            "Statut OFFICIEL obligatoire"
        )

    if (
        candidate.get("source_url")
        != source_url
        or not official_url(source_url)
    ):
        raise ValueError(
            "Source PMU non conforme"
        )

    published = instant(
        candidate.get("date_publication")
    )

    departure = instant(
        course.get("depart")
    )

    now = dt.datetime.now(dt.timezone.utc)

    if published <= departure:
        raise ValueError(
            "Publication avant le depart"
        )

    if published > now:
        raise ValueError(
            "Date de publication future"
        )

    arrival = candidate.get("arrivee")

    if (
        not isinstance(arrival, list)
        or not arrival
        or len(arrival) > len(active)
    ):
        raise ValueError(
            "Arrivee absente ou trop longue"
        )

    if any(
        type(number) is not int
        or number <= 0
        for number in arrival
    ):
        raise ValueError(
            "Numero d'arrivee invalide"
        )

    if len(set(arrival)) != len(arrival):
        raise ValueError(
            "Numero repete dans l'arrivee"
        )

    if not set(arrival).issubset(active):
        raise ValueError(
            "Arrivee hors participants actifs"
        )

    return {
        "schema": "X1000-RESULTAT-1",
        "version_moteur": VERSION,
        "course_id": cid,
        "arrivee": list(arrival),
        "statut": "OFFICIEL_ATTESTE",
        "source_url": source_url,
        "date_publication": published.isoformat(),
        "verification": "ATTESTATION_HUMAINE",
        "verification_automatique_source": False,
        "source_course_sha256": sha256(course),
    }


def archive_payload(record):
    body_hash = sha256(record)

    return {
        **record,
        "sha256": body_hash,
    }


def verify_archive(path):
    if path.is_symlink():
        raise ValueError(
            f"Lien symbolique interdit : {path}"
        )

    obj = read_json(path)

    if not isinstance(obj, dict):
        raise ValueError(
            f"Archive non objet : {path}"
        )

    stored_hash = obj.get("sha256")

    if (
        not isinstance(stored_hash, str)
        or not SHA_RE.fullmatch(stored_hash)
    ):
        raise ValueError(
            f"SHA256 invalide : {path}"
        )

    body = {
        key: value
        for key, value in obj.items()
        if key != "sha256"
    }

    actual_hash = sha256(body)

    if actual_hash != stored_hash:
        raise ValueError(
            f"Archive alteree : {path}"
        )

    cid = obj.get("course_id")

    if (
        not valid_course_id(cid)
        or path.parent.name != cid
        or path.name != stored_hash + ".json"
    ):
        raise ValueError(
            f"Chemin archive incoherent : {path}"
        )

    if obj.get("schema") != "X1000-RESULTAT-1":
        raise ValueError(
            f"Schema archive inconnu : {path}"
        )

    return obj


def verify_archives():
    if not ARCHIVES.exists():
        return 0

    if ARCHIVES.is_symlink():
        raise ValueError(
            "Repertoire archives symbolique interdit"
        )

    count = 0

    for directory in sorted(ARCHIVES.iterdir()):
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or not valid_course_id(directory.name)
        ):
            raise ValueError(
                f"Repertoire archive invalide : {directory}"
            )

        for path in sorted(directory.iterdir()):
            if (
                path.is_symlink()
                or not path.is_file()
                or path.suffix != ".json"
            ):
                raise ValueError(
                    f"Fichier archive inattendu : {path}"
                )

            verify_archive(path)
            count += 1

    return count


def save_immutable(record):
    # Refuser toute nouvelle ecriture si les archives
    # precedentes presentent une anomalie.
    verify_archives()

    cid = record["course_id"]

    if not valid_course_id(cid):
        raise ValueError(
            "Identifiant archive invalide"
        )

    payload = archive_payload(record)
    body_hash = payload["sha256"]

    target_dir = ARCHIVES / cid
    target = target_dir / (body_hash + ".json")

    if target.exists() or target.is_symlink():
        existing = verify_archive(target)

        if existing != payload:
            raise ValueError(
                "Archive existante differente"
            )

        return target, False

    if ARCHIVES.is_symlink():
        raise ValueError(
            "Repertoire archives symbolique"
        )

    ARCHIVES.mkdir(
        parents=True,
        exist_ok=True,
    )

    if target_dir.is_symlink():
        raise ValueError(
            "Dossier course symbolique"
        )

    target_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Creation exclusive : aucun ecrasement.
    # En cas d'interruption, un fichier incomplet
    # sera detecte par la verification SHA256.
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
    )

    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(
        target,
        flags,
        0o644,
    )

    try:
        with os.fdopen(
            fd,
            "wb",
        ) as handle:
            content = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ).encode("utf-8") + b"\n"

            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    except BaseException:
        # Ne pas effacer un fichier dont l'ecriture
        # pourrait avoir ete interrompue : il reste
        # detectable par le controle d'integrite.
        raise

    verify_archive(target)

    return target, True


def self_test():
    sample_id = "2026-11-01-R1-C1"

    sample = {
        "course_id": sample_id,
        "statut": "OFFICIEL",
        "arrivee": [2, 1],
        "source_url": "https://www.pmu.fr/exemple",
        "date_publication":
            "2026-11-01T16:00:00+01:00",
    }

    course = {
        "id": sample_id,
        "depart":
            "2026-11-01T14:00:00+01:00",
        "localisation_france_verifiee": True,
        "depart_verifie": True,
        "nombre_participants": 2,
        "nombre_partants": 2,
        "non_partants": [],
        "partants": [
            {
                "numero": 1,
                "nom": "CHEVAL_TEST_1",
                "non_partant": False,
            },
            {
                "numero": 2,
                "nom": "CHEVAL_TEST_2",
                "non_partant": False,
            },
        ],
    }

    courses = {sample_id: course}

    result = validate_result(
        sample,
        courses,
        sample["source_url"],
        True,
    )

    assert result["arrivee"] == [2, 1]

    assert (
        result["verification_automatique_source"]
        is False
    )

    assert valid_course_id(sample_id)

    assert not valid_course_id(
        "2026-99-99-R1-C1"
    )

    assert not official_url(
        "https://pmu.fr.evil.example/test"
    )

    assert official_url(
        "https://www.pmu.fr/exemple"
    )

    invalid_cases = [
        {"arrivee": [3]},
        {"arrivee": [1, 1]},
        {"arrivee": [True]},
        {"statut": "PROVISOIRE"},
        {
            "date_publication":
                "2026-11-01T13:00:00+01:00"
        },
    ]

    for changes in invalid_cases:
        candidate = dict(sample)
        candidate.update(changes)

        try:
            validate_result(
                candidate,
                courses,
                sample["source_url"],
                True,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"Cas invalide accepte : {changes}"
            )

    body = {
        "course_id": sample_id,
        "arrivee": [2, 1],
    }

    assert sha256(body) == sha256(body)

    assert sha256(body) != sha256(
        {
            "course_id": sample_id,
            "arrivee": [1, 2],
        }
    )

    print(
        "SELF-TEST OK - "
        "aucun reseau, aucune ecriture"
    )


def main():
    parser = argparse.ArgumentParser(
        description=VERSION
    )

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    parser.add_argument(
        "--verify-archives",
        action="store_true",
    )

    parser.add_argument(
        "--import-file",
        type=Path,
    )

    parser.add_argument(
        "--source-url",
    )

    parser.add_argument(
        "--attestation",
        action="store_true",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    args = parser.parse_args()

    try:
        if args.self_test:
            self_test()
            return 0

        if args.verify_archives:
            count = verify_archives()

            print(
                "ARCHIVES INTEGRES :",
                count,
            )

            return 0

        if (
            args.import_file is None
            or args.source_url is None
        ):
            parser.error(
                "--import-file et --source-url requis"
            )

        if not args.attestation:
            raise ValueError(
                "Attestation humaine requise"
            )

        candidate = read_json(
            args.import_file
        )

        courses = load_courses()

        record = validate_result(
            candidate,
            courses,
            args.source_url,
            args.attestation,
        )

        if args.dry_run:
            print(
                "VALIDATION OK SANS ECRITURE :",
                record["course_id"],
            )

            return 0

        target, created = save_immutable(
            record
        )

        print(
            "ARCHIVE CREEE"
            if created
            else "ARCHIVE DEJA EXISTANTE"
        )

        print("Fichier :", target)
        print("SHA256 :", sha256(record))

        return 0

    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        UnicodeError,
    ) as exc:
        print(
            "ERREUR :",
            exc,
            file=sys.stderr,
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
