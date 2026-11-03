#!/usr/bin/env python3
# X1000 - Initialisation securisee M1-M5 V1.3
# Source PMU reelle uniquement.
# Aucun pronostic fictif.
# Aucun FIGE retroactif.
# Aucune modification des archives.
# Creation exclusive de publication_moteurs.json.

import hashlib
import json
import re

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE = Path("data/publication_courses.json")
DESTINATION = Path("data/publication_moteurs.json")

PARIS = ZoneInfo("Europe/Paris")
MAX_AGE_SECONDS = 3 * 60 * 60
FUTURE_TOLERANCE_SECONDS = 60

COURSE_ID = re.compile(
    r"^(\d{4}-\d{2}-\d{2})-R([1-9]\d*)-C([1-9]\d*)$"
)

TIMEZONE_SUFFIX = re.compile(
    r"(Z|[+-]\d{2}:\d{2})$"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def positive_integer(value):
    return type(value) is int and value > 0


def nonnegative_integer(value):
    return type(value) is int and value >= 0


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def parse_time(value):
    require(nonempty(value), "Horodatage absent")
    require(
        TIMEZONE_SUFFIX.search(value) is not None,
        "Fuseau horaire obligatoire"
    )

    dt = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    require(
        dt.utcoffset() is not None,
        "Fuseau horaire absent"
    )

    return dt


def check_freshness(timestamp, now, context):
    age = (now - timestamp).total_seconds()

    require(
        age >= -FUTURE_TOLERANCE_SECONDS,
        f"Collecte future : {context}"
    )

    require(
        age <= MAX_AGE_SECONDS,
        f"Collecte trop ancienne : {context}"
    )


def validate_source(data):
    require(
        isinstance(data, dict),
        "Source PMU non conforme"
    )

    require(
        data.get("statut") in ("OK", "PARTIEL"),
        "Statut PMU invalide"
    )

    require(
        nonempty(data.get("source")),
        "Source PMU absente"
    )

    day = data.get("date")

    require(
        nonempty(day)
        and re.fullmatch(
            r"\d{4}-\d{2}-\d{2}", day
        ) is not None,
        "Date PMU invalide"
    )

    datetime.strptime(day, "%Y-%m-%d")

    now = datetime.now(timezone.utc)
    collected = parse_time(data.get("collecte_a"))

    check_freshness(
        collected, now, "publication generale"
    )

    require(
        collected.astimezone(
            PARIS
        ).date().isoformat() == day,
        "Date de collecte incompatible"
    )

    races = data.get("courses")

    require(
        isinstance(races, list),
        "Liste des courses invalide"
    )

    require(
        len(races) > 0,
        "Aucune course PMU disponible"
    )

    seen_races = set()

    for race in races:
        require(
            isinstance(race, dict),
            "Objet course invalide"
        )

        rid = race.get("id")
        require(nonempty(rid), "ID course absent")

        match = COURSE_ID.fullmatch(rid)

        require(
            match is not None,
            f"Identifiant invalide : {rid}"
        )

        require(
            match.group(1) == day,
            f"Date ID incorrecte : {rid}"
        )

        require(
            rid not in seen_races,
            f"Course dupliquee : {rid}"
        )

        seen_races.add(rid)

        require(
            type(race.get("reunion")) is int
            and race["reunion"] == int(match.group(2)),
            f"Reunion incorrecte : {rid}"
        )

        require(
            type(race.get("course")) is int
            and race["course"] == int(match.group(3)),
            f"Numero course incorrect : {rid}"
        )

        require(
            race.get("localisation_france_verifiee")
            is True,
            f"Localisation France non verifiee : {rid}"
        )

        require(
            race.get("depart_verifie") is True,
            f"Depart non verifie : {rid}"
        )

        for field in (
            "hippodrome",
            "nom",
            "discipline"
        ):
            require(
                nonempty(race.get(field)),
                f"Champ {field} absent : {rid}"
            )

        require(
            race.get("source") == "PMU",
            f"Source course invalide : {rid}"
        )

        departure = parse_time(
            race.get("depart")
        )

        require(
            departure.astimezone(
                PARIS
            ).date().isoformat() == day,
            f"Date depart incompatible : {rid}"
        )

        race_collected = parse_time(
            race.get("collecte_a")
        )

        check_freshness(
            race_collected, now, rid
        )

        difference = (
            race_collected - collected
        ).total_seconds()

        require(
            difference <= FUTURE_TOLERANCE_SECONDS,
            f"Collecte course posterieure au lot : {rid}"
        )

        horses = race.get("partants")
        absent = race.get("non_partants")

        require(
            isinstance(horses, list),
            f"Liste partants invalide : {rid}"
        )

        require(
            isinstance(absent, list),
            f"Liste non-partants invalide : {rid}"
        )

        numbers = set()
        horses_by_number = {}
        actual_absent = set()
        active = 0

        for horse in horses:
            require(
                isinstance(horse, dict),
                f"Cheval invalide : {rid}"
            )

            number = horse.get("numero")

            require(
                positive_integer(number),
                f"Numero cheval invalide : {rid}"
            )

            require(
                number not in numbers,
                f"Numero duplique : {rid}/{number}"
            )

            numbers.add(number)
            horses_by_number[number] = horse

            require(
                nonempty(horse.get("nom")),
                f"Nom cheval absent : {rid}/{number}"
            )

            require(
                type(horse.get("non_partant")) is bool,
                f"Indicateur NP invalide : {rid}/{number}"
            )

            require(
                horse.get("source") == "PMU",
                f"Source cheval invalide : {rid}/{number}"
            )

            if horse["non_partant"]:
                actual_absent.add(number)

                require(
                    horse.get("statut") == "NON_PARTANT",
                    f"Statut NP incorrect : {rid}/{number}"
                )
            else:
                active += 1

                require(
                    horse.get("statut") == "PARTANT",
                    f"Statut partant incorrect : {rid}/{number}"
                )

        declared_absent = set()

        for horse in absent:
            require(
                isinstance(horse, dict),
                f"Non-partant invalide : {rid}"
            )

            number = horse.get("numero")

            require(
                positive_integer(number),
                f"Numero NP invalide : {rid}"
            )

            require(
                nonempty(horse.get("nom")),
                f"Nom NP absent : {rid}"
            )

            require(
                number not in declared_absent,
                f"NP duplique : {rid}/{number}"
            )

            declared_absent.add(number)

            original = horses_by_number.get(number)

            require(
                original is not None,
                f"NP inconnu : {rid}/{number}"
            )

            require(
                original["nom"] == horse["nom"],
                f"Nom NP incompatible : {rid}/{number}"
            )

            require(
                original["non_partant"] is True,
                f"NP non confirme : {rid}/{number}"
            )

        require(
            declared_absent == actual_absent,
            f"Liste NP incoherente : {rid}"
        )

        require(
            nonnegative_integer(
                race.get("nombre_participants")
            )
            and race["nombre_participants"] == len(horses),
            f"Total participants incorrect : {rid}"
        )

        require(
            nonnegative_integer(
                race.get("nombre_partants")
            )
            and race["nombre_partants"] == active,
            f"Total actifs incorrect : {rid}"
        )

    return day, len(races)


def main():
    require(
        SOURCE.is_file()
        and not SOURCE.is_symlink(),
        "Source PMU absente ou lien symbolique"
    )

    require(
        not DESTINATION.exists()
        and not DESTINATION.is_symlink(),
        "Publication moteurs deja existante"
    )

    raw = SOURCE.read_bytes()

    source_hash = hashlib.sha256(
        raw
    ).hexdigest()

    data = json.loads(
        raw.decode("utf-8")
    )

    day, count = validate_source(data)

    # Controle contre les modifications de source.
    require(
        hashlib.sha256(
            SOURCE.read_bytes()
        ).hexdigest() == source_hash,
        "Source PMU modifiee pendant validation"
    )

    publication = {
        "version": "X1000-MOTEURS-1.0",
        "statut": "PUBLICATION_MOTEURS",
        "date": day,
        "source": SOURCE.as_posix(),
        "source_sha256": source_hash,
        "publie_a": datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds"),
        "courses": []
    }

    content = json.dumps(
        publication,
        ensure_ascii=False,
        indent=2,
        allow_nan=False
    ) + "\n"

    # Creation exclusive.
    # Le mode x interdit l'ecrasement.
    try:
        with DESTINATION.open(
            "x", encoding="utf-8"
        ) as output:
            try:
                output.write(content)
                output.flush()
            except BaseException:
                output.close()
                DESTINATION.unlink(missing_ok=True)
                raise

    except FileExistsError:
        raise SystemExit(
            "ARRET - Publication moteurs deja existante"
        )

    print("====================================")
    print("X1000 - INITIALISATION M1-M5 V1.3")
    print("====================================")
    print(f"OK - {count} courses PMU controlees")
    print("OK - Localisation France verifiee")
    print("OK - Departs verifies")
    print("OK - Partants et NP coherents")
    print("OK - Fraicheur PMU conforme")
    print("OK - SHA256 :", source_hash)
    print("OK - Publication moteurs initialisee")
    print("OK - Zero FIGE fictif")
    print("OK - Aucune archive modifiee")
    print("====================================")


if __name__ == "__main__":
    try:
        main()

    except (
        ValueError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        OSError,
        OverflowError
    ) as exc:
        raise SystemExit(
            f"ERREUR - {exc}"
        ) from exc
