import logging
import pulp
import math
from .core import BiwengerCore, detect_current_round_and_phase, get_active_teams_for_round
from .database import get_all_player_reports_from_db
from .rules import get_phase_rules, standings_warnings

logger = logging.getLogger("Optimizer")


def get_player_advanced_score(
    player: dict,
    reports: list[dict],
    standings_map: dict[str, dict],
    opponent_team: str | None,
    objective: str = "points_and_value",
    custom_ratings: dict[str, float] | None = None,
    risk_profile: str = "balanced",
    score_system: str = "sofascore",
) -> float:
    """
    Calculate an advanced composite score for a player based on:
    - Base rating (points, manual_rating database overrides, or request-specific custom_ratings)
    - Form factor (recent 3 match reports average)
    - Price efficiency (Points-Per-Million)
    - Dynamic Fixture Difficulty (adjusting based on upcoming opponent standing and goals)
    - Position-specific bonuses (clean sheets for GK/DEF, goals/assists for MID/FWD)
    - Consistency / playing time penalties (rotation and point volatility)
    """
    player_id = player.get("id")
    status = str(player.get("status", "ok")).lower()
    
    # 1. Exclusion constraints
    if status in ("injured", "suspended", "no_disponible"):
        return -9999.0

    # 2. Resolve Base points
    # First: custom_ratings from request override
    if custom_ratings and player_id in custom_ratings:
        base_points = float(custom_ratings[player_id])
    # Second: manual_rating from database
    elif player.get("manual_rating") is not None:
        base_points = float(player["manual_rating"])
    # Third: default total points
    else:
        base_points = float(player.get("points", 0))

    # Detailed reports are synchronized with SofaScore. Other official systems
    # use their own aggregate total without mixing in SofaScore match form.
    scoring_reports = reports if score_system == "sofascore" else []
    num_matches = len(scoring_reports)
    avg_pts = base_points / max(1.0, num_matches) if num_matches > 0 else base_points

    # 3. Form Factor (average of last 3 matches)
    if num_matches > 0:
        recent_reports = sorted(scoring_reports, key=lambda x: x.get("date", 0), reverse=True)[:3]
        form = sum(r.get("points", 0) for r in recent_reports) / len(recent_reports)
    else:
        form = avg_pts

    # 4. Price/Value efficiency (PPM)
    price = float(player.get("fixed_price", 1.0))
    if price <= 0:
        price = 1.0
    ppm = avg_pts / price

    # 5. Dynamic Fixture Difficulty Adjustment
    fixture_bonus = 0.0
    if opponent_team and standings_map and opponent_team in standings_map:
        opp = standings_map[opponent_team]
        opp_pos = int(opp.get("position", 4))
        opp_conceded = float(opp.get("against", 0))
        opp_scored = float(opp.get("scored", 0))
        
        # Favorable opponent (bottom group half)
        if opp_pos >= 3:
            fixture_bonus += 2.0
        # Tough opponent (top team)
        elif opp_pos == 1:
            fixture_bonus -= 2.0
            
        # Defense adjustments for attackers
        if player["position"] in ("MID", "FWD") and opp_conceded > 3:
            fixture_bonus += 1.5
            
        # Attack adjustments for defenders
        if player["position"] in ("GK", "DEF") and opp_scored > 4:
            fixture_bonus -= 1.5

    # 6. Position-Specific Bonuses
    pos_bonus = 0.0
    if player["position"] in ("GK", "DEF"):
        # Clean sheet potential: inversely proportional to opponent scoring strength
        if opponent_team and standings_map and opponent_team in standings_map:
            opp_scored = float(standings_map[opponent_team].get("scored", 0))
            if opp_scored <= 1:
                pos_bonus += 2.0
            elif opp_scored >= 4:
                pos_bonus -= 1.0
    elif player["position"] in ("MID", "FWD"):
        # Attack efficiency: goals/assists weighted
        total_goals = float(player.get("goals", 0))
        total_assists = float(player.get("assists", 0))
        pos_bonus += (total_goals + 0.5 * total_assists) * 1.5

    # 7. Consistency and Rotation Penalties
    penalties = 0.0
    if status == "doubtful":
        penalties += 15.0
        
    if num_matches >= 3:
        # Standard deviation penalty (variance in points)
        pts_list = [float(r.get("points", 0)) for r in scoring_reports]
        mean_val = sum(pts_list) / len(pts_list)
        variance = sum((x - mean_val) ** 2 for x in pts_list) / len(pts_list)
        std_dev = math.sqrt(variance)
        if std_dev > 5.0:
            penalties += 1.5
            
        # Rotation penalty: average minutes played per match
        avg_mins = sum(int(r.get("minutes_played", 0)) for r in scoring_reports) / num_matches
        if avg_mins < 60:
            penalties += 3.0

    if risk_profile == "conservative":
        penalties *= 1.5
        fixture_bonus *= 0.75
        pos_bonus *= 0.9
    elif risk_profile == "aggressive":
        penalties *= 0.75
        fixture_bonus *= 1.25
        pos_bonus *= 1.2

    # 8. Objective calculation
    if objective == "value":
        return round(ppm, 2)
    if objective == "points":
        return round((0.55 * avg_pts) + (0.35 * form) + (0.1 * fixture_bonus * 5) + pos_bonus - penalties, 2)

    comp_score = (0.4 * avg_pts) + (0.3 * form) + (0.2 * ppm * 10) + (0.1 * fixture_bonus * 5) + pos_bonus - penalties
    return round(comp_score, 2)


async def optimize_lineup(core: BiwengerCore, user_id: str, request, override_settings: dict | None = None) -> dict:
    """
    Solves the 15-player squad optimization problem using mixed-integer linear programming (MILP).
    Roster composition:
    - 11 Starters: 1 GK, DEF/MID/FWD matching the active formation.
    - Optional bench: between 0 and 4 players, at most one per position.
    - Global Constraints: total budget cap, maximum players per country.
    """
    # 1. Load active settings and detect current tournament stage
    if override_settings:
        cfg = override_settings
    else:
        cfg = await core.get_settings(user_id)
    
    all_matches = await core.get_matches()
    curr_round, detected_phase = detect_current_round_and_phase(all_matches)
    
    # Use request phase, fallback to automatically detected phase
    phase = getattr(request, "phase", "groups")
    if phase == "groups" and detected_phase != "groups":
        phase = detected_phase
        
    rules = get_phase_rules(phase)
    
    # Dynamic settings adjustment: if settings in DB match standard group stage defaults,
    # we automatically adjust them to the correct phase-specific rules (budget increment / country cap).
    # If the user has explicitly changed them, we respect their manual choice.
    db_budget = cfg["total_budget"]
    db_max_same_team = cfg["max_players_same_team"]
    
    group_rules = get_phase_rules("groups")
    if db_budget == group_rules["budget"]:
        budget = rules["budget"]
    else:
        budget = db_budget
        
    if db_max_same_team == group_rules["max_players_same_team"]:
        max_same_team = rules["max_players_same_team"]
    else:
        max_same_team = db_max_same_team
        
    # Get active teams for knockout rounds
    active_teams = get_active_teams_for_round(all_matches, curr_round, phase)
    
    num_gk = 1
    num_def = cfg["num_def"]
    num_mid = cfg["num_mid"]
    num_fwd = cfg["num_fwd"]
    squad_size = cfg["squad_size"]
    substitute_count = squad_size - 11
    
    logger.info(
        f"Active Optimizer Config: Phase: {phase} | Round: {curr_round} | "
        f"Budget: {budget}M | Formation: {cfg['active_formation']} | Max per Team: {max_same_team}"
    )
    
    # 2. Load players list from database
    score_system = getattr(request, "score_system", "sofascore")
    raw_players = await core.get_players(user_id, limit=10000, score_system=score_system)
    if not raw_players:
        return {
            "lineup": [],
            "substitutes": [],
            "captain": None,
            "ariete": None,
            "transfers": [],
            "expected_points": 0,
            "expected_value_growth": 0,
            "rule_checks": {},
            "explanation": [f"No player database available. Synchronize using POST /api/users/{user_id}/sync first."]
        }
        
    # Filter eliminated, unavailable, malformed, and non-fixed-price records.
    base_players = getattr(request, "base_players", None) or []
    base_player_ids = [str(pid).strip() for pid in base_players if pid]
    
    raw_players_map = {p["id"]: p for p in raw_players}
    missing_base_ids = [pid for pid in base_player_ids if pid not in raw_players_map]
    if missing_base_ids:
        return {
            "lineup": [],
            "substitutes": [],
            "captain": None,
            "ariete": None,
            "transfers": [],
            "expected_points": 0,
            "expected_value_growth": 0,
            "rule_checks": {"budget_ok": False},
            "explanation": [f"The following base players were not found in the database: {', '.join(missing_base_ids)}."]
        }

    players = []
    for p in raw_players:
        pid = p["id"]
        is_base = pid in base_player_ids
        
        if active_teams and p["team"] not in active_teams:
            if is_base:
                return {
                    "lineup": [],
                    "substitutes": [],
                    "captain": None,
                    "ariete": None,
                    "transfers": [],
                    "expected_points": 0,
                    "expected_value_growth": 0,
                    "rule_checks": {"budget_ok": False},
                    "explanation": [f"Base player {p['name']} ({pid}) is from {p['team']}, which is eliminated from the tournament."]
                }
            continue
        if p.get("position") not in {"GK", "DEF", "MID", "FWD"}:
            continue
        if float(p.get("fixed_price") or 0) <= 0:
            continue
        if str(p.get("status", "ok")).lower() in {"injured", "suspended", "no_disponible"}:
            if not is_base:
                continue
        players.append(p)
        
    if not players:
        return {
            "lineup": [],
            "substitutes": [],
            "captain": None,
            "ariete": None,
            "transfers": [],
            "expected_points": 0,
            "expected_value_growth": 0,
            "rule_checks": {},
            "explanation": [f"No players available for the active teams in this phase: {', '.join(active_teams)}."]
        }
        
    # Fetch player reports, standings, and fixtures to build dynamic scores
    all_reports = get_all_player_reports_from_db()
    reports_map = {}
    for r in all_reports:
        pid = r["player_id"]
        if pid not in reports_map:
            reports_map[pid] = []
        reports_map[pid].append(r)
        
    standings = await core.get_standings()
    standings_map = {item["team_name"]: item for item in standings}
    
    matches = await core.get_matches(status="pending")
    next_opponents = {}
    for match in matches:
        home = match["home_team_name"]
        away = match["away_team_name"]
        if home not in next_opponents:
            next_opponents[home] = away
        if away not in next_opponents:
            next_opponents[away] = home

    # 3. Initialize PuLP optimization problem
    prob = pulp.LpProblem("Biwenger_Fixed_Price_Optimizer", pulp.LpMaximize)
    
    player_indices = list(range(len(players)))
    base_player_indices = [idx for idx, p in enumerate(players) if p["id"] in base_player_ids]
    
    # Binary variables: s[i] = 1 if player i is starter, b[i] = 1 if player i is substitute
    s = pulp.LpVariable.dicts("starter", player_indices, cat="Binary")
    b = pulp.LpVariable.dicts("sub", player_indices, cat="Binary")
    
    # 4. Objective: Maximize starter score and, when enabled, half-weight bench score.
    custom_ratings = getattr(request, "custom_ratings", None)
    player_scores = []
    for p in players:
        p_reports = reports_map.get(p["id"], [])
        opponent = next_opponents.get(p["team"])
        score = get_player_advanced_score(
            p, p_reports, standings_map, opponent, 
            request.objective, custom_ratings, request.risk_profile, score_system
        )
        player_scores.append(score)
        
    prob += pulp.lpSum((player_scores[i] * s[i]) + (0.5 * player_scores[i] * b[i]) for i in player_indices), "Total_Squad_Score"
    
    # 5. Global Roster Size Constraints
    prob += pulp.lpSum(s[i] for i in player_indices) == 11, "Starters_Count"
    prob += pulp.lpSum(b[i] for i in player_indices) == substitute_count, "Subs_Count"
    
    for idx in base_player_indices:
        prob += s[idx] + b[idx] == 1, f"Force_Base_Player_{idx}"
    
    # Starters position quotas
    prob += pulp.lpSum(s[i] for i in player_indices if players[i]["position"] == "GK") == num_gk, "Starters_GK"
    prob += pulp.lpSum(s[i] for i in player_indices if players[i]["position"] == "DEF") == num_def, "Starters_DEF"
    prob += pulp.lpSum(s[i] for i in player_indices if players[i]["position"] == "MID") == num_mid, "Starters_MID"
    prob += pulp.lpSum(s[i] for i in player_indices if players[i]["position"] == "FWD") == num_fwd, "Starters_FWD"
    
    # Optional substitutes: never duplicate a position. Four substitutes implies one of each.
    prob += pulp.lpSum(b[i] for i in player_indices if players[i]["position"] == "GK") <= 1, "Subs_GK"
    prob += pulp.lpSum(b[i] for i in player_indices if players[i]["position"] == "DEF") <= 1, "Subs_DEF"
    prob += pulp.lpSum(b[i] for i in player_indices if players[i]["position"] == "MID") <= 1, "Subs_MID"
    prob += pulp.lpSum(b[i] for i in player_indices if players[i]["position"] == "FWD") <= 1, "Subs_FWD"
    
    # Exclude double selection
    for i in player_indices:
        prob += s[i] + b[i] <= 1, f"Single_Selection_{i}"
        
    # Budget Cap (sum of starter and substitute prices)
    prob += pulp.lpSum((players[i]["fixed_price"] * s[i]) + (players[i]["fixed_price"] * b[i]) for i in player_indices) <= budget, "Budget_Cap"
    
    # Max players from same team (national country)
    teams = list(set(p["team"] for p in players))
    for team in teams:
        prob += pulp.lpSum((s[i] + b[i]) for i in player_indices if players[i]["team"] == team) <= max_same_team, f"Max_{team.replace(' ', '_')}"

    captain_max = rules["captain_max_price"]
    ariete_max = rules["ariete_max_price"]
    prob += pulp.lpSum(
        s[i] for i in player_indices
        if players[i]["position"] != "GK" and players[i]["fixed_price"] <= captain_max
    ) >= 1, "Eligible_Captain"
    prob += pulp.lpSum(
        s[i] for i in player_indices
        if players[i]["position"] == "FWD" and players[i]["fixed_price"] <= ariete_max
    ) >= 1, "Eligible_Ariete"
        
    # 6. Solve
    solver = pulp.PULP_CBC_CMD(msg=False)
    prob.solve(solver)
    
    status_str = pulp.LpStatus[prob.status]
    logger.info(f"Optimization Status: {status_str}")
    
    if status_str != "Optimal":
        return {
            "lineup": [],
            "substitutes": [],
            "captain": None,
            "ariete": None,
            "transfers": [],
            "expected_points": 0,
            "expected_value_growth": 0,
            "rule_checks": {"budget_ok": False, "status": status_str},
            "explanation": [
                f"Could not find an optimal solution. Status: {status_str}.",
                "Please check if: 1) Your budget is too low. 2) There are not enough available players for all positions."
            ]
        }
        
    # Extract chosen players
    starters_chosen = []
    subs_chosen = []
    
    for i in player_indices:
        p_data = players[i]
        p_data["score"] = player_scores[i]
        if s[i].varValue > 0.99:
            starters_chosen.append(p_data)
        elif b[i].varValue > 0.99:
            subs_chosen.append(p_data)
            
    # Sort starters by position rank: GK, DEF, MID, FWD
    pos_rank = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
    starters_chosen.sort(key=lambda x: (pos_rank.get(x["position"], 9), -x["fixed_price"]))
    subs_chosen.sort(key=lambda x: (pos_rank.get(x["position"], 9), -x["fixed_price"]))
    
    # 7. Select Captain and Ariete
    captain_candidates = [
        p for p in starters_chosen 
        if p["fixed_price"] <= captain_max
        and p["position"] != "GK"
    ]
    ariete_candidates = [
        p for p in starters_chosen 
        if p["fixed_price"] <= ariete_max
        and p["position"] == "FWD"
    ]
    
    # Sort candidates by composite score descending
    player_id_to_score = {players[i]["id"]: player_scores[i] for i in player_indices}
    captain_candidates.sort(key=lambda x: -player_id_to_score.get(x["id"], 0.0))
    ariete_candidates.sort(key=lambda x: -player_id_to_score.get(x["id"], 0.0))
    
    captain = captain_candidates[0] if captain_candidates else None
    ariete = ariete_candidates[0] if ariete_candidates else None
    
    total_cost = sum(p["fixed_price"] for p in starters_chosen) + sum(p["fixed_price"] for p in subs_chosen)
    expected_points = sum(p["points"] for p in starters_chosen)
    
    missing_reports = sum(1 for p in starters_chosen if not reports_map.get(p["id"]))
    standings_review = standings_warnings(standings)
    explanation = [
        f"Successfully compiled optimal starting XI ({cfg['active_formation']})"
        + (f" and {substitute_count} substitutes" if substitute_count else " without substitutes")
        + " using advanced composite scoring.",
        f"Total squad cost: {total_cost:.2f}M / {budget:.2f}M limit.",
    ]
    if missing_reports:
        explanation.append(f"{missing_reports} starters have no saved match reports; review the recommendation before using it.")
    explanation.extend(standings_review)

    return {
        "user_id": user_id,
        "lineup": starters_chosen,
        "substitutes": subs_chosen,
        "captain": captain,
        "ariete": ariete,
        "transfers": [],
        "expected_points": expected_points,
        "score_system": score_system,
        "expected_value_growth": 0,
        "rule_checks": {
            "budget_ok": True,
            "total_cost": total_cost,
            "budget_limit": budget,
            "max_same_team_ok": True,
            "squad_size": squad_size,
            "substitute_count": substitute_count,
            "phase": phase,
            "transfers_allowed": rules["transfers_allowed"],
            "captain_max_price": captain_max,
            "ariete_max_price": ariete_max,
        },
        "needs_review": missing_reports > 0 or bool(standings_review),
        "explanation": explanation,
    }


async def analyze_captain_candidates(core: BiwengerCore, user_id: str, request) -> dict:
    """Analyze and rank the top captain candidates (price <= CAPTAIN_MAX_PRICE) from the database."""
    score_system = getattr(request, "score_system", "sofascore")
    players = await core.get_players(user_id, limit=10000, score_system=score_system)
    if not players:
        return {"best_candidate": None, "candidates": [], "explanation": ["No player database available."]}
        
    all_matches = await core.get_matches()
    curr_round, detected_phase = detect_current_round_and_phase(all_matches)
    
    phase = getattr(request, "phase", "groups")
    if phase == "groups" and detected_phase != "groups":
        phase = detected_phase
    captain_max = get_phase_rules(phase)["captain_max_price"]
        
    active_teams = get_active_teams_for_round(all_matches, curr_round, phase)
    
    all_reports = get_all_player_reports_from_db()
    reports_map = {}
    for r in all_reports:
        pid = r["player_id"]
        if pid not in reports_map:
            reports_map[pid] = []
        reports_map[pid].append(r)
        
    standings = await core.get_standings()
    standings_map = {item["team_name"]: item for item in standings}
    standings_review = standings_warnings(standings)
    
    matches = await core.get_matches(status="pending")
    next_opponents = {}
    for match in matches:
        home = match["home_team_name"]
        away = match["away_team_name"]
        if home not in next_opponents:
            next_opponents[home] = away
        if away not in next_opponents:
            next_opponents[away] = home
            
    custom_ratings = getattr(request, "custom_ratings", None)
    
    candidates = []
    for p in players:
        if active_teams and p["team"] not in active_teams:
            continue
        if p["fixed_price"] > captain_max or p["position"] == "GK":
            continue
        status = str(p.get("status", "ok")).lower()
        if status in ("injured", "suspended", "no_disponible"):
            continue
            
        p_reports = reports_map.get(p["id"], [])
        opponent = next_opponents.get(p["team"])
        score = get_player_advanced_score(
            p, p_reports, standings_map, opponent, 
            request.objective, custom_ratings, request.risk_profile, score_system
        )
        
        candidates.append({
            "player": p,
            "score": score,
            "opponent": opponent or "Desconocido",
            "price_million": p["fixed_price"]
        })
        
    candidates.sort(key=lambda x: -x["score"])
    top_candidates = candidates[:5]
    
    explanation = []
    if top_candidates:
        best = top_candidates[0]["player"]
        explanation.append(f"Recommended Captain: {best['name']} ({best['team']}) - Score: {top_candidates[0]['score']} | Price: {best['fixed_price']}M")
        for idx, c in enumerate(top_candidates[1:], 2):
            p = c["player"]
            explanation.append(f"Option #{idx}: {p['name']} ({p['team']}) - Score: {c['score']} | Price: {p['fixed_price']}M")
    else:
        explanation.append("No valid captain candidates found.")
    explanation.extend(standings_review)
        
    return {
        "score_system": score_system,
        "best_candidate": top_candidates[0]["player"] if top_candidates else None,
        "candidates": top_candidates,
        "needs_review": bool(standings_review),
        "explanation": explanation
    }


async def analyze_ariete_candidates(core: BiwengerCore, user_id: str, request) -> dict:
    """Analyze and rank forward-only ariete candidates within the phase price cap."""
    score_system = getattr(request, "score_system", "sofascore")
    players = await core.get_players(user_id, limit=10000, score_system=score_system)
    if not players:
        return {"best_candidate": None, "candidates": [], "explanation": ["No player database available."]}
        
    all_matches = await core.get_matches()
    curr_round, detected_phase = detect_current_round_and_phase(all_matches)
    
    phase = getattr(request, "phase", "groups")
    if phase == "groups" and detected_phase != "groups":
        phase = detected_phase
    ariete_max = get_phase_rules(phase)["ariete_max_price"]
        
    active_teams = get_active_teams_for_round(all_matches, curr_round, phase)
    
    all_reports = get_all_player_reports_from_db()
    reports_map = {}
    for r in all_reports:
        pid = r["player_id"]
        if pid not in reports_map:
            reports_map[pid] = []
        reports_map[pid].append(r)
        
    standings = await core.get_standings()
    standings_map = {item["team_name"]: item for item in standings}
    standings_review = standings_warnings(standings)
    
    matches = await core.get_matches(status="pending")
    next_opponents = {}
    for match in matches:
        home = match["home_team_name"]
        away = match["away_team_name"]
        if home not in next_opponents:
            next_opponents[home] = away
        if away not in next_opponents:
            next_opponents[away] = home
            
    custom_ratings = getattr(request, "custom_ratings", None)
    
    candidates = []
    for p in players:
        if active_teams and p["team"] not in active_teams:
            continue
        if p["fixed_price"] > ariete_max or p["position"] != "FWD":
            continue
        status = str(p.get("status", "ok")).lower()
        if status in ("injured", "suspended", "no_disponible"):
            continue
            
        p_reports = reports_map.get(p["id"], [])
        opponent = next_opponents.get(p["team"])
        score = get_player_advanced_score(
            p, p_reports, standings_map, opponent, 
            request.objective, custom_ratings, request.risk_profile, score_system
        )
        
        candidates.append({
            "player": p,
            "score": score,
            "opponent": opponent or "Desconocido",
            "price_million": p["fixed_price"]
        })
        
    candidates.sort(key=lambda x: -x["score"])
    top_candidates = candidates[:5]
    
    explanation = []
    if top_candidates:
        best = top_candidates[0]["player"]
        explanation.append(f"Recommended Ariete: {best['name']} ({best['team']}) - Score: {top_candidates[0]['score']} | Price: {best['fixed_price']}M")
        for idx, c in enumerate(top_candidates[1:], 2):
            p = c["player"]
            explanation.append(f"Option #{idx}: {p['name']} ({p['team']}) - Score: {c['score']} | Price: {p['fixed_price']}M")
    else:
        explanation.append("No valid ariete candidates found.")
    explanation.extend(standings_review)
        
    return {
        "score_system": score_system,
        "best_candidate": top_candidates[0]["player"] if top_candidates else None,
        "candidates": top_candidates,
        "needs_review": bool(standings_review),
        "explanation": explanation
    }


async def find_better_alternatives(core: BiwengerCore, user_id: str, request) -> dict:
    """
    Given a player_id, find alternative players in the same position
    that have a higher advanced score and/or are cheaper/more efficient.
    """
    player_id = request.player_id
    
    # 1. Load target player
    score_system = getattr(request, "score_system", "sofascore")
    target_player = await core.get_player(user_id, player_id, score_system)
    if not target_player:
        return {"target_player": None, "alternatives": [], "explanation": [f"Player with ID {player_id} not found."]}
        
    position = target_player["position"]
    target_price = target_player["fixed_price"]
    
    # Defaults max_price to target_price + 3.0M if not provided
    max_price = request.max_price
    if max_price is None:
        max_price = target_price + 3.0
        
    # 2. Load all players in the same position
    players = await core.get_players(user_id, position=position, limit=10000, score_system=score_system)
    
    # Load reports, standings, and next fixtures
    all_reports = get_all_player_reports_from_db()
    reports_map = {}
    for r in all_reports:
        pid = r["player_id"]
        if pid not in reports_map:
            reports_map[pid] = []
        reports_map[pid].append(r)
        
    standings = await core.get_standings()
    standings_map = {item["team_name"]: item for item in standings}
    standings_review = standings_warnings(standings)
    
    matches = await core.get_matches(status="pending")
    next_opponents = {}
    for match in matches:
        home = match["home_team_name"]
        away = match["away_team_name"]
        if home not in next_opponents:
            next_opponents[home] = away
        if away not in next_opponents:
            next_opponents[away] = home
            
    # Calculate target player's score
    target_reports = reports_map.get(player_id, [])
    target_opponent = next_opponents.get(target_player["team"])
    target_score = get_player_advanced_score(
        target_player, target_reports, standings_map, target_opponent, 
        request.objective, score_system=score_system
    )
    target_player["score"] = target_score
    
    # 3. Evaluate candidates
    alternatives = []
    for p in players:
        if p["id"] == player_id:
            continue
        # Apply filters
        if p["fixed_price"] > max_price:
            continue
        status = str(p.get("status", "ok")).lower()
        if status in ("injured", "suspended", "no_disponible"):
            continue
            
        p_reports = reports_map.get(p["id"], [])
        opponent = next_opponents.get(p["team"])
        score = get_player_advanced_score(
            p, p_reports, standings_map, opponent, 
            request.objective, score_system=score_system
        )
        
        # We consider a player an alternative if:
        # - They have a strictly higher score, OR
        # - They have a similar score (within 0.5 points) but are cheaper
        score_diff = score - target_score
        price_diff = p["fixed_price"] - target_price
        
        is_alt = score_diff > 0.0 or (score_diff >= -0.5 and price_diff < 0.0)
        
        if is_alt:
            alternatives.append({
                "player": p,
                "score": score,
                "score_diff": round(score_diff, 2),
                "price_diff_million": round(price_diff, 2),
                "opponent": opponent or "Desconocido"
            })
            
    # Sort alternatives: first by score difference descending, then by price difference ascending
    alternatives.sort(key=lambda x: (-x["score_diff"], x["price_diff_million"]))
    top_alts = alternatives[:5]
    
    # 4. Generate explanations
    explanation = []
    explanation.append(f"Target Player: {target_player['name']} ({target_player['team']}) - Score: {target_score:.2f} | Price: {target_price:.1f}M")
    
    if top_alts:
        for idx, alt in enumerate(top_alts, 1):
            p = alt["player"]
            p_diff = alt["price_diff_million"]
            s_diff = alt["score_diff"]
            price_desc = f"saves {-p_diff:.1f}M" if p_diff < 0 else f"costs {p_diff:.1f}M more"
            score_desc = f"+{s_diff:.2f} rating" if s_diff > 0 else f"{s_diff:.2f} rating"
            explanation.append(
                f"Alternative #{idx}: {p['name']} ({p['team']}) - Score: {alt['score']:.2f} ({score_desc}) | "
                f"Price: {p['fixed_price']:.1f}M ({price_desc})"
            )
    else:
        explanation.append(f"No better alternatives found for {target_player['name']} under {max_price:.1f}M.")
    explanation.extend(standings_review)
        
    return {
        "score_system": score_system,
        "target_player": target_player,
        "alternatives": top_alts,
        "needs_review": bool(standings_review),
        "explanation": explanation
    }
