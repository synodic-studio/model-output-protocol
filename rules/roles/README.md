# Role overlays

Role overlays adjust MOP's output expectations based on what the user does.
A solo founder, a senior engineer, a designer, and a product manager all
care about different details when an agent reports on a change.

Users select one or more roles in their per-chat config; rules from selected
overlays merge with `core/`.

These files are stubs. Each role's actual rule set will be built out as
patterns emerge. The intent of each is captured in the file's preamble.

## Roles defined here

- **solo-founder.yml** — early-stage founder doing every job; cares about
  outcomes, runway, what's blocked.
- **product-manager.yml** — focused on user-facing impact, timelines, risks;
  usually skips implementation depth.
- **senior-dev.yml** — wants technical depth, tradeoffs, test status; can
  read the diff.
- **junior-dev.yml** — wants more context and "why"; benefits from explicit
  next-step suggestions.
- **designer.yml** — visual outcomes, component/screen names; not
  implementation details.
- **devops.yml** — infra impact, monitoring implications, rollback plan.

Add more by following the same pattern.
