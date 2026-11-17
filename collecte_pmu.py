#!/usr/bin/env python3
"""Collecte PMU France, lecture seule; aucune décision sportive créée."""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

BASE = 'https://online.turfinfo.api.pmu.fr/rest/client/1/programme'
TODAY = datetime.now(ZoneInfo('Europe/Paris'))
DATE = TODAY.strftime('%d%m%Y')
OUT = os.environ.get('X1000_SORTIE', 'sortie')


def get(path):
    url = BASE + path
    req = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'X1000-PMU-Controle/1.0'
        }
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status != 200 or resp.geturl() != url:
            raise RuntimeError('Réponse ou redirection inattendue')
        raw = resp.read(8_000_001)
        if len(raw) > 8_000_000:
            raise RuntimeError('Réponse trop volumineuse')
        return json.loads(raw.decode('utf-8-sig'))


def pick(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for key in keys:
        v = obj.get(key)
        if v is not None and v != '':
            return v
    return None


def label(v):
    if isinstance(v, dict):
        return str(
            pick(v, 'libelleCourt', 'libelleLong',
                 'nom', 'libelle', 'code') or ''
        ).strip()
    return str(v or '').strip()


def country(reunion):
    hip = reunion.get('hippodrome')
    sources = [reunion] + (
        [hip] if isinstance(hip, dict) else []
    )
    vals = []
    for source in sources:
        for k in (
            'pays', 'codePays', 'nomPays',
            'country', 'countryCode',
            'codePaysHippodrome'
        ):
            v = source.get(k)
            if v is not None:
                vals.append(label(v).upper())
    vals = [x for x in vals if x]
    return bool(vals) and all(
        x in ('FR', 'FRA', 'FRANCE',
              'FRANCAISE', 'FRANÇAISE')
        for x in vals
    )


def positive(v):
    if isinstance(v, bool):
        return None
    s = str(v or '')
    return int(s) if re.fullmatch(
        r'[1-9][0-9]*', s
    ) else None


def depart(v):
    if isinstance(v, (int, float)) and v > 0:
        try:
            timestamp = v / 1000 if v > 10**11 else v
            return datetime.fromtimestamp(
                timestamp, ZoneInfo('Europe/Paris')
            ).isoformat()
        except (ValueError, OverflowError, OSError):
            return None

    if isinstance(v, str) and re.fullmatch(
        r'\d{2}:\d{2}(:\d{2})?', v
    ):
        return TODAY.strftime('%Y-%m-%d') + 'T' + v

    return None


def collect():
    data = get('/' + DATE)
    programme = pick(data, 'programme') or data
    reunions = pick(programme, 'reunions')

    if not isinstance(reunions, list):
        raise RuntimeError(
            'Programme PMU : réunions absentes '
            'ou format inconnu'
        )

    result = []
    diagnostics = []

    for r in reunions[:30]:
        if not isinstance(r, dict) or not country(r):
            continue

        nr = positive(
            pick(r, 'numOfficiel', 'numReunion', 'numero')
        )
        hippodrome = label(
            pick(r, 'hippodrome', 'nomHippodrome')
        )
        courses = pick(r, 'courses', 'listeCourses')

        if not nr or not hippodrome or not isinstance(
            courses, list
        ):
            diagnostics.append(
                'Réunion française incomplète'
            )
            continue

        for c in courses[:40]:
            if not isinstance(c, dict):
                continue

            nc = positive(
                pick(c, 'numOrdre', 'numCourse', 'numero')
            )
            heure = depart(
                pick(c, 'heureDepart',
                     'heure_depart', 'depart')
            )

            if not nc or not heure:
                diagnostics.append(
                    f'R{nr}: course sans numéro '
                    'ou départ vérifiable'
                )
                continue

            try:
                raw = get(
                    f'/{DATE}/R{nr}/C{nc}/participants'
                )
            except Exception as exc:
                diagnostics.append(
                    f'R{nr}C{nc}: participants '
                    f'indisponibles ({type(exc).__name__})'
                )
                continue

            participants = (
                raw if isinstance(raw, list)
                else pick(
                    raw, 'participants', 'partants',
                    'listeParticipants'
                )
            )

            if not isinstance(
                participants, list
            ) or not participants:
                diagnostics.append(
                    f'R{nr}C{nc}: aucun partant confirmé'
                )
                continue

            chevaux = []
            seen = set()

            for p in participants:
                if not isinstance(p, dict):
                    continue

                n = positive(
                    pick(p, 'numPmu', 'numero', 'num')
                )
                nom = label(
                    pick(p, 'nom', 'nomCheval', 'cheval')
                )

                if not n or not nom or n in seen:
                    continue

                seen.add(n)
                chevaux.append({
                    'numero': n,
                    'nom': nom,
                    'non_partant': pick(
                        p, 'nonPartant', 'non_partant'
                    ) is True
                })

            if not chevaux:
                diagnostics.append(
                    f'R{nr}C{nc}: partants invalides'
                )
                continue

            result.append({
                'course_id':
                    f'{TODAY:%Y-%m-%d}-R{nr}-C{nc}',
                'reunion': f'R{nr}',
                'course': f'C{nc}',
                'hippodrome': hippodrome,
                'date': TODAY.strftime('%Y-%m-%d'),
                'depart': heure,
                'nom_course': label(
                    pick(c, 'libelle', 'nomCourse')
                ),
                'partants': sorted(
                    chevaux,
                    key=lambda x: x['numero']
                ),
                'source': 'PMU TurfInfo',
                'pays_verifie': 'FR'
            })

    return result, diagnostics


def main():
    os.makedirs(OUT, exist_ok=True)
    courses, diagnostics = collect()

    document = {
        'schema': 'x1000-pmu-france-v1',
        'date_publication':
            datetime.now(timezone.utc).isoformat(),
        'date_pmu': TODAY.strftime('%Y-%m-%d'),
        'source': 'PMU TurfInfo',
        'statut': (
            'DONNEES_VERIFIEES'
            if courses else 'AUCUNE_COURSE_VALIDEE'
        ),
        'courses': courses,
        'diagnostics': diagnostics[:100]
    }

    path = os.path.join(
        OUT, 'publication_courses.json'
    )

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(
            document, f,
            ensure_ascii=False, indent=2
        )

    print(
        f'Courses françaises vérifiées: '
        f'{len(courses)}; fichier: {path}'
    )

    for msg in diagnostics[:20]:
        print('ATTENTION:', msg)

    if not courses:
        raise RuntimeError(
            'Aucune course vérifiée : '
            'aucune publication exploitable'
        )


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(
            'ECHEC COLLECTE:', exc,
            file=sys.stderr
        )
        sys.exit(1)
