#!/usr/bin/env python3
"""Preview/merge a misspelled named profile into another one; local SSH use only."""

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import persistent_memory as memory
from services import user_merge


def require_stopped():
    for name in ("minitel-chatgpt", "admin-ui"):
        try:
            result = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise user_merge.MergeProblem("Impossible de vérifier l’arrêt des services avec systemctl.") from None
        if result.stdout.strip() not in {"inactive", "failed"}:
            raise user_merge.MergeProblem("Arrêter minitel-chatgpt et admin-ui avant --apply (et toute console terminal indépendante).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list existing profile IDs; never creates a user")
    parser.add_argument("--source", type=int, help="ID of the misspelled profile to remove")
    parser.add_argument("--target", type=int, help="ID of the existing profile to retain")
    parser.add_argument("--apply", action="store_true", help="apply the previewed direction, after stopping both app services")
    parser.add_argument("--data-dir", type=Path, help="override MINITEL_MEMORY_DIR")
    args = parser.parse_args(argv)
    if args.list and (args.source is not None or args.target is not None or args.apply):
        parser.error("--list cannot be combined with a merge")
    if not args.list and (args.source is None or args.target is None):
        parser.error("both --source and --target are required, or use --list")
    try:
        env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
        store = memory.MemoryStore(args.data_dir or os.getenv("MINITEL_MEMORY_DIR") or env.get("MINITEL_MEMORY_DIR"))
        if args.list:
            for user in user_merge.profiles(store):
                print(f"#{user['id']}  {user['display_name']}" + (" [désactivé]" if not user["enabled"] else ""))
            return 0
        plan = user_merge.preview(store, args.source, args.target)
        source, target = plan["source"], plan["target"]
        print(f"SOURCE SUPPRIMÉE : #{args.source} {source['user']['display_name']}")
        print(f"DESTINATION CONSERVÉE : #{args.target} {target['user']['display_name']}")
        print(f"Souvenirs après fusion : {plan['items_after']}; énoncés en attente : {plan['pending_after']}.")
        print("Tous les éléments conservés, y compris doublons/contradictions. Les alias source redirigeront vers la destination.")
        print("Accès/langue/accueil de destination conservés (langue source si destination sans langue).")
        print("Les accès source sont retirés, pas transférés. Les comptes partagés restent enregistrés.")
        print(f"Journal source archivé chiffré : {'oui' if source['journal_archived'] else 'non'}; brouillons source archivés : {source['drafts_archived']}.")
        if not args.apply:
            print("APERÇU SEULEMENT. Arrêter les services puis ajouter --apply pour effectuer la fusion.")
            return 0
        require_stopped()
        result = user_merge.merge(store, args.source, args.target)
        print("Fusion terminée. Rouvrir les sessions du profil conservé.")
        print("Sauvegarde : " + result["backup"])
        print("Archives source : " + result["archive"])
        return 0
    except (memory.MemoryProblem, OSError, ValueError, sqlite3.Error) as error:
        # Only fixed application messages; don't echo low-level paths, SQL or content.
        print(str(error) if isinstance(error, memory.MemoryProblem) else "Stockage indisponible; aucune fusion confirmée.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
