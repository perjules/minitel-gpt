"""On-demand provider catalogues; no SDK, background refresh or stored keys."""
import json
import re

import requests


class CatalogueError(ValueError):
    pass


def valid_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}', value) is not None


def models(provider, key):
    if provider not in {'mistral', 'claude'} or not key:
        raise CatalogueError('Fournisseur ou clé API manquante.')
    url = {'mistral': 'https://api.mistral.ai/v1/models',
           'claude': 'https://api.anthropic.com/v1/models'}[provider]
    headers = ({'Authorization': 'Bearer ' + key} if provider == 'mistral' else
               {'x-api-key': key, 'anthropic-version': '2023-06-01'})
    params = {} if provider == 'mistral' else {'limit': 100}
    found, size = {}, 0
    try:
        for _ in range(10):
            with requests.get(url, headers=headers, params=params, timeout=(3, 10), stream=True) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > 2 * 1024 * 1024:
                        raise CatalogueError('Catalogue trop volumineux.')
                    body.extend(chunk)
                payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
                raise CatalogueError('Format du catalogue inattendu.')
            for item in payload['data']:
                if not isinstance(item, dict) or not valid_id(item.get('id')) or item.get('archived'):
                    continue
                if provider == 'mistral' and not item.get('capabilities', {}).get('completion_chat'):
                    continue
                found[item['id']] = {'id': item['id'], 'label': item.get('display_name') or item['id']}
            if provider != 'claude' or not payload.get('has_more'):
                return list(found.values())
            cursor = payload.get('last_id')
            if not valid_id(cursor) or cursor == params.get('after_id'):
                break
            params['after_id'] = cursor
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        raise CatalogueError('Liste indisponible. Vérifiez la clé ou réessayez ; le modèle actuel est conservé.') from None
    raise CatalogueError('Catalogue incomplet ; réessayez ou saisissez un identifiant manuel.')
