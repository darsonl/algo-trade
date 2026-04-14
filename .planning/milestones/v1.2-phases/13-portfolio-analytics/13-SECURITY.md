---
phase: 13
slug: portfolio-analytics
status: verified
threats_open: 0
asvs_level: 1
created: 2026-04-14
---

# Phase 13 — Security

> Per-phase security contract: threat register, accepted risks, and audit trail.

---

## Trust Boundaries

| Boundary | Description | Data Crossing |
|----------|-------------|---------------|
| Discord interaction → bot handler | User-supplied interaction data (interaction.user, interaction.guild_id) crosses here; /stats and /positions are read-only queries with no user-controlled inputs | Discord OAuth2-authenticated user identity (low sensitivity) |
| bot.py → database/queries.py | All values passed to get_trade_stats and get_position_summary originate from the DB, not from Discord input | Internal aggregated trade/position data |
| SellApproveRejectView.approve → create_trade | cost_basis is fetched from DB (avg_cost_usd), not from user input | Trade cost basis (financial, operator-only) |

---

## Threat Register

| Threat ID | Category | Component | Disposition | Mitigation | Status |
|-----------|----------|-----------|-------------|------------|--------|
| T-13-01 | Spoofing | /stats slash command | accept | Read-only command; no state change. Discord OAuth2 authenticates users. No user input reaches DB queries. | closed |
| T-13-02 | Tampering | trades.cost_basis via SellApproveRejectView | mitigate | cost_basis fetched from `queries.get_open_positions()` (DB source), never from interaction payload. Verified in `discord_bot/bot.py:118-134`. Inline comment `# T-13-02` present. | closed |
| T-13-03 | Information Disclosure | /positions footer leaks portfolio value | accept | Bot operates in a private Discord server (operator-only). No PII, no external exposure. | closed |
| T-13-04 | Denial of Service | get_trade_stats full table scan | accept | trades table is bounded by operator's own history (hundreds of rows maximum). No pagination required at this scale. | closed |
| T-13-05 | Elevation of Privilege | /stats command unauthorized use | accept | Bot operates in a private guild; only the operator has access. No role-based restriction required at this scale. | closed |

*Status: open · closed*
*Disposition: mitigate (implementation required) · accept (documented risk) · transfer (third-party)*

---

## Accepted Risks Log

| Risk ID | Threat Ref | Rationale | Accepted By | Date |
|---------|------------|-----------|-------------|------|
| AR-13-01 | T-13-01 | /stats is read-only with no user-controlled inputs; Discord OAuth2 handles authentication at the platform level | operator | 2026-04-14 |
| AR-13-02 | T-13-03 | Private Discord server; operator controls access; no PII exposure; portfolio value visible only to authorized guild members | operator | 2026-04-14 |
| AR-13-03 | T-13-04 | Table bounded by operator trade history; full-scan is O(hundreds) not O(millions); acceptable at this operational scale | operator | 2026-04-14 |
| AR-13-04 | T-13-05 | Private guild with no public membership; operator is sole user; role-based access control would add complexity with no security benefit | operator | 2026-04-14 |

---

## Security Audit Trail

| Audit Date | Threats Total | Closed | Open | Run By |
|------------|---------------|--------|------|--------|
| 2026-04-14 | 5 | 5 | 0 | gsd-secure-phase (automated) |

---

## Sign-Off

- [x] All threats have a disposition (mitigate / accept / transfer)
- [x] Accepted risks documented in Accepted Risks Log
- [x] `threats_open: 0` confirmed
- [x] `status: verified` set in frontmatter

**Approval:** verified 2026-04-14
