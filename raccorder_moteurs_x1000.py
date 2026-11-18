#!/usr/bin/env python3
"""X1000 - Raccordement controle des decisions M1-M5 et X1000.

Sources existantes uniquement. Aucun calcul de pronostic, aucun FIGE,
aucune modification d'archives, aucun commit/push/deploiement.
La publication est creee exclusivement si elle n'existe pas deja.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)
TODAY = NOW.date().isoformat()
COURSES = ROOT / "sortie/publication_courses.json"
ENTREES = Path(os.environ.get("X1000_ENTREES_MOTEURS", str(ROOT / "entrees_moteurs"))).resolve()
DEST = ROOT / "sortie/publication_moteurs.json"
MOTEURS = ("M1", "M2", "M3", "M4", "M5")
SOURCES = (*MOTEURS, "X1000")
ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-R[1-9]\d*-C[1-9]\d*$")
TOLERANCE = timedelta(minutes=5)


def exiger(condition, message):
    if not condition:
        raise ValueError(message)


def lire(path):
    exiger(path.is_file(), f"SOURCE_ABSENTE: {path}")
    exiger(path.stat().st_size > 0, f"SOURCE_VIDE: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"JSON_INVALIDE: {path}: {exc}") from exc
    exiger(isinstance(document, dict), f"OBJET_ATTENDU: {path}")
    return document


def date_heure(value, contexte, future_interdite=False):
    exiger(isinstance(value, str) and bool(value.strip()), f"DATE_ABSENTE: {contexte}")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"DATE_INVALIDE: {contexte}") from exc
    exiger(dt.tzinfo is not None and dt.utcoffset() is not None,
           f"FUSEAU_ABSENT: {contexte}")
    dt = dt.astimezone(PARIS)
    exiger(dt.date().isoformat() == TODAY, f"DATE_HORS_JOUR: {contexte}")
    if future_interdite:
        exiger(dt <= NOW + TOLERANCE, f"DATE_FUTURE: {contexte}")
    return dt


def verifier_courses():
    doc = lire(COURSES)
    exiger(doc.get("schema") == "x1000-pmu-france-v1", "SCHEMA_PMU_INVALIDE")
    exiger(doc.get("date_pmu") == TODAY, "DATE_PMU_OBSOLETE")
    exiger(doc.get("source") == "PMU TurfInfo", "SOURCE_PMU_INVALIDE")
    liste = doc.get("courses")
    exiger(isinstance(liste, list) and bool(liste), "AUCUNE_COURSE_PMU")
    resultat = {}
    for course in liste:
        exiger(isinstance(course, dict), "COURSE_MAL_FORMEE")
        cid = course.get("course_id")
        exiger(isinstance(cid, str) and ID_RE.fullmatch(cid) is not None,
               f"COURSE_ID_INVALIDE: {cid}")
        exiger(cid.startswith(TODAY + "-") and cid not in resultat,
               f"COURSE_DATE_OU_DOUBLON: {cid}")
        exiger(course.get("pays_verifie") == "FR", f"HORS_FRANCE: {cid}")
        hippodrome = course.get("hippodrome")
        exiger(isinstance(hippodrome, str) and hippodrome.strip(),
               f"HIPPODROME_ABSENT: {cid}")
        depart = date_heure(course.get("depart"), f"DEPART {cid}")
        partants = course.get("partants")
        exiger(isinstance(partants, list) and bool(partants), f"PARTANTS_ABSENTS: {cid}")
        numeros, actifs = set(), set()
        for cheval in partants:
            exiger(isinstance(cheval, dict), f"PARTANT_INVALIDE: {cid}")
            numero = cheval.get("numero")
            exiger(type(numero) is int and numero > 0 and numero not in numeros,
                   f"NUMERO_PARTANT_INVALIDE: {cid}")
            numeros.add(numero)
            exiger(isinstance(cheval.get("nom"), str) and cheval["nom"].strip(),
                   f"NOM_PARTANT_ABSENT: {cid}")
            exiger(type(cheval.get("non_partant")) is bool,
                   f"STATUT_PARTANT_INVALIDE: {cid}")
            if not cheval["non_partant"]:
                actifs.add(numero)
        resultat[cid] = {"hippodrome": hippodrome.strip(), "depart": depart,
                         "actifs": actifs}
    return resultat


def lire_sorties(nom):
    doc = lire(ENTREES / f"{nom}.json")
    exiger(doc.get("moteur") == nom, f"IDENTITE_SOURCE_INVALIDE: {nom}")
    decisions = doc.get("decisions")
    exiger(isinstance(decisions, list), f"DECISIONS_ABSENTES: {nom}")
    resultat = {}
    for decision in decisions:
        exiger(isinstance(decision, dict), f"DECISION_MAL_FORMEE: {nom}")
        cid = decision.get("course_id")
        exiger(isinstance(cid, str) and ID_RE.fullmatch(cid) is not None,
               f"COURSE_ID_INVALIDE: {nom}: {cid}")
        exiger(cid not in resultat, f"DECISION_DUPLIQUEE: {nom}: {cid}")
        resultat[cid] = decision
    return resultat


def verifier_decision(decision, cid, course, nom):
    contexte = f"{nom} {cid}"
    exiger(decision.get("course_id") == cid, f"COURSE_INCOHERENTE: {contexte}")
    classement = decision.get("classement")
    exiger(isinstance(classement, list) and len(classement) == 5,
           f"CLASSEMENT_NON_CINQ: {contexte}")
    exiger(all(type(n) is int and n > 0 for n in classement),
           f"NUMERO_INVALIDE: {contexte}")
    exiger(len(set(classement)) == 5, f"DOUBLON_CLASSEMENT: {contexte}")
    exiger(set(classement).issubset(course["actifs"]),
           f"CHEVAL_NON_PARTANT: {contexte}")
    analyse = decision.get("analyse")
    exiger(isinstance(analyse, str) and len(analyse.strip()) >= 30,
           f"ANALYSE_INSUFFISANTE: {contexte}")
    source = decision.get("source")
    exiger(isinstance(source, str) and source.strip(),
           f"PROVENANCE_ABSENTE: {contexte}")
    date = date_heure(decision.get("date"), contexte, future_interdite=True)
    exiger(date.astimezone(timezone.utc) < course["depart"].astimezone(timezone.utc),
           f"DECISION_APRES_DEPART: {contexte}")
    statut = decision.get("statut")
    exiger(isinstance(statut, str) and statut.strip(), f"STATUT_ABSENT: {contexte}")
    pari = decision.get("pari")
    exiger(pari is None or isinstance(pari, str), f"PARI_INVALIDE: {contexte}")
    return {
        "classement": classement,
        "analyse": analyse.strip(),
        "justification": analyse.strip(),
        "source": source.strip(),
        "date": date.isoformat(),
        "statut": statut.strip(),
        "pari": pari.strip() if pari else None,
    }


def construire():
    courses = verifier_courses()
    sources = {nom: lire_sorties(nom) for nom in SOURCES}
    ids = set.intersection(*(set(decisions) for decisions in sources.values()))
    ids &= set(courses)
    exiger(bool(ids), "AUCUNE_COURSE_AVEC_SIX_DECISIONS")
    for cid in sorted(set.union(*(set(d) for d in sources.values())) - ids):
        print("COURSE_NON_PUBLIEE_SOURCES_INCOMPLETES_OU_NON_VERIFIEES:", cid)
    publications = []
    for cid in sorted(ids):
        course = courses[cid]
        if len(course["actifs"]) < 5:
            print("COURSE_NON_PUBLIEE_MOINS_DE_CINQ_PARTANTS:", cid)
            continue
        moteurs = {nom: verifier_decision(sources[nom][cid], cid, course, nom)
                   for nom in MOTEURS}
        arbitre = verifier_decision(sources["X1000"][cid], cid, course, "X1000")
        publications.append({
            "course_id": cid,
            "hippodrome": course["hippodrome"],
            "depart": course["depart"].isoformat(),
            "moteurs": moteurs,
            "arbitre_x1000": arbitre,
        })
    exiger(bool(publications), "AUCUNE_PUBLICATION_ELIGIBLE")
    publie_a = datetime.now(PARIS)
    for item in publications:
        for nom, decision in (*item["moteurs"].items(), ("X1000", item["arbitre_x1000"])):
            dt = datetime.fromisoformat(decision["date"])
            exiger(dt <= publie_a + TOLERANCE,
                   f"DECISION_POSTERIEURE_PUBLICATION: {nom} {item['course_id']}")
    return {
        "schema": "x1000-moteurs-v1",
        "date_pmu": TODAY,
        "publie_a": publie_a.isoformat(),
        "source": "Sorties M1-M5 et X1000 fournies",
        "courses": publications,
        "arbitre_x1000": None,
    }


def enregistrer(document):
    DEST.parent.mkdir(parents=True, exist_ok=True)
    exiger(not DEST.exists(), f"DESTINATION_EXISTANTE: {DEST}")
    contenu = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    fd, temporaire = tempfile.mkstemp(prefix=".x1000-moteurs-", suffix=".tmp",
                                     dir=str(DEST.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fichier:
            fichier.write(contenu)
        os.link(temporaire, DEST)
    finally:
        Path(temporaire).unlink(missing_ok=True)


def main():
    print("=== X1000 RACCORDEMENT M1-M5 ===")
    print("DATE:", TODAY)
    print("ENTREES:", ENTREES)
    print("DESTINATION:", DEST)
    document = construire()
    enregistrer(document)
    print("COURSES_PUBLIEES:", len(document["courses"]))
    print("STRUCTURE_ET_CHRONOLOGIE_VALIDEES")
    print("PROVENANCE_DECLAREE_NON_AUTHENTIFIEE")
    print("AUCUN_PRONOSTIC_INVENTE")
    print("AUCUN_FIGE_OU_ARCHIVE_MODIFIE")
    print("AUCUN_COMMIT_PUSH_DEPLOIEMENT")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print("ECHEC_RACCORDEMENT:", exc, file=sys.stderr)
        sys.exit(1)
