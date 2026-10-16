# Console de test sans Minitel

Le prototype personnel `3615 rose` dispose aussi de son atelier isolé :
[ROSE.md](ROSE.md) (porte locale obligatoire, `scripts/rose_console.py`,
dialogue simulé optionnel, conversations transitoires).

Depuis Bash sur le Pi, par SSH ou Raspberry Pi Connect :

```bash
cd /home/minitel/minitel-gpt
python3 scripts/terminal_console.py
```

Utiliser le Python qui possède les dépendances de l'installation. Si Gmail a
créé le venv du dépôt :

```bash
.venv/bin/python scripts/terminal_console.py
```

Le terminal doit offrir au moins **40 colonnes × 24 lignes**. Aucune nouvelle
dépendance : `curses` fait partie du Python Linux. Un lancement SSH direct demande
un terminal interactif (`ssh -t`). Ctrl-Q ou Ctrl-C quitte et restaure le terminal.

| Clavier console | Touche Minitel |
|---|---|
| Entrée | ENVOI |
| Ctrl-N, F3, Page suivante | SUITE |
| Ctrl-P, F2, Home | SOMMAIRE |
| Ctrl-G, F1 | GUIDE |
| Ctrl-R, F4, Échap | RETOUR |
| Retour arrière | CORRECTION |

Ces touches ont exactement le comportement de la boucle Minitel selon l'écran.
SOMMAIRE réinitialise la session et permet de changer de nom. RETOUR quitte la
console CALC ou interrompt son spectacle; dans PRONOTE, il quitte le mode ou
interrompt la consultation; pendant une saisie ordinaire il efface
un caractère. Tapez les commandes applicatives (`MAIL`, `CALC`, `ANNULER`, etc.)
puis Entrée. La saisie accepte les caractères ASCII comme la boucle actuelle.

## Ce qui est partagé

La console fournit seulement un transport clavier/écran à `Term`; elle appelle
`minitel_chatgpt.run` et son décodeur de touches réels. Elle utilise les mêmes
presets, `.env`, fichiers PRONOTE, mémoire, historique, services AI, routage,
sources, pages et règles de confirmation. Elle reproduit le texte réellement
émis, après conversion ASCII, sur un écran de 40 × 24 cellules, avec les modes
page et défilement. Elle conserve les pauses applicatives et les délais réseau.

Les couleurs et caractères de double taille sont ignorés. Il n'y a ni UART,
ni délai 1200 bauds, ni validation des particularités du vrai CRT/clavier.
Le service physique peut rester lancé : les ports ne se disputent pas et les
conversations sont distinctes. Les sessions d'un même nom partagent néanmoins
leurs souvenirs persistants.

## Intégrations et effets réels

Ce n'est pas un mode simulé : les appels API sont facturés normalement, les notes
et journaux sont enregistrés, et `ENVOYER` peut envoyer un vrai email après la
revue complète. Les credentials restent dans les mécanismes existants.

Les variables et credentials ajoutés par systemd ne sont pas automatiquement
hérités par Bash. `GMAIL_ENABLED=1` seul active Gmail, mais ne fournit pas le
credential chiffré : la console affiche alors une demande de reconnexion même
si Gmail fonctionne dans l'admin. Le nom saisi au démarrage choisit les souvenirs,
pas le compte Gmail; changer de nom ne résout pas ce problème.

Pour Gmail déjà enrôlé avec son helper installé :

```bash
.venv/bin/python scripts/terminal_console.py --gmail
```

Le flag lance automatiquement un service temporaire avec un terminal interactif
et le credential chiffré existant. `sudo` peut demander le mot de passe Linux;
la console tourne ensuite sous l'utilisateur Linux `minitel`, comme les services.
Le script trouve son dépôt et son venv sans chemin à saisir. S'il dispose déjà
de credentials livrés par systemd, il active Gmail directement.

Aucun réenrôlement ni copie de token n'est nécessaire. `TERM` et un éventuel
`MINITEL_MEMORY_DIR` sont transmis au service temporaire; les autres variables
du shell ne le sont pas. Le `.env` du dépôt reste chargé normalement. Pour isoler
seulement les souvenirs et journaux de test, définir `MINITEL_MEMORY_DIR` vers un
répertoire accessible à `minitel`; les API et autres intégrations restent réelles.

Le test web de personnalité reste indépendant et ne reproduit pas toute cette
session. Les contrôles automatisés de la console emploient des réponses simulées;
la validation du Pi, des fournisseurs réels et du Minitel physique reste distincte.

## Mode PRONOTE

Tapez `PRONOTE` puis Entrée. Les commandes locales (`COURS`, `DEVOIRS`, `NOTES`,
`ENFANT`, `AIDE`, etc.) utilisent les mêmes alias/services que le Minitel et
fonctionnent sans clé AI. Ctrl-N page les résultats; Ctrl-R quitte le mode,
Ctrl-P réinitialise la session et Ctrl-G garde l'administration existante.
`IMPRIMER` utilise le résultat complet chargé, pas la dernière page affichée.
Les lectures directes restent transitoires et ne sont pas ajoutées au journal
mémoire. Les appels PRONOTE, l'éventuel fallback AI et l'impression sont réels.
Voir [PRONOTE.md](PRONOTE.md) pour les commandes, limites et vérifications live.
