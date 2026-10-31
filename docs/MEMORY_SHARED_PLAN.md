# Household context and memory editing

Approved scope (31 October 2026): edit/add/delete ordinary memory in Maintenance;
curate shared household facts in the reserved MinitelGPT profile; let all profiles
read those facts, including for mail recipient identification. Personal learning,
account permissions, private journals and Rose remain separate.

## Work packages

1. **Administration.** Keep the existing ADMIN_PASSWORD five-minute gate and CSRF.
   Make profile links discoverable while locked. Add plain forms for creating,
   editing and deleting records. Validate the profile and memory revisions,
   preserve unrelated records, fence old extraction and invalidate changed vectors.
   No model call is needed. Give global facts an explicit publication control;
   do not silently publish historical default-profile records.
2. **Shared recall.** Reuse the existing SQLite records and exact-vector/lexical
   recall. A default-profile record explicitly published by the administrator has
   origin `household`; other default records remain unpublished. Supply bounded,
   separately labelled personal and household context plus the selected identity.
   Global facts describe the appliance/household, not automatically the visitor.
   Terminal memory commands cannot write global records. Named users continue
   learning only into their own records; default automatic learning stays off.
3. **Mail and acceptance.** Give only relevant facts/preferences and selected
   identity to recipient selection, never the writer, mail router or integration
   permissions. Select actual mailbox candidates by ID; never synthesize addresses.
   Keep ambiguity handling, draft review and explicit send confirmation. Cover
   admin gate/CSRF/stale writes, delayed extraction, personal isolation, explicit
   publication, global recall, and relationship lookup using synthetic data and
   mocked APIs. Update operational documentation and run all AGENTS.md checks.

## Delivery

Work on develop; coherent local commits, no push until requested. Recheck remote
develop before delivery/publication. No main promotion, live API probe, account
operation, credential change, data purge or Pi deployment. Physical checks remain:
phone editor layout; two users reading a household fact without sharing personal
facts; Alice's brother resolving to a real Bob candidate; usual draft review.

## Completed implementation and evidence

All three work packages are implemented. Household publication is an explicit
`household` origin on existing default-profile fact/preference records; legacy
records remain unpublished. No dependency, schema migration or installer change.
Manual edits use the normal SQLite write/barrier path with additional profile
and form revision checks; old extraction cannot restore removed/replaced items.

Offline validation: locked all-extras sync; **998 tests and 400 subtests passed**,
including 23 new admin/household cases. Ruff, requirements-export freshness and
whitespace checks passed. A final profile-view relock/form-target correction was
also checked with the focused admin/household suite. No live model or Google call,
mail send, Pi deployment, physical terminal or visual phone validation was run.

Physical checks listed above remain for the owner after a requested publication
and normal update. In particular, wording/relationship reasoning still depends
on the model; offline candidate tests verify context routing and grounded IDs,
not real-model interpretation quality.
