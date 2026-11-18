#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 PHOENIX M1-M5 V1.2 - Reparation controlee.

Commandes :
 python x1000_moteurs.py --self-test
 python x1000_moteurs.py --dry-run
 python x1000_moteurs.py --publish

Source : data/publication_courses.json
Sortie : data/publication_moteurs.json

Aucun FIGE, archive, audit ou pronostic officiel modifie.
Aucun commit, push ou deploiement.
"""
from __future__ import annotations

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
VERSION = "X1000-M1M5-1.2"

MOTEURS = {
    "M1": "regularite des performances",
    "M2": "forme recente ponderee",
    "M3": "frequence des places",
    "M4": "progression des performances",
    "M5": "robustesse des performances",
}

MIN_HISTORY = 3
MIN_CANDIDATES = 5
MAX_HISTORY = 10
MAX_SOURCE_AGE = dt.timedelta(hours=3)
MIN_BEFORE_START = dt.timedelta(seconds=60)

TOKEN = re.compile(r"(\d{1,2}|D|A|T)([pamhsc])", re.I)


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def instant(value):
    if not isinstance(value, str):
        raise ValueError("Horodatage non textuel")

    if not re.search(r"(Z|[+-]\d{2}:\d{2})$", value):
        raise ValueError("Fuseau horaire obligatoire")

    result = dt.datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )

    if result.utcoffset() is None:
        raise ValueError("Fuseau absent")

    return result.astimezone(dt.timezone.utc)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Cle JSON dupliquee : " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("Constante JSON interdite : " + value)


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
    """Extrait uniquement les rangs explicites.

    Les incidents restent inconnus et ne sont pas
    transformes en resultats sportifs inventes.
    """
    if not isinstance(value, str):
        return []

    clean = re.sub(r"\([^)]*\)", "", value)
    clean = re.sub(r"\s+", "", clean)

    if not clean:
        return []

    matches = list(TOKEN.finditer(clean))

    if not matches:
        return []

    reconstructed = "".join(
        match.group(0) for match in matches
    )

    if reconstructed.lower() != clean.lower():
        return []

    positions = []

    for match in matches[:MAX_HISTORY]:
        rank = match.group(1)

        if not rank.isdigit():
            positions.append(None)
            continue

        number = int(rank)

        if number == 0:
            positions.append(None)
        elif 1 <= number <= 20:
            positions.append(number)
        else:
            return []

    return positions


def scores(horse):
    history = musique_positions(horse.get("musique"))

    known = [
        (index, rank)
        for index, rank in enumerate(history)
        if rank is not None
    ]

    if len(known) < MIN_HISTORY:
        return None

    ranks = [rank for _, rank in known]
    inverse = [1.0 / rank for rank in ranks]

    mean = sum(ranks) / len(ranks)
    variance = sum(
        (rank - mean) ** 2 for rank in ranks
    ) / len(ranks)

    weights = [
        1.0 / (index + 1)
        for index, _ in known
    ]

    recent = ranks[:3]
    older = ranks[3:]

    progression = None

    if len(older) >= 2:
        progression = (
            sum(older) / len(older)
            - sum(recent) / len(recent)
        )

    result = {
        "M1": sum(inverse) / len(inverse),
        "M2": sum(
            (1.0 / rank) * weight
            for (_, rank), weight in zip(known, weights)
        ) / sum(weights),
        "M3": sum(
            rank <= 3 for rank in ranks
        ) / len(ranks),
        "M4": progression,
        "M5": 1.0 / (
            1.0 + mean + math.sqrt(variance)
        ),
    }

    return {
        key: value
        for key, value in result.items()
        if value is not None and math.isfinite(value)
    }


def analyse_moteur(course, moteur):
    candidates = []

    for horse in course["partants"]:
        if horse["non_partant"]:
            continue

        values = scores(horse)

        if values is None or moteur not in values:
            continue

        candidates.append({
            "numero": horse["numero"],
            "score": values[moteur],
        })

    if len(candidates) < MIN_CANDIDATES:
        return None

    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["numero"],
        )
    )

    return {
        "positions": [
            item["numero"] for item in candidates[:5]
        ],
        "scores": [
            {
                "numero": item["numero"],
                "score": round(item["score"], 8),
            }
            for item in candidates[:5]
        ],
        "candidats_evalues": len(candidates),
    }


def coordination(decisions):
    """Consensus indicatif, sans remplacer les moteurs."""
    points = {}

    for decision in decisions.values():
        for position, numero in enumerate(
            decision["positions"]
        ):
            points[numero] = (
                points.get(numero, 0)
                + 5 - position
            )

    ordered = sorted(
        points.items(),
        key=lambda item: (-item[1], item[0]),
    )

    return {
        "statut": "CONSENSUS_EXPERIMENTAL",
        "moteurs_participants": len(decisions),
        "positions": [
            numero for numero, _ in ordered[:5]
        ],
        "methode": "Borda indicatif sur les top 5",
        "pari": None,
        "fige": None,
    }


def build(payload, raw, timestamp):
    date = validate(payload)
    collected = instant(payload["collecte_a"])

    if collected > timestamp + dt.timedelta(minutes=5):
        raise ValueError("Collecte datee dans le futur")

    if timestamp - collected > MAX_SOURCE_AGE:
        raise ValueError("Collecte PMU trop ancienne")

    seen = set()
    output = []
    diagnostics = []

    for course in payload["courses"]:
        cid = validate_course(course, date)

        if cid in seen:
            raise ValueError("Course en doublon : " + cid)

        seen.add(cid)

        depart = instant(course["depart"])

        if timestamp + MIN_BEFORE_START >= depart:
            diagnostics.append({
                "course_id": cid,
                "motif": "DEPART_TROP_PROCHE_OU_PASSE",
            })
            continue

        decisions = {}

        for moteur, method in MOTEURS.items():
            result = analyse_moteur(course, moteur)

            if result is None:
                continue

            decisions[moteur] = {
                "course_id": cid,
                "statut": "PROVISOIRE",
                "version": VERSION,
                "calcule_a": timestamp.isoformat(),
                "positions": result["positions"],
                "scores": result["scores"],
                "candidats_evalues": result[
                    "candidats_evalues"
                ],
                "justification": (
                    "Evaluation experimentale fondee "
                    "uniquement sur la musique : " + method
                ),
                "pari": None,
                "fige": None,
            }

        if not decisions:
            diagnostics.append({
                "course_id": cid,
                "motif": "DONNEES_INSUFFISANTES",
            })
            continue

        output.append({
            "id": cid,
            "depart": course["depart"],
            "statut": "ANALYSE_PROVISOIRE",
            "moteurs": decisions,
            "coordination_x1000": coordination(decisions),
        })

    return {
        "version": VERSION,
        "date": date,
        "statut": "PUBLICATION_PROVISOIRE",
        "source": "data/publication_courses.json",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "collecte_a": payload["collecte_a"],
        "publie_a": timestamp.isoformat(),
        "courses": output,
        "diagnostics": diagnostics,
        "figes_modifies": False,
        "archives_modifiees": False,
        "publication_publique": False,
    }


def atomic_replace(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=".x1000-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temp = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temp, path)
        temp = None

    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def publish(result):
    """Publication locale provisoire uniquement.

    Refuse de remplacer une sortie inconnue ou
    contenant des decisions figees.
    """
    stamp = instant(result["publie_a"])

    if abs((utc_now() - stamp).total_seconds()) > 30:
        raise ValueError("Calcul devenu trop ancien")

    current_raw = SOURCE.read_bytes()

    if hashlib.sha256(current_raw).hexdigest() != (
        result["source_sha256"]
    ):
        raise ValueError("Source PMU modifiee")

    if OUTPUT.is_symlink():
        raise ValueError("Lien symbolique de sortie interdit")

    if OUTPUT.exists():
        existing = strict_json(OUTPUT.read_bytes())

        if not isinstance(existing, dict):
            raise ValueError("Publication existante invalide")

        if existing.get("version") != VERSION:
            raise ValueError(
                "Ancienne publication conservee : "
                "migration manuelle necessaire"
            )

        if existing.get("statut") != "PUBLICATION_PROVISOIRE":
            raise ValueError(
                "Publication non provisoire protegee"
            )

        if existing.get("figes_modifies") is not False:
            raise ValueError(
                "Protection des FIGEs non verifiable"
            )

    # Nouvelle verification juste avant l'ecriture.
    for course in result["courses"]:
        if utc_now() + MIN_BEFORE_START >= instant(
            course["depart"]
        ):
            raise ValueError(
                "Depart trop proche : " + course["id"]
            )

    if hashlib.sha256(SOURCE.read_bytes()).hexdigest() != (
        result["source_sha256"]
    ):
        raise ValueError("Source changee avant publication")

    atomic_replace(OUTPUT, encode(result))


def self_test():
    assert musique_positions(
        "1p3p5p(25)2p4p"
    ) == [1, 3, 5, 2, 4]

    assert musique_positions(
        "0p1p2p3p4p"
    ) == [None, 1, 2, 3, 4]

    assert musique_positions(
        "Da1a2a3a4a"
    ) == [None, 1, 2, 3, 4]

    assert musique_positions("texte") == []

    assert scores({
        "musique": "1p2p3p4p5p"
    }) is not None

    assert scores({
        "musique": "1p2p"
    }) is None

    assert set(scores({
        "musique": "1p2p3p4p5p"
    })) == set(MOTEURS)

    sample = {
        "partants": [
            {
                "numero": i,
                "non_partant": False,
                "musique": (
                    "1p2p3p4p5p"
                    if i % 2 else "5p4p3p2p1p"
                ),
            }
            for i in range(1, 7)
        ]
    }

    decisions = {}

    for moteur in MOTEURS:
        result = analyse_moteur(sample, moteur)
        assert result is not None
        assert len(result["positions"]) == 5
        assert len(set(result["positions"])) == 5
        decisions[moteur] = result

    central = coordination(decisions)

    assert central["moteurs_participants"] == 5
    assert len(central["positions"]) == 5
    assert central["fige"] is None

    print(
        "SELF-TEST OK : M1-M5, consensus, "
        "abstention et protection des FIGEs"
    )


def main():
    parser = argparse.ArgumentParser(
        description=VERSION
    )

    modes = parser.add_mutually_exclusive_group(
        required=True
    )

    modes.add_argument(
        "--self-test", action="store_true"
    )
    modes.add_argument(
        "--dry-run", action="store_true"
    )
    modes.add_argument(
        "--publish", action="store_true"
    )

    args = parser.parse_args()

    try:
        if args.self_test:
            self_test()
            return 0

        if not SOURCE.is_file():
            raise FileNotFoundError(
                "Source PMU absente : " + str(SOURCE)
            )

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
                "PUBLICATION_PROVISOIRE"
                if args.publish else "DRY_RUN"
            ),
            "courses": len(result["courses"]),
            "decisions": sum(
                len(c["moteurs"])
                for c in result["courses"]
            ),
            "diagnostics": result["diagnostics"],
            "source_sha256": result["source_sha256"],
            "figes_modifies": False,
            "archives_modifiees": False,
        }, indent=2, ensure_ascii=False))

        return 0

    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AssertionError,
    ) as exc:
        print("ERREUR : " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
