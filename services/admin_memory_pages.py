"""Gated, responsive ordinary-memory editor; uses the existing admin page shell."""

BODY = '''
<p><a href="/?section=maintenance#memory">Retour à Maintenance</a></p>
<p><a href=/users>Gérer les profils / supprimer un utilisateur et ses mémoires</a></p>
<p><a href=/maintenance/sessions>Consulter les sessions de conversation</a> (même déverrouillage).</p>
<p>Souvenirs personnels par profil et faits du foyer partagés depuis MinitelGPT. Les noms ne sont pas une authentification.
Les conversations et sauvegardes héritées restent séparées. Consultation et édition manuelle sans appel IA;
« Ajouter une mémoire (IA) » ouvre le même parcours que RETIENS.</p>
<p>Apprentissage automatique des profils nommés : <b>{{'actif' if automatic else 'en pause'}}</b>.
Défaut MinitelGPT : apprentissage désactivé, commandes autorisées après ACTIVER MEMOIRE. Les autres profils gardent leurs commandes personnelles.</p>
{% if not unlocked %}
<section id=memory-unlock><h2>Consulter / modifier / télécharger les contenus</h2>
<p>Confirmez le mot de passe d’administration. Le mot de passe Linux n’est pas utilisé.</p>
<form method=post action=/maintenance/memory><input type=hidden name=csrf value="{{csrf_token()}}">
<input type=hidden name=profile value="{{target}}">
{% if next_add %}<input type=hidden name=after_add value=yes>{% endif %}
<input type=hidden name=action value=unlock>
<label>ADMIN_PASSWORD<input type=password name=password autocomplete=current-password required></label>
<button>Déverrouiller pour 5 minutes</button></form></section>
{% else %}
<p>Contenus déverrouillés pour cette session, pendant 5 minutes maximum.</p>
<form method=post action=/maintenance/memory><input type=hidden name=csrf value="{{csrf_token()}}"><button name=action value=lock>Verrouiller</button></form>
<p><a href=/maintenance/memory/export>Télécharger toutes les mémoires (JSON)</a> — sans journaux ni vecteurs.</p>
{% endif %}
{% for p in profiles %}<section>
<h2>{{p.name}}{{" — mémoire du foyer" if p.user_id==1}} · #{{p.user_id}} {{'(profil désactivé)' if not p.enabled}}</h2>
{% if p.error %}<p role=status>{{p.error}}</p>
{% else %}
<p>{{p.count}} éléments · {{p.bytes}} octets de texte · dernière modification : {{p.updated}}</p>
{% if p.user_id!=1 %}<p>{{p.shared_count}} / {{p.shareable}} faits partagés avec les autres profils.</p>{% endif %}
<p>Vecteurs actuels : {{p.indexed}} / {{p.indexable}} · énoncés en attente : {{p.pending}}.
Apprentissage : {{'actif' if p.auto_enabled and automatic and p.enabled else 'inactif'}} · état : {{p.processing}}.</p>
{% if p.onboarding %}<p>Premier accueil : {{p.onboarding.status}} · {{p.onboarding.turns}} échanges.
{% for topic,status in p.onboarding.topics.items() %}{{topic}} : {{status}}{{'; ' if not loop.last}}{% endfor %}</p>{% endif %}
{% if p.retry_after %}<p>Une reprise est différée après un échec; une prochaine interaction relancera le travail.</p>{% endif %}
{% if unlocked %}<p><a href="/maintenance/memory/{{p.user_id}}">Afficher / modifier les souvenirs</a> ·
<a href="/maintenance/memory/export/{{p.user_id}}">Télécharger ce profil</a></p>{% else %}<p><a href="/maintenance/memory?profile={{p.user_id}}#memory-unlock">Afficher / modifier les souvenirs — déverrouiller</a></p>{% endif %}
{% if p.enabled and not selected %}<form method=get action="/maintenance/memory/{{p.user_id}}/add"><button>Ajouter une mémoire (IA)</button></form>{% endif %}
{% endif %}<p>Dossier historique : <code>{{p.memory_key}}</code></p></section>{% endfor %}
{% if selected %}
<section><h2>Souvenirs de {{selected.name}}</h2>
{% if selected.enabled %}<form method=get action="/maintenance/memory/{{selected.user_id}}/add"><button>Ajouter une mémoire (IA)</button></form>{% endif %}
{% if selected.user_id==1 %}<p>Les faits et préférences cochés « Partager » sont accessibles à tous les profils.
Les anciens éléments restent non partagés jusqu’à votre choix explicite. Utilisez des noms précis :
« Le frère d’Alice est Bob », « Le Minitel est installé à… ». Ne saisissez que des informations destinées à tous.</p>{% endif %}
{% if selected.user_id!=1 %}
<p>Les faits privés restent dans ce profil. Un fait partagé est une déclaration attribuée à
<strong>{{selected.name}} (#{{selected.user_id}})</strong>, lisible par tous les autres profils,
y compris MinitelGPT anonyme. « User », « je » et « mon » dans son texte désignent cette personne.
Les tiers nommés restent distincts; les citations sources ne sont pas partagées.</p>
<form method=post action="/maintenance/memory/{{selected.user_id}}/change">
<input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=sharing>
<input type=hidden name=profile_revision value="{{selected.profile_revision}}">
<input type=hidden name=memory_revision value="{{selected.data.revision}}">
<label><input type=checkbox name=share value=yes {{'checked' if selected.shareable and selected.shared_count==selected.shareable}}> Partager tous les faits actuels</label>
<p>{{selected.shared_count}} / {{selected.shareable}} faits partagés. Cocher puis appliquer partage les faits affichés;
décocher puis appliquer les rend tous privés. Les futurs faits et les changements automatiques restent privés.
Préférences, notes, listes et tâches restent privées dans cette phase.</p>
<button>Appliquer à tous les faits</button></form>
{% endif %}
<details><summary>Ajouter un souvenir — saisie manuelle (sans IA)</summary>
<form method=post action="/maintenance/memory/{{selected.user_id}}/change">
<input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=add>
<input type=hidden name=profile_revision value="{{selected.profile_revision}}">
<input type=hidden name=memory_revision value="{{selected.data.revision}}">
<label>Type<select name=kind><option value=fact>Fait</option><option value=preference>Préférence</option>
{% if selected.user_id!=1 %}<option value=note>Note</option><option value=list>Entrée de liste</option><option value=task>Tâche</option>{% endif %}</select></label>
<label>Titre (obligatoire pour une note ou une liste)<input name=title maxlength=160></label>
<label>Texte<textarea name=text rows=5 maxlength=32000 required></textarea></label>
{% if selected.user_id==1 %}<label><input type=checkbox name=publish value=yes> Partager avec tous les utilisateurs</label>
{% else %}<label><input type=checkbox name=share value=yes> Partager ce nouveau fait (faits uniquement)</label>{% endif %}
<button>Ajouter</button></form></details>
{% for item in selected.data['items'] %}<article>
<h3>{{item.title or item.kind}} {{'[fait]' if item.status=='done'}}</h3>
<p>{{item.kind}} · {{item.origin}} · {{item.updated}} · <code>{{item.id}}</code></p>
<pre>{{item.text}}</pre>
{% if selected.user_id==1 %}<p>{{"Partagé avec tous les utilisateurs" if item.origin=='household' else "Non partagé"}}</p>
{% else %}<p>{{"Fait partagé — déclaration de " ~ selected.name ~ " (#" ~ selected.user_id ~ ")" if item.shared else "Privé"}}</p>
{% if item.kind=='fact' %}<form method=post action="/maintenance/memory/{{selected.user_id}}/change">
<input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=sharing>
<input type=hidden name=profile_revision value="{{selected.profile_revision}}">
<input type=hidden name=memory_revision value="{{selected.data.revision}}">
<input type=hidden name=identifier value="{{item.id}}">
<label><input type=checkbox name=share value=yes {{'checked' if item.shared}}> Partager ce fait avec les autres profils</label>
<button>Appliquer le partage</button></form>{% endif %}{% endif %}
<details><summary>Modifier cet élément</summary>
<form method=post action="/maintenance/memory/{{selected.user_id}}/change">
<input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=edit>
<input type=hidden name=profile_revision value="{{selected.profile_revision}}">
<input type=hidden name=memory_revision value="{{selected.data.revision}}">
<input type=hidden name=identifier value="{{item.id}}">
<label>Titre<input name=title value="{{item.title}}" maxlength=160></label>
<label>Texte<textarea name=text rows=5 maxlength=32000 required>{{item.text}}</textarea></label>
{% if selected.user_id==1 and item.kind in ['fact','preference'] %}<label><input type=checkbox name=publish value=yes {{'checked' if item.origin=='household'}}> Partager avec tous les utilisateurs</label>{% endif %}
{% if selected.user_id!=1 and item.kind=='fact' %}<label><input type=checkbox name=share value=yes {{'checked' if item.shared}}> Partager ce texte corrigé après relecture</label>{% endif %}
<p>L’enregistrement remplace le contenu et son origine par une correction administrative.
Une correction au terminal ou par apprentissage automatique rend un texte modifié privé jusqu’à nouvelle publication.</p>
<button>Enregistrer les modifications</button></form></details>
<details><summary>Supprimer cet élément</summary>
<form method=post action="/maintenance/memory/{{selected.user_id}}/change">
<input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=delete>
<input type=hidden name=profile_revision value="{{selected.profile_revision}}">
<input type=hidden name=memory_revision value="{{selected.data.revision}}">
<input type=hidden name=identifier value="{{item.id}}">
<label><input type=checkbox name=confirm value=yes required> Confirmer la suppression de ce souvenir</label>
<p>Les anciennes conversations et sauvegardes sont conservées.</p><button>Supprimer</button></form></details>
{% if item.source %}<details><summary>Origine de l’apprentissage</summary>
{% for source in item.source %}<p>{{source.at}} · session {{source.session_id}} · énoncé {{source.event_id}}</p><pre>{{source.quote}}</pre>{% if source.question %}<p>Question à laquelle la réponse se rapporte :</p><pre>{{source.question}}</pre>{% endif %}{% endfor %}
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
