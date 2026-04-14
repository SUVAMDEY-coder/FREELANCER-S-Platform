from http.server import HTTPServer, BaseHTTPRequestHandler
import json, uuid, datetime, hashlib, os, urllib.parse, sqlite3, threading, base64

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
            freelancer_email TEXT, freelancer_id TEXT, freelancer_name TEXT,
            freelancer_accepted INTEGER DEFAULT 0, deadline TEXT,
            total REAL NOT NULL DEFAULT 0, released REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS milestones (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
            amount REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            submitted_at TEXT, approved_at TEXT, sort_order INTEGER DEFAULT 0,
            submission_note TEXT, file_name TEXT, file_data TEXT, file_type TEXT
        );
        CREATE TABLE IF NOT EXISTS activity (
            id TEXT PRIMARY KEY, time TEXT NOT NULL, text TEXT NOT NULL,
            project_id TEXT, user_id TEXT
        );
    ''')
    # Migration: add file columns if they don't exist yet
    try:
        conn.execute('ALTER TABLE milestones ADD COLUMN submission_note TEXT')
    except: pass
    try:
        conn.execute('ALTER TABLE milestones ADD COLUMN file_name TEXT')
    except: pass
    try:
        conn.execute('ALTER TABLE milestones ADD COLUMN file_data TEXT')
    except: pass
    try:
        conn.execute('ALTER TABLE milestones ADD COLUMN file_type TEXT')
    except: pass
    # Migration: allow NULL freelancer_email for open projects
    try:
        conn.execute("UPDATE projects SET status='open' WHERE status='pending' AND freelancer_email=''")
    except: pass
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

def get_project_full(pid, include_file_data=False):
    db = get_db()
    p = row_to_dict(db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone())
    if not p: return None
    ms = db.execute('SELECT * FROM milestones WHERE project_id=? ORDER BY sort_order', (pid,)).fetchall()
    milestones = []
    for m in ms:
        md = row_to_dict(m)
        if not include_file_data:
            md.pop('file_data', None)  # don't send raw base64 in lists
        milestones.append(md)
    p['milestones'] = milestones
    p['freelancer_accepted'] = bool(p['freelancer_accepted'])
    return p

HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LockWork — Freelance Escrow Platform</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --navy:#0a0e1a;--navy2:#111827;--navy3:#1a2235;
    --amber:#f59e0b;--amber2:#fbbf24;--amber-dim:#92600a;
    --green:#10b981;--red:#ef4444;--blue:#3b82f6;
    --text:#e2e8f0;--muted:#64748b;--border:#1e2d45;
    --mono:'Space Mono',monospace;--sans:'Syne',sans-serif;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{background:var(--navy);color:var(--text);font-family:var(--mono);min-height:100vh;overflow-x:hidden;}
  body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(245,158,11,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(245,158,11,0.03) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0;}
  .container{max-width:1100px;margin:0 auto;padding:0 24px;position:relative;z-index:1;}
  nav{border-bottom:1px solid var(--border);padding:16px 0;position:sticky;top:0;background:rgba(10,14,26,0.97);backdrop-filter:blur(8px);z-index:100;}
  nav .inner{display:flex;align-items:center;justify-content:space-between;max-width:1100px;margin:0 auto;padding:0 24px;}
  .logo{font-family:var(--sans);font-weight:800;font-size:1.4rem;color:var(--amber);letter-spacing:-0.5px;cursor:pointer;}
  .logo span{color:var(--text);}
  nav a{color:var(--muted);text-decoration:none;font-size:0.75rem;letter-spacing:0.1em;text-transform:uppercase;transition:color 0.2s;cursor:pointer;}
  nav a:hover{color:var(--amber);}
  .nav-links{display:flex;gap:24px;align-items:center;}
  .btn{display:inline-block;padding:8px 18px;border-radius:4px;font-family:var(--mono);font-size:0.75rem;letter-spacing:0.08em;text-transform:uppercase;cursor:pointer;border:none;transition:all 0.2s;text-decoration:none;}
  .btn-amber{background:var(--amber);color:var(--navy);font-weight:700;}
  .btn-amber:hover{background:var(--amber2);}
  .btn-outline{border:1px solid var(--border);color:var(--text);background:transparent;}
  .btn-outline:hover{border-color:var(--amber);color:var(--amber);}
  .btn-sm{padding:6px 12px;font-size:0.7rem;}
  .btn-green{background:var(--green);color:white;}
  .btn-green:hover{opacity:0.9;}
  .btn-danger{background:rgba(239,68,68,0.1);color:var(--red);border:1px solid var(--red);}
  .hero{padding:80px 0 60px;}
  .hero-stamp{display:inline-block;border:2px solid var(--amber-dim);color:var(--amber);font-size:0.65rem;letter-spacing:0.2em;text-transform:uppercase;padding:4px 12px;margin-bottom:24px;}
  .hero h1{font-family:var(--sans);font-size:clamp(2.2rem,5vw,4rem);font-weight:800;line-height:1.05;max-width:700px;margin-bottom:20px;}
  .hero h1 em{color:var(--amber);font-style:normal;}
  .hero p{color:var(--muted);font-size:0.9rem;max-width:480px;line-height:1.7;margin-bottom:36px;}
  .hero-actions{display:flex;gap:12px;flex-wrap:wrap;}
  .stats-bar{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--border);margin:40px 0;background:var(--navy2);}
  .stat{padding:20px 24px;border-right:1px solid var(--border);}
  .stat:last-child{border-right:none;}
  .stat-n{font-family:var(--sans);font-size:2rem;font-weight:800;color:var(--amber);}
  .stat-l{font-size:0.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.1em;margin-top:4px;}
  .section-label{font-size:0.65rem;letter-spacing:0.2em;text-transform:uppercase;color:var(--amber);margin-bottom:16px;}
  .steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:2px;background:var(--border);border:1px solid var(--border);}
  .step{background:var(--navy2);padding:28px 24px;}
  .step-num{font-size:0.65rem;color:var(--amber);letter-spacing:0.15em;margin-bottom:12px;}
  .step h3{font-family:var(--sans);font-weight:600;font-size:1rem;margin-bottom:8px;}
  .step p{font-size:0.78rem;color:var(--muted);line-height:1.6;}
  .page{display:none;padding:40px 0 80px;}
  .page.active{display:block;}
  .page-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:32px;gap:16px;flex-wrap:wrap;}
  .page-header h2{font-family:var(--sans);font-size:1.6rem;font-weight:700;}
  .card{background:var(--navy2);border:1px solid var(--border);padding:24px;margin-bottom:16px;}
  .card-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:16px;}
  .card-title{font-family:var(--sans);font-size:1rem;font-weight:600;}
  .card-id{font-size:0.65rem;color:var(--muted);letter-spacing:0.1em;}
  .badge{display:inline-block;padding:2px 8px;font-size:0.65rem;letter-spacing:0.1em;text-transform:uppercase;border-radius:2px;}
  .badge-pending{background:rgba(245,158,11,0.15);color:var(--amber);}
  .badge-open{background:rgba(59,130,246,0.15);color:var(--blue);}
  .badge-active{background:rgba(59,130,246,0.15);color:var(--blue);}
  .badge-review{background:rgba(168,85,247,0.15);color:#a855f7;}
  .badge-complete{background:rgba(16,185,129,0.15);color:var(--green);}
  .badge-locked{background:rgba(100,116,139,0.15);color:var(--muted);}
  .badge-cancelled{background:rgba(239,68,68,0.15);color:var(--red);}
  .form-group{margin-bottom:20px;}
  label{display:block;font-size:0.7rem;letter-spacing:0.1em;text-transform:uppercase;color:var(--muted);margin-bottom:8px;}
  input,textarea,select{width:100%;background:var(--navy);border:1px solid var(--border);color:var(--text);padding:10px 14px;font-family:var(--mono);font-size:0.85rem;border-radius:2px;outline:none;transition:border-color 0.2s;}
  input:focus,textarea:focus,select:focus{border-color:var(--amber);}
  input[type=file]{padding:8px;cursor:pointer;}
  textarea{resize:vertical;min-height:80px;}
  .form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
  .milestone-item{display:flex;align-items:flex-start;gap:12px;padding:14px 18px;border:1px solid var(--border);margin-bottom:2px;background:var(--navy3);transition:border-color 0.2s;flex-wrap:wrap;}
  .milestone-item:hover{border-color:var(--amber-dim);}
  .milestone-num{font-size:0.65rem;color:var(--amber);min-width:28px;padding-top:2px;}
  .milestone-title{flex:1;font-size:0.85rem;min-width:120px;}
  .milestone-amount{font-family:var(--sans);font-weight:600;color:var(--amber);font-size:0.9rem;min-width:80px;text-align:right;}
  .milestone-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
  .milestone-body{width:100%;padding-top:8px;}
  .escrow-box{border:1px solid var(--amber-dim);background:rgba(245,158,11,0.04);padding:20px 24px;margin-bottom:20px;position:relative;}
  .escrow-box::before{content:'🔒 ESCROW';position:absolute;top:-10px;left:16px;background:var(--navy2);padding:0 8px;font-size:0.65rem;letter-spacing:0.15em;color:var(--amber);}
  .escrow-amount{font-family:var(--sans);font-size:2rem;font-weight:800;color:var(--amber);}
  .escrow-meta{font-size:0.75rem;color:var(--muted);margin-top:6px;}
  .tabs{display:flex;border-bottom:1px solid var(--border);margin-bottom:28px;overflow-x:auto;}
  .tab{padding:10px 20px;font-size:0.75rem;letter-spacing:0.08em;text-transform:uppercase;color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all 0.2s;white-space:nowrap;}
  .tab.active{color:var(--amber);border-bottom-color:var(--amber);}
  .tab:hover{color:var(--text);}
  .contract-preview{background:var(--navy3);border:1px solid var(--border);padding:32px;font-size:0.78rem;line-height:1.8;font-family:var(--mono);white-space:pre-wrap;color:var(--text);max-height:400px;overflow-y:auto;}
  .log-entry{display:flex;gap:14px;padding:12px 0;border-bottom:1px solid var(--border);align-items:flex-start;}
  .log-time{font-size:0.65rem;color:var(--muted);min-width:100px;padding-top:2px;}
  .log-dot{width:8px;height:8px;border-radius:50%;background:var(--amber);margin-top:5px;flex-shrink:0;}
  .log-text{font-size:0.8rem;line-height:1.5;}
  .auth-box{max-width:460px;margin:80px auto;}
  .auth-toggle{font-size:0.75rem;color:var(--muted);margin-top:20px;text-align:center;}
  .auth-toggle a{color:var(--amber);cursor:pointer;}
  .alert{padding:12px 16px;font-size:0.8rem;margin-bottom:20px;border-left:3px solid;}
  .alert-err{background:rgba(239,68,68,0.08);border-color:var(--red);color:#fca5a5;}
  .alert-ok{background:rgba(16,185,129,0.08);border-color:var(--green);color:#6ee7b7;}
  .progress-bar{height:4px;background:var(--border);margin:12px 0;}
  .progress-fill{height:100%;background:var(--amber);transition:width 0.5s;}
  .flex{display:flex;}.items-center{align-items:center;}.justify-between{justify-content:space-between;}
  .mt-8{margin-top:8px;}.mt-16{margin-top:16px;}
  .text-muted{color:var(--muted);font-size:0.78rem;}.text-amber{color:var(--amber);}.text-green{color:var(--green);}.text-red{color:var(--red);}
  .bold{font-weight:700;}.w-full{width:100%;}
  .role-cards{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:20px 0;}
  .role-card{padding:20px;border:2px solid var(--border);cursor:pointer;transition:all 0.2s;text-align:center;}
  .role-card:hover{border-color:var(--amber-dim);}
  .role-card.selected{border-color:var(--amber);background:rgba(245,158,11,0.06);}
  .role-card .role-icon{font-size:1.8rem;margin-bottom:8px;}
  .role-card .role-name{font-family:var(--sans);font-weight:600;font-size:0.9rem;}
  .role-card .role-desc{font-size:0.7rem;color:var(--muted);margin-top:4px;}
  .empty-state{text-align:center;padding:60px 20px;color:var(--muted);font-size:0.85rem;}
  .empty-state .icon{font-size:2.5rem;margin-bottom:12px;}
  .modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.75);z-index:200;align-items:center;justify-content:center;}
  .modal-overlay.active{display:flex;}
  .modal{background:var(--navy2);border:1px solid var(--border);padding:32px;max-width:500px;width:95%;position:relative;max-height:90vh;overflow-y:auto;}
  .modal h3{font-family:var(--sans);font-size:1.2rem;font-weight:700;margin-bottom:16px;}
  .modal-close{position:absolute;top:16px;right:16px;background:none;border:none;color:var(--muted);cursor:pointer;font-size:1.2rem;}
  .topup-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:16px 0;}
  .topup-btn{padding:12px;border:1px solid var(--border);background:var(--navy3);color:var(--amber);font-family:var(--sans);font-weight:700;cursor:pointer;transition:all 0.2s;font-size:0.9rem;}
  .topup-btn:hover{border-color:var(--amber);background:rgba(245,158,11,0.08);}
  .file-preview{background:var(--navy3);border:1px solid var(--border);padding:12px 16px;margin-top:8px;font-size:0.78rem;display:flex;align-items:center;gap:10px;}
  .file-icon{font-size:1.2rem;}
  .upload-zone{border:2px dashed var(--border);padding:20px;text-align:center;cursor:pointer;transition:all 0.2s;font-size:0.8rem;color:var(--muted);}
  .upload-zone:hover{border-color:var(--amber);color:var(--amber);}
  .upload-zone.dragover{border-color:var(--amber);background:rgba(245,158,11,0.04);}
  @keyframes fadeIn{from{opacity:0;transform:translateY(10px);}to{opacity:1;transform:translateY(0);}}
  .animate-in{animation:fadeIn 0.3s ease;}
  @media(max-width:768px){.form-row{grid-template-columns:1fr;}.stats-bar{grid-template-columns:1fr;}.role-cards{grid-template-columns:1fr;}[style*="grid-template-columns:1fr 320px"]{grid-template-columns:1fr !important;}}
</style>
</head>
<body>

<!-- TOPUP MODAL -->
<div class="modal-overlay" id="topupModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('topupModal')">✕</button>
    <h3>Add Funds to Balance</h3>
    <p class="text-muted" style="font-size:0.8rem;margin-bottom:8px;">Select an amount to add (demo mode)</p>
    <div class="topup-grid">
      <button class="topup-btn" onclick="topUp(500)">$500</button>
      <button class="topup-btn" onclick="topUp(1000)">$1,000</button>
      <button class="topup-btn" onclick="topUp(2500)">$2,500</button>
      <button class="topup-btn" onclick="topUp(5000)">$5,000</button>
      <button class="topup-btn" onclick="topUp(10000)">$10,000</button>
      <button class="topup-btn" onclick="topUp(25000)">$25,000</button>
    </div>
    <div id="topupAlert"></div>
  </div>
</div>

<!-- SUBMIT MILESTONE MODAL -->
<div class="modal-overlay" id="submitModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('submitModal')">✕</button>
    <h3>Submit Milestone Work</h3>
    <p class="text-muted" style="font-size:0.8rem;margin-bottom:20px;">Upload your deliverable and add a note for the client.</p>
    <div class="form-group">
      <label>Milestone</label>
      <input type="text" id="submitMsTitle" readonly style="opacity:0.6;">
    </div>
    <div class="form-group">
      <label>Note to Client</label>
      <textarea id="submitNote" placeholder="Describe what you completed, any decisions made, links to live work..." rows="3"></textarea>
    </div>
    <div class="form-group">
      <label>Upload File (optional)</label>
      <div class="upload-zone" id="uploadZone" onclick="document.getElementById('fileInput').click()"
           ondragover="event.preventDefault();this.classList.add('dragover')"
           ondragleave="this.classList.remove('dragover')"
           ondrop="handleDrop(event)">
        📎 Click to upload or drag & drop<br>
        <span style="font-size:0.7rem;">Any file type · Max 5MB</span>
      </div>
      <input type="file" id="fileInput" style="display:none;" onchange="handleFileSelect(this)">
      <div id="filePreview" style="display:none;" class="file-preview">
        <span class="file-icon">📄</span>
        <div>
          <div id="fileName" style="font-weight:700;"></div>
          <div id="fileSize" class="text-muted"></div>
        </div>
        <button class="btn btn-outline btn-sm" onclick="clearFile()" style="margin-left:auto;">✕</button>
      </div>
    </div>
    <div id="submitAlert"></div>
    <button class="btn btn-amber w-full" onclick="doSubmitMilestone()" style="padding:12px;">Submit Work →</button>
  </div>
</div>

<nav>
  <div class="inner">
    <div class="logo" onclick="showPage('home')">Lock<span>Work</span></div>
    <div class="nav-links">
      <a onclick="showPage('home')">Home</a>
      <a onclick="showPage('how')">How It Works</a>
      <span id="authNav"></span>
    </div>
  </div>
</nav>

<!-- HOME -->
<div id="page-home" class="page active">
  <div class="container">
    <div class="hero">
      <div class="hero-stamp">Freelance Trust Infrastructure</div>
      <h1>Work gets done.<br><em>Everyone gets paid.</em></h1>
      <p>LockWork eliminates freelancer ghosting and payment disputes through milestone-based escrow, identity anchoring, and enforceable digital contracts.</p>
      <div class="hero-actions">
        <button class="btn btn-amber" onclick="showPage('auth')">Get Started Free</button>
        <button class="btn btn-outline" onclick="showPage('how')">See How It Works</button>
      </div>
    </div>
    <div class="stats-bar">
      <div class="stat"><div class="stat-n" id="statProjects">0</div><div class="stat-l">Active Projects</div></div>
      <div class="stat"><div class="stat-n" id="statEscrow">$0</div><div class="stat-l">Funds in Escrow</div></div>
      <div class="stat"><div class="stat-n" id="statCompleted">0</div><div class="stat-l">Milestones Completed</div></div>
    </div>
    <div style="padding:48px 0;">
      <div class="section-label">The Problem</div>
      <h2 style="font-family:var(--sans);font-size:1.8rem;font-weight:700;margin-bottom:32px;">Informal hiring is broken</h2>
      <div class="steps">
        <div class="step"><div class="step-num">PROBLEM 01</div><h3>Freelancers ghost</h3><p>After receiving partial payment, workers disappear leaving projects incomplete with no recourse.</p></div>
        <div class="step"><div class="step-num">PROBLEM 02</div><h3>No legal framework</h3><p>Informal channels create agreements with zero enforceability.</p></div>
        <div class="step"><div class="step-num">PROBLEM 03</div><h3>Asymmetric trust</h3><p>Both sides take on risk with no mechanism to balance it.</p></div>
        <div class="step"><div class="step-num">PROBLEM 04</div><h3>Identity is anonymous</h3><p>Workers face zero reputational cost to abandon a project.</p></div>
      </div>
    </div>
  </div>
</div>

<!-- HOW IT WORKS -->
<div id="page-how" class="page">
  <div class="container">
    <div style="padding:48px 0 80px;">
      <div class="section-label">The Solution</div>
      <h2 style="font-family:var(--sans);font-size:1.8rem;font-weight:700;margin-bottom:32px;">How LockWork protects both sides</h2>
      <div class="steps" style="margin-bottom:40px;">
        <div class="step"><div class="step-num">STEP 01</div><h3>Business posts a project</h3><p>Define deliverables, set milestone phases with amounts. Project is visible to all freelancers.</p></div>
        <div class="step"><div class="step-num">STEP 02</div><h3>Funds locked in escrow</h3><p>Business locks the full budget. Money is committed but not released yet.</p></div>
        <div class="step"><div class="step-num">STEP 03</div><h3>Freelancer accepts & delivers</h3><p>Any freelancer can accept an open project, sign the contract, and submit files per milestone.</p></div>
        <div class="step"><div class="step-num">STEP 04</div><h3>Client approves, funds release</h3><p>Each milestone approval releases funds. IP transfers on full completion.</p></div>
      </div>
      <div style="text-align:center;"><button class="btn btn-amber" onclick="showPage('auth')">Start a Project Now</button></div>
    </div>
  </div>
</div>

<!-- AUTH -->
<div id="page-auth" class="page">
  <div class="container">
    <div class="auth-box animate-in">
      <div class="logo" style="font-size:1.6rem;margin-bottom:32px;display:block;">Lock<span style="color:var(--text)">Work</span></div>
      <div id="authAlert"></div>
      <div id="loginForm">
        <div class="section-label">Sign In</div>
        <div class="form-group"><label>Email</label><input type="email" id="loginEmail" placeholder="you@company.com" onkeydown="if(event.key==='Enter')login()"></div>
        <div class="form-group"><label>Password</label><input type="password" id="loginPass" placeholder="••••••••" onkeydown="if(event.key==='Enter')login()"></div>
        <button class="btn btn-amber w-full" onclick="login()">Sign In →</button>
        <div class="auth-toggle">No account? <a onclick="toggleAuth('register')">Create one</a></div>
        <div class="auth-toggle" style="margin-top:12px;">
          <a onclick="demoLogin('business')">⚡ Demo as Business</a> &nbsp;|&nbsp; <a onclick="demoLogin('freelancer')">⚡ Demo as Freelancer</a>
        </div>
      </div>
      <div id="registerForm" style="display:none;">
        <div class="section-label">Create Account</div>
        <div class="form-group"><label>Full Name</label><input type="text" id="regName" placeholder="Alex Johnson"></div>
        <div class="form-group"><label>Email</label><input type="email" id="regEmail" placeholder="you@company.com"></div>
        <div class="form-group"><label>Password</label><input type="password" id="regPass" placeholder="Choose a password"></div>
        <div class="form-group">
          <label>I am a...</label>
          <div class="role-cards">
            <div class="role-card selected" id="roleClient" onclick="selectRole('client')"><div class="role-icon">🏢</div><div class="role-name">Business / Client</div><div class="role-desc">I hire freelancers</div></div>
            <div class="role-card" id="roleFreelancer" onclick="selectRole('freelancer')"><div class="role-icon">👤</div><div class="role-name">Freelancer</div><div class="role-desc">I do the work</div></div>
          </div>
        </div>
        <button class="btn btn-amber w-full" onclick="register()">Create Account →</button>
        <div class="auth-toggle">Already have an account? <a onclick="toggleAuth('login')">Sign in</a></div>
      </div>
    </div>
  </div>
</div>

<!-- DASHBOARD -->
<div id="page-dashboard" class="page">
  <div class="container">
    <div class="page-header">
      <div><h2 id="dashTitle">Dashboard</h2><p id="dashSubtitle" class="text-muted"></p></div>
      <div id="dashActions" style="display:flex;gap:8px;flex-wrap:wrap;"></div>
    </div>
    <div class="tabs">
      <div class="tab active" id="tab-projects" onclick="switchTab('projects')">My Projects</div>
      <div class="tab" id="tab-browse" onclick="switchTab('browse')">Browse Projects</div>
      <div class="tab" id="tab-activity" onclick="switchTab('activity')">Activity</div>
      <div class="tab" id="tab-profile" onclick="switchTab('profile')">Profile</div>
    </div>
    <div id="tabContent"></div>
  </div>
</div>

<!-- NEW PROJECT -->
<div id="page-newproject" class="page">
  <div class="container">
    <div style="padding:40px 0 80px;max-width:680px;">
      <div class="page-header">
        <div><h2>Post a Project</h2><p class="text-muted">Define scope, milestones, and lock funds in escrow</p></div>
        <button class="btn btn-outline btn-sm" onclick="showPage('dashboard')">← Back</button>
      </div>
      <div id="newProjectAlert"></div>
      <div class="card">
        <div class="section-label" style="margin-bottom:20px;">Project Details</div>
        <div class="form-group"><label>Project Title</label><input type="text" id="projTitle" placeholder="e.g. E-commerce Website Redesign"></div>
        <div class="form-group"><label>Description & Deliverables</label><textarea id="projDesc" rows="4" placeholder="Describe what needs to be done, deliverables, tech stack, requirements..."></textarea></div>
        <div class="form-row">
          <div class="form-group">
            <label>Assign to Specific Freelancer <span style="color:var(--muted);font-size:0.65rem;">(optional)</span></label>
            <input type="email" id="projFreelancer" placeholder="freelancer@email.com or leave blank">
          </div>
          <div class="form-group"><label>Deadline</label><input type="date" id="projDeadline"></div>
        </div>
      </div>
      <div class="card">
        <div class="flex justify-between items-center" style="margin-bottom:20px;">
          <div class="section-label" style="margin-bottom:0;">Milestones & Escrow</div>
          <button class="btn btn-outline btn-sm" onclick="addMilestone()">+ Add Milestone</button>
        </div>
        <p class="text-muted" style="font-size:0.75rem;margin-bottom:16px;">Break the project into phases. Funds released only upon your approval.</p>
        <div id="milestoneInputs"></div>
        <div class="escrow-box" style="margin-top:20px;">
          <div class="flex justify-between items-center">
            <div><div class="escrow-amount" id="totalAmount">$0.00</div><div class="escrow-meta">Total to be locked in escrow</div></div>
            <div style="text-align:right;font-size:0.75rem;color:var(--muted);">Funds held securely<br>Released per milestone</div>
          </div>
        </div>
      </div>
      <div id="balanceWarning" style="display:none;" class="alert alert-err">Insufficient balance. <a onclick="openModal('topupModal')" style="color:var(--amber);cursor:pointer;">Add funds →</a></div>
      <button class="btn btn-amber" style="width:100%;padding:14px;" onclick="createProject()">🔒 Lock Funds & Post Project</button>
    </div>
  </div>
</div>

<!-- PROJECT DETAIL -->
<div id="page-project" class="page">
  <div class="container"><div style="padding:40px 0 80px;"><div id="projectDetailContent"></div></div></div>
</div>

<script>
let currentUser=null, currentRole='client', milestoneCount=0, currentProjectId=null, currentTab='projects';
let submitProjId=null, submitMsId=null, selectedFile=null;

async function api(path,method='GET',body=null){
  const opts={method,headers:{'Content-Type':'application/json'},credentials:'include'};
  if(body) opts.body=JSON.stringify(body);
  try{ const r=await fetch(path,opts); return r.json(); }
  catch(e){ return {ok:false,error:'Network error'}; }
}

function showPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  const el=document.getElementById('page-'+name);
  if(el){el.classList.add('active');el.classList.add('animate-in');}
  window.scrollTo(0,0);
  if(name==='dashboard') renderDashboard();
  if(name==='newproject') initNewProject();
  if(name==='home') updateStats();
}

function updateNav(){
  const nav=document.getElementById('authNav');
  if(currentUser){
    nav.innerHTML=`<span style="color:var(--muted);font-size:0.75rem">${currentUser.name}</span>
      <a onclick="showPage('dashboard')">Dashboard</a>
      <a onclick="logout()">Sign Out</a>`;
  }else{
    nav.innerHTML=`<a onclick="showPage('auth')">Sign In</a>`;
  }
}

function toggleAuth(mode){
  document.getElementById('loginForm').style.display=mode==='login'?'block':'none';
  document.getElementById('registerForm').style.display=mode==='register'?'block':'none';
  document.getElementById('authAlert').innerHTML='';
}

function selectRole(role){
  currentRole=role;
  document.getElementById('roleClient').classList.toggle('selected',role==='client');
  document.getElementById('roleFreelancer').classList.toggle('selected',role==='freelancer');
}

async function login(){
  const email=document.getElementById('loginEmail').value.trim();
  const pass=document.getElementById('loginPass').value;
  if(!email||!pass){showAlert('authAlert','Please enter email and password','err');return;}
  const r=await api('/api/login','POST',{email,password:pass});
  if(r.ok){currentUser=r.user;updateNav();showPage('dashboard');}
  else showAlert('authAlert',r.error||'Login failed','err');
}

async function register(){
  const name=document.getElementById('regName').value.trim();
  const email=document.getElementById('regEmail').value.trim();
  const pass=document.getElementById('regPass').value;
  if(!name||!email||!pass){showAlert('authAlert','All fields required','err');return;}
  const r=await api('/api/register','POST',{name,email,password:pass,role:currentRole});
  if(r.ok){currentUser=r.user;updateNav();showPage('dashboard');}
  else showAlert('authAlert',r.error||'Registration failed','err');
}

async function demoLogin(role){
  const demos={business:{email:'business@demo.com',password:'demo123'},freelancer:{email:'freelancer@demo.com',password:'demo123'}};
  let r=await api('/api/login','POST',demos[role]);
  if(!r.ok){
    const names={business:'Acme Corp',freelancer:'Alex Dev'},roles={business:'client',freelancer:'freelancer'};
    r=await api('/api/register','POST',{name:names[role],email:demos[role].email,password:demos[role].password,role:roles[role]});
  }
  if(r.ok){currentUser=r.user;updateNav();showPage('dashboard');}
}

async function logout(){
  await api('/api/logout','POST');
  currentUser=null;updateNav();showPage('home');
}

async function renderDashboard(){
  if(!currentUser){showPage('auth');return;}
  document.getElementById('dashTitle').textContent=`Welcome, ${currentUser.name}`;
  document.getElementById('dashSubtitle').textContent=currentUser.role==='client'?'Manage your projects and milestone approvals':'Find projects and submit your work';
  const actions=document.getElementById('dashActions');
  actions.innerHTML=currentUser.role==='client'
    ?`<button class="btn btn-outline btn-sm" onclick="openModal('topupModal')">💰 Add Funds</button><button class="btn btn-amber" onclick="showPage('newproject')">+ Post Project</button>`
    :'';
  switchTab(currentTab);
}

async function switchTab(tab){
  currentTab=tab;
  ['projects','browse','activity','profile'].forEach(t=>{
    const el=document.getElementById('tab-'+t);
    if(el) el.classList.toggle('active',t===tab);
  });
  const el=document.getElementById('tabContent');
  el.innerHTML='<div class="empty-state"><div>Loading...</div></div>';
  if(tab==='projects') await renderMyProjects(el);
  if(tab==='browse') await renderBrowseProjects(el);
  if(tab==='activity') await renderActivity(el);
  if(tab==='profile') await renderProfile(el);
}

async function renderMyProjects(el){
  const r=await api('/api/projects/mine');
  const projects=r.projects||[];
  if(!projects.length){
    el.innerHTML=`<div class="empty-state"><div class="icon">📋</div>
      <div>${currentUser.role==='client'?'No projects yet. Post your first one.':'You haven\'t accepted any projects yet. Browse open projects!'}</div>
      ${currentUser.role==='freelancer'?'<br><button class="btn btn-amber" onclick="switchTab(\'browse\')" style="margin-top:12px;">Browse Projects →</button>':''}
    </div>`;
    return;
  }
  el.innerHTML=projects.map(p=>projectCard(p)).join('');
}

async function renderBrowseProjects(el){
  const r=await api('/api/projects/all');
  const projects=r.projects||[];
  if(!projects.length){
    el.innerHTML='<div class="empty-state"><div class="icon">🔍</div><div>No open projects available right now.</div></div>';
    return;
  }
  // For clients show their own projects, for freelancers show all open/active
  const label=currentUser.role==='client'?'All your posted projects':'All available projects — accept any open one';
  el.innerHTML=`<p class="text-muted" style="margin-bottom:16px;font-size:0.75rem;">${label}</p>`+projects.map(p=>projectCard(p)).join('');
}

function projectCard(p){
  return `<div class="card" style="cursor:pointer" onclick="viewProject('${p.id}')">
    <div class="card-header">
      <div><div class="card-title">${p.title}</div><div class="card-id">ID: ${p.id} · By ${p.client_name} · ${p.deadline||'No deadline'}</div></div>
      <span class="badge badge-${p.status}">${p.status}</span>
    </div>
    <div class="flex justify-between items-center">
      <div class="text-muted">${p.milestones.length} milestone${p.milestones.length!==1?'s':''}</div>
      <div class="escrow-amount" style="font-size:1.2rem">$${Number(p.total).toFixed(2)}</div>
    </div>
    <div class="progress-bar"><div class="progress-fill" style="width:${progressPct(p)}%"></div></div>
    <div class="text-muted mt-8">${completedCount(p)} of ${p.milestones.length} milestones complete · ${p.freelancer_name||p.freelancer_email||'<span style="color:var(--green)">Open for applications</span>'}</div>
  </div>`;
}

function progressPct(p){return!p.milestones.length?0:(completedCount(p)/p.milestones.length*100).toFixed(0);}
function completedCount(p){return p.milestones.filter(m=>m.status==='complete').length;}

async function renderActivity(el){
  const r=await api('/api/activity');
  const logs=r.logs||[];
  if(!logs.length){el.innerHTML=`<div class="empty-state"><div class="icon">📝</div><div>No activity yet.</div></div>`;return;}
  el.innerHTML=`<div class="card">`+logs.map(l=>`
    <div class="log-entry">
      <div class="log-time">${l.time.slice(0,10)} ${l.time.slice(11,16)}</div>
      <div class="log-dot"></div>
      <div class="log-text">${l.text}</div>
    </div>`).join('')+'</div>';
}

async function renderProfile(el){
  const [bal, statsR, projR] = await Promise.all([
    api('/api/balance'),
    api('/api/stats'),
    api('/api/projects/mine')
  ]);
  const balance = Number(bal.balance||0);
  const projects = projR.projects||[];
  const completed = projects.filter(p=>p.status==='complete').length;
  const active = projects.filter(p=>['active','pending','open'].includes(p.status)).length;
  const totalValue = projects.reduce((s,p)=>s+Number(p.total),0);
  const initials = currentUser.name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
  const isClient = currentUser.role==='client';
  const joinDate = currentUser.created_at ? currentUser.created_at.slice(0,10) : 'N/A';

  el.innerHTML=`
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
    <!-- LEFT: Identity card -->
    <div class="card" style="margin-bottom:0;">
      <div style="display:flex;align-items:center;gap:20px;margin-bottom:24px;">
        <div style="width:72px;height:72px;border-radius:50%;background:var(--amber);display:flex;align-items:center;justify-content:center;font-family:var(--sans);font-size:1.6rem;font-weight:800;color:var(--navy);flex-shrink:0;">${initials}</div>
        <div>
          <div style="font-family:var(--sans);font-size:1.3rem;font-weight:700;">${currentUser.name}</div>
          <div class="text-muted" style="margin-top:4px;">${currentUser.email}</div>
          <span class="badge ${isClient?'badge-active':'badge-complete'}" style="margin-top:6px;display:inline-block;">${isClient?'🏢 Business':'👤 Freelancer'}</span>
        </div>
      </div>
      <div style="border-top:1px solid var(--border);padding-top:16px;">
        <div style="display:flex;justify-content:space-between;font-size:0.78rem;margin-bottom:10px;">
          <span class="text-muted">Member ID</span><span class="bold" style="font-family:var(--mono);">${currentUser.id}</span>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:0.78rem;margin-bottom:10px;">
          <span class="text-muted">Joined</span><span>${joinDate}</span>
        </div>
        <div style="display:flex;justify-content:space-between;font-size:0.78rem;">
          <span class="text-muted">Account Type</span><span>${isClient?'Business / Client':'Freelancer'}</span>
        </div>
      </div>
    </div>

    <!-- RIGHT: Balance card -->
    <div class="card" style="margin-bottom:0;">
      <div class="section-label" style="margin-bottom:16px;">Balance & Earnings</div>
      <div class="escrow-box" style="margin-bottom:16px;">
        <div class="escrow-amount">$${balance.toFixed(2)}</div>
        <div class="escrow-meta">${isClient?'Available to lock in escrow':'Available to withdraw'}</div>
      </div>
      ${isClient?`<button class="btn btn-amber w-full" onclick="openModal('topupModal')">💰 Add Funds</button>`:''}
      <div style="margin-top:16px;font-size:0.75rem;color:var(--muted);line-height:1.6;">
        ${isClient?'Funds you add are locked per project milestone and released upon approval.':'Funds are credited to your balance when a client approves your milestone submission.'}
      </div>
    </div>
  </div>

  <!-- STATS ROW -->
  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:2px;background:var(--border);border:1px solid var(--border);margin-bottom:16px;">
    <div style="background:var(--navy2);padding:20px;text-align:center;">
      <div style="font-family:var(--sans);font-size:1.6rem;font-weight:800;color:var(--amber);">${projects.length}</div>
      <div style="font-size:0.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.1em;margin-top:4px;">Total Projects</div>
    </div>
    <div style="background:var(--navy2);padding:20px;text-align:center;">
      <div style="font-family:var(--sans);font-size:1.6rem;font-weight:800;color:var(--blue);">${active}</div>
      <div style="font-size:0.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.1em;margin-top:4px;">Active</div>
    </div>
    <div style="background:var(--navy2);padding:20px;text-align:center;">
      <div style="font-family:var(--sans);font-size:1.6rem;font-weight:800;color:var(--green);">${completed}</div>
      <div style="font-size:0.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.1em;margin-top:4px;">Completed</div>
    </div>
    <div style="background:var(--navy2);padding:20px;text-align:center;">
      <div style="font-family:var(--sans);font-size:1.6rem;font-weight:800;color:var(--amber);">$${totalValue.toFixed(0)}</div>
      <div style="font-size:0.65rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.1em;margin-top:4px;">${isClient?'Total Spent':'Total Earned'}</div>
    </div>
  </div>

  <!-- EDIT PROFILE -->
  <div class="card">
    <div class="section-label" style="margin-bottom:20px;">Edit Profile</div>
    <div id="profileAlert"></div>
    <div class="form-row">
      <div class="form-group">
        <label>Full Name</label>
        <input type="text" id="editName" value="${currentUser.name}" placeholder="Your full name">
      </div>
      <div class="form-group">
        <label>Email</label>
        <input type="email" id="editEmail" value="${currentUser.email}" placeholder="your@email.com">
      </div>
    </div>
    <button class="btn btn-amber" onclick="saveProfile()">Save Changes</button>
  </div>

  <!-- CHANGE PASSWORD -->
  <div class="card">
    <div class="section-label" style="margin-bottom:20px;">Change Password</div>
    <div id="passwordAlert"></div>
    <div class="form-row">
      <div class="form-group">
        <label>Current Password</label>
        <input type="password" id="currPass" placeholder="••••••••">
      </div>
      <div class="form-group">
        <label>New Password</label>
        <input type="password" id="newPass" placeholder="••••••••">
      </div>
    </div>
    <div class="form-group" style="max-width:280px;">
      <label>Confirm New Password</label>
      <input type="password" id="confirmPass" placeholder="••••••••">
    </div>
    <button class="btn btn-outline" onclick="changePassword()">Update Password</button>
  </div>

  <!-- RECENT PROJECTS -->
  ${projects.length ? `
  <div class="card">
    <div class="section-label" style="margin-bottom:16px;">Recent Projects</div>
    ${projects.slice(0,3).map(p=>`
      <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border);cursor:pointer;" onclick="viewProject('${p.id}')">
        <div>
          <div style="font-size:0.85rem;font-weight:600;">${p.title}</div>
          <div class="text-muted" style="font-size:0.7rem;margin-top:2px;">${p.milestones.length} milestones · $${Number(p.total).toFixed(2)}</div>
        </div>
        <span class="badge badge-${p.status}">${p.status}</span>
      </div>`).join('')}
    ${projects.length>3?`<div style="text-align:center;margin-top:12px;"><a onclick="switchTab('projects')" style="color:var(--amber);cursor:pointer;font-size:0.78rem;">View all ${projects.length} projects →</a></div>`:''}
  </div>` : ''}

  <!-- DANGER ZONE -->
  <div class="card" style="border-color:rgba(239,68,68,0.3);">
    <div class="section-label" style="margin-bottom:12px;color:var(--red);">Account</div>
    <p class="text-muted" style="font-size:0.78rem;margin-bottom:16px;">Sign out of your account on this device.</p>
    <button class="btn btn-danger" onclick="logout()">Sign Out</button>
  </div>
  `;
}

async function saveProfile(){
  const name = document.getElementById('editName').value.trim();
  const email = document.getElementById('editEmail').value.trim();
  if(!name||!email){showAlert('profileAlert','Name and email are required','err');return;}
  const r = await api('/api/profile','POST',{name,email});
  if(r.ok){
    currentUser.name=r.user.name;
    currentUser.email=r.user.email;
    updateNav();
    showAlert('profileAlert','Profile updated successfully!','ok');
    // refresh initials
    const initials=name.split(' ').map(w=>w[0]).join('').toUpperCase().slice(0,2);
    const av=document.querySelector('[style*="border-radius:50%"]');
    if(av) av.textContent=initials;
  }else showAlert('profileAlert',r.error||'Failed to update','err');
}

async function changePassword(){
  const curr=document.getElementById('currPass').value;
  const nw=document.getElementById('newPass').value;
  const conf=document.getElementById('confirmPass').value;
  if(!curr||!nw||!conf){showAlert('passwordAlert','All password fields required','err');return;}
  if(nw!==conf){showAlert('passwordAlert','New passwords do not match','err');return;}
  if(nw.length<6){showAlert('passwordAlert','Password must be at least 6 characters','err');return;}
  const r=await api('/api/password','POST',{current_password:curr,new_password:nw});
  if(r.ok){
    showAlert('passwordAlert','Password changed successfully!','ok');
    document.getElementById('currPass').value='';
    document.getElementById('newPass').value='';
    document.getElementById('confirmPass').value='';
  }else showAlert('passwordAlert',r.error||'Failed to change password','err');
}

function initNewProject(){
  milestoneCount=0;
  document.getElementById('milestoneInputs').innerHTML='';
  document.getElementById('totalAmount').textContent='$0.00';
  document.getElementById('newProjectAlert').innerHTML='';
  document.getElementById('balanceWarning').style.display='none';
  document.getElementById('projTitle').value='';
  document.getElementById('projDesc').value='';
  document.getElementById('projFreelancer').value='';
  addMilestone();addMilestone();addMilestone();
  const d=new Date();d.setDate(d.getDate()+30);
  document.getElementById('projDeadline').value=d.toISOString().split('T')[0];
}

function addMilestone(){
  milestoneCount++;
  const n=milestoneCount;
  const div=document.createElement('div');
  div.id='ms-'+n;div.className='milestone-item';
  div.innerHTML=`<span class="milestone-num">M${n}</span>
    <input type="text" placeholder="Milestone description" style="flex:1;margin-right:8px" id="msTitle-${n}" oninput="calcTotal()">
    <input type="number" placeholder="Amount $" style="width:120px;margin-right:8px" id="msAmt-${n}" min="0" step="0.01" oninput="calcTotal()">
    <button class="btn btn-outline btn-sm" onclick="removeMilestone(${n})" style="padding:4px 8px;">✕</button>`;
  document.getElementById('milestoneInputs').appendChild(div);
}

function removeMilestone(n){const el=document.getElementById('ms-'+n);if(el)el.remove();calcTotal();}

function calcTotal(){
  let total=0;
  for(let i=1;i<=milestoneCount;i++){const a=parseFloat(document.getElementById('msAmt-'+i)?.value||0);if(!isNaN(a))total+=a;}
  document.getElementById('totalAmount').textContent='$'+total.toFixed(2);
}

async function createProject(){
  const title=document.getElementById('projTitle').value.trim();
  const desc=document.getElementById('projDesc').value.trim();
  const freelancerEmail=document.getElementById('projFreelancer').value.trim();
  const deadline=document.getElementById('projDeadline').value;
  const milestones=[];
  for(let i=1;i<=milestoneCount;i++){
    const t=document.getElementById('msTitle-'+i),a=document.getElementById('msAmt-'+i);
    if(t&&a&&t.value.trim()&&parseFloat(a.value)>0) milestones.push({title:t.value.trim(),amount:parseFloat(a.value)});
  }
  if(!title||!milestones.length){showAlert('newProjectAlert','Please fill in title and at least one milestone.','err');return;}
  const r=await api('/api/projects','POST',{title,description:desc,freelancer_email:freelancerEmail,deadline,milestones});
  if(r.ok){showPage('dashboard');setTimeout(()=>viewProject(r.project.id),300);}
  else{
    showAlert('newProjectAlert',r.error||'Failed to create project','err');
    if(r.error&&r.error.includes('balance')) document.getElementById('balanceWarning').style.display='block';
  }
}

async function viewProject(id){
  currentProjectId=id;
  showPage('project');
  document.getElementById('projectDetailContent').innerHTML='<div class="empty-state"><div>Loading project...</div></div>';
  const r=await api('/api/projects/'+id);
  if(!r.ok){document.getElementById('projectDetailContent').innerHTML='<div class="empty-state"><div>Project not found.</div></div>';return;}
  const p=r.project;
  const isClient=currentUser.role==='client'&&p.client_id===currentUser.id;
  const isFreelancer=currentUser.role==='freelancer';
  const isMyFreelancer=isFreelancer&&p.freelancer_id===currentUser.id;

  const milestonesHTML=p.milestones.map((m,i)=>{
    let actions='';
    if(isMyFreelancer&&m.status==='pending') actions=`<button class="btn btn-amber btn-sm" onclick="openSubmitModal('${p.id}','${m.id}','${m.title.replace(/'/g,"\\'")}')">📎 Submit Work</button>`;
    if(isClient&&m.status==='submitted') actions=`<button class="btn btn-green btn-sm" onclick="approveMilestone('${p.id}','${m.id}')">✓ Approve & Pay</button> <button class="btn btn-outline btn-sm" onclick="rejectMilestone('${p.id}','${m.id}')">Request Revision</button>`;
    if(m.status==='complete') actions=`<span class="text-green" style="font-size:0.75rem;">✓ Paid $${Number(m.amount).toFixed(2)}</span>`;
    const bc=m.status==='pending'?'locked':m.status==='submitted'?'review':m.status==='complete'?'complete':'active';

    // File submission display
    let submissionHTML='';
    if(m.status==='submitted'||m.status==='complete'){
      submissionHTML=`<div class="milestone-body">`;
      if(m.submission_note) submissionHTML+=`<div style="font-size:0.8rem;color:var(--text);margin-bottom:8px;">💬 ${m.submission_note}</div>`;
      if(m.file_name) submissionHTML+=`<div class="file-preview"><span class="file-icon">📄</span><div><div style="font-weight:700;font-size:0.8rem;">${m.file_name}</div><div class="text-muted">Submitted file</div></div><a href="/api/milestones/${m.id}/file" class="btn btn-outline btn-sm" style="margin-left:auto;" download="${m.file_name}">↓ Download</a></div>`;
      submissionHTML+=`</div>`;
    }

    return `<div class="milestone-item" style="flex-direction:column;align-items:stretch;">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">
        <span class="milestone-num">M${i+1}</span>
        <span class="milestone-title">${m.title}</span>
        <span class="badge badge-${bc}">${m.status}</span>
        <span class="milestone-amount">$${Number(m.amount).toFixed(2)}</span>
        <div class="milestone-actions">${actions}</div>
      </div>
      ${submissionHTML}
    </div>`;
  }).join('');

  const contractText=generateContract(p);
  const isOpen=p.status==='open'||p.status==='pending';
  const canAccept=isFreelancer&&isOpen&&!p.freelancer_id;
  const isMyProject=isClient||(isMyFreelancer);
  const acceptBtn=canAccept?`<button class="btn btn-amber" onclick="acceptProject('${p.id}')">✅ Accept & Sign Contract</button>`:'';
  const cancelBtn=isClient&&isOpen?`<button class="btn btn-danger btn-sm" onclick="cancelProject('${p.id}')">Cancel Project</button>`:'';

  document.getElementById('projectDetailContent').innerHTML=`
    <div class="page-header">
      <div>
        <button class="btn btn-outline btn-sm" onclick="showPage('dashboard')" style="margin-bottom:12px">← Dashboard</button>
        <h2>${p.title}</h2>
        <p class="text-muted">ID: ${p.id} · Posted by ${p.client_name}</p>
      </div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <span class="badge badge-${p.status}" style="font-size:0.8rem;padding:6px 12px;">${p.status.toUpperCase()}</span>
        ${acceptBtn}${cancelBtn}
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 320px;gap:20px;align-items:start;">
      <div>
        <div class="card">
          <div class="section-label" style="margin-bottom:12px;">Project Brief</div>
          <p style="font-size:0.85rem;line-height:1.7;">${p.description||'No description provided.'}</p>
          ${p.deadline?`<p class="text-muted mt-16" style="font-size:0.75rem;">📅 Deadline: ${p.deadline}</p>`:''}
        </div>
        <div class="card">
          <div class="section-label" style="margin-bottom:16px;">Milestones</div>
          ${milestonesHTML}
          <div class="progress-bar" style="margin-top:20px;"><div class="progress-fill" style="width:${progressPct(p)}%"></div></div>
          <div class="text-muted mt-8">${completedCount(p)} of ${p.milestones.length} complete · $${Number(p.released).toFixed(2)} released</div>
        </div>
        ${isMyProject?`<div class="card">
          <div class="section-label" style="margin-bottom:12px;">Contract</div>
          <div class="contract-preview">${contractText}</div>
          <a class="btn btn-outline btn-sm" style="margin-top:12px;display:inline-block;" href="/api/projects/${p.id}/contract" target="_blank">↓ Download Contract</a>
        </div>`:''}
      </div>
      <div>
        <div class="escrow-box">
          <div class="escrow-amount">$${Number(p.total).toFixed(2)}</div>
          <div class="escrow-meta">Total project value</div>
          <hr style="border-color:var(--amber-dim);margin:16px 0;">
          <div class="flex justify-between text-muted" style="font-size:0.75rem;margin-bottom:6px;"><span>Released</span><span class="text-green">$${Number(p.released).toFixed(2)}</span></div>
          <div class="flex justify-between text-muted" style="font-size:0.75rem;"><span>Locked</span><span class="text-amber">$${(Number(p.total)-Number(p.released)).toFixed(2)}</span></div>
        </div>
        <div class="card">
          <div class="section-label" style="margin-bottom:12px;">Parties</div>
          <div style="font-size:0.78rem;margin-bottom:12px;"><div class="text-muted">Client</div><div class="bold">${p.client_name}</div></div>
          <div style="font-size:0.78rem;">
            <div class="text-muted">Freelancer</div>
            <div class="bold">${p.freelancer_name||p.freelancer_email||'<span style="color:var(--green)">Open — anyone can apply</span>'}</div>
            <span class="badge ${p.freelancer_accepted?'badge-complete':'badge-pending'}" style="margin-top:4px;">${p.freelancer_accepted?'Contract Signed':'Awaiting Acceptance'}</span>
          </div>
        </div>
        <div class="card">
          <div class="section-label" style="margin-bottom:12px;">Activity</div>
          <div id="projectLogs" style="font-size:0.78rem;">Loading...</div>
        </div>
      </div>
    </div>`;

  api('/api/activity?project='+id).then(r=>{
    const logs=r.logs||[];
    document.getElementById('projectLogs').innerHTML=logs.slice(0,8).map(l=>`
      <div style="padding:8px 0;border-bottom:1px solid var(--border);">
        <div class="text-muted" style="font-size:0.65rem;">${l.time.slice(0,10)} ${l.time.slice(11,16)}</div>
        <div style="font-size:0.78rem;margin-top:2px;">${l.text}</div>
      </div>`).join('')||'<div class="text-muted">No activity yet.</div>';
  });
}

function generateContract(p){
  return `SERVICE AGREEMENT
═══════════════════════════════════════
CONTRACT ID:   ${p.id}
DATE:          ${p.created_at.slice(0,10)}

PARTIES
───────────────────────────────────────
CLIENT:        ${p.client_name}
FREELANCER:    ${p.freelancer_name||p.freelancer_email||'[OPEN]'}

PROJECT SCOPE
───────────────────────────────────────
Title: ${p.title}
${p.description||'[No description provided]'}

PAYMENT SCHEDULE (ESCROW)
───────────────────────────────────────
${p.milestones.map((m,i)=>`Milestone ${i+1}: ${m.title}\n  Amount: $${Number(m.amount).toFixed(2)}\n  Status: ${m.status.toUpperCase()}`).join('\n\n')}

TOTAL VALUE: $${Number(p.total).toFixed(2)}

TERMS
───────────────────────────────────────
1. Funds held in escrow until client approves each milestone.
2. IP transfers only upon full payment completion.
3. Freelancer must complete all milestones as described.
4. Platform mediation required before legal action.
5. 14-day no-submit = abandonment; escrowed funds returned.

SIGNATURES
───────────────────────────────────────
Client: ${p.client_name}
${p.freelancer_accepted?'Freelancer: '+(p.freelancer_name||p.freelancer_email)+' [DIGITALLY SIGNED]':'Freelancer: [AWAITING SIGNATURE]'}
`;}

// ── FILE UPLOAD ──
function handleFileSelect(input){
  if(input.files&&input.files[0]) setFile(input.files[0]);
}
function handleDrop(e){
  e.preventDefault();
  document.getElementById('uploadZone').classList.remove('dragover');
  if(e.dataTransfer.files&&e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
}
function setFile(file){
  if(file.size>5*1024*1024){showAlert('submitAlert','File too large. Max 5MB.','err');return;}
  selectedFile=file;
  document.getElementById('uploadZone').style.display='none';
  document.getElementById('filePreview').style.display='flex';
  document.getElementById('fileName').textContent=file.name;
  document.getElementById('fileSize').textContent=(file.size/1024).toFixed(1)+' KB';
}
function clearFile(){
  selectedFile=null;
  document.getElementById('fileInput').value='';
  document.getElementById('uploadZone').style.display='block';
  document.getElementById('filePreview').style.display='none';
}

function openSubmitModal(projId, msId, msTitle){
  submitProjId=projId; submitMsId=msId;
  document.getElementById('submitMsTitle').value=msTitle;
  document.getElementById('submitNote').value='';
  document.getElementById('submitAlert').innerHTML='';
  clearFile();
  openModal('submitModal');
}

async function doSubmitMilestone(){
  const note=document.getElementById('submitNote').value.trim();
  let fileData=null, fileName=null, fileType=null;

  if(selectedFile){
    try{
      fileData=await new Promise((res,rej)=>{
        const reader=new FileReader();
        reader.onload=e=>res(e.target.result.split(',')[1]);
        reader.onerror=rej;
        reader.readAsDataURL(selectedFile);
      });
      fileName=selectedFile.name;
      fileType=selectedFile.type||'application/octet-stream';
    }catch(e){
      showAlert('submitAlert','Failed to read file.','err');return;
    }
  }

  const r=await api(`/api/projects/${submitProjId}/milestones/${submitMsId}/submit`,'POST',
    {note, file_name:fileName, file_data:fileData, file_type:fileType});
  if(r.ok){
    closeModal('submitModal');
    viewProject(submitProjId);
  }else{
    showAlert('submitAlert',r.error||'Submission failed','err');
  }
}

async function acceptProject(id){const r=await api('/api/projects/'+id+'/accept','POST');if(r.ok)viewProject(id);}
async function cancelProject(id){if(!confirm('Cancel project? Remaining escrowed funds will be returned.'))return;const r=await api('/api/projects/'+id+'/cancel','POST');if(r.ok)showPage('dashboard');}
async function approveMilestone(pId,mId){const r=await api(`/api/projects/${pId}/milestones/${mId}/approve`,'POST');if(r.ok){viewProject(pId);updateStats();}}
async function rejectMilestone(pId,mId){const r=await api(`/api/projects/${pId}/milestones/${mId}/reject`,'POST');if(r.ok)viewProject(pId);}

async function updateStats(){
  const r=await api('/api/stats');
  if(r){
    document.getElementById('statProjects').textContent=r.projects;
    document.getElementById('statEscrow').textContent='$'+Number(r.escrow||0).toFixed(0);
    document.getElementById('statCompleted').textContent=r.completed;
  }
}

function openModal(id){document.getElementById(id).classList.add('active');}
function closeModal(id){document.getElementById(id).classList.remove('active');}

async function topUp(amount){
  const r=await api('/api/topup','POST',{amount});
  if(r.ok){
    showAlert('topupAlert',`$${amount.toLocaleString()} added!`,'ok');
    setTimeout(()=>{closeModal('topupModal');if(currentTab==='profile')switchTab('profile');},1500);
  }else showAlert('topupAlert',r.error||'Failed','err');
}

function showAlert(id,msg,type){
  const el=document.getElementById(id);
  if(el){el.innerHTML=`<div class="alert alert-${type}">${msg}</div>`;setTimeout(()=>{if(el)el.innerHTML='';},5000);}
}

api('/api/me').then(r=>{
  if(r.ok){currentUser=r.user;updateNav();}
  updateStats();
});
</script>
</body>
</html>'''

# ─────────────────────────────────────────────
# REQUEST HANDLER
# ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"  {args[0]} {args[1]}")

    def get_session_user(self):
        for part in self.headers.get('Cookie', '').split(';'):
            part = part.strip()
            if part.startswith('session='):
                db = get_db()
                row = db.execute('SELECT user_id FROM sessions WHERE id=?', (part[8:],)).fetchone()
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

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', len(body))
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

    def read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length:
            try: return json.loads(self.rfile.read(length))
            except: return {}
        return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        u = self.get_session_user()

        if path == '/':
            self.send_html(HTML); return

        if path == '/api/me':
            if u: self.send_json({'ok': True, 'user': {k: v for k, v in u.items() if k != 'password'}})
            else: self.send_json({'ok': False})

        # MY projects only (ones you created or accepted)
        elif path == '/api/projects/mine':
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            db = get_db()
            if u['role'] == 'client':
                rows = db.execute('SELECT * FROM projects WHERE client_id=? ORDER BY created_at DESC', (u['id'],)).fetchall()
            else:
                # Freelancer: projects they have accepted
                rows = db.execute('SELECT * FROM projects WHERE freelancer_id=? ORDER BY created_at DESC', (u['id'],)).fetchall()
            projects = []
            for row in rows:
                p = row_to_dict(row)
                ms = db.execute('SELECT * FROM milestones WHERE project_id=? ORDER BY sort_order', (p['id'],)).fetchall()
                p['milestones'] = [row_to_dict(m) for m in ms]
                p['freelancer_accepted'] = bool(p['freelancer_accepted'])
                projects.append(p)
            self.send_json({'ok': True, 'projects': projects})

        # ALL projects visible to everyone (for browse tab)
        elif path == '/api/projects/all':
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            db = get_db()
            if u['role'] == 'client':
                # Clients see all their own projects
                rows = db.execute('SELECT * FROM projects WHERE client_id=? ORDER BY created_at DESC', (u['id'],)).fetchall()
            else:
                # Freelancers see ALL projects that are open OR ones they're working on
                rows = db.execute("""SELECT * FROM projects
                    WHERE status IN ('open','pending','active')
                    ORDER BY created_at DESC""").fetchall()
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
            text += f"CLIENT:     {p['client_name']}\nFREELANCER: {p.get('freelancer_name') or p.get('freelancer_email') or '[OPEN]'}\n\n"
            text += f"PROJECT: {p['title']}\n{p.get('description','')}\n\nMILESTONES:\n"
            for i, m in enumerate(p['milestones']):
                text += f"  {i+1}. {m['title']} — ${float(m['amount']):.2f} [{m['status'].upper()}]\n"
            text += f"\nTOTAL: ${float(p['total']):.2f} | RELEASED: ${float(p['released']):.2f}\n"
            text += "\nIP transfers on full completion. Governed by platform escrow terms.\n"
            text += f"\nClient: {p['client_name']}\n"
            text += f"Freelancer: {p.get('freelancer_name') or p.get('freelancer_email','[OPEN]')} {'[DIGITALLY SIGNED]' if p['freelancer_accepted'] else '[PENDING]'}\n"
            self.send_text(text, f"contract-{pid}.txt")

        elif path.startswith('/api/milestones/') and path.endswith('/file'):
            mid = path.split('/')[3]
            db = get_db()
            ms = row_to_dict(db.execute('SELECT file_data, file_name, file_type FROM milestones WHERE id=?', (mid,)).fetchone())
            if not ms or not ms.get('file_data'):
                self.send_response(404); self.end_headers(); return
            try:
                file_bytes = base64.b64decode(ms['file_data'])
                self.send_response(200)
                self.send_header('Content-Type', ms.get('file_type') or 'application/octet-stream')
                self.send_header('Content-Disposition', f'attachment; filename="{ms.get("file_name","file")}"')
                self.send_header('Content-Length', len(file_bytes))
                self.end_headers()
                self.wfile.write(file_bytes)
            except Exception as e:
                self.send_response(500); self.end_headers()

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
                    WHERE p.freelancer_id=? OR a.project_id IS NULL ORDER BY a.time DESC LIMIT 30''', (u['id'],)).fetchall()
            self.send_json({'logs': [row_to_dict(r) for r in rows]})

        elif path == '/api/balance':
            if not u: self.send_json({'balance': 0}); return
            db = get_db()
            row = db.execute('SELECT balance FROM users WHERE id=?', (u['id'],)).fetchone()
            self.send_json({'balance': row['balance'] if row else 0})

        elif path == '/api/stats':
            db = get_db()
            active = db.execute("SELECT COUNT(*) FROM projects WHERE status IN ('active','open','pending')").fetchone()[0]
            escrow_row = db.execute("SELECT SUM(total-released) FROM projects WHERE status NOT IN ('complete','cancelled')").fetchone()
            escrow = escrow_row[0] or 0
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

        elif path == '/api/profile':
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            name = d.get('name', '').strip()
            email = d.get('email', '').lower().strip()
            if not name or not email:
                self.send_json({'ok': False, 'error': 'Name and email required'}); return
            db = get_db()
            existing = db.execute('SELECT id FROM users WHERE email=? AND id!=?', (email, u['id'])).fetchone()
            if existing:
                self.send_json({'ok': False, 'error': 'Email already in use by another account'}); return
            db.execute('UPDATE users SET name=?, email=? WHERE id=?', (name, email, u['id']))
            db.commit()
            log_activity(f"{u['name']} updated their profile", user_id=u['id'])
            self.send_json({'ok': True, 'user': {'id': u['id'], 'name': name, 'email': email, 'role': u['role']}})

        elif path == '/api/password':
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            curr = d.get('current_password', '')
            new_pass = d.get('new_password', '')
            if not curr or not new_pass:
                self.send_json({'ok': False, 'error': 'Both fields required'}); return
            if len(new_pass) < 6:
                self.send_json({'ok': False, 'error': 'Password must be at least 6 characters'}); return
            db = get_db()
            user = row_to_dict(db.execute('SELECT * FROM users WHERE id=?', (u['id'],)).fetchone())
            if user['password'] != hash_pass(curr):
                self.send_json({'ok': False, 'error': 'Current password is incorrect'}); return
            db.execute('UPDATE users SET password=? WHERE id=?', (hash_pass(new_pass), u['id']))
            db.commit()
            log_activity(f"{u['name']} changed their password", user_id=u['id'])
            self.send_json({'ok': True})

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
            if not d.get('title') or not d.get('milestones'):
                self.send_json({'ok': False, 'error': 'Title and milestones are required'}); return
            total = sum(float(ms['amount']) for ms in d['milestones'])
            db = get_db()
            bal = db.execute('SELECT balance FROM users WHERE id=?', (u['id'],)).fetchone()['balance']
            if total > bal:
                self.send_json({'ok': False, 'error': f'Insufficient balance. Need ${total:.2f}, have ${bal:.2f}'}); return
            db.execute('UPDATE users SET balance=balance-? WHERE id=?', (total, u['id']))
            pid = gen_id()
            fe = (d.get('freelancer_email') or '').lower().strip() or None
            fl = row_to_dict(db.execute('SELECT * FROM users WHERE email=?', (fe,)).fetchone()) if fe else None
            # Status: 'open' if no specific freelancer, 'pending' if assigned to someone
            status = 'pending' if fe else 'open'
            db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (pid, d['title'], d.get('description',''), u['id'], u['name'],
                 fe, fl['id'] if fl else None, fl['name'] if fl else None,
                 0, d.get('deadline'), total, 0.0, status, now()))
            for i, ms in enumerate(d['milestones']):
                db.execute('INSERT INTO milestones VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (gen_id(), pid, ms['title'], float(ms['amount']), 'pending', None, None, i, None, None, None, None))
            db.commit()
            log_activity(f"Project '{d['title']}' posted by {u['name']} — ${total:.2f} locked in escrow", pid, u['id'])
            self.send_json({'ok': True, 'project': get_project_full(pid)})

        elif path.endswith('/accept') and '/projects/' in path:
            if not u: self.send_json({'ok': False, 'error': 'Not authenticated'}); return
            pid = path.split('/')[3]
            p = get_project_full(pid)
            if not p: self.send_json({'ok': False, 'error': 'Not found'}); return
            if p.get('freelancer_id') and p['freelancer_id'] != u['id']:
                self.send_json({'ok': False, 'error': 'This project is assigned to another freelancer'}); return
            db = get_db()
            db.execute('UPDATE projects SET freelancer_accepted=1, status=?, freelancer_id=?, freelancer_name=?, freelancer_email=? WHERE id=?',
                ('active', u['id'], u['name'], u['email'], pid))
            db.commit()
            log_activity(f"{u['name']} accepted project '{p['title']}' and signed the contract", pid, u['id'])
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
            note = d.get('note', '')
            file_name = d.get('file_name')
            file_data = d.get('file_data')
            file_type = d.get('file_type')
            db.execute("""UPDATE milestones SET status='submitted', submitted_at=?,
                submission_note=?, file_name=?, file_data=?, file_type=?
                WHERE id=? AND project_id=?""",
                (now(), note, file_name, file_data, file_type, mid, pid))
            db.commit()
            ms = row_to_dict(db.execute('SELECT title FROM milestones WHERE id=?', (mid,)).fetchone())
            file_info = f" (with file: {file_name})" if file_name else ""
            log_activity(f"{u['name'] if u else 'Freelancer'} submitted '{ms['title']}' for review{file_info}", pid, u['id'] if u else None)
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
            ms = row_to_dict(db.execute('SELECT title FROM milestones WHERE id=?', (mid,)).fetchone())
            db.execute("UPDATE milestones SET status='pending', submitted_at=NULL, submission_note=NULL, file_name=NULL, file_data=NULL WHERE id=?", (mid,))
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
