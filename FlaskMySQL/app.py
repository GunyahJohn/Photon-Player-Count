
import os
import logging
import statistics
from datetime import datetime, timedelta

import pymysql

from flask import Flask, request, jsonify
from pymysql import converters
from werkzeug.exceptions import HTTPException


app = Flask(__name__)
app.logger.setLevel(logging.INFO)


# ============================================================
# DATABASE CONFIGURATION
# ============================================================

MYSQL_DATABASE_HOST = os.getenv("MYSQL_DATABASE_HOST")
MYSQL_DATABASE_USER = os.getenv("MYSQL_DATABASE_USER")
MYSQL_DATABASE_PASSWORD = os.getenv("MYSQL_DATABASE_PASSWORD")
MYSQL_DATABASE_DB = os.getenv("MYSQL_DATABASE_DB")

# TiDB Cloud Starter normally uses port 4000.
MYSQL_DATABASE_PORT = int(
    os.getenv("MYSQL_DATABASE_PORT", "4000")
)

# Use MYSQL_SSL_CA if supplied; otherwise look for ca.pem
# beside this app.py file.
MYSQL_SSL_CA = os.getenv(
    "MYSQL_SSL_CA",
    os.path.join(os.path.dirname(__file__), "ca.pem")
)


def connectToDB():

    required = {
        "MYSQL_DATABASE_HOST": MYSQL_DATABASE_HOST,
        "MYSQL_DATABASE_USER": MYSQL_DATABASE_USER,
        "MYSQL_DATABASE_PASSWORD": MYSQL_DATABASE_PASSWORD,
        "MYSQL_DATABASE_DB": MYSQL_DATABASE_DB
    }

    missing = [
        name for name, value in required.items()
        if not value
    ]

    if missing:
        raise RuntimeError(
            "Missing database environment variables: "
            + ", ".join(missing)
        )

    if not os.path.isfile(MYSQL_SSL_CA):
        raise RuntimeError(
            "TiDB CA certificate not found. "
            "Expected file at: " + MYSQL_SSL_CA
        )

    conv = converters.conversions.copy()
    conv[246] = float

    conn = pymysql.connect(
        host=MYSQL_DATABASE_HOST,
        port=MYSQL_DATABASE_PORT,
        user=MYSQL_DATABASE_USER,
        password=MYSQL_DATABASE_PASSWORD,
        database=MYSQL_DATABASE_DB,
        cursorclass=pymysql.cursors.DictCursor,
        conv=conv,
        connect_timeout=10,
        read_timeout=15,
        write_timeout=15,
        autocommit=False,
        ssl={
            "ca": MYSQL_SSL_CA,
            "check_hostname": True
        }
    )

    return conn


# ============================================================
# REQUEST HELPERS
# ============================================================

def get_request_data():

    json_data = request.get_json(silent=True)

    if isinstance(json_data, dict):
        return json_data

    return request.values


def error_response(message, status=400):
    return jsonify({
        "success": False,
        "error": message
    }), status


# ============================================================
# HOME / HEALTH CHECK
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "status": "online",
        "service": "Gunyahs Gorilla Horror Player Count API",
        "endpoints": [
            "/update_monke_count",
            "/how_many_monke",
            "/how_many_monke_graph"
        ]
    })


@app.route("/health/db", methods=["GET"])
def database_health():

    conn = None

    try:
        conn = connectToDB()

        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 AS result")
            result = cursor.fetchone()

        return jsonify({
            "status": "online",
            "database": "connected",
            "test": result["result"]
        })

    except Exception:
        app.logger.exception("Database health check failed")

        return jsonify({
            "status": "error",
            "database": "unavailable"
        }), 500

    finally:
        if conn:
            conn.close()


# ============================================================
# UPDATE PLAYER COUNT
# ============================================================

@app.route("/update_monke_count", methods=["POST"])
def update_monke_count():

    data = get_request_data()

    raw_count = data.get("player_count")

    if raw_count is None:
        return error_response("No player count supplied")

    try:
        player_count = int(raw_count)

    except (ValueError, TypeError, OverflowError):
        return error_response("Invalid player count")

    if not 0 <= player_count <= 5000000:
        return error_response("Player count is out of range")

    room_name = str(data.get("room_name", "none"))[:255]
    game_version = str(data.get("game_version", "none"))[:100]
    game_name = str(data.get("game_name", "none"))[:255]

    conn = None

    try:
        conn = connectToDB()

        with conn.cursor() as cursor:

            cursor.execute(
                """
                INSERT INTO Monke
                (
                    player_count,
                    room_name,
                    game_version,
                    game_name
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    player_count,
                    room_name,
                    game_version,
                    game_name
                )
            )

        conn.commit()

        app.logger.info(
            "Player count updated: %s",
            player_count
        )

        return jsonify({
            "success": True,
            "player_count": player_count
        })

    except Exception:
        if conn:
            conn.rollback()

        app.logger.exception("Failed to update player count")

        return jsonify({
            "success": False,
            "error": "Database error"
        }), 500

    finally:
        if conn:
            conn.close()


# ============================================================
# GET CURRENT PLAYER COUNT
# ============================================================

@app.route("/how_many_monke", methods=["GET"])
def how_many_monke():

    conn = None

    try:
        conn = connectToDB()

        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT player_count
                FROM Monke
                ORDER BY timestamp DESC
                LIMIT 200
                """
            )

            results = cursor.fetchall()

        if not results:
            return jsonify(0)

        counts = [
            int(row["player_count"])
            for row in results
        ]

        median = round(statistics.median(counts))

        return jsonify(median)

    except Exception:
        app.logger.exception("Failed to retrieve player count")

        return jsonify({
            "error": "Database error"
        }), 500

    finally:
        if conn:
            conn.close()


# ============================================================
# PLAYER COUNT GRAPH
# ============================================================

@app.route("/how_many_monke_graph", methods=["GET"])
def how_many_monke_graph():

    try:
        hours = float(request.args.get("hours", 24))

    except (ValueError, TypeError):
        return error_response("Invalid hours parameter")

    if not 0 < hours <= 720:
        return error_response(
            "Hours must be between 0 and 720"
        )

    conn = None

    try:
        conn = connectToDB()

        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)

        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT timestamp, player_count
                FROM Monke
                WHERE timestamp >= %s
                  AND timestamp <= %s
                  AND player_count >= 0
                  AND player_count <= 5000000
                ORDER BY timestamp ASC
                """,
                (start_time, end_time)
            )

            results = cursor.fetchall()

        # Return an empty graph if there are no records.
        if not results:
            return jsonify([])

        # Convert database rows into JSON-friendly dictionaries.
        all_data = [
            {
                "timestamp": row["timestamp"].isoformat()
                if row["timestamp"] else None,
                "player_count": int(row["player_count"])
            }
            for row in results
        ]

        # Keep the graph response at or below 1024 points.
        max_points = 1024

        if len(all_data) > max_points:

            step = (len(all_data) - 1) / (max_points - 1)

            all_data = [
                all_data[round(i * step)]
                for i in range(max_points)
            ]

        return jsonify(all_data)

    except Exception:
        app.logger.exception("Failed to retrieve player count graph")

        return jsonify({
            "error": "Graph database error"
        }), 500

    finally:
        if conn:
            conn.close()


# ============================================================
# ERROR HANDLING
# ============================================================

@app.errorhandler(Exception)
def handle_errors(error):

    if isinstance(error, HTTPException):
        return error

    app.logger.exception(
        "Unhandled error on %s",
        request.path
    )

    return jsonify({
        "error": "Internal server error"
    }), 500
