# Biwenger World Cup 2026 - Fixed Price Lineup Optimizer

This program helps you select the best starting XI for Biwenger World Cup 2026 using **fixed prices**. It is designed to be accessed via an API, an MCP agent, or local CLI commands.

**Try the public GPT:** [Biwenger World Cup 2026 Optimizer](https://chatgpt.com/g/g-6a2989f4f6788191b307906356d0c143-biwenger-world-cup-2026-optimizer)

Privacy policy: [privacy.md](privacy.md)

Public policy URL for GPT configuration: `https://raw.githubusercontent.com/edurodelg/biwenger-mcp/main/privacy.md`

## Contents

| Section | Description |
| --- | --- |
| [Crucial information](#crucial-information-before-you-start) | Account limitations and local multitenant state. |
| [Authorization profiles](#api-authorization-profiles-personal-admin-vs-public-read-only) | Admin and public read-only capabilities. |
| [Squad sizes](#squad-sizes-from-11-to-15-players) | Starting XI and substitute rules. |
| [Example rules](#configurable-example-rules) | Budgets, transfers, captain and ariete limits. |
| [Fixed prices](#where-fixed-prices-come-from) | Accepted price sources and rejected dynamic values. |
| [Installation](#step-by-step-installation) | Python environment setup. |
| [Configuration](#setup) | Environment and local configuration. |
| [Loading data](#loading-players-and-prices) | Catalog import and public synchronization. |
| [Running the application](#running-the-application) | API and MCP execution. |
| [MyGPT Actions](#configuring-mygpt-actions) | Admin and public OpenAPI contracts. |
| [Responses](#interpreting-responses) | Meaning of warnings and review flags. |
| [Updating data](#updating-data) | Synchronization expectations. |
| [Tests](#running-tests) | Test suite. |
| [Privacy](#privacy--publishing-to-github) | Privacy and repository safety. |
| [License](#license) | MIT license. |

## Crucial Information Before You Start

### No Access to Your Biwenger Account

The program:
- Does not ask for your Biwenger email, password, cookies, or session token.
- Cannot view your actual squad, balance, bids, sales, or offers on the real Biwenger platform.
- Cannot modify your actual lineup on Biwenger.
- Does not consult the private or dynamic market of your league.
- Action endpoints like `make-bid`, `sell-player`, or `set-lineup` are local simulations that modify your team squad state persisted in SQLite.

You must manually apply the recommendations obtained here directly in the Biwenger app.

### Separate Local State for Multiple Users

The program stores its information in `data/biwenger.db`. This local database contains:
- The player catalog.
- The fixed price of each player.
- Player points, position, national team, and current status.
- Synchronized matches and results.
- Group stage standings.
- Match and playing-time reports (when available).
- The detected tournament phase and currently active national teams.
- The squad size, formation, budget, and national team player limit for each user.
- The locally persisted squad players for each user (modified via write actions).
- Separate manual ratings for each user.

The player catalog, match data, and standings are shared. However, configurations, ratings, and squad players are saved individually by `user_id` (e.g., `alice` or `pedro-family`). A user never inherits the budget, ratings, or squad of another user.

Note that `user_id` is not a password and does not verify identity; it only identifies a user's dedicated workspace within the application. HTTP protection is handled via `X-API-Key` (admin permissions) or `READ_ONLY_API_KEY` (public/guest permissions).

## API Authorization Profiles: Personal (Admin) vs. Public (Read-Only)

To support both your **Personal MyGPT (Admin)** and a **Public MyGPT (Read-Only)** for guest users, the API implements two separate authorization profiles. The authorization profile is determined by the header `X-API-Key`.

Here is a detailed summary of what each profile can do:

| Feature / Action | Admin Profile (`API_KEY`) | Guest / Public Profile (`READ_ONLY_API_KEY`) | Endpoint / Path |
| :--- | :--- | :--- | :--- |
| **OpenAPI Schema** | Full admin schema: `mygpt_openapi_minimal.yaml` | Restricted guest schema: `mygpt_openapi_public_readonly.yaml` | YAML file inside `openapi/` |
| **Database Workspace** | **Stateful**. Uses and isolates settings, ratings, and squads by `{user_id}`. | **Stateless**. Calculations use request parameters and fall back to `DEFAULT_USER_ID`. | `/api/...` (Public) vs `/api/users/{user_id}/...` (Private) |
| **Simulated Transfers** | **Allowed**. Can modify local rosters, bid on players, and sell players. | **Blocked**. Attempts return `403 WRITE_ACCESS_DENIED`. | `/api/users/{user_id}/actions/...` |
| **Lineup Optimization** | **Allowed**. Computes the optimal starters and bench from user's SQLite budget and roster. | **Allowed**. Computes optimal lineup using overrides (budget, formation, squad size) in request body. | `POST /api/optimize-lineup` (Public) or `POST /api/users/{user_id}/optimize-lineup` (Private) |
| **Custom Player Ratings** | **Allowed**. Can save custom manual ratings for players to modify optimization scores. | **Blocked**. Attempts return `403 WRITE_ACCESS_DENIED`. | `POST /api/users/{user_id}/player/{player_id}/rating` |
| **Biwenger Live Sync** | **Allowed**. Refreshes catalog, matches, and standings with Biwenger's public API. | **Blocked**. Attempts return `403 WRITE_ACCESS_DENIED`. | `POST /api/users/{user_id}/sync` |
| **Alternative Suggestions** | **Allowed**. Evaluates value replacements for a squad player. | **Allowed**. Evaluates replacements using the default catalog. | `POST /api/suggest-alternatives` (Public) or `POST /api/users/{user_id}/suggest-alternatives` |
| **Captain / Ariete Analysis** | **Allowed**. Picks candidates from user squad or global catalog. | **Allowed**. Picks candidates from global catalog using phase rules. | `POST /api/pick-captain` (Public/Private), `POST /api/pick-ariete` (Public/Private) |
| **Player Comparisons** | **Allowed**. Compares stats of 2 to 5 players. | **Allowed**. Compares stats of 2 to 5 players. | `POST /api/compare-players` (Public/Private) |
| **Shared Catalog Reads** | **Allowed**. Read selections, active matches, group standings, league list, and catalog. | **Allowed**. Read selections, active matches, group standings, league list, and catalog. | `/api/selections`, `/api/players`, `/api/matches`, `/api/standings`, etc. (Public) |

Use `GET /api/selections` to retrieve every national selection and its exact `filter_value`. To include eliminated selections when querying players, replace the scoring placeholder in the returned `players_query`, for example `GET /api/players?team=Espa%C3%B1a&active_only=false&score_system=sofascore`.

Use `GET /api/scoring-systems` to retrieve the four official scoring filters published by Biwenger for the World Cup: `diario_as`, `sofascore`, `average`, and `statistics`. An admin can persist the workspace default with `POST /api/users/{user_id}/settings`. Private requests use that setting when `score_system` is omitted; public player reads and calculations return `SCORING_SYSTEM_REQUIRED` so the client can ask the user instead of assuming a system.

---

## Squad Sizes from 11 to 15 Players

The optimizer always selects 11 starters. Users can configure their total squad size between 11 and 15 players:
- `11`: Starters only, no substitutes.
- `12`: 1 substitute.
- `13`: 2 substitutes.
- `14`: 3 substitutes.
- `15`: 4 substitutes.

Substitutes cannot share positions. With a squad of 15, you will have exactly one substitute for each position (`GK`, `DEF`, `MID`, `FWD`). The total budget includes both starters and substitutes.

## Configurable Example Rules

The figures in `config.example.json` replicate the settings of our private league. **These are not official Biwenger rules** and may not match your league's settings. You can modify these values in your local `config.json`.

| Phase | Budget | Max per Team | Transfers | Max Captain | Max Ariete |
| --- | ---: | ---: | ---: | ---: | ---: |
| Groups | 920M | 3 | 3 per matchday | 70M | 90M |
| Round of 16 | 980M | 3 | 4 | 85M | 105M |
| Quarter-finals | 1040M | 4 | 4 | 100M | 120M |
| Semi-finals | 1100M | 5 | 5 | 115M | 135M |
| Final | 1160M | 6 | 6 | 130M | 150M |

In this example, the budget increases by 60M per phase, and the Captain and Ariete limits increase by 15M per phase. The rule "4 from quarter-finals onwards" is interpreted literally: the maximum of 3 players per national team is maintained during the Round of 16.

Player prices are fixed throughout the tournament. The phase increments modify the allowed limits, not the stored price of individual players.

### Example League Prizes

Prize amounts are meant for a dinner or lunch at a venue chosen by the winner: 1st `+30 EUR`, 2nd `+20 EUR`, 3rd `+10 EUR`, 4th `-10 EUR`, 5th `-20 EUR`, and 6th `-30 EUR`. Negative amounts indicate how much that position contributes to the meal; the program does not perform any financial transactions.

## Where Fixed Prices Come From

Each player has a `fixed_price` field, stored in the `players.fixed_price` column in SQLite. Internal values are in millions: `12.5` represents 12.5 million.

Prices can be populated in two ways:
1. By importing your own CSV or JSON file using `src.app.import_catalog`.
2. Via `/api/users/{user_id}/sync`, but only when the public response contains an explicit `fantasyPrice` or `fixedPrice` field.

Dynamic fields like `price`, `value`, and `marketValue` **never replace** the fixed price. If no valid fixed price is found, the player is excluded to prevent incorrect recommendations.

## Step-by-Step Installation

Requires Python 3.11 or higher.

1. Download or clone this repository.
2. Open a terminal inside the project folder.
3. Run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows, activate the virtual environment with:

```powershell
.venv\Scripts\activate
```

Alternatively, with `uv`, you can set up everything in a single step:

```bash
uv sync
```

## Setup

Actual configuration files are not uploaded to GitHub. Create a local copy of the template:

```bash
cp config.example.json config.json
```

On Windows:

```powershell
copy config.example.json config.json
```

Then, open `config.yaml` or `config.json`. The key options are:
- `API_KEY`: The authorization key to access your private API (admin privileges). Replace the example value.
- `READ_ONLY_API_KEY`: Optional key to allow guest access for GET routes and lineup computations.
- `DEFAULT_USER_ID`: The default user ID when none is specified.
- `DEFAULT_SQUAD_SIZE`: The default squad size for new users (between 11 and 15).
- `DATABASE_URL`: Location of the local SQLite database.
- `REQUIRE_WRITE_CONFIRMATION`: If set to `true`, write actions will require a confirmation token.
- `CAPTAIN_MAX_PRICE`: Maximum price for the captain (in absolute euros in the config).
- `CAPTAIN_PRICE_INCREMENT_PER_PHASE`: Phase-by-phase increment for the captain's price limit.
- `ARIETE_MAX_PRICE`: Maximum price for the ariete (striker).
- `ARIETE_PRICE_INCREMENT_PER_PHASE`: Phase-by-phase increment for the ariete's price limit.
- `GROUP_BUDGET`: The budget for the group stage.
- `BUDGET_INCREMENT_PER_PHASE`: Phase-by-phase budget increment.
- `PRIZES_EUR`: Informative meal prize distribution for this example league.
- `BIWENGER_COMPETITION_SLUG`: Slug for the public endpoint, currently `world-cup`.

## Loading Players and Prices

### Recommended Option: CSV File

Use [examples/players_fixed_prices.csv](examples/players_fixed_prices.csv) as a template. The required columns are:

```text
id,name,position,team,fixed_price
```

Supported positions are `GK`, `DEF`, `MID`, and `FWD`. To import prices written in millions:

```bash
python -m src.app.import_catalog your_catalog.csv --price-unit millions
```

Prices can also be imported in euros or thousands:

```bash
python -m src.app.import_catalog your_catalog.json --price-unit euros
python -m src.app.import_catalog your_catalog.csv --price-unit thousands
```

The import is transactional: if any row is invalid, the catalog will not be imported.

### Automatic Option: Public Endpoint

With the API running, execute `POST /api/users/{user_id}/sync` from the Swagger UI. Synchronization updates shared public data. If Biwenger changes its endpoints, fall back to the CSV import.

## Running the Application

### Web API

```bash
python main.py
```

Then open [http://localhost:8000/docs](http://localhost:8000/docs). The Swagger UI allows you to test endpoints without writing code. Click `Authorize` and enter your configured `API_KEY`.

All endpoints require a user in the path:

```text
/api/users/{user_id}/...
```

Always use the same identifier for the same person. Allowed characters: alphanumeric, dots, hyphens, and underscores; max 64 characters.

Initial endpoints for the user `alice`:
- `GET /api/users/alice/status`
- `GET /api/users/alice/settings`
- `POST /api/users/alice/settings` with `{"squad_size": 13}`
- `GET /api/users/alice/players`
- `POST /api/users/alice/optimize-lineup`
- `POST /api/users/alice/pick-captain`
- `POST /api/users/alice/pick-ariete`

`status` includes `last_successful_sync`. If it is `null`, no successful sync of public data has occurred yet on that database.

### MCP

Change the `INTERFACE_MODE` to `mcp` and run the server. All MCP tools also require `user_id`:

```bash
INTERFACE_MODE=mcp python -m src.app.mcp_server
```

## Configuring MyGPT Actions

Use the contract that matches the GPT profile:

| GPT profile | OpenAPI contract | API key | State |
| --- | --- | --- | --- |
| Personal admin | `openapi/mygpt_openapi_minimal.yaml` | `API_KEY` | Settings, ratings and local squad isolated by `user_id` |
| Public read-only | `openapi/mygpt_openapi_public_readonly.yaml` | `READ_ONLY_API_KEY` | Stateless catalog queries and calculations |

1. Deploy the API via HTTPS.
2. Import the appropriate YAML in the Actions section of the custom GPT.
3. Set up API Key authentication using the `X-API-Key` header.
4. For the admin GPT, use one persistent, non-sensitive `user_id` per workspace.
5. For the public GPT, configure the privacy URL shown at the top of this README.

Do not manually edit the YAML paths or schemas. When FastAPI routes change, regenerate it with:

```bash
python scripts/export_mygpt_openapi.py
```

Private endpoints (like actions under `/api/users/{user_id}/actions/...`) simulate team updates locally inside SQLite and do not write to the real Biwenger server.

## Interpreting Responses

- `ok: true`: The operation completed successfully.
- `warnings`: Incomplete information or aspects that should be reviewed.
- `needs_review: true`: A recommendation is provided, but there is not enough data to consider it definitive.
- `lineup`: The 11 starting players.
- `substitutes`: Contains 0 to 4 players depending on the configured `squad_size`.
- `fixed_price`: Fixed price in millions.
- `rule_checks`: Validation checks for budget, player limits per team, and bench mode.

## Updating Data

The local database does not update automatically. To fetch recent data, you must:
1. Re-run `/api/users/{user_id}/sync` (if the public endpoint is active), or
2. Import an updated catalog, and
3. Check `needs_review` warnings when match reports or lineups are missing.

The timestamp of the local database does not guarantee that data is current. The agent must never claim to know the status of your account or assert that a sync was performed unless a successful sync response was returned.

## Running Tests

```bash
pytest
```

Tests run on a temporary database, verifying all five squad sizes (11, 12, 13, 14, and 15), user-specific routes, and multi-tenant isolation.

## Privacy & Publishing to GitHub

- Public GPT privacy policy: [privacy.md](privacy.md).
- `data/*.db`, `.env`, browser profiles, and screenshots are excluded via `.gitignore`.
- `config.json` and `.env` are ignored to prevent publishing sensitive keys.
- This project is not affiliated with Biwenger or Diario AS.

## License

MIT.
