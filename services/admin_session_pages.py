"""Small paginated session viewer, under the ordinary-memory admin unlock."""

BODY = '''
<p><a href="/?section=maintenance#memory">Maintenance</a> · <a href=/maintenance/memory>Souvenirs</a></p>
<h1>Sessions de conversation</h1>
<p>Historique privé par profil. Le partage des faits ne partage jamais les conversations.
Gmail conserve ses archives séparées; PRONOTE, Rose et journal intime sont exclus.
Les sessions SQL fermées expirent après {{days}} jours. Supprimer une session conserve les souvenirs déjà extraits.</p>
{% if not user %}
{% for p in profiles %}<p><a href="/maintenance/sessions/{{p.id}}">{{p.display_name}} · #{{p.id}}</a></p>{% endfor %}
{% elif not selected %}
<h2>{{user.display_name}} · #{{user.id}}</h2>
<p><a href=/maintenance/sessions>Tous les profils</a></p>
{% for s in sessions %}<section><p><a href="/maintenance/sessions/{{user.id}}/{{s.id}}">{{s.opened_at}}</a>
 · {{s.surface}} · {{s.status}} · {{s.messages}} messages · {{s.pending or 0}} à examiner</p>
<p>Fermeture : {{s.closed_at or 'en cours'}} · {{s.close_reason}}</p></section>{% else %}<p>Aucune session.</p>{% endfor %}
{% if offset %}<a href="?offset={{[0,offset-40]|max}}">Précédentes</a>{% endif %}
{% if sessions|length==40 %}<a href="?offset={{offset+40}}">Suivantes</a>{% endif %}
{% for i in imports %}{% if i.errors or i.status in ['changed','oversized'] %}<p>Import {{i.source}} : {{i.status}}, {{i.errors}} lignes invalides.</p>{% endif %}{% endfor %}
{% else %}
<h2>{{user.display_name}} · {{selected.opened_at}}</h2>
<p><a href="/maintenance/sessions/{{user.id}}">Sessions de ce profil</a> ·
<a href="/maintenance/sessions/{{user.id}}/{{selected.id}}/export">Télécharger JSON</a></p>
<p>{{selected.status}} · {{selected.close_reason}} · {{selected.surface}}</p>
{% for m in messages %}<article><b>{{m.role}} · {{m.channel}} · {{m.at}}</b>
<pre style="white-space:pre-wrap;overflow-wrap:anywhere">{{m.text}}</pre>
{% for ref in m.references %}<p><a rel="noreferrer" href="{{ref.url}}">{{ref.title}}</a></p>{% endfor %}
<p>Apprentissage : {{m.learning}} · #{{m.id}}</p></article>{% endfor %}
{% if messages|length==40 %}<p><a href="?after={{messages[-1].id}}">Suite</a></p>{% endif %}
{% if selected.status not in ['open','importing'] %}
<details><summary>Supprimer cet historique</summary>
<p>Ne supprime pas les souvenirs déjà enregistrés ni les fichiers JSONL d’origine.
Les énoncés encore en attente ne seront plus appris. Saisissez {{selected.id}}.</p>
<form method=post action="/maintenance/sessions/{{user.id}}/{{selected.id}}/delete">
<input type=hidden name=csrf value="{{csrf_token()}}"><input name=confirm required autocomplete=off>
<button>Supprimer cette session</button></form></details>{% endif %}
{% endif %}
'''
