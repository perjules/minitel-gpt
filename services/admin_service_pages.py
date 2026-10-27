"""Small shared shell for feature configuration pages."""
HEAD = '''<!doctype html><html lang=fr><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{{title}} — 3615 IA</title>
<style>body{font:16px system-ui;max-width:850px;margin:20px auto;padding:16px;background:#1b1b1f;color:#eee}
a,summary{color:#4ecdc4}section,fieldset,details{border:1px solid #555;border-radius:8px;padding:16px;margin:16px 0}
label{display:block;margin:12px 0}input,textarea,select,button{font:inherit;box-sizing:border-box;max-width:100%;padding:8px}
input:not([type=checkbox]),textarea,select{width:100%}button{margin:6px 6px 6px 0}table{width:100%;border-collapse:collapse}td,th{padding:6px;text-align:left}
pre{white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}input:disabled{opacity:.5}
@media(max-width:600px){table,tr,td,th{display:block}thead{display:none}}[role=status]{border-left:3px solid #4ecdc4;padding:10px}
</style><a href="/?section=services">Retour aux services</a><h1>{{title}}</h1>
{% if message %}<p role=status>{{message}}</p>{% endif %}'''
FUTURE = '''<details><summary>Associations utilisateurs — à venir</summary>
<p>Les associations et comptes supplémentaires ne sont pas encore disponibles.</p>
<label>Utilisateur<input disabled placeholder="À venir"></label><label>Compte associé<select disabled><option>À venir</option></select></label>
<button disabled>Ajouter un compte</button></details>'''
GMAIL = '''<section><h2>Compte actuel</h2><p>{{account}}</p>
<p>Intégration : {{'active' if enabled else 'inactive'}}. La configuration locale ne confirme pas la connexion.</p>
<form method=post action=/services/gmail/toggle><input type=hidden name=csrf value="{{csrf_token()}}">
<label><input type=checkbox name=enabled value=1 {{'checked' if enabled}}> Activer Gmail</label>
<button>Enregistrer et appliquer</button></form>
<form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><button name=action value=check>Vérifier la connexion (lecture seule)</button></form>
<a href="/test?mode=gmail">Tester Gmail</a></section>
<details><summary>Connexion et reconnexion</summary>
<p>L’enrôlement chiffré existant reste utilisé. Sur le Pi : installer l’extension Gmail, effectuer l’autorisation Google avec l’outil d’enrôlement, puis activer les identifiants pour les deux services.</p>
<pre>sudo bash scripts/install_gmail.sh
sudo /opt/minitel-gpt/venv/bin/python scripts/gmail_enroll.py /etc/minitel-gmail/client.json
sudo bash scripts/install_gmail.sh --activate</pre>
<p>La procédure complète (préparation Google et tunnel de rappel) est décrite dans docs/GMAIL.md. Une désactivation conserve les identifiants.</p></details>'''
PLACEHOLDER = '''<section><h2>En construction</h2><p>Administration inactive. Aucun réglage n’est appliqué depuis cette page.</p></section>'''

TEST_NAV = '''<nav><a href="/test?mode=conversation">Conversation</a> · <a href="/test?mode=gmail">Gmail</a></nav>'''
CHAT_TEST = '''<p>Test de l’IA actuellement configurée. Les requêtes sont réelles et la conversation est journalisée dans la mémoire choisie.</p>
<label>Utilisateur (vide = mémoire commune)<input id=user maxlength=60></label>
<textarea id=question rows=4 placeholder="Votre question"></textarea>
<button id=send type=button onclick="ask(false)">Envoyer</button><button type=button onclick="ask(true)">SOMMAIRE / nouvelle session</button>
<pre id=answer role=status></pre>
<script>
const csrf={{csrf_token()|tojson}}, preset={{preset|tojson}};
let sequence=0;
async function ask(reset){
 const message=document.getElementById('question').value.trim();if(!reset&&!message)return;
 const current=++sequence, out=document.getElementById('answer');out.textContent='Consultation…';
 try{
  const response=await fetch('/test-preset',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
   body:JSON.stringify({preset_key:preset,message:message,memory_user:document.getElementById('user').value,reset:reset})});
  const data=await response.json();if(current!==sequence)return;
  out.textContent=data.ok?(data.answer||'(réponse vide)'):('Erreur : '+data.error);
 }catch(error){if(current===sequence)out.textContent='Requête indisponible. Réessayez.';}
}
</script>'''
