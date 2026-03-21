from flask import Flask, render_template, request, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, random, string, os
from datetime import date, datetime, timedelta
from calendar import monthrange
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'habittracker_xK9mP2nQ7wR4vL6')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, 'habit.db')

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    c = db()
    # Create all tables
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            invite_code TEXT UNIQUE NOT NULL,
            avatar TEXT DEFAULT "🎯",
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            icon TEXT DEFAULT "🎯",
            color TEXT DEFAULT "green",
            position INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER,
            user_id INTEGER NOT NULL,
            log_date TEXT NOT NULL
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
    # Safe migrations - add missing columns
    migrations = [
        'ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT "🎯"',
        'ALTER TABLE logs ADD COLUMN habit_id INTEGER',
        'ALTER TABLE logs ADD COLUMN user_id INTEGER',
    ]
    for m in migrations:
        try:
            c.execute(m)
        except:
            pass
    # Remove old UNIQUE constraint issue by recreating logs if needed
    try:
        c.execute('SELECT habit_id FROM logs LIMIT 1')
    except:
        c.execute('DROP TABLE IF EXISTS logs')
        c.execute('''CREATE TABLE logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER,
            user_id INTEGER NOT NULL,
            log_date TEXT NOT NULL
        )''')
    # For existing users without habits, create a default habit
    users = c.execute('SELECT id FROM users').fetchall()
    for u in users:
        existing = c.execute('SELECT id FROM habits WHERE user_id=?', (u['id'],)).fetchone()
        if not existing:
            c.execute('INSERT INTO habits (user_id, name, icon, color, position) VALUES (?,?,?,?,?)',
                     (u['id'], 'CONSISTENT ON EVERYTHING', '🎯', 'green', 0))
    c.commit()
    c.close()

def make_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=6))
        c = db()
        ex = c.execute('SELECT id FROM users WHERE invite_code=?', (code,)).fetchone()
        c.close()
        if not ex: return code

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'uid' not in session: return redirect('/login')
        return f(*a, **kw)
    return wrap

AVATARS = ['🎯','💪','🏃','📚','🧘','💧','🌱','🔥','⚡','🏆','✨','🎵','🦁','🐯','🦊','🐺','🌟','💎','🚀','🎸']

def get_user_habits(uid):
    c = db()
    habits = c.execute('SELECT * FROM habits WHERE user_id=? ORDER BY position', (uid,)).fetchall()
    c.close()
    return [dict(h) for h in habits]

def get_done(habit_id, year, month):
    c = db()
    rows = c.execute('SELECT log_date FROM logs WHERE habit_id=? AND log_date LIKE ?',
                     (habit_id, f'{year}-{month:02d}-%')).fetchall()
    c.close()
    return set(r['log_date'] for r in rows)

def streak(habit_id):
    c = db()
    rows = c.execute('SELECT log_date FROM logs WHERE habit_id=? ORDER BY log_date DESC', (habit_id,)).fetchall()
    c.close()
    if not rows: return 0
    done = {r['log_date'] for r in rows}
    today = date.today()
    cur = today if today.strftime('%Y-%m-%d') in done else today - timedelta(days=1)
    s = 0
    while cur.strftime('%Y-%m-%d') in done:
        s += 1; cur -= timedelta(days=1)
    return s

def best_streak(habit_id):
    c = db()
    rows = c.execute('SELECT log_date FROM logs WHERE habit_id=? ORDER BY log_date ASC', (habit_id,)).fetchall()
    c.close()
    if not rows: return 0
    dates = sorted(r['log_date'] for r in rows)
    best = cur_s = 1
    for i in range(1, len(dates)):
        d1 = datetime.strptime(dates[i-1], '%Y-%m-%d').date()
        d2 = datetime.strptime(dates[i], '%Y-%m-%d').date()
        cur_s = cur_s+1 if (d2-d1).days==1 else 1
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
    is_current = (year == today.year and month == today.month)
    dots = []
    for day in range(1, days_in_month+1):
        ds = f'{year}-{month:02d}-{day:02d}'
        day_date = date(year, month, day)
        is_today = ds == today_str
        is_future = day_date > today
        cls = 'done' if ds in done else ('today' if is_today else ('future' if is_future else 'missed'))
        clickable = is_mine and is_current and (is_today or ds == yest_str)
        locked = is_mine and not is_future and not is_today and ds != yest_str
        dots.append({'day': day, 'ds': ds, 'cls': cls, 'clickable': clickable, 'locked': locked})
    return dots

def year_overview(habit_id, year):
    today = date.today()
    result = []
    for mo in range(1, 13):
        d, m, _ = month_stats(habit_id, year, mo)
        passed = d + m
        if passed == 0: cls, pct = 'future', 0
        else:
            pct = round((d/passed)*100)
            if year==today.year and mo==today.month: cls='curr'
            elif pct>=60: cls='mostly'
            elif pct>=30: cls='half'
            else: cls='all-red'
        result.append({'mo': mo, 'cls': cls, 'pct': pct, 'd': d, 'm': m})
    return result

# ── PAGES ─────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect('/dashboard' if 'uid' in session else '/login')

@app.route('/login')
def login_page():
    if 'uid' in session: return redirect('/dashboard')
    return render_template('auth.html', mode='login', error=None, avatars=AVATARS)

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
    c = db()
    user = dict(c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
    friend_rows = c.execute('''
        SELECT u.* FROM friends f JOIN users u ON f.friend_id=u.id WHERE f.user_id=?
    ''', (uid,)).fetchall()
    c.close()
    habits = get_user_habits(uid)
    months_list = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    months_full = ['JANUARY','FEBRUARY','MARCH','APRIL','MAY','JUNE','JULY','AUGUST','SEPTEMBER','OCTOBER','NOVEMBER','DECEMBER']
    prev_month = month-1 if month>1 else 12
    prev_year  = year if month>1 else year-1
    next_month = month+1 if month<12 else 1
    next_year  = year if month<12 else year+1
    offset = (date(year, month, 1).weekday() + 1) % 7

    my_habits_data = []
    for h in habits:
        hid = h['id']
        d, m_c, total = month_stats(hid, year, month)
        passed = d + m_c
        pct = round((d/passed)*100) if passed else 0
        my_habits_data.append({
            'habit': h, 'dots': build_dots(hid, year, month, True),
            'done': d, 'missed': m_c, 'total': total, 'pct': pct,
            'streak': streak(hid), 'best': best_streak(hid),
            'year_data': year_overview(hid, year),
        })

    friend_data = []
    for f in friend_rows:
        f = dict(f)
        fhabits = get_user_habits(f['id'])
        fhabits_data = []
        for h in fhabits:
            hid = h['id']
            d, m_c, total = month_stats(hid, year, month)
            passed = d + m_c
            pct = round((d/passed)*100) if passed else 0
            fhabits_data.append({
                'habit': h, 'dots': build_dots(hid, year, month, False),
                'done': d, 'missed': m_c, 'total': total, 'pct': pct,
                'streak': streak(hid), 'best': best_streak(hid),
                'year_data': year_overview(hid, year),
            })
        friend_data.append({'user': f, 'habits_data': fhabits_data})

    return render_template('dashboard.html',
        user=user, year=year, month=month,
        month_name=months_full[month-1],
        months_list=months_list, months_full=months_full,
        offset=offset, my_habits_data=my_habits_data,
        friend_data=friend_data, today_str=today.strftime('%Y-%m-%d'),
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        habit_icons=['🎯','💪','🏃','📚','🧘','💧','🌱','🔥','⚡','🏆','✨','🎵','🥗','😴','✍️','🧠'],
        can_add_habit=len(habits)<3, avatars=AVATARS,
    )

@app.route('/api/register', methods=['POST'])
def do_register():
    username = request.form.get('username','').strip().lower()
    password = request.form.get('password','')
    avatar   = request.form.get('avatar','🎯')
    habit1   = request.form.get('habit1','CONSISTENT ON EVERYTHING').strip().upper() or 'CONSISTENT ON EVERYTHING'
    if len(username)<3: return render_template('auth.html', mode='register', error='Username must be at least 3 characters', avatars=AVATARS)
    if len(password)<6: return render_template('auth.html', mode='register', error='Password must be at least 6 characters', avatars=AVATARS)
    c = db()
    if c.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
        c.close(); return render_template('auth.html', mode='register', error='Username already taken', avatars=AVATARS)
    c.execute('INSERT INTO users (username,password,invite_code,avatar) VALUES (?,?,?,?)',
              (username, generate_password_hash(password), make_code(), avatar))
    c.commit()
    user = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    c.execute('INSERT INTO habits (user_id,name,icon,color,position) VALUES (?,?,?,?,?)',
              (user['id'], habit1, '🎯', 'green', 0))
    c.commit(); c.close()
    session['uid'] = user['id']; session['uname'] = user['username']
    return redirect('/dashboard')

@app.route('/api/login', methods=['POST'])
def do_login():
    username = request.form.get('username','').strip().lower()
    password = request.form.get('password','')
    c = db()
    user = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
    c.close()
    if not user or not check_password_hash(user['password'], password):
        return render_template('auth.html', mode='login', error='Invalid username or password', avatars=AVATARS)
    session['uid'] = user['id']; session['uname'] = user['username']
    return redirect('/dashboard')

@app.route('/logout')
def do_logout():
    session.clear(); return redirect('/login')

@app.route('/habit/add', methods=['POST'])
@login_required
def add_habit():
    uid = session['uid']
    if len(get_user_habits(uid)) >= 3: return redirect('/dashboard')
    name = request.form.get('name','').strip().upper() or 'NEW HABIT'
    icon = request.form.get('icon','🎯')
    c = db()
    c.execute('INSERT INTO habits (user_id,name,icon,color,position) VALUES (?,?,?,?,?)',
              (uid, name, icon, 'green', len(get_user_habits(uid))))
    c.commit(); c.close()
    return redirect('/dashboard')

@app.route('/habit/delete', methods=['POST'])
@login_required
def delete_habit():
    hid = int(request.form.get('habit_id',0))
    uid = session['uid']
    c = db()
    h = c.execute('SELECT * FROM habits WHERE id=? AND user_id=?', (hid, uid)).fetchone()
    if h and len(get_user_habits(uid)) > 1:
        c.execute('DELETE FROM logs WHERE habit_id=?', (hid,))
        c.execute('DELETE FROM habits WHERE id=?', (hid,))
        c.commit()
    c.close(); return redirect('/dashboard')

@app.route('/toggle', methods=['POST'])
@login_required
def toggle():
    hid = int(request.form.get('habit_id',0))
    ds  = request.form.get('date','')
    yr  = int(request.form.get('year',  date.today().year))
    mo  = int(request.form.get('month', date.today().month))
    uid = session['uid']
    try: d = datetime.strptime(ds, '%Y-%m-%d').date()
    except: return redirect(f'/dashboard?year={yr}&month={mo}')
    today = date.today()
    yest  = today - timedelta(days=1)
    if d < yest or d > today: return redirect(f'/dashboard?year={yr}&month={mo}')
    c = db()
    h = c.execute('SELECT id FROM habits WHERE id=? AND user_id=?', (hid, uid)).fetchone()
    if not h: c.close(); return redirect(f'/dashboard?year={yr}&month={mo}')
    ex = c.execute('SELECT id FROM logs WHERE habit_id=? AND log_date=?', (hid, ds)).fetchone()
    if ex: c.execute('DELETE FROM logs WHERE habit_id=? AND log_date=?', (hid, ds))
    else:  c.execute('INSERT INTO logs (habit_id,user_id,log_date) VALUES (?,?,?)', (hid, uid, ds))
    c.commit(); c.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    avatar = request.form.get('avatar','🎯')
    c = db()
    c.execute('UPDATE users SET avatar=? WHERE id=?', (avatar, session['uid']))
    c.commit(); c.close()
    return redirect('/dashboard')

@app.route('/friends/add', methods=['POST'])
@login_required
def add_friend():
    code = request.form.get('code','').strip().upper()
    yr   = request.form.get('year', date.today().year)
    mo   = request.form.get('month', date.today().month)
    c = db()
    friend = c.execute('SELECT * FROM users WHERE invite_code=?', (code,)).fetchone()
    if friend and friend['id'] != session['uid']:
        c.execute('INSERT OR IGNORE INTO friends (user_id,friend_id) VALUES (?,?)', (session['uid'], friend['id']))
        c.execute('INSERT OR IGNORE INTO friends (user_id,friend_id) VALUES (?,?)', (friend['id'], session['uid']))
        c.commit()
    c.close(); return redirect(f'/dashboard?year={yr}&month={mo}')

@app.route('/friends/remove', methods=['POST'])
@login_required
def remove_friend():
    fid = int(request.form.get('friend_id',0))
    yr  = request.form.get('year', date.today().year)
    mo  = request.form.get('month', date.today().month)
    c = db()
    c.execute('DELETE FROM friends WHERE user_id=? AND friend_id=?', (session['uid'], fid))
    c.execute('DELETE FROM friends WHERE user_id=? AND friend_id=?', (fid, session['uid']))
    c.commit(); c.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

@app.route('/react', methods=['POST'])
@login_required
def react():
    to_id = int(request.form.get('to_id',0))
    emoji = request.form.get('emoji','🔥')
    yr    = request.form.get('year', date.today().year)
    mo    = request.form.get('month', date.today().month)
    c = db()
    ok = c.execute('SELECT id FROM friends WHERE user_id=? AND friend_id=?', (session['uid'], to_id)).fetchone()
    if ok:
        c.execute('INSERT INTO reactions (from_id,to_id,emoji) VALUES (?,?,?)', (session['uid'], to_id, emoji))
        c.commit()
    c.close(); return redirect(f'/dashboard?year={yr}&month={mo}')

@app.route('/leaderboard')
@login_required
def leaderboard():
    uid = session['uid']
    c = db()
    me  = dict(c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
    frs = c.execute('SELECT u.* FROM friends f JOIN users u ON f.friend_id=u.id WHERE f.user_id=?', (uid,)).fetchall()
    c.close()
    board = []
    for u in [me] + [dict(f) for f in frs]:
        habits = get_user_habits(u['id'])
        total_streak = sum(streak(h['id']) for h in habits)
        best = max((best_streak(h['id']) for h in habits), default=0)
        board.append({'username': u['username'], 'avatar': u.get('avatar','🎯') or '🎯',
                      'streak': total_streak, 'best': best,
                      'habits': len(habits), 'is_me': u['id']==uid})
    board.sort(key=lambda x: x['streak'], reverse=True)
    return render_template('leaderboard.html', board=board)

try:
    init_db()
    print('✅ Database ready')
except Exception as e:
    print(f'⚠️ DB init: {e}')

if __name__ == '__main__':
    print('\n✅ Running! Open: http://localhost:5000\n')
    app.run(debug=True, port=5000, use_reloader=False)
