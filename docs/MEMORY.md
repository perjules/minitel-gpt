# Mémoire personnelle — 3615 IA

Chaque profil possède ses souvenirs. Aucun espace partagé, aucun héritage global.
Un nom vide sélectionne MinitelGPT, un profil distinct dont l’apprentissage
**automatique** est désactivé. Les noms et alias sélectionnent une identité;
ils ne constituent pas encore une authentification. Voir [USERS.md](USERS.md).

## Utilisation

Le démarrage fictif reste en français, puis le terminal demande le nom et, si
nécessaire, la langue. La langue est enregistrée pour les profils nommés;
MinitelGPT démarre en français. SOMMAIRE recommence la conversation avec le même
profil/langue et soumet les énoncés restants au traitement. LOGOUT revient au choix
du nom. Les souvenirs persistent; les conversations/brouillons en cours ne sont
pas rejoués. La console de test utilise les mêmes profils et le même stockage.

L’apprentissage automatique traite les affirmations personnelles claires et les
préférences durables des profils nommés. Il ne doit pas transformer une hypothèse,
une question ou une réponse inventée par l’assistant en fait personnel. Les
souvenirs automatiques gardent leurs citations et leur origine pour inspection.
Ces règles encadrent un modèle, sans garantir une extraction parfaite.

Les commandes explicites restent prioritaires :

| Demande | Résultat |
| --- | --- |
| RETIENS que je préfère les réponses courtes | Préférence enregistrée immédiatement |
| REMEMBER that I prefer short answers | Même opération en anglais |
| OUBLIE mon ancienne adresse / FORGET my old address | Retrait de l’élément identifié |
| Corrige : les décorations sont au grenier | Correction avec cible validée |
| Garde cette recette sous le nom naan | Réponse précédente complète, sans résumé |
| Rappelle la recette naan | Note complète, paginée si nécessaire |
| Ajoute des piles à ma liste de courses | Entrée de liste |
| J’ai acheté les piles | Entrée marquée faite, clarification si nécessaire |
| MEMOIRE / MEMORY | Tous les éléments du profil, sans appel IA |
| LISTE courses / LIST courses | Liste nommée, sans appel IA |

Le langage naturel utilise toujours le modèle Mistral général pour interpréter
les opérations explicites. Sans lui, MEMOIRE et LISTE restent disponibles; une
opération naturelle échouée ne modifie rien. Une cible ambiguë demande de choisir;
une sélection devenue obsolète est refusée. Les notes longues sont conservées
entières; une correction fondée seulement sur un extrait doit être reformulée
comme remplacement complet explicite.

Une modification explicite annule les énoncés automatiques encore en attente
avant elle, et invalide les résultats déjà en cours. Ce choix conservateur évite
qu’un traitement tardif recrée un souvenir oublié. Un tour ordinaire commencé
avant cette modification ne peut pas non plus réintroduire ses anciens énoncés
lorsque sa réponse termine plus tard. De nouveaux énoncés ultérieurs
peuvent être appris. Supprimer un souvenir ne supprime pas ses anciennes
conversations, ses sauvegardes ou les extraits déjà reçus par un modèle.

## Traitement automatique, sans service supplémentaire

Les données de travail sont des **énoncés utilisateur**, pas les affirmations de
l’assistant ni un balayage des anciens journaux. Le programme n’envoie au
consolidateur que les textes admissibles; Python fixe l’utilisateur, vérifie les
sources/identifiants et écrit le résultat dans une transaction.

- Après six énoncés ordinaires, le lot devient prêt. La clôture rend aussi prêt
  un lot plus court; une nouvelle session reprend le travail interrompu.
- Un seul thread ponctuel par processus traite au plus deux passages bornés.
  Chaque passage traite au plus six énoncés et vectorise au plus huit souvenirs.
  Un bail SQL de 90 secondes évite que terminal et admin traitent le même lot.
- Aucun daemon ni boucle de surveillance. Les interactions/reprises/clôtures
  déclenchent le travail. Un reste de file attend le prochain déclenchement.
- Après échec, les énoncés restent en file et une nouvelle tentative est différée
  d’au moins 60 secondes. Une tentative interrompue peut reprendre après le bail.
- La file est limitée à 48 énoncés par profil, 2000 caractères par énoncé. Une file
  pleine n’accumule plus de nouveaux énoncés; son état est visible dans l’admin.
- Un lot validé est retiré de la file. Les souvenirs retenus conservent seulement
  leurs citations justificatives, dates et références de session/énoncé.

Seuls faits et préférences peuvent être créés automatiquement. Les notes, listes,
tâches et sauvegardes de réponses complètes restent explicites. Les propositions
malformées, sans citation exacte ou visant un ID inconnu sont rejetées ensemble.
La pertinence logique d’une citation reste une limite du modèle.

Gmail, PRONOTE, les modes privés, les scènes fictives, les secrets détectés et les
commandes mémoire sont exclus de cette collecte automatique. La console web
respecte aussi l’exclusion des tours PRONOTE. Une sauvegarde **explicitement**
demandée reste possible via les commandes mémoire. MinitelGPT et les profils
désactivés ne produisent pas de souvenirs automatiques.

`.env` peut contenir `MINITEL_MEMORY_AUTO=0` pour suspendre l’apprentissage;
redémarrer admin-ui et minitel-chatgpt après modification. Cela n’efface ni les
souvenirs ni la file, et ne désactive pas les commandes explicites ou l’indexation.
La clé/modèle Mistral général existant sert au consolidateur; les réglages Gmail
n’interviennent pas. Aucun mot de passe utilisateur n’est ajouté dans cette phase.

## Rappel sémantique

Les textes et leurs vecteurs sont dans SQLite. `mistral-embed` génère des vecteurs
normalisés de 1024 composantes, stockées en float32. Le modèle et l’empreinte du
texte accompagnent chaque vecteur; un texte modifié invalide son vecteur. Une
corruption ou un changement de modèle provoque une reconstruction ultérieure.
Les vecteurs ne remplacent jamais le texte original et ne sont pas exportés.

Pour une réponse ordinaire, le programme vectorise la question et au plus deux
courts messages récents du canal chat, recherche uniquement les souvenirs actifs
du profil sélectionné, puis transmet leurs textes au modèle de réponse. La
comparaison est un parcours exact des vecteurs, sans serveur/index vectoriel tiers.
Les demandes d’embedding sont bornées à quatre secondes. Les textes vectorisés
sont limités aux premiers 6000 caractères; les notes plus longues restent complètes
en base et dans le rappel explicite.

La sélection combine jusqu’à trois préférences, six résultats sémantiques et trois
résultats lexicaux, dédoublonnés dans le budget de contexte existant. Le seuil
cosinus initial est 0,55 : il reste à calibrer sur des conversations réelles.
Un résultat proche est une proximité de sens, pas une preuve. Les nombres,
négations, contradictions et informations devenues anciennes demandent toujours
une interprétation prudente. Aucun résultat pertinent est une réponse valide.

Sans clé, vecteur disponible ou API opérationnelle, le rappel utilise les textes
locaux. Les nouveaux souvenirs restent enregistrés même si leur indexation échoue.
MEMOIRE/LISTE ne vectorisent pas la demande. Les souvenirs ne sont pas injectés
automatiquement dans les mails ni les requêtes scolaires. Linkup conserve son
fonctionnement de recherche isolée, sans prompt mémoire.

Limites par profil conservées : 256 éléments / 1 Mio; contenu de 32000 caractères,
titre de 160. Les réponses ordinaires reçoivent au plus environ 4000 caractères,
avec extraits de 900 caractères. Le planificateur explicite reçoit un catalogue
borné à environ 14000 caractères, avec extraits de 2000 caractères; un catalogue
partiel ne prouve pas l’absence d’un élément. Les éléments terminés restent
consultables explicitement, mais sont exclus du rappel automatique.

## Stockage et migration

Racine par défaut : `/home/minitel/.local/share/3615-ia` pour le compte minitel.
Option : `MINITEL_MEMORY_DIR=/chemin/hors/git` dans `.env`, identique pour les deux
services. Aucun souvenir n’est placé dans Git ou dans les cookies.

| Élément | Contenu |
| --- | --- |
| `users.sqlite3` | Identités/droits existants, souvenirs et métadonnées mémoire |
| `memory_items` | Texte, propriétaire, type, origine, citations, état, vecteur |
| `memory_state` | Révision/langue, apprentissage, barrière explicite, bail et état |
| `memory_events` | Énoncés admissibles en attente, retirés après traitement |
| `memory_schema` | Version du sous-schéma mémoire, actuellement 1 |
| `users/<clé-stable>/logs/*.jsonl` | Archives de conversations visibles |
| `shared/logs/*.jsonl` | Archives du profil MinitelGPT uniquement |
| `<profil>/memory.before-sqlite.json` | Copie intacte de l’ancien fichier importé |
| `<profil>/memory.json` | Marqueur de migration, pas le fichier à éditer |

Le sous-schéma mémoire ne change pas la version 2 du registre utilisateurs/droits.
Ses futures migrations doivent vérifier leur version et préserver les données;
une version inconnue est refusée. Les dossiers restent stables après changement
de nom/alias. Une base ou source invalide est signalée sans remplacement silencieux.

À la première lecture d’un profil, son ancien JSON est importé une seule fois,
avec IDs, langue et révision conservés. Les octets originaux sont archivés dans un
fichier privé; `memory.json` devient un marqueur version 2. L’ancien code JSON-only
refuse ce marqueur, ce qui évite de modifier une copie périmée après un rollback.
Les nouveaux profils reçoivent aussi un marqueur. Ouvrir l’inventaire admin importe
les profils connus; les dossiers historiques sans identité restent conservés et
signalés, puis sont importés lors de la prochaine connexion avec le nom d’origine.

Les données ne sont pas chiffrées. L’administrateur Linux peut les lire. Les textes
sélectionnés quittent le Pi pour les appels Mistral d’extraction, d’embedding et de
réponse. Les journaux techniques de ces opérations ne contiennent pas les textes,
clés, vecteurs ni réponses brutes des fournisseurs.

Les archives visibles restent en JSONL, séparées et jamais relues automatiquement
pour créer des souvenirs. Elles peuvent contenir les mails effectivement affichés.
Les tours scolaires ne sont pas archivés par les parcours terminal/test web actuels;
d’anciennes archives peuvent encore en contenir. Aucun nettoyage rétroactif n’est
fait. Les secrets configurés/détectés sont masqués, sans garantie de reconnaître
un secret arbitraire non déclaré. Les données privées de modes séparés ne sont
pas exposées par la page mémoire générale.

## Administration et export

**Maintenance → Mémoire des utilisateurs** affiche les profils, nombre d’éléments,
volume de texte, dernière modification, indexation, file et état du traitement.
Cela ne déclenche pas d’appel IA. Les profils désactivés restent inspectables.

Une nouvelle saisie d’ADMIN_PASSWORD ouvre pendant cinq minutes maximum, dans la
session admin courante, l’affichage des textes et l’export JSON individuel/global.
Le bouton Verrouiller et la déconnexion ferment cet accès. Le mot de passe Linux
n’est jamais demandé. Pages et téléchargements ne sont pas mis en cache.

L’export contient les textes, langue, IDs, origines et citations; pas les vecteurs,
journaux, credentials ou droits Gmail/PRONOTE. Il ne constitue donc pas une sauvegarde
complète du Pi. L’export global inclut les anciens dossiers sans identité et porte
`complete: false` si des données illisibles n’ont pas pu être exportées. Cette page
est en lecture seule, sans bouton d’édition/suppression.

## Nettoyage manuel et récupération

Avant le premier déploiement et avant une intervention manuelle, arrêter les deux
services puis sauvegarder **tout** le dossier de données. Pour le chemin par défaut :

```sh
sudo systemctl stop minitel-chatgpt admin-ui
cp -a /home/minitel/.local/share/3615-ia "/home/minitel/3615-ia-backup-$(date +%Y%m%d-%H%M%S)"
```

Adapter le chemin si MINITEL_MEMORY_DIR est défini. La base peut être ouverte avec
l’outil `sqlite3` (optionnel) ou le module Python standard `sqlite3`; aucun serveur
n’est requis. Par exemple, pour inventorier :

```sql
SELECT u.id, u.display_name, count(m.id) AS memories
FROM users u LEFT JOIN memory_items m ON m.user_id=u.id GROUP BY u.id;
SELECT id, kind, title, text FROM memory_items WHERE user_id=7;
```

Pour retirer un élément, adapter **les deux identifiants d’exemple** ci-dessous.
L’invalidation du travail antérieur doit accompagner une édition manuelle :

```sql
BEGIN IMMEDIATE;
DELETE FROM memory_events WHERE user_id=7;
UPDATE memory_state SET revision=revision+1, barrier=barrier+1,
 ready=0, lease_until=0, lease_token='', retry_after=0, last_status='manual'
 WHERE user_id=7;
DELETE FROM memory_items WHERE user_id=7 AND id='m_0123456789abcdef';
COMMIT;
```

Pour modifier un texte, utiliser UPDATE et mettre `embedding`, `embedding_model`
et `embedding_hash` à NULL dans la même transaction. Ne pas modifier les IDs,
les alias ou les droits à cette occasion. Les vecteurs seront reconstruits au fil
des prochains déclenchements. Les sauvegardes/journaux se nettoient séparément;
ne pas supprimer les identités pour retirer leurs souvenirs.

Redémarrer ensuite :

```sh
sudo systemctl start minitel-chatgpt admin-ui
systemctl is-active minitel-chatgpt admin-ui
```

La mise à jour normale de Maintenance suffit pour ce code, sans relancer
l’installateur. **Un rollback vers une version antérieure à la mémoire SQLite
ne convertit pas les données en sens inverse.** Restaurer une sauvegarde complète
prise avant migration pour retrouver exactement cet ancien état, ou préparer
hors ligne des JSON compatibles à partir de l’export. Ne pas simplement effacer
les marqueurs ni réactiver les sauvegardes JSON devenues anciennes. Un rollback
sans restauration conserve SQLite mais l’ancien code signale la mémoire indisponible.

## Validation

Les tests utilisent des profils synthétiques et des API simulées. Ils vérifient
migration/reprise/concurrence, intégrité, isolation, vecteurs périmés/corrompus,
repli local, sources automatiques, baux/retry/limites, priorité explicite et accès
admin/CSRF/expiration/export. Aucun appel Mistral/Google live, envoi de mail,
déploiement Pi ou validation matérielle n’est inclus dans ces résultats.

Sur le Pi : sauvegarder avant migration; mettre à jour; vérifier les souvenirs
existants dans Maintenance; tester deux profils et le défaut; reformuler un rappel;
faire six échanges puis SOMMAIRE/LOGOUT; vérifier origine et état; oublier un
souvenir pendant du travail en attente; vérifier l’export depuis le téléphone et
le repli local sans API. La qualité réelle de l’extraction/rappel et les temps
sur Raspberry Pi restent à mesurer avant promotion de develop vers main.
