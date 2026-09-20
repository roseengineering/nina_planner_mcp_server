# plan.md 

<!--
## `plan.md` — the living wishlist

A markdown file at the project root, **edited by the user at any time** (before or during the night). It is intentionally lightweight — entries can be as simple as a target name:

1. **Metadata** — night date, overall intent (optional).
2. **Candidate targets** — each with:
   - name / catalog designation (minimum required — everything else optional)
   - optional: coordinates, filter + exposure + count (if you want specifics)
   - optional: reference to an existing plan JSON file, or let the worker generate one
   - priority (1 = highest, optional)
   - optional time window / note
3. **Selection rules** — how the worker picks among candidates:
   - altitude floor (default e.g. 30°)
   - horizon buffer
   - tie-break order (priority, then highest current altitude, then earliest available)

Example shapes (illustrative — both are valid):

# Tonight

- M31          "before the moon comes up"
- Veil Nebula  plan=veil-nebula_..._20260913.json   prio 1
- M33          "if high targets done"

Rules:
- min_altitude: 30
- horizon_offset_degrees: 2
- tie_break: priority, then altitude
```
-->

