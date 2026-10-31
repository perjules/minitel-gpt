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
