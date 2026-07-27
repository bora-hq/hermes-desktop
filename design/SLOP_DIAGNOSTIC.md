# Slop Diagnostic Report — Hermes Voice Prototypes

## Scoring Rubric (0-10, lower = better)

| Tell | Description | Weight |
|------|-------------|--------|
| 1 | Tech gradient (blue/violet/indigo glossy) | 1 |
| 2 | Generic tech hue (default indigo/violet accent) | 1 |
| 3 | Feature-tile grid (icon+heading+sentence ×3 equal weight) | 1 |
| 4 | Accent rail (colored left strip on cards) | 1 |
| 5 | Unearned blur (glassmorphism without depth system) | 1 |
| 6 | Monument stat (oversized numbers filling space) | 1 |
| 7 | Icon topper (rounded-square icon above every heading) | 1 |
| 8 | Center stack (everything centered, no composition) | 1 |
| 9 | Default type (Inter/system-ui by default) | 1 |
| 10 | Wrong surface (composition doesn't match surface archetype) | 1 |

---

## 01-settings-modal.html — Score: **3/10** ✅ Good

**Fired tells:**
- **Tell 2 (Generic tech hue):** Accent `--accent: #c4a97d` (golden/beige) — NOT generic indigo/violet. ✅ Actually a deliberate brand choice.
- **Tell 5 (Unearned blur):** `.modal-backdrop { backdrop-filter: blur(8px) }` — Has real depth (modal over backdrop), not gratuitous. ✅ Justified.
- **Tell 9 (Default type):** Uses `Georgia, serif` — deliberate editorial choice, not Inter default. ✅ Good.

**Not fired:** 1, 3, 4, 6, 7, 8, 10

**Surface analysis:** This is a **Configure** surface (settings modal). Composition: progressive disclosure sections, clear save/cancel actions, low decoration. ✅ Correct surface.

**Verdict:** Clean. Only minor nit: the "Zona de Perigo" section could use more visual separation (it's just another section).

---

## 02-session-history-sidebar.html — Score: **2/10** ✅ Excellent

**Fired tells:**
- **Tell 5 (Unearned blur):** `.sidebar { backdrop-filter: blur(8px) }` — Has real elevation (fixed sidebar over content), justified. ✅
- **Tell 10 (Wrong surface?):** This is an **Operate** surface (user takes action on sessions). Composition: list with inline actions, search, selection state. ✅ Correct surface.

**Not fired:** 1, 2, 3, 4, 6, 7, 8, 9

**Surface analysis:** **Operate** surface — action affordances (Retomar/Excluir) dominate, selection state clear, density appropriate. ✅

**Verdict:** Strong. Well-executed Operate surface.

---

## 03-onboarding-first-run.html — Score: **4/10** ⚠️ Needs work

**Fired tells:**
- **Tell 3 (Feature-tile grid):** Step 1 has 3 icon+label tiles in a flex row — equal weight, nothing prioritized. "Privado/Rápido/Seu" all same visual weight.
- **Tell 8 (Center stack):** Steps 1, 2, 3, 5 are heavily centered (text-align:center, flex column center, illustration centered). No real composition commitment.
- **Tell 10 (Wrong surface):** This is a **Decide/Learn** surface (onboarding = convince + teach), but it's using **Monitor-style** centered cards with feature tiles instead of **one idea per section** landing flow.

**Not fired:** 1, 2, 4, 5, 6, 7, 9

**Surface analysis:** Onboarding = **Decide/Learn** surface. Should have:
- One clear idea per step
- Hierarchy that guides the eye
- Asymmetric composition, not centered stacks
- The "hero" only belongs on step 1 (welcome), not every step

**Critical issues:**
1. Step 1: Feature-tile grid (3 equal tiles) — violates "one idea lands per section"
2. All steps: Center-stack composition — no visual rhythm, feels like template
3. Step 2 (permissions): Good pattern (list with status icons), but centered
4. Step 3 (voice selector): Radio-group cards — good, but centered
5. Step 4 (mic test): Centered button + visualizer — OK for this specific interaction
6. Step 5 (completion): Centered stack with highlight list — OK for confirmation

---

## Repairs Needed (Matched to Diagnosis)

### For 03-onboarding-first-run.html (tells 3, 8, 10 → **re-layout / re-compose**)

| Step | Current | Repaired |
|------|---------|----------|
| **1 Welcome** | Centered hero + 3 equal feature tiles | Asymmetric layout: headline left, illustration right, value props as numbered list with hierarchy |
| **2 Mic Permission** | Centered illustration + centered permission list | Left-aligned list with status icons, clear primary CTA |
| **3 Voice Select** | Centered radio cards | Left-aligned options with clear selection state, preview on right (desktop) |
| **4 Mic Test** | Keep centered (interaction-focused) | ✅ OK — this IS a Command/Inspect moment |
| **5 Complete** | Centered stack | Asymmetric: checkmark left, summary right, CTA prominent |

### For 01-settings-modal.html (minor)
- Add visual separator before "Zona de Perigo" (thicker border, red accent tint)

### For 02-session-history-sidebar.html (none needed)
- ✅ Already solid Operate surface

---

## Action Plan

1. **Fix 03-onboarding-first-run.html** — full re-layout (not recolor)
2. **Tweak 01-settings-modal.html** — danger zone separation
3. **Verify 02-session-history-sidebar.html** — no changes
4. **Re-score all three** after repairs