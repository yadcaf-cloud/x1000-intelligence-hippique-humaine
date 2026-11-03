#!/usr/bin/env python3
# X1000 — Initialisation sécurisée M1 à M5
# Aucune prévision fictive.
# Aucun FIGÉ rétroactif.
# Aucun écrasement d'une publication existante.

import hashlib
import json
import re

from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path("data/publication_courses.json")
DESTINATION = Path("data/publication_moteurs.json")

COURSE_ID = re.compile(
    r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$"
)


def parse_datetime(value):
    if (
        not isinstance(value, str)
        or not re.search(
            r"(Z|[+-]\d{2}:\d{2})$",
            value
        )
    ):
        raise ValueError(
            "Horodatage avec fuseau horaire obligatoire"
        )

    date = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if date.utcoffset() is None:
        raise ValueError("Fuseau horaire absent")

    return date


def main():

    # 1. Vérification de la source

    if SOURCE.is_symlink() or not SOURCE.is_file():
        raise SystemExit(
            "ERREUR : source absente ou lien symbolique interdit."
        )

    # 2. Protection des publications existantes

    if DESTINATION.is_symlink() or DESTINATION.exists():
        raise SystemExit(
            "ARRÊT : destination déjà présente. "
            "Aucune modification effectuée."
        )

    raw = SOURCE.read_bytes()

    # 3. Validation des données PMU réelles

    try:
        data = json.loads(raw.decode("utf-8"))

        if (
            not isinstance(data, dict)
            or data.get("statut") not in ("OK", "PARTIEL")
        ):
            raise ValueError(
                "Statut de collecte invalide"
            )

        day = data.get("date")

        if (
            not isinstance(day, str)
            or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}",
                day
            )
        ):
            raise ValueError(
                "Date de publication invalide"
            )

        datetime.strptime(day, "%Y-%m-%d")

        collected = parse_datetime(
            data.get("collecte_a")
        )

        if collected > datetime.now(timezone.utc):
            raise ValueError(
                "Collecte datée dans le futur"
            )

        races = data.get("courses")

        if not isinstance(races, list):
            raise ValueError(
                "Liste des courses invalide"
            )

        seen = set()

        for race in races:

            if not isinstance(race, dict):
                raise ValueError(
                    "Objet course invalide"
                )

            rid = race.get("id")

            if (
                not isinstance(rid, str)
                or not COURSE_ID.fullmatch(rid)
                or rid[:10] != day
                or rid in seen
            ):
                raise ValueError(
                    "Identifiant de course invalide "
                    "ou dupliqué"
                )

            seen.add(rid)

    except (
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        OverflowError
    ) as exc:
        raise SystemExit(
            "ERREUR : publication PMU non conforme : "
            + str(exc)
        )

    # 4. Création d'une publication vide et authentique

    publication = {
        "version": "X1000-MOTEURS-1.0",
        "statut": "PUBLICATION_MOTEURS",
        "date": day,
        "source": SOURCE.as_posix(),
        "source_sha256": hashlib.sha256(
            raw
        ).hexdigest(),
        "publie_a": datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds"),
        "courses": []
    }

    # 5. Écriture exclusive

    DESTINATION.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    content = json.dumps(
        publication,
        ensure_ascii=False,
        indent=2
    ) + "\n"

    try:
        with DESTINATION.open(
            "x",
            encoding="utf-8"
        ) as output:
            try:
                output.write(content)
                output.flush()

            except BaseException:
                output.close()
                DESTINATION.unlink(
                    missing_ok=True
                )
                raise

    except FileExistsError:
        raise SystemExit(
            "ARRÊT : publication déjà créée. "
            "Aucun écrasement."
        )

    print(
        "OK : publication_moteurs.json créé."
    )
    print(
        "SHA256 calculé depuis la source réelle."
    )
    print(
        "Décisions FIGÉES publiées : 0."
    )
    print(
        "Aucune donnée fictive créée."
    )


if __name__ == "__main__":
    main()
