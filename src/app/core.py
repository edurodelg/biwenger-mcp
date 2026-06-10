import httpx
import logging
from collections import Counter
from datetime import datetime, timezone
from .config import settings
from .database import (
    init_db, get_db_setting, set_db_setting, get_user_setting, set_user_setting,
    save_players_to_db, get_players_from_db,
    save_matches_to_db, get_matches_from_db,
    save_standings_to_db, get_standings_from_db,
    save_player_reports_to_db, get_player_reports_from_db,
    update_player_aggregate_stats, update_player_manual_rating,
    get_user_squad_from_db, save_user_squad_to_db,
    add_player_to_squad_db, remove_player_from_squad_db
)
from .pricing import fixed_price_from_record, normalize_price_millions
from .rules import get_example_ruleset


logger = logging.getLogger("Core")


def normalize_player_status(status: str | None) -> str:
    value = str(status or "ok").strip().lower()
    return "doubtful" if value == "doubt" else value


def matches_from_active_events(active_events: list[dict]) -> dict[int, dict]:
    matches = {}
    for event in active_events:
        round_name = event.get("name", "Fase de Grupos")
        for game in event.get("games", []):
            matches[game["id"]] = {
                "id": game["id"],
                "round_name": round_name,
                "date": game.get("date"),
                "status": game.get("status", "pending"),
                "home_team_id": game.get("home", {}).get("id"),
                "home_team_name": game.get("home", {}).get("name"),
                "home_score": game.get("home", {}).get("score"),
                "away_team_id": game.get("away", {}).get("id"),
                "away_team_name": game.get("away", {}).get("name"),
                "away_score": game.get("away", {}).get("score"),
            }
    return matches

# Ensure database is initialized on startup
try:
    init_db()
except Exception as e:
    logger.critical(f"Failed to initialize database: {e}")


def detect_current_round_and_phase(matches: list[dict]) -> tuple[str, str]:
    """
    Detect the current active round and phase based on match status.
    The first round (chronologically) that has "pending" matches is active.
    """
    if not matches:
        return "Fase de Grupos", "groups"
        
    # Sort matches by date
    sorted_matches = sorted(matches, key=lambda x: x.get("date") or 0)
    
    # Find the first match that is not finished
    current_round = None
    for m in sorted_matches:
        if m.get("status") != "finished":
            current_round = m.get("round_name")
            break
            
    if not current_round:
        # If all matches are finished, return the last round
        current_round = sorted_matches[-1].get("round_name") if sorted_matches else "Final"
        
    cr_lower = current_round.lower()
    if "octavos" in cr_lower or "16" in cr_lower:
        phase = "round_of_16"
    elif "cuartos" in cr_lower or "quarter" in cr_lower:
        phase = "quarter_finals"
    elif "semi" in cr_lower:
        phase = "semi_finals"
    elif "final" in cr_lower:
        phase = "final"
    else:
        phase = "groups"
        
    return current_round, phase


def get_active_teams_for_round(matches: list[dict], round_name: str, phase: str) -> set[str]:
    """
    Get the set of active team names for the given round.
    For group stage, all teams are active.
    For knockout stages, only teams with matches in this round are active.
    """
    if phase == "groups":
        return set()  # Empty set means all teams active
        
    active_teams = set()
    for m in matches:
        if m.get("round_name") == round_name:
            if m.get("home_team_name"):
                active_teams.add(m["home_team_name"])
            if m.get("away_team_name"):
                active_teams.add(m["away_team_name"])
    return active_teams


class BiwengerCore:
    """
    Core service connecting the FastAPI router and MCP server with the SQLite database
    and performing live synchronization with Biwenger's public endpoints.
    """

    async def status(self, user_id: str) -> dict:
        """Get the status of the API service and database connectivity."""
        cfg = await self.get_settings(user_id)
        return {
            "interface_mode": settings.interface_mode,
            "price_mode": "fixed_only",
            "competition_slug": settings.biwenger_competition_slug,
            "write_actions_enabled": True,
            "database_initialized": True,
            "last_successful_sync": get_db_setting("last_successful_sync"),
            "user_id": user_id,
            "active_budget": cfg["total_budget"],
            "active_formation": cfg["active_formation"],
            "squad_size": cfg["squad_size"],
        }

    async def get_rules(self, user_id: str) -> dict:
        """Retrieve the configurable private-league example rules."""
        cfg = await self.get_settings(user_id)
        ruleset = get_example_ruleset()
        ruleset["user_settings"] = {
            "user_id": user_id,
            "budget": cfg["total_budget"],
            "max_players_same_team": cfg["max_players_same_team"],
            "squad_size": cfg["squad_size"],
        }
        return ruleset

    def parse_formation(self, formation: str) -> tuple[int, int, int]:
        """Parse formation string (e.g. '4-4-2') to (DEF, MID, FWD) counts."""
        try:
            parts = [int(p) for p in formation.split("-")]
            if len(parts) == 3 and sum(parts) == 10:
                return parts[0], parts[1], parts[2]
        except (AttributeError, TypeError, ValueError):
            pass
        raise ValueError("Invalid formation. Use DEF-MID-FWD with positive values summing to 10.")

    async def get_settings(self, user_id: str) -> dict:
        """Get the active budget, formation, and team counts configuration."""
        default_budget = settings.group_budget / 1_000_000.0
        budget = float(get_user_setting(user_id, "total_budget", str(default_budget)))
        formation = get_user_setting(user_id, "active_formation", "4-4-2")
        max_same_team = int(get_user_setting(user_id, "max_players_same_team", str(settings.group_max_players_same_team)))
        squad_size = int(get_user_setting(user_id, "squad_size", str(settings.default_squad_size)))
        if not 11 <= squad_size <= 15:
            squad_size = 11
        try:
            d, m, f = self.parse_formation(formation)
        except ValueError:
            formation = "4-4-2"
            d, m, f = 4, 4, 2
        return {
            "user_id": user_id,
            "total_budget": budget,
            "active_formation": formation,
            "max_players_same_team": max_same_team,
            "num_gk": 1,
            "num_def": d,
            "num_mid": m,
            "num_fwd": f,
            "squad_size": squad_size,
            "num_substitutes": squad_size - 11,
            "total_players": squad_size,
        }

    async def update_settings(
        self,
        user_id: str,
        budget: float = None,
        formation: str = None,
        max_same_team: int = None,
        squad_size: int = None,
    ) -> dict:
        """Update configurations in the SQLite settings table."""
        if budget is not None:
            if budget <= 0:
                raise ValueError("Budget must be a positive number.")
            set_user_setting(user_id, "total_budget", str(budget))
            
        if formation is not None:
            self.parse_formation(formation)
            set_user_setting(user_id, "active_formation", formation)
            
        if max_same_team is not None:
            if max_same_team <= 0:
                raise ValueError("Max players same team must be a positive integer.")
            set_user_setting(user_id, "max_players_same_team", str(max_same_team))

        if squad_size is not None:
            if not 11 <= squad_size <= 15:
                raise ValueError("Squad size must be between 11 and 15 players.")
            set_user_setting(user_id, "squad_size", str(squad_size))
            
        return await self.get_settings(user_id)

    async def get_players(
        self, 
        user_id: str,
        position: str = None, 
        team: str = None, 
        status: str = None, 
        sort_by: str = "fixed_price", 
        active_only: bool = False,
        limit: int = 100, 
        offset: int = 0
    ) -> list[dict]:
        """Query normalized players from the database with optional active-only filtering."""
        if active_only:
            all_matches = await self.get_matches()
            curr_round, detected_phase = detect_current_round_and_phase(all_matches)
            active_teams = get_active_teams_for_round(all_matches, curr_round, detected_phase)
            
            if active_teams:
                # Fetch a larger set from DB and filter in memory to keep database.py simple
                players = get_players_from_db(user_id, position, team, status, sort_by, "DESC", limit=10000, offset=0)
                filtered = [p for p in players if p["team"] in active_teams]
                return filtered[offset:offset+limit]
                
        return get_players_from_db(user_id, position, team, status, sort_by, "DESC", limit, offset)

    async def _sync_player_reports(self, player_id: str, slug: str, client: httpx.AsyncClient) -> bool:
        """Helper to fetch, parse, and save detailed match reports for a player from the API."""
        if not slug:
            return False
            
        competition = settings.biwenger_competition_slug
        player_url = f"https://cf.biwenger.com/api/v2/players/{competition}/{slug}?fields=*,reports(points,events,match(round,home,away))&score=2&lang=es"
        try:
            resp = await client.get(player_url, timeout=15.0)
            if resp.status_code == 200:
                api_data = resp.json().get("data", {})
                reports_list = api_data.get("reports", [])
                
                normalized_reports = []
                for r in reports_list:
                    match_obj = r.get("match", {})
                    if not match_obj or match_obj.get("status") != "finished":
                        continue
                        
                    # Points parser: resolve SofaScore points key '2'
                    pts_data = r.get("points", 0)
                    if isinstance(pts_data, dict):
                        pts = int(pts_data.get("2") or list(pts_data.values())[0] or 0)
                    else:
                        pts = int(pts_data or 0)
                        
                    # Raw stats parser
                    raw_stats = r.get("rawStats", {})
                    goals = int(raw_stats.get("goals", 0))
                    assists = int(raw_stats.get("assists", 0))
                    minutes = int(raw_stats.get("minutesPlayed", 0))
                    sofa = float(raw_stats.get("sofascore", 0.0))
                    
                    normalized_reports.append({
                        "player_id": player_id,
                        "match_id": match_obj.get("id"),
                        "round_name": match_obj.get("round", {}).get("name", "Fase de Grupos"),
                        "home_team": match_obj.get("home", {}).get("name", "Local"),
                        "away_team": match_obj.get("away", {}).get("name", "Visitante"),
                        "home_score": match_obj.get("home", {}).get("score"),
                        "away_score": match_obj.get("away", {}).get("score"),
                        "date": match_obj.get("date"),
                        "points": pts,
                        "goals": goals,
                        "assists": assists,
                        "minutes_played": minutes,
                        "sofascore": sofa
                    })
                    
                if normalized_reports:
                    save_player_reports_to_db(normalized_reports)
                    
                    # Calculate aggregates
                    total_goals = sum(r["goals"] for r in normalized_reports)
                    total_assists = sum(r["assists"] for r in normalized_reports)
                    
                    # Update aggregate totals in SQLite
                    update_player_aggregate_stats(player_id, total_goals, total_assists)
                    return True
        except Exception as e:
            logger.error(f"Error syncing player reports for slug {slug}: {e}")
        return False

    async def get_player(self, user_id: str, player_id: str) -> dict:
        """
        Get player by ID from database and lazy-load their detailed match history 
        and statistics (goals, assists, minutes, etc.) on demand from the API.
        """
        players = get_players_from_db(user_id, limit=10000)
        player_data = None
        for p in players:
            if p["id"] == player_id:
                player_data = p
                break
                
        if not player_data:
            return None
            
        slug = player_data.get("slug")
        if not slug:
            player_data["reports"] = []
            return player_data
            
        # Check if we already have reports in the database
        db_reports = get_player_reports_from_db(player_id)
        
        # If no reports cached, fetch player details from public API
        if not db_reports:
            logger.info(f"Cache miss for player {player_data['name']} (ID: {player_id}). Lazy-loading match reports...")
            async with httpx.AsyncClient() as client:
                await self._sync_player_reports(player_id, slug, client)
            
            db_reports = get_player_reports_from_db(player_id)
            
            # Reload player object to return updated metrics
            players_updated = get_players_from_db(user_id, limit=10000)
            for p_upd in players_updated:
                if p_upd["id"] == player_id:
                    player_data = p_upd
                    break
                    
        player_data["reports"] = db_reports
        return player_data


    async def get_matches(self, round_name: str = None, status: str = None) -> list[dict]:
        """Query match fixtures from the database."""
        return get_matches_from_db(round_name, status)

    async def get_standings(self) -> list[dict]:
        """Query group standings from the database."""
        return get_standings_from_db()

    async def get_my_team(self, user_id: str) -> dict:
        """Return the user's squad/roster stored in the SQLite database."""
        squad_players = get_user_squad_from_db(user_id)
        cfg = await self.get_settings(user_id)
        total_budget = cfg["total_budget"]
        spent = sum(p["fixed_price"] for p in squad_players)
        balance = round(total_budget - spent, 2)
        return {
            "user_id": user_id,
            "players": squad_players,
            "balance": balance,
            "available": True,
            "message": "Squad loaded from local database." if squad_players else "Squad is empty. Use set-lineup to initialize it.",
        }

    async def get_market(self, user_id: str) -> dict:
        """Return the local fixed-price catalogue, not Biwenger's dynamic transfer market."""
        players = get_players_from_db(user_id, limit=200)
        return {
            "user_id": user_id,
            "players": players,
            "price_mode": "fixed_catalog",
            "live_market": False,
        }

    async def sync_database(self) -> dict:
        """
        Synchronize local SQLite database tables with Biwenger's public API.
        Queries the tournament round-by-round to download the full match list,
        and prefetches player details/reports for top/active players concurrently.
        """
        logger.info("Starting database synchronization with Biwenger public API...")
        
        # 1. Fetch Master Competition Data (Players & Teams)
        competition = settings.biwenger_competition_slug
        data_url = f"https://cf.biwenger.com/api/v2/competitions/{competition}/data?lang=es&score=2"
        try:
            async with httpx.AsyncClient() as client:
                resp_data = await client.get(data_url, timeout=30.0)
        except httpx.HTTPError as exc:
            logger.error("Biwenger synchronization request failed: %s", exc)
            return {
                "synchronized": False,
                "players_count": 0,
                "matches_count": 0,
                "standings_count": 0,
                "message": "Biwenger public API is unavailable or the configured competition slug is invalid.",
            }
            
        if resp_data.status_code != 200:
            logger.error(f"Failed to fetch competition data: {resp_data.status_code}")
            return {"synchronized": False, "players_count": 0, "matches_count": 0, "standings_count": 0, "message": "API Error"}
            
        try:
            json_data = resp_data.json().get("data", {})
        except ValueError:
            json_data = {}
        if not isinstance(json_data, dict) or not isinstance(json_data.get("players"), dict):
            return {
                "synchronized": False,
                "players_count": 0,
                "matches_count": 0,
                "standings_count": 0,
                "message": "Biwenger response format changed: no player catalogue was found.",
            }
        
        # Map team IDs to team names
        teams_dict = json_data.get("teams", {})
        team_id_to_name = {}
        for tid, t in teams_dict.items():
            team_id_to_name[int(tid)] = t.get("name", "Desconocido")
            
        # Parse and save players
        players_dict = json_data.get("players", {})
        normalized_players = []
        pos_map = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
        
        skipped_prices = Counter()
        for pid, p in players_dict.items():
            pos_raw = p.get("position", 3)
            pos = pos_map.get(pos_raw, "MID")
            
            tid = p.get("teamID")
            team_name = team_id_to_name.get(tid, "Desconocido")
            
            try:
                fixed_price, price_source = fixed_price_from_record(p, unit="auto")
            except ValueError as exc:
                skipped_prices[str(exc)] += 1
                continue
            
            raw_mv = p.get("price")
            try:
                market_value = normalize_price_millions(raw_mv, unit="auto") if raw_mv else 0.0
            except ValueError:
                market_value = 0.0
            
            normalized_players.append({
                "id": str(pid),
                "name": p.get("name", "Desconocido"),
                "slug": p.get("slug"),
                "position": pos,
                "team": team_name,
                "fixed_price": fixed_price,
                "fixed_price_source": price_source,
                "market_value": market_value,
                "points": int(p.get("points", 0)),
                "status": normalize_player_status(p.get("status")),
                "goals": 0,
                "assists": 0
            })
            
        if not normalized_players:
            return {
                "synchronized": False,
                "players_count": 0,
                "matches_count": 0,
                "standings_count": 0,
                "message": "No players with an explicit fixed fantasy price were found. Dynamic market values were not imported.",
            }
        save_players_to_db(normalized_players)
        
        # 2. Import only events actually exposed by Biwenger. The public API does
        # not support a round query parameter; relabeling the same event would
        # incorrectly make round-one fixtures appear as later rounds.
        active_events = json_data.get("activeEvents", [])
        unique_matches = matches_from_active_events(active_events)
                    
        if unique_matches:
            save_matches_to_db(list(unique_matches.values()))
            
        # 3. Fetch Standings Data
        standings_url = f"https://cf.biwenger.com/api/v2/competitions/{competition}/standings?lang=es"
        try:
            async with httpx.AsyncClient() as client:
                resp_stand = await client.get(standings_url, timeout=30.0)
        except httpx.HTTPError as exc:
            logger.warning("Could not synchronize standings: %s", exc)
            resp_stand = None
            
        standings_count = 0
        if resp_stand is not None and resp_stand.status_code == 200:
            standings_data = resp_stand.json()
            normalized_standings = []
            
            # The current public endpoint exposes only groups A and B. Preserve
            # exactly what the source publishes instead of inventing standings.
            for key in ["data", "meta"]:
                group = standings_data.get(key, {})
                group_name = group.get("name")
                teams = group.get("teams", [])
                
                if group_name and teams:
                    for item in teams:
                        t_obj = item.get("team", {})
                        normalized_standings.append({
                            "team_id": t_obj.get("id"),
                            "team_name": t_obj.get("name"),
                            "group_name": group_name,
                            "position": item.get("position", 1),
                            "points": item.get("points", 0),
                            "won": item.get("won", 0),
                            "lost": item.get("lost", 0),
                            "tied": item.get("tied", 0),
                            "scored": item.get("scored", 0),
                            "against": item.get("against", 0)
                        })
            if normalized_standings:
                save_standings_to_db(normalized_standings)
                standings_count = len(normalized_standings)
        standings_partial = 0 < standings_count < len(team_id_to_name)
                
        # 4. Prefetch detailed player reports concurrently for relevant players
        # Sort by points desc, then by fixed_price desc
        db_players = get_players_from_db(None, limit=10000)
        db_players.sort(key=lambda x: (x["points"], x["fixed_price"]), reverse=True)
        
        # Select top 150 players + any active roster players
        to_sync = []
        seen = set()
        for p in db_players:
            if p["id"] not in seen and (len(to_sync) < 150 or p["points"] > 0):
                to_sync.append(p)
                seen.add(p["id"])
                
        # Also include any team player if not already present
        my_team = await self.get_my_team("system-sync")
        for p in my_team.get("players", []):
            if p["id"] not in seen:
                to_sync.append(p)
                seen.add(p["id"])
                
        logger.info(f"Prefetching detailed reports for {len(to_sync)} players...")
        import asyncio
        sem = asyncio.Semaphore(10)
        
        async def worker(player, client):
            async with sem:
                await self._sync_player_reports(player["id"], player["slug"], client)
                
        async with httpx.AsyncClient() as client:
            tasks = [worker(p, client) for p in to_sync]
            await asyncio.gather(*tasks)
            
        logger.info("Detailed reports prefetch complete.")
        
        synchronized_at = datetime.now(timezone.utc).isoformat()
        set_db_setting("last_successful_sync", synchronized_at)
        set_db_setting("last_sync_players_count", str(len(db_players)))
        set_db_setting("last_sync_matches_count", str(len(unique_matches)))

        return {
            "synchronized": True,
            "synchronized_at": synchronized_at,
            "players_count": len(db_players),
            "matches_count": len(unique_matches),
            "standings_count": standings_count,
            "standings_partial": standings_partial,
            "published_rounds": [event.get("name") for event in active_events if event.get("name")],
            "message": f"Database successfully synchronized. Prefetched reports for {len(to_sync)} players."
            + (f" Skipped fixed-price records: {sum(skipped_prices.values())}." if skipped_prices else "")
        }

    async def get_leagues(self) -> list[dict]:
        """Retrieve leagues list."""
        return [{"id": "world-cup", "name": "Mundial 2026", "active": True}]

    async def set_lineup(self, user_id: str, payload: dict) -> dict:
        """Set a team lineup and save to SQLite."""
        logger.info("Setting lineup for user %s: %s", user_id, payload)
        players = payload.get("players") or []
        captain_id = payload.get("captain_id")
        ariete_id = payload.get("ariete_id")
        save_user_squad_to_db(user_id, players, captain_id, ariete_id)
        return {"ok": True, "message": "Lineup successfully updated and saved to SQLite."}

    async def make_bid(self, user_id: str, payload: dict) -> dict:
        """Simulate making a bid (adds player to user's squad)."""
        logger.info("Simulated bid for user %s: %s", user_id, payload)
        player_id = payload.get("player_id")
        if player_id:
            add_player_to_squad_db(user_id, player_id)
        return {"ok": True, "message": f"Bid on player {player_id} for {payload.get('amount')} successfully recorded and player added to squad."}

    async def cancel_bid(self, user_id: str, payload: dict) -> dict:
        """Simulate canceling an active bid."""
        logger.info("Simulated bid cancellation for user %s: %s", user_id, payload)
        return {"ok": True, "message": "Bid successfully cancelled (simulated)."}

    async def sell_player(self, user_id: str, payload: dict) -> dict:
        """Simulate placing a player on the transfer market (removes player from squad)."""
        logger.info("Simulated sale for user %s: %s", user_id, payload)
        player_id = payload.get("player_id")
        if player_id:
            remove_player_from_squad_db(user_id, player_id)
        return {"ok": True, "message": "Player successfully put on sale and removed from squad."}

    async def accept_offer(self, user_id: str, payload: dict) -> dict:
        """Simulate accepting a transfer offer (removes player from squad)."""
        logger.info("Simulated accepted offer for user %s: %s", user_id, payload)
        player_id = payload.get("player_id")
        if player_id:
            remove_player_from_squad_db(user_id, player_id)
        return {"ok": True, "message": "Offer successfully accepted."}

    async def update_player_rating(self, user_id: str, player_id: str, rating: float | None) -> dict:
        """Update manual rating for a player in the database."""
        players = get_players_from_db(user_id, limit=10000)
        if not any(p["id"] == player_id for p in players):
            return None
        update_player_manual_rating(user_id, player_id, rating)
        players = get_players_from_db(user_id, limit=10000)
        for p in players:
            if p["id"] == player_id:
                return p
        return None

core = BiwengerCore()


async def daily_sync_loop():
    """Background task to synchronize the database once a day at 3:00 AM local time."""
    import asyncio
    from datetime import datetime, time, timedelta
    
    logger.info("Daily database sync loop started.")
    while True:
        try:
            now = datetime.now()
            target_time = time(3, 0, 0)
            target_dt = datetime.combine(now.date(), target_time)
            if now.time() >= target_time:
                target_dt += timedelta(days=1)
            
            sleep_seconds = (target_dt - now).total_seconds()
            logger.info(f"Next scheduled daily sync at {target_dt.isoformat()} (in {sleep_seconds:.1f} seconds)")
            await asyncio.sleep(sleep_seconds)
            
            logger.info("Executing scheduled daily sync...")
            res = await core.sync_database()
            logger.info(f"Scheduled daily sync result: {res}")
        except asyncio.CancelledError:
            logger.info("Daily sync task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in daily sync loop: {e}")
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                break
