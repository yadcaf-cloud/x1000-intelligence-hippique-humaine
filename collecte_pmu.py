#!/usr/bin/env python3
"""X1000 - Collecte PMU France renforcee.

Lecture seule de PMU TurfInfo.
Ratissage large, validation des courses et partants.
Aucun pronostic, FIGE, commit ou deploiement.
"""

import json
import os
import re
import sys
import time
import tempfile
import urllib.error
import urllib.request

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = (
    "https://online.turfinfo.api.pmu.fr"
    "/rest/client/1/programme"
)

PARIS = ZoneInfo("Europe/Paris")
TODAY = datetime.now(PARIS)
DATE = TODAY.strftime("%d%m%Y")
ISO_DATE = TODAY.date().isoformat()

OUT = Path(os.environ.get("X1000_SORTIE", "sortie"))

TIMEOUT = 18
RETRIES = 3
MAX_BYTES = 8_000_000
MAX_DIAGNOSTICS = 1000


def diagnostic(message):
    print("DIAGNOSTIC:", message, flush=True)


def erreur(message):
    print("ERREUR:", message, file=sys.stderr, flush=True)


def get(path):
    """Lecture HTTP PMU avec tentatives limitees."""
    url = BASE + path
    derniere = None

    for tentative in range(1, RETRIES + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "X1000-PMU-Audit/3.0",
                },
                method="GET",
            )

            with urllib.request.urlopen(
                request, timeout=TIMEOUT
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"HTTP_{response.status}"
                    )

                if response.geturl() != url:
                    raise RuntimeError(
                        "REDIRECTION_INATTENDUE"
                    )

                raw = response.read(MAX_BYTES + 1)

            if len(raw) > MAX_BYTES:
                raise RuntimeError(
                    "REPONSE_TROP_VOLUMINEUSE"
                )

            data = json.loads(
                raw.decode("utf-8-sig")
            )

            if not isinstance(data, (dict, list)):
                raise RuntimeError(
                    "FORMAT_JSON_INATTENDU"
                )

            return data

        except urllib.error.HTTPError as exc:
            derniere = f"HTTP_{exc.code}"

            # Une erreur 404 n'est pas transitoire.
            if exc.code in (400, 401, 403, 404):
                break

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            ValueError,
            UnicodeError,
            RuntimeError,
        ) as exc:
            derniere = (
                f"{type(exc).__name__}: {exc}"
            )

        if tentative < RETRIES:
            time.sleep(tentative)

    raise RuntimeError(
        f"ECHEC_SOURCE_PMU {path}: {derniere}"
    )


def pick(obj, *keys):
    if not isinstance(obj, dict):
        return None

    for key in keys:
        value = obj.get(key)
        if value is not None and value != "":
            return value

    return None


def label(value):
    if isinstance(value, dict):
        value = pick(
            value,
            "libelleCourt",
            "libelleLong",
            "nom",
            "libelle",
            "code",
        )

    if isinstance(value, (dict, list)):
        return ""

    return str(value or "").strip()


def positive(value):
    if isinstance(value, bool):
        return None

    if isinstance(value, int) and value > 0:
        return value

    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"[1-9][0-9]*", value):
            return int(value)

    return None


def normaliser_pays(value):
    text = label(value).upper().strip()

    if text in (
        "FR", "FRA", "FRANCE",
        "FRANCAISE", "FRANÇAISE",
    ):
        return "FR"

    return text


def pays_france(reunion):
    """Accepter uniquement une France explicite."""
    hip = reunion.get("hippodrome")

    sources = [reunion]

    if isinstance(hip, dict):
        sources.append(hip)

    valeurs = []

    for source in sources:
        for key in (
            "pays",
            "codePays",
            "nomPays",
            "country",
            "countryCode",
            "codePaysHippodrome",
        ):
            value = source.get(key)

            if value is not None:
                normalise = normaliser_pays(value)

                if normalise:
                    valeurs.append(normalise)

    return bool(valeurs) and all(
        value == "FR" for value in valeurs
    )


def heure_depart(value):
    """Convertir un horaire en ISO Europe/Paris."""
    if isinstance(value, bool):
        return None

    try:
        if isinstance(value, (int, float)):
            if not 0 < value < 10**16:
                return None

            timestamp = (
                value / 1000
                if value >= 10**11
                else value
            )

            dt = datetime.fromtimestamp(
                timestamp, PARIS
            )

        elif isinstance(value, str):
            value = value.strip()

            if re.fullmatch(
                r"\d{2}:\d{2}(:\d{2})?", value
            ):
                dt = datetime.fromisoformat(
                    ISO_DATE + "T" + value
                ).replace(tzinfo=PARIS)

            else:
                dt = datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )

                if dt.tzinfo is None:
                    return None

                dt = dt.astimezone(PARIS)

        else:
            return None

        if dt.date().isoformat() != ISO_DATE:
            return None

        return dt.isoformat()

    except (ValueError, OverflowError, OSError):
        return None


def non_partant(value):
    """Reconnaissance prudente des non-partants."""
    if value is True:
        return True

    if value is False or value is None:
        return False

    if isinstance(value, (str, int)):
        text = str(value).strip().lower()

        if text in ("1", "true", "oui", "np"):
            return True

        if text in ("0", "false", "non", ""):
            return False

    return None


def extraire_partants(raw):
    if isinstance(raw, list):
        return raw

    return pick(
        raw,
        "participants",
        "partants",
        "listeParticipants",
    )


def traiter_partants(participants):
    if not isinstance(participants, list):
        raise ValueError("LISTE_PARTANTS_INVALIDE")

    if not participants:
        raise ValueError("LISTE_PARTANTS_VIDE")

    chevaux = []
    numeros = set()

    for participant in participants:
        if not isinstance(participant, dict):
            raise ValueError("PARTANT_MAL_FORME")

        numero = positive(
            pick(
                participant,
                "numPmu",
                "numero",
                "num",
            )
        )

        nom = label(
            pick(
                participant,
                "nom",
                "nomCheval",
                "cheval",
            )
        )

        if not numero or not nom:
            raise ValueError(
                "NUMERO_OU_NOM_PARTANT_ABSENT"
            )

        if numero in numeros:
            raise ValueError(
                f"DOUBLON_PARTANT_{numero}"
            )

        numeros.add(numero)

        np = non_partant(
            pick(
                participant,
                "nonPartant",
                "non_partant",
            )
        )

        if np is None:
            raise ValueError(
                f"STATUT_PARTANT_INCONNU_{numero}"
            )

        chevaux.append({
            "numero": numero,
            "nom": nom,
            "non_partant": np,
        })

    if not any(
        not cheval["non_partant"]
        for cheval in chevaux
    ):
        raise ValueError(
            "AUCUN_PARTANT_ACTIF"
        )

    return sorted(
        chevaux,
        key=lambda cheval: cheval["numero"]
    )


def collecter():
    diagnostic(
        f"DEBUT_COLLECTE_PMU date={ISO_DATE}"
    )

    programme_brut = get("/" + DATE)

    programme = (
        pick(programme_brut, "programme")
        or programme_brut
    )

    reunions = pick(programme, "reunions")

    if not isinstance(reunions, list):
        raise RuntimeError(
            "REUNIONS_ABSENTES_OU_FORMAT_INCONNU"
        )

    if not reunions:
        raise RuntimeError(
            "PROGRAMME_PMU_SANS_REUNION"
        )

    courses_valides = []
    diagnostics = []
    identifiants = set()

    reunions_france = 0
    courses_examinees = 0

    # Aucune limite artificielle de 30 reunions.
    for reunion in reunions:
        if not isinstance(reunion, dict):
            diagnostics.append(
                "REUNION_MAL_FORMEE"
            )
            continue

        if not pays_france(reunion):
            continue

        reunions_france += 1

        nr = positive(
            pick(
                reunion,
                "numOfficiel",
                "numeroReunion",
                "numReunion",
                "numero",
            )
        )

        hippodrome = label(
            pick(
                reunion,
                "hippodrome",
                "nomHippodrome",
            )
        )

        courses = pick(
            reunion,
            "courses",
            "listeCourses",
        )

        if (
            not nr
            or not hippodrome
            or not isinstance(courses, list)
        ):
            diagnostics.append(
                "REUNION_FRANCAISE_INCOMPLETE"
            )
            continue

        diagnostic(
            f"REUNION_FRANCE R{nr} {hippodrome}"
        )

        # Aucune limite artificielle de 40 courses.
        for course in courses:
            courses_examinees += 1

            if not isinstance(course, dict):
                diagnostics.append(
                    f"R{nr}: COURSE_MAL_FORMEE"
                )
                continue

            nc = positive(
                pick(
                    course,
                    "numOrdre",
                    "numeroCourse",
                    "numCourse",
                    "numero",
                )
            )

            if not nc:
                diagnostics.append(
                    f"R{nr}: NUMERO_COURSE_ABSENT"
                )
                continue

            cid = f"{ISO_DATE}-R{nr}-C{nc}"

            if cid in identifiants:
                diagnostics.append(
                    f"{cid}: COURSE_EN_DOUBLE"
                )
                continue

            identifiants.add(cid)

            depart = heure_depart(
                pick(
                    course,
                    "heureDepart",
                    "heureDepartInitiale",
                    "heureDepartPrevue",
                    "heure_depart",
                    "depart",
                )
            )

            if not depart:
                diagnostics.append(
                    f"{cid}: HORAIRE_INVALIDE"
                )
                continue

            try:
                participants = extraire_partants(
                    get(
                        f"/{DATE}/R{nr}/C{nc}/participants"
                    )
                )

                chevaux = traiter_partants(
                    participants
                )

            except (RuntimeError, ValueError) as exc:
                diagnostics.append(
                    f"{cid}: {exc}"
                )
                continue

            courses_valides.append({
                "course_id": cid,
                "reunion": f"R{nr}",
                "course": f"C{nc}",
                "hippodrome": hippodrome,
                "date": ISO_DATE,
                "depart": depart,
                "nom_course": label(
                    pick(
                        course,
                        "libelle",
                        "nomCourse",
                    )
                ),
                "partants": chevaux,
                "source": "PMU TurfInfo",
                "pays_verifie": "FR",
            })

            diagnostic(
                f"COURSE_VALIDEE {cid} "
                f"partants={len(chevaux)}"
            )

    courses_valides.sort(
        key=lambda c: (
            c["depart"],
            c["course_id"],
        )
    )

    statistiques = {
        "reunions_programme": len(reunions),
        "reunions_france": reunions_france,
        "courses_examinees": courses_examinees,
        "courses_validees": len(courses_valides),
        "anomalies": len(diagnostics),
    }

    return (
        courses_valides,
        diagnostics,
        statistiques,
    )


def enregistrer_atomiquement(path, document):
    """Ne jamais laisser un JSON partiellement ecrit."""
    path.parent.mkdir(
        parents=True, exist_ok=True
    )

    contenu = json.dumps(
        document,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    temporaire = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".x1000-",
            suffix=".tmp",
            delete=False,
        ) as fichier:
            temporaire = Path(fichier.name)
            fichier.write(contenu)
            fichier.flush()
            os.fsync(fichier.fileno())

        os.replace(temporaire, path)

    finally:
        if (
            temporaire is not None
            and temporaire.exists()
        ):
            temporaire.unlink()


def main():
    courses, diagnostics, stats = collecter()

    if not courses:
        raise RuntimeError(
            "AUCUNE_COURSE_FRANCAISE_VALIDEE"
        )

    document = {
        "schema": "x1000-pmu-france-v1",
        "date_publication": datetime.now(
            timezone.utc
        ).isoformat(),
        "date_pmu": ISO_DATE,
        "source": "PMU TurfInfo",
        "statut": "DONNEES_VERIFIEES",
        "courses": courses,
        "diagnostics": diagnostics[:MAX_DIAGNOSTICS],
        "statistiques": stats,
    }

    destination = (
        OUT / "publication_courses.json"
    )

    enregistrer_atomiquement(
        destination, document
    )

    print("\n=== X1000 BILAN COLLECTE ===")
    print(json.dumps(
        stats,
        ensure_ascii=False,
        indent=2,
    ))

    for message in diagnostics[:50]:
        diagnostic(message)

    print("PUBLICATION_COURSES_OK")
    print("FICHIER:", destination)
    print("AUCUN_PRONOSTIC_CREE")
    print("AUCUN_FIGE_MODIFIE")
    print("AUCUNE_ARCHIVE_MODIFIEE")


if __name__ == "__main__":
    try:
        main()

    except Exception as exc:
        erreur(
            f"ECHEC_COLLECTE_PMU: {exc}"
        )
        sys.exit(1)
