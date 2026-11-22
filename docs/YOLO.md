# YOLO au Minitel

YOLO transforme une consigne en application web locale. Son superviseur et les
données des applications appartiennent à `perjules/mainframe-maintenance`.
Le Minitel utilise une API de contrôle sur `127.0.0.1`, indépendante des
applications générées et de leur accès Wi-Fi.

Après avoir commissionné le superviseur, rendez `/etc/yolo/control.token`
lisible par le compte du service Minitel. Ajoutez à son environnement de
production `YOLO_TOKEN_FILE=/etc/yolo/control.token` et éventuellement
`YOLO_API_PORT=8766` si vous avez changé le port. Le réglage est désactivé
sans fichier indiqué. Ne copiez pas le contenu du jeton dans `.env`.

Tapez `:YOLO` puis ENVOI dans la conversation. Le préfixe doit être en première
position. Vous pouvez aussi saisir `:`, puis ENVOI, puis `YOLO` et ENVOI.

| Commande du panneau | Effet |
| --- | --- |
| `NOUVEAU depenses` | Saisir ensuite la consigne de création |
| `MODIFIER depenses` | Saisir ensuite la modification |
| `ETAT id` / `TACHES` | Suivre un travail / retrouver son identifiant |
| `ANNULER` | Demander l'annulation depuis l'écran d'état |
| `LISTE` / `FICHE depenses` | Applications / détails et version active |
| `QR depenses` | Afficher le QR de l'application existante |
| `VERSIONS depenses` | Lister les versions numérotées |
| `RETABLIR depenses 2` | Tester et réactiver la version, avec confirmation |
| `SUPPRIMER depenses` / `PURGER depenses` | Supprimer avec confirmation, en conservant / en effaçant les données |
| `RETOUR` | Revenir au panneau ou à la conversation |

SOMMAIRE revient à l'accueil à tout moment. Une déconnexion ou un RETOUR
continue de laisser les tâches soumises s'exécuter côté superviseur.
L'écran d'état actualise uniquement la ligne de statut sur le terminal doté
de l'éditeur Videotex. En fin de construction il affiche le QR local. Le
téléphone doit être sur le Wi-Fi prévu par le superviseur.

Avec Mistral Conversations, « Crée une application web de calculatrice »
peut proposer une construction. « Modifie l’application web depenses pour … »
nomme explicitement l'application. La confirmation `OUI` + ENVOI est
toujours requise pour les propositions du modèle. L’agent reçoit la consigne
de l’utilisateur, sans historique de conversation ni données d’autres services.
