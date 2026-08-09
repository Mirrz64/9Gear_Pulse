# 9gear Pulse — Growth Plan

*Sequencing draft — the phases build on the priority order already set
in the Build Plan and PRD; timing is directional, not committed.*

## 1. Growth Objective
Move from "early adopters validating the core loop" to "repeatable
usage" — without expanding scope faster than generation accuracy and
trust can support. Growth here should follow proven reliability, not
precede it.

## 2. Growth Thesis
The product grows well when three things happen together:
1. Users get a correct pipeline out of the AI on the first or second
   try (accuracy).
2. The review step gives them enough confidence to actually approve
   what they see (trust).
3. Getting from signup to an approved, scheduled pipeline takes
   minutes, not a support conversation (onboarding).

If any one of these is weak, more acquisition just produces more
frustrated users, not more retained ones — worth resisting the urge to
push acquisition (User Acquisition Plan) ahead of fixing whichever of
the three is weakest.

## 3. Phased Growth

**Phase 1 — Prove the loop (current phase)**
Focus entirely on generation accuracy and the human review experience
with a small, engaged user base. Success = people approving and keeping
scheduled pipelines running, not signup count.

**Phase 2 — Expand surface area**
Once the core loop is trusted on Postgres → Postgres, add the next
connector (Snowflake or BigQuery per the Technical Requirements roadmap)
and the second acquisition segment (data engineers, not just builders).
This is also the natural point to revisit the Monetisation Plan with
real usage data.

**Phase 3 — Self-serve growth loop**
Once there's a steady base of successfully running pipelines, look for
a natural sharing/referral mechanism — e.g., users showcasing pipelines
they built, or a "built with 9gear Pulse" trace in generated code/docs
that creates organic discovery. Don't force this before Phase 1-2 are
solid; a referral loop amplifies whatever experience people are
actually having, good or bad.

## 4. What Would Signal It's Time to Move Phases
- Phase 1 → 2: generation accuracy and self-heal rate (PRD metrics) are
  stable across a variety of real user schemas, not just your own test
  data.
- Phase 2 → 3: a meaningful fraction of approved pipelines are still
  running 30+ days later (PRD retention metric) — that's the signal
  people are getting durable value, which is what makes referral/growth
  loops actually work rather than just adding churn.
