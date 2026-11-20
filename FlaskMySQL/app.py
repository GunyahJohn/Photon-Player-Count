
import os
import logging
import statistics
import traceback

from datetime import datetime, timedelta

import pandas as pd
import pymysql

from flask import Flask, request, jsonify
from pymysql import converters

app = Flask(__name__)

app.logger.setLevel(logging.INFO)

# Database configuration from Vercel environment variables
MYSQL_DATABASE_HOST = os.getenv("MYSQL_DATABASE_HOST")
MYSQL_DATABASE_USER = os.getenv("MYSQL_DATABASE_USER")
MYSQL_DATABASE_PASSWORD = os.getenv("MYSQL_DATABASE_PASSWORD")
MYSQL_DATABASE_DB = os.getenv("MYSQL_DATABASE_DB")
MYSQL_DATABASE_PORT = int(os.getenv("MYSQL_DATABASE_PORT", "3306"))
MYSQL_SSL_CA = os.getenv("MYSQL_SSL_CA")


def connectToDB():
    conv = converters.conversions.copy()
    conv[246] = float

    conn = pymysql.connect(
        host=MYSQL_DATABASE_HOST,
        port=4000,
        user=MYSQL_DATABASE_USER,
        password=MYSQL_DATABASE_PASSWORD,
        database=MYSQL_DATABASE_DB,
        cursorclass=pymysql.cursors.DictCursor,
        conv=conv,
        connect_timeout=10,
        ssl={
            "ca": os.path.join(
                os.path.dirname(__file__),
                "ca.pem"
            ),
            "check_hostname": True
        }
    )

    return conn, conn.cursor()
# ----------------------------------
# UPDATE PLAYER COUNT
# ----------------------------------

@app.route('/update_monke_count', methods=["POST"])
def update_monke_count():

    player_count = request.values.get("player_count")

    if player_count is None:
        return jsonify({"error": "No player count"}), 400

    try:
        player_count = int(player_count)

        if player_count < 0 or player_count > 5000000:
            return jsonify({"error": "Invalid player count"}), 400

    except (ValueError, TypeError):
        return jsonify({"error": "Invalid player count"}), 400

    data = {
        "player_count": player_count,
        "room_name": request.values.get("room_name", "none"),
        "game_version": request.values.get("game_version", "none"),
        "game_name": request.values.get("game_name", "none")
    }

    conn = None

    try:
        conn, curr = connectToDB()

        query = """
            INSERT INTO Monke
            (
                player_count,
                room_name,
                game_version,
                game_name
            )
            VALUES
            (
                %(player_count)s,
                %(room_name)s,
                %(game_version)s,
                %(game_name)s
            )
        """

        curr.execute(query, data)
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
        app.logger.exception("Failed to update player count")
        return jsonify({"error": "Database error"}), 500

    finally:
        if conn:
            conn.close()


# ----------------------------------
# GET CURRENT PLAYER COUNT
# ----------------------------------

@app.route('/how_many_monke', methods=["GET"])
def how_many_monke():

    conn = None

    try:
        conn, curr = connectToDB()

        curr.execute("""
            SELECT player_count
            FROM Monke
            ORDER BY timestamp DESC
            LIMIT 200
        """)

        results = curr.fetchall()

        counts = [
            row["player_count"]
            for row in results
        ]

        if not counts:
            return jsonify(0)

        median = round(statistics.median(counts))

        return jsonify(median)

    except Exception:
        app.logger.exception("Failed to retrieve player count")
        return jsonify({"error": "Database error"}), 500

    finally:
        if conn:
            conn.close()


# ----------------------------------
# CACHE FUNCTIONS
# ----------------------------------

def add_to_cache(data, conn, curr):

    query = """
        INSERT INTO Cache
        (
            timestamp,
            player_count
        )
        VALUES
        (
            %(timestamp)s,
            %(player_count)s
        )
    """

    for row in data:
        curr.execute(query, row)


def get_data_from_hour(hour_timestamp, conn, curr,
                       should_add_to_cache):

    start = hour_timestamp

    end = hour_timestamp.replace(
        minute=0,
        second=0,
        microsecond=0
    ) + timedelta(hours=1)

    query = """
        SELECT timestamp, player_count
        FROM Cache
        WHERE timestamp > %s
        AND timestamp < %s
        AND player_count < 5000000
        ORDER BY timestamp DESC
    """

    curr.execute(query, (start, end))
    output = curr.fetchall()

    if output:
        return output

    query = """
        SELECT timestamp, player_count
        FROM Monke
        WHERE timestamp > %s
        AND timestamp < %s
        AND player_count < 5000000
        ORDER BY timestamp DESC
    """

    curr.execute(query, (start, end))
    output = curr.fetchall()

    if not output:
        return []

    df = pd.DataFrame(output)

    df["player_count"] = pd.to_numeric(
        df["player_count"],
        errors="coerce"
    )

    df = df.dropna()

    if df.empty:
        return []

    df["player_count"] = df["player_count"].astype("int32")

    length = 500

    df["player_count"] = (
        df["player_count"]
        .rolling(length + 1)
        .median()
        .shift(-(length // 2))
    )

    for _ in range(2):
        df = df[
            df["player_count"] >
            df["player_count"].shift(-1) - 100
        ]

        df = df[
            df["player_count"] <
            df["player_count"].shift(-1) + 100
        ]

    df = df.dropna()

    output = df.to_dict("records")

    output = output[::max(1, len(output) // 30)]

    if should_add_to_cache and output:
        add_to_cache(output, conn, curr)

    return output


# ----------------------------------
# PLAYER COUNT GRAPH
# ----------------------------------

@app.route('/how_many_monke_graph', methods=["GET"])
def how_many_monke_graph():

    try:
        hours = float(request.args.get("hours", 24))

        if not 0 < hours <= 720:
            return jsonify({"error": "Hours must be between 0 and 720"}), 400

    except (ValueError, TypeError):
        hours = 24

    conn = None

    try:
        conn, curr = connectToDB()

        now = datetime.utcnow()

        last_hour = now.replace(
            minute=0,
            second=0,
            microsecond=0
        )

        start_time = now - timedelta(hours=hours)

        old_hours = []
        current = last_hour

        while current > start_time:
            current -= timedelta(hours=1)
            old_hours.append(current)

        old_hours.append(start_time)

        all_data = []

        for old_hour in set(old_hours):
            all_data.extend(
                get_data_from_hour(
                    old_hour,
                    conn,
                    curr,
                    True
                )
            )

        all_data.extend(
            get_data_from_hour(
                last_hour,
                conn,
                curr,
                False
            )
        )

        all_data.sort(
            key=lambda row: row["timestamp"]
        )

        num_values = 1024

        if len(all_data) > num_values:
            step = max(1, len(all_data) // num_values)
            all_data = all_data[::step]

        conn.commit()

        return jsonify(all_data)

    except Exception:
        app.logger.exception("Failed to retrieve graph")
        return jsonify({"error": "Graph database error"}), 500

    finally:
        if conn:
            conn.close()


# ----------------------------------
# HEALTH CHECK
# ----------------------------------

@app.route('/', methods=["GET"])
def home():
    return jsonify({
        "status": "online",
        "service": "Gunyahs Gorilla Horror Player Count API"
    })


@app.errorhandler(Exception)
def exceptions(error):

    app.logger.error(
        "Unhandled error: %s\n%s",
        error,
        traceback.format_exc()
    )

    return jsonify({
        "error": "Internal server error"
    }), 500
