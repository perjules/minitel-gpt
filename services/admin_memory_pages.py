"""Read-only, responsive memory inspector; uses the existing admin page shell."""

BODY = '''
<p><a href="/?section=maintenance#memory">Retour à Maintenance</a></p>
<p>Souvenirs propres à chaque profil. Les noms ne sont pas une authentification.
Les conversations et sauvegardes héritées restent séparées. Aucun appel IA depuis cette page.</p>
<p>Apprentissage automatique global : <b>{{'actif' if automatic else 'en pause'}}</b>.
Défaut MinitelGPT : apprentissage désactivé. Les opérations explicites restent disponibles.</p>
{% if not unlocked %}
<section><h2>Consulter / télécharger les contenus</h2>
<p>Confirmez le mot de passe d’administration. Le mot de passe Linux n’est pas utilisé.</p>
<form method=post><input type=hidden name=csrf value="{{csrf_token()}}">
<input type=hidden name=action value=unlock>
<label>ADMIN_PASSWORD<input type=password name=password autocomplete=current-password required></label>
<button>Déverrouiller pour 5 minutes</button></form></section>
{% else %}
<p>Contenus déverrouillés pour cette session, pendant 5 minutes maximum.</p>
<form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><button name=action value=lock>Verrouiller</button></form>
<p><a href=/maintenance/memory/export>Télécharger toutes les mémoires (JSON)</a> — sans journaux ni vecteurs.</p>
{% endif %}
{% for p in profiles %}<section>
<h2>{{p.name}} · #{{p.user_id}} {{'(profil désactivé)' if not p.enabled}}</h2>
{% if p.error %}<p role=status>{{p.error}}</p>
{% else %}
<p>{{p.count}} éléments · {{p.bytes}} octets de texte · dernière modification : {{p.updated}}</p>
<p>Vecteurs actuels : {{p.indexed}} / {{p.indexable}} · énoncés en attente : {{p.pending}}.
Apprentissage : {{'actif' if p.auto_enabled and automatic and p.enabled else 'inactif'}} · état : {{p.processing}}.</p>
{% if p.retry_after %}<p>Une reprise est différée après un échec; une prochaine interaction relancera le travail.</p>{% endif %}
{% if unlocked %}<p><a href="/maintenance/memory/{{p.user_id}}">Afficher les souvenirs</a> ·
<a href="/maintenance/memory/export/{{p.user_id}}">Télécharger ce profil</a></p>{% endif %}
{% endif %}<p>Dossier historique : <code>{{p.memory_key}}</code></p></section>{% endfor %}
{% if selected %}
<section><h2>Souvenirs de {{selected.name}}</h2>
{% for item in selected.data['items'] %}<article>
<h3>{{item.title or item.kind}} {{'[fait]' if item.status=='done'}}</h3>
<p>{{item.kind}} · {{item.origin}} · {{item.updated}} · <code>{{item.id}}</code></p>
<pre>{{item.text}}</pre>
{% if item.source %}<details><summary>Origine de l’apprentissage</summary>
{% for source in item.source %}<p>{{source.at}} · session {{source.session_id}} · énoncé {{source.event_id}}</p><pre>{{source.quote}}</pre>{% endfor %}
</details>{% endif %}<hr></article>{% else %}<p>Aucun souvenir enregistré.</p>{% endfor %}
</section>{% endif %}
{% if legacy %}<section><h2>Anciens dossiers sans profil enregistré</h2>
<p>Conservés sans créer d’identité ni attribuer de droits. Reconnectez-vous avec le nom d’origine pour les importer.
L’export global inclut leurs données lisibles, avec les erreurs éventuelles.</p>
{% for p in legacy %}<details><summary>{{p.memory_key}}</summary>
{% if unlocked %}{% if p.error %}<p>{{p.error}}</p>{% else %}<pre>{{p.data|tojson(indent=2)}}</pre>{% endif %}{% endif %}
</details>{% endfor %}</section>{% endif %}
<details><summary>Stockage et nettoyage manuel</summary>
<p>Base : <code>{{paths.database}}</code>. Souvenirs : tables <code>memory_items</code> et
<code>memory_state</code>; énoncés transitoires : <code>memory_events</code>. Les vecteurs sont reconstruisibles.</p>
<p>Archives : <code>{{paths.archives}}</code> et <code>{{paths.default_archives}}</code>.
Les anciens JSON sont conservés dans <code>{{paths.legacy_backup}}</code>.</p>
<p>Pour un nettoyage manuel : arrêter admin-ui et minitel-chatgpt, sauvegarder tout le dossier,
utiliser sqlite3 selon docs/MEMORY.md, puis redémarrer. Ne pas modifier le marqueur memory.json.
Retirer un souvenir ne retire pas ses anciennes conversations.</p></details>
</html>'''
