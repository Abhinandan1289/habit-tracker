# 🎯 Habit Tracker – Full Stack Web App

## Tech Stack
- **Backend:** Python Flask
- **Database:** SQLite (zero setup)
- **Frontend:** HTML + CSS + JS (no frameworks needed)

## Features
- ✅ Username + Password login/register
- 🔴 Red = missed, 🟢 Green = done, 🟡 Yellow = today
- 🔒 Past months are LOCKED (cannot be changed)
- 📊 Month progress bar, year overview
- 👥 Friend system via unique 6-character invite codes
- 📋 Copy your code, share with friends
- 🌍 Full friend tracker visible side by side

---

## Setup (Local)

```bash
# 1. Install Python dependencies
pip install flask werkzeug

# 2. Run the app
cd habitapp
python app.py

# 3. Open browser
http://localhost:5000
```

---

## Deploy as a Real Website

### Option 1 — Railway.app (FREE, easiest)
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and deploy
railway login
railway init
railway up
```
Add `requirements.txt`:
```
flask
werkzeug
gunicorn
```
Add `Procfile`:
```
web: gunicorn app:app
```

### Option 2 — Render.com (FREE)
1. Push code to GitHub
2. Go to render.com → New Web Service
3. Connect your GitHub repo
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app`
6. Done! You get a free `.onrender.com` URL

### Option 3 — PythonAnywhere (FREE)
1. Sign up at pythonanywhere.com
2. Upload files via Files tab
3. Set up a Web App → Flask
4. Point to your app.py

---

## Things You Can Improve Later

| Feature | How |
|---|---|
| Email notifications | Flask-Mail + daily cron job |
| Google login | Flask-OAuthlib |
| Mobile app | Convert to PWA (add manifest.json) |
| All-time streak | Track across months in DB |
| Leaderboard | Sort friends by streak |
| Custom habit per friend | Already supported in DB |
| Dark/light theme toggle | CSS variables swap |
| Export data | Add CSV download endpoint |
| Profile pictures | Flask file upload |
| Habit categories | Add category column to DB |

---

## File Structure
```
habitapp/
├── app.py              ← Flask backend + all APIs
├── habit.db            ← SQLite database (auto-created)
├── requirements.txt
├── Procfile
└── templates/
    ├── auth.html       ← Login + Register page
    └── dashboard.html  ← Main tracker + friends
```

---

## API Endpoints
| Method | URL | Description |
|---|---|---|
| POST | /api/register | Create account |
| POST | /api/login | Login |
| POST | /api/logout | Logout |
| POST | /api/habit/toggle | Mark/unmark a day |
| GET  | /api/habit/data | Get month data |
| POST | /api/friends/add | Add friend by code |
| POST | /api/friends/remove | Remove friend |
| GET  | /api/me | Get my profile |
