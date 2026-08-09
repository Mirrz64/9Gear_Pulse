# 9gear Pulse — Launch Plan

*Draft strategy based on the product as scoped so far — treat the
specifics (channels, exact framing) as a starting hypothesis to react
to, not a locked plan.*

## 1. Launch Objective
Validate that real users — not just you testing against your own
schema — can get a correct, trustworthy pipeline out of the AI
generation loop. The first launch is about proving the core loop works
on other people's messy schemas, not maximizing signups.

The first launch should focus on:
- Getting a small number of real users through the full describe →
  generate → review → approve loop on their own data.
- Collecting generation-accuracy data across a wider variety of schemas
  than your own test databases.
- Validating whether the review UI actually gives people enough
  confidence to approve AI-generated pipelines touching their data.

**Key question the first launch should answer:** do people trust the
review step enough to actually approve and schedule what the AI
generates, or does it need more transparency/control before they will?

## 2. Launch Positioning
Not "replace your data engineer" — that's a harder claim to earn trust
for on day one. A more defensible first positioning: **"skip the
boilerplate, review the pipeline"** — the AI removes the tedious first
draft, the human still owns the final decision. This also matches the
security posture already built into the architecture (nothing runs
against production without approval).

## 3. Target First Users
Given the security/trust-sensitive nature of the product, the first
cohort should be people willing to test against a **staging/sample
database**, not production — this also lowers the stakes if generation
accuracy isn't perfect yet. Reasonable first audience: solo founders
and small teams already using Postgres, active in developer communities
where "I built this, try it" posts get genuine engagement (dev-focused
subreddits, Hacker News "Show HN", relevant Discord/Slack communities).

## 4. Launch Checklist (V1)
- [ ] Core loop (describe → generate → sandbox test → review → approve
      → schedule) works reliably end-to-end on at least 3-5 varied
      schemas you don't already know by heart.
- [ ] Generation accuracy has been measured, not just observed
      anecdotally (see PRD success metrics).
- [ ] Security basics are in place and can be stated plainly to a
      skeptical technical audience (encryption at rest, credentials
      never touch the AI, sandbox isolation).
- [ ] A simple onboarding path exists for someone who isn't you to get
      from signup to first approved pipeline without help.
