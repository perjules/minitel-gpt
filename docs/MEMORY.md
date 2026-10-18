# Mémoire légère — 3615 IA

Au démarrage, entrez d'abord un nom puis **ENVOI**. Laissez vide pour la
mémoire commune. Le nom sélectionne des fichiers, **ce n'est pas une
authentification** : aucun mot de passe, ni contrôle d'accès supplémentaire.
La casse ne distingue pas deux noms; revenez avec le même nom après un redémarrage.
Choisissez ensuite la langue pour un nouveau nom : ce choix est enregistré dans
son `memory.json` et repris automatiquement aux démarrages suivants, sans nouveau
prompt de langue. Un nom existant sans langue enregistrée reçoit aussi ce prompt.
Un nom vide ouvre directement une session commune en français, sans demander la
langue ni conserver de préférence de langue globale.
Un timeout utilise le français sans enregistrer ce choix implicite. Pour refaire
le choix d'un utilisateur connu, retirez le champ `language` de son `memory.json`
pendant que les deux services sont arrêtés, puis redémarrez-les.
**SOMMAIRE** efface la conversation, les choix en attente et les brouillons Gmail,
puis permet un autre choix de nom, avec sa langue enregistrée. La mémoire reste.

## Demander, lire, préciser

Utilisez la saisie habituelle, **SUITE / RETOUR** et les résultats paginés.
Il n'y a pas de nouvel organisateur ni d'arborescence.

| Demande | Résultat attendu |
| --- | --- |
| « Retiens que je préfère les réponses courtes. » | Préférence enregistrée dans l'espace sélectionné |
| « Note que les décorations sont dans le garage. » | Fait ou note délibérément enregistré |
| « Corrige : les décorations sont au grenier. » | Modification de l'élément existant, avec confirmation du contenu |
| « Ajoute des piles à ma liste de courses. » | Nouvelle entrée à faire dans la liste courses |
| « J'ai acheté les piles. » | Entrée existante marquée faite; clarification si plusieurs correspondent |
| « Ajoute une tâche : appeler le garage. » | Tâche à faire; on peut ensuite la terminer ou la retirer |
| « Garde cette recette sous le nom naan. » | Note contenant la réponse précédente complète, pas un résumé |
| « Rappelle la recette naan. » | Contenu enregistré, paginé si nécessaire |
| « Qu'est-ce que tu sais sur moi ? » | Faits et préférences délibérément enregistrés |
| « Oublie les décorations. » | Suppression de l'élément existant |
| « Ajoute du lait à la liste commune. » | Entrée dans l'espace commun, même si un nom personnel est sélectionné |

Les confirmations indiquent **ce qui a changé et où**. En cas de choix numéroté,
répondez par le numéro, un ordinal (« le deuxième ») ou un nom distinctif.
Une cible inconnue n'est pas inventée. Une nouvelle demande abandonne le choix
précédent; une sélection devenue obsolète demande de reformuler.

Deux raccourcis ne nécessitent pas de modèle : **MEMOIRE** montre les éléments de
l'espace sélectionné; **LISTE courses** montre cette liste, y compris les entrées
faites. Pour un autre espace, dites explicitement « mémoire commune », « notes
partagées », « liste de courses commune » ou « shared shopping list ».
Les éléments d'autres utilisateurs ne sont jamais consultés. Les espaces commun
et personnel ne sont pas fusionnés automatiquement.

La préférence et les faits pertinents sont proposés aux réponses ordinaires
suivantes, y compris après redémarrage. Les souvenirs ne sont pas injectés
automatiquement dans un mail sortant. Pendant l'édition Gmail, les refinements du
brouillon gardent leur routage; dites **RETIENS** ou nommez une liste/mémoire pour
une demande mémoire explicite. Le modèle ne décide jamais du dossier ni d'un
chemin; Python fixe l'espace, valide les identifiants et effectue l'opération.

## Fichiers, limites et journaux

Par défaut, les données se trouvent sous `~/.local/share/3615-ia` pour le compte
qui exécute le service, normalement `/home/minitel/.local/share/3615-ia`.
Option : définir `MINITEL_MEMORY_DIR=/chemin/hors/du/depot` dans `.env` pour les
deux services. Un chemin situé dans le dépôt est refusé.

```text
shared/memory.json
shared/logs/date_session.jsonl
users/nom-empreinte/memory.json
users/nom-empreinte/logs/date_session.jsonl
```

Ce sont des fichiers texte UTF-8 lisibles : JSON indenté pour les éléments,
JSONL pour les conversations. L'empreinte stabilise les noms de dossiers; elle
ne protège pas leur contenu. Pas de base de données, d'index vectoriel ni de
dépendance supplémentaire. Les verrous de fichier évitent les pertes entre
admin et terminal; les écritures remplacent atomiquement un fichier synchronisé.
Les notes ne sont pas chiffrées. Les credentials Gmail continuent à suivre leur
stockage chiffré séparé existant.

Limites : 256 éléments / 1 Mio par espace; 32 000 caractères par contenu;
160 caractères pour un titre. Une note trop longue est refusée, jamais tronquée
sur disque. Les réponses ordinaires reçoivent au plus 4 000 caractères de mémoire,
avec priorité aux préférences et classement lexical simple des faits/notes.
Les éléments terminés ne sont pas proposés automatiquement. Les extraits sont
signalés comme tels; le rappel explicite restitue le texte complet. Le modèle
d'intention reçoit un catalogue borné (environ 14 000 caractères, extraits de 2 000
caractères) et au plus quatre messages visibles récents de 2 000 caractères.
Un catalogue partiel ne permet pas de conclure qu'un élément n'existe pas.
Pour corriger un contenu de plus de 2 000 caractères, donnez un remplacement
complet explicite (« Remplace entièrement la note naan par : ... »); une
correction fondée seulement sur son extrait est refusée pour préserver le reste.

Les journaux contiennent les demandes et les réponses complètes produites pour
l'affichage, datées en Europe/Paris, par utilisateur/session. Une réponse paginée
est enregistrée une fois, même si l'utilisateur quitte avant sa dernière page.
Ils peuvent donc contenir les textes Gmail ou PRONOTE effectivement affichés.
Les sélections de boîte, réponses d'API internes, plans du modèle, credentials,
tokens et théâtre de démarrage ne sont pas archivés comme conversations.
Les secrets configurés et les formes reconnues de secrets dans le texte visible
sont masqués; les demandes mémoire contenant de tels secrets sont refusées.
N'entrez pas de credentials dans la conversation : utilisez les connexions
administratives existantes. Les journaux ne sont **jamais** relus pour répondre,
ni transformés automatiquement en faits. Ils restent jusqu'à suppression manuelle.
Retirer un souvenir ne supprime pas l'historique correspondant.

Un fichier absent est un espace vide. Un fichier malformé est conservé et signalé,
sans écrasement automatique. Pour le réparer, arrêtez les deux services, sauvegardez
le dossier, corrigez le JSON en gardant le schéma, ou déplacez le fichier abîmé
pour repartir vide. Redémarrez ensuite. Sauvegardez ce dossier séparément de Git.
La mémoire ne prouve aucun fait externe et ne donne aucune autorité au modèle;
elle reste séparée des diagnostics fictifs du mainframe.

## Déploiement et acceptation

Cette fonctionnalité est publiée sur **develop**, sans promotion de main.
Vérifiez le commit à installer, puis sur le Pi :

```sh
cd /home/minitel/minitel-gpt
git status --short
git switch develop
git pull --ff-only origin develop
sudo systemctl restart minitel-chatgpt admin-ui
systemctl is-active minitel-chatgpt admin-ui
```

Si `git status` montre du travail local, conservez-le avant le pull. Aucun nouvel
enrôlement Google, changement de scope ni migration de credentials n'est requis.
Le modèle d'intention utilise la clé/modèle Mistral déjà configuré, même si le
fournisseur des réponses ordinaires est différent. Sans Mistral disponible,
MEMOIRE et LISTE restent utilisables; une demande naturelle échouée ne change rien.

Dans **Administration → Personnalités → Tester la personnalité**, saisissez un
nom; vide sélectionne le commun. **SOMMAIRE / nouvelle session** réinitialise le
test. Le champ est relu à chaque requête; changer de nom ferme l'ancienne session.
La page Gmail admin utilise la même sélection, indiquée au-dessus des résultats.
Les tests admin restent protégés par le login existant. Aucun souvenir n'est placé
dans le cookie. Après déploiement, harmoniser la notice de confidentialité publique
avec cette conservation des textes visibles avant un usage par d'autres personnes.

1. Choisir un nom de test; envoyer les exemples de préférence, fait, correction,
   recette, liste et achat. Vérifier le contenu et la destination confirmés.
2. Poser une question ordinaire : constater la préférence, puis rappeler le fait
   ou la recette complète. Tester deux cibles semblables et leur clarification.
3. Changer de nom : aucune mémoire personnelle précédente. Ajouter du lait à la
   liste commune, puis choisir vide et rappeler la liste. Revenir au premier nom.
4. SOMMAIRE puis redémarrer les deux services : les souvenirs restent, les choix
   en attente et conversations sont neufs. Examiner uniquement des journaux de
   test; vérifier leurs dates, leur séparation et le masquage avec un faux secret.
5. Vérifier Gmail (lecture, nouveau brouillon, réponse, pagination et gate ENVOYER),
   PRONOTE et recherche web sans envoyer un mail réel sauf échange owner-controlled
   délibéré. Répéter le démarrage, la pagination et le changement d'utilisateur
   sur le vrai Minitel lorsqu'il est déballé.

**Preuves de cette livraison :** tests offline avec modèle/API simulés, fichiers
réels, client Flask et décodeur UART réel. Ni échange avec un modèle live, ni
déploiement/validation admin sur le Pi, ni validation physique ne sont revendiqués.
Les tests physiques restent en attente. Commande de régression, dans le venv équipé
des dépendances optionnelles existantes :

```sh
.venv/bin/python -m pytest -v
```
