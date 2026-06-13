import sqlite3
import logging
import unicodedata
from pathlib import Path
from .config import settings
from .scoring import DEFAULT_SCORING_SYSTEM, SCORING_SYSTEMS

logger = logging.getLogger("Database")

def remove_accents(input_str) -> str:
    if not input_str:
        return ""
    nfkd_form = unicodedata.normalize('NFKD', str(input_str))
    return "".join([c for c in nfkd_form if not unicodedata.combining(c)]).lower()


TEAM_FILTER_ALIASES = {
    "curacao": "curazao",
}


def normalize_team_filter(team: str) -> str:
    normalized = remove_accents(team).strip()
    return TEAM_FILTER_ALIASES.get(normalized, normalized)

def get_db_path() -> str:
    """Resolve database URL to a filesystem path and ensure the parent directory exists."""
    url = settings.database_url
    if url.startswith("sqlite:///"):
        db_path = url[10:]
    elif url.startswith("sqlite://"):
        db_path = url[9:]
    else:
        db_path = url
        
    path = Path(db_path)
    if not path.is_absolute():
        root_dir = Path(__file__).resolve().parent.parent.parent
        path = root_dir / path
        
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)

def get_connection():
    """Get a connection to the SQLite database with row factory enabled."""
    conn = sqlite3.connect(get_db_path())
    conn.create_function("remove_accents", 1, remove_accents)
    conn.create_function("normalize_team", 1, normalize_team_filter)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables and default configuration settings."""
    db_file = get_db_path()
    logger.info(f"Initializing SQLite database at: {db_file}")
    
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Players Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT,
            position TEXT NOT NULL,
            team TEXT NOT NULL,
            fixed_price REAL DEFAULT 0.0,
            fixed_price_source TEXT,
            market_value REAL DEFAULT 0.0,
            points INTEGER DEFAULT 0,
            points_as INTEGER DEFAULT 0,
            points_sofascore INTEGER DEFAULT 0,
            points_average INTEGER DEFAULT 0,
            points_statistics INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ok',
            goals INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            manual_rating REAL DEFAULT NULL
        )
        """)
        
        # Safe migration if table already exists without manual_rating
        cursor.execute("PRAGMA table_info(players)")
        cols = [r["name"] for r in cursor.fetchall()]
        if "manual_rating" not in cols:
            cursor.execute("ALTER TABLE players ADD COLUMN manual_rating REAL DEFAULT NULL")
        if "fixed_price_source" not in cols:
            cursor.execute("ALTER TABLE players ADD COLUMN fixed_price_source TEXT")
        score_columns = {
            "points_as": "INTEGER DEFAULT 0",
            "points_sofascore": "INTEGER DEFAULT 0",
            "points_average": "INTEGER DEFAULT 0",
            "points_statistics": "INTEGER DEFAULT 0",
        }
        for column, definition in score_columns.items():
            if column not in cols:
                cursor.execute(f"ALTER TABLE players ADD COLUMN {column} {definition}")
                if column == "points_sofascore":
                    cursor.execute("UPDATE players SET points_sofascore = points")

        
        # 2. Matches Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY,
            round_name TEXT,
            date INTEGER,
            status TEXT,
            home_team_id INTEGER,
            home_team_name TEXT,
            home_score INTEGER,
            away_team_id INTEGER,
            away_team_name TEXT,
            away_score INTEGER
        )
        """)
        
        # 3. Standings Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS standings (
            team_id INTEGER PRIMARY KEY,
            team_name TEXT NOT NULL,
            group_name TEXT NOT NULL,
            position INTEGER DEFAULT 1,
            points INTEGER DEFAULT 0,
            won INTEGER DEFAULT 0,
            lost INTEGER DEFAULT 0,
            tied INTEGER DEFAULT 0,
            scored INTEGER DEFAULT 0,
            against INTEGER DEFAULT 0
        )
        """)
        
        # 4. Settings Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_player_ratings (
            user_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            manual_rating REAL,
            PRIMARY KEY (user_id, player_id),
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
        )
        """)
        
        # 5. Player Detailed Reports Table (Lazy-loaded historical match data)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_reports (
            player_id TEXT,
            match_id INTEGER,
            round_name TEXT,
            home_team TEXT,
            away_team TEXT,
            home_score INTEGER,
            away_score INTEGER,
            date INTEGER,
            points INTEGER,
            goals INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            minutes_played INTEGER DEFAULT 0,
            sofascore REAL DEFAULT 0.0,
            PRIMARY KEY (player_id, match_id)
        )
        """)
        
        # 6. User Squad Players Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_squad_players (
            user_id TEXT NOT NULL,
            player_id TEXT NOT NULL,
            is_starter INTEGER NOT NULL DEFAULT 1,
            is_captain INTEGER NOT NULL DEFAULT 0,
            is_ariete INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, player_id),
            FOREIGN KEY (player_id) REFERENCES players(id) ON DELETE CASCADE
        )
        """)
        
        # Insert default settings if they are not already set
        defaults = {
            "total_budget": str(settings.group_budget / 1000000.0),
            "active_formation": "4-4-2",
            "max_players_same_team": str(settings.group_max_players_same_team)
        }
        
        for k, v in defaults.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        conn.commit()
    logger.info("Database initialized successfully.")

# --- Settings DB Operations ---
def get_db_setting(key: str, default: str = None) -> str:
    """Retrieve a setting value from the database."""
    try:
        with get_connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
    except Exception as e:
        logger.error(f"Error reading setting '{key}': {e}")
        return default

def set_db_setting(key: str, value: str):
    """Write or update a setting value in the database."""
    try:
        with get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
            conn.commit()
    except Exception as e:
        logger.error(f"Error setting '{key}' = '{value}': {e}")


def get_user_setting(user_id: str, key: str, default: str | None = None) -> str | None:
    """Retrieve a setting isolated to one user."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
        return row["value"] if row else default


def set_user_setting(user_id: str, key: str, value: str) -> None:
    """Create or update a setting isolated to one user."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, str(value)),
        )
        conn.commit()

# --- Players DB Operations ---
def save_players_to_db(players_list: list[dict]):
    """Insert or update a batch of normalized player records, preserving manual ratings."""
    for player in players_list:
        player.setdefault("fixed_price_source", "unspecified")
        player.setdefault("points_as", 0)
        player.setdefault("points_sofascore", player.get("points", 0))
        player.setdefault("points_average", 0)
        player.setdefault("points_statistics", 0)
    try:
        with get_connection() as conn:
            conn.executemany("""
            INSERT INTO players (
                id, name, slug, position, team, fixed_price, fixed_price_source, market_value,
                points, points_as, points_sofascore, points_average, points_statistics,
                status, goals, assists, manual_rating
            ) VALUES (
                :id, :name, :slug, :position, :team, :fixed_price, :fixed_price_source, :market_value,
                :points, :points_as, :points_sofascore, :points_average, :points_statistics,
                :status, 0, 0, NULL
            )
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                slug = excluded.slug,
                position = excluded.position,
                team = excluded.team,
                fixed_price = excluded.fixed_price,
                fixed_price_source = excluded.fixed_price_source,
                market_value = excluded.market_value,
                points = excluded.points,
                points_as = excluded.points_as,
                points_sofascore = excluded.points_sofascore,
                points_average = excluded.points_average,
                points_statistics = excluded.points_statistics,
                status = excluded.status
            """, players_list)
            conn.commit()
    except Exception:
        logger.exception("Error saving players to database")
        raise

def update_player_manual_rating(user_id: str, player_id: str, rating: float | None):
    """Update or clear a user's manual rating for a specific player."""
    try:
        with get_connection() as conn:
            if rating is None:
                conn.execute(
                    "DELETE FROM user_player_ratings WHERE user_id = ? AND player_id = ?",
                    (user_id, player_id),
                )
            else:
                conn.execute(
                    """INSERT OR REPLACE INTO user_player_ratings
                       (user_id, player_id, manual_rating) VALUES (?, ?, ?)""",
                    (user_id, player_id, rating),
                )
            conn.commit()
    except Exception as e:
        logger.error(f"Error updating rating for user {user_id}, player {player_id}: {e}")


def get_players_from_db(
    user_id: str | None = None,
    position: str = None, 
    team: str = None, 
    status: str = None, 
    sort_by: str = "fixed_price", 
    order: str = "DESC",
    limit: int = 100, 
    offset: int = 0,
    score_system: str = DEFAULT_SCORING_SYSTEM,
) -> list[dict]:
    """Retrieve player records with dynamic filters, sorting, and pagination."""
    if score_system not in SCORING_SYSTEMS:
        score_system = DEFAULT_SCORING_SYSTEM
    score_details = SCORING_SYSTEMS[score_system]
    points_column = score_details["points_column"]

    if user_id:
        query = """
        SELECT p.id, p.name, p.slug, p.position, p.team, p.fixed_price,
               p.fixed_price_source, p.market_value, p.points, p.points_as,
               p.points_sofascore, p.points_average, p.points_statistics, p.status,
               p.goals, p.assists, upr.manual_rating AS manual_rating
        FROM players p
        LEFT JOIN user_player_ratings upr
          ON upr.player_id = p.id AND upr.user_id = ?
        WHERE 1=1
        """
        params = [user_id]
        sort_prefix = "p."
    else:
        query = "SELECT * FROM players WHERE 1=1"
        params = []
        sort_prefix = ""
    
    if position:
        query += f" AND {sort_prefix}position = ?"
        params.append(position.upper())
    if team:
        query += f" AND normalize_team({sort_prefix}team) = ?"
        params.append(normalize_team_filter(team))
    if status:
        query += f" AND LOWER({sort_prefix}status) = LOWER(?)"
        params.append(status)
        
    allowed_sort_fields = {"id", "name", "position", "team", "fixed_price", "market_value", "points", "status", "goals", "assists"}
    if sort_by not in allowed_sort_fields:
        sort_by = "fixed_price"
        
    if order.upper() not in ("ASC", "DESC"):
        order = "DESC"
        
    sort_column = points_column if sort_by == "points" else sort_by
    query += f" ORDER BY {sort_prefix}{sort_column} {order.upper()} LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    try:
        with get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            players = []
            for row in rows:
                player = dict(row)
                player["points_by_system"] = {
                    "diario_as": player["points_as"],
                    "sofascore": player["points_sofascore"],
                    "average": player["points_average"],
                    "statistics": player["points_statistics"],
                }
                player["points"] = player[points_column]
                player["score_system"] = score_system
                players.append(player)
            return players
    except Exception as e:
        logger.error(f"Error fetching players: {e}")
        return []


def get_selections_from_db() -> list[dict]:
    """Return every national selection and its player count without pagination."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT team
                FROM players
                WHERE TRIM(team) <> ''
                ORDER BY remove_accents(team), team
                """
            ).fetchall()
    except Exception as e:
        logger.error(f"Error fetching selections: {e}")
        return []

    selections: dict[str, dict] = {}
    for row in rows:
        name = row["team"].strip()
        key = normalize_team_filter(name)
        if key not in selections:
            selections[key] = {"name": name, "player_count": 0}
        selections[key]["player_count"] += 1
    return list(selections.values())

def update_player_aggregate_stats(player_id: str, goals: int, assists: int):
    """Update total goals and assists for a specific player."""
    try:
        with get_connection() as conn:
            conn.execute("UPDATE players SET goals = ?, assists = ? WHERE id = ?", (goals, assists, player_id))
            conn.commit()
    except Exception as e:
        logger.error(f"Error updating aggregate player stats: {e}")

# --- Matches DB Operations ---
def save_matches_to_db(matches_list: list[dict]):
    """Insert or replace a batch of match fixture records."""
    try:
        with get_connection() as conn:
            conn.executemany("""
            INSERT OR REPLACE INTO matches (
                id, round_name, date, status, home_team_id, home_team_name, home_score, away_team_id, away_team_name, away_score
            ) VALUES (
                :id, :round_name, :date, :status, :home_team_id, :home_team_name, :home_score, :away_team_id, :away_team_name, :away_score
            )
            """, matches_list)
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving matches to database: {e}")

def get_matches_from_db(round_name: str = None, status: str = None) -> list[dict]:
    """Query match fixtures with optional filters."""
    query = "SELECT * FROM matches WHERE 1=1"
    params = []
    
    if round_name:
        query += " AND remove_accents(round_name) = ?"
        params.append(remove_accents(round_name).strip())
    if status:
        query += " AND LOWER(status) = LOWER(?)"
        params.append(status)
        
    query += " ORDER BY date ASC"
    
    try:
        with get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error querying matches: {e}")
        return []

# --- Standings DB Operations ---
def save_standings_to_db(standings_list: list[dict]):
    """Insert or replace a batch of group standings records."""
    try:
        with get_connection() as conn:
            conn.executemany("""
            INSERT OR REPLACE INTO standings (
                team_id, team_name, group_name, position, points, won, lost, tied, scored, against
            ) VALUES (
                :team_id, :team_name, :group_name, :position, :points, :won, :lost, :tied, :scored, :against
            )
            """, standings_list)
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving standings to database: {e}")

def get_standings_from_db() -> list[dict]:
    """Query all standings sorted by group and position."""
    try:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM standings ORDER BY group_name ASC, position ASC").fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching standings: {e}")
        return []

# --- Player Reports DB Operations ---
def save_player_reports_to_db(reports_list: list[dict]):
    """Insert or replace a list of match performance reports for players."""
    try:
        with get_connection() as conn:
            conn.executemany("""
            INSERT OR REPLACE INTO player_reports (
                player_id, match_id, round_name, home_team, away_team, home_score, away_score, 
                date, points, goals, assists, minutes_played, sofascore
            ) VALUES (
                :player_id, :match_id, :round_name, :home_team, :away_team, :home_score, :away_score, 
                :date, :points, :goals, :assists, :minutes_played, :sofascore
            )
            """, reports_list)
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving player reports to database: {e}")

def get_player_reports_from_db(player_id: str) -> list[dict]:
    """Retrieve all historical match reports for a specific player."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM player_reports WHERE player_id = ? ORDER BY date DESC", 
                (player_id,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching player reports: {e}")
        return []

def get_all_player_reports_from_db() -> list[dict]:
    """Retrieve all historical match reports for all players."""
    try:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM player_reports").fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching all player reports: {e}")
        return []

def get_user_squad_from_db(user_id: str) -> list[dict]:
    """Retrieve all player records currently in the user's squad."""
    query = """
    SELECT p.id, p.name, p.slug, p.position, p.team, p.fixed_price,
           p.fixed_price_source, p.market_value, p.points, p.status,
           p.goals, p.assists, upr.manual_rating AS manual_rating,
           usp.is_starter, usp.is_captain, usp.is_ariete
    FROM user_squad_players usp
    JOIN players p ON usp.player_id = p.id
    LEFT JOIN user_player_ratings upr
      ON upr.player_id = p.id AND upr.user_id = usp.user_id
    WHERE usp.user_id = ?
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(query, (user_id,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"Error fetching user squad from DB: {e}")
        return []

def save_user_squad_to_db(user_id: str, player_ids: list[str], captain_id: str | None, ariete_id: str | None):
    """Save the full squad (starters + substitutes) for a user to the database."""
    try:
        with get_connection() as conn:
            # Delete existing squad
            conn.execute("DELETE FROM user_squad_players WHERE user_id = ?", (user_id,))
            
            records = []
            for idx, pid in enumerate(player_ids):
                # The first 11 are starters, the rest are substitutes
                is_starter = 1 if idx < 11 else 0
                is_captain = 1 if pid == captain_id else 0
                is_ariete = 1 if pid == ariete_id else 0
                records.append((user_id, pid, is_starter, is_captain, is_ariete))
            
            if records:
                conn.executemany(
                    """INSERT INTO user_squad_players 
                       (user_id, player_id, is_starter, is_captain, is_ariete) 
                       VALUES (?, ?, ?, ?, ?)""",
                    records
                )
            conn.commit()
    except Exception as e:
        logger.error(f"Error saving user squad to DB: {e}")
        raise

def add_player_to_squad_db(user_id: str, player_id: str):
    """Add a player to the user's squad as a substitute (simulated buy/bid)."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_squad_players WHERE user_id = ? AND player_id = ?",
                (user_id, player_id)
            ).fetchone()
            if not row:
                conn.execute(
                    """INSERT INTO user_squad_players 
                       (user_id, player_id, is_starter, is_captain, is_ariete) 
                       VALUES (?, ?, 0, 0, 0)""",
                    (user_id, player_id)
                )
                conn.commit()
    except Exception as e:
        logger.error(f"Error adding player to squad in DB: {e}")

def remove_player_from_squad_db(user_id: str, player_id: str):
    """Remove a player from the user's squad (simulated sale)."""
    try:
        with get_connection() as conn:
            conn.execute(
                "DELETE FROM user_squad_players WHERE user_id = ? AND player_id = ?",
                (user_id, player_id)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Error removing player from squad in DB: {e}")
