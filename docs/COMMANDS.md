# Explicit terminal commands

The first character `:` selects a fixed command. `:MAIL` opens mail directly;
`MAIL` without the prefix is conversation input and may be interpreted by the
normal conversational tools. Command selection itself is local. A selected
command may still contact Gmail, PRONOTE, the home controller or Mistral to perform
its advertised work.

Submitting `:` alone clears the input and arms command mode for exactly the next
submission. For example, submit `:`, then `MAIL`. Empty commands and unknown
commands produce a local message; they must never fall through to general chat.
The pending state belongs to the current console and is discarded on exit/reset.

Use `:AIDE` for the main command overview. Common entries are `:MAIL`, `:PRONOTE`,
`:JOURNAL INTIME`, `:AIRZONE`, `:3615`, `:CALC`, `:NOEL`, `:MAJ`, `:IMPRIMER`,
`:SOMMAIRE`, `:LOGOUT` and `:INTELLIGENCE 11`. Availability depends on installed/configured services.

Inside a console, prefix its commands too: `:LIRE 2`, `:ENVOYER`, `:NOTES`,
`:ENREGISTRER`, `:QUITTER`. Shortcuts put the colon first: `:T texte`, `:M corrections`,
`:E`, `:Q`. Their meaning remains scoped to the active service; e.g. `:E` sends a
reviewed mail draft but saves a reviewed journal entry. Old `T:` syntax is no longer
a keyboard command. Internal service adapters retain their existing interfaces.

Passwords and the answers to explicit confirmation/selection prompts are literal:
enter the password, `OUI`/`NON`, an offered number or a requested value without a
prefix. Passwords beginning with `:` remain passwords. Physical function keys keep
their existing roles. Rose's safety word remains immediately effective.

For loaded pages use SUITE/RETOUR, or `:SUITE`/`:PRECEDENT`. Source links use
`:LIEN n`. Ordinary text must not be mistaken for page navigation.

## Offline verification and physical acceptance

Regression coverage must verify command selection, unknown-command isolation,
mail draft review/send guards, journal ciphertext/password behavior, PRONOTE
navigation, Rose stop handling, controller confirmation and cancellation/reset.
Use synthetic accounts and mocked services only. Physical acceptance remains:

1. Submit `:` and verify the input clears and the command indicator appears.
2. Submit `AIDE`; check that the next ordinary submission returns to conversation.
3. Test direct commands, one-shot commands, unknown commands and shortcuts in each
   enabled console, including from intermediate pages.
4. Check passwords beginning with `:`, confirmation answers, SUITE/RETOUR/GUIDE,
   SOMMAIRE, logout, idle expiry and reconnect.
5. Verify a mail can be sent only after the current draft has been fully reviewed.

Offline validation for this implementation: 1,743 tests and 400 subtests passed;
one sudoers syntax check was skipped because `visudo` is unavailable. After the
final dispatcher validation review, all 56 affected command/mail/document/Christmas
tests passed again. Ruff, locked dependency sync, requirements export consistency
and whitespace checks passed. These checks do not replace the physical acceptance
steps above.
