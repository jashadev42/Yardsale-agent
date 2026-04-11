# Yardsale Agent Task List

## RULES FOR THE AGENT
- Always ask before modifying the DB schema or Supabase config
- Always ask before pushing to production or deploying
- Commit code in small logical chunks with conventional commit messages
- If unsure about a design decision, send a Slack message and wait
- Work on the `agent/YYYY-MM-DD` branch — never push to main

---

## ACTIVE TASKS

## [HIGH] Implement hashtag filtering on Explore page
- FilterBar component lives in `frontend/src/components/`
- Tags live on listing objects as `listing.tags[]`
- Use AND logic — stacking tags narrows results, doesn't replace the filter
- Do NOT touch the DB schema

## [HIGH] Add loading skeletons to listing cards on Explore page
- Existing skeleton components in `frontend/src/components/Skeleton.jsx`
- Show skeletons while `isLoading` is true, replace with real cards on load
- Keep the same grid layout so there's no jump

## [MED] Fix mobile nav overflow on small screens
- Layout component at `frontend/src/components/Layout.jsx`
- Overflow is visible on iPhone SE width (375px)
- Do not break desktop layout

---

## DONE
(Completed tasks will be checked off here — `## [x] [HIGH] ...`.)

---

## Stretch Goals

The agent runs these when the queue is empty. Keep them low-risk and
self-contained.

## [LOW] Add JSDoc-style comments to complex functions in `frontend/src/lib/api.js`
- Only functions >20 lines with non-obvious behavior
- Do not change any logic

## [LOW] Audit and document console.warn calls in `backend/main.py`
- List any warnings emitted during normal startup
- Add a short comment explaining each one

## [LOW] Clean up unused imports in `frontend/src/pages/`
- One file per commit
