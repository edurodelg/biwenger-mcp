# AGENTS.md — Biwenger Agent Rules & Instruction Manual

## Objective

This document defines the behavior, business rules, and interface specifications that any AI Agent must follow to optimize lineups, suggest transfers, select the captain/ariete (striker), and recommend player alternatives in **Biwenger Fantasy World Cup 2026**.

The system operates in two modes:
*   `INTERFACE_MODE=api`: HTTP REST endpoints for cloud-based agents (e.g., MyGPT / Actions).
*   `INTERFACE_MODE=mcp`: Local tools for agents compatible with the Model Context Protocol (e.g., Claude Desktop, local runtimes).

---

## Fundamental Rule: Private Account vs. Local State

This project **does not have access to the user's personal Biwenger account**. No agent may claim to know or have modified the state on the actual Biwenger platform. Specifically, it has no connection to:

* The user's actual squad on Biwenger.
* Their actual balance or remaining budget on Biwenger.
* Their bids, offers, purchases, or sales on Biwenger.
* The private/dynamic market of their league on Biwenger.
* The lineup currently saved on Biwenger.

No credentials, cookies, or session tokens are received. However, the system maintains a **local SQLite database** to track and persist the user's squad players, transaction simulation status, budget, formation, and manual ratings per `user_id`. `GET /api/users/{user_id}/team` returns this locally persisted squad state and balance, and action endpoints simulate updates locally. `GET /api/users/{user_id}/market` returns the local fixed-price catalog.

## Mandatory Multitenant Rule

* All API routes use `/api/users/{user_id}/...` (except the public GET endpoints which have no prefix); there are no functional tenant-specific routes without a user.
* Every MCP tool receives `user_id` as a mandatory argument.
* `user_id` identifies a data workspace but does not authenticate the user. The API remains protected by `X-API-Key` or `READ_ONLY_API_KEY`.
* Never read or write configuration or ratings without filtering by `user_id`.
* Write confirmation tokens must include the `user_id` to prevent them from being reused across different users.
* Shared data: players, fixed prices, matches, standings, and sports reports.
* Private data per user: budget, formation, squad size, custom limit (quota) per national team, and `manual_rating`.

An existing database may be outdated. If a recent sync or import has not run successfully, the agent must warn the user and return `needs_review: true` if it affects recommendations.

---

## Configuration and Environment Variables

The public template is `config.example.yaml` / `config.example.json`. Each installation creates a local `config.yaml` / `config.json`, which is ignored by Git. **No email or password credentials are required**, as synchronization uses public endpoints.

The main variables exposed and configurable in configuration files (and overridable by environment variables) are:

*   `INTERFACE_MODE`: `"api"` or `"mcp"`.
*   `API_KEY`: Static authorization key. Every private API call must include the header:
    ```http
    X-API-Key: <API_KEY>
    ```
*   `READ_ONLY_API_KEY`: Optional key to allow guest users read-only access (GET routes/lineup computations) but block actions.
*   `DATABASE_URL`: Database path (defaults to `sqlite:///data/biwenger.db`).
*   `DEFAULT_USER_ID`: Fallback user ID if none is provided.
*   `REQUIRE_WRITE_CONFIRMATION`: Requests a confirmation token before any write if set to `true`.
*   `DEFAULT_SQUAD_SIZE`: Initial squad size for new users, between 11 and 15.
*   `CAPTAIN_MAX_PRICE`: Initial Captain price limit (`70000000` / 70M).
*   `CAPTAIN_PRICE_INCREMENT_PER_PHASE`: Captain price limit increment per phase (`15000000` / 15M per phase).
*   `ARIETE_MAX_PRICE`: Initial Ariete (striker) price limit (`90000000` / 90M).
*   `ARIETE_PRICE_INCREMENT_PER_PHASE`: Ariete price limit increment per phase (`15000000` / 15M per phase).
*   `GROUP_BUDGET`: Base budget for the group stage (`920000000` / 920M).
*   `BUDGET_INCREMENT_PER_PHASE`: Budget added at each phase change (`60000000` / 60M).
*   `PRIZES_EUR`: Informational distribution of the private league dinner/lunch prizes.

---

## Example Rules of Our Private League

**These figures are a configurable example chosen by the authors. They are not official Biwenger rules.** The agent must explain this when presenting the rules and must respect the values in `config.json` if the installation modifies them.

| Phase | Budget | Max per Team | Transfers | Max Captain | Max Ariete |
| :--- | ---: | ---: | ---: | ---: | ---: |
| **Groups** | 920M | 3 | 3 per matchday | 70M | 90M |
| **Round of 16** | 980M | 3 | 4 | 85M | 105M |
| **Quarter-finals** | 1040M | 4 | 4 | 100M | 120M |
| **Semi-finals** | 1100M | 5 | 5 | 115M | 135M |
| **Final** | 1160M | 6 | 6 | 130M | 150M |

The "4 from quarter-finals onwards" rule maintains the maximum of 3 in the Round of 16. The budget increases by 60M per phase, and the Captain and Ariete limits increase by 15M per phase.

### Example Prizes

Prizes are allocated for a dinner or lunch at a venue chosen by the winner: 1st `+30 EUR`, 2nd `+20 EUR`, 3rd `+10 EUR`, 4th `-10 EUR`, 5th `-20 EUR`, and 6th `-30 EUR`. A negative amount means that position contributes the specified value. The system only tracks and displays this rule; it does not collect or pay out money.

### Positional Roles and Restrictions
*   **Captain:** Cannot be a Goalkeeper (`GK`), and their price limit depends on the phase, starting at 70M.
*   **Ariete (Striker):** Must be a `FWD`, and their price limit depends on the phase, starting at 90M.
*   **Configurable Size:** Exactly 11 starters and between 0 and 4 substitutes, for a configurable total of 11 to 15 players.
*   Substitutes cannot share a position. With four substitutes, there must be exactly one `GK`, `DEF`, `MID`, and `FWD`.
*   The budget is calculated based on all selected players (starters + substitutes).

### Fixed Price Source and Storage

* The price used by the optimizer is strictly the `fixed_price`.
* Prices remain fixed throughout the tournament; phase increments only modify the budget and role limits, not the players' prices.
* It is stored in SQLite under the `players` table, `fixed_price` column, expressed in millions.
* `fixed_price_source` logs the verified source of the price.
* Prices are accepted from imported catalogs or explicit public fields such as `fantasyPrice`/`fixedPrice`.
* `price`, `value`, and `marketValue` are dynamic or ambiguous and must never replace the fixed price.
* Any player without a positive fixed price must be excluded, not estimated.

---

## Advanced Scoring Algorithm (Base Scoring)

The agent calculates players' estimated scores using a weighted average based on:

$$\text{Composite Score}_i = 0.4 \cdot \text{BaseRating}_i + 0.3 \cdot \text{Form}_i + 0.2 \cdot \text{PPM}_i + 0.1 \cdot \text{FixtureBonus}_i + \text{PosBonus}_i - \text{Penalties}_i$$

1.  **BaseRating:** Prioritizes `custom_ratings`, followed by the active `user_id`'s `manual_rating`, and finally historical points.
2.  **Form:** Average of the last 3 matches played.
3.  **PPM (Points Per Million):** Average points divided by price, rewarding cost-effective, budget players.
4.  **Fixture:** Dynamically adjusts the score based on the next opponent (awarding bonuses for playing against weak defenses/attacks according to the standings).
5.  **PosBonus (Position Bonus):** Clean sheet probability for goalkeepers/defenders, or goals/assists ratio per 90 minutes for midfielders/forwards.
6.  **Penalties:** Applies severe deductions for injuries, suspensions, or rotation risks (average minutes played $< 60$).

---

## API Endpoints Contract and MCP Mapping

`openapi/mygpt_openapi_minimal.yaml` is generated from FastAPI using `python scripts/export_mygpt_openapi.py`. Do not modify the contract manually: every new route must have a unique `operation_id`, regenerate the YAML, and pass `tests/test_mygpt_contract.py`.

MyGPT instructions are stored alongside the contracts:
* `openapi/prompt_private.md` for the tenant/admin Actions contract.
* `openapi/prompt_public.md` for the general read-only Actions contract.

The Agent interacts via the following endpoints (API) or equivalent tools (MCP):

### 📊 Data Retrieval and Ratings Management
*   **API:** `GET /api/users/{user_id}/status` | **MCP:** `biwenger_status(user_id)`
*   **API:** `GET /api/users/{user_id}/rules` | **MCP:** `biwenger_get_rules(user_id)`
*   **API:** `GET /api/users/{user_id}/team` | **MCP:** `biwenger_get_my_team(user_id)`
*   **API:** `GET /api/users/{user_id}/market` | **MCP:** `biwenger_get_market(user_id)`
*   **API:** `GET /api/users/{user_id}/selections`: returns every selection and the exact `team` filter value; this shared catalogue is also available at `GET /api/selections`.
*   **API:** `GET /api/users/{user_id}/scoring-systems`: returns `diario_as`, `sofascore`, `average`, and `statistics`, including the upstream Biwenger score IDs. The same shared catalogue is available at `GET /api/scoring-systems`.
    * Admin workspaces persist their default through `POST /api/users/{user_id}/settings` using `score_system`.
    * Public player reads and calculations require `score_system`; when omitted they return `SCORING_SYSTEM_REQUIRED` with all valid choices so the client can ask the user.
*   **API:** `GET /api/users/{user_id}/player/{player_id}` | **MCP:** `biwenger_get_player(user_id, player_id)`
*   **API:** `POST /api/users/{user_id}/player/{player_id}/rating`: saves the rating only for this user.

### 🧠 Analysis and Optimization
*   **API:** `POST /api/users/{user_id}/optimize-lineup` | **MCP:** `biwenger_optimize_lineup`
    *   *Input:* `{"phase": "groups", "objective": "points_and_value", "custom_ratings": {"id": 9.5}}`
    *   *Output:* 11 starters, between 0 and 4 substitutes, captain, ariete (striker), and validations.
*   **API:** `POST /api/users/{user_id}/suggest-alternatives` | **MCP:** `biwenger_suggest_alternatives`
    *   *Input:* `{"player_id": "player_id", "max_price": float | null}`
    *   *Output:* Ordered list of the best alternative players in the same position (cheaper or higher performing) with a comparative explanation of price and score.
*   **API:** `POST /api/users/{user_id}/pick-captain` | **MCP:** `biwenger_pick_captain`
    *   Returns a detailed ranking of the top 5 captain candidates.
*   **API:** `POST /api/users/{user_id}/pick-ariete` | **MCP:** `biwenger_pick_ariete`
    *   Returns a detailed ranking of the top 5 ariete (striker) candidates.
*   **API:** `POST /api/users/{user_id}/compare-players` | **MCP:** `biwenger_compare_players`
    *   *Input:* `{"player_ids": ["id1", "id2"]}`
*   **API:** `POST /api/users/{user_id}/build-phase-plan` | **MCP:** `biwenger_build_phase_plan`
    *   *Input:* `{"phase": "round_of_16"}`

### ✍️ Actions (Protection and Confirmations)
* All use `/api/users/{user_id}/actions/...` and their MCP equivalent receives `user_id`.

*Note: these actions are local simulations and do not write to a real Biwenger account. They modify the local squad state stored in SQLite. If a read-only key is used, they return `WRITE_ACCESS_DENIED`. If `REQUIRE_WRITE_CONFIRMATION=true` is configured, they require the expected `confirmation_token`:*
`CONFIRM_BID_<player_id>_<amount>`, `CONFIRM_SELL_<player_id>`, `CONFIRM_LINEUP_<hash>`, etc.

---

## Standard Response Formats

### Success (Status 200 OK)
```json
{
  "ok": true,
  "data": {},
  "warnings": [],
  "needs_review": false
}
```

### Error
If a write action is attempted with a read-only API key:
```json
{
  "ok": false,
  "error": "WRITE_ACCESS_DENIED",
  "message": "Write actions are blocked for read-only keys.",
  "details": {}
}
```

If a write confirmation token is missing or invalid:
```json
{
  "ok": false,
  "error": "WRITE_CONFIRMATION_REQUIRED",
  "message": "Write confirmation token is required.",
  "details": {
    "expected_token": "CONFIRM_LINEUP_..."
  }
}
```

---

## Agent Prohibitions

The Agent is prohibited from:
1.  **Inventing data:** If there are no saved match reports or the player does not exist in the database, it must return `needs_review: true`.
2.  **Recommending eliminated players:** Immediately exclude players whose national teams have no upcoming matches (eliminated from the World Cup).
3.  **Violating budget or team limits:** The 11 starters plus substitutes must never exceed the budget configured for the current phase.
4.  **Executing write actions without confirmation:** If the received confirmation token does not match the expected one, raise the error `WRITE_CONFIRMATION_REQUIRED`.
5.  **Confusing local state with real account:** Never claim to update or know the user's actual squad or balance on the real Biwenger platform. The agent must clarify that squad modifications and transactions are simulated and persisted only in the local SQLite database, and the user must manually replicate them on the Biwenger platform.
6.  **Using dynamic prices:** Never replace `fixed_price` with `price`, `value`, or `marketValue`.
7.  **Hiding lack of updates:** If the import/sync status is not marked as successful or if match reports are missing, report this clearly.
8.  **Mixing tenants:** Never reuse configuration, ratings, or confirmations across different `user_id` values.
