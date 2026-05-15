"""Manual checklist appended at the end of the markdown report.

Dashboard click-paths cannot be scripted credibly without Playwright;
this is the structured 5-minute checklist for the human to fill in.
"""

MANUAL_CHECKLIST = """\
- [ ] Dashboard loads at `http://127.0.0.1:<port>/dashboard/` (yes/no, attach screenshot or note).
- [ ] Sidebar shows the right sections; current backend + agent count badges are accurate.
- [ ] **Memories** page: filter + bulk-edit + drawer create/edit/forget round-trip works visually.
- [ ] **Conflicts** page: deliberately remember two close-but-different memories via CLI, refresh the page, resolve the conflict in the UI, verify event log shows it.
- [ ] **Recall Lab** scoring breakdown is readable, side-by-side compare works.
- [ ] **Agents** page: traffic-light dots match `clickmem agents`; click Test on one adapter, watch event appear in Recent Memories card.
- [ ] Auth modal triggers on a fresh browser profile when API key is required.
- [ ] Deep-linked refresh on `/dashboard/conflicts` returns the page, not a 404 (SPA fallback live).
"""
