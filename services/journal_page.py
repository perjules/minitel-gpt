"""Private browser console: content stays out of chat, storage and URL fields."""

HTML = r'''<!doctype html><html lang=fr><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta name=referrer content=no-referrer><title>Journal intime — 3615 IA</title>
<style>body{background:#101820;color:#eee;font:16px system-ui;max-width:720px;margin:1em auto;padding:1em}
a{color:#4ecdc4}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#000;color:#7fffa0;padding:1em;min-height:8em}
input,button{font:inherit;padding:.6em;max-width:100%;box-sizing:border-box}input{width:100%;margin:.5em 0}button{margin:.3em}
[hidden]{display:none!important}</style>
<a href="/test?mode=conversation">Retour aux tests</a><h1>Journal intime</h1>
<p>Mot de passe propre au journal. Les textes ne sont pas journalisés ni mémorisés par la conversation.</p>
<form id=choose autocomplete=off><label>Profil nommé<input id=username maxlength=60 value="{{username}}" required></label>
<button>Ouvrir le journal</button></form>
<pre id=output role=status>Choisissez un profil et ouvrez son journal.</pre>
<form id=commands autocomplete=off hidden>
<label id=label for=field>Saisie</label><input id=field type=password autocomplete=off maxlength=2000>
<button id=submit>ENVOI</button><button id=next type=button hidden>SUITE</button>
<button id=lock type=button>VERROUILLER</button></form>
<script>
const csrf={{csrf_token()|tojson}};
let timer=null,epoch=0,busy=false,opened=false,lastTouch=0;
const output=document.getElementById('output'),choose=document.getElementById('choose'),commands=document.getElementById('commands'),field=document.getElementById('field'),next=document.getElementById('next');
function clearPrivate(){++epoch;opened=false;field.value='';field.type='password';output.textContent='Journal verrouillé.';commands.hidden=true;choose.hidden=false;clearTimeout(timer);}
function lock(){clearPrivate();fetch('/journal-api',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({action:'lock'}),keepalive:true}).catch(()=>{});}
function arm(ms){clearTimeout(timer);timer=setTimeout(lock,Math.max(0,ms));}
async function send(action,text=''){
 if(busy&&action!=='lock')return;
 const ticket=epoch;busy=true;document.getElementById('submit').disabled=true;
 try{
  const response=await fetch('/journal-api',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({action:action,text:text,username:document.getElementById('username').value})});
  const data=await response.json();if(ticket!==epoch)return;
  if(!response.ok||data.closed){clearPrivate();output.textContent=data.display||'Journal indisponible.';return;}
  opened=true;choose.hidden=true;commands.hidden=false;field.value='';field.type=data.secret?'password':'text';
  document.getElementById('label').textContent=data.secret?'Mot de passe (saisie masquée)':'Commande ou texte';
  output.textContent=data.display;next.hidden=!data.more;field.disabled=!!data.more;
  arm(data.expires_in);field.focus();
 }catch(error){if(ticket===epoch)lock();}
 finally{if(ticket===epoch){busy=false;document.getElementById('submit').disabled=false;}else busy=false;}
}
choose.addEventListener('submit',event=>{event.preventDefault();send('open');});
commands.addEventListener('submit',event=>{event.preventDefault();const text=field.value;field.value='';send('command',text);});
next.addEventListener('click',()=>send('next'));
document.getElementById('lock').addEventListener('click',lock);
field.addEventListener('input',()=>{
 if(!opened)return;arm(300000);
 if(Date.now()-lastTouch>30000){lastTouch=Date.now();const ticket=epoch;
  fetch('/journal-api',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({action:'touch'})})
   .then(r=>r.json()).then(data=>{if(ticket===epoch&&data.closed)lock();}).catch(()=>lock());
 }
});
window.addEventListener('pagehide',lock);
window.addEventListener('pageshow',event=>{if(event.persisted)lock();});
</script></html>'''
