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
GMAIL = '''<section><h2>Comptes Gmail</h2><p>{{account}}</p>
<p><a href=/users>Utilisateurs et accès</a></p>
{% for a in oauth_accounts %}<p>{{a.email}} · {% for u in a.users %}{{u.display_name}} (#{{u.user_id}}, {{'lecture et envoi' if u.can_send else 'lecture seule'}}){{', ' if not loop.last}}{% else %}non attribué{% endfor %}</p>
<form method=post action=/services/gmail/accounts><input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=disable><input type=hidden name=account_id value="{{a.id}}"><label><input type=checkbox name=enabled {{'checked' if a.enabled}}> Compte actif</label><button>Appliquer</button></form>
<form method=post action=/services/gmail/accounts onsubmit="return confirm('Déconnecter ce compte et retirer les accès de tous les profils qui l’utilisent ?')"><input type=hidden name=csrf value="{{csrf_token()}}"><input type=hidden name=action value=disconnect><input type=hidden name=account_id value="{{a.id}}"><button>Déconnecter localement</button></form>{% endfor %}
<form method=post action=/services/gmail/accounts><input type=hidden name=csrf value="{{csrf_token()}}"><button name=action value=migrate>Importer l’ancien compte partagé sans l’attribuer</button><button name=action value=sync>Synchroniser les comptes enregistrés</button></form>
<p>Intégration : {{'active' if enabled else 'inactive'}}. La configuration locale ne confirme pas la connexion.</p>
<form method=post action=/services/gmail/toggle><input type=hidden name=csrf value="{{csrf_token()}}">
<label><input type=checkbox name=enabled value=1 {{'checked' if enabled}}> Activer Gmail</label>
<button>Enregistrer et appliquer</button></form>
<form method=post><input type=hidden name=csrf value="{{csrf_token()}}"><label>Compte à vérifier<select name=account_id>{% for a in oauth_accounts %}<option value="{{a.id}}">{{a.email}}</option>{% endfor %}</select></label><button name=action value=check>Vérifier la connexion (lecture seule)</button></form>
<a href="/test?mode=gmail">Tester Gmail</a></section>
<section><h2>Connexion Google</h2>
<p>Autorisez le compte du profil sélectionné dans votre navigateur, puis appliquez l’autorisation aux deux services. Aucun tunnel SSH n’est nécessaire après la configuration initiale.</p>
{% if oauth_uri %}<p>Retour Google enregistré : <code>{{oauth_uri}}</code></p>
<p>Utilisez <a href="{{oauth_console}}">cette adresse HTTPS de l’administration</a>, avec Tailscale connecté sur votre téléphone.</p>
<form method=post action=/services/gmail/oauth/start><input type=hidden name=csrf value="{{csrf_token()}}"><label>Profil à connecter<select name=user_id>{% for u in oauth_users %}<option value="{{u.id}}">{{u.display_name}} · #{{u.id}}</option>{% endfor %}</select></label><button>Connecter / réautoriser Gmail avec Google</button></form>
{% else %}<p>Configurez d’abord un client Google de type Application Web ci-dessous. Un ancien client Desktop ne permet pas le retour direct au téléphone.</p>{% endif %}
{% if oauth_ready %}<form method=post action=/services/gmail/oauth/apply onsubmit="return confirm('Charger la nouvelle autorisation et redémarrer le Minitel et l’administration ? La session en cours sera interrompue.')">
<input type=hidden name=csrf value="{{csrf_token()}}"><label><input type=checkbox name=send> Autoriser aussi l’envoi pour ce profil</label><button>Appliquer l’autorisation enregistrée</button></form>
<p>Les services redémarrent quelques secondes après ce bouton. Rechargez ensuite la page et vérifiez la connexion. Activez Gmail si nécessaire.</p>{% endif %}
<details><summary>Configuration initiale / changer le client Google</summary>
<p>Une seule fois : activer HTTPS avec Tailscale Serve pour cette administration, puis créer un client <b>Application Web</b> dans Google Cloud et télécharger son JSON. Ouvrez cette page via HTTPS avant de l’importer.</p>
<p>Exemple d’URI à enregistrer exactement dans Google et dans ce formulaire :</p>
<pre>https://nom-du-pi.nom-du-tailnet.ts.net:9443/services/gmail/oauth/callback</pre>
<p>Dans Google, choisissez le statut <b>En production</b> pour éviter l’expiration après sept jours du mode Test. Les permissions restent lecture Gmail et envoi Gmail uniquement.</p>
<form method=post action=/services/gmail/oauth/configure enctype=multipart/form-data><input type=hidden name=csrf value="{{csrf_token()}}">
<label>JSON du client Google (Application Web)<input type=file name=client_json required></label>
<p>Choisissez le fichier JSON téléchargé depuis Google. Son contenu est vérifié sur le Pi après l’envoi.</p>
<label>URI de redirection autorisée<input type=url name=redirect_uri value="{{oauth_uri}}" required placeholder="https://nom-du-pi.nom-du-tailnet.ts.net:9443/services/gmail/oauth/callback"></label>
<button>Enregistrer le client chiffré</button></form>
<p>Le JSON et les jetons restent chiffrés sur le Pi, hors Git. Votre mot de passe Google est saisi chez Google uniquement. La procédure détaillée et l’ancien outil Desktop restent disponibles dans docs/GMAIL.md.</p>
</details></section>'''
PLACEHOLDER = '''<section><h2>En construction</h2><p>Administration inactive. Aucun réglage n’est appliqué depuis cette page.</p></section>'''

TEST_NAV = '''<nav><a href="/test?mode=conversation">Conversation</a> · <a href="/test?mode=gmail">Gmail</a></nav>'''
CHAT_TEST = r'''<p>Test de l’IA configurée. Les requêtes sont réelles et la conversation est journalisée dans la mémoire choisie.</p>
<label>Utilisateur (vide = mémoire commune)<input id=memoryUser maxlength=60 value="{{username}}" onchange="++testSequence;document.getElementById('testOut').textContent='';document.getElementById('testSpin').style.display='none'"></label>
<textarea id=testMsg rows=4 placeholder="Votre question"></textarea>
<button type=button onclick="testPreset(false)">Envoyer</button><button type=button onclick="testPreset(true)">SOMMAIRE / nouvelle session</button>
<span id=testSpin style="display:none">Consultation…</span><pre id=testOut role=status></pre>
<script>
const CSRF_TOKEN={{csrf_token()|tojson}}, PRESET={{preset|tojson}}, DRAFT_SYSTEM={{draft|tojson}};
let testSequence=0;
async function testPreset(reset=false){
 const msg=document.getElementById('testMsg').value.trim();
 if(!msg&&!reset)return;
 const sequence=++testSequence, spin=document.getElementById('testSpin'), out=document.getElementById('testOut');
 spin.style.display='block';out.textContent='Consultation…';
 try{
  const response=await fetch('/test-preset',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':CSRF_TOKEN},
   body:JSON.stringify({preset_key:PRESET,system:DRAFT_SYSTEM,message:msg,memory_user:document.getElementById('memoryUser').value,reset:reset})});
  const data=await response.json();if(sequence!==testSequence)return;
  if(data.ok&&data.scene){
   out.textContent='';const started=performance.now();
   for(const line of data.scene){
    if(sequence!==testSequence)return;
    if(performance.now()-started>90000)break;
    out.textContent+=line+'\n';await new Promise(resolve=>setTimeout(resolve,220));
   }
  }else out.textContent=data.ok?(data.answer||'(réponse vide)'):('Erreur : '+data.error);
 }catch(error){if(sequence===testSequence)out.textContent='Requête indisponible. Réessayez.';}
 if(sequence===testSequence)spin.style.display='none';
}
</script>'''
