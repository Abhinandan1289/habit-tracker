from flask import Flask, render_template, request, session, redirect, g
from werkzeug.security import generate_password_hash, check_password_hash
import os, random, string
from datetime import date, datetime, timedelta
from calendar import monthrange
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'habittracker_xK9mP2nQ7wR4vL6jH1')

# ── DATABASE — supports both PostgreSQL (production) and SQLite (local) ──
DATABASE_URL = os.environ.get('DATABASE_URL', None)
USE_PG = DATABASE_URL is not None

if USE_PG:
    import psycopg2
    import psycopg2.extras
    # Render gives postgres:// but psycopg2 needs postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

def db():
    if USE_PG:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    else:
        import sqlite3
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        conn = sqlite3.connect(os.path.join(BASE_DIR, 'habit.db'))
        conn.row_factory = sqlite3.Row
        return conn

def qmark(sql):
    """Convert ? placeholders to %s for PostgreSQL"""
    if USE_PG:
        return sql.replace('?', '%s')
    return sql

def fetchone(cur):
    row = cur.fetchone()
    if row is None: return None
    if USE_PG: return dict(row)
    return row

def fetchall(cur):
    rows = cur.fetchall()
    if USE_PG: return [dict(r) for r in rows]
    return rows

def execute(conn, sql, params=()):
    sql = qmark(sql)
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur

def init_db():
    conn = db()
    cur = conn.cursor()
    if not USE_PG:
        # SQLite: drop and recreate all tables fresh to avoid schema mismatch
        cur.executescript('''
            DROP TABLE IF EXISTS reactions;
            DROP TABLE IF EXISTS logs;
            DROP TABLE IF EXISTS friends;
            DROP TABLE IF EXISTS habits;
            DROP TABLE IF EXISTS users;
        ''')
        conn.commit()
    if USE_PG:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                invite_code TEXT UNIQUE NOT NULL,
                avatar TEXT DEFAULT '🎯',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS habits (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                name TEXT NOT NULL,
                icon TEXT DEFAULT '🎯',
                color TEXT DEFAULT 'green',
                position INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY,
                habit_id INTEGER NOT NULL REFERENCES habits(id),
                user_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                UNIQUE(habit_id, log_date)
            )''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS friends (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                friend_id INTEGER NOT NULL,
                UNIQUE(user_id, friend_id)
            )''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS reactions (
                id SERIAL PRIMARY KEY,
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )''')
    else:
        cur.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                invite_code TEXT UNIQUE NOT NULL,
                avatar TEXT DEFAULT '🎯',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                icon TEXT DEFAULT '🎯',
                color TEXT DEFAULT 'green',
                position INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                UNIQUE(habit_id, log_date)
            );
            CREATE TABLE IF NOT EXISTS friends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                friend_id INTEGER NOT NULL,
                UNIQUE(user_id, friend_id)
            );
            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')
    conn.commit()
    conn.close()

def make_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=6))
        conn = db()
        cur = execute(conn, 'SELECT id FROM users WHERE invite_code=?', (code,))
        ex = fetchone(cur)
        conn.close()
        if not ex: return code

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'uid' not in session: return redirect('/login')
        return f(*a, **kw)
    return wrap

# ── HELPERS ──────────────────────────────────────────────
AVATARS = ['🎯','💪','🏃','📚','🧘','💧','🌱','🔥','⚡','🏆','✨','🎵','🦁','🐯','🦊','🐺','🌟','💎','🚀','🎸']
HABIT_ICONS = ['🎯','💪','🏃','📚','🧘','💧','🌱','🔥','⚡','🏆','✨','🎵','🥗','😴','✍️','🧠']
HABIT_COLORS = ['green','blue','purple','orange','red','pink']

def get_user_habits(uid):
    conn = db()
    cur = execute(conn, 'SELECT * FROM habits WHERE user_id=? ORDER BY position', (uid,))
    habits = fetchall(cur)
    conn.close()
    return habits

def get_done(habit_id, year, month):
    conn = db()
    cur = execute(conn, 'SELECT log_date FROM logs WHERE habit_id=? AND log_date LIKE ?',
                  (habit_id, f'{year}-{month:02d}-%'))
    rows = fetchall(cur)
    conn.close()
    return set(r['log_date'] for r in rows)

def streak(habit_id):
    conn = db()
    cur = execute(conn, 'SELECT log_date FROM logs WHERE habit_id=? ORDER BY log_date DESC', (habit_id,))
    rows = fetchall(cur)
    conn.close()
    if not rows: return 0
    done = {r['log_date'] for r in rows}
    today = date.today()
    cur_date = today if today.strftime('%Y-%m-%d') in done else today - timedelta(days=1)
    s = 0
    while cur_date.strftime('%Y-%m-%d') in done:
        s += 1
        cur_date -= timedelta(days=1)
    return s

def best_streak(habit_id):
    conn = db()
    cur = execute(conn, 'SELECT log_date FROM logs WHERE habit_id=? ORDER BY log_date ASC', (habit_id,))
    rows = fetchall(cur)
    conn.close()
    if not rows: return 0
    dates = sorted(r['log_date'] for r in rows)
    best = cur_s = 1
    for i in range(1, len(dates)):
        d1 = datetime.strptime(dates[i-1], '%Y-%m-%d').date()
        d2 = datetime.strptime(dates[i],   '%Y-%m-%d').date()
        cur_s = cur_s + 1 if (d2 - d1).days == 1 else 1
        best = max(best, cur_s)
    return best

def month_stats(habit_id, year, month):
    days = monthrange(year, month)[1]
    today = date.today()
    done = get_done(habit_id, year, month)
    d = m = 0
    for day in range(1, days+1):
        ds = f'{year}-{month:02d}-{day:02d}'
        if date(year, month, day) > today: break
        if ds in done: d += 1
        else: m += 1
    return d, m, days

def build_dots(habit_id, year, month, is_mine=False):
    today = date.today()
    today_str = today.strftime('%Y-%m-%d')
    yest_str = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    done = get_done(habit_id, year, month)
    days_in_month = monthrange(year, month)[1]
    is_current_month = (year == today.year and month == today.month)
    dots = []
    for day in range(1, days_in_month+1):
        ds = f'{year}-{month:02d}-{day:02d}'
        day_date = date(year, month, day)
        is_today  = ds == today_str
        is_future = day_date > today
        cls = 'done' if ds in done else ('today' if is_today else ('future' if is_future else 'missed'))
        clickable = is_mine and is_current_month and (is_today or ds == yest_str)
        locked    = is_mine and not is_future and not is_today and ds != yest_str
        dots.append({'day': day, 'ds': ds, 'cls': cls, 'clickable': clickable, 'locked': locked})
    return dots

def year_overview(habit_id, year):
    today = date.today()
    result = []
    for mo in range(1, 13):
        d, m, _ = month_stats(habit_id, year, mo)
        passed = d + m
        if passed == 0:
            cls, pct = 'future', 0
        else:
            pct = round((d / passed) * 100)
            if year == today.year and mo == today.month: cls = 'curr'
            elif pct >= 60: cls = 'mostly'
            elif pct >= 30: cls = 'half'
            else:           cls = 'all-red'
        result.append({'mo': mo, 'cls': cls, 'pct': pct, 'd': d, 'm': m})
    return result

def user_streak(uid):
    """Overall streak = streak of first habit"""
    habits = get_user_habits(uid)
    if not habits: return 0
    return streak(habits[0]['id'])

# ── PAGES ─────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect('/dashboard' if 'uid' in session else '/login')

@app.route('/login')
def login_page():
    if 'uid' in session: return redirect('/dashboard')
    return render_template('auth.html', mode='login', error=None)

@app.route('/register')
def register_page():
    if 'uid' in session: return redirect('/dashboard')
    return render_template('auth.html', mode='register', error=None, avatars=AVATARS)

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    year  = int(request.args.get('year',  today.year))
    month = int(request.args.get('month', today.month))
    uid   = session['uid']

    conn = db()
    user = fetchone(execute(conn, 'SELECT * FROM users WHERE id=?', (uid,)))
    friend_rows = fetchall(execute(conn, '''
        SELECT u.* FROM friends f JOIN users u ON f.friend_id=u.id WHERE f.user_id=?
    ''', (uid,)))
    conn.close()

    habits = get_user_habits(uid)
    months_list = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    months_full = ['JANUARY','FEBRUARY','MARCH','APRIL','MAY','JUNE','JULY','AUGUST','SEPTEMBER','OCTOBER','NOVEMBER','DECEMBER']

    prev_month = month-1 if month>1 else 12
    prev_year  = year   if month>1 else year-1
    next_month = month+1 if month<12 else 1
    next_year  = year   if month<12 else year+1

    offset = (date(year, month, 1).weekday() + 1) % 7

    # Build my habit data
    my_habits_data = []
    for h in habits:
        hid = h['id']
        d, m_c, total = month_stats(hid, year, month)
        passed = d + m_c
        pct = round((d/passed)*100) if passed else 0
        my_habits_data.append({
            'habit': h,
            'dots': build_dots(hid, year, month, True),
            'done': d, 'missed': m_c, 'total': total, 'pct': pct,
            'streak': streak(hid),
            'best': best_streak(hid),
            'year_data': year_overview(hid, year),
        })

    # Build friend data
    friend_data = []
    for f in friend_rows:
        fhabits = get_user_habits(f['id'])
        fhabits_data = []
        for h in fhabits:
            hid = h['id']
            d, m_c, total = month_stats(hid, year, month)
            passed = d + m_c
            pct = round((d/passed)*100) if passed else 0
            fhabits_data.append({
                'habit': h,
                'dots': build_dots(hid, year, month, False),
                'done': d, 'missed': m_c, 'total': total, 'pct': pct,
                'streak': streak(hid),
                'best': best_streak(hid),
                'year_data': year_overview(hid, year),
            })
        friend_data.append({'user': f, 'habits_data': fhabits_data})

    return render_template('dashboard.html',
        user=user, year=year, month=month,
        month_name=months_full[month-1],
        months_list=months_list, months_full=months_full,
        offset=offset,
        my_habits_data=my_habits_data,
        friend_data=friend_data,
        today_str=today.strftime('%Y-%m-%d'),
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        habit_icons=HABIT_ICONS,
        max_habits=3,
        can_add_habit=len(habits) < 3,
        avatars=AVATARS,
    )

# ── AUTH ──────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def do_register():
    username = request.form.get('username','').strip().lower()
    password = request.form.get('password','')
    avatar   = request.form.get('avatar','🎯')
    habit1   = request.form.get('habit1','CONSISTENT ON EVERYTHING').strip().upper() or 'CONSISTENT ON EVERYTHING'
    habit1_icon = request.form.get('habit1_icon','🎯')

    if len(username) < 3:
        return render_template('auth.html', mode='register', error='Username must be at least 3 characters', avatars=AVATARS)
    if len(password) < 6:
        return render_template('auth.html', mode='register', error='Password must be at least 6 characters', avatars=AVATARS)

    conn = db()
    if fetchone(execute(conn, 'SELECT id FROM users WHERE username=?', (username,))):
        conn.close()
        return render_template('auth.html', mode='register', error='Username already taken', avatars=AVATARS)

    execute(conn, 'INSERT INTO users (username,password,invite_code,avatar) VALUES (?,?,?,?)',
            (username, generate_password_hash(password), make_code(), avatar))
    conn.commit()
    user = fetchone(execute(conn, 'SELECT * FROM users WHERE username=?', (username,)))
    # Create first habit
    execute(conn, 'INSERT INTO habits (user_id,name,icon,color,position) VALUES (?,?,?,?,?)',
            (user['id'], habit1, habit1_icon, 'green', 0))
    conn.commit()
    conn.close()

    session['uid'] = user['id']
    session['uname'] = user['username']
    return redirect('/dashboard')

@app.route('/api/login', methods=['POST'])
def do_login():
    username = request.form.get('username','').strip().lower()
    password = request.form.get('password','')
    conn = db()
    user = fetchone(execute(conn, 'SELECT * FROM users WHERE username=?', (username,)))
    conn.close()
    if not user or not check_password_hash(user['password'], password):
        return render_template('auth.html', mode='login', error='Invalid username or password', avatars=AVATARS)
    session['uid'] = user['id']
    session['uname'] = user['username']
    return redirect('/dashboard')

@app.route('/logout')
def do_logout():
    session.clear()
    return redirect('/login')

# ── HABIT MANAGEMENT ──────────────────────────────────────
@app.route('/habit/add', methods=['POST'])
@login_required
def add_habit():
    uid = session['uid']
    habits = get_user_habits(uid)
    if len(habits) >= 3:
        return redirect('/dashboard')
    name = request.form.get('name','').strip().upper() or 'NEW HABIT'
    icon = request.form.get('icon','🎯')
    color= request.form.get('color','green')
    pos  = len(habits)
    conn = db()
    execute(conn, 'INSERT INTO habits (user_id,name,icon,color,position) VALUES (?,?,?,?,?)',
            (uid, name, icon, color, pos))
    conn.commit()
    conn.close()
    return redirect('/dashboard')

@app.route('/habit/delete', methods=['POST'])
@login_required
def delete_habit():
    habit_id = int(request.form.get('habit_id',0))
    uid = session['uid']
    conn = db()
    h = fetchone(execute(conn, 'SELECT * FROM habits WHERE id=? AND user_id=?', (habit_id, uid)))
    if h:
        execute(conn, 'DELETE FROM logs WHERE habit_id=?', (habit_id,))
        execute(conn, 'DELETE FROM habits WHERE id=?', (habit_id,))
        conn.commit()
    conn.close()
    return redirect('/dashboard')

# ── TOGGLE ────────────────────────────────────────────────
@app.route('/toggle', methods=['POST'])
@login_required
def toggle():
    habit_id = int(request.form.get('habit_id', 0))
    ds   = request.form.get('date','')
    yr   = int(request.form.get('year',  date.today().year))
    mo   = int(request.form.get('month', date.today().month))
    uid  = session['uid']

    try:
        d = datetime.strptime(ds, '%Y-%m-%d').date()
    except:
        return redirect(f'/dashboard?year={yr}&month={mo}')

    today = date.today()
    yest  = today - timedelta(days=1)
    if d < yest or d > today:
        return redirect(f'/dashboard?year={yr}&month={mo}')

    conn = db()
    # Verify habit belongs to user
    h = fetchone(execute(conn, 'SELECT id FROM habits WHERE id=? AND user_id=?', (habit_id, uid)))
    if not h:
        conn.close()
        return redirect(f'/dashboard?year={yr}&month={mo}')

    ex = fetchone(execute(conn, 'SELECT id FROM logs WHERE habit_id=? AND log_date=?', (habit_id, ds)))
    if ex:
        execute(conn, 'DELETE FROM logs WHERE habit_id=? AND log_date=?', (habit_id, ds))
    else:
        execute(conn, 'INSERT INTO logs (habit_id,user_id,log_date) VALUES (?,?,?)', (habit_id, uid, ds))
    conn.commit()
    conn.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

# ── PROFILE ───────────────────────────────────────────────
@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    avatar = request.form.get('avatar','🎯')
    conn = db()
    execute(conn, 'UPDATE users SET avatar=? WHERE id=?', (avatar, session['uid']))
    conn.commit()
    conn.close()
    return redirect('/dashboard')

# ── FRIENDS ───────────────────────────────────────────────
@app.route('/friends/add', methods=['POST'])
@login_required
def add_friend():
    code = request.form.get('code','').strip().upper()
    yr   = request.form.get('year',  date.today().year)
    mo   = request.form.get('month', date.today().month)
    conn = db()
    friend = fetchone(execute(conn, 'SELECT * FROM users WHERE invite_code=?', (code,)))
    if friend and friend['id'] != session['uid']:
        execute(conn, 'INSERT INTO friends (user_id,friend_id) VALUES (?,?) ON CONFLICT DO NOTHING' if USE_PG
                else 'INSERT OR IGNORE INTO friends (user_id,friend_id) VALUES (?,?)',
                (session['uid'], friend['id']))
        execute(conn, 'INSERT INTO friends (user_id,friend_id) VALUES (?,?) ON CONFLICT DO NOTHING' if USE_PG
                else 'INSERT OR IGNORE INTO friends (user_id,friend_id) VALUES (?,?)',
                (friend['id'], session['uid']))
        conn.commit()
    conn.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

@app.route('/friends/remove', methods=['POST'])
@login_required
def remove_friend():
    fid = int(request.form.get('friend_id',0))
    yr  = request.form.get('year',  date.today().year)
    mo  = request.form.get('month', date.today().month)
    conn = db()
    execute(conn, 'DELETE FROM friends WHERE user_id=? AND friend_id=?', (session['uid'], fid))
    execute(conn, 'DELETE FROM friends WHERE user_id=? AND friend_id=?', (fid, session['uid']))
    conn.commit()
    conn.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

# ── REACTIONS ─────────────────────────────────────────────
@app.route('/react', methods=['POST'])
@login_required
def react():
    to_id = int(request.form.get('to_id',0))
    emoji = request.form.get('emoji','🔥')
    yr    = request.form.get('year',  date.today().year)
    mo    = request.form.get('month', date.today().month)
    conn  = db()
    ok = fetchone(execute(conn, 'SELECT id FROM friends WHERE user_id=? AND friend_id=?', (session['uid'], to_id)))
    if ok:
        execute(conn, 'INSERT INTO reactions (from_id,to_id,emoji) VALUES (?,?,?)', (session['uid'], to_id, emoji))
        conn.commit()
    conn.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

# ── LEADERBOARD ───────────────────────────────────────────
@app.route('/leaderboard')
@login_required
def leaderboard():
    uid  = session['uid']
    conn = db()
    me   = fetchone(execute(conn, 'SELECT * FROM users WHERE id=?', (uid,)))
    frs  = fetchall(execute(conn, 'SELECT u.* FROM friends f JOIN users u ON f.friend_id=u.id WHERE f.user_id=?', (uid,)))
    conn.close()

    board = []
    for u in [me] + list(frs):
        habits = get_user_habits(u['id'])
        total_streak = sum(streak(h['id']) for h in habits)
        best = max((best_streak(h['id']) for h in habits), default=0)
        board.append({
            'username': u['username'], 'avatar': u['avatar'] or '🎯',
            'streak': total_streak, 'best': best,
            'habits': len(habits), 'is_me': u['id'] == uid
        })
    board.sort(key=lambda x: x['streak'], reverse=True)
    return render_template('leaderboard.html', board=board)

# ── INIT ──────────────────────────────────────────────────
try:
    init_db()
    print('✅ Database initialized')
except Exception as e:
    print(f'⚠️ DB init warning: {e}')

if __name__ == '__main__':
    print('\n✅ Habit Tracker v5 running!')
    print('👉 Open: http://localhost:5000\n')
    app.run(debug=True, port=5000, use_reloader=False)
