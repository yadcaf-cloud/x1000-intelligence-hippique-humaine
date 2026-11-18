#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X1000 - Diagnostic controle M1-M5 et coordination V1.1.

Audit statique des cinq moteurs et des deux scripts centraux.
Autotests des moteurs en processus Python isoles.

Aucun pronostic, FIGE, archive ou publication modifie
par le code de diagnostic lui-meme.

Les autotests executent cependant du code des moteurs :
leur absence d'effets secondaires n'est pas garantie.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MOTEURS = ("m1", "m2", "m3", "m4", "m5")

COORDINATEURS = (
    "raccorder_moteurs_x1000.py",
    "verifier_raccordement_moteurs.py",
)

FONCTIONS = (
    "analyser_course",
    "calculer_score",
    "self_test",
)


def analyser_fichier(path):
    if not path.is_file():
        raise ValueError(f"FICHIER_ABSENT: {path}")

    contenu = path.read_text(encoding="utf-8")

    if not contenu.strip():
        raise ValueError(f"FICHIER_VIDE: {path}")

    return ast.parse(contenu, filename=str(path))


def fonctions_definies(arbre):
    return {
        noeud.name
        for noeud in arbre.body
        if isinstance(
            noeud,
            (ast.FunctionDef, ast.AsyncFunctionDef),
        )
    }


def examiner_imports(arbre):
    imports = set()

    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                imports.add(alias.name)

        elif isinstance(noeud, ast.ImportFrom):
            module = noeud.module or ""
            imports.add(module)

    return sorted(imports)


def examiner_appels(arbre):
    appels = set()

    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.Call):
            continue

        fonction = noeud.func

        if isinstance(fonction, ast.Name):
            appels.add(fonction.id)

        elif isinstance(fonction, ast.Attribute):
            appels.add(fonction.attr)

    return sorted(appels)


def analyser_coordination():
    resultats = {}

    for nom in COORDINATEURS:
        chemin = ROOT / nom

        etat = {
            "present": chemin.is_file(),
            "syntaxe_valide": False,
            "imports_moteurs": [],
            "appel_analyser_course_detecte": False,
            "raccordement_direct_prouve": False,
        }

        try:
            arbre = analyser_fichier(chemin)

            etat["syntaxe_valide"] = True

            imports = examiner_imports(arbre)
            appels = examiner_appels(arbre)

            etat["imports_moteurs"] = [
                element
                for element in imports
                if element == "moteurs"
                or element.startswith("moteurs.")
            ]

            etat["appel_analyser_course_detecte"] = (
                "analyser_course" in appels
            )

            # Un examen AST ne prouve pas la transmission
            # reelle des donnees entre les composants.

        except (
            OSError,
            UnicodeError,
            SyntaxError,
            ValueError,
        ) as exc:
            etat["erreur"] = str(exc)

        resultats[nom] = etat

    return resultats


def lancer_autotest(chemin):
    environnement = os.environ.copy()

    environnement["PYTHONDONTWRITEBYTECODE"] = "1"
    environnement["PYTHONNOUSERSITE"] = "1"
    environnement["PYTHONUTF8"] = "1"

    processus = subprocess.run(
        [
            sys.executable,
            "-B",
            "-I",
            str(chemin),
            "--self-test",
        ],
        cwd=ROOT,
        env=environnement,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    return {
        "reussi": processus.returncode == 0,
        "code_retour": processus.returncode,
        "sortie": processus.stdout.strip()[:1000],
        "erreur": processus.stderr.strip()[:1000],
    }


def verifier_moteurs(executer_autotests=False):
    resultats = {}

    for nom in MOTEURS:
        chemin = ROOT / "moteurs" / f"{nom}.py"

        etat = {
            "fichier_present": chemin.is_file(),
            "syntaxe_valide": False,
            "interface_valide": False,
            "autotest_execute": False,
            "autotest_reussi": None,
        }

        try:
            arbre = analyser_fichier(chemin)

            etat["syntaxe_valide"] = True

            fonctions = fonctions_definies(arbre)

            manquantes = [
                fonction
                for fonction in FONCTIONS
                if fonction not in fonctions
            ]

            etat["interface_valide"] = not manquantes
            etat["fonctions_manquantes"] = manquantes

            if executer_autotests and not manquantes:
                etat["autotest_execute"] = True

                test = lancer_autotest(chemin)

                etat["autotest_reussi"] = test["reussi"]
                etat["code_retour"] = test["code_retour"]

                if test["sortie"]:
                    etat["sortie_autotest"] = test["sortie"]

                if test["erreur"]:
                    etat["erreur_autotest"] = test["erreur"]

        except (
            OSError,
            UnicodeError,
            SyntaxError,
            ValueError,
            subprocess.TimeoutExpired,
        ) as exc:
            etat["erreur"] = str(exc)

            if etat["autotest_execute"]:
                etat["autotest_reussi"] = False

        resultats[nom.upper()] = etat

    return resultats


def main():
    parser = argparse.ArgumentParser(
        description="X1000 - Diagnostic controle M1-M5"
    )

    parser.add_argument(
        "--self-tests",
        action="store_true",
        help="Executer les autotests des cinq moteurs",
    )

    args = parser.parse_args()

    moteurs = verifier_moteurs(
        executer_autotests=args.self_tests
    )

    coordination = analyser_coordination()

    moteurs_structure_ok = all(
        etat["fichier_present"]
        and etat["syntaxe_valide"]
        and etat["interface_valide"]
        for etat in moteurs.values()
    )

    coordination_structure_ok = all(
        etat["present"]
        and etat["syntaxe_valide"]
        for etat in coordination.values()
    )

    if args.self_tests:
        autotests_ok = all(
            etat["autotest_reussi"] is True
            for etat in moteurs.values()
        )
    else:
        autotests_ok = None

    structure_ok = (
        moteurs_structure_ok
        and coordination_structure_ok
    )

    controles_ok = (
        structure_ok
        and (autotests_ok is not False)
    )

    resultat = {
        "diagnostic": "X1000_M1_M5_V1.1",
        "mode": (
            "STRUCTURE_ET_AUTOTESTS"
            if args.self_tests
            else "STATIQUE_SEULEMENT"
        ),
        "moteurs": moteurs,
        "coordination": coordination,
        "moteurs_structure_ok": moteurs_structure_ok,
        "coordination_structure_ok": (
            coordination_structure_ok
        ),
        "autotests_ok": autotests_ok,
        "controles_ok": controles_ok,
        "raccordement_reel_certifie": False,
        "donnees_pmu_verifiees": False,
        "publication_effectuee": False,
        "fige_modifie_par_diagnostic": False,
    }

    print(
        json.dumps(
            resultat,
            ensure_ascii=False,
            indent=2,
        )
    )

    return 0 if controles_ok else 1


if __name__ == "__main__":
    sys.exit(main())
