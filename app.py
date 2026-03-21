from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, random, string, os
from datetime import date, datetime, timedelta
from calendar import monthrange
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'habittracker_xK9mP2nQ7wR4vL6')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, 'habit.db')

# ── DATABASE ─────────────────────────────────────────────
def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = db()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            invite_code TEXT UNIQUE NOT NULL,
            habit_name TEXT DEFAULT 'CONSISTENT ON EVERYTHING',
            habit_icon TEXT DEFAULT '🎯',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            log_date TEXT NOT NULL,
            UNIQUE(user_id, log_date)
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
    c.commit()
    c.close()

def make_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=6))
        c = db()
        ex = c.execute('SELECT id FROM users WHERE invite_code=?', (code,)).fetchone()
        c.close()
        if not ex:
            return code

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'uid' not in session:
            return redirect('/login')
        return f(*a, **kw)
    return wrap

# ── HELPERS ──────────────────────────────────────────────
def get_done(uid, year, month):
    c = db()
    rows = c.execute('SELECT log_date FROM logs WHERE user_id=? AND log_date LIKE ?',
                     (uid, f'{year}-{month:02d}-%')).fetchall()
    c.close()
    return set(r['log_date'] for r in rows)

def streak(uid):
    c = db()
    rows = c.execute('SELECT log_date FROM logs WHERE user_id=? ORDER BY log_date DESC', (uid,)).fetchall()
    c.close()
    if not rows: return 0
    done = {r['log_date'] for r in rows}
    today = date.today()
    cur = today if today.strftime('%Y-%m-%d') in done else today - timedelta(days=1)
    s = 0
    while cur.strftime('%Y-%m-%d') in done:
        s += 1
        cur -= timedelta(days=1)
    return s

def best_streak(uid):
    c = db()
    rows = c.execute('SELECT log_date FROM logs WHERE user_id=? ORDER BY log_date ASC', (uid,)).fetchall()
    c.close()
    if not rows: return 0
    dates = sorted(r['log_date'] for r in rows)
    best = cur = 1
    for i in range(1, len(dates)):
        d1 = datetime.strptime(dates[i-1], '%Y-%m-%d').date()
        d2 = datetime.strptime(dates[i],   '%Y-%m-%d').date()
        cur = cur + 1 if (d2 - d1).days == 1 else 1
        best = max(best, cur)
    return best

def month_stats(uid, year, month):
    days = monthrange(year, month)[1]
    today = date.today()
    done = get_done(uid, year, month)
    d = m = 0
    for day in range(1, days+1):
        ds = f'{year}-{month:02d}-{day:02d}'
        if date(year, month, day) > today: break
        if ds in done: d += 1
        else: m += 1
    return d, m, days

def year_data(uid, year):
    today = date.today()
    result = []
    for mo in range(1, 13):
        d, m, _ = month_stats(uid, year, mo)
        passed = d + m
        if passed == 0:
            cls, pct = 'future', 0
        else:
            pct = round((d / passed) * 100)
            if year == today.year and mo == today.month:
                cls = 'curr'
            elif pct >= 60: cls = 'mostly'
            elif pct >= 30: cls = 'half'
            else:           cls = 'all-red'
        result.append({'mo': mo, 'cls': cls, 'pct': pct, 'd': d, 'm': m})
    return result

def all_time(uid):
    c = db()
    total = c.execute('SELECT COUNT(*) as n FROM logs WHERE user_id=?', (uid,)).fetchone()['n']
    joined = c.execute('SELECT created_at FROM users WHERE id=?', (uid,)).fetchone()['created_at']
    c.close()
    days = max((date.today() - datetime.strptime(joined[:10], '%Y-%m-%d').date()).days + 1, 1)
    rate = min(round((total / days) * 100), 100)
    return total, days, rate

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
    return render_template('auth.html', mode='register', error=None)

@app.route('/dashboard')
@login_required
def dashboard():
    today = date.today()
    year  = int(request.args.get('year',  today.year))
    month = int(request.args.get('month', today.month))

    c = db()
    user = c.execute('SELECT * FROM users WHERE id=?', (session['uid'],)).fetchone()
    friend_rows = c.execute('''
        SELECT u.* FROM friends f JOIN users u ON f.friend_id=u.id WHERE f.user_id=?
    ''', (session['uid'],)).fetchall()
    c.close()

    uid = session['uid']
    done = get_done(uid, year, month)
    d, m, total_days = month_stats(uid, year, month)
    passed = d + m
    pct = round((d / passed) * 100) if passed else 0
    total_done, days_joined, rate = all_time(uid)

    # Build dot data for template
    first_day = date(year, month, 1).weekday()  # Monday=0
    # Convert to Sunday=0 format
    first_day = (first_day + 1) % 7

    days_in_month = monthrange(year, month)[1]
    dots = []
    for day in range(1, days_in_month + 1):
        ds = f'{year}-{month:02d}-{day:02d}'
        day_date = date(year, month, day)
        is_today = ds == today.strftime('%Y-%m-%d')
        is_yest  = ds == (today - timedelta(days=1)).strftime('%Y-%m-%d')
        is_future = day_date > today
        is_done   = ds in done
        is_current_month = (year == today.year and month == today.month)

        if is_done:
            cls = 'done'
        elif is_today:
            cls = 'today'
        elif is_future:
            cls = 'future'
        else:
            cls = 'missed'

        clickable = is_current_month and (is_today or is_yest)
        locked    = is_current_month and not is_future and not is_today and not is_yest

        dots.append({
            'day': day, 'ds': ds, 'cls': cls,
            'clickable': clickable, 'locked': locked
        })

    # Friend data
    friend_data = []
    for f in friend_rows:
        fd, fm, ftotal = month_stats(f['id'], year, month)
        fp = d + m
        fdone = get_done(f['id'], year, month)
        fdots = []
        ffd = (date(year, month, 1).weekday() + 1) % 7
        for day in range(1, days_in_month + 1):
            ds = f'{year}-{month:02d}-{day:02d}'
            day_date = date(year, month, day)
            is_today = ds == today.strftime('%Y-%m-%d')
            cls = 'done' if ds in fdone else ('today' if is_today else ('future' if day_date > today else 'missed'))
            fdots.append({'day': day, 'ds': ds, 'cls': cls})
        fdp, fdm, _ = month_stats(f['id'], year, month)
        fp2 = fdp + fdm
        fpct = round((fdp / fp2) * 100) if fp2 else 0
        ft, _, fr = all_time(f['id'])
        friend_data.append({
            'user': f,
            'dots': fdots,
            'offset': ffd,
            'done': fdp, 'missed': fdm,
            'pct': fpct,
            'streak': streak(f['id']),
            'best': best_streak(f['id']),
            'total': ft,
            'rate': fr,
            'year_data': year_data(f['id'], year),
        })

    months_list = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    prev_month = month - 1 if month > 1 else 12
    prev_year  = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year  = year if month < 12 else year + 1

    return render_template('dashboard.html',
        user=user,
        year=year, month=month,
        month_name=months_list[month-1],
        dots=dots,
        offset=first_day,
        done_count=d, missed_count=m, total_days=total_days,
        pct=pct,
        my_streak=streak(uid),
        my_best=best_streak(uid),
        total_done=total_done,
        rate=rate,
        year_data=year_data(uid, year),
        friend_data=friend_data,
        today_str=today.strftime('%Y-%m-%d'),
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        months_list=months_list,
    )

# ── AUTH ──────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def do_register():
    username = request.form.get('username','').strip().lower()
    password = request.form.get('password','')
    habit    = request.form.get('habit','CONSISTENT ON EVERYTHING').strip().upper() or 'CONSISTENT ON EVERYTHING'
    if len(username) < 3:
        return render_template('auth.html', mode='register', error='Username must be at least 3 characters')
    if len(password) < 6:
        return render_template('auth.html', mode='register', error='Password must be at least 6 characters')
    c = db()
    if c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
        c.close()
        return render_template('auth.html', mode='register', error='Username already taken')
    c.execute('INSERT INTO users (username,password,invite_code,habit_name) VALUES (?,?,?,?)',
              (username, generate_password_hash(password), make_code(), habit))
    c.commit()
    user = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    c.close()
    session['uid'] = user['id']
    session['uname'] = user['username']
    return redirect('/dashboard')

@app.route('/api/login', methods=['POST'])
def do_login():
    username = request.form.get('username','').strip().lower()
    password = request.form.get('password','')
    c = db()
    user = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    c.close()
    if not user or not check_password_hash(user['password'], password):
        return render_template('auth.html', mode='login', error='Invalid username or password')
    session['uid'] = user['id']
    session['uname'] = user['username']
    return redirect('/dashboard')

@app.route('/logout')
def do_logout():
    session.clear()
    return redirect('/login')

# ── TOGGLE ────────────────────────────────────────────────
@app.route('/toggle', methods=['POST'])
@login_required
def toggle():
    ds = request.form.get('date','')
    yr = int(request.form.get('year',  date.today().year))
    mo = int(request.form.get('month', date.today().month))
    try:
        d = datetime.strptime(ds, '%Y-%m-%d').date()
    except:
        return redirect(f'/dashboard?year={yr}&month={mo}')
    today = date.today()
    yest  = today - timedelta(days=1)
    if d < yest or d > today:
        return redirect(f'/dashboard?year={yr}&month={mo}')
    c = db()
    ex = c.execute('SELECT id FROM logs WHERE user_id=? AND log_date=?', (session['uid'], ds)).fetchone()
    if ex:
        c.execute('DELETE FROM logs WHERE user_id=? AND log_date=?', (session['uid'], ds))
    else:
        c.execute('INSERT INTO logs (user_id, log_date) VALUES (?,?)', (session['uid'], ds))
    c.commit()
    c.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

# ── FRIENDS ───────────────────────────────────────────────
@app.route('/friends/add', methods=['POST'])
@login_required
def add_friend():
    code = request.form.get('code','').strip().upper()
    yr   = request.form.get('year',  date.today().year)
    mo   = request.form.get('month', date.today().month)
    c = db()
    friend = c.execute('SELECT * FROM users WHERE invite_code=?', (code,)).fetchone()
    if friend and friend['id'] != session['uid']:
        c.execute('INSERT OR IGNORE INTO friends (user_id, friend_id) VALUES (?,?)', (session['uid'], friend['id']))
        c.execute('INSERT OR IGNORE INTO friends (user_id, friend_id) VALUES (?,?)', (friend['id'], session['uid']))
        c.commit()
    c.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

@app.route('/friends/remove', methods=['POST'])
@login_required
def remove_friend():
    fid = int(request.form.get('friend_id',0))
    yr  = request.form.get('year',  date.today().year)
    mo  = request.form.get('month', date.today().month)
    c = db()
    c.execute('DELETE FROM friends WHERE user_id=? AND friend_id=?', (session['uid'], fid))
    c.execute('DELETE FROM friends WHERE user_id=? AND friend_id=?', (fid, session['uid']))
    c.commit()
    c.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

# ── REACTIONS ─────────────────────────────────────────────
@app.route('/react', methods=['POST'])
@login_required
def react():
    to_id = int(request.form.get('to_id', 0))
    emoji = request.form.get('emoji', '🔥')
    yr    = request.form.get('year',  date.today().year)
    mo    = request.form.get('month', date.today().month)
    c = db()
    ok = c.execute('SELECT id FROM friends WHERE user_id=? AND friend_id=?', (session['uid'], to_id)).fetchone()
    if ok:
        c.execute('INSERT INTO reactions (from_id, to_id, emoji) VALUES (?,?,?)', (session['uid'], to_id, emoji))
        c.commit()
    c.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

# ── LEADERBOARD ───────────────────────────────────────────
@app.route('/leaderboard')
@login_required
def leaderboard():
    c = db()
    me = c.execute('SELECT * FROM users WHERE id=?', (session['uid'],)).fetchone()
    frs = c.execute('SELECT u.* FROM friends f JOIN users u ON f.friend_id=u.id WHERE f.user_id=?',
                    (session['uid'],)).fetchall()
    c.close()
    board = [{'username': me['username'], 'habit_name': me['habit_name'],
              'icon': me['habit_icon'] or '🎯', 'streak': streak(me['id']),
              'best': best_streak(me['id']), 'is_me': True}]
    for f in frs:
        board.append({'username': f['username'], 'habit_name': f['habit_name'],
                      'icon': f['habit_icon'] or '🎯', 'streak': streak(f['id']),
                      'best': best_streak(f['id']), 'is_me': False})
    board.sort(key=lambda x: x['streak'], reverse=True)
    return render_template('leaderboard.html', board=board)


init_db()
if __name__ == '__main__':
    init_db()
    print('\n✅ Habit Tracker running!')
    print('👉 Open: http://localhost:5000\n')
    app.run(debug=True, port=5000, use_reloader=False)

# ── This runs on Railway/Render via gunicorn ──
init_db()
