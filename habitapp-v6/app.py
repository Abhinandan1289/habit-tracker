from flask import Flask, render_template, request, session, redirect, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
import os, random, string, smtplib, json, time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date, datetime, timedelta
from calendar import monthrange
from functools import wraps
from collections import defaultdict

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'habittracker_xK9mP2nQ7wR4vL6jH1')

# ── CONFIG ────────────────────────────────────────────────
DATABASE_URL    = os.environ.get('DATABASE_URL', None)
USE_PG          = DATABASE_URL is not None
ADMIN_PASSWORD  = os.environ.get('ADMIN_PASSWORD', '')
GMAIL_USER      = os.environ.get('GMAIL_USER', '')
GMAIL_PASS      = os.environ.get('GMAIL_PASS', '')
STRIPE_PK       = os.environ.get('STRIPE_PK', '')
STRIPE_SK       = os.environ.get('STRIPE_SK', '')
STRIPE_PRICE_ID = os.environ.get('STRIPE_PRICE_ID', '')
APP_URL         = os.environ.get('APP_URL', 'https://habit-tracker-n1fm.onrender.com')
PREMIUM_PRICE   = '₹99'

if USE_PG:
    import psycopg2, psycopg2.extras
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# ── BRUTE FORCE PROTECTION ───────────────────────────────
failed_attempts = defaultdict(list)  # ip -> [timestamps]
blocked_ips     = {}  # ip -> unblock_time
admin_otp_store = {}  # session_id -> {otp, expires}

def get_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()

def is_blocked(ip):
    if ip in blocked_ips:
        if time.time() < blocked_ips[ip]:
            return True
        else:
            del blocked_ips[ip]
    return False

def record_failed(ip):
    now = time.time()
    failed_attempts[ip] = [t for t in failed_attempts[ip] if now - t < 600]
    failed_attempts[ip].append(now)
    if len(failed_attempts[ip]) >= 5:
        blocked_ips[ip] = time.time() + 1800  # block 30 mins
        return True
    return False

def clear_failed(ip):
    failed_attempts.pop(ip, None)

# ── DATABASE ─────────────────────────────────────────────
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
    return sql.replace('?', '%s') if USE_PG else sql

def fetchone(cur):
    row = cur.fetchone()
    if row is None: return None
    return dict(row) if USE_PG else row

def fetchall(cur):
    rows = cur.fetchall()
    return [dict(r) for r in rows] if USE_PG else rows

def execute(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(qmark(sql), params)
    return cur

def init_db():
    conn = db()
    cur = conn.cursor()
    if not USE_PG:
        cur.executescript('''
            DROP TABLE IF EXISTS tasks;
            DROP TABLE IF EXISTS reactions;
            DROP TABLE IF EXISTS logs;
            DROP TABLE IF EXISTS friends;
            DROP TABLE IF EXISTS habits;
            DROP TABLE IF EXISTS users;
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                invite_code TEXT UNIQUE NOT NULL,
                avatar TEXT DEFAULT "🎯",
                email TEXT DEFAULT NULL,
                whatsapp TEXT DEFAULT NULL,
                notify_email INTEGER DEFAULT 0,
                notify_whatsapp INTEGER DEFAULT 0,
                is_premium INTEGER DEFAULT 0,
                stripe_customer_id TEXT DEFAULT NULL,
                theme TEXT DEFAULT "dark",
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE habits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                icon TEXT DEFAULT "🎯",
                color TEXT DEFAULT "green",
                position INTEGER DEFAULT 0
            );
            CREATE TABLE logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                habit_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                log_date TEXT NOT NULL,
                UNIQUE(habit_id, log_date)
            );
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                done INTEGER DEFAULT 0,
                priority TEXT DEFAULT 'normal',
                due_date TEXT DEFAULT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE friends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                friend_id INTEGER NOT NULL,
                UNIQUE(user_id, friend_id)
            );
            CREATE TABLE reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_id INTEGER NOT NULL,
                to_id INTEGER NOT NULL,
                emoji TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')
    else:
        cur.execute('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, invite_code TEXT UNIQUE NOT NULL,
            avatar TEXT DEFAULT '🎯', email TEXT DEFAULT NULL,
            whatsapp TEXT DEFAULT NULL, notify_email INTEGER DEFAULT 0,
            notify_whatsapp INTEGER DEFAULT 0, is_premium INTEGER DEFAULT 0,
            stripe_customer_id TEXT DEFAULT NULL, theme TEXT DEFAULT 'dark',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS habits (
            id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL,
            name TEXT NOT NULL, icon TEXT DEFAULT '🎯',
            color TEXT DEFAULT 'green', position INTEGER DEFAULT 0)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY, habit_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, log_date TEXT NOT NULL,
            UNIQUE(habit_id, log_date))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL,
            title TEXT NOT NULL, done INTEGER DEFAULT 0,
            priority TEXT DEFAULT 'normal', due_date TEXT DEFAULT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS friends (
            id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL,
            friend_id INTEGER NOT NULL, UNIQUE(user_id, friend_id))''')
        cur.execute('''CREATE TABLE IF NOT EXISTS reactions (
            id SERIAL PRIMARY KEY, from_id INTEGER NOT NULL,
            to_id INTEGER NOT NULL, emoji TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

# ── HELPERS ──────────────────────────────────────────────
AVATARS = ['🎯','💪','🏃','📚','🧘','💧','🌱','🔥','⚡','🏆','✨','🎵','🦁','🐯','🦊','🌟','💎','🚀','🎸','🧠']
HABIT_ICONS = ['🎯','💪','🏃','📚','🧘','💧','🌱','🔥','⚡','🏆','✨','🎵','🥗','😴','✍️','🧠']

def make_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=6))
        c = db()
        ex = fetchone(execute(c, 'SELECT id FROM users WHERE invite_code=?', (code,)))
        c.close()
        if not ex: return code

def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if 'uid' not in session: return redirect('/login')
        return f(*a, **kw)
    return wrap

def get_user_habits(uid):
    c = db()
    rows = fetchall(execute(c, 'SELECT * FROM habits WHERE user_id=? ORDER BY position', (uid,)))
    c.close()
    return rows

def get_done(habit_id, year, month):
    c = db()
    rows = fetchall(execute(c, 'SELECT log_date FROM logs WHERE habit_id=? AND log_date LIKE ?',
                            (habit_id, f'{year}-{month:02d}-%')))
    c.close()
    return set(r['log_date'] for r in rows)

def streak(habit_id):
    c = db()
    rows = fetchall(execute(c, 'SELECT log_date FROM logs WHERE habit_id=? ORDER BY log_date DESC', (habit_id,)))
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
    rows = fetchall(execute(c, 'SELECT log_date FROM logs WHERE habit_id=? ORDER BY log_date ASC', (habit_id,)))
    c.close()
    if not rows: return 0
    dates = sorted(r['log_date'] for r in rows)
    best = cur = 1
    for i in range(1, len(dates)):
        d1 = datetime.strptime(dates[i-1], '%Y-%m-%d').date()
        d2 = datetime.strptime(dates[i], '%Y-%m-%d').date()
        cur = cur + 1 if (d2-d1).days == 1 else 1
        best = max(best, cur)
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
    yest_str  = (today - timedelta(days=1)).strftime('%Y-%m-%d')
    done = get_done(habit_id, year, month)
    dim  = monthrange(year, month)[1]
    is_current = (year == today.year and month == today.month)
    dots = []
    for day in range(1, dim+1):
        ds = f'{year}-{month:02d}-{day:02d}'
        dd = date(year, month, day)
        is_today  = ds == today_str
        is_future = dd > today
        cls = 'done' if ds in done else ('today' if is_today else ('future' if is_future else 'missed'))
        clickable = is_mine and is_current and (is_today or ds == yest_str)
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
            pct = round((d/passed)*100)
            if year == today.year and mo == today.month: cls = 'curr'
            elif pct >= 60: cls = 'mostly'
            elif pct >= 30: cls = 'half'
            else:           cls = 'all-red'
        result.append({'mo': mo, 'cls': cls, 'pct': pct, 'd': d, 'm': m})
    return result

# ── EMAIL ─────────────────────────────────────────────────
def send_email(to_email, subject, body_html):
    if not GMAIL_USER or not GMAIL_PASS:
        print(f'Email not configured. Would send to {to_email}: {subject}')
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = f'Habit Tracker <{GMAIL_USER}>'
        msg['To']      = to_email
        msg.attach(MIMEText(body_html, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASS)
            smtp.sendmail(GMAIL_USER, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f'Email error: {e}')
        return False

def send_whatsapp(phone, message):
    """Send WhatsApp via CallMeBot"""
    try:
        import urllib.request, urllib.parse
        phone = phone.replace('+','').replace(' ','')
        url = f'https://api.callmebot.com/whatsapp.php?phone={phone}&text={urllib.parse.quote(message)}&apikey=YOUR_API_KEY'
        urllib.request.urlopen(url, timeout=5)
        return True
    except Exception as e:
        print(f'WhatsApp error: {e}')
        return False

# ── PAGES ─────────────────────────────────────────────────
@app.route('/')
def index():
    if 'uid' in session: return redirect('/dashboard')
    return render_template('landing.html',
        premium_price=PREMIUM_PRICE,
        stripe_pk=STRIPE_PK)

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
    conn  = db()
    conn_u = db()
    user  = dict(fetchone(execute(conn_u, 'SELECT * FROM users WHERE id=?', (uid,))))
    conn_u.close()
    conn = db()
    friends = fetchall(execute(conn, '''
        SELECT u.* FROM friends f JOIN users u ON f.friend_id=u.id WHERE f.user_id=?
    ''', (uid,)))
    conn.close()

    habits = get_user_habits(uid)
    months_list = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC']
    months_full = ['JANUARY','FEBRUARY','MARCH','APRIL','MAY','JUNE','JULY','AUGUST','SEPTEMBER','OCTOBER','NOVEMBER','DECEMBER']
    offset = (date(year, month, 1).weekday() + 1) % 7
    prev_month = month-1 if month>1 else 12
    prev_year  = year   if month>1 else year-1
    next_month = month+1 if month<12 else 1
    next_year  = year   if month<12 else year+1
    is_premium = bool(user.get('is_premium') or user['is_premium'])
    max_habits = 3 if is_premium else 1
    max_friends = 999 if is_premium else 3

    my_habits_data = []
    for h in habits:
        hid = h['id']
        d, mc, total = month_stats(hid, year, month)
        passed = d + mc
        pct = round((d/passed)*100) if passed else 0
        my_habits_data.append({
            'habit': h, 'dots': build_dots(hid, year, month, True),
            'done': d, 'missed': mc, 'total': total, 'pct': pct,
            'streak': streak(hid), 'best': best_streak(hid),
            'year_data': year_overview(hid, year),
        })

    friend_data = []
    for f in friends:
        fhabits = get_user_habits(f['id'])
        fhdata  = []
        for h in fhabits:
            hid = h['id']
            d, mc, total = month_stats(hid, year, month)
            passed = d + mc
            pct = round((d/passed)*100) if passed else 0
            fhdata.append({
                'habit': h, 'dots': build_dots(hid, year, month, False),
                'done': d, 'missed': mc, 'total': total, 'pct': pct,
                'streak': streak(hid), 'best': best_streak(hid),
                'year_data': year_overview(hid, year),
            })
        friend_data.append({'user': f, 'habits_data': fhdata})

    theme = user.get('theme', 'dark') or 'dark'

    return render_template('dashboard.html',
        user=user, year=year, month=month,
        month_name=months_full[month-1],
        months_list=months_list, months_full=months_full,
        offset=offset, my_habits_data=my_habits_data,
        friend_data=friend_data,
        today_str=today.strftime('%Y-%m-%d'),
        prev_year=prev_year, prev_month=prev_month,
        next_year=next_year, next_month=next_month,
        habit_icons=HABIT_ICONS, avatars=AVATARS,
        max_habits=max_habits, max_friends=max_friends,
        can_add_habit=len(habits) < max_habits,
        is_premium=is_premium,
        stripe_pk=STRIPE_PK,
        premium_price=PREMIUM_PRICE,
        theme=theme,
    )

# ── PUBLIC PROFILE ────────────────────────────────────────
@app.route('/u/<username>')
def public_profile(username):
    conn = db()
    user = fetchone(execute(conn, 'SELECT id,username,avatar,created_at FROM users WHERE username=?', (username.lower(),)))
    conn.close()
    if not user: return render_template('404.html'), 404
    habits = get_user_habits(user['id'])
    profile_data = []
    for h in habits:
        s = streak(h['id'])
        b = best_streak(h['id'])
        today = date.today()
        d, m, total = month_stats(h['id'], today.year, today.month)
        passed = d + m
        pct = round((d/passed)*100) if passed else 0
        profile_data.append({'habit': h, 'streak': s, 'best': b, 'pct': pct, 'done': d})
    total_streak = sum(p['streak'] for p in profile_data)
    return render_template('profile.html', user=user, profile_data=profile_data, total_streak=total_streak)

# ── AUTH ──────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def do_register():
    username = request.form.get('username','').strip().lower()
    password = request.form.get('password','')
    avatar   = request.form.get('avatar','🎯')
    habit1   = request.form.get('habit1','CONSISTENT ON EVERYTHING').strip().upper() or 'CONSISTENT ON EVERYTHING'
    email    = request.form.get('email','').strip().lower()

    if len(username) < 3:
        return render_template('auth.html', mode='register', error='Username must be at least 3 characters', avatars=AVATARS)
    if len(password) < 6:
        return render_template('auth.html', mode='register', error='Password must be at least 6 characters', avatars=AVATARS)

    conn = db()
    if fetchone(execute(conn, 'SELECT id FROM users WHERE username=?', (username,))):
        conn.close()
        return render_template('auth.html', mode='register', error='Username already taken', avatars=AVATARS)

    execute(conn, 'INSERT INTO users (username,password,invite_code,avatar,email) VALUES (?,?,?,?,?)',
            (username, generate_password_hash(password), make_code(), avatar, email or None))
    conn.commit()
    user = fetchone(execute(conn, 'SELECT * FROM users WHERE username=?', (username,)))
    execute(conn, 'INSERT INTO habits (user_id,name,icon,color,position) VALUES (?,?,?,?,?)',
            (user['id'], habit1, '🎯', 'green', 0))
    conn.commit()
    conn.close()

    session['uid']   = user['id']
    session['uname'] = user['username']

    # Welcome email
    if email and GMAIL_USER:
        send_email(email, '🎯 Welcome to Habit Tracker!',
            f'<h2>Welcome {username}!</h2><p>Your habit tracker is ready. Start tracking: <a href="{APP_URL}">{APP_URL}</a></p>')

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
    session['uid']   = user['id']
    session['uname'] = user['username']
    return redirect('/dashboard')

@app.route('/logout')
def do_logout():
    session.clear()
    return redirect('/login')

# ── SETTINGS ──────────────────────────────────────────────
@app.route('/settings', methods=['GET','POST'])
@login_required
def settings():
    uid  = session['uid']
    conn = db()
    user = fetchone(execute(conn, 'SELECT * FROM users WHERE id=?', (uid,)))
    conn.close()
    if request.method == 'POST':
        avatar    = request.form.get('avatar', user['avatar'])
        email     = request.form.get('email','').strip().lower()
        whatsapp  = request.form.get('whatsapp','').strip()
        notify_e  = 1 if request.form.get('notify_email') else 0
        notify_w  = 1 if request.form.get('notify_whatsapp') else 0
        theme     = request.form.get('theme', 'dark')
        conn = db()
        execute(conn, '''UPDATE users SET avatar=?,email=?,whatsapp=?,
            notify_email=?,notify_whatsapp=?,theme=? WHERE id=?''',
            (avatar, email or None, whatsapp or None, notify_e, notify_w, theme, uid))
        conn.commit()
        conn.close()
        return redirect('/dashboard')
    return render_template('settings.html', user=user, avatars=AVATARS)

# ── THEME TOGGLE ──────────────────────────────────────────
@app.route('/theme/toggle', methods=['POST'])
@login_required
def toggle_theme():
    uid  = session['uid']
    conn = db()
    user = fetchone(execute(conn, 'SELECT theme FROM users WHERE id=?', (uid,)))
    new_theme = 'light' if (user.get('theme') or 'dark') == 'dark' else 'dark'
    execute(conn, 'UPDATE users SET theme=? WHERE id=?', (new_theme, uid))
    conn.commit()
    conn.close()
    return redirect(request.referrer or '/dashboard')

# ── HABIT MANAGEMENT ──────────────────────────────────────
@app.route('/habit/add', methods=['POST'])
@login_required
def add_habit():
    uid    = session['uid']
    habits = get_user_habits(uid)
    conn   = db()
    user   = fetchone(execute(conn, 'SELECT is_premium FROM users WHERE id=?', (uid,)))
    conn.close()
    max_h  = 3 if user.get('is_premium') else 1
    if len(habits) >= max_h:
        return redirect('/dashboard')
    name = request.form.get('name','').strip().upper() or 'NEW HABIT'
    icon = request.form.get('icon','🎯')
    conn = db()
    execute(conn, 'INSERT INTO habits (user_id,name,icon,color,position) VALUES (?,?,?,?,?)',
            (uid, name, icon, 'green', len(habits)))
    conn.commit()
    conn.close()
    return redirect('/dashboard')

@app.route('/habit/delete', methods=['POST'])
@login_required
def delete_habit():
    hid = int(request.form.get('habit_id',0))
    uid = session['uid']
    conn = db()
    h = fetchone(execute(conn, 'SELECT * FROM habits WHERE id=? AND user_id=?', (hid, uid)))
    if h:
        execute(conn, 'DELETE FROM logs WHERE habit_id=?', (hid,))
        execute(conn, 'DELETE FROM habits WHERE id=?', (hid,))
        conn.commit()
    conn.close()
    return redirect('/dashboard')

# ── TOGGLE ────────────────────────────────────────────────
@app.route('/toggle', methods=['POST'])
@login_required
def toggle():
    hid  = int(request.form.get('habit_id',0))
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
    h = fetchone(execute(conn, 'SELECT id FROM habits WHERE id=? AND user_id=?', (hid, uid)))
    if not h:
        conn.close()
        return redirect(f'/dashboard?year={yr}&month={mo}')
    ex = fetchone(execute(conn, 'SELECT id FROM logs WHERE habit_id=? AND log_date=?', (hid, ds)))
    if ex:
        execute(conn, 'DELETE FROM logs WHERE habit_id=? AND log_date=?', (hid, ds))
    else:
        execute(conn, 'INSERT INTO logs (habit_id,user_id,log_date) VALUES (?,?,?)', (hid, uid, ds))
    conn.commit()
    conn.close()
    return redirect(f'/dashboard?year={yr}&month={mo}')

# ── FRIENDS ───────────────────────────────────────────────
@app.route('/friends/add', methods=['POST'])
@login_required
def add_friend():
    code = request.form.get('code','').strip().upper()
    yr   = request.form.get('year',  date.today().year)
    mo   = request.form.get('month', date.today().month)
    uid  = session['uid']
    conn = db()
    user = fetchone(execute(conn, 'SELECT is_premium FROM users WHERE id=?', (uid,)))
    cur_friends = len(fetchall(execute(conn, 'SELECT id FROM friends WHERE user_id=?', (uid,))))
    max_f = 999 if user.get('is_premium') else 3
    if cur_friends >= max_f:
        conn.close()
        return redirect(f'/dashboard?year={yr}&month={mo}')
    friend = fetchone(execute(conn, 'SELECT * FROM users WHERE invite_code=?', (code,)))
    if friend and friend['id'] != uid:
        execute(conn, 'INSERT OR IGNORE INTO friends (user_id,friend_id) VALUES (?,?)' if not USE_PG
                else 'INSERT INTO friends (user_id,friend_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                (uid, friend['id']))
        execute(conn, 'INSERT OR IGNORE INTO friends (user_id,friend_id) VALUES (?,?)' if not USE_PG
                else 'INSERT INTO friends (user_id,friend_id) VALUES (%s,%s) ON CONFLICT DO NOTHING',
                (friend['id'], uid))
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
        ts = sum(streak(h['id']) for h in habits)
        b  = max((best_streak(h['id']) for h in habits), default=0)
        board.append({'username': u['username'], 'avatar': u['avatar'] or '🎯',
                      'streak': ts, 'best': b, 'habits': len(habits), 'is_me': u['id']==uid})
    board.sort(key=lambda x: x['streak'], reverse=True)
    return render_template('leaderboard.html', board=board)

# ── STRIPE PAYMENTS ───────────────────────────────────────
@app.route('/upgrade')
@login_required
def upgrade_page():
    return render_template('upgrade.html', stripe_pk=STRIPE_PK, premium_price=PREMIUM_PRICE)

@app.route('/create-checkout', methods=['POST'])
@login_required
def create_checkout():
    if not STRIPE_SK:
        return jsonify({'error': 'Payments not configured'}), 400
    try:
        import stripe
        stripe.api_key = STRIPE_SK
        checkout = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price': STRIPE_PRICE_ID, 'quantity': 1}],
            mode='subscription',
            success_url=APP_URL+'/payment/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=APP_URL+'/dashboard',
            client_reference_id=str(session['uid']),
        )
        return jsonify({'url': checkout.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/payment/success')
@login_required
def payment_success():
    conn = db()
    execute(conn, 'UPDATE users SET is_premium=1 WHERE id=?', (session['uid'],))
    conn.commit()
    conn.close()
    return render_template('payment_success.html')

# ── SEND REMINDERS (call this via cron) ───────────────────
@app.route('/api/send-reminders', methods=['POST'])
def send_reminders():
    secret = request.headers.get('X-Secret','')
    if secret != os.environ.get('CRON_SECRET',''):
        return jsonify({'error':'Unauthorized'}), 401
    conn = db()
    users = fetchall(execute(conn, 'SELECT * FROM users WHERE notify_email=1 OR notify_whatsapp=1'))
    conn.close()
    sent = 0
    for u in users:
        habits = get_user_habits(u['id'])
        today_str = date.today().strftime('%Y-%m-%d')
        unchecked = []
        for h in habits:
            done = get_done(h['id'], date.today().year, date.today().month)
            if today_str not in done:
                unchecked.append(h['name'])
        if unchecked:
            msg = f"🎯 Don't forget to check in today!\nPending: {', '.join(unchecked)}\n{APP_URL}"
            if u.get('notify_email') and u.get('email'):
                send_email(u['email'], "🔥 Daily Habit Reminder", f"<p>{msg.replace(chr(10),'<br>')}</p>")
                sent += 1
            if u.get('notify_whatsapp') and u.get('whatsapp'):
                send_whatsapp(u['whatsapp'], msg)
                sent += 1
    return jsonify({'sent': sent})

# ── TASKS ────────────────────────────────────────────────
@app.route('/tasks')
@login_required
def tasks_page():
    uid = session['uid']
    conn = db()
    user = dict(fetchone(execute(conn, 'SELECT * FROM users WHERE id=?', (uid,))))
    if not user.get('is_premium'):
        conn.close()
        return redirect('/upgrade')
    tasks = fetchall(execute(conn, 'SELECT * FROM tasks WHERE user_id=? ORDER BY done ASC, priority DESC, created_at DESC', (uid,)))
    conn.close()
    done_count = sum(1 for t in tasks if t['done'])
    return render_template('tasks.html', user=user, tasks=tasks,
        done_count=done_count, total=len(tasks), max_tasks=10)

@app.route('/tasks/add', methods=['POST'])
@login_required
def add_task():
    uid = session['uid']
    conn = db()
    user = dict(fetchone(execute(conn, 'SELECT is_premium FROM users WHERE id=?', (uid,))))
    task_count = fetchone(execute(conn, 'SELECT COUNT(*) as c FROM tasks WHERE user_id=?', (uid,)))['c']
    if not user.get('is_premium') or task_count >= 10:
        conn.close()
        return redirect('/tasks')
    title    = request.form.get('title','').strip()
    priority = request.form.get('priority','normal')
    due_date = request.form.get('due_date','') or None
    if title:
        execute(conn, 'INSERT INTO tasks (user_id,title,priority,due_date) VALUES (?,?,?,?)',
                (uid, title, priority, due_date))
        conn.commit()
    conn.close()
    return redirect('/tasks')

@app.route('/tasks/toggle', methods=['POST'])
@login_required
def toggle_task():
    uid = session['uid']
    tid = int(request.form.get('task_id',0))
    conn = db()
    t = fetchone(execute(conn, 'SELECT * FROM tasks WHERE id=? AND user_id=?', (tid, uid)))
    if t:
        new_done = 0 if t['done'] else 1
        execute(conn, 'UPDATE tasks SET done=? WHERE id=?', (new_done, tid))
        conn.commit()
    conn.close()
    return redirect('/tasks')

@app.route('/tasks/delete', methods=['POST'])
@login_required
def delete_task():
    uid = session['uid']
    tid = int(request.form.get('task_id',0))
    conn = db()
    execute(conn, 'DELETE FROM tasks WHERE id=? AND user_id=?', (tid, uid))
    conn.commit()
    conn.close()
    return redirect('/tasks')

@app.route('/tasks/clear-done', methods=['POST'])
@login_required
def clear_done_tasks():
    uid = session['uid']
    conn = db()
    execute(conn, 'DELETE FROM tasks WHERE user_id=? AND done=1', (uid,))
    conn.commit()
    conn.close()
    return redirect('/tasks')

# ── ADMIN ─────────────────────────────────────────────────
ADMIN_PASSWORD_FINAL = os.environ.get('ADMIN_PASSWORD', '')

@app.route('/admin')
def admin_login():
    if session.get('is_admin'): return redirect('/admin/dashboard')
    return render_template('admin_login.html', error=None)

@app.route('/admin/login', methods=['POST'])
def admin_do_login():
    ip  = get_ip()
    pwd = request.form.get('password','')
    if is_blocked(ip):
        mins = int((blocked_ips[ip] - time.time()) / 60) + 1
        return render_template('admin_login.html', error=f'Too many attempts. Blocked for {mins} more minutes.')
    if pwd == ADMIN_PASSWORD_FINAL or (not ADMIN_PASSWORD_FINAL and pwd == 'admin1289'):
        clear_failed(ip)
        # Send OTP if email configured
        if GMAIL_USER and os.environ.get('ADMIN_EMAIL'):
            otp = str(random.randint(100000, 999999))
            sid = session.get('_id', os.urandom(16).hex())
            session['_id'] = sid
            admin_otp_store[sid] = {'otp': otp, 'expires': time.time() + 300}
            send_email(os.environ.get('ADMIN_EMAIL'), '🔐 Admin OTP',
                f'<h2>Your OTP: <strong>{otp}</strong></h2><p>Expires in 5 minutes.</p>')
            return render_template('admin_otp.html', error=None)
        else:
            session['is_admin'] = True
            return redirect('/admin/dashboard')
    else:
        blocked = record_failed(ip)
        remaining = 5 - len(failed_attempts[ip])
        if blocked:
            return render_template('admin_login.html', error='Too many failed attempts. Blocked for 30 minutes.')
        return render_template('admin_login.html', error=f'Wrong password! {remaining} attempts remaining.')

@app.route('/admin/verify-otp', methods=['POST'])
def admin_verify_otp():
    sid = session.get('_id','')
    otp = request.form.get('otp','').strip()
    stored = admin_otp_store.get(sid)
    if stored and time.time() < stored['expires'] and otp == stored['otp']:
        del admin_otp_store[sid]
        session['is_admin'] = True
        return redirect('/admin/dashboard')
    return render_template('admin_otp.html', error='Wrong or expired OTP. Try again.')

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect('/admin')

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('is_admin'): return redirect('/admin')
    conn = db()
    users = fetchall(execute(conn, '''
        SELECT u.id,u.username,u.avatar,u.invite_code,u.email,u.is_premium,
        u.notify_email,u.notify_whatsapp,u.created_at,
        COUNT(DISTINCT f.friend_id) as friend_count
        FROM users u LEFT JOIN friends f ON f.user_id=u.id
        GROUP BY u.id ORDER BY u.id DESC
    '''))
    habits = fetchall(execute(conn, '''
        SELECT h.*,u.username FROM habits h JOIN users u ON h.user_id=u.id ORDER BY h.id DESC
    '''))
    today_str  = date.today().strftime('%Y-%m-%d')
    total_logs = fetchone(execute(conn,'SELECT COUNT(*) as c FROM logs'))['c']
    today_logs = fetchone(execute(conn,'SELECT COUNT(*) as c FROM logs WHERE log_date=?',(today_str,)))['c']
    total_rx   = fetchone(execute(conn,'SELECT COUNT(*) as c FROM reactions'))['c']
    total_fr   = fetchone(execute(conn,'SELECT COUNT(*) as c FROM friends'))['c']
    premium_ct = fetchone(execute(conn,'SELECT COUNT(*) as c FROM users WHERE is_premium=1'))['c']
    recent = fetchall(execute(conn,'''
        SELECT l.log_date,u.username,h.name as habit_name,u.avatar
        FROM logs l JOIN users u ON l.user_id=u.id JOIN habits h ON l.habit_id=h.id
        ORDER BY l.id DESC LIMIT 30
    '''))
    conn.close()
    users = [dict(u) for u in users]
    for u in users:
        uh = get_user_habits(u['id'])
        u['total_streak'] = sum(streak(h['id']) for h in uh)
        u['habit_count']  = len(uh)
    return render_template('admin.html',
        users=users, habits=habits,
        total_logs=total_logs, today_logs=today_logs,
        total_rx=total_rx, total_fr=total_fr,
        total_users=len(users), total_habits=len(habits),
        premium_ct=premium_ct, recent=recent,
    )

@app.route('/admin/toggle-premium', methods=['POST'])
def admin_toggle_premium():
    if not session.get('is_admin'): return redirect('/admin')
    uid = int(request.form.get('user_id',0))
    conn = db()
    u = fetchone(execute(conn,'SELECT is_premium FROM users WHERE id=?',(uid,)))
    new_val = 0 if u.get('is_premium') else 1
    execute(conn,'UPDATE users SET is_premium=? WHERE id=?',(new_val,uid))
    conn.commit()
    conn.close()
    return redirect('/admin/dashboard')

@app.route('/admin/delete-user', methods=['POST'])
def admin_delete_user():
    if not session.get('is_admin'): return redirect('/admin')
    uid = int(request.form.get('user_id',0))
    conn = db()
    hs = fetchall(execute(conn,'SELECT id FROM habits WHERE user_id=?',(uid,)))
    for h in hs: execute(conn,'DELETE FROM logs WHERE habit_id=?',(h['id'],))
    execute(conn,'DELETE FROM habits WHERE user_id=?',(uid,))
    execute(conn,'DELETE FROM friends WHERE user_id=? OR friend_id=?',(uid,uid))
    execute(conn,'DELETE FROM reactions WHERE from_id=? OR to_id=?',(uid,uid))
    execute(conn,'DELETE FROM users WHERE id=?',(uid,))
    conn.commit()
    conn.close()
    return redirect('/admin/dashboard')

# ── 404 ───────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

# ── INIT ──────────────────────────────────────────────────
try:
    init_db()
    print('✅ DB initialized')
except Exception as e:
    print(f'⚠️ DB warning: {e}')

if __name__ == '__main__':
    print('\n✅ Habit Tracker v6 running!')
    print('👉 Open: http://localhost:5000\n')
    app.run(debug=True, port=5000, use_reloader=False)
