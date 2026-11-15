from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
import re
import secrets
import base64
import types
from functools import wraps
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# Wispbyte .env loader
# Wispbyte Environment tab may write variables to /.env rather than exporting
# them into the process environment. Load both common locations explicitly.
# Existing non-empty hosting environment variables take precedence.
# ---------------------------------------------------------------------------
def _load_hosting_env():
    import os
    project_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.getenv("WISPBYTE_ENV_FILE", "").strip(),
        "/.env",
        os.path.join(project_dir, ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(project_dir), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(project_dir)), ".env"),
    ]
    candidates = [path for path in candidates if path]
    loaded_from = []
    for env_path in dict.fromkeys(candidates):
        try:
            if not os.path.isfile(env_path):
                continue
            with open(env_path, "r", encoding="utf-8-sig") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[7:].strip()
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if not key or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                        continue
                    # Remove matching single/double quotes and optional trailing comments.
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                        value = value[1:-1]
                    elif " #" in value:
                        value = value.split(" #", 1)[0].rstrip()
                    # Prefer a non-empty process variable; otherwise use .env value.
                    if value and not os.environ.get(key, "").strip():
                        os.environ[key] = value
            loaded_from.append(env_path)
        except (OSError, UnicodeError) as exc:
            print(f"[ENV] Could not read {env_path}: {type(exc).__name__}: {exc}", flush=True)
    return loaded_from

_ENV_FILES_LOADED = _load_hosting_env()
print(f"[ENV] Environment file(s) loaded: {len(_ENV_FILES_LOADED)}", flush=True)


app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))

basedir = os.path.abspath(os.path.dirname(__file__))
# One absolute SQLite path shared by the website and likess.py.
DATABASE_FILE = os.path.abspath(os.getenv('AUTOLIKE_DB_PATH', os.path.join(basedir, 'database.db')))
os.makedirs(os.path.dirname(DATABASE_FILE), exist_ok=True)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + DATABASE_FILE
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

OWNER_KEY = os.getenv('OWNER_KEY', 'OWNER-KEY-2026')
QR_CODE_URL = 'https://i.ibb.co/sv0dtymC/IMG-20261113-151609-027.jpg'
LOGO_URL = 'https://raw.githubusercontent.com/debasish6289199489-hash/Images/refs/heads/main/ffgroup.jpg'
REGIONS = ['IND', 'BR', 'US', 'NA', 'SAC', 'SG', 'RU', 'ID', 'TW', 'VN', 'TH', 'ME', 'PK', 'CIS', 'BD', 'EUROPE', 'EU']


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=True)
    email = db.Column(db.String(160), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    username = db.Column(db.String(80), nullable=True)
    uid = db.Column(db.String(40), nullable=True)
    region = db.Column(db.String(20), nullable=True)
    plan = db.Column(db.String(50), default='Free')
    likes_remaining = db.Column(db.Integer, default=0)
    plan_total_likes = db.Column(db.Integer, default=0)
    daily_limit = db.Column(db.Integer, default=0)
    is_owner = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)

    def is_active(self):
        return bool(self.is_owner or (self.likes_remaining or 0) > 0)


class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    price = db.Column(db.String(20), nullable=False)
    total_likes = db.Column(db.Integer, default=1000)
    likes_per_day = db.Column(db.Integer, default=220)
    duration_days = db.Column(db.Integer, nullable=True)
    description = db.Column(db.String(200))


class Purchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    uid = db.Column(db.String(40), nullable=False)
    region = db.Column(db.String(20), nullable=False)
    plan_id = db.Column(db.Integer, db.ForeignKey('plan.id'), nullable=False)
    utr = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='awaiting_payment')
    user_key = db.Column(db.String(64), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    plan = db.relationship('Plan')
    autolike_task = db.relationship('AutoLikeTask', back_populates='purchase', uselist=False)


class AutoLikeTask(db.Model):
    __tablename__ = 'autolike_task'
    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey('purchase.id'), unique=True, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    uid = db.Column(db.String(40), nullable=False, index=True)
    region = db.Column(db.String(20), nullable=False)
    target_likes = db.Column(db.Integer, nullable=False)
    remaining_likes = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='queued', nullable=False, index=True)
    source = db.Column(db.String(20), default='website', nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    purchase = db.relationship('Purchase', back_populates='autolike_task')


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    rating = db.Column(db.Integer, default=5)
    comment = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def current_user():
    user_id = session.get('user_id')
    if user_id:
        return User.query.get(user_id)
    old_key = session.get('user_key')
    if old_key:
        return User.query.filter_by(key=old_key).first()
    return None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user:
            session.clear()
            flash('Please sign in first', 'error')
            return redirect(url_for('signin'))
        session['user_id'] = user.id
        session['is_owner'] = bool(user.is_owner)
        return f(*args, **kwargs)
    return decorated


def owner_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = current_user()
        if not user or not user.is_owner:
            flash('Owner access only', 'error')
            return redirect(url_for('signin'))
        return f(*args, **kwargs)
    return decorated


def ensure_columns():
    """Migrate columns used by older SQLite installs without deleting existing data."""
    import sqlite3
    db_path = os.path.join(basedir, 'database.db')
    if not os.path.exists(db_path):
        return
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute('PRAGMA table_info(user)')
    user_cols = {row[1] for row in cur.fetchall()}
    for name, sql in [
        ('email', 'ALTER TABLE user ADD COLUMN email VARCHAR(160)'),
        ('password_hash', 'ALTER TABLE user ADD COLUMN password_hash VARCHAR(255)'),
        ('plan_total_likes', 'ALTER TABLE user ADD COLUMN plan_total_likes INTEGER DEFAULT 0'),
        ('daily_limit', 'ALTER TABLE user ADD COLUMN daily_limit INTEGER DEFAULT 0'),
        ('uid', 'ALTER TABLE user ADD COLUMN uid VARCHAR(40)'),
        ('region', 'ALTER TABLE user ADD COLUMN region VARCHAR(20)'),
    ]:
        if name not in user_cols:
            cur.execute(sql)
    cur.execute('PRAGMA table_info(purchase)')
    purchase_cols = {row[1] for row in cur.fetchall()}
    if 'user_id' not in purchase_cols:
        cur.execute('ALTER TABLE purchase ADD COLUMN user_id INTEGER')
    cur.execute('PRAGMA table_info(plan)')
    plan_cols = {row[1] for row in cur.fetchall()}
    if 'total_likes' not in plan_cols:
        cur.execute('ALTER TABLE plan ADD COLUMN total_likes INTEGER DEFAULT 1000')
    conn.commit()
    conn.close()


def new_redeem_key():
    while True:
        key = secrets.token_hex(8).upper()
        if not User.query.filter_by(key=key).first():
            return key


def mark_expired_purchases():
    now = datetime.utcnow()
    Purchase.query.filter(
        Purchase.status.in_(['awaiting_payment', 'awaiting_approval']),
        Purchase.expires_at < now
    ).update({'status': 'expired'}, synchronize_session=False)
    db.session.commit()


@app.route('/bg-music.mp3')
def bg_music():
    # Wispbyte can run the Flask process from a different working directory
    # than the directory shown in the file manager. Resolve the MP3 from
    # several safe locations instead of relying on app.root_path alone.
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bg-music.mp3'),
        os.path.join(os.getcwd(), 'bg-music.mp3'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'bg-music.mp3'),
        os.path.join(os.getcwd(), 'static', 'bg-music.mp3'),
    ]
    target = next((p for p in candidates if os.path.isfile(p)), None)
    if not target:
        return ('Music file not found', 404, {'Cache-Control': 'no-store'})
    return send_file(
        target,
        mimetype='audio/mpeg',
        conditional=True,
        etag=True,
        max_age=3600,
        download_name='bg-music.mp3',
    )

@app.route('/')
def index():
    mark_expired_purchases()
    reviews = Review.query.order_by(Review.created_at.desc()).limit(6).all()
    review_count = Review.query.count()
    review_avg = round(sum(r.rating for r in Review.query.all()) / review_count, 1) if review_count else 0
    user = current_user()
    return render_template('index.html', reviews=reviews, review_count=review_count, review_avg=review_avg, logo_url=LOGO_URL, regions=REGIONS, user=user)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'GET':
        return render_template('auth.html', mode='signup', logo_url=LOGO_URL)
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    confirm = request.form.get('confirm_password', '')
    if not username or not email or not password or not confirm:
        flash('All signup fields are required.', 'error')
        return redirect(url_for('signup'))
    if password != confirm:
        flash('Password and Confirm Password do not match.', 'error')
        return redirect(url_for('signup'))
    if len(password) < 6:
        flash('Password must be at least 6 characters.', 'error')
        return redirect(url_for('signup'))
    if User.query.filter_by(email=email).first():
        flash('Email is already registered. Please sign in.', 'error')
        return redirect(url_for('signin'))
    user = User(key=new_redeem_key(), username=username, email=email,
                password_hash=generate_password_hash(password), plan='Free',
                likes_remaining=0, plan_total_likes=0, daily_limit=0, is_owner=False)
    db.session.add(user)
    db.session.commit()
    session.clear()
    session['user_id'] = user.id
    session['username'] = user.username
    session['is_owner'] = False
    flash('Account created successfully.', 'success')
    return redirect(url_for('panel'))


@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if request.method == 'GET':
        return render_template('auth.html', mode='signin', logo_url=LOGO_URL)
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    user = User.query.filter_by(email=email).first() if email else None
    if not user or not user.password_hash or not check_password_hash(user.password_hash, password):
        flash('Invalid email or password.', 'error')
        return redirect(url_for('signin'))
    session.clear()
    session['user_id'] = user.id
    session['username'] = user.username or ('Owner' if user.is_owner else 'User')
    session['is_owner'] = bool(user.is_owner)
    return redirect(url_for('owner' if user.is_owner else 'panel'))


@app.route('/login', methods=['POST'])
def legacy_login():
    flash('Key login has been replaced by Email + Password sign in.', 'error')
    return redirect(url_for('signin'))


@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully', 'success')
    return redirect(url_for('index'))


@app.route('/panel')
@login_required
def panel():
    user = current_user()
    if not user:
        return redirect(url_for('signin'))
    plans = Plan.query.order_by(Plan.id.asc()).all()

    # Keep every approved purchase as its own history row, even when UID/plan repeat.
    purchase_filter = (Purchase.user_id == user.id)
    if user.key:
        purchase_filter = purchase_filter | (Purchase.user_key == user.key)
    approved_purchases = (
        Purchase.query.filter_by(status='approved')
        .filter(purchase_filter)
        .order_by(Purchase.created_at.desc(), Purchase.id.desc())
        .all()
    )

    return render_template('panel.html', user=user, plans=plans,
                           approved_purchases=approved_purchases,
                           logo_url=LOGO_URL, regions=REGIONS)


@app.route('/buy_plan/<int:plan_id>')
def buy_plan(plan_id):
    plan = Plan.query.get_or_404(plan_id)
    user = current_user()
    if not user or user.is_owner:
        flash('Please sign in with your user account before buying a plan.', 'error')
        return redirect(url_for('signin'))
    return render_template('payment.html', plan=plan, qr_code_url=QR_CODE_URL, logo_url=LOGO_URL, regions=REGIONS, user=user)


@app.route('/create_purchase/<int:plan_id>', methods=['POST'])
def create_purchase(plan_id):
    mark_expired_purchases()
    plan = Plan.query.get_or_404(plan_id)
    user = current_user()
    if not user or user.is_owner:
        flash('Please sign in with your user account first.', 'error')
        return redirect(url_for('signin'))

    username = request.form.get('username', '').strip()
    uid = request.form.get('uid', '').strip()
    region = request.form.get('region', '').strip().upper()
    user_key = user.key

    if not username or not uid or not region:
        flash('Username, UID and Region are required.', 'error')
        return redirect(url_for('buy_plan', plan_id=plan.id))
    if region not in REGIONS:
        flash('Invalid region selected.', 'error')
        return redirect(url_for('buy_plan', plan_id=plan.id))
    if not uid.isdigit():
        flash('UID must contain numbers only.', 'error')
        return redirect(url_for('buy_plan', plan_id=plan.id))

    purchase = Purchase(
        username=username,
        uid=uid,
        region=region,
        plan_id=plan.id,
        user_key=user_key,
        user_id=user.id,
        status='awaiting_payment',
        expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    db.session.add(purchase)
    db.session.commit()
    return redirect(url_for('payment_page', purchase_id=purchase.id))


@app.route('/payment/<int:purchase_id>')
def payment_page(purchase_id):
    purchase = Purchase.query.get_or_404(purchase_id)
    if purchase.status == 'awaiting_payment' and purchase.expires_at < datetime.utcnow():
        purchase.status = 'expired'
        db.session.commit()
    return render_template('payment.html', plan=purchase.plan, qr_code_url=QR_CODE_URL,
                           logo_url=LOGO_URL, purchase=purchase, regions=REGIONS,
                           payment_mode=True)


@app.route('/submit_utr/<int:purchase_id>', methods=['POST'])
def submit_utr(purchase_id):
    purchase = Purchase.query.get_or_404(purchase_id)
    if purchase.status != 'awaiting_payment':
        flash('This payment request is no longer active.', 'error')
        return redirect(url_for('payment_page', purchase_id=purchase.id))
    if purchase.expires_at < datetime.utcnow():
        purchase.status = 'expired'
        db.session.commit()
        flash('5-minute payment window expired. Please create a new request.', 'error')
        return redirect(url_for('index'))

    utr = request.form.get('utr', '').strip()
    if not utr:
        flash('Please enter the UTR / transaction ID.', 'error')
        return redirect(url_for('payment_page', purchase_id=purchase.id))

    purchase.utr = utr
    purchase.status = 'awaiting_approval'
    db.session.commit()
    return render_template('waiting.html', purchase=purchase, logo_url=LOGO_URL)


@app.route('/confirm_payment/<int:plan_id>', methods=['POST'])
@login_required
def confirm_payment(plan_id):
    """Compatibility endpoint: create a real payment request instead of instantly crediting likes."""
    return redirect(url_for('buy_plan', plan_id=plan_id))


@app.route('/add_review', methods=['POST'])
@login_required
def add_review():
    comment = request.form.get('comment', '').strip()
    try:
        rating = max(1, min(5, int(request.form.get('rating', 5))))
    except ValueError:
        rating = 5
    username = request.form.get('review_name', '').strip() or session.get('username', 'User')
    username = username[:80]
    if comment:
        db.session.add(Review(username=username, rating=rating, comment=comment[:500]))
        db.session.commit()
        flash('Thank you for your review!', 'success')
    return redirect(url_for('index'))


@app.route('/owner')
@login_required
@owner_required
def owner():
    mark_expired_purchases()
    users = User.query.filter_by(is_owner=False).order_by(User.created_at.desc()).all()
    plans = Plan.query.order_by(Plan.id.asc()).all()
    pending = Purchase.query.filter_by(status='awaiting_approval').order_by(Purchase.created_at.desc()).all()
    total_users = len(users)
    running_autolikes = sum(1 for u in users if u.is_active())
    total_remaining = sum((u.likes_remaining or 0) for u in users)
    review_count = Review.query.count()
    review_avg = round(sum(r.rating for r in Review.query.all()) / review_count, 1) if review_count else 0
    recent_reviews = Review.query.order_by(Review.created_at.desc()).limit(8).all()
    return render_template('owner.html', users=users, plans=plans, pending=pending,
                           total_users=total_users, running_autolikes=running_autolikes,
                           total_remaining=total_remaining, review_count=review_count,
                           review_avg=review_avg, recent_reviews=recent_reviews, logo_url=LOGO_URL)


@app.route('/owner/approve/<int:purchase_id>', methods=['POST'])
@login_required
@owner_required
def approve_purchase(purchase_id):
    purchase = Purchase.query.get_or_404(purchase_id)
    if purchase.status != 'awaiting_approval':
        flash('This payment is no longer awaiting approval.', 'error')
        return redirect(url_for('owner'))

    plan = purchase.plan
    user = User.query.filter_by(id=purchase.user_id, is_owner=False).first() if purchase.user_id else None
    if not user and purchase.user_key:
        user = User.query.filter_by(key=purchase.user_key, is_owner=False).first()

    if user:
        # Extension: preserve the exact same redeem code.
        user.username = purchase.username or user.username
        user.uid = purchase.uid or user.uid
        user.region = purchase.region or user.region
        user.plan = plan.name
        user.plan_total_likes = (user.plan_total_likes or 0) + plan.total_likes
        user.likes_remaining = (user.likes_remaining or 0) + plan.total_likes
        user.daily_limit = plan.likes_per_day
        key = user.key
        action = 'extended'
    else:
        key = new_redeem_key()
        user = User(
            key=key,
            username=purchase.username,
            uid=purchase.uid,
            region=purchase.region,
            plan=plan.name,
            likes_remaining=plan.total_likes,
            plan_total_likes=plan.total_likes,
            daily_limit=plan.likes_per_day,
            is_owner=False,
        )
        db.session.add(user)
        action = 'created'

    db.session.flush()
    purchase.user_key = key
    purchase.user_id = user.id
    purchase.status = 'approved'
    purchase.reviewed_at = datetime.utcnow()

    # One AutoLike task per approved purchase, even for repeated UID/plan.
    task = AutoLikeTask.query.filter_by(purchase_id=purchase.id).first()
    if task is None:
        db.session.add(AutoLikeTask(
            purchase_id=purchase.id,
            user_id=user.id,
            uid=purchase.uid,
            region=purchase.region,
            target_likes=int(plan.total_likes or 0),
            remaining_likes=int(plan.total_likes or 0),
            status='queued',
            source='website',
        ))
    db.session.commit()
    flash('Payment approved. Likes credited and AutoLike task queued.', 'success')
    return redirect(url_for('owner'))


@app.route('/owner/reject/<int:purchase_id>', methods=['POST'])
@login_required
@owner_required
def reject_purchase(purchase_id):
    purchase = Purchase.query.get_or_404(purchase_id)
    if purchase.status == 'awaiting_approval':
        purchase.status = 'rejected'
        purchase.reviewed_at = datetime.utcnow()
        db.session.commit()
        flash('Payment request rejected.', 'success')
    return redirect(url_for('owner'))


@app.route('/owner/create_key', methods=['POST'])
@login_required
@owner_required
def create_key():
    # Kept only for backwards compatibility; the normal flow now generates a key after payment approval.
    flash('Redeem codes are generated automatically after payment approval.', 'error')
    return redirect(url_for('owner'))


@app.route('/owner/update_plan/<int:plan_id>', methods=['POST'])
@login_required
@owner_required
def update_plan(plan_id):
    plan = Plan.query.get_or_404(plan_id)
    name = request.form.get('name', '').strip()
    price = request.form.get('price', '').strip()
    total_likes = request.form.get('total_likes', '').strip()
    likes_per_day = request.form.get('likes_per_day', '').strip()

    if not name or not price:
        flash('Plan title and price are required.', 'error')
        return redirect(url_for('owner'))
    try:
        total_likes = int(total_likes)
        likes_per_day = int(likes_per_day)
        if total_likes <= 0 or likes_per_day <= 0:
            raise ValueError
    except ValueError:
        flash('Likes values must be positive numbers.', 'error')
        return redirect(url_for('owner'))

    plan.name = name
    plan.price = price if price.startswith('₹') else f'₹{price}'
    plan.total_likes = total_likes
    plan.likes_per_day = likes_per_day
    plan.description = f'{total_likes:,} Likes • {likes_per_day} Daily'
    db.session.commit()
    flash(f'{plan.name} updated successfully.', 'success')
    return redirect(url_for('owner'))


def init_db():
    with app.app_context():
        db.create_all()
        ensure_columns()

        owner = User.query.filter_by(is_owner=True).first()
        owner_email = os.getenv('OWNER_EMAIL', 'busyravanid@gmail.com').strip().lower()
        owner_password = os.getenv('OWNER_PASSWORD', 'BUSYGFX')
        if not owner:
            owner = User(key=OWNER_KEY, username='Owner', email=owner_email,
                         password_hash=generate_password_hash(owner_password), plan='Owner', is_owner=True)
            db.session.add(owner)
        else:
            owner.email = owner_email
            owner.is_owner = True
            owner.key = owner.key or OWNER_KEY
            owner.password_hash = generate_password_hash(owner_password)

        User.query.filter(User.is_owner.is_(False), User.key.in_(['DEMO-KEY-001', 'DEMO-KEY-002'])).delete(synchronize_session=False)

        defaults = [
            ('1K Likes', '₹30', 1000),
            ('2K Likes', '₹60', 2000),
            ('3K Likes', '₹90', 3000),
            ('4K Likes', '₹120', 4000),
            ('10K Likes', '₹300', 10000),
        ]
        existing = Plan.query.order_by(Plan.id.asc()).all()
        for i, (name, price, total) in enumerate(defaults):
            if i < len(existing):
                plan = existing[i]
                # Preserve owner edits on an existing live database.
                if not plan.name:
                    plan.name = name
                if not plan.price:
                    plan.price = price
                if not plan.total_likes:
                    plan.total_likes = total
                if not plan.likes_per_day:
                    plan.likes_per_day = 220
                plan.description = f'{plan.total_likes:,} Likes • {plan.likes_per_day} Daily'
            else:
                db.session.add(Plan(name=name, price=price, total_likes=total, likes_per_day=220,
                                    description=f'{total:,} Likes • 220 Daily'))

        for extra in Plan.query.order_by(Plan.id.asc()).all()[len(defaults):]:
            db.session.delete(extra)

        try:
            db.session.execute(text('DROP TABLE IF EXISTS gift_code'))
        except Exception:
            pass

        # Do not seed fake reviews; reviews are created by real signed-in users.
        Review.query.filter(
            Review.username.in_(['Rahul_FF', 'ProGamer99']),
            Review.comment.in_([
                'Fast autolike service and simple dashboard system.',
                'Plan extension is simple and easy to track.'
            ])
        ).delete(synchronize_session=False)
        db.session.commit()
        print('✅ Database initialized!')
        print('👑 Owner Login: busyravanid@gmail.com / BUSYGFX')
        print('💳 Plans: ₹30/1K, ₹60/2K, ₹90/3K, ₹120/4K, ₹300/10K • 220 Daily')



_BOT_RUNTIME_B64 = (
    'aW1wb3J0IHRlbGVib3QKZnJvbSB0ZWxlYm90LnR5cGVzIGltcG9ydCBJbmxpbmVLZXlib2FyZE1hcmt1cCwgSW5saW5lS2V5Ym9h'
    'cmRCdXR0b24KaW1wb3J0IHJlcXVlc3RzCmltcG9ydCB0aHJlYWRpbmcKaW1wb3J0IGRhdGV0aW1lCmltcG9ydCB0aW1lCmltcG9y'
    'dCBvcwppbXBvcnQgcmUKaW1wb3J0IGpzb24KaW1wb3J0IGJhc2U2NAppbXBvcnQgemxpYgppbXBvcnQgc3FsaXRlMwoKIyAtLS0t'
    'LS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0KIyBX'
    'aXNwYnl0ZSAuZW52IGxvYWRlcgojIFdpc3BieXRlIEVudmlyb25tZW50IHRhYiBtYXkgd3JpdGUgdmFyaWFibGVzIHRvIC8uZW52'
    'IHJhdGhlciB0aGFuIGV4cG9ydGluZwojIHRoZW0gaW50byB0aGUgcHJvY2VzcyBlbnZpcm9ubWVudC4gTG9hZCBib3RoIGNvbW1v'
    'biBsb2NhdGlvbnMgZXhwbGljaXRseS4KIyBFeGlzdGluZyBub24tZW1wdHkgaG9zdGluZyBlbnZpcm9ubWVudCB2YXJpYWJsZXMg'
    'dGFrZSBwcmVjZWRlbmNlLgojIC0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0tLS0t'
    'LS0tLS0tLS0tLS0tLS0tLS0tLQpkZWYgX2xvYWRfaG9zdGluZ19lbnYoKToKICAgIGltcG9ydCBvcwogICAgY2FuZGlkYXRlcyA9'
    'IFsKICAgICAgICAiLy5lbnYiLAogICAgICAgIG9zLnBhdGguam9pbihvcy5wYXRoLmRpcm5hbWUob3MucGF0aC5hYnNwYXRoKF9f'
    'ZmlsZV9fKSksICIuZW52IiksCiAgICAgICAgb3MucGF0aC5qb2luKG9zLmdldGN3ZCgpLCAiLmVudiIpLAogICAgXQogICAgbG9h'
    'ZGVkX2Zyb20gPSBbXQogICAgZm9yIGVudl9wYXRoIGluIGRpY3QuZnJvbWtleXMoY2FuZGlkYXRlcyk6CiAgICAgICAgdHJ5Ogog'
    'ICAgICAgICAgICBpZiBub3Qgb3MucGF0aC5pc2ZpbGUoZW52X3BhdGgpOgogICAgICAgICAgICAgICAgY29udGludWUKICAgICAg'
    'ICAgICAgd2l0aCBvcGVuKGVudl9wYXRoLCAiciIsIGVuY29kaW5nPSJ1dGYtOC1zaWciKSBhcyBlbnZfZmlsZToKICAgICAgICAg'
    'ICAgICAgIGZvciByYXdfbGluZSBpbiBlbnZfZmlsZToKICAgICAgICAgICAgICAgICAgICBsaW5lID0gcmF3X2xpbmUuc3RyaXAo'
    'KQogICAgICAgICAgICAgICAgICAgIGlmIG5vdCBsaW5lIG9yIGxpbmUuc3RhcnRzd2l0aCgiIyIpOgogICAgICAgICAgICAgICAg'
    'ICAgICAgICBjb250aW51ZQogICAgICAgICAgICAgICAgICAgIGlmIGxpbmUuc3RhcnRzd2l0aCgiZXhwb3J0ICIpOgogICAgICAg'
    'ICAgICAgICAgICAgICAgICBsaW5lID0gbGluZVs3Ol0uc3RyaXAoKQogICAgICAgICAgICAgICAgICAgIGlmICI9IiBub3QgaW4g'
    'bGluZToKICAgICAgICAgICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICAgICAgICAgICAgICBrZXksIHZhbHVlID0gbGlu'
    'ZS5zcGxpdCgiPSIsIDEpCiAgICAgICAgICAgICAgICAgICAga2V5ID0ga2V5LnN0cmlwKCkKICAgICAgICAgICAgICAgICAgICB2'
    'YWx1ZSA9IHZhbHVlLnN0cmlwKCkKICAgICAgICAgICAgICAgICAgICBpZiBub3Qga2V5IG9yIG5vdCByZS5mdWxsbWF0Y2gociJb'
    'QS1aYS16X11bQS1aYS16MC05X10qIiwga2V5KToKICAgICAgICAgICAgICAgICAgICAgICAgY29udGludWUKICAgICAgICAgICAg'
    'ICAgICAgICAjIFJlbW92ZSBtYXRjaGluZyBzaW5nbGUvZG91YmxlIHF1b3RlcyBhbmQgb3B0aW9uYWwgdHJhaWxpbmcgY29tbWVu'
    'dHMuCiAgICAgICAgICAgICAgICAgICAgaWYgbGVuKHZhbHVlKSA+PSAyIGFuZCB2YWx1ZVswXSA9PSB2YWx1ZVstMV0gYW5kIHZh'
    'bHVlWzBdIGluICgiJyIsICciJyk6CiAgICAgICAgICAgICAgICAgICAgICAgIHZhbHVlID0gdmFsdWVbMTotMV0KICAgICAgICAg'
    'ICAgICAgICAgICBlbGlmICIgIyIgaW4gdmFsdWU6CiAgICAgICAgICAgICAgICAgICAgICAgIHZhbHVlID0gdmFsdWUuc3BsaXQo'
    'IiAjIiwgMSlbMF0ucnN0cmlwKCkKICAgICAgICAgICAgICAgICAgICAjIFByZWZlciBhIG5vbi1lbXB0eSBwcm9jZXNzIHZhcmlh'
    'YmxlOyBvdGhlcndpc2UgdXNlIC5lbnYgdmFsdWUuCiAgICAgICAgICAgICAgICAgICAgaWYgdmFsdWUgYW5kIG5vdCBvcy5lbnZp'
    'cm9uLmdldChrZXksICIiKS5zdHJpcCgpOgogICAgICAgICAgICAgICAgICAgICAgICBvcy5lbnZpcm9uW2tleV0gPSB2YWx1ZQog'
    'ICAgICAgICAgICBsb2FkZWRfZnJvbS5hcHBlbmQoZW52X3BhdGgpCiAgICAgICAgZXhjZXB0IChPU0Vycm9yLCBVbmljb2RlRXJy'
    'b3IpIGFzIGV4YzoKICAgICAgICAgICAgcHJpbnQoZiJbRU5WXSBDb3VsZCBub3QgcmVhZCB7ZW52X3BhdGh9OiB7dHlwZShleGMp'
    'Ll9fbmFtZV9ffToge2V4Y30iLCBmbHVzaD1UcnVlKQogICAgcmV0dXJuIGxvYWRlZF9mcm9tCgpfRU5WX0ZJTEVTX0xPQURFRCA9'
    'IF9sb2FkX2hvc3RpbmdfZW52KCkKcHJpbnQoZiJbRU5WXSBFbnZpcm9ubWVudCBmaWxlKHMpIGxvYWRlZDoge2xlbihfRU5WX0ZJ'
    'TEVTX0xPQURFRCl9IiwgZmx1c2g9VHJ1ZSkKCgojID09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQoj'
    'IPCflJAgQ09ORklHVVJBVElPTgojID09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQojIFNldCBCT1Rf'
    'VE9LRU4gaW4geW91ciBob3N0aW5nIApCT1RfVE9LRU4gPSBvcy5nZXRlbnYoIkJPVF9UT0tFTiIsICIiKS5zdHJpcCgpCmlmIG5v'
    'dCBCT1RfVE9LRU46CiAgICByYWlzZSBSdW50aW1lRXJyb3IoIkJPVF9UT0tFTiBlbnZpcm9ubWVudCB2YXJpYWJsZSBpcyByZXF1'
    'aXJlZCB0byBzdGFydCBsaWtlc3MucHkiKQpib3QgPSB0ZWxlYm90LlRlbGVCb3QoQk9UX1RPS0VOLCBwYXJzZV9tb2RlPSJIVE1M'
    'IikKCk9XTkVSX0lEID0gODk3NTUxNjU0NgoKIyBBRE1JTlMgc3RydWN0dXJlOiB7dXNlcl9pZDogeyJleHBpcmVzIjogdGltZXN0'
    'YW1wIG9yIE5vbmUsICJhdXRvbGlrZV9saW1pdCI6IGludCBvciBOb25lLCAiYXV0b2xpa2VfdXNlZCI6IGludH19CiMgTm9uZSBm'
    'b3IgZXhwaXJlcyA9IHBlcm1hbmVudCwgTm9uZSBmb3IgYXV0b2xpa2VfbGltaXQgPSB1bmxpbWl0ZWQKQURNSU5TID0gewogICAg'
    'T1dORVJfSUQ6IHsKICAgICAgICAiZXhwaXJlcyI6IE5vbmUsICAgICAgICAgICMgUGVybWFuZW50CiAgICAgICAgImF1dG9saWtl'
    'X2xpbWl0IjogTm9uZSwgICAjIFVubGltaXRlZAogICAgICAgICJhdXRvbGlrZV91c2VkIjogMAogICAgfQp9CgpkZWYgaXNfYWRt'
    'aW4odXNlcl9pZCk6CiAgICAiIiJDaGVjayBpZiB1c2VyIGlzIGFkbWluIGFuZCBub3QgZXhwaXJlZC4iIiIKICAgIGlmIHVzZXJf'
    'aWQgbm90IGluIEFETUlOUzoKICAgICAgICByZXR1cm4gRmFsc2UKICAgIGRhdGEgPSBBRE1JTlNbdXNlcl9pZF0KICAgIGV4cGly'
    'ZXMgPSBkYXRhLmdldCgiZXhwaXJlcyIpCiAgICBpZiBleHBpcmVzIGlzIG5vdCBOb25lIGFuZCB0aW1lLnRpbWUoKSA+IGV4cGly'
    'ZXM6CiAgICAgICAgIyBBdXRvIHJlbW92ZSBleHBpcmVkIGFkbWluIChleGNlcHQgb3duZXIpCiAgICAgICAgaWYgdXNlcl9pZCAh'
    'PSBPV05FUl9JRDoKICAgICAgICAgICAgZGVsIEFETUlOU1t1c2VyX2lkXQogICAgICAgIHJldHVybiBGYWxzZQogICAgcmV0dXJu'
    'IFRydWUKCmRlZiBnZXRfYWRtaW5fZGF0YSh1c2VyX2lkKToKICAgIHJldHVybiBBRE1JTlMuZ2V0KHVzZXJfaWQpCgojIEJvdGgg'
    'Z3JvdXBzIGFyZSBtYW5kYXRvcnkgZm9yIGZvcmNlLWpvaW4gdmVyaWZpY2F0aW9uLgpSRVFVSVJFRF9HUk9VUFMgPSBbCiAgICB7'
    'CiAgICAgICAgImlkIjogLTEwMDM5NDY4OTQzNzYsCiAgICAgICAgIm5hbWUiOiAiRsqA4bSH4bSHIEbJqsqA4bSHIEHhtJzhtJvh'
    'tI9MyarhtIvhtIciLAogICAgICAgICJsaW5rIjogImh0dHBzOi8vdC5tZS9BdXRvTGlrZV8yMjBEYWlseSIsCiAgICB9LApdCiMg'
    'S2VlcCB0aGUgbGVnYWN5IG5vdGlmaWNhdGlvbiB0YXJnZXQgcG9pbnRlZCBhdCB0aGUgc2Vjb25kIGdyb3VwLgpSRVFVSVJFRF9H'
    'Uk9VUF9JRCA9IC0xMDA0NDQ4NTM4OTU3CgpNQU5VQUxfTElLRV9BUEkgPSAiaHR0cHM6Ly9mcmVldXNlcjIwLnZlcmNlbC5hcHAi'
    'CkFVVE9fTElLRV9BUEkgPSAiaHR0cHM6Ly8yMjBsaWtlcy1hcGkudmVyY2VsLmFwcCIKSU1BR0VfVVJMID0gImh0dHBzOi8vbXlp'
    'bWdzLm9yZy9zdG9yYWdlL2ltYWdlcy83NDg1MS8zNTE3NC5qcGciCgojID09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09'
    'PT09PT09PT09PQojIPCfjqggUFJFTUlVTSBFTU9KSSBNQVBQSU5HIAojID09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09'
    'PT09PT09PT09PQpfRU1PSklfTUFQX0I2NCA9ICJlSnlOVlV0dUhEa012VXFnZFJiOGk4eFZFaS9zcnU0YlpEV1l1K2ZKTG5XWGhK'
    'bkIySENoWUR3V1JiNlAvbXEvZmgrcEI1NzM5NC8yNDl2UDVtSWt4aUlhS3VXRzkvYjkyNHA3Ky95SGRQS3ZFc01yVldmcjVzNDlx'
    'Tm9MTWNGMit3U0hwS1FMaWJOWHNTWkZleUhlWnFzYm5vK3M4L3NhaEZNVk8zV3hIcXB0eDcxZHozakk4MkNrN0wwek9sbDFwZDUy'
    'M0ZKNHAvdFhvWFpsNDhUNWhLT0tNdHFPV3d1VHpvN21rbGFKZFdRSXhsczdEdHhjaU9SWjB0SERzbHRtcEFYYXRSZGk2WEk3enVO'
    'WmROY1NLWXZzcGVyZWR0enNFdmJyOStOT2ozbThjbkw4U1dJWkNjTGFqanNMdWZxMVVDUXF3NnFrTUpxRGtMYmoxb1VjZkc2eUt2'
    'R3JYRG5lbGJUdHVKVTdwdWRhbEJJTm5jM0N5TEx0dURtajYxa0NYV0Y5YXFnTFk0Nm50SUJZdXp6ZWw3V3dKeW1GZEUySDFIcTFm'
    'MGFmSGZYeDdNaGR4WlZ3dklSdjhpVGlFN0Z5ZHp2RnJNR2dPZ0tjazRKaXRiYmoxc0t3cHlhSlpUQUlKYXNUVTl0eGkzMk9zdWtD'
    'aWRRWWJpT0loWDIxejhDdHZxUGp5cDFFU0lBT1o2SzI0N1laMzZkaGgrdGdBNDJ4V3RGc08yNHRyR01LMnlXMG1BejJLd2NyYmNj'
    'OUMrL2o3RjNuY2lnN3lHY0dlOXFUWDc2YnVMV1FZeFpLSlVRY1NZaVo2SmU0bTdpVDlIaW4xVXJ3ekdBOUVaSEo2TnAyM09vSTg5'
    'Zm1Iak5za1JKbENFLzh3UHJZZHZ1dm1sWERkRHRINEpGdWVNQXhQYnpUdXJTQm15TlVYVWRRNDg1Z1ZqTVI5ZmpPTlBVTHQ5SjBs'
    'NjhNSjZnQzl3UHlRQ3JVT1ZkVEQ5eXFxQnRQb3pFOFN1U0tyS1BnUzNCTjNOclJuc0tvanRzRVVrNmtWdnEycUlGYitWWEVFam9k'
    'WXhxVEpjb1FLckN0bHlpWTY5ZDEvWHZsK3ZHRHI3Uk0rRHpMeDhlNUpkS2hDOWdtNElmd2k5UC8zMWRXdW1laWppdG02TlE4WUN5'
    'd3Z0RzlKK3J0UHUwOFJvZmNFQ0dCNENHdHR1TTJPNThxaFQwZ0NjeVJQUXZoYU5KMjNCWWcvc3k2R01tcTZHMkJ5M3NQRU55L2Yv'
    'OEJMYTh6WUE9PSIgICAjIDwtLSBwYXN0ZSBoZXJlCl9CVVRUT05fTUFQX0I2NCA9ICJlSnlGems5THcwQVFCZkN2TXU3WncrenUv'
    'UFVtcGhRRXV5RTJpa2ZCRlFzbEtRRVZFYis3U1FOZVBjenAvWGh2dnNOMTA4RGRCdllGbmtyZndiWXJmUnV1SURCcGlrS21aQ1pH'
    'SXU3aEVzSjkzN2FsMjY5Q2xMT241Q1NtbmpQeklzcmpidE90T1Rrano1Y3NveG9wTGZudGVCamc1dTE1R09yeHpGSVNOeUgzNU9o'
    'em8rQWYyMDdqKytrZlZGOHU0S0ZPaDljdjJJMmY2M0JTbkZXY0Y1bWpDcDVmYitySGNUelZhU0dDbUMyaVJrVEprWVV6aFo5ZkFs'
    'VS9VZz09IiAjIDwtLSBwYXN0ZSBoZXJlCgpkZWYgX2dldF9lbW9qaV9tYXAoKToKICAgIGlmIG5vdCBoYXNhdHRyKF9nZXRfZW1v'
    'amlfbWFwLCAiY2FjaGUiKToKICAgICAgICBkYXRhID0gemxpYi5kZWNvbXByZXNzKGJhc2U2NC5iNjRkZWNvZGUoX0VNT0pJX01B'
    'UF9CNjQpKQogICAgICAgIF9nZXRfZW1vamlfbWFwLmNhY2hlID0ganNvbi5sb2FkcyhkYXRhLmRlY29kZSgndXRmLTgnKSkKICAg'
    'IHJldHVybiBfZ2V0X2Vtb2ppX21hcC5jYWNoZQoKZGVmIF9nZXRfYnV0dG9uX21hcCgpOgogICAgaWYgbm90IGhhc2F0dHIoX2dl'
    'dF9idXR0b25fbWFwLCAiY2FjaGUiKToKICAgICAgICBkYXRhID0gemxpYi5kZWNvbXByZXNzKGJhc2U2NC5iNjRkZWNvZGUoX0JV'
    'VFRPTl9NQVBfQjY0KSkKICAgICAgICBfZ2V0X2J1dHRvbl9tYXAuY2FjaGUgPSBqc29uLmxvYWRzKGRhdGEuZGVjb2RlKCd1dGYt'
    'OCcpKQogICAgcmV0dXJuIF9nZXRfYnV0dG9uX21hcC5jYWNoZQoKZGVmIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVt'
    'KHRleHQ6IHN0cikgLT4gc3RyOgogICAgaWYgbm90IHRleHQ6CiAgICAgICAgcmV0dXJuIHRleHQKICAgIGVtb2ppX21hcCA9IF9n'
    'ZXRfZW1vamlfbWFwKCkKICAgIGZvciBlbW9qaV9jaGFyIGluIHNvcnRlZChlbW9qaV9tYXAua2V5cygpLCBrZXk9bGVuLCByZXZl'
    'cnNlPVRydWUpOgogICAgICAgIGlmIGVtb2ppX2NoYXIgaW4gdGV4dDoKICAgICAgICAgICAgZWlkLCBmYWxsYmFjayA9IGVtb2pp'
    'X21hcFtlbW9qaV9jaGFyXQogICAgICAgICAgICB0YWcgPSBmJzx0Zy1lbW9qaSBlbW9qaS1pZD0ie2VpZH0iPntmYWxsYmFja308'
    'L3RnLWVtb2ppPicKICAgICAgICAgICAgdGV4dCA9IHRleHQucmVwbGFjZShlbW9qaV9jaGFyLCB0YWcpCiAgICByZXR1cm4gdGV4'
    'dAoKZGVmIHN0cmlwX2Vtb2ppcyh0ZXh0OiBzdHIpIC0+IHN0cjoKICAgICIiIlJlbW92ZSBhbGwgZW1vamkgY2hhcmFjdGVycyBm'
    'cm9tIHRleHQuIiIiCiAgICBlbW9qaV9wYXR0ZXJuID0gcmUuY29tcGlsZSgKICAgICAgICByJ1snCiAgICAgICAgcidcVTAwMDFG'
    'MUU2LVxVMDAwMUYxRkZcVTAwMDFGNjAwLVxVMDAwMUY2NEZcVTAwMDFGMzAwLVxVMDAwMUY1RkYnCiAgICAgICAgcidcVTAwMDFG'
    'NjgwLVxVMDAwMUY2RkZcVTAwMDFGNzAwLVxVMDAwMUY3N0ZcVTAwMDFGNzgwLVxVMDAwMUY3RkYnCiAgICAgICAgcidcVTAwMDFG'
    'ODAwLVxVMDAwMUY4RkZcVTAwMDFGOTAwLVxVMDAwMUY5RkZcVTAwMDFGQTcwLVxVMDAwMUZBRkYnCiAgICAgICAgcidcdTI2MDAt'
    'XHUyNkZGXHUyNzAwLVx1MjdCRlx1MjMwMC1cdTIzRkZcdUZFMEZcdTIwMEQnCiAgICAgICAgciddKycsCiAgICAgICAgZmxhZ3M9'
    'cmUuVU5JQ09ERQogICAgKQogICAgcmV0dXJuIGVtb2ppX3BhdHRlcm4uc3ViKCcnLCB0ZXh0KS5zdHJpcCgpCgpkZWYgbWFrZV9w'
    'cmVtaXVtX2J1dHRvbih0ZXh0OiBzdHIsIGNhbGxiYWNrX2RhdGE6IHN0ciA9IE5vbmUsIHVybDogc3RyID0gTm9uZSwgaWNvbl9p'
    'ZDogc3RyID0gTm9uZSwgc3R5bGU6IHN0ciA9ICJwcmltYXJ5Iik6CiAgICAiIiJDcmVhdGUgYnV0dG9uIHdpdGggcHJlbWl1bSBp'
    'Y29uIGFuZCBjb2xvciBzdHlsZS4iIiIKICAgIGNsZWFuX3RleHQgPSBzdHJpcF9lbW9qaXModGV4dCkKICAgIGt3YXJncyA9IHsi'
    'dGV4dCI6IGNsZWFuX3RleHR9CiAgICBpZiBjYWxsYmFja19kYXRhOgogICAgICAgIGt3YXJnc1siY2FsbGJhY2tfZGF0YSJdID0g'
    'Y2FsbGJhY2tfZGF0YQogICAgaWYgdXJsOgogICAgICAgIGt3YXJnc1sidXJsIl0gPSB1cmwKICAgIGlmIGljb25faWQ6CiAgICAg'
    'ICAga3dhcmdzWyJpY29uX2N1c3RvbV9lbW9qaV9pZCJdID0gaWNvbl9pZAogICAga3dhcmdzWyJzdHlsZSJdID0gc3R5bGUKICAg'
    'IHJldHVybiBJbmxpbmVLZXlib2FyZEJ1dHRvbigqKmt3YXJncykKCiMgPT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09'
    'PT09PT09PT09CiMg8J+SviBSQU0gREFUQUJBU0UgKFVOQ0hBTkdFRCkKIyA9PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09'
    'PT09PT09PT09PT0KR0xPQkFMX0xJTUlUID0gNjAKUkVHSU9OUyA9IFsiSU5EIiwgIkJSIiwgIlVTIiwgIk5BIiwgIlNBQyIsICJT'
    'RyIsICJSVSIsICJJRCIsICJUVyIsICJWTiIsICJUSCIsICJNRSIsICJQSyIsICJDSVMiLCAiQkQiLCAiRVVST1BFIiwgIkVVIl0K'
    'dG90YWxfbGlrZXNfdXNlZCA9IDAKdG90YWxfcmVxdWVzdHNfdXNlZCA9IDAKbGFzdF9yZXF1ZXN0X3RpbWUgPSAwClJFUVVFU1Rf'
    'TE9DSyA9IHRocmVhZGluZy5Mb2NrKCkKU1RBVEVfTE9DSyA9IHRocmVhZGluZy5STG9jaygpCnVzZXJzID0gc2V0KCkKQUxMT1dF'
    'RF9HUk9VUFMgPSBbLTEwMDM5NDY4OTQzNzZdCnVzZXJfc3VjY2Vzc19sb2dzID0ge30KdXNlcl9jdXN0b21fbGltaXRzID0ge30K'
    'YXV0b2xpa2VfbGlzdCA9IHt9CkJPVF9FTkFCTEVEID0gVHJ1ZSAgIyAvb24gL29mZiBzZSBjb250cm9sIGhvdGEgaGFpClNUQVRF'
    'X0ZJTEUgPSBvcy5wYXRoLmpvaW4ob3MucGF0aC5kaXJuYW1lKG9zLnBhdGguYWJzcGF0aChfX2ZpbGVfXykpLCAibGlrZXNfc3Rh'
    'dGUuanNvbiIpCgpkZWYgc2F2ZV9kYXRhKCk6CiAgICAiIiJBdG9taWNhbGx5IHBlcnNpc3QgYm90IHN0YXRlIHNvIHJlc3RhcnRz'
    'IGRvIG5vdCBlcmFzZSBhcHByb3ZhbHMvbGltaXRzLiIiIgogICAgd2l0aCBTVEFURV9MT0NLOgogICAgICAgIHN0YXRlID0gewog'
    'ICAgICAgICAgICAiYXV0b2xpa2VfbGlzdCI6IGF1dG9saWtlX2xpc3QsCiAgICAgICAgICAgICJhZG1pbnMiOiB7c3RyKGspOiB2'
    'IGZvciBrLCB2IGluIEFETUlOUy5pdGVtcygpfSwKICAgICAgICAgICAgImFsbG93ZWRfZ3JvdXBzIjogQUxMT1dFRF9HUk9VUFMs'
    'CiAgICAgICAgICAgICJ1c2VycyI6IHNvcnRlZCh1c2VycyksCiAgICAgICAgICAgICJ1c2VyX3N1Y2Nlc3NfbG9ncyI6IHVzZXJf'
    'c3VjY2Vzc19sb2dzLAogICAgICAgICAgICAidXNlcl9jdXN0b21fbGltaXRzIjogdXNlcl9jdXN0b21fbGltaXRzLAogICAgICAg'
    'ICAgICAiYm90X2VuYWJsZWQiOiBCT1RfRU5BQkxFRCwKICAgICAgICAgICAgImdsb2JhbF9saW1pdCI6IEdMT0JBTF9MSU1JVCwK'
    'ICAgICAgICAgICAgInRvdGFsX2xpa2VzX3VzZWQiOiB0b3RhbF9saWtlc191c2VkLAogICAgICAgICAgICAidG90YWxfcmVxdWVz'
    'dHNfdXNlZCI6IHRvdGFsX3JlcXVlc3RzX3VzZWQsCiAgICAgICAgICAgICJsaWtlc19hcHByb3ZhbHMiOiB7c3RyKGspOiB2IGZv'
    'ciBrLCB2IGluIExJS0VTX0FQUFJPVkFMUy5pdGVtcygpfSwKICAgICAgICAgICAgImxpa2VzX2FsbG93ZWRfZ3JvdXBzIjoge3N0'
    'cihrKTogdiBmb3IgaywgdiBpbiBMSUtFU19BTExPV0VEX0dST1VQUy5pdGVtcygpfSwKICAgICAgICAgICAgIm1hbnVhbF9saWtl'
    'X2FwaSI6IE1BTlVBTF9MSUtFX0FQSSwKICAgICAgICAgICAgImF1dG9fbGlrZV9hcGkiOiBBVVRPX0xJS0VfQVBJLAogICAgICAg'
    'IH0KICAgICAgICB0bXBfcGF0aCA9IFNUQVRFX0ZJTEUgKyAiLnRtcCIKICAgICAgICB0cnk6CiAgICAgICAgICAgIHdpdGggb3Bl'
    'bih0bXBfcGF0aCwgInciLCBlbmNvZGluZz0idXRmLTgiKSBhcyBmOgogICAgICAgICAgICAgICAganNvbi5kdW1wKHN0YXRlLCBm'
    'LCBlbnN1cmVfYXNjaWk9RmFsc2UsIGluZGVudD0yKQogICAgICAgICAgICBvcy5yZXBsYWNlKHRtcF9wYXRoLCBTVEFURV9GSUxF'
    'KQogICAgICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZXhjOgogICAgICAgICAgICBwcmludChmIltTdGF0ZSBTYXZlIEVycm9yXSB7'
    'ZXhjfSIpCiAgICAgICAgICAgIHRyeToKICAgICAgICAgICAgICAgIGlmIG9zLnBhdGguZXhpc3RzKHRtcF9wYXRoKTogb3MucmVt'
    'b3ZlKHRtcF9wYXRoKQogICAgICAgICAgICBleGNlcHQgT1NFcnJvcjoKICAgICAgICAgICAgICAgIHBhc3MKCmRlZiBsb2FkX2Rh'
    'dGEoKToKICAgICIiIlJlc3RvcmUgcGVyc2lzdGVkIGJvdCBzdGF0ZTsgbWFsZm9ybWVkIHN0YXRlIGlzIGlnbm9yZWQgc2FmZWx5'
    'LiIiIgogICAgZ2xvYmFsIGF1dG9saWtlX2xpc3QsIEFETUlOUywgQUxMT1dFRF9HUk9VUFMsIEJPVF9FTkFCTEVELCBHTE9CQUxf'
    'TElNSVQKICAgIGdsb2JhbCB0b3RhbF9saWtlc191c2VkLCB0b3RhbF9yZXF1ZXN0c191c2VkLCBNQU5VQUxfTElLRV9BUEksIEFV'
    'VE9fTElLRV9BUEkKICAgIHRyeToKICAgICAgICB3aXRoIG9wZW4oU1RBVEVfRklMRSwgInIiLCBlbmNvZGluZz0idXRmLTgiKSBh'
    'cyBmOgogICAgICAgICAgICBzdGF0ZSA9IGpzb24ubG9hZChmKQogICAgICAgIGF1dG9saWtlX2xpc3QgPSBzdGF0ZS5nZXQoImF1'
    'dG9saWtlX2xpc3QiLCB7fSkgaWYgaXNpbnN0YW5jZShzdGF0ZS5nZXQoImF1dG9saWtlX2xpc3QiLCB7fSksIGRpY3QpIGVsc2Ug'
    'e30KICAgICAgICBBRE1JTlMgPSB7aW50KGspOiB2IGZvciBrLCB2IGluIHN0YXRlLmdldCgiYWRtaW5zIiwge30pLml0ZW1zKCl9'
    'CiAgICAgICAgQURNSU5TLnNldGRlZmF1bHQoT1dORVJfSUQsIHsiZXhwaXJlcyI6IE5vbmUsICJhdXRvbGlrZV9saW1pdCI6IE5v'
    'bmUsICJhdXRvbGlrZV91c2VkIjogMH0pCiAgICAgICAgQUxMT1dFRF9HUk9VUFMgPSBbaW50KHgpIGZvciB4IGluIHN0YXRlLmdl'
    'dCgiYWxsb3dlZF9ncm91cHMiLCBBTExPV0VEX0dST1VQUyldCiAgICAgICAgdXNlcnMudXBkYXRlKGludCh4KSBmb3IgeCBpbiBz'
    'dGF0ZS5nZXQoInVzZXJzIiwgW10pKQogICAgICAgIHVzZXJfc3VjY2Vzc19sb2dzLnVwZGF0ZShzdGF0ZS5nZXQoInVzZXJfc3Vj'
    'Y2Vzc19sb2dzIiwge30pKQogICAgICAgIHVzZXJfY3VzdG9tX2xpbWl0cy51cGRhdGUoc3RhdGUuZ2V0KCJ1c2VyX2N1c3RvbV9s'
    'aW1pdHMiLCB7fSkpCiAgICAgICAgQk9UX0VOQUJMRUQgPSBib29sKHN0YXRlLmdldCgiYm90X2VuYWJsZWQiLCBCT1RfRU5BQkxF'
    'RCkpCiAgICAgICAgR0xPQkFMX0xJTUlUID0gaW50KHN0YXRlLmdldCgiZ2xvYmFsX2xpbWl0IiwgR0xPQkFMX0xJTUlUKSkKICAg'
    'ICAgICB0b3RhbF9saWtlc191c2VkID0gaW50KHN0YXRlLmdldCgidG90YWxfbGlrZXNfdXNlZCIsIHRvdGFsX2xpa2VzX3VzZWQp'
    'KQogICAgICAgIHRvdGFsX3JlcXVlc3RzX3VzZWQgPSBpbnQoc3RhdGUuZ2V0KCJ0b3RhbF9yZXF1ZXN0c191c2VkIiwgdG90YWxf'
    'cmVxdWVzdHNfdXNlZCkpCiAgICAgICAgTElLRVNfQVBQUk9WQUxTLnVwZGF0ZSh7aW50KGspOiB2IGZvciBrLCB2IGluIHN0YXRl'
    'LmdldCgibGlrZXNfYXBwcm92YWxzIiwge30pLml0ZW1zKCl9KQogICAgICAgIExJS0VTX0FMTE9XRURfR1JPVVBTLnVwZGF0ZSh7'
    'aW50KGspOiBzdHIodikgZm9yIGssIHYgaW4gc3RhdGUuZ2V0KCJsaWtlc19hbGxvd2VkX2dyb3VwcyIsIHt9KS5pdGVtcygpfSkK'
    'ICAgICAgICBNQU5VQUxfTElLRV9BUEkgPSBzdGF0ZS5nZXQoIm1hbnVhbF9saWtlX2FwaSIsIE1BTlVBTF9MSUtFX0FQSSkKICAg'
    'ICAgICBBVVRPX0xJS0VfQVBJID0gc3RhdGUuZ2V0KCJhdXRvX2xpa2VfYXBpIiwgQVVUT19MSUtFX0FQSSkKICAgIGV4Y2VwdCBG'
    'aWxlTm90Rm91bmRFcnJvcjoKICAgICAgICBwYXNzCiAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGV4YzoKICAgICAgICBwcmludChm'
    'IltTdGF0ZSBMb2FkIEVycm9yXSB7ZXhjfTsgc3RhcnRpbmcgd2l0aCBkZWZhdWx0cyIpCgpEQVRBQkFTRV9GSUxFID0gb3MucGF0'
    'aC5hYnNwYXRoKG9zLmdldGVudigiQVVUT0xJS0VfREJfUEFUSCIsIG9zLnBhdGguam9pbihvcy5wYXRoLmRpcm5hbWUob3MucGF0'
    'aC5hYnNwYXRoKF9fZmlsZV9fKSksICJkYXRhYmFzZS5kYiIpKSkKCgpkZWYgZW5zdXJlX2F1dG9saWtlX3RhYmxlKCk6CiAgICAi'
    'IiJDcmVhdGUgdGhlIHNoYXJlZCB0YXNrIHRhYmxlIGlmIHRoZSB3ZWJzaXRlIGhhcyBub3QgY3JlYXRlZCBpdCB5ZXQuIiIiCiAg'
    'ICB3aXRoIHNxbGl0ZTMuY29ubmVjdChEQVRBQkFTRV9GSUxFLCB0aW1lb3V0PTMwKSBhcyBjb25uOgogICAgICAgIGNvbm4uZXhl'
    'Y3V0ZSgiIiIKICAgICAgICAgICAgQ1JFQVRFIFRBQkxFIElGIE5PVCBFWElTVFMgYXV0b2xpa2VfdGFzayAoCiAgICAgICAgICAg'
    'ICAgICBpZCBJTlRFR0VSIFBSSU1BUlkgS0VZIEFVVE9JTkNSRU1FTlQsCiAgICAgICAgICAgICAgICBwdXJjaGFzZV9pZCBJTlRF'
    'R0VSIFVOSVFVRSwKICAgICAgICAgICAgICAgIHVzZXJfaWQgSU5URUdFUiwKICAgICAgICAgICAgICAgIHVpZCBWQVJDSEFSKDQw'
    'KSBOT1QgTlVMTCwKICAgICAgICAgICAgICAgIHJlZ2lvbiBWQVJDSEFSKDIwKSBOT1QgTlVMTCwKICAgICAgICAgICAgICAgIHRh'
    'cmdldF9saWtlcyBJTlRFR0VSIE5PVCBOVUxMLAogICAgICAgICAgICAgICAgcmVtYWluaW5nX2xpa2VzIElOVEVHRVIgTk9UIE5V'
    'TEwsCiAgICAgICAgICAgICAgICBzdGF0dXMgVkFSQ0hBUigyMCkgTk9UIE5VTEwgREVGQVVMVCAncXVldWVkJywKICAgICAgICAg'
    'ICAgICAgIHNvdXJjZSBWQVJDSEFSKDIwKSBOT1QgTlVMTCBERUZBVUxUICdtYW51YWwnLAogICAgICAgICAgICAgICAgY3JlYXRl'
    'ZF9hdCBEQVRFVElNRSBERUZBVUxUIENVUlJFTlRfVElNRVNUQU1QLAogICAgICAgICAgICAgICAgdXBkYXRlZF9hdCBEQVRFVElN'
    'RSBERUZBVUxUIENVUlJFTlRfVElNRVNUQU1QCiAgICAgICAgICAgICkKICAgICAgICAiIiIpCiAgICAgICAgY29ubi5jb21taXQo'
    'KQoKCmRlZiBsb2FkX2RiKCk6CiAgICAiIiJSZWFkIGFjdGl2ZSB0YXNrcyBmcm9tIHRoZSBzYW1lIFNRTGl0ZSBkYXRhYmFzZSB1'
    'c2VkIGJ5IHRoZSB3ZWJzaXRlLiIiIgogICAgZW5zdXJlX2F1dG9saWtlX3RhYmxlKCkKICAgIHdpdGggc3FsaXRlMy5jb25uZWN0'
    'KERBVEFCQVNFX0ZJTEUsIHRpbWVvdXQ9MzApIGFzIGNvbm46CiAgICAgICAgY29ubi5yb3dfZmFjdG9yeSA9IHNxbGl0ZTMuUm93'
    'CiAgICAgICAgcm93cyA9IGNvbm4uZXhlY3V0ZSgKICAgICAgICAgICAgIlNFTEVDVCAqIEZST00gYXV0b2xpa2VfdGFzayBXSEVS'
    'RSBzdGF0dXMgSU4gKCdxdWV1ZWQnLCdydW5uaW5nJykgQU5EIHJlbWFpbmluZ19saWtlcyA+IDAgT1JERVIgQlkgaWQiCiAgICAg'
    'ICAgKS5mZXRjaGFsbCgpCiAgICB0YXNrcyA9IHt9CiAgICBmb3Igcm93IGluIHJvd3M6CiAgICAgICAgdGFza3Nbc3RyKHJvd1sn'
    'aWQnXSldID0gewogICAgICAgICAgICAndWlkJzogc3RyKHJvd1sndWlkJ10pLAogICAgICAgICAgICAncmVnaW9uJzogc3RyKHJv'
    'd1sncmVnaW9uJ10pLAogICAgICAgICAgICAndGFyZ2V0X2xpa2VzJzogaW50KHJvd1sndGFyZ2V0X2xpa2VzJ10pLAogICAgICAg'
    'ICAgICAncmVtYWluaW5nX2xpa2VzJzogaW50KHJvd1sncmVtYWluaW5nX2xpa2VzJ10pLAogICAgICAgICAgICAncHVyY2hhc2Vf'
    'aWQnOiByb3dbJ3B1cmNoYXNlX2lkJ10sCiAgICAgICAgICAgICd1c2VyX2lkJzogcm93Wyd1c2VyX2lkJ10sCiAgICAgICAgICAg'
    'ICdzb3VyY2UnOiByb3dbJ3NvdXJjZSddLAogICAgICAgIH0KICAgIHJldHVybiB7J2F1dG9saWtlX2xpc3QnOiB0YXNrc30KCgpk'
    'ZWYgYWRkX2F1dG9saWtlX3Rhc2sodWlkLCByZWdpb24sIHRhcmdldCwgc291cmNlPSdtYW51YWwnLCBwdXJjaGFzZV9pZD1Ob25l'
    'LCB1c2VyX2lkPU5vbmUpOgogICAgZW5zdXJlX2F1dG9saWtlX3RhYmxlKCkKICAgIHdpdGggc3FsaXRlMy5jb25uZWN0KERBVEFC'
    'QVNFX0ZJTEUsIHRpbWVvdXQ9MzApIGFzIGNvbm46CiAgICAgICAgY3VyID0gY29ubi5leGVjdXRlKAogICAgICAgICAgICAiIiJJ'
    'TlNFUlQgSU5UTyBhdXRvbGlrZV90YXNrCiAgICAgICAgICAgICAgIChwdXJjaGFzZV9pZCwgdXNlcl9pZCwgdWlkLCByZWdpb24s'
    'IHRhcmdldF9saWtlcywgcmVtYWluaW5nX2xpa2VzLCBzdGF0dXMsIHNvdXJjZSwgY3JlYXRlZF9hdCwgdXBkYXRlZF9hdCkKICAg'
    'ICAgICAgICAgICAgVkFMVUVTICg/LCA/LCA/LCA/LCA/LCA/LCAncXVldWVkJywgPywgQ1VSUkVOVF9USU1FU1RBTVAsIENVUlJF'
    'TlRfVElNRVNUQU1QKSIiIiwKICAgICAgICAgICAgKHB1cmNoYXNlX2lkLCB1c2VyX2lkLCBzdHIodWlkKSwgc3RyKHJlZ2lvbiku'
    'dXBwZXIoKSwgaW50KHRhcmdldCksIGludCh0YXJnZXQpLCBzb3VyY2UpCiAgICAgICAgKQogICAgICAgIGNvbm4uY29tbWl0KCkK'
    'ICAgICAgICByZXR1cm4gY3VyLmxhc3Ryb3dpZAoKCmRlZiByZW1vdmVfYXV0b2xpa2VfdGFza3ModWlkKToKICAgIGVuc3VyZV9h'
    'dXRvbGlrZV90YWJsZSgpCiAgICB3aXRoIHNxbGl0ZTMuY29ubmVjdChEQVRBQkFTRV9GSUxFLCB0aW1lb3V0PTMwKSBhcyBjb25u'
    'OgogICAgICAgIGN1ciA9IGNvbm4uZXhlY3V0ZSgiREVMRVRFIEZST00gYXV0b2xpa2VfdGFzayBXSEVSRSB1aWQgPSA/IEFORCBz'
    'dGF0dXMgIT0gJ2NvbXBsZXRlZCciLCAoc3RyKHVpZCksKSkKICAgICAgICBjb25uLmNvbW1pdCgpCiAgICAgICAgcmV0dXJuIGN1'
    'ci5yb3djb3VudAoKCmRlZiBleHRlbmRfYXV0b2xpa2VfdGFzayh1aWQsIHJlZ2lvbiwgYWRkX2xpa2VzKToKICAgICIiIkluY3Jl'
    'YXNlIHRoZSB0YXJnZXQgb2YgdGhlIG5ld2VzdCBhY3RpdmUgVUlEK3JlZ2lvbiB0YXNrIHdpdGhvdXQgcmVzZXR0aW5nIGl0cyBy'
    'ZW1haW5pbmcgY291bnQuCgogICAgSWYgbm8gYWN0aXZlIHRhc2sgZXhpc3RzLCBjcmVhdGUgYSBmcmVzaCB0YXNrIHdpdGggYWRk'
    'X2xpa2VzIHJlbWFpbmluZy4KICAgIEZvciB3ZWJzaXRlLWxpbmtlZCB0YXNrcywgYWxzbyBjcmVkaXQgdGhlIGxpbmtlZCBkYXNo'
    'Ym9hcmQgd2FsbGV0IGJ5IGFkZF9saWtlcy4KICAgICIiIgogICAgZW5zdXJlX2F1dG9saWtlX3RhYmxlKCkKICAgIHVpZCA9IHN0'
    'cih1aWQpCiAgICByZWdpb24gPSBzdHIocmVnaW9uKS51cHBlcigpCiAgICBhZGRfbGlrZXMgPSBpbnQoYWRkX2xpa2VzKQogICAg'
    'd2l0aCBzcWxpdGUzLmNvbm5lY3QoREFUQUJBU0VfRklMRSwgdGltZW91dD0zMCkgYXMgY29ubjoKICAgICAgICBjb25uLnJvd19m'
    'YWN0b3J5ID0gc3FsaXRlMy5Sb3cKICAgICAgICBjb25uLmV4ZWN1dGUoJ0JFR0lOIElNTUVESUFURScpCiAgICAgICAgcm93ID0g'
    'Y29ubi5leGVjdXRlKAogICAgICAgICAgICAiU0VMRUNUIGlkLCB0YXJnZXRfbGlrZXMsIHJlbWFpbmluZ19saWtlcywgdXNlcl9p'
    'ZCBGUk9NIGF1dG9saWtlX3Rhc2sgIgogICAgICAgICAgICAiV0hFUkUgdWlkPT8gQU5EIHJlZ2lvbj0/ICIKICAgICAgICAgICAg'
    'Ik9SREVSIEJZIENBU0UgV0hFTiBzdGF0dXMgSU4gKCdxdWV1ZWQnLCdydW5uaW5nJykgQU5EIHJlbWFpbmluZ19saWtlcyA+IDAg'
    'VEhFTiAwIEVMU0UgMSBFTkQsIGlkIERFU0MgTElNSVQgMSIsCiAgICAgICAgICAgICh1aWQsIHJlZ2lvbikKICAgICAgICApLmZl'
    'dGNob25lKCkKICAgICAgICBpZiByb3cgaXMgTm9uZToKICAgICAgICAgICAgY3VyID0gY29ubi5leGVjdXRlKAogICAgICAgICAg'
    'ICAgICAgIklOU0VSVCBJTlRPIGF1dG9saWtlX3Rhc2sgKHVpZCwgcmVnaW9uLCB0YXJnZXRfbGlrZXMsIHJlbWFpbmluZ19saWtl'
    'cywgc3RhdHVzLCBzb3VyY2UsIGNyZWF0ZWRfYXQsIHVwZGF0ZWRfYXQpICIKICAgICAgICAgICAgICAgICJWQUxVRVMgKD8sID8s'
    'ID8sID8sICdxdWV1ZWQnLCAndGVsZWdyYW1fZXh0ZW5kJywgQ1VSUkVOVF9USU1FU1RBTVAsIENVUlJFTlRfVElNRVNUQU1QKSIs'
    'CiAgICAgICAgICAgICAgICAodWlkLCByZWdpb24sIGFkZF9saWtlcywgYWRkX2xpa2VzKQogICAgICAgICAgICApCiAgICAgICAg'
    'ICAgIHRhc2tfaWQgPSBjdXIubGFzdHJvd2lkCiAgICAgICAgICAgIG5ld190YXJnZXQsIG5ld19yZW1haW5pbmcsIHVzZXJfaWQg'
    'PSBhZGRfbGlrZXMsIGFkZF9saWtlcywgTm9uZQogICAgICAgICAgICBhY3Rpb24gPSAnY3JlYXRlZCcKICAgICAgICBlbHNlOgog'
    'ICAgICAgICAgICB0YXNrX2lkID0gaW50KHJvd1snaWQnXSkKICAgICAgICAgICAgbmV3X3RhcmdldCA9IGludChyb3dbJ3Rhcmdl'
    'dF9saWtlcyddIG9yIDApICsgYWRkX2xpa2VzCiAgICAgICAgICAgIG5ld19yZW1haW5pbmcgPSBpbnQocm93WydyZW1haW5pbmdf'
    'bGlrZXMnXSBvciAwKSArIGFkZF9saWtlcwogICAgICAgICAgICB1c2VyX2lkID0gcm93Wyd1c2VyX2lkJ10KICAgICAgICAgICAg'
    'Y29ubi5leGVjdXRlKAogICAgICAgICAgICAgICAgIlVQREFURSBhdXRvbGlrZV90YXNrIFNFVCB0YXJnZXRfbGlrZXM9PywgcmVt'
    'YWluaW5nX2xpa2VzPT8sIHN0YXR1cz0ncXVldWVkJywgdXBkYXRlZF9hdD1DVVJSRU5UX1RJTUVTVEFNUCBXSEVSRSBpZD0/IiwK'
    'ICAgICAgICAgICAgICAgIChuZXdfdGFyZ2V0LCBuZXdfcmVtYWluaW5nLCB0YXNrX2lkKQogICAgICAgICAgICApCiAgICAgICAg'
    'ICAgIGlmIHVzZXJfaWQgaXMgbm90IE5vbmU6CiAgICAgICAgICAgICAgICBjb25uLmV4ZWN1dGUoCiAgICAgICAgICAgICAgICAg'
    'ICAgIlVQREFURSB1c2VyIFNFVCBsaWtlc19yZW1haW5pbmc9Q09BTEVTQ0UobGlrZXNfcmVtYWluaW5nLDApKz8sICIKICAgICAg'
    'ICAgICAgICAgICAgICAicGxhbl90b3RhbF9saWtlcz1DT0FMRVNDRShwbGFuX3RvdGFsX2xpa2VzLDApKz8gV0hFUkUgaWQ9PyIs'
    'CiAgICAgICAgICAgICAgICAgICAgKGFkZF9saWtlcywgYWRkX2xpa2VzLCBpbnQodXNlcl9pZCkpCiAgICAgICAgICAgICAgICAp'
    'CiAgICAgICAgICAgIGFjdGlvbiA9ICdleHRlbmRlZCcKICAgICAgICBjb25uLmNvbW1pdCgpCiAgICByZXR1cm4gdGFza19pZCwg'
    'YWN0aW9uLCBuZXdfcmVtYWluaW5nLCBuZXdfdGFyZ2V0LCB1c2VyX2lkCgoKZGVmIHVwZGF0ZV90YXNrX3Byb2dyZXNzKHRhc2tf'
    'aWQsIGxpa2VzX2dpdmVuKToKICAgICIiIkFwcGx5IGFjdHVhbCBBUEkgbGlrZXMgdG8gdGhpcyBwdXJjaGFzZSBhbmQgdGhlIGxp'
    'bmtlZCB3ZWJzaXRlIHdhbGxldCBhdG9taWNhbGx5LiIiIgogICAgZW5zdXJlX2F1dG9saWtlX3RhYmxlKCkKICAgIHdpdGggc3Fs'
    'aXRlMy5jb25uZWN0KERBVEFCQVNFX0ZJTEUsIHRpbWVvdXQ9MzApIGFzIGNvbm46CiAgICAgICAgY29ubi5yb3dfZmFjdG9yeSA9'
    'IHNxbGl0ZTMuUm93CiAgICAgICAgY29ubi5leGVjdXRlKCdCRUdJTiBJTU1FRElBVEUnKQogICAgICAgIHJvdyA9IGNvbm4uZXhl'
    'Y3V0ZSgKICAgICAgICAgICAgIlNFTEVDVCByZW1haW5pbmdfbGlrZXMsIHVzZXJfaWQgRlJPTSBhdXRvbGlrZV90YXNrIFdIRVJF'
    'IGlkID0gPyIsIChpbnQodGFza19pZCksKQogICAgICAgICkuZmV0Y2hvbmUoKQogICAgICAgIGlmIHJvdyBpcyBOb25lOgogICAg'
    'ICAgICAgICBjb25uLnJvbGxiYWNrKCkKICAgICAgICAgICAgcmV0dXJuIDAsIDAKICAgICAgICB0YXNrX3JlbWFpbmluZyA9IG1h'
    'eCgwLCBpbnQocm93WydyZW1haW5pbmdfbGlrZXMnXSBvciAwKSkKICAgICAgICBhcHBsaWVkID0gbWluKG1heCgwLCBpbnQobGlr'
    'ZXNfZ2l2ZW4pKSwgdGFza19yZW1haW5pbmcpCiAgICAgICAgdXNlcl9pZCA9IHJvd1sndXNlcl9pZCddCiAgICAgICAgaWYgdXNl'
    'cl9pZCBpcyBub3QgTm9uZToKICAgICAgICAgICAgdXNlciA9IGNvbm4uZXhlY3V0ZSgiU0VMRUNUIGxpa2VzX3JlbWFpbmluZyBG'
    'Uk9NIHVzZXIgV0hFUkUgaWQgPSA/IiwgKGludCh1c2VyX2lkKSwpKS5mZXRjaG9uZSgpCiAgICAgICAgICAgIGlmIHVzZXIgaXMg'
    'bm90IE5vbmU6CiAgICAgICAgICAgICAgICB3YWxsZXRfcmVtYWluaW5nID0gbWF4KDAsIGludCh1c2VyWydsaWtlc19yZW1haW5p'
    'bmcnXSBvciAwKSkKICAgICAgICAgICAgICAgIGFwcGxpZWQgPSBtaW4oYXBwbGllZCwgd2FsbGV0X3JlbWFpbmluZykKICAgICAg'
    'ICAgICAgICAgIGNvbm4uZXhlY3V0ZSgKICAgICAgICAgICAgICAgICAgICAiVVBEQVRFIHVzZXIgU0VUIGxpa2VzX3JlbWFpbmlu'
    'ZyA9IE1BWCgwLCBDT0FMRVNDRShsaWtlc19yZW1haW5pbmcsMCkgLSA/KSBXSEVSRSBpZCA9ID8iLAogICAgICAgICAgICAgICAg'
    'ICAgIChhcHBsaWVkLCBpbnQodXNlcl9pZCkpCiAgICAgICAgICAgICAgICApCiAgICAgICAgbmV3X3JlbWFpbmluZyA9IG1heCgw'
    'LCB0YXNrX3JlbWFpbmluZyAtIGFwcGxpZWQpCiAgICAgICAgbmV3X3N0YXR1cyA9ICdjb21wbGV0ZWQnIGlmIG5ld19yZW1haW5p'
    'bmcgPD0gMCBlbHNlICdxdWV1ZWQnCiAgICAgICAgY29ubi5leGVjdXRlKAogICAgICAgICAgICAiVVBEQVRFIGF1dG9saWtlX3Rh'
    'c2sgU0VUIHJlbWFpbmluZ19saWtlcyA9ID8sIHN0YXR1cyA9ID8sIHVwZGF0ZWRfYXQgPSBDVVJSRU5UX1RJTUVTVEFNUCBXSEVS'
    'RSBpZCA9ID8iLAogICAgICAgICAgICAobmV3X3JlbWFpbmluZywgbmV3X3N0YXR1cywgaW50KHRhc2tfaWQpKQogICAgICAgICkK'
    'ICAgICAgICBjb25uLmNvbW1pdCgpCiAgICAgICAgcmV0dXJuIG5ld19yZW1haW5pbmcsIGFwcGxpZWQKCgpkZWYgbWlncmF0ZV9s'
    'ZWdhY3lfYXV0b2xpa2VfdGFza3MoKToKICAgICIiIlByZXNlcnZlIHRhc2tzIGZyb20gdGhlIG9sZGVyIGxpa2VzX3N0YXRlLmpz'
    'b24gZm9ybWF0IHdpdGhvdXQgVUlEIGNvbGxpc2lvbnMuIiIiCiAgICBlbnN1cmVfYXV0b2xpa2VfdGFibGUoKQogICAgaWYgbm90'
    'IGlzaW5zdGFuY2UoYXV0b2xpa2VfbGlzdCwgZGljdCk6CiAgICAgICAgcmV0dXJuCiAgICBmb3Igb2xkX3VpZCwgaW5mbyBpbiBs'
    'aXN0KGF1dG9saWtlX2xpc3QuaXRlbXMoKSk6CiAgICAgICAgdHJ5OgogICAgICAgICAgICB1aWQgPSBzdHIob2xkX3VpZCkKICAg'
    'ICAgICAgICAgcmVnaW9uID0gc3RyKGluZm8uZ2V0KCdyZWdpb24nLCAnSU5EJykpLnVwcGVyKCkKICAgICAgICAgICAgdGFyZ2V0'
    'ID0gaW50KGluZm8uZ2V0KCd0YXJnZXRfbGlrZXMnLCAwKSkKICAgICAgICAgICAgcmVtYWluaW5nID0gaW50KGluZm8uZ2V0KCdy'
    'ZW1haW5pbmdfbGlrZXMnLCB0YXJnZXQpKQogICAgICAgICAgICBpZiB1aWQuaXNkaWdpdCgpIGFuZCByZWdpb24gaW4gUkVHSU9O'
    'UyBhbmQgdGFyZ2V0ID4gMDoKICAgICAgICAgICAgICAgIHdpdGggc3FsaXRlMy5jb25uZWN0KERBVEFCQVNFX0ZJTEUsIHRpbWVv'
    'dXQ9MzApIGFzIGNvbm46CiAgICAgICAgICAgICAgICAgICAgY29ubi5leGVjdXRlKAogICAgICAgICAgICAgICAgICAgICAgICAi'
    'IiJJTlNFUlQgSU5UTyBhdXRvbGlrZV90YXNrCiAgICAgICAgICAgICAgICAgICAgICAgICAgICh1aWQsIHJlZ2lvbiwgdGFyZ2V0'
    'X2xpa2VzLCByZW1haW5pbmdfbGlrZXMsIHN0YXR1cywgc291cmNlLCBjcmVhdGVkX2F0LCB1cGRhdGVkX2F0KQogICAgICAgICAg'
    'ICAgICAgICAgICAgICAgICBTRUxFQ1QgPywgPywgPywgPywgJ3F1ZXVlZCcsICdsZWdhY3knLCBDVVJSRU5UX1RJTUVTVEFNUCwg'
    'Q1VSUkVOVF9USU1FU1RBTVAKICAgICAgICAgICAgICAgICAgICAgICAgICAgV0hFUkUgTk9UIEVYSVNUUyAoCiAgICAgICAgICAg'
    'ICAgICAgICAgICAgICAgICAgICBTRUxFQ1QgMSBGUk9NIGF1dG9saWtlX3Rhc2sgV0hFUkUgdWlkPT8gQU5EIHJlZ2lvbj0/IEFO'
    'RCB0YXJnZXRfbGlrZXM9PyBBTkQgcmVtYWluaW5nX2xpa2VzPT8KICAgICAgICAgICAgICAgICAgICAgICAgICAgKSIiIiwKICAg'
    'ICAgICAgICAgICAgICAgICAgICAgKHVpZCwgcmVnaW9uLCB0YXJnZXQsIHJlbWFpbmluZywgdWlkLCByZWdpb24sIHRhcmdldCwg'
    'cmVtYWluaW5nKQogICAgICAgICAgICAgICAgICAgICkKICAgICAgICAgICAgICAgICAgICBjb25uLmNvbW1pdCgpCiAgICAgICAg'
    'ZXhjZXB0IEV4Y2VwdGlvbiBhcyBleGM6CiAgICAgICAgICAgIHByaW50KGYnW0xlZ2FjeSBBdXRvTGlrZSBNaWdyYXRpb25dIHtl'
    'eGN9JykKCiMgPT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09CiMg8J+boCBIRUxQRVIgRlVOQ1RJT05T'
    'CiMgPT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09CmRlZiBub3dfaW5kaWEoKToKICAgIHJldHVybiBk'
    'YXRldGltZS5kYXRldGltZS5ub3coZGF0ZXRpbWUudGltZXpvbmUoZGF0ZXRpbWUudGltZWRlbHRhKGhvdXJzPTUsIG1pbnV0ZXM9'
    'MzApKSkKCmRlZiBnZXRfY3VycmVudF9kYXlfc3RyKCk6CiAgICBub3cgPSBub3dfaW5kaWEoKQogICAgaWYgbm93LmhvdXIgPCA0'
    'OgogICAgICAgIGVmZmVjdGl2ZV9kYXRlID0gbm93IC0gZGF0ZXRpbWUudGltZWRlbHRhKGRheXM9MSkKICAgIGVsc2U6CiAgICAg'
    'ICAgZWZmZWN0aXZlX2RhdGUgPSBub3cKICAgIHJldHVybiBlZmZlY3RpdmVfZGF0ZS5zdHJmdGltZSgiJVktJW0tJWQiKQoKZGVm'
    'IG1ha2VfcmVxdWVzdCh1cmwpOgogICAgZ2xvYmFsIGxhc3RfcmVxdWVzdF90aW1lCiAgICB0cnk6CiAgICAgICAgd2l0aCBSRVFV'
    'RVNUX0xPQ0s6CiAgICAgICAgICAgIHdhaXQgPSBtYXgoMCwgMSAtICh0aW1lLnRpbWUoKSAtIGxhc3RfcmVxdWVzdF90aW1lKSkK'
    'ICAgICAgICAgICAgaWYgd2FpdDoKICAgICAgICAgICAgICAgIHRpbWUuc2xlZXAod2FpdCkKICAgICAgICAgICAgbGFzdF9yZXF1'
    'ZXN0X3RpbWUgPSB0aW1lLnRpbWUoKQogICAgICAgICAgICByZXR1cm4gcmVxdWVzdHMuZ2V0KHVybCwgdGltZW91dD0zMCkKICAg'
    'IGV4Y2VwdCByZXF1ZXN0cy5SZXF1ZXN0RXhjZXB0aW9uIGFzIGU6CiAgICAgICAgcHJpbnQoZiJbQVBJIEVycm9yXSB7ZX0iKQog'
    'ICAgICAgIHJldHVybiBOb25lCgpkZWYgZ2V0X3JlbWFpbmluZ19saWtlcygpOgogICAgIiIiUmV0dXJuIGEgY29uc2lzdGVudCwg'
    'dGhyZWFkLXNhZmUgc25hcHNob3Qgb2YgdGhlIHJlbWFpbmluZyBnbG9iYWwgbGlrZXMuIiIiCiAgICB3aXRoIFNUQVRFX0xPQ0s6'
    'CiAgICAgICAgcmV0dXJuIG1heCgwLCBpbnQoR0xPQkFMX0xJTUlUKSAtIGludCh0b3RhbF9yZXF1ZXN0c191c2VkKSkKCgpkZWYg'
    'aXNfbWVtYmVyKHVzZXJfaWQpOgogICAgIiIiVXNlciBtdXN0IGpvaW4gYWxsIHJlcXVpcmVkIGdyb3Vwcy4iIiIKICAgIHRyeToK'
    'ICAgICAgICBmb3IgcmVxdWlyZWRfZ3JvdXAgaW4gUkVRVUlSRURfR1JPVVBTOgogICAgICAgICAgICBtZW1iZXIgPSBib3QuZ2V0'
    'X2NoYXRfbWVtYmVyKHJlcXVpcmVkX2dyb3VwWyJpZCJdLCB1c2VyX2lkKQogICAgICAgICAgICBpZiBtZW1iZXIuc3RhdHVzIGlu'
    'IFsibGVmdCIsICJraWNrZWQiXToKICAgICAgICAgICAgICAgIHJldHVybiBGYWxzZQogICAgICAgIHJldHVybiBUcnVlCiAgICBl'
    'eGNlcHQgRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgcHJpbnQoZiLwn5qrIENvdWxkIG5vdCBjaGVjayBtZW1iZXJzaGlwIGZvciB1'
    'c2VyIHt1c2VyX2lkfToge2V9IikKICAgICAgICByZXR1cm4gRmFsc2UKCgpkZWYgc2VuZF9mb3JjZV9qb2luX3Byb21wdChtZXNz'
    'YWdlKToKICAgICIiIlNob3cgam9pbiBidXR0b25zIGZvciBhbGwgcmVxdWlyZWQgZ3JvdXBzIGFuZCBhIHZlcmlmaWNhdGlvbiBi'
    'dXR0b24uIiIiCiAgICBtYXJrdXAgPSBJbmxpbmVLZXlib2FyZE1hcmt1cCgpCiAgICBmb3IgcmVxdWlyZWRfZ3JvdXAgaW4gUkVR'
    'VUlSRURfR1JPVVBTOgogICAgICAgIGJ0biA9IG1ha2VfcHJlbWl1bV9idXR0b24oCiAgICAgICAgICAgICJKb2luICIgKyByZXF1'
    'aXJlZF9ncm91cFsibmFtZSJdLAogICAgICAgICAgICB1cmw9cmVxdWlyZWRfZ3JvdXBbImxpbmsiXSwKICAgICAgICAgICAgaWNv'
    'bl9pZD1fZ2V0X2J1dHRvbl9tYXAoKS5nZXQoIkpvaW4gR3JvdXAiKSwKICAgICAgICAgICAgc3R5bGU9InByaW1hcnkiCiAgICAg'
    'ICAgKQogICAgICAgIG1hcmt1cC5hZGQoYnRuKQoKICAgIGJ0bl92ZXIgPSBtYWtlX3ByZW1pdW1fYnV0dG9uKAogICAgICAgICJK'
    'b2luZWQhIFZlcmlmeSBOb3ciLAogICAgICAgIGNhbGxiYWNrX2RhdGE9InZlcmlmeV9qb2luIiwKICAgICAgICBpY29uX2lkPV9n'
    'ZXRfYnV0dG9uX21hcCgpLmdldCgiSm9pbmVkISBWZXJpZnkgTm93IiksCiAgICAgICAgc3R5bGU9InN1Y2Nlc3MiCiAgICApCiAg'
    'ICBtYXJrdXAuYWRkKGJ0bl92ZXIpCiAgICB3YXJuX3RleHQgPSAoCiAgICAgICAgIjxiPvCfm5EgU3RvcCEgVG8gdXNlIHRoaXMg'
    'Y29tbWFuZCwgeW91IG11c3Qgam9pbiBhbGwgdGhyZWUgcmVxdWlyZWQgZ3JvdXBzLjwvYj4iCiAgICAgICAgKyBjaHIoMTApICsg'
    'Y2hyKDEwKQogICAgICAgICsgIlBsZWFzZSBqb2luIGFsbCB0aHJlZSBncm91cHMgYW5kIHRoZW4gY2xpY2sgdGhlICdWZXJpZnkg'
    'Tm93JyBidXR0b24gYmVsb3cuIgogICAgKQogICAgd2Fybl90ZXh0ID0gY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0o'
    'd2Fybl90ZXh0KQogICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIHdhcm5fdGV4dCwgcmVwbHlfbWFya3VwPW1hcmt1cCkKCgojID09'
    'PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQojIPCflJAgRk9SQ0UgSk9JTiBDQUxMQkFDSyAobGlrZXMu'
    'cHkgc3R5bGUpCiMgPT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09CkBib3QuY2FsbGJhY2tfcXVlcnlf'
    'aGFuZGxlcihmdW5jPWxhbWJkYSBjYWxsOiBjYWxsLmRhdGEgPT0gInZlcmlmeV9qb2luIikKZGVmIGhhbmRsZV92ZXJpZmljYXRp'
    'b25fY2FsbGJhY2soY2FsbCk6CiAgICB1c2VyX2lkID0gY2FsbC5mcm9tX3VzZXIuaWQKICAgIGlmIGlzX21lbWJlcih1c2VyX2lk'
    'KToKICAgICAgICBib3QuYW5zd2VyX2NhbGxiYWNrX3F1ZXJ5KGNhbGwuaWQsICLinIUgVmVyaWZpY2F0aW9uIHN1Y2Nlc3NmdWwh'
    'IikKICAgICAgICBtc2dfdGV4dCA9IGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKAogICAgICAgICAgICAiPGI+4pyF'
    'IFZlcmlmaWNhdGlvbiBzdWNjZXNzZnVsITwvYj4iICsgY2hyKDEwKSArCiAgICAgICAgICAgICJZb3UgY2FuIG5vdyBzZW5kIHlv'
    'dXIgPGNvZGU+L2xpa2U8L2NvZGU+IGNvbW1hbmQgYWdhaW4uIgogICAgICAgICkKICAgICAgICB0cnk6CiAgICAgICAgICAgIGJv'
    'dC5lZGl0X21lc3NhZ2VfdGV4dCgKICAgICAgICAgICAgICAgIG1zZ190ZXh0LAogICAgICAgICAgICAgICAgY2hhdF9pZD1jYWxs'
    'Lm1lc3NhZ2UuY2hhdC5pZCwKICAgICAgICAgICAgICAgIG1lc3NhZ2VfaWQ9Y2FsbC5tZXNzYWdlLm1lc3NhZ2VfaWQsCiAgICAg'
    'ICAgICAgICAgICByZXBseV9tYXJrdXA9Tm9uZQogICAgICAgICAgICApCiAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbjoKICAgICAg'
    'ICAgICAgYm90LnNlbmRfbWVzc2FnZShjYWxsLm1lc3NhZ2UuY2hhdC5pZCwgbXNnX3RleHQpCiAgICBlbHNlOgogICAgICAgIGJv'
    'dC5hbnN3ZXJfY2FsbGJhY2tfcXVlcnkoCiAgICAgICAgICAgIGNhbGwuaWQsCiAgICAgICAgICAgICLinYwgWW91IGhhdmVuJ3Qg'
    'am9pbmVkIHRoZSBjaGFubmVsIGFuZCBncm91cCB5ZXQuIiwKICAgICAgICAgICAgc2hvd19hbGVydD1UcnVlCiAgICAgICAgKQoK'
    'CiMgPT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09CiMg8J+OriBNQU5VQUwgTElLRUJPVCBDT01NQU5E'
    'UwojID09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQpAYm90Lm1lc3NhZ2VfaGFuZGxlcihjb21tYW5k'
    'cz1bJ3N0YXJ0J10pCmRlZiBoYW5kbGVfc3RhcnQobWVzc2FnZSk6CiAgICBpZiBtZXNzYWdlLmNoYXQudHlwZSAhPSAncHJpdmF0'
    'ZSc6CiAgICAgICAgcmV0dXJuICAKICAgIHVzZXJfaWQgPSBtZXNzYWdlLmZyb21fdXNlci5pZAogICAgaWYgdXNlcl9pZCBub3Qg'
    'aW4gdXNlcnM6CiAgICAgICAgdXNlcnMuYWRkKHVzZXJfaWQpCiAgICAgICAgc2F2ZV9kYXRhKCkKICAgIHVzZXJfbmFtZSA9IG1l'
    'c3NhZ2UuZnJvbV91c2VyLmZpcnN0X25hbWUKICAgIHN0YXJ0X3RleHQgPSAoZiLwn46JIFdFTENPTUUge3VzZXJfbmFtZX0g8J+O'
    'iVxuIgogICAgICAgICAgICAgICAgICAi4pSB4pSB4pSB4pym4pym4pSB4pSB4pSBXG4iCiAgICAgICAgICAgICAgICAgICLwn5Sl'
    'IFJlYWR5IHRvIEZpcmUsIFJlYWR5IHRvIFdpbiDwn5SlXG4iCiAgICAgICAgICAgICAgICAgICLwn5iOIEVuam95IHRoZSBHYW1l'
    'IHdpdGggb3VyIEJPVCDwn5qAXG4iCiAgICAgICAgICAgICAgICAgICLilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHi'
    'lIHilIHilIHilIFcbiIKICAgICAgICAgICAgICAgICAgIjxiPkNvbW1hbmQgLTwvYj5cbiIKICAgICAgICAgICAgICAgICAgIjxj'
    'b2RlPi9saWtlIHtyZWdpb259IHt1aWR9PC9jb2RlPiIpCiAgICBzdGFydF90ZXh0ID0gY29udmVydF9ub3JtYWxfZW1vamlzX3Rv'
    'X3ByZW1pdW0oc3RhcnRfdGV4dCkKICAgIG1hcmt1cCA9IElubGluZUtleWJvYXJkTWFya3VwKCkKICAgIGJvdF91c2VybmFtZSA9'
    'IGJvdC5nZXRfbWUoKS51c2VybmFtZQogICAgYWRkX3RvX2dyb3VwX2xpbmsgPSBmImh0dHBzOi8vdC5tZS97Ym90X3VzZXJuYW1l'
    'fT9zdGFydGdyb3VwPXRydWUiCiAgICAKICAgIGJ0bjEgPSBtYWtlX3ByZW1pdW1fYnV0dG9uKCJBREQgTUUgVE8gWU9VUiBHUk9V'
    'UCIsIHVybD1hZGRfdG9fZ3JvdXBfbGluaywgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICBpY29uX2lkPV9nZXRfYnV0'
    'dG9uX21hcCgpLmdldCgiQUREIE1FIFRPIFlPVVIgR1JPVVAiKSwgc3R5bGU9InByaW1hcnkiKQogICAgYnRuMiA9IG1ha2VfcHJl'
    'bWl1bV9idXR0b24oIlNVUFBPUlQiLCB1cmw9Imh0dHBzOi8vdC5tZS9USVRBTkxJS0VTR1JPVVAiLAogICAgICAgICAgICAgICAg'
    'ICAgICAgICAgICAgICAgaWNvbl9pZD1fZ2V0X2J1dHRvbl9tYXAoKS5nZXQoIlNVUFBPUlQiKSwgc3R5bGU9InN1Y2Nlc3MiKQog'
    'ICAgYnRuMyA9IG1ha2VfcHJlbWl1bV9idXR0b24oIk9XTkVSIiwgdXJsPSJodHRwczovL3QubWUvcHJpbmNlZ21yIiwKICAgICAg'
    'ICAgICAgICAgICAgICAgICAgICAgICAgIGljb25faWQ9X2dldF9idXR0b25fbWFwKCkuZ2V0KCJPV05FUiIpLCBzdHlsZT0iZGFu'
    'Z2VyIikKICAgIG1hcmt1cC5hZGQoYnRuMSkKICAgIG1hcmt1cC5hZGQoYnRuMiwgYnRuMykKICAgIGJvdC5yZXBseV90byhtZXNz'
    'YWdlLCBzdGFydF90ZXh0LCByZXBseV9tYXJrdXA9bWFya3VwKQoKQGJvdC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WydoZWxw'
    'J10pCmRlZiBoYW5kbGVfaGVscChtZXNzYWdlKToKICAgIHVzZXJfaWQgPSBtZXNzYWdlLmZyb21fdXNlci5pZAogICAgCiAgICBp'
    'ZiBpc19hZG1pbih1c2VyX2lkKToKICAgICAgICAjIEFkbWluIC8gT3duZXIgaGVscAogICAgICAgIGhlbHBfdGV4dCA9ICgKICAg'
    'ICAgICAgICAgIvCfkZEgPGI+QURNSU4gLyBPV05FUiBDT01NQU5EUzwvYj5cbiIKICAgICAgICAgICAgIuKUgeKUgeKUgeKUgeKU'
    'geKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgVxuIgogICAgICAgICAgICAi8J+GlCA8Y29kZT4vZ2V0aWQ8'
    'L2NvZGU+IC0gQ3VycmVudCBjaGF0IElEXG4iCiAgICAgICAgICAgICLwn5OiIDxjb2RlPi9icm9hZGNhc3QgJmx0O21lc3NhZ2Um'
    'Z3Q7PC9jb2RlPiAtIEJyb2FkY2FzdCB0byB1c2Vycy9ncm91cHNcbiIKICAgICAgICAgICAgIvCfkaQgPGNvZGU+L3VzZWxpbWl0'
    'ICZsdDt1c2VyX2lkJmd0OyAmbHQ7bGltaXQmZ3Q7PC9jb2RlPiAtIFNldCB1c2VyIGRhaWx5IGxpbWl0XG4iCiAgICAgICAgICAg'
    'ICLwn5OKIDxjb2RlPi9zZXRsaW1pdCAmbHQ7bnVtYmVyJmd0OzwvY29kZT4gLSBTZXQgZ2xvYmFsIGRhaWx5IGxpbWl0XG4iCiAg'
    'ICAgICAgICAgICLinIUgPGNvZGU+L2FwcHJvdmUgJmx0O2dyb3VwX2lkJmd0OzwvY29kZT4gLSBBcHByb3ZlIGEgZ3JvdXBcbiIK'
    'ICAgICAgICAgICAgIuKdjCA8Y29kZT4vZGlzYXBwcm92ZSAmbHQ7Z3JvdXBfaWQmZ3Q7PC9jb2RlPiAtIERpc2FwcHJvdmUgYSBn'
    'cm91cFxuIgogICAgICAgICAgICAi8J+TiyA8Y29kZT4vbGlzdGdyb3VwczwvY29kZT4gLSBMaXN0IGFwcHJvdmVkIGdyb3Vwc1xu'
    'IgogICAgICAgICAgICAi8J+UlyA8Y29kZT4vc2V0bGlrZWFwaSAmbHQ7dXJsJmd0OzwvY29kZT4gLSBTZXQgbWFudWFsIGxpa2Ug'
    'QVBJXG4iCiAgICAgICAgICAgICLwn5SXIDxjb2RlPi9zZXRhdXRvYXBpICZsdDt1cmwmZ3Q7PC9jb2RlPiAtIFNldCBhdXRvIGxp'
    'a2UgQVBJXG4iCiAgICAgICAgICAgICLwn5qAIDxjb2RlPi9hdXRvbGlrZSAmbHQ7cmVnaW9uJmd0OyAmbHQ7dWlkJmd0OyAmbHQ7'
    'dGFyZ2V0Jmd0OzwvY29kZT4gLSBBZGQgYXV0byBsaWtlIHRhc2tcbiIKICAgICAgICAgICAgIuKelSA8Y29kZT4vZXh0ZW5kICZs'
    'dDtyZWdpb24mZ3Q7ICZsdDt1aWQmZ3Q7ICZsdDthZGRsaWtlJmd0OzwvY29kZT4gLSBFeHRlbmQgZXhpc3RpbmcgdGFyZ2V0XG4i'
    'CiAgICAgICAgICAgICLwn5eRIDxjb2RlPi9yZW1vdmUgJmx0O3VpZCZndDs8L2NvZGU+IC0gUmVtb3ZlIGF1dG8gbGlrZSB0YXNr'
    'XG4iCiAgICAgICAgICAgICLwn5OcIDxjb2RlPi9saXN0dWlkPC9jb2RlPiAtIFNob3cgYWxsIGF1dG8gbGlrZSBVSURzXG4iCiAg'
    'ICAgICAgICAgICLilrbvuI8gPGNvZGU+L3J1bjwvY29kZT4gLSBSdW4gYXV0b2xpa2UgcHJvY2VzcyBtYW51YWxseVxuIgogICAg'
    'ICAgICAgICAi8J+RkSA8Y29kZT4vYWRkYWRtaW4gJmx0O3VzZXJfaWQmZ3Q7ICZsdDtkYXlzJmd0OyBbbWF4X2F1dG9saWtlXTwv'
    'Y29kZT4gLSBBZGQgdGVtcC9wZXJtYW5lbnQgYWRtaW5cbiIKICAgICAgICAgICAgIvCfl5EgPGNvZGU+L3JlbW92ZWFkbWluICZs'
    'dDt1c2VyX2lkJmd0OzwvY29kZT4gLSBSZW1vdmUgYW4gYWRtaW4gKE93bmVyIG9ubHkpXG4iCiAgICAgICAgICAgICLwn5+iIDxj'
    'b2RlPi9vbjwvY29kZT4gLSBUdXJuIGJvdCBPTiAoT3duZXIgb25seSlcbiIKICAgICAgICAgICAgIvCflLQgPGNvZGU+L29mZjwv'
    'Y29kZT4gLSBUdXJuIGJvdCBPRkYgKE93bmVyIG9ubHkpXG4iCiAgICAgICAgICAgICLinaTvuI8gPGNvZGU+L2xpa2VzICZsdDty'
    'ZWdpb24mZ3Q7ICZsdDt1aWQmZ3Q7PC9jb2RlPiAtIEluc3RhbnQgQXV0b0xpa2Vcbjxjb2RlPi9saWtlcyAmbHQ7dXNlcl9pZCZn'
    'dDsgJmx0O3VzZXMmZ3Q7ICZsdDtkYXlzJmd0OzwvY29kZT4gLSBBcHByb3ZlIC9saWtlcyAoT3duZXIpXG48Y29kZT4vbGlrZXNy'
    'ZW1vdmUgJmx0O3VzZXJfaWQmZ3Q7PC9jb2RlPiAtIFJlbW92ZSBhcHByb3ZhbCAoT3duZXIpXG4iCiAgICAgICAgICAgICLwn5ST'
    'IDxjb2RlPi9saWtlc2FsbG93PC9jb2RlPiAtIEFsbG93IC9saWtlcyBpbiB0aGlzIGdyb3VwIChPd25lciBvbmx5KVxuIgogICAg'
    'ICAgICAgICAi8J+aqyA8Y29kZT4vbGlrZXNkaXNhbGxvdzwvY29kZT4gLSBEaXNhYmxlIC9saWtlcyBpbiB0aGlzIGdyb3VwIChP'
    'd25lciBvbmx5KVxuIgogICAgICAgICAgICAi8J+TiyA8Y29kZT4vbGlrZXNncm91cHM8L2NvZGU+IC0gU2hvdyBhbGwgL2xpa2Vz'
    'IGFsbG93ZWQgZ3JvdXBzIChPd25lciBvbmx5KVxuIgogICAgICAgICAgICAi4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB'
    '4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSBXG4iCiAgICAgICAgICAgICLwn5GkIDxiPkFsc28gYXZhaWxhYmxlIChVc2VyIGNvbW1h'
    'bmRzKTo8L2I+XG4iCiAgICAgICAgICAgICLwn46JIDxjb2RlPi9zdGFydDwvY29kZT4gLSBXZWxjb21lIG1lc3NhZ2VcbiIKICAg'
    'ICAgICAgICAgIuKdpO+4jyA8Y29kZT4vbGlrZSAmbHQ7cmVnaW9uJmd0OyAmbHQ7dWlkJmd0OzwvY29kZT4gLSBTZW5kIGxpa2Vz'
    'XG4iCiAgICAgICAgICAgICLwn5OKIDxjb2RlPi9yZW1haW48L2NvZGU+IC0gR2xvYmFsIHJlbWFpbmluZyByZXF1ZXN0cyIKICAg'
    'ICAgICApCiAgICBlbHNlOgogICAgICAgICMgTm9ybWFsIHVzZXIgaGVscAogICAgICAgIGhlbHBfdGV4dCA9ICgKICAgICAgICAg'
    'ICAgIvCfk5ogPGI+VVNFUiBDT01NQU5EUzwvYj5cbiIKICAgICAgICAgICAgIuKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKU'
    'geKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgVxuIgogICAgICAgICAgICAi8J+OiSA8Y29kZT4vc3RhcnQ8L2NvZGU+IC0gV2VsY29t'
    'ZSBtZXNzYWdlXG4iCiAgICAgICAgICAgICLinaTvuI8gPGNvZGU+L2xpa2UgJmx0O3JlZ2lvbiZndDsgJmx0O3VpZCZndDs8L2Nv'
    'ZGU+IC0gU2VuZCBsaWtlc1xuIgogICAgICAgICAgICAiICAgRXhhbXBsZTogPGNvZGU+L2xpa2UgSU5EIDEyMzQ1Njc4OTA8L2Nv'
    'ZGU+XG4iCiAgICAgICAgICAgICLwn5OKIDxjb2RlPi9yZW1haW48L2NvZGU+IC0gQ2hlY2sgZ2xvYmFsIHJlbWFpbmluZyByZXF1'
    'ZXN0c1xuIgogICAgICAgICAgICAi4p2MIDxjb2RlPi9oZWxwPC9jb2RlPiAtIFNob3cgdGhpcyBoZWxwIG1lc3NhZ2VcbiIKICAg'
    'ICAgICAgICAgIuKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgVxuIgogICAgICAg'
    'ICAgICAi4oS577iPIEJvdCBpcyBmcmVlIHRvIHVzZS4iCiAgICAgICAgKQogICAgCiAgICBoZWxwX3RleHQgPSBjb252ZXJ0X25v'
    'cm1hbF9lbW9qaXNfdG9fcHJlbWl1bShoZWxwX3RleHQpCiAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgaGVscF90ZXh0KQoKQGJv'
    'dC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WyJsaWtlIl0pCmRlZiBoYW5kbGVfbGlrZShtZXNzYWdlKToKICAgIHVzZXJfaWQg'
    'PSBtZXNzYWdlLmZyb21fdXNlci5pZAogICAgdXNlcl9zdHJfaWQgPSBzdHIodXNlcl9pZCkKICAgIAogICAgIyBCb3QgT0ZGIGNo'
    'ZWNrIChhZG1pbnMgY2FuIHN0aWxsIHVzZSkKICAgIGlmIG5vdCBCT1RfRU5BQkxFRCBhbmQgbm90IGlzX2FkbWluKHVzZXJfaWQp'
    'OgogICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgi8J+bkSA8Yj5C'
    'b3QgaXMgY3VycmVudGx5IE9GRi48L2I+XG5QbGVhc2Ugd2FpdCB1bnRpbCB0aGUgT3duZXIgdHVybnMgaXQgT04uIikpCiAgICAg'
    'ICAgcmV0dXJuCgogICAgIyBGb3JjZSBqb2luIChsaWtlcy5weSBzdHlsZSkgLSBPd25lciBhbHdheXMgYWxsb3dlZAogICAgaWYg'
    'dXNlcl9pZCAhPSBPV05FUl9JRCBhbmQgbm90IGlzX21lbWJlcih1c2VyX2lkKToKICAgICAgICBzZW5kX2ZvcmNlX2pvaW5fcHJv'
    'bXB0KG1lc3NhZ2UpCiAgICAgICAgcmV0dXJuCgogICAgaWYgbWVzc2FnZS5jaGF0LmlkIG5vdCBpbiBBTExPV0VEX0dST1VQUzoK'
    'ICAgICAgICBkZW55X3RleHQgPSAi8J+UkCA8Yj5UaGlzIGdyb3VwIGlzIG5vdCBhcHByb3ZlZCB0byB1c2UgdGhlIGJvdC5cblBs'
    'ZWFzZSBjb250YWN0OiBAcHJpbmNlZ21yPC9iPiIKICAgICAgICBkZW55X3RleHQgPSBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9f'
    'cHJlbWl1bShkZW55X3RleHQpCiAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGRlbnlfdGV4dCkKICAgICAgICByZXR1cm4K'
    'CiAgICBjdXJyZW50X2RheSA9IGdldF9jdXJyZW50X2RheV9zdHIoKQogICAgCiAgICBpZiBub3QgaXNfYWRtaW4odXNlcl9pZCk6'
    'CiAgICAgICAgdXNlcl9kYWlseV9saW1pdCA9IHVzZXJfY3VzdG9tX2xpbWl0cy5nZXQodXNlcl9zdHJfaWQsIDEpCiAgICAgICAg'
    'dXNlcl9sb2cgPSB1c2VyX3N1Y2Nlc3NfbG9ncy5nZXQodXNlcl9zdHJfaWQsIHt9KQogICAgICAgIGlmIHVzZXJfbG9nLmdldCgi'
    'ZGF0ZSIpID09IGN1cnJlbnRfZGF5OgogICAgICAgICAgICBjdXJyZW50X3VzYWdlID0gdXNlcl9sb2cuZ2V0KCJjb3VudCIsIDAp'
    'CiAgICAgICAgZWxzZToKICAgICAgICAgICAgY3VycmVudF91c2FnZSA9IDAKICAgICAgICBpZiBjdXJyZW50X3VzYWdlID49IHVz'
    'ZXJfZGFpbHlfbGltaXQ6CiAgICAgICAgICAgIGxpbWl0X3RleHQgPSAoZiLwn5qrIDxiPkRhaWx5IExpbWl0IFJlYWNoZWQhPC9i'
    'PlxuXG4iCiAgICAgICAgICAgICAgICAgICAgICAgICAgZiJZb3UgaGF2ZSB1c2VkOiA8Yj57Y3VycmVudF91c2FnZX0ve3VzZXJf'
    'ZGFpbHlfbGltaXR9PC9iPiBzdWNjZXNzZnVsIGxpa2VzIHRvZGF5LlxuIgogICAgICAgICAgICAgICAgICAgICAgICAgICJQbGVh'
    'c2UgdHJ5IGFnYWluIHRvbW9ycm93IGFmdGVyIDxiPjQ6MDAgQU0gSVNUPC9iPi4iKQogICAgICAgICAgICBsaW1pdF90ZXh0ID0g'
    'Y29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0obGltaXRfdGV4dCkKICAgICAgICAgICAgYm90LnJlcGx5X3RvKG1lc3Nh'
    'Z2UsIGxpbWl0X3RleHQpCiAgICAgICAgICAgIHJldHVybgoKICAgIGdsb2JhbCB0b3RhbF9saWtlc191c2VkLCB0b3RhbF9yZXF1'
    'ZXN0c191c2VkCiAgICBhcmdzID0gbWVzc2FnZS50ZXh0LnNwbGl0KCkKICAgIGlmIGxlbihhcmdzKSAhPSAzOgogICAgICAgIGZv'
    'cm1hdF90ZXh0ID0gIjxiPuKdjCBJbmNvcnJlY3QgZm9ybWF0ITwvYj5cbkNvcnJlY3QgdXNhZ2U6IDxjb2RlPi9saWtlICZsdDty'
    'ZWdpb24mZ3Q7ICZsdDt1aWQmZ3Q7PC9jb2RlPlxuRXhhbXBsZTogPGNvZGU+L2xpa2UgSU5EIDEyMzQ1Njc4OTA8L2NvZGU+Igog'
    'ICAgICAgIGZvcm1hdF90ZXh0ID0gY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oZm9ybWF0X3RleHQpCiAgICAgICAg'
    'Ym90LnJlcGx5X3RvKG1lc3NhZ2UsIGZvcm1hdF90ZXh0KQogICAgICAgIHJldHVybgogICAgcmVnaW9uLCB1aWQgPSBhcmdzWzFd'
    'LnVwcGVyKCksIGFyZ3NbMl0KICAgIGlmIG5vdCB1aWQuaXNkaWdpdCgpOgogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVz'
    'c2FnZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAgICLinYwgPGI+VUlEIHNpcmYgbnVtYmVy'
    'cyBtZWluIGhvbmEgY2hhaGl5ZS48L2I+IgogICAgICAgICkpCiAgICBpZiByZWdpb24gbm90IGluIFJFR0lPTlM6CiAgICAgICAg'
    'cmVnaW9uX3RleHQgPSBmIvCfl7rvuI8gSW52YWxpZCByZWdpb246IGB7cmVnaW9ufWAiCiAgICAgICAgcmVnaW9uX3RleHQgPSBj'
    'b252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShyZWdpb25fdGV4dCkKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwg'
    'cmVnaW9uX3RleHQpCiAgICAgICAgcmV0dXJuCiAgICAgICAgCiAgICByZW1haW5pbmcgPSBnZXRfcmVtYWluaW5nX2xpa2VzKCkK'
    'ICAgIGlmIHJlbWFpbmluZyA8PSAwIGFuZCBub3QgaXNfYWRtaW4odXNlcl9pZCk6CiAgICAgICAgZ2xvYmFsX2xpbWl0X3RleHQg'
    'PSAi8J+aqyA8Yj5HbG9iYWwgTGltaXQgcmVhY2hlZCEgUGxlYXNlIHRyeSBhZ2FpbiBsYXRlci48L2I+IgogICAgICAgIGdsb2Jh'
    'bF9saW1pdF90ZXh0ID0gY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oZ2xvYmFsX2xpbWl0X3RleHQpCiAgICAgICAg'
    'Ym90LnJlcGx5X3RvKG1lc3NhZ2UsIGdsb2JhbF9saW1pdF90ZXh0KQogICAgICAgIHJldHVybgogICAgCiAgICBwcm9jZXNzaW5n'
    'X21zZyA9IGJvdC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgi4o+zIDxiPlByb2Nl'
    'c3NpbmcgeW91ciByZXF1ZXN0Li4uPC9iPiIpKQogICAgCiAgICB0cnk6CiAgICAgICAgIyBMZXQgdGhlIEFQSSBkZWNpZGUgaG93'
    'IG1hbnkgbGlrZXMgaXQgY2FuIHNlbmQuCiAgICAgICAgIyBEbyBub3QgZm9yY2UgYSBmaXhlZCBgbGlrZXM9MjBgIHJlcXVlc3Qg'
    'cGFyYW1ldGVyLgogICAgICAgIHVybCA9IGYie01BTlVBTF9MSUtFX0FQSS5yc3RyaXAoJy8nKX0vbGlrZT91aWQ9e3VpZH0mc2Vy'
    'dmVyX25hbWU9e3JlZ2lvbn0iCiAgICAgICAgcmVzcG9uc2UgPSBtYWtlX3JlcXVlc3QodXJsKQoKICAgICAgICBpZiByZXNwb25z'
    'ZSBhbmQgcmVzcG9uc2Uuc3RhdHVzX2NvZGUgPT0gMjAwOgogICAgICAgICAgICByZXN1bHQgPSByZXNwb25zZS5qc29uKCkKICAg'
    'ICAgICAgICAgaWYgbm90IGlzaW5zdGFuY2UocmVzdWx0LCBkaWN0KToKICAgICAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3Io'
    'IkFQSSByZXNwb25zZSBpcyBub3QgYSBKU09OIG9iamVjdCIpCiAgICAgICAgICAgIHN0YXR1cyA9IHJlc3VsdC5nZXQoInN0YXR1'
    'cyIpCiAgICAgICAgICAgICMgUHJlZmVyIHRoZSBwbGF5ZXIncyByZWFsIGJlZm9yZS9hZnRlciBjb3VudGVyIGRpZmZlcmVuY2Uu'
    'CiAgICAgICAgICAgICMgU29tZSBBUEkgYnVpbGRzIHJlcG9ydCBMaWtlc0dpdmVuQnlBUEkgZm9yIG9ubHkgb25lIGFjY291bnQK'
    'ICAgICAgICAgICAgIyBldmVuIHdoZW4gdGhlIGJhY2tlbmQgaGFzIHNlbnQgbGlrZXMgdGhyb3VnaCBzZXZlcmFsIGFjY291bnRz'
    'LgogICAgICAgICAgICAjIFRoZSBjb3VudGVyIGRlbHRhIGlzIHRoZSBiZXN0IGF2YWlsYWJsZSB0b3RhbCBmb3IgdGhlIGNvbW1h'
    'bmQuCiAgICAgICAgICAgIGxpa2VzX2dpdmVuID0gTm9uZQogICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICBsaWtlc19i'
    'ZWZvcmUgPSBpbnQoc3RyKHJlc3VsdC5nZXQoIkxpa2VzYmVmb3JlQ29tbWFuZCIpKS5yZXBsYWNlKCIsIiwgIiIpLnN0cmlwKCkp'
    'CiAgICAgICAgICAgICAgICBsaWtlc19hZnRlciA9IGludChzdHIocmVzdWx0LmdldCgiTGlrZXNhZnRlckNvbW1hbmQiKSkucmVw'
    'bGFjZSgiLCIsICIiKS5zdHJpcCgpKQogICAgICAgICAgICAgICAgY291bnRlcl9kZWx0YSA9IGxpa2VzX2FmdGVyIC0gbGlrZXNf'
    'YmVmb3JlCiAgICAgICAgICAgICAgICBpZiBjb3VudGVyX2RlbHRhID49IDA6CiAgICAgICAgICAgICAgICAgICAgbGlrZXNfZ2l2'
    'ZW4gPSBjb3VudGVyX2RlbHRhCiAgICAgICAgICAgIGV4Y2VwdCAoVHlwZUVycm9yLCBWYWx1ZUVycm9yKToKICAgICAgICAgICAg'
    'ICAgIHBhc3MKCiAgICAgICAgICAgICMgRmFsbCBiYWNrIHRvIHRoZSBBUEkgZmllbGQgb25seSB3aGVuIGNvdW50ZXJzIGFyZSB1'
    'bmF2YWlsYWJsZQogICAgICAgICAgICAjIG9yIGludmFsaWQuIE5ldmVyIGltcG9zZSBhIGZpeGVkIDIwLWxpa2UgY2FwIGluIHRo'
    'ZSBib3QuCiAgICAgICAgICAgIGlmIGxpa2VzX2dpdmVuIGlzIE5vbmU6CiAgICAgICAgICAgICAgICB0cnk6CiAgICAgICAgICAg'
    'ICAgICAgICAgbGlrZXNfZ2l2ZW4gPSBtYXgoMCwgaW50KHN0cihyZXN1bHQuZ2V0KCJMaWtlc0dpdmVuQnlBUEkiKSkucmVwbGFj'
    'ZSgiLCIsICIiKS5zdHJpcCgpKSkKICAgICAgICAgICAgICAgIGV4Y2VwdCAoVHlwZUVycm9yLCBWYWx1ZUVycm9yKToKICAgICAg'
    'ICAgICAgICAgICAgICBsaWtlc19naXZlbiA9IDAKCiAgICAgICAgICAgIGlmIHN0cihzdGF0dXMpID09ICIxIjoKICAgICAgICAg'
    'ICAgICAgICMgR2xvYmFsIC9zZXRsaW1pdCBpcyBtZWFzdXJlZCBpbiBhY3R1YWwgbGlrZXMsIG5vdCBBUEkgY2FsbHMuCiAgICAg'
    'ICAgICAgICAgICAjIFVwZGF0ZSB0aGUgZ2xvYmFsIGNvdW50ZXIgYW5kIHBlci11c2VyIGRhaWx5IHVzYWdlIGF0b21pY2FsbHku'
    'CiAgICAgICAgICAgICAgICAjIFRoaXMgcHJldmVudHMgY29uY3VycmVudCByZXF1ZXN0cyBmcm9tIGxvc2luZyBjb3VudGVyL2xv'
    'ZyB1cGRhdGVzLgogICAgICAgICAgICAgICAgd2l0aCBTVEFURV9MT0NLOgogICAgICAgICAgICAgICAgICAgIHRvdGFsX2xpa2Vz'
    'X3VzZWQgKz0gbGlrZXNfZ2l2ZW4KICAgICAgICAgICAgICAgICAgICB0b3RhbF9yZXF1ZXN0c191c2VkICs9IDEKICAgICAgICAg'
    'ICAgICAgICAgICB1c2VyX2xvZyA9IHVzZXJfc3VjY2Vzc19sb2dzLmdldCh1c2VyX3N0cl9pZCwge30pCiAgICAgICAgICAgICAg'
    'ICAgICAgaWYgdXNlcl9sb2cuZ2V0KCJkYXRlIikgPT0gY3VycmVudF9kYXk6CiAgICAgICAgICAgICAgICAgICAgICAgIGN1cnJl'
    'bnRfdXNhZ2UgPSBpbnQodXNlcl9sb2cuZ2V0KCJjb3VudCIsIDApKQogICAgICAgICAgICAgICAgICAgIGVsc2U6CiAgICAgICAg'
    'ICAgICAgICAgICAgICAgIGN1cnJlbnRfdXNhZ2UgPSAwCiAgICAgICAgICAgICAgICAgICAgdXNlcl9zdWNjZXNzX2xvZ3NbdXNl'
    'cl9zdHJfaWRdID0gewogICAgICAgICAgICAgICAgICAgICAgICAiZGF0ZSI6IGN1cnJlbnRfZGF5LAogICAgICAgICAgICAgICAg'
    'ICAgICAgICAiY291bnQiOiBjdXJyZW50X3VzYWdlICsgMQogICAgICAgICAgICAgICAgICAgIH0KICAgICAgICAgICAgICAgIHNh'
    'dmVfZGF0YSgpCgogICAgICAgICAgICAgICAgbmlja25hbWUgPSBzdHIocmVzdWx0LmdldCgnUGxheWVyTmlja25hbWUnLCAnTi9B'
    'JykpLnJlcGxhY2UoJzwnLCAnJmx0OycpLnJlcGxhY2UoJz4nLCAnJmd0OycpCiAgICAgICAgICAgICAgICAKICAgICAgICAgICAg'
    'ICAgIGlmIGlzX2FkbWluKHVzZXJfaWQpOgogICAgICAgICAgICAgICAgICAgIGxpbWl0X21zZyA9ICLwn5OJIDxiPllvdXIgTGlt'
    'aXQ6IFVubGltaXRlZCAoT3duZXIpPC9iPiIKICAgICAgICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICAgICAgdXNlcl9k'
    'YWlseV9saW1pdCA9IHVzZXJfY3VzdG9tX2xpbWl0cy5nZXQodXNlcl9zdHJfaWQsIDEpCiAgICAgICAgICAgICAgICAgICAgbGlt'
    'aXRfbXNnID0gZiLwn5OJIDxiPllvdXIgTGltaXQ6IHtjdXJyZW50X3VzYWdlICsgMX0ve3VzZXJfZGFpbHlfbGltaXR9PC9iPiIK'
    'CiAgICAgICAgICAgICAgICB0ZXh0ID0gKAogICAgICAgICAgICAgICAgICAgIGYiPGJsb2NrcXVvdGU+IgogICAgICAgICAgICAg'
    'ICAgICAgIGYi4pyFIDxiPkzJquG0i+G0h+qcsSBT4bSHybThtJsgU+G0nOG0hOG0hOG0h+qcseqcseqcsOG0nMqfyp/KjzwvYj4g'
    '8J+ls1xuIgogICAgICAgICAgICAgICAgICAgIGYi4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB'
    '4pSB4pSB4pSBXG4iCiAgICAgICAgICAgICAgICAgICAgZiLwn5GkIDxiPlDKn+G0gMqP4bSHyoA6PC9iPiB7bmlja25hbWV9XG4i'
    'CiAgICAgICAgICAgICAgICAgICAgZiLwn4aUIDxiPlVJRDo8L2I+IHt1aWR9XG4iCiAgICAgICAgICAgICAgICAgICAgZiLwn4yN'
    'IDxiPlLhtIfJosmq4bSPybQ6PC9iPiB7cmVnaW9ufVxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+TiiA8Yj5M4bSH4bSg4bSH'
    'yp86PC9iPiB7cmVzdWx0LmdldCgnUGxheWVyTGV2ZWwnLCAnTi9BJyl9XG4iCiAgICAgICAgICAgICAgICAgICAgZiLinaTvuI8g'
    'PGI+QuG0h+qcsOG0j8qA4bSHOjwvYj4ge3Jlc3VsdC5nZXQoJ0xpa2VzYmVmb3JlQ29tbWFuZCcsICdOL0EnKX1cbiIKICAgICAg'
    'ICAgICAgICAgICAgICBmIvCfkp0gPGI+QeqcsOG0m+G0h8qAOjwvYj4ge3Jlc3VsdC5nZXQoJ0xpa2VzYWZ0ZXJDb21tYW5kJywg'
    'J04vQScpfVxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+kliA8Yj5HyarhtKDhtIfJtCBCyo8gQuG0j+G0mzo8L2I+IHtsaWtl'
    'c19naXZlbn1cbiIKICAgICAgICAgICAgICAgICAgICBmIuKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKU'
    'geKUgeKUgeKUgeKUgVxuIgogICAgICAgICAgICAgICAgICAgIGYie2xpbWl0X21zZ30iCiAgICAgICAgICAgICAgICAgICAgZiI8'
    'L2Jsb2NrcXVvdGU+IgogICAgICAgICAgICAgICAgKQogICAgICAgICAgICBlbGlmIHN0YXR1cyA9PSAyOgogICAgICAgICAgICAg'
    'ICAgbmlja25hbWUgPSBzdHIocmVzdWx0LmdldCgnUGxheWVyTmlja25hbWUnLCAnTi9BJykpLnJlcGxhY2UoJzwnLCAnJmx0Oycp'
    'LnJlcGxhY2UoJz4nLCAnJmd0OycpCiAgICAgICAgICAgICAgICB0ZXh0ID0gKAogICAgICAgICAgICAgICAgICAgIGYiPGJsb2Nr'
    'cXVvdGU+IgogICAgICAgICAgICAgICAgICAgIGYi4pqg77iPIDxiPkbhtIDJqsqf4bSH4bSFIOG0m+G0jyBT4bSHybThtIUgTMmq'
    '4bSL4bSH6pyxPC9iPlxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+RpCA8Yj5Qyp/htIDKj+G0h8qAOjwvYj4ge25pY2tuYW1l'
    'fVxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+GlCA8Yj5VSUQ6PC9iPiB7dWlkfVxuIgogICAgICAgICAgICAgICAgICAgIGYi'
    '8J+SnSA8Yj5Ub3RhbCBMyarhtIvhtIc8L2I+IHtyZXN1bHQuZ2V0KCdMaWtlc2FmdGVyQ29tbWFuZCcsICdOL0EnKX1cbiIKICAg'
    'ICAgICAgICAgICAgICAgICBmIuKaoO+4jyA8Yj5NZXNzYWdlOjwvYj4gTElNSVRfUkVBQ0hFRCIKICAgICAgICAgICAgICAgICAg'
    'ICBmIjwvYmxvY2txdW90ZT4iCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICBuaWNr'
    'bmFtZSA9IHN0cihyZXN1bHQuZ2V0KCdQbGF5ZXJOaWNrbmFtZScsICdOL0EnKSkucmVwbGFjZSgnPCcsICcmbHQ7JykucmVwbGFj'
    'ZSgnPicsICcmZ3Q7JykKICAgICAgICAgICAgICAgIHRleHQgPSAoCiAgICAgICAgICAgICAgICAgICAgZiI8YmxvY2txdW90ZT4i'
    'CiAgICAgICAgICAgICAgICAgICAgZiLimqDvuI8gPGI+UMqf4bSAyo/htIfKgCBO4bSP4bSbIEbhtI/htJzJtOG0hTwvYj5cbiIK'
    'ICAgICAgICAgICAgICAgICAgICBmIvCfkaQgPGI+UMqf4bSAyo/htIfKgDo8L2I+IHtuaWNrbmFtZX1cbiIKICAgICAgICAgICAg'
    'ICAgICAgICBmIvCfhpQgPGI+VUlEOjwvYj4ge3VpZH1cbiIKICAgICAgICAgICAgICAgICAgICBmIvCfmYXwn4+7IDxiPk1lc3Nh'
    'Z2U6PC9iPiBwbGF5ZXJfbm90X2ZvdW5kIgogICAgICAgICAgICAgICAgICAgIGYiPC9ibG9ja3F1b3RlPiIKICAgICAgICAgICAg'
    'ICAgICkKICAgICAgICBlbHNlOgogICAgICAgICAgICB0ZXh0ID0gIjxibG9ja3F1b3RlPvCflIwgPGI+RmFpbGVkIHRvIGNvbm5l'
    'Y3QgdG8gdGhlIEFQSS4gUGxlYXNlIHRyeSBhZ2FpbiBsYXRlci48L2I+PC9ibG9ja3F1b3RlPiIKCiAgICAgICAgdGV4dCA9IGNv'
    'bnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKHRleHQpCiAgICAgICAgbWFya3VwID0gSW5saW5lS2V5Ym9hcmRNYXJrdXAo'
    'KQogICAgICAgIGRldl9idG4gPSBtYWtlX3ByZW1pdW1fYnV0dG9uKCJEZXZsb3BlciIsIHVybD0iaHR0cHM6Ly90Lm1lL3ByaW5j'
    'ZWdtciIsCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgaWNvbl9pZD1fZ2V0X2J1dHRvbl9tYXAoKS5nZXQo'
    'IkRldmxvcGVyIiksIHN0eWxlPSJwcmltYXJ5IikKICAgICAgICBtYXJrdXAuYWRkKGRldl9idG4pCiAgICAgICAgYm90LmVkaXRf'
    'bWVzc2FnZV90ZXh0KHRleHQsIG1lc3NhZ2UuY2hhdC5pZCwgcHJvY2Vzc2luZ19tc2cubWVzc2FnZV9pZCwgcmVwbHlfbWFya3Vw'
    'PW1hcmt1cCkKCiAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgZXJyb3JfdGV4dCA9IGYiPGJsb2NrcXVvdGU+4pqZ'
    '77iPIDxiPkVycm9yOjwvYj4ge3N0cihlKS5yZXBsYWNlKCc8JywgJyZsdDsnKS5yZXBsYWNlKCc+JywgJyZndDsnKX08L2Jsb2Nr'
    'cXVvdGU+IgogICAgICAgIGVycm9yX3RleHQgPSBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShlcnJvcl90ZXh0KQog'
    'ICAgICAgIG1hcmt1cCA9IElubGluZUtleWJvYXJkTWFya3VwKCkKICAgICAgICBkZXZfYnRuID0gbWFrZV9wcmVtaXVtX2J1dHRv'
    'bigiRGV2bG9wZXIiLCB1cmw9Imh0dHBzOi8vdC5tZS9wcmluY2VnbXIiLAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAg'
    'ICAgICAgIGljb25faWQ9X2dldF9idXR0b25fbWFwKCkuZ2V0KCJEZXZsb3BlciIpLCBzdHlsZT0icHJpbWFyeSIpCiAgICAgICAg'
    'bWFya3VwLmFkZChkZXZfYnRuKQogICAgICAgIGJvdC5lZGl0X21lc3NhZ2VfdGV4dChlcnJvcl90ZXh0LCBtZXNzYWdlLmNoYXQu'
    'aWQsIHByb2Nlc3NpbmdfbXNnLm1lc3NhZ2VfaWQsIHJlcGx5X21hcmt1cD1tYXJrdXApCgpAYm90Lm1lc3NhZ2VfaGFuZGxlcihj'
    'b21tYW5kcz1bInJlbWFpbiJdKQpkZWYgaGFuZGxlX3JlbWFpbihtZXNzYWdlKToKICAgIGlmIG5vdCBCT1RfRU5BQkxFRCBhbmQg'
    'bm90IGlzX2FkbWluKG1lc3NhZ2UuZnJvbV91c2VyLmlkKToKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgY29udmVydF9u'
    'b3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oIvCfm5EgPGI+Qm90IGlzIGN1cnJlbnRseSBPRkYuPC9iPlxuUGxlYXNlIHdhaXQgdW50'
    'aWwgdGhlIE93bmVyIHR1cm5zIGl0IE9OLiIpKQogICAgICAgIHJldHVybgogICAgaWYgbWVzc2FnZS5jaGF0LmlkIG5vdCBpbiBB'
    'TExPV0VEX0dST1VQUzoKICAgICAgICBkZW55X3RleHQgPSAi8J+UkCBUaGlzIGdyb3VwIGlzIG5vdCBhcHByb3ZlZCB0byB1c2Ug'
    'dGhlIGJvdC5cblBsZWFzZSBjb250YWN0OiBAcHJpbmNlZ21yIgogICAgICAgIGRlbnlfdGV4dCA9IGNvbnZlcnRfbm9ybWFsX2Vt'
    'b2ppc190b19wcmVtaXVtKGRlbnlfdGV4dCkKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgZGVueV90ZXh0KQogICAgICAg'
    'IHJldHVybgogICAgcmVtYWluaW5nID0gZ2V0X3JlbWFpbmluZ19saWtlcygpCiAgICByZXBseV90ZXh0ID0gZiLwn5OKIDxiPlJl'
    'bWFpbmluZyBSZXF1ZXN0czoge3JlbWFpbmluZ30ve0dMT0JBTF9MSU1JVH08L2I+IgogICAgcmVwbHlfdGV4dCA9IGNvbnZlcnRf'
    'bm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKHJlcGx5X3RleHQpCiAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgcmVwbHlfdGV4dCkK'
    'CkBib3QubWVzc2FnZV9oYW5kbGVyKGNvbW1hbmRzPVsnZ2V0aWQnXSkKZGVmIGdldF9jaGF0X2lkKG1lc3NhZ2UpOgogICAgaWYg'
    'aXNfYWRtaW4obWVzc2FnZS5mcm9tX3VzZXIuaWQpOgogICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCBmIvCfhpQgVGhpcyBj'
    'aGF0J3MgSUQgaXM6IGB7bWVzc2FnZS5jaGF0LmlkfWAiLCBwYXJzZV9tb2RlPSJNYXJrZG93biIpCgpAYm90Lm1lc3NhZ2VfaGFu'
    'ZGxlcihjb21tYW5kcz1bJ2Jyb2FkY2FzdCddKQpkZWYgaGFuZGxlX2Jyb2FkY2FzdChtZXNzYWdlKToKICAgIGlmIG5vdCBpc19h'
    'ZG1pbihtZXNzYWdlLmZyb21fdXNlci5pZCk6CiAgICAgICAgZGVueV90ZXh0ID0gIvCfmqsgPGI+QWNjZXNzIERlbmllZCEgVGhp'
    'cyBjb21tYW5kIGlzIGZvciB0aGUgYWRtaW4gb25seS48L2I+IgogICAgICAgIGRlbnlfdGV4dCA9IGNvbnZlcnRfbm9ybWFsX2Vt'
    'b2ppc190b19wcmVtaXVtKGRlbnlfdGV4dCkKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgZGVueV90ZXh0KQogICAgICAg'
    'IHJldHVybgogICAgYXJncyA9IG1lc3NhZ2UudGV4dC5zcGxpdChtYXhzcGxpdD0xKQogICAgaWYgbGVuKGFyZ3MpIDwgMjoKICAg'
    'ICAgICBlcnJfdGV4dCA9ICLinYwgPGI+SW5jb3JyZWN0IGZvcm1hdCE8L2I+XG5Db3JyZWN0IHVzYWdlOiA8Y29kZT4vYnJvYWRj'
    'YXN0ICZsdDttZXNzYWdlJmd0OzwvY29kZT4iCiAgICAgICAgZXJyX3RleHQgPSBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJl'
    'bWl1bShlcnJfdGV4dCkKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgZXJyX3RleHQpCiAgICAgICAgcmV0dXJuCiAgICBi'
    'cm9hZGNhc3RfbWVzc2FnZSA9IGFyZ3NbMV0KICAgIGJyb2FkY2FzdF9tZXNzYWdlID0gY29udmVydF9ub3JtYWxfZW1vamlzX3Rv'
    'X3ByZW1pdW0oYnJvYWRjYXN0X21lc3NhZ2UpCiAgICB0YXJnZXRfY2hhdHMgPSBzZXQoQUxMT1dFRF9HUk9VUFMpLnVuaW9uKHVz'
    'ZXJzKQogICAgaWYgbm90IHRhcmdldF9jaGF0czoKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgIvCfk60gTm8gdXNlcnMg'
    'b3IgZ3JvdXBzIHRvIGJyb2FkY2FzdCB0by4iKQogICAgICAgIHJldHVybgogICAgc3VjY2Vzc19jb3VudCA9IDAKICAgIGZhaWxf'
    'Y291bnQgPSAwCiAgICBzdGF0dXNfbXNnID0gYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19w'
    'cmVtaXVtKGYi8J+ToiA8Yj5Ccm9hZGNhc3Qgc3RhcnRlZC4uLjwvYj5cblNlbmRpbmcgdG8ge2xlbih0YXJnZXRfY2hhdHMpfSBj'
    'aGF0cy4iKSkKICAgIGZvciBjaGF0X2lkIGluIHRhcmdldF9jaGF0czoKICAgICAgICB0cnk6CiAgICAgICAgICAgIGJvdC5zZW5k'
    'X21lc3NhZ2UoY2hhdF9pZCwgYnJvYWRjYXN0X21lc3NhZ2UpCiAgICAgICAgICAgIHN1Y2Nlc3NfY291bnQgKz0gMQogICAgICAg'
    'IGV4Y2VwdCBFeGNlcHRpb24gYXMgZToKICAgICAgICAgICAgZmFpbF9jb3VudCArPSAxCiAgICAgICAgdGltZS5zbGVlcCgwLjEp'
    'CiAgICBmaW5hbF9yZXBvcnQgPSAoCiAgICAgICAgZiLwn4+BIDxiPkJyb2FkY2FzdCBGaW5pc2hlZCE8L2I+XG5cbiIKICAgICAg'
    'ICBmIuKchSBTZW50IHN1Y2Nlc3NmdWxseToge3N1Y2Nlc3NfY291bnR9XG4iCiAgICAgICAgZiLinYwgRmFpbGVkIHRvIHNlbmQ6'
    'IHtmYWlsX2NvdW50fSIKICAgICkKICAgIGZpbmFsX3JlcG9ydCA9IGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKGZp'
    'bmFsX3JlcG9ydCkKICAgIGJvdC5lZGl0X21lc3NhZ2VfdGV4dChmaW5hbF9yZXBvcnQsIG1lc3NhZ2UuY2hhdC5pZCwgc3RhdHVz'
    'X21zZy5tZXNzYWdlX2lkKQoKQGJvdC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WyJ1c2VsaW1pdCJdKQpkZWYgaGFuZGxlX3Nl'
    'dF91c2VyX2xpbWl0KG1lc3NhZ2UpOgogICAgaWYgbm90IGlzX2FkbWluKG1lc3NhZ2UuZnJvbV91c2VyLmlkKToKICAgICAgICBk'
    'ZW55X3RleHQgPSAi8J+aqyA8Yj5BY2Nlc3MgRGVuaWVkISBUaGlzIGNvbW1hbmQgaXMgZm9yIHRoZSBhZG1pbiBvbmx5LjwvYj4i'
    'CiAgICAgICAgZGVueV90ZXh0ID0gY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oZGVueV90ZXh0KQogICAgICAgIGJv'
    'dC5yZXBseV90byhtZXNzYWdlLCBkZW55X3RleHQpCiAgICAgICAgcmV0dXJuCiAgICBhcmdzID0gbWVzc2FnZS50ZXh0LnNwbGl0'
    'KCkKICAgIGlmIGxlbihhcmdzKSAhPSAzOgogICAgICAgIGVycl90ZXh0ID0gIuKdjCA8Yj5JbmNvcnJlY3QgZm9ybWF0ITwvYj5c'
    'bkNvcnJlY3QgdXNhZ2U6IDxjb2RlPi91c2VsaW1pdCB7dXNlcl9pZH0ge3VzZXN9PC9jb2RlPlxuRXhhbXBsZTogPGNvZGU+L3Vz'
    'ZWxpbWl0IDEyMzQ1Njc4OSAyPC9jb2RlPiIKICAgICAgICBlcnJfdGV4dCA9IGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVt'
    'aXVtKGVycl90ZXh0KQogICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCBlcnJfdGV4dCkKICAgICAgICByZXR1cm4KICAgIHRy'
    'eToKICAgICAgICB0YXJnZXRfdXNlcl9pZCA9IHN0cihhcmdzWzFdKQogICAgICAgIG5ld19saW1pdCA9IGludChhcmdzWzJdKQog'
    'ICAgICAgIGlmIG5vdCB0YXJnZXRfdXNlcl9pZC5pc2RpZ2l0KCkgb3IgbmV3X2xpbWl0IDwgMSBvciBuZXdfbGltaXQgPiAxMDAw'
    'OgogICAgICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLinYwgVXNlciBJRCB2YWxpZCBob25hIGNoYWhpeWUg'
    'YXVyIGxpbWl0IDHigJMxMDAwIGtlIGJlZWNoIGhvbmkgY2hhaGl5ZS4iKQogICAgICAgIHVzZXJfY3VzdG9tX2xpbWl0c1t0YXJn'
    'ZXRfdXNlcl9pZF0gPSBuZXdfbGltaXQKICAgICAgICBzYXZlX2RhdGEoKQogICAgICAgIHJlcGx5X3RleHQgPSBmIuKchSA8Yj5M'
    'aW1pdCBVcGRhdGVkITwvYj5cblxuVXNlciBJRDogPGNvZGU+e3RhcmdldF91c2VyX2lkfTwvY29kZT5cbk5ldyBEYWlseSBMaW1p'
    'dDogPGI+e25ld19saW1pdH08L2I+IGxpa2VzIHBlciBkYXkuIgogICAgICAgIHJlcGx5X3RleHQgPSBjb252ZXJ0X25vcm1hbF9l'
    'bW9qaXNfdG9fcHJlbWl1bShyZXBseV90ZXh0KQogICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCByZXBseV90ZXh0KQogICAg'
    'ZXhjZXB0IFZhbHVlRXJyb3I6CiAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLinYwgPGI+RXJyb3I6PC9iPiBVc2VyIElE'
    'IGFuZCBMaW1pdCBtdXN0IGJlIG51bWJlcnMuIikKCkBib3QubWVzc2FnZV9oYW5kbGVyKGNvbW1hbmRzPVsic2V0bGltaXQiLCAi'
    'YXBwcm92ZSIsICJkaXNhcHByb3ZlIiwgImxpc3Rncm91cHMiXSkKZGVmIGhhbmRsZV9hZG1pbl9jb21tYW5kcyhtZXNzYWdlKToK'
    'ICAgIGlmIG5vdCBpc19hZG1pbihtZXNzYWdlLmZyb21fdXNlci5pZCk6CiAgICAgICAgZGVueV90ZXh0ID0gIvCfmqsgPGI+QWNj'
    'ZXNzIERlbmllZCEgVGhpcyBjb21tYW5kIGlzIGZvciB0aGUgYWRtaW4gb25seS48L2I+IgogICAgICAgIGRlbnlfdGV4dCA9IGNv'
    'bnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKGRlbnlfdGV4dCkKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgZGVu'
    'eV90ZXh0KQogICAgICAgIHJldHVybgogICAgY29tbWFuZCwgKmFyZ3MgPSBtZXNzYWdlLnRleHQuc3BsaXQoKQogICAgdXNhZ2Vf'
    'dGV4dCA9ICIiCiAgICBpZiBjb21tYW5kID09ICIvYXBwcm92ZSIgYW5kIG5vdCBhcmdzOiB1c2FnZV90ZXh0ID0gIi9hcHByb3Zl'
    'IDxncm91cF9pZD4iCiAgICBlbGlmIGNvbW1hbmQgPT0gIi9kaXNhcHByb3ZlIiBhbmQgbm90IGFyZ3M6IHVzYWdlX3RleHQgPSAi'
    'L2Rpc2FwcHJvdmUgPGdyb3VwX2lkPiIKICAgIGVsaWYgY29tbWFuZCA9PSAiL3NldGxpbWl0IiBhbmQgbm90IGFyZ3M6IHVzYWdl'
    'X3RleHQgPSAiL3NldGxpbWl0IDxudW1iZXI+IgogICAgaWYgdXNhZ2VfdGV4dDoKICAgICAgICBib3QucmVwbHlfdG8obWVzc2Fn'
    'ZSwgZiLinYwgSW5jb3JyZWN0IGZvcm1hdCFcbkNvcnJlY3QgdXNhZ2U6IGB7dXNhZ2VfdGV4dH1gIiwgcGFyc2VfbW9kZT0iTWFy'
    'a2Rvd24iKQogICAgICAgIHJldHVybgogICAgdHJ5OgogICAgICAgIGlmIGNvbW1hbmQgPT0gIi9hcHByb3ZlIjoKICAgICAgICAg'
    'ICAgZ3JvdXBfaWQgPSBpbnQoYXJnc1swXSkKICAgICAgICAgICAgaWYgZ3JvdXBfaWQgaW4gQUxMT1dFRF9HUk9VUFM6CiAgICAg'
    'ICAgICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgIvCfkY0gVGhpcyBncm91cCBpcyBhbHJlYWR5IGFwcHJvdmVkLiIpCiAg'
    'ICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICBBTExPV0VEX0dST1VQUy5hcHBlbmQoZ3JvdXBfaWQpCiAgICAgICAgICAg'
    'ICAgICBzYXZlX2RhdGEoKQogICAgICAgICAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGYi4pyFIEdyb3VwIEFwcHJvdmVk'
    'IVxuSUQ6IGB7Z3JvdXBfaWR9YCIsIHBhcnNlX21vZGU9Ik1hcmtkb3duIikKICAgICAgICBlbGlmIGNvbW1hbmQgPT0gIi9kaXNh'
    'cHByb3ZlIjoKICAgICAgICAgICAgZ3JvdXBfaWQgPSBpbnQoYXJnc1swXSkKICAgICAgICAgICAgaWYgZ3JvdXBfaWQgaW4gQUxM'
    'T1dFRF9HUk9VUFM6CiAgICAgICAgICAgICAgICBBTExPV0VEX0dST1VQUy5yZW1vdmUoZ3JvdXBfaWQpCiAgICAgICAgICAgICAg'
    'ICBzYXZlX2RhdGEoKQogICAgICAgICAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGYi4p2MIEdyb3VwIERpc2FwcHJvdmVk'
    'IVxuSUQ6IGB7Z3JvdXBfaWR9YCIsIHBhcnNlX21vZGU9Ik1hcmtkb3duIikKICAgICAgICAgICAgZWxzZToKICAgICAgICAgICAg'
    'ICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCAi8J+kt+KAjeKZgu+4jyBUaGlzIGdyb3VwIGlzIG5vdCBpbiB0aGUgYXBwcm92ZWQg'
    'bGlzdC4iKQogICAgICAgIGVsaWYgY29tbWFuZCA9PSAiL2xpc3Rncm91cHMiOgogICAgICAgICAgICBpZiBub3QgQUxMT1dFRF9H'
    'Uk9VUFM6CiAgICAgICAgICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgIvCfk60gTm8gZ3JvdXBzIGFyZSBjdXJyZW50bHkg'
    'YXBwcm92ZWQuIikKICAgICAgICAgICAgZWxzZToKICAgICAgICAgICAgICAgIHRleHQgPSAi4pyFIEFwcHJvdmVkIEdyb3VwIElE'
    'czpcbiIgKyAiXG4iLmpvaW4oW2YiLSBge2dpZH1gIiBmb3IgZ2lkIGluIEFMTE9XRURfR1JPVVBTXSkKICAgICAgICAgICAgICAg'
    'IGJvdC5yZXBseV90byhtZXNzYWdlLCB0ZXh0LCBwYXJzZV9tb2RlPSJNYXJrZG93biIpCiAgICAgICAgZWxpZiBjb21tYW5kID09'
    'ICIvc2V0bGltaXQiOgogICAgICAgICAgICBnbG9iYWwgR0xPQkFMX0xJTUlUCiAgICAgICAgICAgIG5ld19nbG9iYWxfbGltaXQg'
    'PSBpbnQoYXJnc1swXSkKICAgICAgICAgICAgaWYgbmV3X2dsb2JhbF9saW1pdCA8IDEgb3IgbmV3X2dsb2JhbF9saW1pdCA+IDEw'
    'MDAwMDA6CiAgICAgICAgICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLinYwgR2xvYmFsIGxpbWl0IDHigJMx'
    'LDAwMCwwMDAga2UgYmVlY2ggaG9uaSBjaGFoaXllLiIpCiAgICAgICAgICAgIHdpdGggU1RBVEVfTE9DSzoKICAgICAgICAgICAg'
    'ICAgIEdMT0JBTF9MSU1JVCA9IG5ld19nbG9iYWxfbGltaXQKICAgICAgICAgICAgc2F2ZV9kYXRhKCkKICAgICAgICAgICAgcmVw'
    'bHlfdGV4dCA9IGYi4pyFIEdsb2JhbCBsaW1pdCBpcyBub3cgc2V0IHRvIHtHTE9CQUxfTElNSVR9LiIKICAgICAgICAgICAgcmVw'
    'bHlfdGV4dCA9IGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKHJlcGx5X3RleHQpCiAgICAgICAgICAgIGJvdC5yZXBs'
    'eV90byhtZXNzYWdlLCByZXBseV90ZXh0KQogICAgZXhjZXB0IChWYWx1ZUVycm9yLCBJbmRleEVycm9yKToKICAgICAgICBib3Qu'
    'cmVwbHlfdG8obWVzc2FnZSwgIuKame+4jyBJbnZhbGlkIGNvbW1hbmQgZm9ybWF0IG9yIElELiIpCiAgICBleGNlcHQgRXhjZXB0'
    'aW9uIGFzIGU6CiAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGYi4pqZ77iPIEFuIGVycm9yIG9jY3VycmVkOiB7ZX0iKQoK'
    'IyA9PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0KIyDwn4aVIEFQSSBTRVRURVIgQ09NTUFORFMKIyA9'
    'PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0KQGJvdC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WyJz'
    'ZXRsaWtlYXBpIl0pCmRlZiBoYW5kbGVfc2V0bGlrZWFwaShtZXNzYWdlKToKICAgIGlmIG5vdCBpc19hZG1pbihtZXNzYWdlLmZy'
    'b21fdXNlci5pZCk6CiAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLwn5qrIDxiPkFjY2VzcyBEZW5pZWQhIFRoaXMgY29t'
    'bWFuZCBpcyBmb3IgdGhlIGFkbWluIG9ubHkuPC9iPiIpCiAgICAgICAgcmV0dXJuCiAgICBhcmdzID0gbWVzc2FnZS50ZXh0LnNw'
    'bGl0KG1heHNwbGl0PTEpCiAgICBpZiBsZW4oYXJncykgIT0gMjoKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgIuKdjCA8'
    'Yj5Vc2FnZTo8L2I+IDxjb2RlPi9zZXRsaWtlYXBpIHthcGlfdXJsfTwvY29kZT4iKQogICAgICAgIHJldHVybgogICAgZ2xvYmFs'
    'IE1BTlVBTF9MSUtFX0FQSQogICAgaWYgbm90IGFyZ3NbMV0uc3RyaXAoKS5zdGFydHN3aXRoKCgiaHR0cHM6Ly8iLCAiaHR0cDov'
    'LyIpKToKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLinYwgQVBJIFVSTCBodHRwOi8vIHlhIGh0dHBzOi8v'
    'IHNlIHN0YXJ0IGhvbmEgY2hhaGl5ZS4iKQogICAgTUFOVUFMX0xJS0VfQVBJID0gYXJnc1sxXS5zdHJpcCgpLnJzdHJpcCgiLyIp'
    'CiAgICBzYXZlX2RhdGEoKQogICAgcmVwbHlfdGV4dCA9IGYi4pyFIDxiPk1hbnVhbCBMaWtlIEFQSSBVcGRhdGVkITwvYj5cbk5l'
    'dyBVUkw6IDxjb2RlPntNQU5VQUxfTElLRV9BUEl9PC9jb2RlPiIKICAgIHJlcGx5X3RleHQgPSBjb252ZXJ0X25vcm1hbF9lbW9q'
    'aXNfdG9fcHJlbWl1bShyZXBseV90ZXh0KQogICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIHJlcGx5X3RleHQpCgpAYm90Lm1lc3Nh'
    'Z2VfaGFuZGxlcihjb21tYW5kcz1bInNldGF1dG9hcGkiXSkKZGVmIGhhbmRsZV9zZXRhdXRvYXBpKG1lc3NhZ2UpOgogICAgaWYg'
    'bm90IGlzX2FkbWluKG1lc3NhZ2UuZnJvbV91c2VyLmlkKToKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgIvCfmqsgPGI+'
    'QWNjZXNzIERlbmllZCEgVGhpcyBjb21tYW5kIGlzIGZvciB0aGUgYWRtaW4gb25seS48L2I+IikKICAgICAgICByZXR1cm4KICAg'
    'IGFyZ3MgPSBtZXNzYWdlLnRleHQuc3BsaXQobWF4c3BsaXQ9MSkKICAgIGlmIGxlbihhcmdzKSAhPSAyOgogICAgICAgIGJvdC5y'
    'ZXBseV90byhtZXNzYWdlLCAi4p2MIDxiPlVzYWdlOjwvYj4gPGNvZGU+L3NldGF1dG9hcGkge2FwaV91cmx9PC9jb2RlPiIpCiAg'
    'ICAgICAgcmV0dXJuCiAgICBnbG9iYWwgQVVUT19MSUtFX0FQSQogICAgaWYgbm90IGFyZ3NbMV0uc3RyaXAoKS5zdGFydHN3aXRo'
    'KCgiaHR0cHM6Ly8iLCAiaHR0cDovLyIpKToKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLinYwgQVBJIFVS'
    'TCBodHRwOi8vIHlhIGh0dHBzOi8vIHNlIHN0YXJ0IGhvbmEgY2hhaGl5ZS4iKQogICAgQVVUT19MSUtFX0FQSSA9IGFyZ3NbMV0u'
    'c3RyaXAoKS5yc3RyaXAoIi8iKQogICAgc2F2ZV9kYXRhKCkKICAgIHJlcGx5X3RleHQgPSBmIuKchSA8Yj5BdXRvIExpa2UgQVBJ'
    'IFVwZGF0ZWQhPC9iPlxuTmV3IFVSTDogPGNvZGU+e0FVVE9fTElLRV9BUEl9PC9jb2RlPiIKICAgIHJlcGx5X3RleHQgPSBjb252'
    'ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShyZXBseV90ZXh0KQogICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIHJlcGx5X3Rl'
    'eHQpCgpAYm90Lm1lc3NhZ2VfaGFuZGxlcihjb21tYW5kcz1bImFkZGFkbWluIl0pCmRlZiBoYW5kbGVfYWRkYWRtaW4obWVzc2Fn'
    'ZSk6CiAgICBpZiBub3QgaXNfYWRtaW4obWVzc2FnZS5mcm9tX3VzZXIuaWQpOgogICAgICAgIGRlbnlfdGV4dCA9ICLwn5qrIDxi'
    'PkFjY2VzcyBEZW5pZWQhIFRoaXMgY29tbWFuZCBpcyBmb3IgYWRtaW5zIG9ubHkuPC9iPiIKICAgICAgICBkZW55X3RleHQgPSBj'
    'b252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShkZW55X3RleHQpCiAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGRl'
    'bnlfdGV4dCkKICAgICAgICByZXR1cm4KICAgIAogICAgIyBPbmx5IG9yaWdpbmFsIG93bmVyIGNhbiBhZGQgbmV3IGFkbWlucwog'
    'ICAgaWYgbWVzc2FnZS5mcm9tX3VzZXIuaWQgIT0gT1dORVJfSUQ6CiAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZl'
    'cnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKCLwn5qrIDxiPk9ubHkgdGhlIE93bmVyIGNhbiBhZGQgbmV3IGFkbWlucy48L2I+'
    'IikpCiAgICAgICAgcmV0dXJuCiAgICAKICAgIGFyZ3MgPSBtZXNzYWdlLnRleHQuc3BsaXQoKQogICAgIyAvYWRkYWRtaW4gPHVz'
    'ZXJfaWQ+IDxkYXlzPiBbbWF4X2F1dG9saWtlXQogICAgaWYgbGVuKGFyZ3MpIDwgMzoKICAgICAgICBlcnJfdGV4dCA9ICgKICAg'
    'ICAgICAgICAgIuKdjCA8Yj5JbmNvcnJlY3QgZm9ybWF0ITwvYj5cbiIKICAgICAgICAgICAgIkNvcnJlY3QgdXNhZ2U6IDxjb2Rl'
    'Pi9hZGRhZG1pbiAmbHQ7dXNlcl9pZCZndDsgJmx0O2RheXMmZ3Q7IFttYXhfYXV0b2xpa2VdPC9jb2RlPlxuIgogICAgICAgICAg'
    'ICAiRXhhbXBsZTogPGNvZGU+L2FkZGFkbWluIDEyMzQ1Njc4OSA3IDU8L2NvZGU+XG4iCiAgICAgICAgICAgICLigKIgZGF5cyA9'
    'IGtpdG5lIGRpbiBrZSBsaXllIGFkbWluIHJhaGVnYSAoMCA9IHBlcm1hbmVudClcbiIKICAgICAgICAgICAgIuKAoiBtYXhfYXV0'
    'b2xpa2UgPSBvcHRpb25hbCwga2l0bmkgYmFhciAvYXV0b2xpa2UgdXNlIGthciBzYWt0YSBoYWkgKGJsYW5rID0gdW5saW1pdGVk'
    'KSIKICAgICAgICApCiAgICAgICAgZXJyX3RleHQgPSBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShlcnJfdGV4dCkK'
    'ICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgZXJyX3RleHQpCiAgICAgICAgcmV0dXJuCiAgICAKICAgIHRyeToKICAgICAg'
    'ICBuZXdfYWRtaW5faWQgPSBpbnQoYXJnc1sxXSkKICAgICAgICBkYXlzID0gaW50KGFyZ3NbMl0pCiAgICAgICAgbWF4X2F1dG9s'
    'aWtlID0gaW50KGFyZ3NbM10pIGlmIGxlbihhcmdzKSA+PSA0IGVsc2UgTm9uZQogICAgICAgIAogICAgICAgIGlmIG5ld19hZG1p'
    'bl9pZCA9PSBPV05FUl9JRDoKICAgICAgICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190'
    'b19wcmVtaXVtKCLwn5qrIE93bmVyIGFscmVhZHkgcGVybWFuZW50IGFkbWluIGhhaS4iKSkKICAgICAgICAgICAgcmV0dXJuCiAg'
    'ICAgICAgCiAgICAgICAgaWYgZGF5cyA8IDA6CiAgICAgICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1h'
    'bF9lbW9qaXNfdG9fcHJlbWl1bSgi4p2MIERheXMgMCB5YSB1c3NlIHp5YWRhIGhvbmEgY2hhaGl5ZS4iKSkKICAgICAgICAgICAg'
    'cmV0dXJuCiAgICAgICAgCiAgICAgICAgZXhwaXJlcyA9IE5vbmUgaWYgZGF5cyA9PSAwIGVsc2UgdGltZS50aW1lKCkgKyAoZGF5'
    'cyAqIDI0ICogNjAgKiA2MCkKICAgICAgICAKICAgICAgICBpZiBtYXhfYXV0b2xpa2UgaXMgbm90IE5vbmUgYW5kIG1heF9hdXRv'
    'bGlrZSA8IDA6CiAgICAgICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgIuKdjCBtYXhfYXV0b2xpa2UgMCB5YSB1'
    'c3NlIHp5YWRhIGhvbmEgY2hhaGl5ZS4iKQogICAgICAgIEFETUlOU1tuZXdfYWRtaW5faWRdID0gewogICAgICAgICAgICAiZXhw'
    'aXJlcyI6IGV4cGlyZXMsCiAgICAgICAgICAgICJhdXRvbGlrZV9saW1pdCI6IG1heF9hdXRvbGlrZSwKICAgICAgICAgICAgImF1'
    'dG9saWtlX3VzZWQiOiAwCiAgICAgICAgfQogICAgICAgIHNhdmVfZGF0YSgpCiAgICAgICAgCiAgICAgICAgZGF5c190ZXh0ID0g'
    'IlBlcm1hbmVudCIgaWYgZGF5cyA9PSAwIGVsc2UgZiJ7ZGF5c30gZGF5cyIKICAgICAgICBsaW1pdF90ZXh0ID0gIlVubGltaXRl'
    'ZCIgaWYgbWF4X2F1dG9saWtlIGlzIE5vbmUgZWxzZSBzdHIobWF4X2F1dG9saWtlKQogICAgICAgIAogICAgICAgIHJlcGx5X3Rl'
    'eHQgPSAoCiAgICAgICAgICAgIGYi4pyFIDxiPk5ldyBBZG1pbiBBZGRlZCE8L2I+XG5cbiIKICAgICAgICAgICAgZiLwn5GkIFVz'
    'ZXIgSUQ6IDxjb2RlPntuZXdfYWRtaW5faWR9PC9jb2RlPlxuIgogICAgICAgICAgICBmIuKPsyBEdXJhdGlvbjogPGI+e2RheXNf'
    'dGV4dH08L2I+XG4iCiAgICAgICAgICAgIGYi8J+agCBBdXRvbGlrZSBMaW1pdDogPGI+e2xpbWl0X3RleHR9PC9iPlxuIgogICAg'
    'ICAgICAgICBmIvCfk4ogVG90YWwgQWRtaW5zOiA8Yj57bGVuKEFETUlOUyl9PC9iPiIKICAgICAgICApCiAgICAgICAgcmVwbHlf'
    'dGV4dCA9IGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKHJlcGx5X3RleHQpCiAgICAgICAgYm90LnJlcGx5X3RvKG1l'
    'c3NhZ2UsIHJlcGx5X3RleHQpCiAgICAgICAgCiAgICAgICAgIyBOb3RpZnkgbmV3IGFkbWluCiAgICAgICAgdHJ5OgogICAgICAg'
    'ICAgICBub3RpZnkgPSAoCiAgICAgICAgICAgICAgICBmIvCfjokgPGI+WW91IGhhdmUgYmVlbiBtYWRlIGFuIEFkbWluITwvYj5c'
    'blxuIgogICAgICAgICAgICAgICAgZiLij7MgRHVyYXRpb246IDxiPntkYXlzX3RleHR9PC9iPlxuIgogICAgICAgICAgICAgICAg'
    'ZiLwn5qAIEF1dG9saWtlIHVzZXMgYWxsb3dlZDogPGI+e2xpbWl0X3RleHR9PC9iPlxuIgogICAgICAgICAgICAgICAgZiJUeXBl'
    'IC9oZWxwIHRvIHNlZSBhbGwgYWRtaW4gY29tbWFuZHMuIgogICAgICAgICAgICApCiAgICAgICAgICAgIGJvdC5zZW5kX21lc3Nh'
    'Z2UobmV3X2FkbWluX2lkLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShub3RpZnkpKQogICAgICAgIGV4Y2VwdDoK'
    'ICAgICAgICAgICAgcGFzcwogICAgICAgICAgICAKICAgIGV4Y2VwdCBWYWx1ZUVycm9yOgogICAgICAgIGJvdC5yZXBseV90byht'
    'ZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgi4p2MIDxiPkVycm9yOjwvYj4gVXNlciBJRCwgZGF5cyBh'
    'bmQgbWF4X2F1dG9saWtlIG51bWJlcnMgaG9uZSBjaGFoaXllLiIpKQoKQGJvdC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WyJy'
    'ZW1vdmVhZG1pbiJdKQpkZWYgaGFuZGxlX3JlbW92ZWFkbWluKG1lc3NhZ2UpOgogICAgaWYgbm90IGlzX2FkbWluKG1lc3NhZ2Uu'
    'ZnJvbV91c2VyLmlkKToKICAgICAgICBkZW55X3RleHQgPSAi8J+aqyA8Yj5BY2Nlc3MgRGVuaWVkISBUaGlzIGNvbW1hbmQgaXMg'
    'Zm9yIGFkbWlucyBvbmx5LjwvYj4iCiAgICAgICAgZGVueV90ZXh0ID0gY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0o'
    'ZGVueV90ZXh0KQogICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCBkZW55X3RleHQpCiAgICAgICAgcmV0dXJuCiAgICAKICAg'
    'ICMgT25seSBPd25lciBjYW4gcmVtb3ZlIGFkbWlucwogICAgaWYgbWVzc2FnZS5mcm9tX3VzZXIuaWQgIT0gT1dORVJfSUQ6CiAg'
    'ICAgICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKCLwn5qrIDxiPk9ubHkg'
    'dGhlIE93bmVyIGNhbiByZW1vdmUgYWRtaW5zLjwvYj4iKSkKICAgICAgICByZXR1cm4KICAgIAogICAgYXJncyA9IG1lc3NhZ2Uu'
    'dGV4dC5zcGxpdCgpCiAgICBpZiBsZW4oYXJncykgIT0gMjoKICAgICAgICBlcnJfdGV4dCA9ICLinYwgPGI+SW5jb3JyZWN0IGZv'
    'cm1hdCE8L2I+XG5Db3JyZWN0IHVzYWdlOiA8Y29kZT4vcmVtb3ZlYWRtaW4gJmx0O3VzZXJfaWQmZ3Q7PC9jb2RlPlxuRXhhbXBs'
    'ZTogPGNvZGU+L3JlbW92ZWFkbWluIDEyMzQ1Njc4OTwvY29kZT4iCiAgICAgICAgZXJyX3RleHQgPSBjb252ZXJ0X25vcm1hbF9l'
    'bW9qaXNfdG9fcHJlbWl1bShlcnJfdGV4dCkKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgZXJyX3RleHQpCiAgICAgICAg'
    'cmV0dXJuCiAgICAKICAgIHRyeToKICAgICAgICB0YXJnZXRfaWQgPSBpbnQoYXJnc1sxXSkKICAgICAgICAKICAgICAgICBpZiB0'
    'YXJnZXRfaWQgPT0gT1dORVJfSUQ6CiAgICAgICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9q'
    'aXNfdG9fcHJlbWl1bSgi8J+aqyA8Yj5Pd25lciBrbyBrYWJoaSByZW1vdmUgbmFoaSBraXlhIGphIHNha3RhITwvYj4iKSkKICAg'
    'ICAgICAgICAgcmV0dXJuCiAgICAgICAgCiAgICAgICAgaWYgdGFyZ2V0X2lkIG5vdCBpbiBBRE1JTlM6CiAgICAgICAgICAgIGJv'
    'dC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShmIuKdjCBVc2VyIDxjb2RlPnt0YXJn'
    'ZXRfaWR9PC9jb2RlPiBhZG1pbiBuYWhpIGhhaS4iKSkKICAgICAgICAgICAgcmV0dXJuCiAgICAgICAgCiAgICAgICAgZGVsIEFE'
    'TUlOU1t0YXJnZXRfaWRdCiAgICAgICAgc2F2ZV9kYXRhKCkKICAgICAgICByZXBseV90ZXh0ID0gZiLinIUgPGI+QWRtaW4gUmVt'
    'b3ZlZCE8L2I+XG5Vc2VyIElEOiA8Y29kZT57dGFyZ2V0X2lkfTwvY29kZT5cblJlbWFpbmluZyBBZG1pbnM6IDxiPntsZW4oQURN'
    'SU5TKX08L2I+IgogICAgICAgIHJlcGx5X3RleHQgPSBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShyZXBseV90ZXh0'
    'KQogICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCByZXBseV90ZXh0KQogICAgICAgIAogICAgICAgIHRyeToKICAgICAgICAg'
    'ICAgYm90LnNlbmRfbWVzc2FnZSh0YXJnZXRfaWQsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKCLimqDvuI8gPGI+'
    'WW91IGhhdmUgYmVlbiByZW1vdmVkIGFzIEFkbWluLjwvYj4iKSkKICAgICAgICBleGNlcHQ6CiAgICAgICAgICAgIHBhc3MKICAg'
    'ICAgICAgICAgCiAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICBib3QucmVwbHlfdG8obWVzc2FnZSwgY29udmVydF9ub3Jt'
    'YWxfZW1vamlzX3RvX3ByZW1pdW0oIuKdjCA8Yj5FcnJvcjo8L2I+IFVzZXIgSUQgbXVzdCBiZSBhIG51bWJlci4iKSkKCkBib3Qu'
    'bWVzc2FnZV9oYW5kbGVyKGNvbW1hbmRzPVsib24iXSkKZGVmIGhhbmRsZV9vbihtZXNzYWdlKToKICAgIGdsb2JhbCBCT1RfRU5B'
    'QkxFRAogICAgaWYgbm90IGlzX2FkbWluKG1lc3NhZ2UuZnJvbV91c2VyLmlkKToKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3Rv'
    'KG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKCLwn5qrIDxiPkFjY2VzcyBEZW5pZWQhPC9iPiIpKQog'
    'ICAgaWYgbWVzc2FnZS5mcm9tX3VzZXIuaWQgIT0gT1dORVJfSUQ6CiAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdl'
    'LCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgi8J+aqyA8Yj5Pbmx5IE93bmVyIGNhbiB0dXJuIHRoZSBib3QgT04u'
    'PC9iPiIpKQogICAgCiAgICBCT1RfRU5BQkxFRCA9IFRydWUKICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1h'
    'bF9lbW9qaXNfdG9fcHJlbWl1bSgi4pyFIDxiPkJvdCBpcyBub3cgT04hPC9iPlxuVXNlcnMgY2FuIHVzZSB0aGUgYm90IGFnYWlu'
    'LiIpKQoKQGJvdC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WyJvZmYiXSkKZGVmIGhhbmRsZV9vZmYobWVzc2FnZSk6CiAgICBn'
    'bG9iYWwgQk9UX0VOQUJMRUQKICAgIGlmIG5vdCBpc19hZG1pbihtZXNzYWdlLmZyb21fdXNlci5pZCk6CiAgICAgICAgcmV0dXJu'
    'IGJvdC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgi8J+aqyA8Yj5BY2Nlc3MgRGVu'
    'aWVkITwvYj4iKSkKICAgIGlmIG1lc3NhZ2UuZnJvbV91c2VyLmlkICE9IE9XTkVSX0lEOgogICAgICAgIHJldHVybiBib3QucmVw'
    'bHlfdG8obWVzc2FnZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oIvCfmqsgPGI+T25seSBPd25lciBjYW4gdHVy'
    'biB0aGUgYm90IE9GRi48L2I+IikpCiAgICAKICAgIEJPVF9FTkFCTEVEID0gRmFsc2UKIyDwn5SQIEdST1VQLUJBU0VEIC9saWtl'
    'cyBBQ0NFU1MKIyA9PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0KIyBPbmx5IE9XTkVSIGNhbiBhbGxv'
    'dy9kaXNhbGxvdyAvbGlrZXMgZm9yIGdyb3Vwcy4KIyBBcHByb3ZlZCBncm91cHMgYXJlIHN0b3JlZCBpbiBtZW1vcnk6IHtjaGF0'
    'X2lkOiBncm91cF90aXRsZX0KTElLRVNfQUxMT1dFRF9HUk9VUFMgPSB7fQoKZGVmIGlzX2xpa2VzX2dyb3VwX2FsbG93ZWQoY2hh'
    'dF9pZCk6CiAgICByZXR1cm4gY2hhdF9pZCBpbiBMSUtFU19BTExPV0VEX0dST1VQUwoKZGVmIGxpa2VzX2dyb3VwX3RpdGxlKG1l'
    'c3NhZ2UpOgogICAgdGl0bGUgPSBnZXRhdHRyKG1lc3NhZ2UuY2hhdCwgInRpdGxlIiwgTm9uZSkKICAgIHJldHVybiB0aXRsZSBv'
    'ciBzdHIobWVzc2FnZS5jaGF0LmlkKQoKQGJvdC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WyJsaWtlc2FsbG93Il0pCmRlZiBo'
    'YW5kbGVfbGlrZXNhbGxvdyhtZXNzYWdlKToKICAgIGlmIG1lc3NhZ2UuZnJvbV91c2VyLmlkICE9IE9XTkVSX0lEOgogICAgICAg'
    'IHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAg'
    'ICLinYwgPGI+T25seSBvd25lciBjYW4gdXNlIHRoaXMgY29tbWFuZC48L2I+IgogICAgICAgICkpCiAgICBjaGF0ID0gbWVzc2Fn'
    'ZS5jaGF0CiAgICBpZiBjaGF0LnR5cGUgPT0gInByaXZhdGUiOgogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwg'
    'Y29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAgICLinYwgPGI+WWUgY29tbWFuZCBncm91cCBtZWlu'
    'IHVzZSBrYXJvLjwvYj4iCiAgICAgICAgKSkKICAgIGlmIGNoYXQuaWQgaW4gTElLRVNfQUxMT1dFRF9HUk9VUFM6CiAgICAgICAg'
    'cmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgKICAgICAgICAgICAg'
    'IuKEue+4jyA8Yj5JcyBncm91cCBtZWluIC9saWtlcyBhbHJlYWR5IGFsbG93ZWQgaGFpLjwvYj4iCiAgICAgICAgKSkKICAgIExJ'
    'S0VTX0FMTE9XRURfR1JPVVBTW2NoYXQuaWRdID0gbGlrZXNfZ3JvdXBfdGl0bGUobWVzc2FnZSkKICAgIHNhdmVfZGF0YSgpCiAg'
    'ICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKAogICAgICAgICLi'
    'nIUgPGI+L2xpa2VzIGVuYWJsZWQgZm9yIHRoaXMgZ3JvdXAuPC9iPlxuIgogICAgICAgIGYi8J+Pt++4jyA8Yj5Hcm91cDo8L2I+'
    'IHtsaWtlc19ncm91cF90aXRsZShtZXNzYWdlKX1cbiIKICAgICAgICBmIvCfhpQgPGI+Q2hhdCBJRDo8L2I+IDxjb2RlPntjaGF0'
    'LmlkfTwvY29kZT5cbiIKICAgICAgICBmIvCfk4ogPGI+QWxsb3dlZCBHcm91cHM6PC9iPiB7bGVuKExJS0VTX0FMTE9XRURfR1JP'
    'VVBTKX0iCiAgICApKQoKQGJvdC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WyJsaWtlc2Rpc2FsbG93Il0pCmRlZiBoYW5kbGVf'
    'bGlrZXNkaXNhbGxvdyhtZXNzYWdlKToKICAgIGlmIG1lc3NhZ2UuZnJvbV91c2VyLmlkICE9IE9XTkVSX0lEOgogICAgICAgIHJl'
    'dHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAgICLi'
    'nYwgPGI+T25seSBvd25lciBjYW4gdXNlIHRoaXMgY29tbWFuZC48L2I+IgogICAgICAgICkpCiAgICBjaGF0ID0gbWVzc2FnZS5j'
    'aGF0CiAgICBpZiBjaGF0LnR5cGUgPT0gInByaXZhdGUiOgogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgY29u'
    'dmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAgICLinYwgPGI+WWUgY29tbWFuZCBncm91cCBtZWluIHVz'
    'ZSBrYXJvLjwvYj4iCiAgICAgICAgKSkKICAgIGlmIGNoYXQuaWQgbm90IGluIExJS0VTX0FMTE9XRURfR1JPVVBTOgogICAgICAg'
    'IHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAg'
    'ICLihLnvuI8gPGI+SXMgZ3JvdXAgbWVpbiAvbGlrZXMgYWxyZWFkeSBkaXNhYmxlZCBoYWkuPC9iPiIKICAgICAgICApKQogICAg'
    'ZGVsIExJS0VTX0FMTE9XRURfR1JPVVBTW2NoYXQuaWRdCiAgICBzYXZlX2RhdGEoKQogICAgcmV0dXJuIGJvdC5yZXBseV90byht'
    'ZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgKICAgICAgICAi8J+aqyA8Yj4vbGlrZXMgZGlzYWJsZWQg'
    'Zm9yIHRoaXMgZ3JvdXAuPC9iPlxuIgogICAgICAgIGYi8J+GlCA8Yj5DaGF0IElEOjwvYj4gPGNvZGU+e2NoYXQuaWR9PC9jb2Rl'
    'PlxuIgogICAgICAgIGYi8J+TiiA8Yj5BbGxvd2VkIEdyb3Vwczo8L2I+IHtsZW4oTElLRVNfQUxMT1dFRF9HUk9VUFMpfSIKICAg'
    'ICkpCgpAYm90Lm1lc3NhZ2VfaGFuZGxlcihjb21tYW5kcz1bImxpa2VzZ3JvdXBzIl0pCmRlZiBoYW5kbGVfbGlrZXNncm91cHMo'
    'bWVzc2FnZSk6CiAgICBpZiBtZXNzYWdlLmZyb21fdXNlci5pZCAhPSBPV05FUl9JRDoKICAgICAgICByZXR1cm4gYm90LnJlcGx5'
    'X3RvKG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKAogICAgICAgICAgICAi4p2MIDxiPk9ubHkgb3du'
    'ZXIgY2FuIHVzZSB0aGlzIGNvbW1hbmQuPC9iPiIKICAgICAgICApKQogICAgaWYgbm90IExJS0VTX0FMTE9XRURfR1JPVVBTOgog'
    'ICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAg'
    'ICAgICAgICLwn5OtIDxiPktvaSBncm91cCAvbGlrZXMga2UgbGl5ZSBhbGxvd2VkIG5haGkgaGFpLjwvYj4iCiAgICAgICAgKSkK'
    'ICAgIGxpbmVzID0gWyLwn5OLIDxiPi9saWtlcyBBbGxvd2VkIEdyb3VwczwvYj4iLCAi4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB'
    '4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSBIl0KICAgIGZvciBpLCAoY2hhdF9pZCwgdGl0bGUpIGluIGVudW1lcmF0ZShMSUtF'
    'U19BTExPV0VEX0dST1VQUy5pdGVtcygpLCAxKToKICAgICAgICBsaW5lcy5hcHBlbmQoZiJ7aX0uIPCfj7fvuI8gPGI+e3RpdGxl'
    'fTwvYj5cbiAgIPCfhpQgPGNvZGU+e2NoYXRfaWR9PC9jb2RlPiIpCiAgICBsaW5lcy5hcHBlbmQoIuKUgeKUgeKUgeKUgeKUgeKU'
    'geKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgeKUgSIpCiAgICBsaW5lcy5hcHBlbmQoZiLwn5OKIDxiPlRvdGFsOjwvYj4g'
    'e2xlbihMSUtFU19BTExPV0VEX0dST1VQUyl9IGdyb3VwcyIpCiAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZl'
    'cnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKCJcbiIuam9pbihsaW5lcykpKQoKIyA9PT09PT09PT09PT09PT09PT09PT09PT09'
    'PT09PT09PT09PT09PT09PT0KIyDinaTvuI8gSU5TVEFOVCBBVVRPLUxJS0UgQ09NTUFORCAoL2xpa2VzKQojID09PT09PT09PT09'
    'PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQojIE93bmVyIGNhbiBhcHByb3ZlIGFueSB1c2VyL2FkbWluIHdpdGggYSBm'
    'aXhlZCBudW1iZXIgb2YgL2xpa2VzIHVzZXMuCiMgT3duZXIgaGltc2VsZiBoYXMgdW5saW1pdGVkIC9saWtlcyB1c2UuCkxJS0VT'
    'X0FQUFJPVkFMUyA9IHt9ICAjIHt1c2VyX2lkOiB7ImxpbWl0IjogaW50LCAidXNlZCI6IGludH19CgpkZWYgaGFzX2xpa2VzX2Fj'
    'Y2Vzcyh1c2VyX2lkKToKICAgIGlmIHVzZXJfaWQgPT0gT1dORVJfSUQ6CiAgICAgICAgcmV0dXJuIFRydWUKICAgIGRhdGEgPSBM'
    'SUtFU19BUFBST1ZBTFMuZ2V0KHVzZXJfaWQpCiAgICBpZiBkYXRhIGlzIE5vbmU6CiAgICAgICAgcmV0dXJuIEZhbHNlCiAgICBl'
    'eHBpcmVzID0gZGF0YS5nZXQoImV4cGlyZXMiKQogICAgaWYgZXhwaXJlcyBpcyBub3QgTm9uZSBhbmQgdGltZS50aW1lKCkgPj0g'
    'ZmxvYXQoZXhwaXJlcyk6CiAgICAgICAgcmV0dXJuIEZhbHNlCiAgICByZXR1cm4gaW50KGRhdGEuZ2V0KCJ1c2VkIiwgMCkpIDwg'
    'aW50KGRhdGEuZ2V0KCJsaW1pdCIsIDApKQoKCkBib3QubWVzc2FnZV9oYW5kbGVyKGNvbW1hbmRzPVsibGlrZXNyZW1vdmUiXSkK'
    'ZGVmIGhhbmRsZV9saWtlc3JlbW92ZShtZXNzYWdlKToKICAgIGlmIG1lc3NhZ2UuZnJvbV91c2VyLmlkICE9IE9XTkVSX0lEOgog'
    'ICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgIuKdjCA8Yj5Pbmx5IG93bmVyIGNhbiByZW1vdmUgL2xpa2VzIGFw'
    'cHJvdmFsLjwvYj4iKQogICAgYXJncyA9IG1lc3NhZ2UudGV4dC5zcGxpdCgpCiAgICBpZiBsZW4oYXJncykgIT0gMiBvciBub3Qg'
    'YXJnc1sxXS5pc2RpZ2l0KCk6CiAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCAi4p2MIDxiPlVzYWdlOjwvYj4g'
    'PGNvZGU+L2xpa2VzcmVtb3ZlICZsdDt1c2VyX2lkJmd0OzwvY29kZT4iKQogICAgdGFyZ2V0X3VzZXJfaWQgPSBpbnQoYXJnc1sx'
    'XSkKICAgIGlmIHRhcmdldF91c2VyX2lkID09IE9XTkVSX0lEOgogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwg'
    'IvCfkZEgPGI+T3duZXIgYXBwcm92YWwgY2Fubm90IGJlIHJlbW92ZWQuPC9iPiIpCiAgICBpZiBMSUtFU19BUFBST1ZBTFMucG9w'
    'KHRhcmdldF91c2VyX2lkLCBOb25lKSBpcyBOb25lOgogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgZiLihLnv'
    'uI8gPGI+VXNlciA8Y29kZT57dGFyZ2V0X3VzZXJfaWR9PC9jb2RlPiBoYXMgbm8gL2xpa2VzIGFwcHJvdmFsLjwvYj4iKQogICAg'
    'c2F2ZV9kYXRhKCkKICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgZiLwn5eRIDxiPi9saWtlcyBhcHByb3ZhbCByZW1v'
    'dmVkLjwvYj5cbvCfhpQgVXNlciBJRDogPGNvZGU+e3RhcmdldF91c2VyX2lkfTwvY29kZT4iKQoKQGJvdC5tZXNzYWdlX2hhbmRs'
    'ZXIoY29tbWFuZHM9WyJsaWtlcyJdKQpkZWYgaGFuZGxlX2xpa2VzKG1lc3NhZ2UpOgogICAgdXNlcl9pZCA9IG1lc3NhZ2UuZnJv'
    'bV91c2VyLmlkCiAgICBhcmdzID0gbWVzc2FnZS50ZXh0LnNwbGl0KCkKCiAgICAjIE93bmVyLWFwcHJvdmVkIHVzZXJzIGNhbiB1'
    'c2UgL2xpa2VzIGV2ZW4gb3V0c2lkZSBhbiBhbGxvd2VkIGdyb3VwLgogICAgIyBPdGhlciB1c2VycyBzdGlsbCBuZWVkIHRoZSBj'
    'dXJyZW50IGdyb3VwIHRvIGJlIG93bmVyLWFwcHJvdmVkLgogICAgaWYgKHVzZXJfaWQgIT0gT1dORVJfSUQKICAgICAgICAgICAg'
    'YW5kIG5vdCBoYXNfbGlrZXNfYWNjZXNzKHVzZXJfaWQpCiAgICAgICAgICAgIGFuZCBub3QgaXNfbGlrZXNfZ3JvdXBfYWxsb3dl'
    'ZChtZXNzYWdlLmNoYXQuaWQpKToKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vt'
    'b2ppc190b19wcmVtaXVtKAogICAgICAgICAgICAi4p2MIDxiPllvdSBkb24ndCBoYXZlIHBlcm1pc3Npb24gdG8gdXNlIHRoaXMg'
    'Y29tbWFuZCE8L2I+XG4iCiAgICAgICAgICAgICJDb250YWN0IDxiPkBwcmluY2VnbXI8L2I+IHRvIHJlcXVlc3QiCiAgICAgICAg'
    'KSkKCiAgICAjIEZvcmNlIGpvaW4gKGxpa2VzLnB5IHN0eWxlKSAtIE93bmVyIGFsd2F5cyBhbGxvd2VkCiAgICBpZiB1c2VyX2lk'
    'ICE9IE9XTkVSX0lEIGFuZCBub3QgaXNfbWVtYmVyKHVzZXJfaWQpOgogICAgICAgIHNlbmRfZm9yY2Vfam9pbl9wcm9tcHQobWVz'
    'c2FnZSkKICAgICAgICByZXR1cm4KCiAgICAjIE93bmVyIGFwcHJvdmFsOiAvbGlrZXMgPHVzZXJfaWQ+IDx1c2VzPiA8ZGF5cz4K'
    'ICAgICMgTm9ybWFsIC9saWtlcyA8cmVnaW9uPiA8dWlkPiByZXF1ZXN0cyBjb250aW51ZSB0byB1c2UgdGhyZWUgYXJndW1lbnRz'
    'LgogICAgaWYgdXNlcl9pZCA9PSBPV05FUl9JRCBhbmQgbGVuKGFyZ3MpID09IDQ6CiAgICAgICAgdHJ5OgogICAgICAgICAgICB0'
    'YXJnZXRfdXNlcl9pZCA9IGludChhcmdzWzFdKQogICAgICAgICAgICB1c2VzID0gaW50KGFyZ3NbMl0pCiAgICAgICAgICAgIGRh'
    'eXMgPSBpbnQoYXJnc1szXSkKICAgICAgICAgICAgaWYgdGFyZ2V0X3VzZXJfaWQgPD0gMCBvciB1c2VzIDwgMSBvciBkYXlzIDwg'
    'MToKICAgICAgICAgICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgIuKdjCA8Yj5Vc2VyIElEIHBvc2l0aXZlLCB1'
    'c2VzIGF1ciBkYXlzIGthbSBzZSBrYW0gMSBob25lIGNoYWhpeWUuPC9iPiIpCiAgICAgICAgICAgIGlmIHRhcmdldF91c2VyX2lk'
    'ID09IE9XTkVSX0lEOgogICAgICAgICAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCAi8J+RkSA8Yj5Pd25lciBr'
    'byBhcHByb3ZhbCBraSB6YXJ1cmF0IG5haGkgaGFpLjwvYj4iKQogICAgICAgICAgICBleHBpcmVzID0gdGltZS50aW1lKCkgKyAo'
    'ZGF5cyAqIDI0ICogNjAgKiA2MCkKICAgICAgICAgICAgTElLRVNfQVBQUk9WQUxTW3RhcmdldF91c2VyX2lkXSA9IHsibGltaXQi'
    'OiB1c2VzLCAidXNlZCI6IDAsICJleHBpcmVzIjogZXhwaXJlc30KICAgICAgICAgICAgc2F2ZV9kYXRhKCkKICAgICAgICAgICAg'
    'cmVwbHlfdGV4dCA9ICgKICAgICAgICAgICAgICAgICLinIUgPGI+L2xpa2VzIEFwcHJvdmVkITwvYj5cblxuIgogICAgICAgICAg'
    'ICAgICAgZiLwn4aUIFVzZXIgSUQ6IDxjb2RlPnt0YXJnZXRfdXNlcl9pZH08L2NvZGU+XG4iCiAgICAgICAgICAgICAgICBmIvCf'
    'lKIgQWxsb3dlZCBVc2VzOiA8Yj57dXNlc308L2I+XG4iCiAgICAgICAgICAgICAgICBmIuKPsyBWYWxpZGl0eTogPGI+e2RheXN9'
    'IGRheXM8L2I+XG4iCiAgICAgICAgICAgICAgICAi4p2k77iPIENvbW1hbmQ6IDxjb2RlPi9saWtlcyAmbHQ7cmVnaW9uJmd0OyAm'
    'bHQ7dWlkJmd0OzwvY29kZT4iCiAgICAgICAgICAgICkKICAgICAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCBj'
    'b252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShyZXBseV90ZXh0KSkKICAgICAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAg'
    'ICAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCAi4p2MIDxiPlVzYWdlOjwvYj4gPGNvZGU+L2xpa2VzICZsdDt1'
    'c2VyX2lkJmd0OyAmbHQ7dXNlcyZndDsgJmx0O2RheXMmZ3Q7PC9jb2RlPlxuRXhhbXBsZTogPGNvZGU+L2xpa2VzIDEyMzQ1Njc4'
    'OSA1IDc8L2NvZGU+IikKCiAgICAjIFVzZXIvQWRtaW46IC9saWtlcyA8cmVnaW9uPiA8dWlkPgogICAgaWYgbGVuKGFyZ3MpICE9'
    'IDM6CiAgICAgICAgaWYgdXNlcl9pZCA9PSBPV05FUl9JRDoKICAgICAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdl'
    'LCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgKICAgICAgICAgICAgICAgICLinYwgPGI+VXNhZ2U6PC9iPlxuIgog'
    'ICAgICAgICAgICAgICAgIjxjb2RlPi9saWtlcyAmbHQ7cmVnaW9uJmd0OyAmbHQ7dWlkJmd0OzwvY29kZT4gLSBJbnN0YW50IEF1'
    'dG9MaWtlXG4iCiAgICAgICAgICAgICAgICAiPGNvZGU+L2xpa2VzICZsdDt1c2VyX2lkJmd0OyAmbHQ7dXNlcyZndDsgJmx0O2Rh'
    'eXMmZ3Q7PC9jb2RlPiAtIEFwcHJvdmUgdXNlciAoT3duZXIpXG48Y29kZT4vbGlrZXNyZW1vdmUgJmx0O3VzZXJfaWQmZ3Q7PC9j'
    'b2RlPiAtIFJlbW92ZSBhcHByb3ZhbCAoT3duZXIpIgogICAgICAgICAgICApKQogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8o'
    'bWVzc2FnZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAgICLinYwgPGI+VXNhZ2U6PC9iPiA8'
    'Y29kZT4vbGlrZXMgJmx0O3JlZ2lvbiZndDsgJmx0O3VpZCZndDs8L2NvZGU+IgogICAgICAgICkpCgogICAgcmVnaW9uLCB1aWQg'
    'PSBhcmdzWzFdLnVwcGVyKCksIGFyZ3NbMl0KICAgIGlmIG5vdCB1aWQuaXNkaWdpdCgpOgogICAgICAgIHJldHVybiBib3QucmVw'
    'bHlfdG8obWVzc2FnZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oIuKdjCBVSUQgbXVzdCBjb250YWluIGRpZ2l0'
    'cyBvbmx5LiIpKQogICAgaWYgcmVnaW9uIG5vdCBpbiBSRUdJT05TOgogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2Fn'
    'ZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAgIGYi8J+Xuu+4jyA8Yj5JbnZhbGlkIHJlZ2lv'
    'bjo8L2I+IDxjb2RlPntyZWdpb259PC9jb2RlPiIKICAgICAgICApKQoKICAgIHByb2Nlc3NpbmdfbXNnID0gYm90LnJlcGx5X3Rv'
    'KAogICAgICAgIG1lc3NhZ2UsCiAgICAgICAgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oIuKPsyA8Yj5JbnN0YW50'
    'IEzJquG0i+G0hyBwcm9jZXNzaW5nLi4uPC9iPiIpCiAgICApCgogICAgdHJ5OgogICAgICAgIGlmIG5vdCBBVVRPX0xJS0VfQVBJ'
    'OgogICAgICAgICAgICByZXR1cm4gYm90LmVkaXRfbWVzc2FnZV90ZXh0KAogICAgICAgICAgICAgICAgY29udmVydF9ub3JtYWxf'
    'ZW1vamlzX3RvX3ByZW1pdW0oCiAgICAgICAgICAgICAgICAgICAgIuKdjCA8Yj5BdXRvIExpa2UgQVBJIHNldCBuYWhpIGhhaS48'
    'L2I+XG5Pd25lciA8Y29kZT4vc2V0YXV0b2FwaSAmbHQ7dXJsJmd0OzwvY29kZT4gdXNlIGthcmUuIgogICAgICAgICAgICAgICAg'
    'KSwKICAgICAgICAgICAgICAgIG1lc3NhZ2UuY2hhdC5pZCwKICAgICAgICAgICAgICAgIHByb2Nlc3NpbmdfbXNnLm1lc3NhZ2Vf'
    'aWQKICAgICAgICAgICAgKQoKICAgICAgICB1cmwgPSBmIntBVVRPX0xJS0VfQVBJLnJzdHJpcCgnLycpfS9saWtlP3VpZD17dWlk'
    'fSZzZXJ2ZXJfbmFtZT17cmVnaW9ufSIKICAgICAgICByZXNwb25zZSA9IG1ha2VfcmVxdWVzdCh1cmwpCgogICAgICAgIGlmIG5v'
    'dCByZXNwb25zZToKICAgICAgICAgICAgdGV4dCA9ICI8YmxvY2txdW90ZT7wn5SMIDxiPkF1dG8gTGlrZSBBUEkgc2UgcmVzcG9u'
    'c2UgbmFoaSBtaWxhLiBQbGVhc2UgdHJ5IGFnYWluIGxhdGVyLjwvYj48L2Jsb2NrcXVvdGU+IgogICAgICAgIGVsaWYgcmVzcG9u'
    'c2Uuc3RhdHVzX2NvZGUgIT0gMjAwOgogICAgICAgICAgICB0ZXh0ID0gKAogICAgICAgICAgICAgICAgZiI8YmxvY2txdW90ZT7w'
    'n5SMIDxiPkF1dG8gTGlrZSBBUEkgRXJyb3I8L2I+XG4iCiAgICAgICAgICAgICAgICBmIkhUVFAgU3RhdHVzOiA8Y29kZT57cmVz'
    'cG9uc2Uuc3RhdHVzX2NvZGV9PC9jb2RlPjwvYmxvY2txdW90ZT4iCiAgICAgICAgICAgICkKICAgICAgICBlbHNlOgogICAgICAg'
    'ICAgICByZXN1bHQgPSByZXNwb25zZS5qc29uKCkKICAgICAgICAgICAgaWYgbm90IGlzaW5zdGFuY2UocmVzdWx0LCBkaWN0KToK'
    'ICAgICAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoIkFQSSByZXNwb25zZSBpcyBub3QgYSBKU09OIG9iamVjdCIpCiAgICAg'
    'ICAgICAgIHN0YXR1cyA9IHJlc3VsdC5nZXQoInN0YXR1cyIsIDApCgogICAgICAgICAgICBpZiBzdGF0dXMgPT0gMToKICAgICAg'
    'ICAgICAgICAgICMgQ291bnQgYSBwZXJzb25hbCBhcHByb3ZhbCBvbmx5IHdoZW4gYWNjZXNzIGNvbWVzIGZyb20gdGhhdAogICAg'
    'ICAgICAgICAgICAgIyBhcHByb3ZhbCAodXNlcnMgaW4gYW4gb3duZXItYWxsb3dlZCBncm91cCByZW1haW4gdW5saW1pdGVkKS4K'
    'ICAgICAgICAgICAgICAgIGlmIHVzZXJfaWQgPT0gT1dORVJfSUQ6CiAgICAgICAgICAgICAgICAgICAgdXNhZ2VfdGV4dCA9ICJP'
    'd25lciAtIFVubGltaXRlZCIKICAgICAgICAgICAgICAgIGVsaWYgbm90IGlzX2xpa2VzX2dyb3VwX2FsbG93ZWQobWVzc2FnZS5j'
    'aGF0LmlkKToKICAgICAgICAgICAgICAgICAgICBhcHByb3ZhbCA9IExJS0VTX0FQUFJPVkFMUy5nZXQodXNlcl9pZCkKICAgICAg'
    'ICAgICAgICAgICAgICBpZiBhcHByb3ZhbCBpcyBub3QgTm9uZToKICAgICAgICAgICAgICAgICAgICAgICAgYXBwcm92YWxbInVz'
    'ZWQiXSA9IGludChhcHByb3ZhbC5nZXQoInVzZWQiLCAwKSkgKyAxCiAgICAgICAgICAgICAgICAgICAgICAgIHNhdmVfZGF0YSgp'
    'CiAgICAgICAgICAgICAgICAgICAgICAgIHVzYWdlX3RleHQgPSBmInthcHByb3ZhbFsndXNlZCddfS97YXBwcm92YWwuZ2V0KCds'
    'aW1pdCcsIDApfSIKICAgICAgICAgICAgICAgICAgICBlbHNlOgogICAgICAgICAgICAgICAgICAgICAgICB1c2FnZV90ZXh0ID0g'
    'IkFsbG93ZWQgZ3JvdXAgLSBVbmxpbWl0ZWQiCiAgICAgICAgICAgICAgICBlbHNlOgogICAgICAgICAgICAgICAgICAgIHVzYWdl'
    'X3RleHQgPSAiQWxsb3dlZCBncm91cCAtIFVubGltaXRlZCIKCiAgICAgICAgICAgICAgICBuaWNrbmFtZSA9IHN0cihyZXN1bHQu'
    'Z2V0KCJQbGF5ZXJOaWNrbmFtZSIsICJOL0EiKSkucmVwbGFjZSgiPCIsICImbHQ7IikucmVwbGFjZSgiPiIsICImZ3Q7IikKICAg'
    'ICAgICAgICAgICAgIGxpa2VzX2dpdmVuID0gcmVzdWx0LmdldCgiTGlrZXNHaXZlbkJ5QVBJIiwgMCkKCiAgICAgICAgICAgICAg'
    'ICB0ZXh0ID0gKAogICAgICAgICAgICAgICAgICAgICI8YmxvY2txdW90ZT4iCiAgICAgICAgICAgICAgICAgICAgIuKchSA8Yj5J'
    'ybRz4bSb4bSAybThtJsgTMmq4bSL4bSHIFPhtIfJtOG0myBT4bSc4bSE4bSE4bSHc3PSk+G0nMqfyp/KjzwvYj4g8J+ls1xuIgog'
    'ICAgICAgICAgICAgICAgICAgICLilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIFcbiIK'
    'ICAgICAgICAgICAgICAgICAgICBmIvCfkaQgPGI+UMqf4bSAyo/htIfKgDo8L2I+IHtuaWNrbmFtZX1cbiIKICAgICAgICAgICAg'
    'ICAgICAgICBmIvCfhpQgPGI+VUlEOjwvYj4ge3VpZH1cbiIKICAgICAgICAgICAgICAgICAgICBmIvCfjI0gPGI+UuG0h8miyarh'
    'tI/JtDo8L2I+IHtyZXN1bHQuZ2V0KCdQbGF5ZXJSZWdpb24nLCByZWdpb24pfVxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+T'
    'iiA8Yj5M4bSH4bSg4bSHyp86PC9iPiB7cmVzdWx0LmdldCgnUGxheWVyTGV2ZWwnLCAnTi9BJyl9XG4iCiAgICAgICAgICAgICAg'
    'ICAgICAgZiLinaTvuI8gPGI+QuG0h9KT4bSPyoDhtIc6PC9iPiB7cmVzdWx0LmdldCgnTGlrZXNiZWZvcmVDb21tYW5kJywgJ04v'
    'QScpfVxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+SnSA8Yj5B0pPhtJvhtIfKgDo8L2I+IHtyZXN1bHQuZ2V0KCdMaWtlc2Fm'
    'dGVyQ29tbWFuZCcsICdOL0EnKX1cbiIKICAgICAgICAgICAgICAgICAgICBmIvCfpJYgPGI+R8mq4bSg4bSHybQgQsqPIEHhtJzh'
    'tJvhtI8gQVBJOjwvYj4ge2xpa2VzX2dpdmVufVxuIgogICAgICAgICAgICAgICAgICAgICLilIHilIHilIHilIHilIHilIHilIHi'
    'lIHilIHilIHilIHilIHilIHilIHilIHilIHilIFcbiIKICAgICAgICAgICAgICAgICAgICBmIvCfk4kgPGI+L2xpa2VzIFXqnLHh'
    'tIc6PC9iPiB7dXNhZ2VfdGV4dH0iCiAgICAgICAgICAgICAgICAgICAgIjwvYmxvY2txdW90ZT4iCiAgICAgICAgICAgICAgICAp'
    'CiAgICAgICAgICAgIGVsaWYgc3RhdHVzID09IDI6CiAgICAgICAgICAgICAgICB0ZXh0ID0gKAogICAgICAgICAgICAgICAgICAg'
    'ICI8YmxvY2txdW90ZT4iCiAgICAgICAgICAgICAgICAgICAgIuKaoO+4jyA8Yj5MyarhtIvhtIdzIEHKn8qA4bSH4bSA4bSFyo8g'
    'U+G0h8m04bSbPC9iPlxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+GlCA8Yj5VSUQ6PC9iPiB7dWlkfVxuIgogICAgICAgICAg'
    'ICAgICAgICAgICLwn5OKIDxiPk1lc3NhZ2U6PC9iPiBMSU1JVF9SRUFDSEVEIgogICAgICAgICAgICAgICAgICAgICI8L2Jsb2Nr'
    'cXVvdGU+IgogICAgICAgICAgICAgICAgKQogICAgICAgICAgICBlbHNlOgogICAgICAgICAgICAgICAgdGV4dCA9ICgKICAgICAg'
    'ICAgICAgICAgICAgICAiPGJsb2NrcXVvdGU+IgogICAgICAgICAgICAgICAgICAgICLinYwgPGI+UMqf4bSAyo/htIfKgCBO4bSP'
    '4bSbIEbhtI/htJzJtOG0hSAvIEFQSSBFcnJvcjwvYj5cbiIKICAgICAgICAgICAgICAgICAgICBmIvCfhpQgPGI+VUlEOjwvYj4g'
    'e3VpZH1cbiIKICAgICAgICAgICAgICAgICAgICBmIuKaoO+4jyA8Yj5NZXNzYWdlOjwvYj4ge3Jlc3VsdC5nZXQoJ21lc3NhZ2Un'
    'LCAncGxheWVyX25vdF9mb3VuZCcpfSIKICAgICAgICAgICAgICAgICAgICAiPC9ibG9ja3F1b3RlPiIKICAgICAgICAgICAgICAg'
    'ICkKCiAgICAgICAgdGV4dCA9IGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKHRleHQpCiAgICAgICAgbWFya3VwID0g'
    'SW5saW5lS2V5Ym9hcmRNYXJrdXAoKQogICAgICAgIGRldl9idG4gPSBtYWtlX3ByZW1pdW1fYnV0dG9uKAogICAgICAgICAgICAi'
    'RGV2bG9wZXIiLAogICAgICAgICAgICB1cmw9Imh0dHBzOi8vdC5tZS9wcmluY2VnbXIiLAogICAgICAgICAgICBpY29uX2lkPV9n'
    'ZXRfYnV0dG9uX21hcCgpLmdldCgiRGV2bG9wZXIiKSwKICAgICAgICAgICAgc3R5bGU9InByaW1hcnkiCiAgICAgICAgKQogICAg'
    'ICAgIG1hcmt1cC5hZGQoZGV2X2J0bikKICAgICAgICBib3QuZWRpdF9tZXNzYWdlX3RleHQoCiAgICAgICAgICAgIHRleHQsCiAg'
    'ICAgICAgICAgIG1lc3NhZ2UuY2hhdC5pZCwKICAgICAgICAgICAgcHJvY2Vzc2luZ19tc2cubWVzc2FnZV9pZCwKICAgICAgICAg'
    'ICAgcmVwbHlfbWFya3VwPW1hcmt1cAogICAgICAgICkKCiAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgIyBLZWVw'
    'IHRlY2huaWNhbCBkZXRhaWxzIGluIHRoZSBib3QgbG9nOyBzaG93IGEgc2FmZSBtZXNzYWdlIHRvIGdyb3VwIHVzZXJzLgogICAg'
    'ICAgIHByaW50KGYiW2xpa2VzXSBFcnJvciBmb3IgdXNlcl9pZD17dXNlcl9pZH0sIHVpZD17dWlkfSwgcmVnaW9uPXtyZWdpb259'
    'OiB7dHlwZShlKS5fX25hbWVfX306IHtlfSIpCiAgICAgICAgZXJyb3JfdGV4dCA9ICgKICAgICAgICAgICAgIjxibG9ja3F1b3Rl'
    'PuKaoO+4jyA8Yj5SZXF1ZXN0IHByb2Nlc3MgbmFoaSBobyBwYXlpLjwvYj4gIgogICAgICAgICAgICAiUGxlYXNlIHRob2RpIGRl'
    'ciBiYWFkIGRvYmFyYSB0cnkga2FyZWluLiBBZ2FyIGlzc3VlIGNvbnRpbnVlIGhvLCAiCiAgICAgICAgICAgICJPd25lciBrbyBp'
    'bmZvcm0ga2FyZWluLjwvYmxvY2txdW90ZT4iCiAgICAgICAgKQogICAgICAgIHRyeToKICAgICAgICAgICAgYm90LmVkaXRfbWVz'
    'c2FnZV90ZXh0KAogICAgICAgICAgICAgICAgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oZXJyb3JfdGV4dCksCiAg'
    'ICAgICAgICAgICAgICBtZXNzYWdlLmNoYXQuaWQsCiAgICAgICAgICAgICAgICBwcm9jZXNzaW5nX21zZy5tZXNzYWdlX2lkCiAg'
    'ICAgICAgICAgICkKICAgICAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGVkaXRfZXJyb3I6CiAgICAgICAgICAgIHByaW50KGYiW2xp'
    'a2VzXSBDb3VsZCBub3QgZWRpdCBwcm9jZXNzaW5nIG1lc3NhZ2U6IHt0eXBlKGVkaXRfZXJyb3IpLl9fbmFtZV9ffToge2VkaXRf'
    'ZXJyb3J9IikKICAgICAgICAgICAgdHJ5OgogICAgICAgICAgICAgICAgYm90LnNlbmRfbWVzc2FnZSgKICAgICAgICAgICAgICAg'
    'ICAgICBtZXNzYWdlLmNoYXQuaWQsCiAgICAgICAgICAgICAgICAgICAgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0o'
    'ZXJyb3JfdGV4dCksCiAgICAgICAgICAgICAgICAgICAgcmVwbHlfdG9fbWVzc2FnZV9pZD1tZXNzYWdlLm1lc3NhZ2VfaWQKICAg'
    'ICAgICAgICAgICAgICkKICAgICAgICAgICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBzZW5kX2Vycm9yOgogICAgICAgICAgICAgICAg'
    'cHJpbnQoZiJbbGlrZXNdIENvdWxkIG5vdCBzZW5kIGZhbGxiYWNrIGVycm9yOiB7dHlwZShzZW5kX2Vycm9yKS5fX25hbWVfX306'
    'IHtzZW5kX2Vycm9yfSIpCgojID09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQojIPCfmoAgQVVUT0xJ'
    'S0UgQ09NTUFORFMKIyA9PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0KQGJvdC5tZXNzYWdlX2hhbmRs'
    'ZXIoY29tbWFuZHM9WyJhdXRvbGlrZSJdKQpkZWYgaGFuZGxlX2F1dG9saWtlKG1lc3NhZ2UpOgogICAgdXNlcl9pZCA9IG1lc3Nh'
    'Z2UuZnJvbV91c2VyLmlkCiAgICBpZiBub3QgaXNfYWRtaW4odXNlcl9pZCk6CiAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byht'
    'ZXNzYWdlLCAi4p2MIDxiPk9ubHkgb3duZXIvYWRtaW4gY2FuIHVzZSB0aGlzIGNvbW1hbmQuPC9iPiIpCgogICAgYWRtaW5fZGF0'
    'YSA9IGdldF9hZG1pbl9kYXRhKHVzZXJfaWQpCiAgICBpZiBhZG1pbl9kYXRhIGlzIG5vdCBOb25lOgogICAgICAgIGxpbWl0ID0g'
    'YWRtaW5fZGF0YS5nZXQoImF1dG9saWtlX2xpbWl0IikKICAgICAgICB1c2VkID0gYWRtaW5fZGF0YS5nZXQoImF1dG9saWtlX3Vz'
    'ZWQiLCAwKQogICAgICAgIGlmIGxpbWl0IGlzIG5vdCBOb25lIGFuZCB1c2VkID49IGxpbWl0OgogICAgICAgICAgICByZXR1cm4g'
    'Ym90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKAogICAgICAgICAgICAgICAgZiLw'
    'n5qrIDxiPkF1dG9saWtlIExpbWl0IFJlYWNoZWQhPC9iPlxcbllvdSBoYXZlIHVzZWQge3VzZWR9L3tsaW1pdH0gdGltZXMuXFxu'
    'Q29udGFjdCBPd25lciBmb3IgbW9yZS4iCiAgICAgICAgICAgICkpCgogICAgYXJncyA9IG1lc3NhZ2UudGV4dC5zcGxpdCgpCiAg'
    'ICBpZiBsZW4oYXJncykgIT0gNDoKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsICJVc2FnZTogL2F1dG9saWtl'
    'IHtyZWdpb259IHt1aWR9IHtUYXJnZXQgTGlrZXN9IikKICAgIHJlZ2lvbiwgdWlkLCB0YXJnZXQgPSBhcmdzWzFdLnVwcGVyKCks'
    'IGFyZ3NbMl0sIGFyZ3NbM10KICAgIGlmIHJlZ2lvbiBub3QgaW4gUkVHSU9OUzoKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3Rv'
    'KG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKGYi4p2MIEludmFsaWQgcmVnaW9uOiA8Y29kZT57cmVn'
    'aW9ufTwvY29kZT4iKSkKICAgIGlmIG5vdCB1aWQuaXNkaWdpdCgpOgogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2Fn'
    'ZSwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oIuKdjCBVSUQgbXVzdCBjb250YWluIGRpZ2l0cyBvbmx5LiIpKQog'
    'ICAgdHJ5OgogICAgICAgIHRhcmdldCA9IGludCh0YXJnZXQpCiAgICAgICAgaWYgdGFyZ2V0IDw9IDAgb3IgdGFyZ2V0ID4gMTAw'
    'MDAwMDoKICAgICAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCAi4p2MIFRhcmdldCBMaWtlcyBtdXN0IGJlIGJl'
    'dHdlZW4gMSBhbmQgMSwwMDAsMDAwLiIpCiAgICBleGNlcHQgVmFsdWVFcnJvcjoKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3Rv'
    'KG1lc3NhZ2UsICLinYwgVGFyZ2V0IExpa2VzIG11c3QgYmUgYSBudW1iZXIuIikKCiAgICB0YXNrX2lkID0gYWRkX2F1dG9saWtl'
    'X3Rhc2sodWlkLCByZWdpb24sIHRhcmdldCwgc291cmNlPSdtYW51YWwnKQogICAgaWYgYWRtaW5fZGF0YSBpcyBub3QgTm9uZSBh'
    'bmQgYWRtaW5fZGF0YS5nZXQoImF1dG9saWtlX2xpbWl0IikgaXMgbm90IE5vbmU6CiAgICAgICAgQURNSU5TW3VzZXJfaWRdWyJh'
    'dXRvbGlrZV91c2VkIl0gPSBhZG1pbl9kYXRhLmdldCgiYXV0b2xpa2VfdXNlZCIsIDApICsgMQogICAgICAgIHNhdmVfZGF0YSgp'
    'CiAgICByZXBseV90ZXh0ID0gZiLinIUgPGI+QWRkZWQgdG8gQXV0b0xpa2U6PC9iPlxcbvCfp74gVGFzazogI3t0YXNrX2lkfVxc'
    'bvCfhpQgVUlEOiB7dWlkfVxcbvCfjI0gUmVnaW9uOiB7cmVnaW9ufVxcbvCfjq8gVGFyZ2V0IExpa2VzOiB7dGFyZ2V0fSIKICAg'
    'IGJvdC5yZXBseV90byhtZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShyZXBseV90ZXh0KSkKCkBib3Qu'
    'bWVzc2FnZV9oYW5kbGVyKGNvbW1hbmRzPVsiZXh0ZW5kIl0pCmRlZiBoYW5kbGVfZXh0ZW5kKG1lc3NhZ2UpOgogICAgaWYgbm90'
    'IGlzX2FkbWluKG1lc3NhZ2UuZnJvbV91c2VyLmlkKToKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLinYwg'
    'PGI+T25seSBvd25lci9hZG1pbiBjYW4gdXNlIHRoaXMgY29tbWFuZC48L2I+IikKICAgIGFyZ3MgPSBtZXNzYWdlLnRleHQuc3Bs'
    'aXQoKQogICAgaWYgbGVuKGFyZ3MpICE9IDQ6CiAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCAiVXNhZ2U6IC9l'
    'eHRlbmQge3JlZ2lvbn0ge3VpZH0ge2FkZGxpa2V9IikKICAgIHJlZ2lvbiwgdWlkLCBhbW91bnRfdGV4dCA9IGFyZ3NbMV0udXBw'
    'ZXIoKSwgYXJnc1syXSwgYXJnc1szXQogICAgaWYgcmVnaW9uIG5vdCBpbiBSRUdJT05TOgogICAgICAgIHJldHVybiBib3QucmVw'
    'bHlfdG8obWVzc2FnZSwgZiLinYwgSW52YWxpZCByZWdpb246IDxjb2RlPntyZWdpb259PC9jb2RlPiIpCiAgICBpZiBub3QgdWlk'
    'LmlzZGlnaXQoKToKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLinYwgVUlEIG11c3QgY29udGFpbiBkaWdp'
    'dHMgb25seS4iKQogICAgdHJ5OgogICAgICAgIGFkZF9saWtlcyA9IGludChhbW91bnRfdGV4dCkKICAgIGV4Y2VwdCBWYWx1ZUVy'
    'cm9yOgogICAgICAgIHJldHVybiBib3QucmVwbHlfdG8obWVzc2FnZSwgIuKdjCBFeHRlbmQgbGlrZXMgbXVzdCBiZSBhIG51bWJl'
    'ci4iKQogICAgaWYgYWRkX2xpa2VzIDw9IDAgb3IgYWRkX2xpa2VzID4gMTAwMDAwMDoKICAgICAgICByZXR1cm4gYm90LnJlcGx5'
    'X3RvKG1lc3NhZ2UsICLinYwgRXh0ZW5kIGxpa2VzIG11c3QgYmUgYmV0d2VlbiAxIGFuZCAxLDAwMCwwMDAuIikKCiAgICB0cnk6'
    'CiAgICAgICAgdGFza19pZCwgYWN0aW9uLCByZW1haW5pbmcsIHRhcmdldCwgdXNlcl9pZCA9IGV4dGVuZF9hdXRvbGlrZV90YXNr'
    'KHVpZCwgcmVnaW9uLCBhZGRfbGlrZXMpCiAgICAgICAgYWN0aW9uX3RleHQgPSAiTmV3IEF1dG9MaWtlIHNldCIgaWYgYWN0aW9u'
    'ID09ICdjcmVhdGVkJyBlbHNlICJBdXRvTGlrZSB0YXJnZXQgZXh0ZW5kZWQiCiAgICAgICAgcmVwbHkgPSAoCiAgICAgICAgICAg'
    'IGYi4pyFIDxiPnthY3Rpb25fdGV4dH0gc3VjY2Vzc2Z1bGx5ITwvYj5cbiIKICAgICAgICAgICAgZiLwn6e+IFRhc2s6IDxjb2Rl'
    'PiN7dGFza19pZH08L2NvZGU+XG4iCiAgICAgICAgICAgIGYi8J+GlCBVSUQ6IDxjb2RlPnt1aWR9PC9jb2RlPlxuIgogICAgICAg'
    'ICAgICBmIvCfjI0gUmVnaW9uOiA8Yj57cmVnaW9ufTwvYj5cbiIKICAgICAgICAgICAgZiLinpUgQWRkZWQgdGFyZ2V0OiA8Yj57'
    'YWRkX2xpa2VzfTwvYj5cbiIKICAgICAgICAgICAgZiLwn46vIFJlbWFpbmluZyAvIFRhcmdldDogPGI+e3JlbWFpbmluZ30ve3Rh'
    'cmdldH08L2I+IgogICAgICAgICkKICAgICAgICBpZiB1c2VyX2lkIGlzIG5vdCBOb25lOgogICAgICAgICAgICByZXBseSArPSAi'
    'XG7wn4yQIFdlYnNpdGUgd2FsbGV0IGNyZWRpdGVkIHdpdGggdGhlIGV4dGVuc2lvbi4iCiAgICAgICAgYm90LnJlcGx5X3RvKG1l'
    'c3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKHJlcGx5KSkKICAgIGV4Y2VwdCBFeGNlcHRpb24gYXMgZXhj'
    'OgogICAgICAgIGJvdC5yZXBseV90byhtZXNzYWdlLCBmIuKdjCBDb3VsZCBub3QgZXh0ZW5kIEF1dG9MaWtlOiA8Y29kZT57c3Ry'
    'KGV4Yyl9PC9jb2RlPiIpCgoKQGJvdC5tZXNzYWdlX2hhbmRsZXIoY29tbWFuZHM9WyJyZW1vdmUiXSkKZGVmIGhhbmRsZV9yZW1v'
    'dmUobWVzc2FnZSk6CiAgICBpZiBub3QgaXNfYWRtaW4obWVzc2FnZS5mcm9tX3VzZXIuaWQpOgogICAgICAgIHJldHVybiBib3Qu'
    'cmVwbHlfdG8obWVzc2FnZSwgIuKdjCA8Yj5Pbmx5IG93bmVyIGNhbiB1c2UgdGhpcyBjb21tYW5kLjwvYj4iKQogICAgYXJncyA9'
    'IG1lc3NhZ2UudGV4dC5zcGxpdCgpCiAgICBpZiBsZW4oYXJncykgIT0gMjoKICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1l'
    'c3NhZ2UsICJVc2FnZTogL3JlbW92ZSB7dWlkfSIpCiAgICB1aWQgPSBhcmdzWzFdCiAgICBpZiBub3QgdWlkLmlzZGlnaXQoKToK'
    'ICAgICAgICByZXR1cm4gYm90LnJlcGx5X3RvKG1lc3NhZ2UsICLinYwgVUlEIG11c3QgY29udGFpbiBkaWdpdHMgb25seS4iKQog'
    'ICAgcmVtb3ZlZCA9IHJlbW92ZV9hdXRvbGlrZV90YXNrcyh1aWQpCiAgICBpZiByZW1vdmVkOgogICAgICAgIGJvdC5yZXBseV90'
    'byhtZXNzYWdlLCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bSgKICAgICAgICAgICAgZiLwn5eRIFJlbW92ZWQge3Jl'
    'bW92ZWR9IGFjdGl2ZSB0YXNrKHMpIGZvciBVSUQge3VpZH0uIgogICAgICAgICkpCiAgICBlbHNlOgogICAgICAgIGJvdC5yZXBs'
    'eV90byhtZXNzYWdlLCBmIuKdjCBObyBhY3RpdmUgdGFza3MgZm91bmQgZm9yIFVJRCB7dWlkfS4iKQoKQGJvdC5tZXNzYWdlX2hh'
    'bmRsZXIoY29tbWFuZHM9WyJsaXN0dWlkIl0pCmRlZiBoYW5kbGVfbGlzdHVpZChtZXNzYWdlKToKICAgIGlmIG5vdCBpc19hZG1p'
    'bihtZXNzYWdlLmZyb21fdXNlci5pZCk6CiAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCAi4p2MIDxiPk9ubHkg'
    'b3duZXIgY2FuIHVzZSB0aGlzIGNvbW1hbmQuPC9iPiIpCiAgICBkYXRhID0gbG9hZF9kYigpLmdldCgiYXV0b2xpa2VfbGlzdCIs'
    'IHt9KQogICAgaWYgbm90IGRhdGE6CiAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90byhtZXNzYWdlLCAi8J+TrSA8Yj5EYXRhYmFz'
    'ZSBpcyBlbXB0eS48L2I+IikKICAgIG1zZyA9IGYi8J+TiyA8Yj5BdXRvTGlrZSBMaXN0IChUb3RhbDoge2xlbihkYXRhKX0pOjwv'
    'Yj5cbiIKICAgIGZvciB0YXNrX2lkLCBpbmZvIGluIGRhdGEuaXRlbXMoKToKICAgICAgICByZW1haW5pbmcgPSBpbmZvLmdldCgi'
    'cmVtYWluaW5nX2xpa2VzIiwgMCkKICAgICAgICB0YXJnZXQgPSBpbmZvLmdldCgidGFyZ2V0X2xpa2VzIiwgMCkKICAgICAgICBt'
    'c2cgKz0gKAogICAgICAgICAgICBmIvCfp74gPGI+VGFzayAje3Rhc2tfaWR9PC9iPiB8IPCfhpQgPGNvZGU+XG57aW5mb1sndWlk'
    'J119XG48L2NvZGU+IHwgIgogICAgICAgICAgICBmIvCfjI0ge2luZm9bJ3JlZ2lvbiddfSB8IPCfjq8ge3JlbWFpbmluZ30ve3Rh'
    'cmdldH0g4p2k77iPXG4iCiAgICAgICAgKQogICAgYm90LnJlcGx5X3RvKG1lc3NhZ2UsIGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190'
    'b19wcmVtaXVtKG1zZykpCgpAYm90Lm1lc3NhZ2VfaGFuZGxlcihjb21tYW5kcz1bInJ1biJdKQpkZWYgaGFuZGxlX3J1bihtZXNz'
    'YWdlKToKICAgIGlmIG5vdCBpc19hZG1pbihtZXNzYWdlLmZyb21fdXNlci5pZCk6CiAgICAgICAgcmV0dXJuIGJvdC5yZXBseV90'
    'byhtZXNzYWdlLCAi4p2MIE9ubHkgb3duZXIuIikKICAgIHRocmVhZGluZy5UaHJlYWQodGFyZ2V0PXJ1bl9hdXRvbGlrZV9wcm9j'
    'ZXNzLCBhcmdzPShUcnVlLCBtZXNzYWdlLmNoYXQuaWQpLCBkYWVtb249VHJ1ZSkuc3RhcnQoKQogICAgYm90LnJlcGx5X3RvKG1l'
    'c3NhZ2UsICLwn5qAIDxiPkF1dG9MaWtlIHByb2Nlc3Mgc3RhcnRlZCBtYW51YWxseS4gUmVzdWx0IHdpbGwgYmUgcmVwb3J0ZWQg'
    'aGVyZS48L2I+IikKCmRlZiBydW5fYXV0b2xpa2VfcHJvY2VzcyhtYW51YWw9RmFsc2UsIHJlcG9ydF9jaGF0X2lkPU5vbmUpOgog'
    'ICAgZGIgPSBsb2FkX2RiKCkKICAgIGF1dG9saWtlX2RhdGEgPSBkYi5nZXQoImF1dG9saWtlX2xpc3QiLCB7fSkKICAgIGlmIG5v'
    'dCBhdXRvbGlrZV9kYXRhOgogICAgICAgIGlmIG1hbnVhbDoKICAgICAgICAgICAgYm90LnNlbmRfbWVzc2FnZShyZXBvcnRfY2hh'
    'dF9pZCBvciBSRVFVSVJFRF9HUk9VUF9JRCwgY29udmVydF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oIvCfk60gPGI+Tm8gYWN0'
    'aXZlIHdlYnNpdGUvYm90IHRhc2tzIGZvdW5kIGluIHRoZSBzaGFyZWQgZGF0YWJhc2UuPC9iPiIpKQogICAgICAgIHJldHVybgog'
    'ICAgaWYgbWFudWFsOgogICAgICAgIGJvdC5zZW5kX21lc3NhZ2UocmVwb3J0X2NoYXRfaWQgb3IgUkVRVUlSRURfR1JPVVBfSUQs'
    'IGNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKGYi8J+agCA8Yj5BdXRvTGlrZSBTdGFydGVkLi4uPC9iPlxu8J+TiyBB'
    'Y3RpdmUgdGFza3M6IHtsZW4oYXV0b2xpa2VfZGF0YSl9IikpCgogICAgZm9yIHRhc2tfaWQsIGluZm8gaW4gbGlzdChhdXRvbGlr'
    'ZV9kYXRhLml0ZW1zKCkpOgogICAgICAgIHVpZCA9IHN0cihpbmZvWyJ1aWQiXSkKICAgICAgICByZWdpb24gPSBpbmZvWyJyZWdp'
    'b24iXQogICAgICAgIHRhcmdldCA9IGludChpbmZvLmdldCgidGFyZ2V0X2xpa2VzIiwgMCkpCiAgICAgICAgcmVtYWluaW5nID0g'
    'aW50KGluZm8uZ2V0KCJyZW1haW5pbmdfbGlrZXMiLCB0YXJnZXQpKQogICAgICAgIHVybCA9IGYie0FVVE9fTElLRV9BUEkucnN0'
    'cmlwKCcvJyl9L2xpa2U/dWlkPXt1aWR9JnNlcnZlcl9uYW1lPXtyZWdpb259IgogICAgICAgIHRyeToKICAgICAgICAgICAgcmVz'
    'ID0gbWFrZV9yZXF1ZXN0KHVybCkKICAgICAgICAgICAgaWYgbm90IHJlcyBvciByZXMuc3RhdHVzX2NvZGUgIT0gMjAwOgogICAg'
    'ICAgICAgICAgICAgZXJyb3JfbXNnID0gZiLinYwgTm8gcmVzcG9uc2UgZm9yIFVJRCB7dWlkfSAoVGFzayAje3Rhc2tfaWR9KSIK'
    'ICAgICAgICAgICAgICAgIGJvdC5zZW5kX21lc3NhZ2UocmVwb3J0X2NoYXRfaWQgb3IgUkVRVUlSRURfR1JPVVBfSUQsIGNvbnZl'
    'cnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKGVycm9yX21zZyksIHBhcnNlX21vZGU9IkhUTUwiKQogICAgICAgICAgICAgICAg'
    'Y29udGludWUKICAgICAgICAgICAgcmVzdWx0ID0gcmVzLmpzb24oKQogICAgICAgICAgICBpZiBub3QgaXNpbnN0YW5jZShyZXN1'
    'bHQsIGRpY3QpOgogICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcigiQVBJIHJlc3BvbnNlIGlzIG5vdCBhIEpTT04gb2Jq'
    'ZWN0IikKICAgICAgICAgICAgc3RhdHVzID0gcmVzdWx0LmdldCgic3RhdHVzIiwgMCkKICAgICAgICAgICAgbWFya3VwID0gSW5s'
    'aW5lS2V5Ym9hcmRNYXJrdXAoKQogICAgICAgICAgICBkZXZfYnRuID0gbWFrZV9wcmVtaXVtX2J1dHRvbigiRGV2bG9wZXIiLCB1'
    'cmw9Imh0dHBzOi8vdC5tZS9wcmluY2VnbXIiLAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICBpY29u'
    'X2lkPV9nZXRfYnV0dG9uX21hcCgpLmdldCgiRGV2bG9wZXIiKSwgc3R5bGU9InByaW1hcnkiKQogICAgICAgICAgICBtYXJrdXAu'
    'YWRkKGRldl9idG4pCgogICAgICAgICAgICBpZiBzdGF0dXMgPT0gMToKICAgICAgICAgICAgICAgIG5pY2tuYW1lID0gc3RyKHJl'
    'c3VsdC5nZXQoJ1BsYXllck5pY2tuYW1lJywgJ04vQScpKS5yZXBsYWNlKCc8JywgJyZsdDsnKS5yZXBsYWNlKCc+JywgJyZndDsn'
    'KQogICAgICAgICAgICAgICAgbGlrZXNfZ2l2ZW4gPSBpbnQocmVzdWx0LmdldCgnTGlrZXNHaXZlbkJ5QVBJJywgMCkgb3IgMCkK'
    'ICAgICAgICAgICAgICAgIGlmIGxpa2VzX2dpdmVuIDwgMDoKICAgICAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCJB'
    'UEkgcmV0dXJuZWQgYSBuZWdhdGl2ZSBMaWtlc0dpdmVuQnlBUEkgdmFsdWUiKQoKICAgICAgICAgICAgICAgIHJlbWFpbmluZywg'
    'Y3JlZGl0ZWRfbGlrZXMgPSB1cGRhdGVfdGFza19wcm9ncmVzcyhpbnQodGFza19pZCksIGxpa2VzX2dpdmVuKQogICAgICAgICAg'
    'ICAgICAgbXNnID0gKAogICAgICAgICAgICAgICAgICAgIGYiPGJsb2NrcXVvdGU+IgogICAgICAgICAgICAgICAgICAgIGYi4pyF'
    'IDxiPkHhtJzhtJvhtI/Kn8mq4bSL4bSH6pyxIFPhtIfJtOG0myBT4bSc4bSE4bSE4bSH6pyx6pyw4bScyp/Kn8qPPC9iPiDwn6Wz'
    'XFxuIgogICAgICAgICAgICAgICAgICAgIGYi4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB4pSB'
    '4pSBXFxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+RpCA8Yj5Qyp/htIDKj+G0h8qAOjwvYj4ge25pY2tuYW1lfVxcbiIKICAg'
    'ICAgICAgICAgICAgICAgICBmIvCfk4ogPGI+TOG0h+G0oOG0h8qfOjwvYj4ge3Jlc3VsdC5nZXQoJ1BsYXllckxldmVsJywnTi9B'
    'Jyl9XFxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+MjSA8Yj5S4bSHyaLJquG0j8m0OjwvYj4ge3Jlc3VsdC5nZXQoJ1BsYXll'
    'clJlZ2lvbicsIHJlZ2lvbil9XFxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+GlCA8Yj5VSUQ6PC9iPiB7cmVzdWx0LmdldCgn'
    'VUlEJywgdWlkKX1cXG4iCiAgICAgICAgICAgICAgICAgICAgZiLwn5O2IDxiPkFQSSBV6pyx4bSH4bSFOjwvYj4gQVBJIDFcXG4i'
    'CiAgICAgICAgICAgICAgICAgICAgZiLinaTvuI8gPGI+QuG0h+qcsOG0j8qA4bSHOjwvYj4ge3Jlc3VsdC5nZXQoJ0xpa2VzYmVm'
    'b3JlQ29tbWFuZCcsJ04vQScpfVxcbiIKICAgICAgICAgICAgICAgICAgICBmIvCfkp0gPGI+QeqcsOG0m+G0h8qAOjwvYj4ge3Jl'
    'c3VsdC5nZXQoJ0xpa2VzYWZ0ZXJDb21tYW5kJywnTi9BJyl9XFxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+kliA8Yj5Hyarh'
    'tKDhtIfJtCBCyo8gQuG0j+G0mzo8L2I+IHtsaWtlc19naXZlbn1cXG4iCiAgICAgICAgICAgICAgICAgICAgZiLilIHilIHilIHi'
    'lIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIHilIFcXG4iCiAgICAgICAgICAgICAgICAgICAgZiLwn5OJIDxi'
    'PlLhtIfhtI3htIDJqsm0yarJtMmiIFThtIDKgMmi4bSH4bSbIEzJquG0i+G0h+qcsTo8L2I+IHtyZW1haW5pbmd9L3t0YXJnZXR9'
    'IPCfkp1cXG4iCiAgICAgICAgICAgICAgICAgICAgZiLwn6e+IDxiPlB1cmNoYXNlIFRhc2s6PC9iPiAje3Rhc2tfaWR9IgogICAg'
    'ICAgICAgICAgICAgICAgIGYiPC9ibG9ja3F1b3RlPiIKICAgICAgICAgICAgICAgICkKICAgICAgICAgICAgICAgIGJvdC5zZW5k'
    'X3Bob3RvKHJlcG9ydF9jaGF0X2lkIG9yIFJFUVVJUkVEX0dST1VQX0lELCBJTUFHRV9VUkwsIGNhcHRpb249Y29udmVydF9ub3Jt'
    'YWxfZW1vamlzX3RvX3ByZW1pdW0obXNnKSwKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIHJlcGx5X21hcmt1cD1tYXJr'
    'dXAsIHBhcnNlX21vZGU9IkhUTUwiKQoKICAgICAgICAgICAgICAgIGlmIHJlbWFpbmluZyA8PSAwOgogICAgICAgICAgICAgICAg'
    'ICAgIGRvbmVfbXNnID0gZiI8YmxvY2txdW90ZT7wn4+BIDxiPlVJRCB7dWlkfSBUQVNLICN7dGFza19pZH0gVEFSR0VUIENPTVBM'
    'RVRFRC48L2I+PC9ibG9ja3F1b3RlPiIKICAgICAgICAgICAgICAgICAgICBib3Quc2VuZF9tZXNzYWdlKFJFUVVJUkVEX0dST1VQ'
    'X0lELCBjb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShkb25lX21zZyksIHBhcnNlX21vZGU9IkhUTUwiKQoKICAgICAg'
    'ICAgICAgZWxpZiBzdGF0dXMgPT0gMjoKICAgICAgICAgICAgICAgIG1zZyA9ICgKICAgICAgICAgICAgICAgICAgICBmIjxibG9j'
    'a3F1b3RlPiIKICAgICAgICAgICAgICAgICAgICBmIuKaoO+4jyBVSUQge3VpZH06IDxiPkxpa2VzIGFscmVhZHkgc2VudCB0b2Rh'
    'eS48L2I+XFxuIgogICAgICAgICAgICAgICAgICAgIGYi8J+TiiBSZW1haW5pbmcgVGFyZ2V0OiB7cmVtYWluaW5nfS97dGFyZ2V0'
    'fVxcbiIKICAgICAgICAgICAgICAgICAgICBmIvCfp74gVGFzazogI3t0YXNrX2lkfSIKICAgICAgICAgICAgICAgICAgICBmIjwv'
    'YmxvY2txdW90ZT4iCiAgICAgICAgICAgICAgICApCiAgICAgICAgICAgICAgICBib3Quc2VuZF9waG90byhyZXBvcnRfY2hhdF9p'
    'ZCBvciBSRVFVSVJFRF9HUk9VUF9JRCwgSU1BR0VfVVJMLCBjYXB0aW9uPWNvbnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVt'
    'KG1zZyksCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICByZXBseV9tYXJrdXA9bWFya3VwLCBwYXJzZV9tb2RlPSJIVE1M'
    'IikKICAgICAgICAgICAgZWxzZToKICAgICAgICAgICAgICAgIG1zZyA9ICgKICAgICAgICAgICAgICAgICAgICBmIjxibG9ja3F1'
    'b3RlPiIKICAgICAgICAgICAgICAgICAgICBmIuKdjCBVSUQge3VpZH06IDxiPlBsYXllciBub3QgZm91bmQgb3IgRXJyb3IuPC9i'
    'PlxcbiIKICAgICAgICAgICAgICAgICAgICBmIvCfk4ogUmVtYWluaW5nIFRhcmdldDoge3JlbWFpbmluZ30ve3RhcmdldH1cXG4i'
    'CiAgICAgICAgICAgICAgICAgICAgZiLwn6e+IFRhc2s6ICN7dGFza19pZH0iCiAgICAgICAgICAgICAgICAgICAgZiI8L2Jsb2Nr'
    'cXVvdGU+IgogICAgICAgICAgICAgICAgKQogICAgICAgICAgICAgICAgYm90LnNlbmRfcGhvdG8ocmVwb3J0X2NoYXRfaWQgb3Ig'
    'UkVRVUlSRURfR1JPVVBfSUQsIElNQUdFX1VSTCwgY2FwdGlvbj1jb252ZXJ0X25vcm1hbF9lbW9qaXNfdG9fcHJlbWl1bShtc2cp'
    'LAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgcmVwbHlfbWFya3VwPW1hcmt1cCwgcGFyc2VfbW9kZT0iSFRNTCIpCiAg'
    'ICAgICAgZXhjZXB0IEV4Y2VwdGlvbiBhcyBlOgogICAgICAgICAgICBlcnJvcl9tc2cgPSBmIjxibG9ja3F1b3RlPuKdjCBFcnJv'
    'ciBwcm9jZXNzaW5nIFVJRCB7dWlkfSAoVGFzayAje3Rhc2tfaWR9KToge3N0cihlKX08L2Jsb2NrcXVvdGU+IgogICAgICAgICAg'
    'ICBib3Quc2VuZF9waG90byhyZXBvcnRfY2hhdF9pZCBvciBSRVFVSVJFRF9HUk9VUF9JRCwgSU1BR0VfVVJMLCBjYXB0aW9uPWNv'
    'bnZlcnRfbm9ybWFsX2Vtb2ppc190b19wcmVtaXVtKGVycm9yX21zZyksIHBhcnNlX21vZGU9IkhUTUwiKQogICAgICAgIHRpbWUu'
    'c2xlZXAoNSkKCiAgICBpZiBtYW51YWw6CiAgICAgICAgYm90LnNlbmRfbWVzc2FnZShSRVFVSVJFRF9HUk9VUF9JRCwgY29udmVy'
    'dF9ub3JtYWxfZW1vamlzX3RvX3ByZW1pdW0oIvCfjIUgPGI+QXV0b0xpa2UgUHJvY2VzcyBDb21wbGV0ZWQuPC9iPiIpKQoKIyA9'
    'PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0KIyDij7AgU0NIRURVTEVSCiMgPT09PT09PT09PT09PT09'
    'PT09PT09PT09PT09PT09PT09PT09PT09PT09CmRlZiBkYWlseV9zY2hlZHVsZXIoKToKICAgIGdsb2JhbCB0b3RhbF9saWtlc191'
    'c2VkLCB0b3RhbF9yZXF1ZXN0c191c2VkLCB1c2VyX3N1Y2Nlc3NfbG9ncwogICAgd2hpbGUgVHJ1ZToKICAgICAgICBub3cgPSBu'
    'b3dfaW5kaWEoKQogICAgICAgIG5leHRfcnVuID0gKG5vdyArIGRhdGV0aW1lLnRpbWVkZWx0YShkYXlzPTEpKS5yZXBsYWNlKGhv'
    'dXI9NCwgbWludXRlPTAsIHNlY29uZD0wLCBtaWNyb3NlY29uZD0wKQogICAgICAgIGlmIG5vdy5ob3VyIDwgNDoKICAgICAgICAg'
    'ICAgbmV4dF9ydW4gPSBub3cucmVwbGFjZShob3VyPTQsIG1pbnV0ZT0wLCBzZWNvbmQ9MCwgbWljcm9zZWNvbmQ9MCkKICAgICAg'
    'ICB3YWl0X3NlY29uZHMgPSAobmV4dF9ydW4gLSBub3cpLnRvdGFsX3NlY29uZHMoKQogICAgICAgIGlmIHdhaXRfc2Vjb25kcyA8'
    'IDA6CiAgICAgICAgICAgIHRpbWUuc2xlZXAoMTApCiAgICAgICAgICAgIGNvbnRpbnVlCiAgICAgICAgcHJpbnQoZiLwn5i0IFNj'
    'aGVkdWxlciBzbGVlcGluZyBmb3Ige3dhaXRfc2Vjb25kc30gc2Vjb25kcy4iKQogICAgICAgIHRpbWUuc2xlZXAod2FpdF9zZWNv'
    'bmRzKQogICAgICAgIHdpdGggU1RBVEVfTE9DSzoKICAgICAgICAgICAgdG90YWxfbGlrZXNfdXNlZCA9IDAKICAgICAgICAgICAg'
    'dG90YWxfcmVxdWVzdHNfdXNlZCA9IDAKICAgICAgICAgICAgdXNlcl9zdWNjZXNzX2xvZ3MuY2xlYXIoKQogICAgICAgIHNhdmVf'
    'ZGF0YSgpCiAgICAgICAgcHJpbnQoZiLwn5SEIERhaWx5IGxpa2UgbGltaXQgYW5kIHVzZXIgbG9ncyByZXNldCBhdCB7bm93X2lu'
    'ZGlhKCl9IikKICAgICAgICBydW5fYXV0b2xpa2VfcHJvY2VzcyhtYW51YWw9RmFsc2UpCiAgICAgICAgdGltZS5zbGVlcCg2MCkK'
    'CiMgUmVzdG9yZSBzYXZlZCBzdGF0ZSBvbmx5IGFmdGVyIGFsbCBzdGF0ZSBjb250YWluZXJzIGFuZCBoYW5kbGVycyBhcmUgZGVm'
    'aW5lZC4KbG9hZF9kYXRhKCkKZW5zdXJlX2F1dG9saWtlX3RhYmxlKCkKbWlncmF0ZV9sZWdhY3lfYXV0b2xpa2VfdGFza3MoKQoK'
    'IyA9PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0KIyDwn5qAIE1BSU4gTE9PUAojID09PT09PT09PT09'
    'PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PQpkZWYgc3RhcnRfYm90X2xvb3AoKToKICAgIHdoaWxlIFRydWU6CiAgICAg'
    'ICAgdHJ5OgogICAgICAgICAgICBwcmludCgi8J+agCBCb3QgcG9sbGluZyBzdGFydGVkLi4uIikKICAgICAgICAgICAgYm90Lmlu'
    'ZmluaXR5X3BvbGxpbmcodGltZW91dD02MCwgbG9uZ19wb2xsaW5nX3RpbWVvdXQ9NjApCiAgICAgICAgZXhjZXB0IHRlbGVib3Qu'
    'YXBpaGVscGVyLkFwaVRlbGVncmFtRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgICAgIGlmIGUucmVzdWx0LnN0YXR1c19jb2RlID09'
    'IDQwOToKICAgICAgICAgICAgICAgIHByaW50KCJDb25mbGljdCBlcnJvciAoNDA5KSBkZXRlY3RlZC4gUmVzdGFydGluZyBpbiA1'
    'IHNlY29uZHMuLi4iKQogICAgICAgICAgICAgICAgdGltZS5zbGVlcCg1KQogICAgICAgICAgICBlbHNlOgogICAgICAgICAgICAg'
    'ICAgcHJpbnQoZiJBUEkgZXJyb3I6IHtlfS4gUmVzdGFydGluZyBpbiAxMHMuLi4iKQogICAgICAgICAgICAgICAgdGltZS5zbGVl'
    'cCgxMCkKICAgICAgICBleGNlcHQgRXhjZXB0aW9uIGFzIGU6CiAgICAgICAgICAgIHByaW50KGYi4pqg77iPIEJvdCBjcmFzaGVk'
    'OiB7ZX0iKQogICAgICAgICAgICB0aW1lLnNsZWVwKDEwKQogICAgICAgIGV4Y2VwdCBLZXlib2FyZEludGVycnVwdDoKICAgICAg'
    'ICAgICAgcHJpbnQoIvCfm5EgQm90IHN0b3BwZWQgbWFudWFsbHkuIikKICAgICAgICAgICAgYnJlYWsKCg=='
)
_bot_runtime = None

_bot_background_started = False
_bot_background_lock = None
_bot_status = {
    "state": "not_started",
    "detail": "Application startup has not run yet.",
}

def _bot_worker(likess_module):
    """Validate Telegram credentials, then keep long polling in a daemon thread."""
    try:
        me = likess_module.bot.get_me()
        _bot_status["state"] = "running"
        _bot_status["detail"] = f"Telegram connected as @{getattr(me, 'username', '') or 'bot'}"
        print("✅ Telegram bot authenticated; starting polling.")
        likess_module.start_bot_loop()
        _bot_status["state"] = "stopped"
        _bot_status["detail"] = "Telegram polling returned."
    except Exception as exc:
        _bot_status["state"] = "error"
        _bot_status["detail"] = f"{type(exc).__name__}: {exc}"
        print(f"❌ Telegram bot startup failed: {_bot_status['detail']}", flush=True)

def start_bot_background():
    """Start bot + scheduler once, whether app.py is run directly or loaded by WSGI."""
    global _bot_background_started, _bot_background_lock
    if _bot_background_started:
        return
    if not os.getenv("BOT_TOKEN", "").strip():
        _bot_status["state"] = "missing_token"
        _bot_status["detail"] = "BOT_TOKEN not found in process environment or checked .env locations. Check Wispbyte Environment file and exact key name BOT_TOKEN."
        print("❌ BOT_TOKEN missing after checking process environment and .env files. Website is online; Telegram bot is NOT started.", flush=True)
        return

    # Wispbyte may load the WSGI app more than once. A process lock prevents
    # multiple Telegram long-polling consumers from answering the same bot.
    try:
        import fcntl
        lock_path = os.path.join(basedir, ".telegram_bot.lock")
        _bot_background_lock = open(lock_path, "w")
        try:
            fcntl.flock(_bot_background_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _bot_status["state"] = "already_running"
            _bot_status["detail"] = "Another web worker owns Telegram polling."
            print("ℹ️ Telegram polling already owned by another worker.", flush=True)
            return
    except Exception as exc:
        _bot_status["state"] = "lock_error"
        _bot_status["detail"] = f"Could not acquire bot process lock: {exc}"
        print(f"❌ {_bot_status['detail']}", flush=True)
        return

    try:
        import threading

        # Load the Telegram bot implementation embedded in this single app.py.
        # It runs in its own namespace so bot globals cannot overwrite Flask globals.
        global _bot_runtime
        _bot_runtime = types.ModuleType("ff_autolike_bot_runtime")
        _bot_runtime.__file__ = __file__
        _bot_runtime.__package__ = ""
        exec(compile(
            base64.b64decode(_BOT_RUNTIME_B64).decode("utf-8"),
            "<embedded-likess.py>",
            "exec"
        ), _bot_runtime.__dict__)

        # Force both website and bot to use exactly the same SQLite file.
        _bot_runtime.DATABASE_FILE = DATABASE_FILE
        _bot_runtime.ensure_autolike_table()
        _bot_runtime.migrate_legacy_autolike_tasks()

        threading.Thread(
            target=_bot_worker, args=(_bot_runtime,),
            name="telegram-bot-polling", daemon=True
        ).start()
        threading.Thread(
            target=_bot_runtime.daily_scheduler,
            name="autolike-daily-scheduler", daemon=True
        ).start()
        _bot_background_started = True
        _bot_status["state"] = "starting"
        _bot_status["detail"] = "Bot thread launched; waiting for Telegram authentication."
        print(f"✅ Bot threads launched. Shared database: {DATABASE_FILE}", flush=True)
    except Exception as exc:
        _bot_status["state"] = "import_error"
        _bot_status["detail"] = f"{type(exc).__name__}: {exc}"
        print(f"❌ Could not launch Telegram bot: {_bot_status['detail']}", flush=True)

@app.route("/health")
def health():
    """Simple hosting diagnostic: confirms web, bot state and shared DB location."""
    try:
        with app.app_context():
            db.session.execute(text("SELECT 1"))
        db_ok = os.path.isfile(DATABASE_FILE)
    except Exception as exc:
        db_ok = False
        db_error = f"{type(exc).__name__}: {exc}"
    else:
        db_error = ""
    return {
        "website": "ok",
        "database": "ok" if db_ok else "error",
        "database_file": os.path.basename(DATABASE_FILE),
        "bot": _bot_status["state"],
        "bot_detail": _bot_status["detail"],
        "bot_token_configured": bool(os.getenv("BOT_TOKEN", "").strip()),
        "env_files_loaded": len(_ENV_FILES_LOADED),
        "database_error": db_error,
    }, (200 if db_ok else 500)

def bootstrap_application():
    """Initialize tables before starting bot; safe for direct and WSGI startup."""
    try:
        init_db()
        start_bot_background()
    except Exception as exc:
        print(f"❌ Application bootstrap failed: {type(exc).__name__}: {exc}", flush=True)
        _bot_status["state"] = "bootstrap_error"
        _bot_status["detail"] = f"{type(exc).__name__}: {exc}"

# IMPORTANT: WSGI servers (e.g. gunicorn) import app:app and never execute
# the __main__ block. Bootstrap at module load so bot startup is not skipped.
bootstrap_application()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.getenv('PORT', '11632')))
