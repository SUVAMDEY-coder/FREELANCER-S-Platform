from http.server import HTTPServer, BaseHTTPRequestHandler
import json, uuid, datetime, hashlib, os, urllib.parse, sqlite3, threading

DB_PATH = os.environ.get('DB_PATH', 'lockwork.db')
db_local = threading.local()

def get_db():
    if not hasattr(db_local, 'conn'):
        db_local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        db_local.conn.row_factory = sqlite3.Row
    return db_local.conn

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'client',
            balance REAL NOT NULL DEFAULT 10000.0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT DEFAULT '',
            client_id TEXT NOT NULL, client_name TEXT NOT NULL,
            freelancer_email TEXT NOT NULL, freelancer_id TEXT, freelancer_name TEXT,
            freelancer_accepted INTEGER DEFAULT 0, deadline TEXT,
            total REAL NOT NULL DEFAULT 0, released REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS milestones (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
            amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            submitted_at TEXT, approved_at TEXT, sort_order INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS activity (
            id TEXT PRIMARY KEY, time TEXT NOT NULL, text TEXT NOT NULL,
            project_id TEXT, user_id TEXT
        );
    ''')
    conn.commit()
    conn.close()

def now(): return datetime.datetime.now().isoformat()
def gen_id(): return str(uuid.uuid4())[:8].upper()
def hash_pass(p): return hashlib.sha256(p.encode()).hexdigest()
def row_to_dict(row): return dict(row) if row else None

def log_activity(text, project_id=None, user_id=None):
    db = get_db()
    db.execute('INSERT INTO activity VALUES (?,?,?,?,?)', (gen_id(), now(), text, project_id, user_id))
    db.commit()

def get_project_full(pid):
    db = get_db()
    p = row_to_dict(db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone())
    if not p: return None
    ms = db.execute('SELECT * FROM milestones WHERE project_id=? ORDER BY sort_order', (pid,)).fetchall()
    p['milestones'] = [row_to_dict(m) for m in ms]
    p['freelancer_accepted'] = bool(p['freelancer_accepted'])
    return p

class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"  {args[0]} {args[1]}")

    def get_session_user(self):
        for part in self.headers.get('Cookie', '').split(';'):
            part = part.strip()
            if part.startswith('session='):
                sid = part[8:]
                db = get_db()
                row = db.execute('SELECT user_id FROM sessions WHERE id=?', (sid,)).fetchone()
                if row:
                    u = db.execute('SELECT * FROM users WHERE id=?', (row['user_id'],)).fetchone()
                    return row_to_dict(u)
        return None

    def set_session(self, user_id):
        sid = str(uuid.uuid4())
        db = get_db()
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (sid, user_id, now()))
        db.commit()
        return sid

    def send_json(self, data, status=200, cookie=None):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        if cookie: self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, filename='file.txt'):
        body = text.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain')
        self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, filepath, mime):
        try:
            with open(filepath, 'rb') as f: body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'404 Not Found')

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length:
            try: return json.loads(self.rfile.read(length))
            except: return {}
        return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        u = self.get_session_user()

        if path == '/' or path == '/index.html':
            self.serve_static('index.html', 'text/html; charset=utf-8'); return

        if path == '/api/me':
            if u:
                self.send_json({'ok': True, 'user': {k: v for k, v in u.items() if k != 'password'}})
            else:
                self.send_json({'ok': False})

        elif path == '/api/projects':
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            db = get_db()
            rows = db.execute(
                'SELECT * FROM projects WHERE client_id=? ORDER BY created_at DESC' if u['role'] == 'client'
                else 'SELECT * FROM projects WHERE freelancer_email=? ORDER BY created_at DESC',
                (u['id'] if u['role'] == 'client' else u['email'],)
            ).fetchall()
            projects = []
            for row in rows:
                p = row_to_dict(row)
                ms = db.execute('SELECT * FROM milestones WHERE project_id=? ORDER BY sort_order', (p['id'],)).fetchall()
                p['milestones'] = [row_to_dict(m) for m in ms]
                p['freelancer_accepted'] = bool(p['freelancer_accepted'])
                projects.append(p)
            self.send_json({'ok': True, 'projects': projects})

        elif path.startswith('/api/projects/') and path.endswith('/contract'):
            pid = path.split('/')[3]
            p = get_project_full(pid)
            if not p: self.send_json({'error': 'Not found'}, 404); return
            text = f"SERVICE AGREEMENT — CONTRACT ID: {p['id']}\nGenerated: {now()[:10]}\n\n"
            text += f"CLIENT:     {p['client_name']}\nFREELANCER: {p.get('freelancer_name') or p['freelancer_email']}\n\n"
            text += f"PROJECT: {p['title']}\n{p.get('description','')}\n\nMILESTONES:\n"
            for i, m in enumerate(p['milestones']):
                text += f"  {i+1}. {m['title']} — ${float(m['amount']):.2f} [{m['status'].upper()}]\n"
            text += f"\nTOTAL: ${float(p['total']):.2f} | RELEASED: ${float(p['released']):.2f}\n"
            text += "\nTerms: IP transfers on full completion. Governed by platform escrow terms.\n"
            text += f"\nClient: {p['client_name']}\n"
            text += f"Freelancer: {p.get('freelancer_name') or p['freelancer_email']} {'[DIGITALLY SIGNED]' if p['freelancer_accepted'] else '[PENDING]'}\n"
            self.send_text(text, f"contract-{pid}.txt")

        elif path.startswith('/api/projects/') and len(path.split('/')) == 4:
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            pid = path.split('/')[3]
            p = get_project_full(pid)
            if not p: self.send_json({'ok': False, 'error': 'Not found'}); return
            self.send_json({'ok': True, 'project': p})

        elif path == '/api/activity':
            if not u: self.send_json({'logs': []}); return
            db = get_db()
            proj_filter = query.get('project', [None])[0]
            if proj_filter:
                rows = db.execute('SELECT * FROM activity WHERE project_id=? ORDER BY time DESC LIMIT 30', (proj_filter,)).fetchall()
            elif u['role'] == 'client':
                rows = db.execute('''SELECT a.* FROM activity a LEFT JOIN projects p ON a.project_id=p.id
                    WHERE p.client_id=? OR a.project_id IS NULL ORDER BY a.time DESC LIMIT 30''', (u['id'],)).fetchall()
            else:
                rows = db.execute('''SELECT a.* FROM activity a LEFT JOIN projects p ON a.project_id=p.id
                    WHERE p.freelancer_email=? OR a.project_id IS NULL ORDER BY a.time DESC LIMIT 30''', (u['email'],)).fetchall()
            self.send_json({'logs': [row_to_dict(r) for r in rows]})

        elif path == '/api/balance':
            if not u: self.send_json({'balance': 0}); return
            row = get_db().execute('SELECT balance FROM users WHERE id=?', (u['id'],)).fetchone()
            self.send_json({'balance': row['balance'] if row else 0})

        elif path == '/api/stats':
            db = get_db()
            active = db.execute("SELECT COUNT(*) FROM projects WHERE status IN ('active','pending')").fetchone()[0]
            escrow = db.execute("SELECT SUM(total-released) FROM projects WHERE status NOT IN ('complete','cancelled')").fetchone()[0] or 0
            completed = db.execute("SELECT COUNT(*) FROM milestones WHERE status='complete'").fetchone()[0]
            self.send_json({'projects': active, 'escrow': escrow, 'completed': completed})

        else:
            self.send_json({'error': 'Not found'}, 404)

    def do_POST(self):
        path = self.path
        d = self.read_body()
        u = self.get_session_user()

        if path == '/api/register':
            if not d.get('email') or not d.get('password') or not d.get('name'):
                self.send_json({'ok': False, 'error': 'All fields required'}); return
            db = get_db()
            if db.execute('SELECT id FROM users WHERE email=?', (d['email'].lower().strip(),)).fetchone():
                self.send_json({'ok': False, 'error': 'Email already registered'}); return
            uid = gen_id()
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?,?)',
                (uid, d['name'].strip(), d['email'].lower().strip(), hash_pass(d['password']), d.get('role','client'), 10000.0, now()))
            db.commit()
            sid = self.set_session(uid)
            log_activity(f"New user registered: {d['name']} ({d.get('role','client')})")
            self.send_json({'ok': True, 'user': {'id': uid, 'name': d['name'].strip(), 'email': d['email'].lower().strip(), 'role': d.get('role','client'), 'balance': 10000.0}},
                           cookie=f'session={sid}; Path=/; SameSite=Lax')

        elif path == '/api/login':
            db = get_db()
            user = row_to_dict(db.execute('SELECT * FROM users WHERE email=?', (d.get('email','').lower().strip(),)).fetchone())
            if not user or user['password'] != hash_pass(d.get('password','')):
                self.send_json({'ok': False, 'error': 'Invalid email or password'}); return
            sid = self.set_session(user['id'])
            self.send_json({'ok': True, 'user': {k: v for k, v in user.items() if k != 'password'}},
                           cookie=f'session={sid}; Path=/; SameSite=Lax')

        elif path == '/api/logout':
            for part in self.headers.get('Cookie','').split(';'):
                part = part.strip()
                if part.startswith('session='):
                    db = get_db()
                    db.execute('DELETE FROM sessions WHERE id=?', (part[8:],))
                    db.commit()
            self.send_json({'ok': True}, cookie='session=; Path=/; Max-Age=0')

        elif path == '/api/topup':
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            amount = float(d.get('amount', 0))
            if amount <= 0: self.send_json({'ok': False, 'error': 'Invalid amount'}); return
            db = get_db()
            db.execute('UPDATE users SET balance=balance+? WHERE id=?', (amount, u['id']))
            db.commit()
            new_bal = db.execute('SELECT balance FROM users WHERE id=?', (u['id'],)).fetchone()['balance']
            log_activity(f"{u['name']} added ${amount:.2f} to balance", user_id=u['id'])
            self.send_json({'ok': True, 'balance': new_bal})

        elif path == '/api/projects':
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            if not d.get('title') or not d.get('milestones') or not d.get('freelancer_email'):
                self.send_json({'ok': False, 'error': 'Missing required fields'}); return
            total = sum(float(ms['amount']) for ms in d['milestones'])
            db = get_db()
            bal = db.execute('SELECT balance FROM users WHERE id=?', (u['id'],)).fetchone()['balance']
            if total > bal:
                self.send_json({'ok': False, 'error': f'Insufficient balance. Need ${total:.2f}, have ${bal:.2f}'}); return
            db.execute('UPDATE users SET balance=balance-? WHERE id=?', (total, u['id']))
            pid = gen_id()
            fl = row_to_dict(db.execute('SELECT * FROM users WHERE email=?', (d['freelancer_email'].lower().strip(),)).fetchone())
            db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (pid, d['title'], d.get('description',''), u['id'], u['name'],
                 d['freelancer_email'].lower().strip(),
                 fl['id'] if fl else None, fl['name'] if fl else None,
                 0, d.get('deadline'), total, 0.0, 'pending', now()))
            for i, ms in enumerate(d['milestones']):
                db.execute('INSERT INTO milestones VALUES (?,?,?,?,?,?,?,?)',
                    (gen_id(), pid, ms['title'], float(ms['amount']), 'pending', None, None, i))
            db.commit()
            log_activity(f"Project '{d['title']}' created by {u['name']} — ${total:.2f} locked in escrow", pid, u['id'])
            self.send_json({'ok': True, 'project': get_project_full(pid)})

        elif path.endswith('/accept') and '/projects/' in path:
            pid = path.split('/')[3]
            p = get_project_full(pid)
            if not p: self.send_json({'ok': False, 'error': 'Not found'}); return
            db = get_db()
            db.execute('UPDATE projects SET freelancer_accepted=1, status=?, freelancer_id=?, freelancer_name=? WHERE id=?',
                ('active', u['id'] if u else None, u['name'] if u else None, pid))
            db.commit()
            log_activity(f"{u['name'] if u else 'Freelancer'} accepted project '{p['title']}' and signed contract", pid, u['id'] if u else None)
            self.send_json({'ok': True, 'project': get_project_full(pid)})

        elif path.endswith('/cancel') and '/projects/' in path:
            pid = path.split('/')[3]
            p = get_project_full(pid)
            if not p: self.send_json({'ok': False, 'error': 'Not found'}); return
            db = get_db()
            remaining = float(p['total']) - float(p['released'])
            db.execute('UPDATE projects SET status=? WHERE id=?', ('cancelled', pid))
            db.execute('UPDATE users SET balance=balance+? WHERE id=?', (remaining, p['client_id']))
            db.commit()
            log_activity(f"{u['name'] if u else 'Client'} cancelled '{p['title']}' — ${remaining:.2f} returned", pid, u['id'] if u else None)
            self.send_json({'ok': True})

        elif '/milestones/' in path and path.endswith('/submit'):
            parts = path.split('/')
            pid, mid = parts[3], parts[5]
            db = get_db()
            db.execute("UPDATE milestones SET status='submitted', submitted_at=? WHERE id=? AND project_id=?", (now(), mid, pid))
            db.commit()
            ms = row_to_dict(db.execute('SELECT * FROM milestones WHERE id=?', (mid,)).fetchone())
            log_activity(f"{u['name'] if u else 'Freelancer'} submitted '{ms['title']}' for review", pid, u['id'] if u else None)
            self.send_json({'ok': True})

        elif '/milestones/' in path and path.endswith('/approve'):
            parts = path.split('/')
            pid, mid = parts[3], parts[5]
            db = get_db()
            ms = row_to_dict(db.execute('SELECT * FROM milestones WHERE id=?', (mid,)).fetchone())
            if not ms: self.send_json({'ok': False, 'error': 'Not found'}); return
            db.execute("UPDATE milestones SET status='complete', approved_at=? WHERE id=?", (now(), mid))
            db.execute('UPDATE projects SET released=released+? WHERE id=?', (ms['amount'], pid))
            p = get_project_full(pid)
            if p and p.get('freelancer_id'):
                db.execute('UPDATE users SET balance=balance+? WHERE id=?', (ms['amount'], p['freelancer_id']))
            if p and all(m['status'] == 'complete' for m in p['milestones']):
                db.execute("UPDATE projects SET status='complete' WHERE id=?", (pid,))
            db.commit()
            log_activity(f"{u['name'] if u else 'Client'} approved '{ms['title']}' — ${float(ms['amount']):.2f} released", pid, u['id'] if u else None)
            self.send_json({'ok': True})

        elif '/milestones/' in path and path.endswith('/reject'):
            parts = path.split('/')
            pid, mid = parts[3], parts[5]
            db = get_db()
            ms = row_to_dict(db.execute('SELECT * FROM milestones WHERE id=?', (mid,)).fetchone())
            db.execute("UPDATE milestones SET status='pending', submitted_at=NULL WHERE id=?", (mid,))
            db.commit()
            log_activity(f"{u['name'] if u else 'Client'} requested revisions on '{ms['title'] if ms else mid}'", pid, u['id'] if u else None)
            self.send_json({'ok': True})

        else:
            self.send_json({'error': 'Not found'}, 404)


if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f"\n🔒 LockWork running → http://localhost:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
