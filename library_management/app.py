"""
Library Management System
--------------------------
A Flask + SQLite web app to manage Books, Members, Book Issue / Return
records, Fines, and Payments - protected behind a Login / Register system.

NOTE ON PAYMENTS:
The "Pay Fine" checkout on /pay/<transaction_id> is a SIMULATED payment
flow (no real card is charged, no real gateway is contacted). It is built
so the UI/UX and database records behave exactly like a real checkout,
making it simple to swap in a real provider later. See README.md for how
to plug in a real gateway (Razorpay / Stripe) when you are ready.

Run with:  python app.py
Then open: http://127.0.0.1:5000 in your browser
"""

import random
import sqlite3
import string
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "library_secret_key"   # needed for sessions + flash messages

DB_NAME = "library.db"

# ---- Library policy settings ----
LOAN_PERIOD_DAYS = 14      # how many days a member can keep a book
FINE_PER_DAY = 5           # fine charged per day overdue (in INR / your currency)


# ---------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------
def get_db_connection():
    """Create a connection to the SQLite database."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row   # lets us access columns by name
    return conn


def init_db():
    """Create tables if they do not already exist."""
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            isbn TEXT,
            quantity INTEGER NOT NULL,
            available INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            issue_date TEXT NOT NULL,
            due_date TEXT NOT NULL,
            return_date TEXT,
            status TEXT NOT NULL,
            fine_amount REAL NOT NULL DEFAULT 0,
            fine_paid INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (book_id) REFERENCES books (id),
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    """)

    # Manual charges: membership fees, damage fees, replacement costs, etc.
    # These can be billed to a member directly (no book/return involved),
    # so payments are always accessible for testing/demo, not only after
    # a book happens to come back late.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS charges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            paid INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    """)

    # Payments are denormalized (member name + description stored directly)
    # so the history page never breaks even if the underlying fine/charge
    # record is later removed.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,          -- 'fine' or 'charge'
            source_id INTEGER NOT NULL,
            member_id INTEGER NOT NULL,
            member_name TEXT NOT NULL,
            description TEXT NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            card_last4 TEXT,
            reference_no TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            paid_at TEXT NOT NULL,
            FOREIGN KEY (member_id) REFERENCES members (id)
        )
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------------
# AUTH HELPERS
# ---------------------------------------------------------
def login_required(view_func):
    """Redirect anonymous visitors to the login page, remembering where
    they were headed so we can send them back after they sign in."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to access that page.", "error")
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    """Make the logged-in username available to every template."""
    return {"current_username": session.get("username")}


# ---------------------------------------------------------
# FINE / PAYMENT HELPERS
# ---------------------------------------------------------
def days_overdue(due_date_str, as_of=None):
    """Return how many whole days overdue something is (0 if not overdue)."""
    due = datetime.strptime(due_date_str, "%Y-%m-%d").date()
    today = as_of or date.today()
    delta = (today - due).days
    return max(delta, 0)


def generate_reference_no():
    """Generate a unique-looking payment reference number, e.g. PAY-7F3K9Q2A."""
    chars = string.ascii_uppercase + string.digits
    return "PAY-" + "".join(random.choices(chars, k=8))


# ---------------------------------------------------------
# AUTH ROUTES
# ---------------------------------------------------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("Please fill in all fields.", "error")
            return redirect(url_for("register"))

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "error")
            return redirect(url_for("register"))

        if password != confirm:
            flash("Passwords do not match.", "error")
            return redirect(url_for("register"))

        conn = get_db_connection()
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? OR email = ?",
            (username, email),
        ).fetchone()

        if existing:
            conn.close()
            flash("That username or email is already registered.", "error")
            return redirect(url_for("register"))

        conn.execute(
            "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (username, email, generate_password_hash(password), date.today().isoformat()),
        )
        conn.commit()
        conn.close()

        flash("Account created successfully! You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        conn = get_db_connection()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (identifier, identifier.lower()),
        ).fetchone()
        conn.close()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid username/email or password.", "error")
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]

        flash(f"Welcome back, {user['username']}!", "success")
        next_page = request.args.get("next")
        return redirect(next_page or url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ---------------------------------------------------------
# HOME / DASHBOARD
# ---------------------------------------------------------
@app.route("/")
@login_required
def index():
    conn = get_db_connection()
    total_books = conn.execute("SELECT COALESCE(SUM(quantity),0) AS c FROM books").fetchone()["c"]
    total_titles = conn.execute("SELECT COUNT(*) AS c FROM books").fetchone()["c"]
    total_members = conn.execute("SELECT COUNT(*) AS c FROM members").fetchone()["c"]
    issued_count = conn.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE status = 'Issued'"
    ).fetchone()["c"]
    overdue_count = conn.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE status='Issued' AND due_date < ?",
        (date.today().isoformat(),),
    ).fetchone()["c"]
    pending_dues = conn.execute(
        "SELECT COALESCE(SUM(fine_amount),0) AS s FROM transactions WHERE fine_paid=0 AND fine_amount>0"
    ).fetchone()["s"]
    total_collected = conn.execute(
        "SELECT COALESCE(SUM(amount),0) AS s FROM payments WHERE status='Paid'"
    ).fetchone()["s"]
    conn.close()

    stats = {
        "total_books": total_books,
        "total_titles": total_titles,
        "total_members": total_members,
        "issued_count": issued_count,
        "overdue_count": overdue_count,
        "pending_dues": pending_dues,
        "total_collected": total_collected,
    }
    return render_template("index.html", stats=stats)


# ---------------------------------------------------------
# BOOKS
# ---------------------------------------------------------
@app.route("/books")
@login_required
def books():
    search = request.args.get("search", "").strip()
    conn = get_db_connection()

    if search:
        query = "SELECT * FROM books WHERE title LIKE ? OR author LIKE ? ORDER BY id DESC"
        like = f"%{search}%"
        all_books = conn.execute(query, (like, like)).fetchall()
    else:
        all_books = conn.execute("SELECT * FROM books ORDER BY id DESC").fetchall()

    conn.close()
    return render_template("books.html", books=all_books, search=search)


@app.route("/books/add", methods=["GET", "POST"])
@login_required
def add_book():
    if request.method == "POST":
        title = request.form["title"].strip()
        author = request.form["author"].strip()
        isbn = request.form.get("isbn", "").strip()
        quantity = int(request.form["quantity"])

        if not title or not author or quantity <= 0:
            flash("Please fill all required fields with valid values.", "error")
            return redirect(url_for("add_book"))

        conn = get_db_connection()
        conn.execute(
            "INSERT INTO books (title, author, isbn, quantity, available) VALUES (?, ?, ?, ?, ?)",
            (title, author, isbn, quantity, quantity),
        )
        conn.commit()
        conn.close()

        flash(f'Book "{title}" added successfully!', "success")
        return redirect(url_for("books"))

    return render_template("add_book.html")


@app.route("/books/edit/<int:book_id>", methods=["GET", "POST"])
@login_required
def edit_book(book_id):
    conn = get_db_connection()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()

    if book is None:
        conn.close()
        flash("Book not found.", "error")
        return redirect(url_for("books"))

    if request.method == "POST":
        title = request.form["title"].strip()
        author = request.form["author"].strip()
        isbn = request.form.get("isbn", "").strip()
        quantity = int(request.form["quantity"])

        # Adjust "available" count based on the change in total quantity
        issued_out = book["quantity"] - book["available"]
        new_available = max(quantity - issued_out, 0)

        conn.execute(
            "UPDATE books SET title=?, author=?, isbn=?, quantity=?, available=? WHERE id=?",
            (title, author, isbn, quantity, new_available, book_id),
        )
        conn.commit()
        conn.close()

        flash("Book updated successfully!", "success")
        return redirect(url_for("books"))

    conn.close()
    return render_template("edit_book.html", book=book)


@app.route("/books/delete/<int:book_id>")
@login_required
def delete_book(book_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
    conn.close()
    flash("Book deleted.", "success")
    return redirect(url_for("books"))


# ---------------------------------------------------------
# MEMBERS
# ---------------------------------------------------------
@app.route("/members")
@login_required
def members():
    conn = get_db_connection()
    all_members = conn.execute("""
        SELECT m.*,
               COALESCE(fines.total, 0) + COALESCE(charges.total, 0) AS pending_dues
        FROM members m
        LEFT JOIN (
            SELECT member_id, SUM(fine_amount) AS total
            FROM transactions
            WHERE fine_paid = 0 AND fine_amount > 0
            GROUP BY member_id
        ) fines ON fines.member_id = m.id
        LEFT JOIN (
            SELECT member_id, SUM(amount) AS total
            FROM charges
            WHERE paid = 0
            GROUP BY member_id
        ) charges ON charges.member_id = m.id
        ORDER BY m.id DESC
    """).fetchall()
    conn.close()
    return render_template("members.html", members=all_members)


@app.route("/members/add", methods=["GET", "POST"])
@login_required
def add_member():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()

        if not name:
            flash("Member name is required.", "error")
            return redirect(url_for("add_member"))

        conn = get_db_connection()
        conn.execute(
            "INSERT INTO members (name, email, phone) VALUES (?, ?, ?)",
            (name, email, phone),
        )
        conn.commit()
        conn.close()

        flash(f'Member "{name}" added successfully!', "success")
        return redirect(url_for("members"))

    return render_template("add_member.html")


@app.route("/members/delete/<int:member_id>")
@login_required
def delete_member(member_id):
    conn = get_db_connection()
    active = conn.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE member_id=? AND status='Issued'",
        (member_id,),
    ).fetchone()["c"]

    if active > 0:
        conn.close()
        flash("Cannot delete member with books currently issued.", "error")
        return redirect(url_for("members"))

    unpaid_fines = conn.execute(
        "SELECT COUNT(*) AS c FROM transactions WHERE member_id=? AND fine_paid=0 AND fine_amount>0",
        (member_id,),
    ).fetchone()["c"]

    unpaid_charges = conn.execute(
        "SELECT COUNT(*) AS c FROM charges WHERE member_id=? AND paid=0",
        (member_id,),
    ).fetchone()["c"]

    if unpaid_fines > 0 or unpaid_charges > 0:
        conn.close()
        flash("Cannot delete member with unpaid dues. Please settle payments first.", "error")
        return redirect(url_for("members"))

    conn.execute("DELETE FROM members WHERE id = ?", (member_id,))
    conn.commit()
    conn.close()
    flash("Member deleted.", "success")
    return redirect(url_for("members"))


# ---------------------------------------------------------
# ISSUE / RETURN BOOKS
# ---------------------------------------------------------
@app.route("/issue", methods=["GET", "POST"])
@login_required
def issue_book():
    conn = get_db_connection()

    if request.method == "POST":
        book_id = int(request.form["book_id"])
        member_id = int(request.form["member_id"])

        book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()

        if book is None or book["available"] <= 0:
            flash("Selected book is not available for issue.", "error")
            conn.close()
            return redirect(url_for("issue_book"))

        unpaid_fines = conn.execute(
            "SELECT COUNT(*) AS c FROM transactions WHERE member_id=? AND fine_paid=0 AND fine_amount>0",
            (member_id,),
        ).fetchone()["c"]
        unpaid_charges = conn.execute(
            "SELECT COUNT(*) AS c FROM charges WHERE member_id=? AND paid=0",
            (member_id,),
        ).fetchone()["c"]
        if unpaid_fines > 0 or unpaid_charges > 0:
            conn.close()
            flash("This member has unpaid dues. Please settle payments before issuing a new book.", "error")
            return redirect(url_for("issue_book"))

        issue_date = date.today()
        due_date = issue_date + timedelta(days=LOAN_PERIOD_DAYS)

        conn.execute(
            "INSERT INTO transactions (book_id, member_id, issue_date, due_date, status) VALUES (?, ?, ?, ?, ?)",
            (book_id, member_id, issue_date.isoformat(), due_date.isoformat(), "Issued"),
        )
        conn.execute(
            "UPDATE books SET available = available - 1 WHERE id = ?", (book_id,)
        )
        conn.commit()
        conn.close()

        flash(f"Book issued successfully! Due back by {due_date.strftime('%d %b %Y')}.", "success")
        return redirect(url_for("transactions"))

    available_books = conn.execute("SELECT * FROM books WHERE available > 0").fetchall()
    all_members = conn.execute("SELECT * FROM members").fetchall()
    conn.close()
    return render_template(
        "issue.html", books=available_books, members=all_members,
        loan_days=LOAN_PERIOD_DAYS,
    )


@app.route("/return/<int:transaction_id>")
@login_required
def return_book(transaction_id):
    conn = get_db_connection()
    txn = conn.execute("SELECT * FROM transactions WHERE id=?", (transaction_id,)).fetchone()

    if txn and txn["status"] == "Issued":
        return_date = date.today()
        late_days = days_overdue(txn["due_date"], as_of=return_date)
        fine = late_days * FINE_PER_DAY

        conn.execute(
            "UPDATE transactions SET status='Returned', return_date=?, fine_amount=?, fine_paid=? WHERE id=?",
            (return_date.isoformat(), fine, 1 if fine == 0 else 0, transaction_id),
        )
        conn.execute(
            "UPDATE books SET available = available + 1 WHERE id=?", (txn["book_id"],)
        )
        conn.commit()

        if fine > 0:
            flash(
                f"Book returned {late_days} day(s) late. A fine of Rs. {fine:.2f} has been added — "
                f"collect payment on the Payments page.",
                "error",
            )
        else:
            flash("Book returned on time. No fine due.", "success")
    else:
        flash("Invalid transaction.", "error")

    conn.close()
    return redirect(url_for("transactions"))


@app.route("/transactions")
@login_required
def transactions():
    conn = get_db_connection()
    all_txns = conn.execute("""
        SELECT t.id, b.title AS book_title, m.name AS member_name,
               t.issue_date, t.due_date, t.return_date, t.status,
               t.fine_amount, t.fine_paid
        FROM transactions t
        JOIN books b ON t.book_id = b.id
        JOIN members m ON t.member_id = m.id
        ORDER BY t.id DESC
    """).fetchall()
    conn.close()

    today_str = date.today().isoformat()
    return render_template("transactions.html", transactions=all_txns, today=today_str)


# ---------------------------------------------------------
# PAYMENTS  (book fines + manually-added charges)
# ---------------------------------------------------------
def _process_checkout(conn, method, form):
    """Shared card/UPI validation used by both pay_fine and pay_charge.
    Returns (ok, last4_or_None, error_message)."""
    if method == "card":
        card_name = form.get("card_name", "").strip()
        card_number = form.get("card_number", "").replace(" ", "")
        expiry = form.get("expiry", "").strip()
        cvv = form.get("cvv", "").strip()
        if not card_name or len(card_number) < 12 or len(expiry) < 4 or len(cvv) < 3:
            return False, None, "Please fill in valid card details."
        return True, card_number[-4:], None
    elif method == "upi":
        upi_id = form.get("upi_id", "").strip()
        if not upi_id or "@" not in upi_id:
            return False, None, "Please enter a valid UPI ID (e.g. name@bank)."
        return True, None, None
    else:  # cash / counter payment needs no extra fields
        return True, None, None


@app.route("/payments")
@login_required
def payments():
    conn = get_db_connection()

    pending_fines = conn.execute("""
        SELECT t.id AS transaction_id, b.title AS book_title, m.name AS member_name,
               t.due_date, t.return_date, t.fine_amount
        FROM transactions t
        JOIN books b ON t.book_id = b.id
        JOIN members m ON t.member_id = m.id
        WHERE t.fine_paid = 0 AND t.fine_amount > 0
        ORDER BY t.id DESC
    """).fetchall()

    pending_charges = conn.execute("""
        SELECT c.id AS charge_id, c.description, c.amount, c.created_at, m.name AS member_name
        FROM charges c
        JOIN members m ON c.member_id = m.id
        WHERE c.paid = 0
        ORDER BY c.id DESC
    """).fetchall()

    payment_history = conn.execute(
        "SELECT * FROM payments ORDER BY id DESC"
    ).fetchall()

    all_members = conn.execute("SELECT * FROM members ORDER BY name").fetchall()
    preselect_member_id = request.args.get("member_id", type=int)

    conn.close()
    return render_template(
        "payments.html",
        pending_fines=pending_fines,
        pending_charges=pending_charges,
        payment_history=payment_history,
        all_members=all_members,
        preselect_member_id=preselect_member_id,
    )


@app.route("/charges/add", methods=["POST"])
@login_required
def add_charge():
    """Bill a member directly - membership fee, damage fee, replacement
    cost, etc. Makes a payment always available to test/collect, even if
    no book has ever been returned late."""
    member_id = request.form.get("member_id", "")
    description = request.form.get("description", "").strip()
    amount = request.form.get("amount", "")

    try:
        amount = float(amount)
    except ValueError:
        amount = 0

    if not member_id or not description or amount <= 0:
        flash("Please choose a member, a description, and a valid amount.", "error")
        return redirect(url_for("payments"))

    conn = get_db_connection()
    conn.execute(
        "INSERT INTO charges (member_id, description, amount, created_at) VALUES (?, ?, ?, ?)",
        (member_id, description, amount, date.today().isoformat()),
    )
    conn.commit()
    conn.close()

    flash(f'Charge "{description}" (Rs. {amount:.2f}) added — ready to collect.', "success")
    return redirect(url_for("payments"))


@app.route("/pay/fine/<int:transaction_id>", methods=["GET", "POST"], endpoint="pay_fine")
@login_required
def pay_fine(transaction_id):
    conn = get_db_connection()
    txn = conn.execute("""
        SELECT t.*, b.title AS book_title, m.name AS member_name, m.id AS member_id
        FROM transactions t
        JOIN books b ON t.book_id = b.id
        JOIN members m ON t.member_id = m.id
        WHERE t.id = ?
    """, (transaction_id,)).fetchone()

    if txn is None or txn["fine_amount"] <= 0 or txn["fine_paid"] == 1:
        conn.close()
        flash("There is no pending fine for that record.", "error")
        return redirect(url_for("payments"))

    if request.method == "POST":
        method = request.form.get("method", "card")
        ok, last4, error = _process_checkout(conn, method, request.form)

        if not ok:
            conn.close()
            flash(error, "error")
            return redirect(url_for("pay_fine", transaction_id=transaction_id))

        reference_no = generate_reference_no()
        description = f"Late fine - {txn['book_title']}"

        conn.execute(
            """INSERT INTO payments
               (source_type, source_id, member_id, member_name, description, amount,
                method, card_last4, reference_no, status, paid_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("fine", transaction_id, txn["member_id"], txn["member_name"], description,
             txn["fine_amount"], method, last4, reference_no, "Paid",
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute(
            "UPDATE transactions SET fine_paid = 1 WHERE id = ?", (transaction_id,)
        )
        conn.commit()
        conn.close()

        flash(f"Payment successful! Reference No: {reference_no}", "success")
        return redirect(url_for("payments"))

    conn.close()
    return render_template(
        "pay.html",
        heading="Pay Outstanding Fine",
        description=f"Late fine for \"{txn['book_title']}\"",
        member_name=txn["member_name"],
        amount=txn["fine_amount"],
        summary_rows=[
            ("Book", txn["book_title"]),
            ("Member", txn["member_name"]),
            ("Due Date", txn["due_date"]),
            ("Returned On", txn["return_date"] or "-"),
        ],
        action_url=url_for("pay_fine", transaction_id=transaction_id),
    )


@app.route("/pay/charge/<int:charge_id>", methods=["GET", "POST"], endpoint="pay_charge")
@login_required
def pay_charge(charge_id):
    conn = get_db_connection()
    charge = conn.execute("""
        SELECT c.*, m.name AS member_name, m.id AS member_id
        FROM charges c
        JOIN members m ON c.member_id = m.id
        WHERE c.id = ?
    """, (charge_id,)).fetchone()

    if charge is None or charge["paid"] == 1:
        conn.close()
        flash("There is no pending charge for that record.", "error")
        return redirect(url_for("payments"))

    if request.method == "POST":
        method = request.form.get("method", "card")
        ok, last4, error = _process_checkout(conn, method, request.form)

        if not ok:
            conn.close()
            flash(error, "error")
            return redirect(url_for("pay_charge", charge_id=charge_id))

        reference_no = generate_reference_no()

        conn.execute(
            """INSERT INTO payments
               (source_type, source_id, member_id, member_name, description, amount,
                method, card_last4, reference_no, status, paid_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("charge", charge_id, charge["member_id"], charge["member_name"],
             charge["description"], charge["amount"], method, last4, reference_no,
             "Paid", datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute("UPDATE charges SET paid = 1 WHERE id = ?", (charge_id,))
        conn.commit()
        conn.close()

        flash(f"Payment successful! Reference No: {reference_no}", "success")
        return redirect(url_for("payments"))

    conn.close()
    return render_template(
        "pay.html",
        heading="Pay Outstanding Charge",
        description=charge["description"],
        member_name=charge["member_name"],
        amount=charge["amount"],
        summary_rows=[
            ("Description", charge["description"]),
            ("Member", charge["member_name"]),
            ("Billed On", charge["created_at"]),
        ],
        action_url=url_for("pay_charge", charge_id=charge_id),
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
if __name__ == "__main__":
    init_db()
    app.run(debug=True)
