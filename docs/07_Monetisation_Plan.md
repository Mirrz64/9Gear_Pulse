# 9gear Pulse — Monetisation Plan

*This is a comparison of common models for a product like this, not a
recommendation of one — the right choice depends on decisions about
your business that only you can make (risk tolerance, how fast you want
revenue vs. adoption, target customer size). I'm not a financial
advisor; treat the numbers here as illustrative, not benchmarked
pricing.*

## Options Worth Considering

### 1. Usage-based (per pipeline run / per generation)
- **How it works**: charge per AI generation call and/or per scheduled
  pipeline run.
- **Pros**: aligns cost with value delivered; easy to start free and
  scale with usage; matches how you're already paying Anthropic per
  token, so margin is easy to reason about.
- **Cons**: unpredictable bills can scare off small teams; harder to
  forecast your own revenue early on.

### 2. Seat-based subscription
- **How it works**: flat monthly fee per user/admin seat.
- **Pros**: predictable revenue, familiar SaaS model, easy to
  communicate.
- **Cons**: doesn't scale with actual usage — a team running 200
  pipelines pays the same as a team running 5; can undercharge your
  heaviest users.

### 3. Freemium with usage caps
- **How it works**: free tier with a low cap (e.g., a handful of
  pipelines/month against sample data only), paid tiers unlock
  production scheduling, more connectors, higher run volume.
- **Pros**: lowers the barrier to the exact validation your Launch Plan
  needs — people can try the core loop with no commitment; natural
  upgrade trigger (production scheduling) that maps to real value.
- **Cons**: requires clear tier boundaries decided up front, and some
  free users never convert.

### 4. Hybrid (seat + usage)
- **How it works**: base seat fee for access + metered charges beyond
  an included usage allowance.
- **Pros**: predictable floor revenue with usage-based upside; common
  in dev-tool SaaS (this is roughly how many AI coding tools price).
- **Cons**: more complex to explain and to bill correctly.

## A Reasonable Starting Point for This Stage
Given you're still validating generation accuracy (per the Launch Plan),
**freemium with usage caps** is worth strong consideration for the
earliest phase — it removes friction for the exact validation you need,
and the free-tier boundary (sample data only, capped runs) maps
naturally onto the "sandbox vs. production" distinction already built
into the architecture. Revisit pricing model choice once you have real
usage data, not before.
