#!/usr/bin/env python3
"""Opt-in paid Mistral comparison on synthetic drafts. No Gmail access or sending."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from dotenv import dotenv_values

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from services.gmail_mail import SIGNATURE, GmailError, MailModel

CASES = {
    "conditional": (
        "Écris à Camille : je suis d'accord, mais uniquement si le devis reste sous 500 euros. Ne présente pas cela comme une commande ferme.",
        "Accord explicitement conditionnel, plafond exact, aucune commande ferme.",
    ),
    "refusal": (
        "Décline chaleureusement l'invitation de Camille, sans inventer d'excuse et sans fermer la porte à une prochaine occasion.",
        "Refus clair et chaleureux, aucune excuse ou autre date inventée.",
    ),
    "reminder": (
        "Demande à Camille où en est la réparation. Je commence à m'impatienter, mais ne sois ni agressif ni excessivement conciliant.",
        "Demande d'avancement ferme et polie, sans menace ni excuse excessive.",
    ),
    "negation": (
        "Dis à Camille que je n'ai pas refusé la proposition : je n'ai simplement pas encore pris ma décision.",
        "Ni refus, ni acceptation, ni date de décision inventée.",
    ),
    "reservation": (
        "Remercie Camille pour le travail. Je suis globalement satisfait, mais le problème du bruit reste à résoudre.",
        "Remerciement avec réserve explicite ; ne pas présenter le problème comme résolu.",
    ),
    "counteroffer": (
        "À Camille : mardi ne m'arrange pas. Propose plutôt jeudi après-midi, sans confirmer un horaire précis.",
        "Mardi décliné, jeudi après-midi proposé et non confirmé, aucune heure inventée.",
    ),
    "tone": (
        "Dis à Camille que le document est incomplet et que j'ai besoin de la version complète pour avancer. Factuel, sans reproche personnel.",
        "Document incomplet, action attendue explicite, aucun reproche personnel.",
    ),
    "no_promise": (
        "À Camille : je vais regarder si je peux aider, mais je ne promets rien et je ne veux pas qu'il compte déjà sur moi.",
        "Possibilité d'aider seulement ; aucun engagement ajouté.",
    ),
    "instruction": (
        "À Camille : je ne pourrai pas venir demain. Sois bref et évite les grandes excuses ; ceci est une consigne de rédaction, pas une phrase à inclure.",
        "Absence demain, formulation brève ; aucune consigne de style citée dans le mail.",
    ),
    "clarity": (
        "Explique à Camille que l'offre m'intéresse, mais que j'ai besoin de connaître le prix total avant de me prononcer. N'invente pas de montant.",
        "Intérêt sans acceptation ; demande du prix total, aucun montant inventé.",
    ),
}
PROFILES = (
    ("mistral-small-2603", "none"),
    ("mistral-small-2603", "high"),
    ("mistral-large-4-0", "medium"),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="explicitly authorize paid Mistral requests"
    )
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    args = parser.parse_args(argv)
    if not args.live:
        parser.print_help()
        print("No requests made. --live runs 3 paid requests per case; all runs 30.")
        return 0
    env = {
        **os.environ,
        **{
            k: v
            for k, v in dotenv_values(PROJECT / ".env", interpolate=False).items()
            if v is not None
        },
    }
    if not env.get("MISTRAL_KEY") or env["MISTRAL_KEY"] == "...":
        print("MISTRAL_KEY is required; no requests made.", file=sys.stderr)
        return 1
    results = []
    selected = CASES if args.case == "all" else {args.case: CASES[args.case]}
    for name, (prompt, expected) in selected.items():
        for model, effort in PROFILES:
            print(f"{name}: {model} / {effort}", file=sys.stderr, flush=True)
            values = {
                **env,
                "MISTRAL_GMAIL_MODEL": "",
                "MISTRAL_GMAIL_DRAFT_MODEL": model,
                "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": effort,
            }
            result = {
                "case": name,
                "prompt": prompt,
                "expected": expected,
                "model": model,
                "effort": effort,
            }
            started = time.monotonic()
            try:
                subject, body = MailModel(env=lambda values=values: values).composition(
                    prompt,
                    {"address": "camille@example.org", "names": ["Camille"]},
                    started + 60,
                )
                result.update(
                    subject=subject, body=body.removesuffix("\n\n" + SIGNATURE)
                )
            except GmailError as error:
                result["error"] = str(error)
            result["elapsed_seconds"] = round(time.monotonic() - started, 2)
            results.append(result)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(any("error" in result for result in results))


if __name__ == "__main__":
    sys.exit(main())
