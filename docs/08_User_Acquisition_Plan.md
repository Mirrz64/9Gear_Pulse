# 9gear Pulse — User Acquisition Plan

*Draft channel strategy — treat as hypotheses to test cheaply, not a
committed budget/plan.*

## 1. Acquisition Objective
For the launch phase, the goal isn't volume — it's a few dozen real
users putting real (non-production) schemas through the generation
loop, so you get signal on accuracy across schema variety you can't
produce yourself by testing alone.

Early acquisition should optimize for:
- Technical users who will actually try connecting a real schema, not
  just read about the product.
- Willingness to give direct feedback on where the generated pipeline
  was wrong — this is more valuable at this stage than raw signups.
- Low cost, since there's no revenue yet to fund paid acquisition.

## 2. Target User Segments
Two segments worth distinguishing, since they need different messaging:

- **Builders**: solo founders/small teams who'd otherwise hand-write
  pipelines themselves or skip them entirely. Message: "skip the
  boilerplate."
- **Data engineers**: people who already know how to build pipelines
  but want the tedious first draft handled. Message: "review, don't
  write, the first version."

Data engineers are the harder-won but more valuable long-term segment —
they're also the toughest critics of generation accuracy, which makes
them a good source of the feedback the launch actually needs.

## 3. Channels (Early Stage)
- **Developer communities**: relevant subreddits (r/dataengineering,
  r/ExperiencedDevs), Hacker News "Show HN" once the loop is reliable
  enough to survive a technical audience kicking the tires.
- **Content**: a technical write-up of the actual architecture — the
  credentials-never-touch-the-AI design, the self-healing loop — is
  more persuasive to this audience than typical marketing copy, and
  you already have the material for it from this build process.
- **Direct outreach**: a short list of people you know who run small
  data teams or freelance data engineering — warm, low-volume, high
  feedback-quality.

## 4. What to Track
- Signup → first connection profile created (does onboarding even get
  people to the starting line?)
- First connection profile → first generated pipeline (does the core
  value prop land quickly?)
- First generated pipeline → approved & scheduled (the real conversion
  moment — this is where trust in the AI's output gets tested)
