"""Local, atomic Rose overrides. Tracked editorial defaults remain untouched."""
import copy
import json
import re
from pathlib import Path

try:
    from . import config_store
except ImportError:
    import config_store

LOCAL = 'rose-local.json'
CATALOGUES = {'main': 'rose_personas.json', 'test': 'rose_test_personas.json'}


def state(config):
    path = Path(config) / LOCAL
    if not path.exists():
        return {}
    try:
        data = json.loads(config_store.read_bytes(path))
        if not isinstance(data, dict):
            raise TypeError
        return data
    except (ValueError, UnicodeError, TypeError):
        raise config_store.InvalidConfig('Configuration locale invalide') from None


def catalogue(config, test=False, data=None):
    data = state(config) if data is None else data
    key = 'test' if test else 'main'
    return copy.deepcopy(data.get('catalogues', {}).get(key) or
                         json.loads((Path(config) / CATALOGUES[key]).read_text(encoding='utf-8')))


def split_voice(voice):
    match = re.fullmatch(r'(Registre E([1-5]) : .*?) (Registre S([1-5]) : .*?\.)(?: (.*))?', voice, re.DOTALL)
    return match.groups() if match else None


def levels(config, data=None):
    data = state(config) if data is None else data
    if 'levels' in data:
        return data['levels']
    result = {}
    original = json.loads((Path(config) / CATALOGUES['test']).read_text(encoding='utf-8'))
    for person in original['personas']:
        parts = split_voice(person['voice'])
        if parts:
            e, en, s, sn, _ = parts
            result.setdefault('E' + en, e)
            result['S' + sn] = s
    if set(result) != {f'{axis}{n}' for axis in 'ES' for n in range(1, 6)}:
        raise config_store.InvalidConfig('Définitions E/S incomplètes')
    return result


def editable(person, bank):
    person = copy.deepcopy(person)
    if 'e_level' not in person:
        parts = split_voice(person['voice'])
        if parts:
            e, en, s, sn, style = parts
            person.update(e_level=int(en), s_level=int(sn), voice=style or '')
            if bank.get('E' + en) != e:
                person['e_prompt_override'] = e
            if bank.get('S' + sn) != s:
                person['s_prompt_override'] = s
    return person


def voice(config, person):
    if 'e_level' not in person:
        return person['voice']
    bank = levels(config)
    return ' '.join(part for part in (person.get('e_prompt_override') or bank['E' + str(person['e_level'])],
                                     person.get('s_prompt_override') or bank['S' + str(person['s_level'])], person['voice']) if part)


def prompt(config):
    return state(config).get('prompt', (Path(config) / 'rose_prompt.txt').read_text(encoding='utf-8'))


def validate_person(person):
    if not isinstance(person, dict):
        raise config_store.InvalidConfig('Profil invalide')
    for name in ('id', 'name', 'bio', 'voice'):
        if not isinstance(person.get(name), str) or len(person[name]) > 16000:
            raise config_store.InvalidConfig('Texte du profil invalide')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', person['id']) or person['id'] == 'new' or not person['name'].strip():
        raise config_store.InvalidConfig('Identifiant ou nom invalide')
    if (person.get('gender') not in {'homme', 'femme'} or not isinstance(person.get('aliases'), list)
            or any(not isinstance(a, str) or not a.strip() or len(a) > 80 for a in person['aliases'])):
        raise config_store.InvalidConfig('Identité du profil invalide')
    if any(k in person for k in ('e_level', 's_level')) and any(type(person.get(k)) is not int or not 1 <= person[k] <= 5 for k in ('e_level', 's_level')):
        raise config_store.InvalidConfig('Niveaux E/S invalides')
    for key in ('e_prompt_override', 's_prompt_override'):
        if key in person and (not isinstance(person[key], str) or len(person[key]) > 16000):
            raise config_store.InvalidConfig('Variante de niveau invalide')
    if type(person.get('tempo', 0)) is not int or not 0 <= person.get('tempo', 0) <= 100:
        raise config_store.InvalidConfig('Tempo invalide')
    if type(person.get('asexual', False)) is not bool:
        raise config_store.InvalidConfig('Option de profil invalide')
    if not isinstance(person.get('kinks', []), list) or any(not isinstance(k, str) for k in person.get('kinks', [])):
        raise config_store.InvalidConfig('Modules invalides')
    fees = person.get('fees')
    if not isinstance(fees, list) or any(not isinstance(f, list) or len(f) != 2 or
        not isinstance(f[0], str) or type(f[1]) is not int or not 0 <= f[1] <= 1000000 for f in fees):
        raise config_store.InvalidConfig('Tarification invalide')


def save(config, action, *, person=None, test=False, bank=None, text=None):
    path = Path(config) / LOCAL
    with config_store.locked(path):
        data = state(config)
        if action == 'person':
            validate_person(person)
            modules = json.loads((Path(config) / 'rose_kinks.json').read_text(encoding='utf-8'))
            if any(key not in modules for key in person.get('kinks', [])):
                raise config_store.InvalidConfig('Module inconnu')
            cast = catalogue(config, test, data)
            cast['personas'] = [person if p['id'] == person['id'] else p for p in cast['personas']]
            if not any(p['id'] == person['id'] for p in cast['personas']):
                cast['personas'].append(person)
            if len(cast['personas']) > 200:
                raise config_store.InvalidConfig('Catalogue trop volumineux')
            names = [p['name'].casefold() for p in cast['personas']]
            if len(names) != len(set(names)):
                raise config_store.InvalidConfig('Nom déjà utilisé')
            data.setdefault('catalogues', {})['test' if test else 'main'] = cast
        elif action == 'levels':
            if not isinstance(bank, dict) or set(bank) != {f'{a}{n}' for a in 'ES' for n in range(1, 6)} or any(not isinstance(v, str) or not v.strip() or len(v) > 16000 for v in bank.values()):
                raise config_store.InvalidConfig('Définitions E/S invalides')
            previous = levels(config, data)
            for key in CATALOGUES:
                cast = catalogue(config, key == 'test', data)
                cast['personas'] = [editable(p, previous) for p in cast['personas']]
                data.setdefault('catalogues', {})[key] = cast
            data['levels'] = bank
        elif action == 'prompt':
            if not isinstance(text, str) or not text.strip() or len(text) > 64000:
                raise config_store.InvalidConfig('Prompt invalide')
            data['prompt'] = text
        else:
            raise config_store.InvalidConfig('Action inconnue')
        config_store.atomic_write(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())
