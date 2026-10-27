# Console d’administration 3615 IA

Quatre sections : **Tableau de bord**, **Configuration IA**, **Services**,
**Maintenance**. Interface Flask/HTML/CSS/JavaScript, sans dépendance ajoutée,
sans framework ni nouvelle surveillance permanente. Les mesures sont prises
à l’ouverture/actualisation et les vérifications réseau sur demande explicite.

## Tableau de bord

Température CPU (capteur système), jauge native de disque et espace libre,
uptime, état du service Minitel et de l’administration, Wi-Fi, IP locale et
Tailscale. Une mesure indisponible reste indiquée sans bloquer la page.
Un processus actif ne prouve pas la présence du Minitel physique.
Version compacte, fournisseur/modèle configurés et cinq lignes récentes du
journal. Les liens Gérer ouvrent directement le bon bloc de configuration ou
de maintenance. La personnalité active n’est pas affichée ici.

## Configuration IA et tests

Mistral reste le chemin normal ; Claude et Linkup restent disponibles. Les
modèles Mistral/Claude sont chargés côté serveur à l’ouverture de leurs réglages,
avec la clé enregistrée ou une nouvelle clé saisie sans la sauvegarder. Aucun
appel de catalogue depuis le tableau de bord. Actualisation explicite possible,
filtrage des modèles Mistral non conversationnels, pagination Claude bornée.
La sélection est conservée en cas d’erreur ; identifiant manuel disponible.
Les identifiants sont validés sans liste statique qui bloquerait les nouveautés.
Linkup conserve les profondeurs existantes. Aucun prix figé n’est affiché.

Les outils de personnalité, consignes, accueil, connaissances et génération de
prompt sont dans une zone avancée fermée. Les fichiers et le schéma général
restent identiques. Les brouillons sont signalés et la navigation locale les
conserve. Un avertissement précède leur abandon. Enregistrer un preset actif,
changer les réglages IA ou ses connaissances conserve le redémarrage automatique
existant. Le message distingue sauvegarde réussie et redémarrage non confirmé.
Un prompt système vide conserve les consignes précédentes, comme auparavant.

La console `/test` propose Conversation et Gmail. Elle partage le contexte
utilisateur de mémoire/journal ; ce nom n’est pas une authentification ni un
sélecteur de boîte mail. Le bouton avancé Tester ce brouillon ouvre une fenêtre
séparée, sans sauvegarde ni activation. Les effets de calcul et l’annulation des
réponses tardives sont conservés. Les requêtes sont réelles quand un opérateur
utilise la console ; les tests automatisés utilisent des réponses simulées.

## Services

Cartes uniformes avec état d’activation, contrôle et page Configurer. Actif signifie
activé dans la configuration, pas connexion distante vérifiée.

- Gmail : `/services/gmail`, compte unique partagé, contrôle d’activation et
  vérification explicite en lecture seule. L’enrôlement chiffré existant reste
  utilisé. Le changement d’activation est enregistré dans `.env`, appliqué au
  processus admin et demande le redémarrage du service Minitel. Les identifiants
  déjà livrés à admin-ui restent nécessaires ; le guide d’enrôlement explique
  l’activation des deux services. `/gmail` reste compatible avec le test existant.
- PRONOTE : `/pronote`, comptes parents existants, enrôlement QR replié, table
  Alias / Compte / Nom exact, éditeur JSON avancé conservé. Aucun enfant autorisé
  automatiquement. Activation et changements sont pris en compte aux prochaines
  requêtes selon le backend existant.
- Alexa, Imprimante, Maison connectée : administration inactive, contrôle désactivé,
  pages En construction. Le code d’impression du terminal n’est pas désactivé.

Les associations par utilisateur sont des champs désactivés réservés à une
évolution future. Gmail reste mono-compte ; PRONOTE conserve ses comptes parents
multiples. Aucun nouveau stockage de comptes ni routage utilisateur n’est simulé.
Les tickets de revue, la pagination, la modification et l’envoi explicite Gmail
restent gérés par le backend existant. Aucune vérification de connexion n’envoie
un mail ni ne marque un message comme lu.

## Atelier privé

Adresse directe **`/admin/rose`**, absente des cartes et liens ordinaires. Session
admin puis mot de passe Rose existant ; valeur absente/vide = accès refusé.
Déverrouillage de vingt minutes d’inactivité, bouton Verrouiller et fermeture à
la déconnexion. Toutes les opérations, lectures privées et écritures restent
protégées ; les textes ne sont pas intégrés aux pages ordinaires.

Catalogues normal/test, édition et ajout de profils, prompt commun et banque
E1–E5/S1–S5. Les sauvegardes utilisent `config/rose-local.json`, ignoré par Git,
privé (0600), verrouillé et remplacé atomiquement. Les fichiers éditoriaux suivis
restent les défauts intacts. Les profils sont relus au début d’une nouvelle
session ; prompt commun et niveaux partagés aux requêtes suivantes. Les profils
historiques sans niveaux explicites conservent leur comportement original.

La séparation des niveaux reproduit exactement la voix des 60 profils actuels.
Les variantes rédigées différemment sont conservées dans les paramètres avancés
`e_prompt_override` / `s_prompt_override`. Les vider fait suivre la définition
partagée ; changer le niveau d’un profil abandonne sa variante de cet axe.
La sauvegarde de la banque migre les références des deux catalogues dans la
même écriture atomique. Aucun texte n’est modifié en ouvrant l’atelier.

Les fichiers dans `logs/rose` sont consultables uniquement après déverrouillage :
liste bornée et derniers 64 Kio du fichier choisi, sans lien symbolique ni fichier
spécial. Aucune suppression. Le stockage ordinaire expose seulement des totaux
génériques. Les références incidentes dans les journaux techniques ordinaires
ne sont pas filtrées.

## Maintenance et déploiement

Blocs système, logiciel (`#software`), journaux (`#logs`), stockage (`#storage`).
Journaux Terminal / Administration / Wi-Fi / Déploiement, 40/100/200 lignes,
actualisation manuelle. Rapport `/diagnostics.txt` : versions, états et mesures,
sans contenu des conversations, clé API ou configuration privée.

La vérification Git reste explicite, datée et propre à la session du navigateur.
L’historique proposé est borné. Une vérification échouée retire la liste périmée.
L’état du déploiement vient du fichier géré par le helper. Planifié ne signifie
pas terminé. La version précédente indique sa branche et sa disponibilité.

Le changement de branche comporte une prévisualisation puis confirmation du
commit exact sur origin. Il vérifie une copie propre, le commit réellement
installé, les commits locaux, la branche distante et les collisions avec des
fichiers privés et avec les fichiers locaux non suivis ou ignorés. L’environnement est préparé avant changement des services et
de la branche. Un échec d’activation restaure branche, code, environnement et
configuration système. Le rollback retient aussi la branche précédente. Choisir
la branche et le commit déjà installés ne reconstruit ni ne redémarre rien.
Les branches cibles doivent contenir les fichiers du runtime partagé ; une
branche incompatible échoue pendant la préparation, avant activation.

Les commandes privilégiées restent fixes. Le changement de branche reçoit un
petit document JSON sur stdin ; nom de branche et SHA sont validés dans le
helper root. Aucune commande shell arbitraire ni URL de dépôt fournie par le web.
Les actions Pi reboot/poweroff sont des travaux systemd différés de trois secondes,
refusés pendant un déploiement. Les commandes Minitel cachées restent inchangées.

## Première installation de ces contrôles sur le Pi

Après publication autorisée et sélection d’une branche contenant ces changements :

1. Définir une valeur explicite non vide pour `ADMIN_PASSWORD` dans `.env` avant
   de redémarrer l’administration. Il n’existe plus de mot de passe de secours.
   Une connexion avec variable absente/vide est refusée avec une page de setup.
2. Depuis cette branche propre, relancer `sudo bash install.sh` avec `--branch`
   correspondant à la branche courante. Cette exécution manuelle installe les
   nouvelles règles sudo et les copies root des helpers, notamment la sélection
   du journal de déploiement et les opérations de branche/puissance. Un simple
   remplacement de `admin_ui.py` ne suffit pas.
3. Vérifier sur le Pi les permissions de journaux, les températures/adresses,
   les cartes et activations, le Minitel et l’interface sur téléphone. Tester
   ensuite une préparation refusée, un changement de branche et un rollback.
   Tester les commandes de puissance uniquement en présence de l’appareil.

L’installateur n’affiche plus de mot de passe par défaut et protège `.env` en 0600.
Ces étapes ne sont pas exécutées sur le poste de développement.

## Validation hors Pi — 27 octobre 2026

- Synchronisation uv verrouillée, sans changement de dépendances.
- Suite complète : **601 tests et 402 sous-tests** passent. Comptes synthétiques,
  API simulées, Git réel dans des dépôts temporaires locaux et opérations système
  simulées ; aucun envoi, impression ni changement réseau/puissance réel.
- Ruff sur le dépôt, exports requirements, `git diff --check`, `bash -n install.sh`.
- Treize états de pages rendus : HTML équilibré, identifiants uniques, aucun
  formulaire imbriqué. Scripts et attributs d’événements vérifiés avec Node.
- Test JavaScript exécuté pour les réponses tardives et les animations du calcul.
- Les quatre tests Wi-Fi obsolètes portent maintenant sur le provisionnement au
  démarrage et l’attente du serveur HTTP ; aucune surveillance périodique rétablie.
- Validation visuelle dans un navigateur et validation physique **non effectuées**.
  Chromium était absent et son téléchargement a échoué dans cet environnement.
  Les catalogues fournisseurs n’ont pas été interrogés avec des clés réelles.

Sauvegarde/restauration, nettoyage manuel, routage multi-compte et intégrations des
placeholders restent différés. Aucun push ni merge effectué pendant ces travaux.
