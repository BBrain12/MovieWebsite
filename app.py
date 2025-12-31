from flask import Flask, render_template, request, jsonify, send_from_directory
import sqlite3
import os
import json
from pathlib import Path

app = Flask(__name__, template_folder='templates', static_folder='static')

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'imdbdata.db'
LIST_PATH = BASE_DIR / 'data' / 'list.json'
LIST_PATH.parent.mkdir(exist_ok=True)


# Saved-list storage is in the SQLite DB for safety/multi-user support.


def dict_factory(cursor, row):
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = dict_factory
    return conn


def init_saved_table():
    conn = get_conn()
    cur = conn.cursor()
    # Create lists table and saved table (with list_name) if missing.
    cur.execute('''
        CREATE TABLE IF NOT EXISTS lists (
            name TEXT PRIMARY KEY,
            created_at TEXT
        )
    ''')
    # add updated_at column if it doesn't exist (SQLite allows ADD COLUMN)
    try:
        cur.execute("ALTER TABLE lists ADD COLUMN updated_at TEXT")
    except Exception:
        # if the column exists, ignore
        pass
    cur.execute('''
        CREATE TABLE IF NOT EXISTS saved (
            list_name TEXT,
            id TEXT,
            title TEXT,
            year TEXT,
            genre TEXT,
            runtime TEXT,
            synopsis TEXT,
            PRIMARY KEY(list_name, id, year, genre)
        )
    ''')
    # add runtime and synopsis columns if they don't exist
    try:
        cur.execute("ALTER TABLE saved ADD COLUMN runtime TEXT")
    except Exception:
        # column probably exists, ignore
        pass
    try:
        cur.execute("ALTER TABLE saved ADD COLUMN synopsis TEXT")
    except Exception:
        pass
    conn.commit()
    # migrate JSON list if present into default list
    if LIST_PATH.exists():
        try:
            with open(LIST_PATH, 'r', encoding='utf-8') as f:
                listing = json.load(f)
        except Exception:
            listing = {}

        if listing:
            # ensure the default list exists
            try:
                cur.execute("INSERT OR IGNORE INTO lists (name, created_at) VALUES (?, datetime('now'))", ('default',))
            except Exception:
                pass
            for year, genres in listing.items():
                for genre, movies in genres.items():
                    for m in movies:
                        try:
                            cur.execute('INSERT OR IGNORE INTO saved (list_name, id, title, year, genre) VALUES (?, ?, ?, ?, ?)',
                                        ('default', m.get('id'), m.get('title'), year, genre))
                        except Exception:
                            pass
            # set updated_at for default list
            try:
                cur.execute("UPDATE lists SET updated_at = datetime('now') WHERE name = ?", ('default',))
            except Exception:
                pass
            conn.commit()
            # optional: remove JSON file after migration
            try:
                LIST_PATH.unlink()
            except Exception:
                pass

    conn.close()


init_saved_table()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/search')
def api_search():
    title = request.args.get('title', '').strip()
    year = request.args.get('year', '').strip()
    if not title and not year:
        return jsonify({'error': 'provide title and/or year'}), 400

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = dict_factory
    cur = conn.cursor()

    # build where clauses
    clauses = []
    params = []
    # exclude rows with missing or non-numeric year markers (IMDB uses '\N' for unknown)
    # require start_year to contain at least one digit and not be the IMDB '\N' marker or empty
    # these workarounds are easier than editing the database itself to erase all the entries I don't need thereby making the database file smaller
    clauses.append("start_year IS NOT NULL AND start_year != '' AND start_year != ? AND start_year GLOB '*[0-9]*'")
    params.append('\\N')
    # exclude rows with missing runtime (IMDB uses '\N' for unknown runtime)
    clauses.append("runtime IS NOT NULL AND runtime != '' AND runtime != ?")
    params.append('\\N')
    if title:
        # If no year provided, require a normalized exact title match (ignore case and extra spaces).
        if not year:
            # normalize the input title by collapsing whitespace
            normalized = ' '.join(title.split())
            # normalize DB title by trimming and collapsing multiple spaces (several REPLACE passes)
            clauses.append("LOWER(TRIM(REPLACE(REPLACE(REPLACE(title, '  ', ' '), '  ', ' '), '  ', ' '))) = LOWER(?)")
            params.append(normalized)
        else:
            # when year is provided, allow substring matches for flexibility
            clauses.append('LOWER(title) LIKE LOWER(?)')
            params.append(f'%{title}%')
    if year:
        # allow a flexible year match: exact year OR +/- 1 year when year is numeric
        try:
            y = int(year)
            clauses.append('(start_year = ? OR start_year = ? OR start_year = ?)')
            params.extend([str(y), str(y - 1), str(y + 1)])
        except ValueError:
            # non-numeric year? - fall back to exact match
            clauses.append('start_year = ?')
            params.append(year)

    where = ' AND '.join(clauses) if clauses else '1'
    # If the user didn't supply a year, limit results to 10 to avoid noisy matches.
    limit = 10 if not year else 25
    sql = f"SELECT id, title, start_year, genres, runtime FROM movies WHERE {where} ORDER BY start_year DESC, title LIMIT {limit}"
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return jsonify(rows)


@app.route('/api/add', methods=['POST'])
def api_add():
    data = request.get_json() or {}
    movie_id = data.get('id')
    list_name = data.get('list_name') or 'default'
    if not movie_id:
        return jsonify({'error': 'missing id'}), 400
    app.logger.info(f"api_add called: id={movie_id}, list_name={list_name}")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT id, title, start_year, genres, runtime FROM movies WHERE id = ?', (movie_id,))
    movie = cur.fetchone()
    if not movie:
        conn.close()
        return jsonify({'error': 'movie not found'}), 404

    year = movie.get('start_year') or 'Unknown'
    runtime = movie.get('runtime') or 'Unknown'
    # default empty synopsis for newly added saved rows
    synopsis = ''
    # Pick only the first associated genre (trim whitespace). If none, use '(no genre)'
    raw_genres = (movie.get('genres') or '')
    if raw_genres:
        parts = [g.strip() for g in raw_genres.split(',') if g and g.strip()]
        genres = [parts[0]] if parts else ['(no genre)']
    else:
        genres = ['(no genre)']

    # ensure list exists
    try:
        cur.execute('INSERT OR IGNORE INTO lists (name, created_at) VALUES (?, datetime("now"))', (list_name,))
        cur.execute('UPDATE lists SET updated_at = datetime("now") WHERE name = ?', (list_name,))
    except Exception as e:
        app.logger.exception('failed to ensure list exists')

    # add into saved table under the first genre only
    g = (genres[0] or '(no genre)').strip()
    try:
        cur.execute('INSERT OR IGNORE INTO saved (list_name, id, title, year, genre, runtime, synopsis) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (list_name, movie['id'], movie['title'], year, g, runtime, synopsis))
        app.logger.info(f"Inserted saved: list={list_name}, id={movie['id']}, year={year}, genre={g}, runtime={runtime}")
    except Exception as e:
        app.logger.exception('failed to insert saved row')

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'movie': movie})


@app.route('/api/list')
def api_list():
    # Read from saved table and assemble the nested dict
    list_name = request.args.get('list_name') or 'default'
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT year, genre, id, title, runtime, synopsis FROM saved WHERE list_name = ?', (list_name,))
    rows = cur.fetchall()
    app.logger.info(f"api_list: list_name={list_name}, rows_found={len(rows)}")
    conn.close()

    listing = {}
    for r in rows:
        y = r.get('year') or 'Unknown'
        g = r.get('genre') or '(no genre)'
        year_bucket = listing.setdefault(y, {})
        genre_bucket = year_bucket.setdefault(g, [])
        genre_bucket.append({'id': r.get('id'), 'title': r.get('title'), 'runtime': r.get('runtime'), 'synopsis': r.get('synopsis')})

    # sort
    sorted_listing = {}
    for year in sorted(listing.keys(), reverse=True):
        sorted_listing[year] = {}
        for genre in sorted(listing[year].keys()):
            sorted_listing[year][genre] = sorted(listing[year][genre], key=lambda x: x['title'].lower())

    return jsonify(sorted_listing)


@app.route('/api/lists', methods=['GET'])
def api_lists():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('SELECT name, created_at, updated_at FROM lists ORDER BY name')
    rows = cur.fetchall()
    conn.close()
    return jsonify([r for r in rows])


@app.route('/api/lists', methods=['POST'])
def api_create_list():
    data = request.get_json() or {}
    name = data.get('name')
    if not name:
        return jsonify({'error': 'missing name'}), 400
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('INSERT INTO lists (name, created_at, updated_at) VALUES (?, datetime("now"), datetime("now"))', (name,))
        conn.commit()
    except Exception as e:
        conn.close()
        return jsonify({'error': 'could not create list', 'detail': str(e)}), 400
    conn.close()
    return jsonify({'ok': True, 'name': name})


@app.route('/api/lists', methods=['DELETE'])
def api_delete_list():
    data = request.get_json() or {}
    name = data.get('name')
    if not name:
        return jsonify({'error': 'missing name'}), 400
    if name == 'default':
        return jsonify({'error': 'cannot delete default list'}), 400
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM saved WHERE list_name = ?', (name,))
    cur.execute('DELETE FROM lists WHERE name = ?', (name,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/lists/rename', methods=['POST'])
def api_rename_list():
    data = request.get_json() or {}
    old = data.get('old')
    new = data.get('new')
    if not old or not new:
        return jsonify({'error': 'missing old or new name'}), 400
    if old == 'default':
        return jsonify({'error': 'cannot rename default list'}), 400
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute('UPDATE saved SET list_name = ? WHERE list_name = ?', (new, old))
        cur.execute('UPDATE lists SET name = ? WHERE name = ?', (new, old))
        conn.commit()
        # update timestamp on new list
        try:
            cur.execute('UPDATE lists SET updated_at = datetime("now") WHERE name = ?', (new,))
            conn.commit()
        except Exception:
            pass
    except Exception as e:
        conn.close()
        return jsonify({'error': 'rename failed', 'detail': str(e)}), 400
    conn.close()
    return jsonify({'ok': True, 'name': new})


@app.route('/api/remove', methods=['POST'])
def api_remove():
    data = request.get_json() or {}
    movie_id = data.get('id')
    year = data.get('year')
    genre = data.get('genre')
    list_name = data.get('list_name') or 'default'
    if not movie_id:
        return jsonify({'error': 'missing id'}), 400

    conn = get_conn()
    cur = conn.cursor()
    if year and genre:
        cur.execute('DELETE FROM saved WHERE list_name = ? AND id = ? AND year = ? AND genre = ?', (list_name, movie_id, year, genre))
    else:
        cur.execute('DELETE FROM saved WHERE list_name = ? AND id = ?', (list_name, movie_id))
    affected = cur.rowcount
    conn.commit()
    # update list timestamp if any rows were changed
    if affected:
        try:
            cur.execute('UPDATE lists SET updated_at = datetime("now") WHERE name = ?', (list_name,))
            conn.commit()
        except Exception:
            pass
    conn.close()
    if affected:
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'message': 'movie not found in list'}), 404


@app.route('/api/update', methods=['POST'])
def api_update():
    data = request.get_json() or {}
    movie_id = data.get('id')
    new_title = data.get('new_title')
    new_year = data.get('new_year')
    new_genre = data.get('new_genre')
    new_synopsis = data.get('new_synopsis')
    list_name = data.get('list_name') or 'default'
    if not movie_id:
        return jsonify({'error': 'missing id'}), 400

    conn = get_conn()
    cur = conn.cursor()
    # find existing rows for this id in the requested list
    cur.execute('SELECT year, genre, title, synopsis, runtime FROM saved WHERE id = ? AND list_name = ?', (movie_id, list_name))
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return jsonify({'error': 'movie not found in list'}), 404

    # If only changing title, update title for all rows with this id
    if new_title and not new_year and not new_genre:
        if new_synopsis is not None:
            cur.execute('UPDATE saved SET title = ?, synopsis = ? WHERE id = ? AND list_name = ?', (new_title, new_synopsis, movie_id, list_name))
        else:
            cur.execute('UPDATE saved SET title = ? WHERE id = ? AND list_name = ?', (new_title, movie_id, list_name))
        conn.commit()
        try:
            cur.execute('UPDATE lists SET updated_at = datetime("now") WHERE name = ?', (list_name,))
            conn.commit()
        except Exception:
            pass
        conn.close()
        return jsonify({'ok': True})

    # Otherwise, remove existing placements and re-insert with new values
    cur.execute('DELETE FROM saved WHERE id = ? AND list_name = ?', (movie_id, list_name))

    target_years = set()
    target_genres = set()
    if new_year:
        target_years.add(str(new_year))
    if new_genre:
        target_genres.add(new_genre.strip())

    if not target_years or not target_genres:
        # re-add to original placements
        for r in rows:
            target_years.add(r.get('year') or 'Unknown')
            target_genres.add(r.get('genre') or '(no genre)')

    insert_title = new_title or (rows[0].get('title') if rows else '')
    insert_synopsis = new_synopsis if new_synopsis is not None else (rows[0].get('synopsis') if rows else '')
    insert_runtime = rows[0].get('runtime') if rows else ''
    for y in target_years:
        for g in target_genres:
            cur.execute('INSERT OR IGNORE INTO saved (list_name, id, title, year, genre, synopsis, runtime) VALUES (?, ?, ?, ?, ?, ?, ?)',
                        (list_name, movie_id, insert_title, y, g, insert_synopsis, insert_runtime))

    conn.commit()
    try:
        cur.execute('UPDATE lists SET updated_at = datetime("now") WHERE name = ?', (list_name,))
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/clear', methods=['POST'])
def api_clear():
    # remove list file or truncate
    data = request.get_json() or {}
    list_name = data.get('list_name') or 'default'
    conn = get_conn()
    cur = conn.cursor()
    cur.execute('DELETE FROM saved WHERE list_name = ?', (list_name,))
    conn.commit()
    try:
        cur.execute('UPDATE lists SET updated_at = datetime("now") WHERE name = ?', (list_name,))
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify({'ok': True})


@app.route('/stream')
def stream():
    # SSE endpoint that notifies clients when a list's updated_at changes
    list_name = request.args.get('list_name') or 'default'

    def event_stream(name):
        last = None
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = dict_factory
        cur = conn.cursor()
        try:
            cur.execute('SELECT updated_at FROM lists WHERE name = ?', (name,))
            r = cur.fetchone()
            last = r.get('updated_at') if r else None
        except Exception:
            last = None

        import time
        try:
            while True:
                time.sleep(1.0)
                try:
                    cur.execute('SELECT updated_at FROM lists WHERE name = ?', (name,))
                    r = cur.fetchone()
                    now = r.get('updated_at') if r else None
                except Exception:
                    now = None
                if now != last:
                    last = now
                    yield f'data: updated\n\n'
        finally:
            conn.close()

    return app.response_class(event_stream(list_name), mimetype='text/event-stream')


if __name__ == '__main__':
    app.run(debug=True)
