#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 PHOENIX M1–M5 V1.1 — calcul expérimental, publication contrôlée.

python x1000_moteurs.py --self-test
python x1000_moteurs.py --dry-run
python x1000_moteurs.py --publish

Ne modifie jamais les FIGÉs existants. La publication n'est pas transactionnelle
entre GitHub et le système de fichiers : un seul processus éditeur est requis.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path

from analyse_hippique import validate, validate_course

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SOURCE = DATA / "publication_courses.json"
OUTPUT = DATA / "publication_moteurs.json"
ARCHIVES = DATA / "figes_moteurs"
VERSION = "X1000-M1M5-1.1"

MOTEURS = {
    "M1": "regularite globale",
    "M2": "forme recente ponderee",
    "M3": "frequence des places",
    "M4": "progression recente",
    "M5": "robustesse des performances",
}

COURSE_ID = re.compile(
    r"\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*"
)
MUSIQUE_TOKEN = re.compile(
    r"(\d{1,2}|[A-Za-z])([pamhsc])", re.I
)

MIN_HISTORY = 5
MIN_CANDIDATES = 5
MIN_BEFORE_START = dt.timedelta(seconds=60)
MAX_SOURCE_AGE = dt.timedelta(hours=3)


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def instant(value):
    if (
        not isinstance(value, str)
        or not re.search(
            r"(?:Z|[+-]\d{2}:\d{2})$", value
        )
    ):
        raise ValueError(
            "Horodatage ISO avec fuseau requis"
        )

    parsed = dt.datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if parsed.utcoffset() is None:
        raise ValueError(
            "Fuseau horaire manquant"
        )

    return parsed.astimezone(dt.timezone.utc)


def unique_keys(pairs):
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


def strict_json(raw):
    return json.loads(
        raw,
        object_pairs_hook=unique_keys,
        parse_constant=reject_constant,
    )


def encode(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def musique_positions(value):
    """Retenir les rangs explicites sans inventer les incidents."""
    if (
        not isinstance(value, str)
        or not value.strip()
    ):
        return []

    clean = re.sub(
        r"\([^)]*\)", "", value
    ).replace(" ", "")

    tokens = list(
        MUSIQUE_TOKEN.finditer(clean)
    )

    if (
        not tokens
        or "".join(
            m.group(0) for m in tokens
        ).lower() != clean.lower()
    ):
        return []

    positions = []

    for match in tokens[:10]:
        rank = match.group(1)

        if not rank.isdigit():
            return []

        number = int(rank)

        if number < 1 or number > 20:
            return []

        positions.append(number)

    return positions


def scores(horse):
    positions = musique_positions(
        horse.get("musique")
    )

    if len(positions) < MIN_HISTORY:
        return None

    inv = [1 / p for p in positions]

    weights = [
        1 / (i + 1)
        for i in range(len(positions))
    ]

    recent = positions[:3]
    older = positions[3:]

    mean = sum(positions) / len(positions)

    variance = sum(
        (p - mean) ** 2
        for p in positions
    ) / len(positions)

    return {
        "M1": sum(inv) / len(inv),

        "M2": sum(
            v * w
            for v, w in zip(inv, weights)
        ) / sum(weights),

        "M3": sum(
            p <= 3 for p in positions
        ) / len(positions),

        "M4": (
            sum(older) / len(older)
            - sum(recent) / len(recent)
        ),

        "M5": 1 / (
            1 + mean + math.sqrt(variance)
        ),
    }


def analyse_moteur(course, moteur):
    ranked = []

    for horse in course["partants"]:
        if horse["non_partant"]:
            continue

        result = scores(horse)

        if result is None:
            continue

        score = result[moteur]

        if math.isfinite(score):
            ranked.append(
                (score, horse["numero"])
            )

    if len(ranked) < MIN_CANDIDATES:
        return None

    ranked.sort(
        key=lambda item: (
            -item[0],
            item[1],
        )
    )

    return [
        numero
        for _, numero in ranked[:5]
    ]


def build(payload, raw, timestamp):
    date = validate(payload)
    collected = instant(
        payload["collecte_a"]
    )

    if (
        collected > timestamp + dt.timedelta(minutes=5)
        or timestamp - collected > MAX_SOURCE_AGE
    ):
        raise ValueError(
            "Collecte PMU trop ancienne ou datee dans le futur"
        )

    seen = set()
    output = []

    for course in payload["courses"]:
        cid = validate_course(
            course, date
        )

        if cid in seen:
            raise ValueError(
                f"Course en doublon : {cid}"
            )

        seen.add(cid)

        if (
            timestamp + MIN_BEFORE_START
            >= instant(course["depart"])
        ):
            continue

        decisions = {}

        for moteur, method in MOTEURS.items():
            positions = analyse_moteur(
                course, moteur
            )

            if positions is None:
                continue

            decisions[moteur] = {
                "course_id": cid,
                "statut": "FIGE",
                "version": VERSION,
                "fige_a": timestamp.isoformat(),
                "positions": positions,
                "justification": (
                    "Classement experimental issu "
                    "uniquement de la musique; "
                    "methode : " + method
                ),
            }

        if decisions:
            output.append({
                "id": cid,
                "moteurs": decisions,
            })

    return {
        "version": VERSION,
        "statut": "PUBLICATION_MOTEURS",
        "source": "data/publication_courses.json",
        "source_sha256": hashlib.sha256(
            raw
        ).hexdigest(),
        "publie_a": timestamp.isoformat(),
        "courses": output,
    }


def archive_path(course_id, moteur):
    if (
        not COURSE_ID.fullmatch(course_id)
        or moteur not in MOTEURS
    ):
        raise ValueError(
            "Identifiant de FIGE invalide"
        )

    return (
        ARCHIVES
        / course_id
        / f"{moteur}.json"
    )


def exclusive_write(path, raw):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )

    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_replace(path, raw):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=".x1000-",
            delete=False,
        ) as stream:
            temp = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temp, path)

    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def publish(result):
    stamp = instant(
        result["publie_a"]
    )

    if abs(
        (utc_now() - stamp).total_seconds()
    ) > 30:
        raise ValueError(
            "Decision trop ancienne pour publication"
        )

    if hashlib.sha256(
        SOURCE.read_bytes()
    ).hexdigest() != result["source_sha256"]:
        raise ValueError(
            "La source PMU a change depuis le calcul"
        )

    if OUTPUT.exists():
        raise FileExistsError(
            "Publication existante : revision manuelle requise"
        )

    jobs = []

    for course in result["courses"]:
        for moteur, decision in course["moteurs"].items():
            if (
                decision["fige_a"] != result["publie_a"]
                or decision["course_id"] != course["id"]
            ):
                raise ValueError(
                    "Decision incoherente"
                )

            source_course = next(
                (
                    c
                    for c in strict_json(
                        SOURCE.read_bytes()
                    )["courses"]
                    if c["id"] == course["id"]
                ),
                None,
            )

            if (
                source_course is None
                or utc_now() + MIN_BEFORE_START
                >= instant(source_course["depart"])
            ):
                raise ValueError(
                    "Depart trop proche : FIGE interdit"
                )

            path = archive_path(
                course["id"], moteur
            )

            if (
                path.exists()
                or path.is_symlink()
            ):
                raise FileExistsError(
                    f"FIGE existant : {path}"
                )

            jobs.append(
                (path, encode(decision))
            )

    for path, raw in jobs:
        exclusive_write(path, raw)

    if hashlib.sha256(
        SOURCE.read_bytes()
    ).hexdigest() != result["source_sha256"]:
        raise ValueError(
            "Source PMU modifiee pendant la publication"
        )

    exclusive_write(
        OUTPUT,
        encode(result),
    )


def self_test():
    assert musique_positions(
        "1p3p5p(25)2p4p"
    ) == [1, 3, 5, 2, 4]

    assert musique_positions(
        "0p1p2p3p4p"
    ) == []

    assert musique_positions(
        "Da1a2a3a4a"
    ) == []

    assert musique_positions(
        "1p texte 2p"
    ) == []

    assert scores({
        "musique": "1p2p3p4p5p"
    }) is not None

    assert scores({
        "musique": "1p2p"
    }) is None

    assert set(scores({
        "musique": "1p2p3p4p5p"
    })) == set(MOTEURS)

    assert len({
        "M1", "M2", "M3", "M4", "M5"
    }) == 5

    print(
        "SELF-TEST OK : cinq methodes, "
        "abstention et musique"
    )


def main():
    parser = argparse.ArgumentParser(
        description=VERSION
    )

    modes = parser.add_mutually_exclusive_group(
        required=True
    )

    modes.add_argument(
        "--self-test",
        action="store_true",
    )

    modes.add_argument(
        "--dry-run",
        action="store_true",
    )

    modes.add_argument(
        "--publish",
        action="store_true",
    )

    args = parser.parse_args()

    try:
        if args.self_test:
            self_test()
            return 0

        raw = SOURCE.read_bytes()
        payload = strict_json(raw)

        result = build(
            payload,
            raw,
            utc_now(),
        )

        if args.publish:
            publish(result)

        print(json.dumps({
            "version": VERSION,
            "mode": (
                "PUBLICATION"
                if args.publish
                else "DRY_RUN"
            ),
            "courses": len(
                result["courses"]
            ),
            "decisions": sum(
                len(c["moteurs"])
                for c in result["courses"]
            ),
            "source_sha256": result[
                "source_sha256"
            ],
        }, indent=2, ensure_ascii=False))

        return 0

    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AssertionError,
    ) as exc:
        print(
            f"ERREUR : {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
