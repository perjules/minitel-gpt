# MINITEL GPT

Transformer un **Minitel** (testé sur **Minitel 1 Telic / Alcatel** et
**Minitel 2**) en terminal de chat IA autonome, piloté par un **Raspberry Pi
Zero 2 W**.

On tape sa question sur le clavier du Minitel, le Pi interroge le fournisseur
d'IA choisi - **Mistral**, **Claude**, ou **Linkup** en recherche web pure -
et affiche la réponse à l'écran, à 1200 bauds. Le fournisseur, la clé et le
modèle se règlent depuis l'interface web, tout comme la personnalité de
l'assistant (la version phare est « bloquée dans les années 80 »).

🌐 Présentation du projet : https://minitel-gpt.herard.com

## Direction de ce fork : demander, lire, préciser

**Tapez ce que vous voulez faire.** Quelques commandes mémorables (`MAIL`, `LIRE 2`)
offrent des raccourcis précis ; le langage naturel permet d'exprimer une intention
et de préciser le résultat. Les nouvelles fonctions réutilisent la saisie, l'écran
et la pagination du terminal. Les indications contextuelles montrent les actions
disponibles, sans ajouter une arborescence de menus à chaque fonctionnalité.
Python contrôle les opérations réelles et les confirmations ; l'IA interprète,
résume ou rédige. L'administration web sert à configurer, diagnostiquer et tester.
Les principes détaillés et leurs limites sont dans
[FORK.md](FORK.md#interaction-design-words-context-and-clear-feedback).

---

## Matériel

| Élément | Détail |
|---|---|
| Raspberry Pi | **Zero W**, **Zero 2 W** ou **Pi 3** (tous validés) - Raspberry Pi OS Lite. Le Zero W suffit (voir ci-dessous) |
| Adaptateur USB-série | **FTDI FT232RL**, jumper sur **5 V** |
| Câble | OTG micro-USB (FTDI → port USB *data* du Pi) |
| Minitel | Prise DIN 5 broches « péri-informatique » - testé sur **Minitel 1** (Telic/Alcatel) et **Minitel 2** |

> **Compatible Pi Zero W (v1, 2017), Zero 2 W et Pi 3** : les trois sont validés.
> En cas de pénurie de Zero 2 W, le Zero W d'origine fonctionne. Le LLM est appelé en HTTP (`requests`), sans dépendance
> lourde à compiler - l'installation passe donc sur l'architecture ARMv6.
> Flasher **Raspberry Pi OS Lite 32-bit**. Le CPU plus lent n'a quasi aucun impact :
> l'affichage est de toute façon limité par la liaison série à 1200 bauds.

### Câblage série

| FTDI | DIN Minitel | Rôle |
|---|---|---|
| TXD | broche 1 | Pi → Minitel (RX) |
| RXD | broche 3 | Minitel → Pi (TX) |
| GND | broche 2 | masse commune |

Paramètres série : **1200 bauds, 7 bits, parité paire, 1 stop (7E1)** - norme Videotex.
Le port apparaît côté Pi comme `/dev/ttyUSB0`.

> ⚠️ **Seules les broches 1, 2 et 3 servent.** Laisser les broches **4 et 5
> libres** (la broche 5 porte une **alimentation/tension**). Ne jamais ponter une
> broche de signal vers la 4 ou la 5 : sur un Minitel 2 cela bloque la réception.

> **Activation selon le modèle** (testé sur Minitel 1 **et** Minitel 2) :
>
> - **Minitel 1** (Telic / Alcatel) : le port DIN affiche les données **par
>   défaut**, aucune manipulation - le service est prêt dès l'allumage.
> - **Minitel 2** : démarre sur son **Répertoire** local. À chaque allumage :
>   appuyer sur **`Fnct + Sommaire`** (quitte le Répertoire, connecte la prise à
>   l'écran en Vidéotex), puis sur **`Sommaire`** pour afficher l'accueil
>   MINITEL GPT.
>   ⚠️ **Ne pas** faire `Fnct + T` puis `A` : cela bascule en mode
>   *téléinformatique* (80 colonnes défilant, sans pagination) au lieu du beau
>   Vidéotex 40 colonnes.
>
> **Compatibilité entre modèles** : le brochage DIN est *normalisé*
> (norme Télétel/STUM), identique sur tous les Minitels à prise péri-informatique.
> Le service reconnaît les touches de fonction aussi bien en Vidéotex (`SEP`)
> qu'en téléinformatique (VT100 `ESC O x`), donc il fonctionne quel que soit le mode.

---

## Installation

### 1. Préparer la carte SD (avant tout)

Avec **Raspberry Pi Imager** (<https://www.raspberrypi.com/software/>) : flasher
**Raspberry Pi OS Lite**.

- **Pi Zero 2 W / Pi 3** : version **64-bit**.
- **Pi Zero W (v1)** : version **32-bit** - c'est la seule proposée par l'imageur
  pour ce modèle (CPU ARMv6, pas de 64-bit), c'est normal.

Dans les réglages (⚙) de l'imageur : activer **SSH**, définir l'utilisateur
**`minitel`** + un mot de passe, et renseigner le **WiFi** initial. Le projet est
entièrement *headless* : ni écran ni clavier branchés sur le Pi.

### 2. Se connecter au Pi en SSH

Depuis Windows, ouvrir **PowerShell**. Trouver l'adresse IP que le Pi a prise sur
le réseau (le filtre cible les adresses MAC des Raspberry) :

```powershell
arp -a | Select-String "b8-27-eb|dc-a6-32|e4-5f-01|d8-3a-dd|2c-cf-67|28-cd-c1"
```

> Astuce : pour coller dans PowerShell, faites un **clic droit** sur la ligne de
> commande.

Se connecter en remplaçant l'IP par celle obtenue ci-dessus :

```powershell
ssh minitel@192.168.1.42
```

Saisir le mot de passe défini à l'étape 1. C'est normal qu'**aucun caractère ne
s'affiche** pendant la frappe : c'est une sécurité.

### 3. Installer MINITEL GPT

> Ce fork utilise un environnement uv partagé : voir [installation et migration](docs/RUNTIME.md).

Depuis le terminal, `UPDATE`, `MISE A JOUR` ou `MAJ` ouvre la
[maintenance du mainframe](docs/TERMINAL_UPDATES.md), avec confirmation OUI/NON
et procédure Alcatel à défilement automatique.
> Pour développer et tester : [guide de test](docs/TESTING.md).

Une fois connecté au Pi, mettre à jour le système et installer **git** (qui sert
ensuite à garder le Pi sur la dernière version) :

```bash
sudo apt update
sudo apt install git
```

Télécharger le projet :

```bash
cd /home/minitel
git clone https://github.com/perjules/minitel-gpt.git
cd minitel-gpt
```

Lancer l'installation (dépendances + services systemd) :

```bash
sudo bash install.sh
```

Puis démarrer les services :

```bash
sudo systemctl restart minitel-chatgpt admin-ui wifi-manager
```

**Voilà, c'est fini.** L'écran d'accueil s'affiche sur le Minitel (sur un
**Minitel 2**, faire `Fnct + Sommaire` à chaque allumage). Il reste à saisir la
clé API depuis l'admin (étape 4).

Le script `install.sh` se charge de **tout** : paquets système, dépendance Python
`pyfiglet` **et ses polices** via le runtime uv partagé, désactivation du
`dnsmasq` système (conflit hotspot), **redirection DNS du portail captif**
(`config/dnsmasq-shared-captive.conf` copié dans
`/etc/NetworkManager/dnsmasq-shared.d/`, sans quoi le téléphone n'ouvre pas le
portail tout seul), groupe `dialout` (accès au port série FTDI), création de
`config/prompts.json` et d'un `.env` par défaut s'ils manquent, règle sudo
(l'admin redémarre les services), et activation des 3 services systemd.

> Le script tolère l'absence de réseau (`apt-get update` en échec non bloquant) :
> une installation uv hors ligne nécessite également le binaire uv et un cache
> complet des dépendances. Le démarrage des services ne télécharge rien.

> Le dossier est un **clone git**, ce qui permet la mise à jour en un clic depuis
> l'admin (onglet **Paramètres**). Ne pas remplacer les fichiers à la main.

### 4. Renseigner la clé API

Aucun fichier à éditer à la main : ouvrir l'**admin web** (voir plus bas), onglet
**Paramètres**, choisir le **fournisseur d'IA** (**Mistral**, **Claude** ou
**Linkup**) et coller la clé correspondante. Un menu propose pour chacun les
modèles (ou, pour Linkup, les profondeurs de recherche) disponibles avec leur
coût et leur pertinence.

- Clé **Mistral** : <https://admin.mistral.ai/organization/api-keys>
- Clé **Claude** : <https://platform.claude.com/>
- Clé **Linkup** : <https://app.linkup.so>

Modèles Claude proposés (prix entrée / sortie par million de tokens) :

| Modèle | Prix | Usage |
|---|---|---|
| Claude Haiku 4.5 | 1 $ / 5 $ | le moins cher et le plus rapide, recommandé |
| Claude Sonnet 5.5 | 2 $ / 10 $ | équilibre vitesse / intelligence |
| Claude Opus 5.5 | 4 $ / 20 $ | le plus pertinent, un peu plus lent |

> Sonnet 5.5 et Opus 5.5 réfléchissent par défaut avant de répondre : le terminal
> leur fixe un effort **bas** (`output_config.effort = low`) pour garder des
> réponses rapides et courtes (2 à 4 s mesurées depuis un Pi Zero W).

> ⚠️ **Mistral : « erreur de connexion » avec une clé pourtant valide** - si
> l'API répond `429 Rate limit exceeded` avec une limite de **0 requête/minute**,
> aucune offre n'est activée sur l'espace de travail de la clé. Activer l'offre
> gratuite *Experiment* (ou *Scale*) dans <https://admin.mistral.ai>, rubrique
> Billing, puis patienter quelques minutes.

> ⚠️ **Linkup** est un moteur de **recherche web**, pas un générateur de texte :
> à la différence de Mistral et Claude, la personnalité (prompt système) et
> l'historique de conversation ne s'appliquent pas dans ce mode. Chaque question
> devient une recherche web isolée, dont la réponse sourcée sert de réponse du
> terminal - utile pour une personnalité « actualités / infos factuelles ».

> Le `.env` (créé automatiquement par l'installation) peut aussi être édité
> directement, mais l'admin est plus simple. Variables disponibles :
> `LLM_PROVIDER` (`mistral`, `claude` ou `linkup`), `MISTRAL_KEY`, `MISTRAL_MODEL`,
> `ANTHROPIC_KEY`, `CLAUDE_MODEL`, `LINKUP_KEY`, `LINKUP_DEPTH`.

### Calculateur

Le [calculateur](docs/CALCULATOR.md) reconnaît les expressions arithmétiques simples
ou s'ouvre avec CALC puis ENVOI. Il calcule localement, avec un bref théâtre
mathématique. La division par zéro réserve une scène générée de panne et de
maintenance fictives, interruptible par les touches existantes.

### Mémoire, notes et listes

La [mémoire légère](docs/MEMORY.md) ajoute un nom facultatif au démarrage :
vide sélectionne le profil MinitelGPT, sans authentification.
Chaque profil dispose de sa propre mémoire; aucun espace commun n’est proposé.
Préférences, notes,
listes et tâches se gèrent en langage naturel. Les profils nommés bénéficient
aussi d’un apprentissage périodique et d’un rappel sémantique Mistral; les commandes
RETIENS/OUBLIE restent prioritaires. SQLite conserve les souvenirs et leurs vecteurs.
Maintenance propose un inventaire et des exports après confirmation du mot de passe admin.
Un [premier accueil conversationnel](docs/ONBOARDING.md) présente le Minitel aux
nouveaux profils nommés et apprend quelques repères personnels, avec des questions
adaptées et facultatives. PASSER, PLUS TARD et FIN ACCUEIL permettent de le guider. Sur le Minitel, SOMMAIRE revient à
une conversation vide en conservant l'utilisateur et sa langue; LOGOUT puis ENVOI
permet de changer d'utilisateur. Le démarrage fictif en français précède la
connexion et n'est pas rejoué après SOMMAIRE ou LOGOUT. Les journaux de conversations
visibles sont conservés séparément sur le Pi, hors Git. Voir le guide pour
le test admin, le stockage et l'acceptation avant validation physique.

### Mettre à jour le code (après installation)

Le plus simple : depuis l'**admin web** → onglet **Paramètres** → *Vérifier les
mises à jour* puis *Mettre à jour maintenant* (voir [Mise à jour depuis
l'admin](#mise-à-jour-depuis-ladmin)).

En ligne de commande, c'est équivalent à :

```bash
cd /home/minitel/minitel-gpt
git pull
sudo systemctl restart minitel-chatgpt admin-ui wifi-manager
```

---

## Services systemd

| Service | Rôle |
|---|---|
| `minitel-chatgpt` | Terminal : lit le clavier Minitel, interroge l'IA (Mistral, Claude ou Linkup), affiche la réponse paginée |
| `wifi-manager` | Connexion WiFi autonome + hotspot de provisioning (portail captif) |
| `admin-ui` | Interface web d'administration (port 8080) |

```bash
sudo systemctl status minitel-chatgpt     # état
sudo systemctl restart minitel-chatgpt    # redémarrer
tail -f logs/chatgpt.log                  # logs
```

> ⚠️ Ne pas lancer le script du terminal à la main en parallèle du service :
> deux instances se disputeraient le port série. Toujours
> `sudo pkill -9 -f minitel_chatgpt` avant un lancement manuel de debug.

---

## Interface d'administration

### Tester depuis un terminal, sans Minitel

La [console de test](docs/CONSOLE.md) lance la **vraie boucle du Minitel** dans
un terminal Bash/SSH, sans ouvrir le port série : démarrage, historique, routage
des fonctionnalités et pagination restent identiques. Depuis le dépôt :

```bash
.venv/bin/python scripts/terminal_console.py
```

Utiliser `.venv/bin/python` à la place de `python3` si les dépendances de
l'installation sont dans ce venv. Entrée = ENVOI, Ctrl-N = SUITE, Ctrl-P =
SOMMAIRE, Ctrl-G = GUIDE, Ctrl-R = RETOUR, Ctrl-Q = quitter. Terminal d'au moins
40 colonnes et 24 lignes. Ce sont des appels et des actions réels, avec la mémoire
de l'installation; le test de personnalité web reste un aperçu distinct.

`http://<ip-du-pi>:8080` - mot de passe par défaut **mistral** (`ADMIN_PASSWORD`).

Trois onglets :
- **Tableau de bord** : état des services, activation des personnalités
- **Personnalités** : créer / modifier / supprimer des presets, génération de
  prompt par IA, **fichiers de connaissance** (.txt) injectés dans le contexte,
  textes d'accueil personnalisables, **animation d'accueil composable** (titre
  ASCII pyfiglet par personnalité : un ou plusieurs mots, chacun avec sa
  police/taille, avec aperçu), **zone de test** pour essayer les requêtes
  sans le Minitel (mêmes prompt, connaissances et fournisseur d'IA)
- **Paramètres** : choix du **fournisseur d'IA** (Mistral, Claude ou Linkup) avec
  la clé et le modèle (ou la profondeur de recherche) de chacun (coût + pertinence
  indiqués), **mise à jour de l'application**, logs

Les personnalités sont stockées dans `config/prompts.json`, leurs fichiers de
connaissance dans `config/knowledge/<personnalité>/`.

### Mise à jour depuis l'admin

L'onglet **Paramètres** permet de mettre à jour le code directement depuis
GitHub, sans manipulation SSH :

1. **Vérifier les mises à jour** → `git fetch` + affichage du changelog des
   nouveautés disponibles.
2. **Mettre à jour maintenant** → `git reset --hard origin/main` puis
   redémarrage automatique des services.
3. **Revenir à la version précédente** → rollback vers le commit d'avant.

Tes données locales ne sont **jamais** écrasées : `prompts.json` (personnalités),
`.env` (clés) et `config/knowledge/` sont hors du suivi git. Le dépôt fournit un
`config/prompts.default.json`, recopié seulement si `prompts.json` est absent.

> Prérequis : le dossier `~/minitel-gpt` du Pi doit être un **clone git** du dépôt
> (`git init` + `remote add origin` + `fetch` + `reset --hard origin/main`).

L'adresse de l'admin est aussi consultable **sur le Minitel via la touche Guide**.

---

## WiFi autonome

- Au démarrage, le Pi rejoint un réseau connu.
- Sans réseau connu pendant ~2 min, il bascule en **hotspot ouvert
  `MinitelGPT-Setup`** (IP `192.168.4.1`).
- Se connecter au hotspot ouvre automatiquement le **portail captif** :
  on choisit le réseau du lieu, le Pi s'y connecte et coupe le hotspot.
  L'ouverture automatique repose sur la redirection DNS installée par
  `install.sh` ; à défaut, ouvrir `http://192.168.4.1` dans le navigateur du
  téléphone (et accepter de rester connecté à un réseau « sans internet »).
- L'IP du Pi est ensuite consultable sur le Minitel (touche **Guide**).

---

## Arborescence

```
services/
  minitel_chatgpt.py   terminal (boucle de chat, appel Mistral/Claude/Linkup)
  minitel_serial.py    abstraction série
  wifi_manager.py      provisioning WiFi + portail captif
  admin_ui.py          interface web d'admin
config/
  prompts.json         personnalités (local, hors git)
  prompts.default.json personnalités par défaut (copiées si prompts.json absent)
  dnsmasq-shared-captive.conf  redirection DNS du portail captif
  knowledge/           fichiers .txt par personnalité
  *.service            unités systemd
  minitel-gpt-sudoers  règle sudo pour l'admin
install.sh             installation
```

---

## Dépannage

| Symptôme | Cause probable |
|---|---|
| Rien ne s'affiche sur le Minitel | fil série délogé (cause n°1), jumper FTDI pas sur 5 V, broche 4/5 pontée par erreur, ou Minitel 2 resté dans le Répertoire (faire `Fnct + Sommaire`) |
| Minitel 2 : écran d'accueil vide après `Fnct + Sommaire` | normal - appuyer sur `Sommaire` pour afficher MINITEL GPT |
| Affichage 80 colonnes qui défile sans pagination | Minitel 2 passé en mode téléinformatique via `Fnct + T A` - éviter cette combinaison (rester en `Fnct + Sommaire`) |
| Charabia à l'écran | vitesse Pi ≠ vitesse Minitel (rester à 1200 bauds des deux côtés) |
| Le hotspot n'apparaît pas | service `dnsmasq` système actif (port 53) → le désactiver ; au tout premier démarrage, patienter 5 à 10 min (installation + redémarrage) |
| Hotspot visible mais le portail ne s'ouvre pas tout seul | `/etc/NetworkManager/dnsmasq-shared.d/minitel-captive.conf` absent → relancer `sudo bash install.sh` (ou ouvrir `http://192.168.4.1`) |
| Aperçu du titre d'accueil : « Erreur » avec le nom de la police | polices figlet absentes → `sudo apt install figlet toilet-fonts` (fait par `install.sh`) |
| Mistral : « erreur de connexion » | voir la note 429 de l'étape 4 (offre Mistral non activée) |
| Caractères doublés à la saisie | écho local du Minitel + écho logiciel (ne pas ré-écho côté Pi) |
| Touches de fonction sans effet | Minitel 2 en mode téléinfo (touches VT100, gérées) ou octet `0x13` isolé (corrigé) |

---

*Projet personnel de Jérôme Hérard.*

## PRONOTE (optionnel)

Lecture locale des devoirs et notes via un compte parent connecté par QR.
Installation, autorisation des enfants et tests : [docs/PRONOTE.md](docs/PRONOTE.md).

## Gmail (prototype optionnel)

Lecture et recherche Gmail, résumé du message sélectionné et réponse locale avec
signature fixe et confirmation explicite `ENVOYER` après pagination complète.
OAuth Desktop sur ordinateur, credentials chiffrés systemd, aucun mot de passe
Google et aucune modification de boîte mail. Installation, enrollment et contrôles
réels Pi/Minitel : [docs/GMAIL.md](docs/GMAIL.md).
