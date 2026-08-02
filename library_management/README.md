# Library Management System

A complete Flask + SQLite web application to manage a library: books,
members, issuing/returning books, overdue fines, and fine payments —
all protected behind a login/register system.

## Features

- **Login / Register** — every page requires an account. Passwords are
  hashed (never stored in plain text).
- **Dashboard** — live stats: total titles, copies, members, books issued,
  overdue books, pending dues, and total fines collected.
- **Books** — add, edit, delete, and search books by title/author.
- **Members** — add and remove members; each member shows their pending
  dues at a glance.
- **Issue / Return** — issuing a book sets a due date automatically
  (14-day loan period by default). Members with unpaid fines cannot be
  issued a new book until they settle up.
- **Automatic fines** — returning a book late calculates a fine
  (Rs. 5/day by default) and records it against that transaction.
- **Payments** — a dedicated Payments page with three sections:
  - **Bill a Member** — charge any member directly for anything (membership
    fee, damaged book, replacement cost, etc.) without needing a book to
    ever be issued or returned late. This makes the payment feature
    always accessible for testing/demo, not only after an overdue return.
  - **Pending Book Fines** — automatic late-return fines waiting to be paid.
  - **Pending Charges** — manual charges waiting to be paid.
  - **Payment History** — every completed payment, with reference number,
    method, and amount.
  Every pending item (fine or charge) has a "Pay Now" button that opens a
  realistic checkout page (Card / UPI / Cash tabs, order summary,
  card-number auto-formatting) and generates a payment reference number
  on success.

## IMPORTANT — About the Payment Feature

The checkout on `/pay/<transaction_id>` is a **simulated payment flow**:

- No real card is charged and no external payment gateway is contacted.
- Card details entered are used only to display the last 4 digits on the
  receipt — the full card number, expiry, and CVV are **never saved** to
  the database.
- It exists so the full user experience (and your database records)
  behave exactly like a real checkout, so you can swap in a **real**
  payment gateway later without changing the rest of the app.

### To connect a real payment gateway later
Replace the body of the `POST` branch in the `pay_fine()` / `pay_charge()`
views in `app.py` (both share the `_process_checkout()` helper) with a
call to your provider's SDK, for example:
- **Razorpay** (popular in India): create an Order, use Razorpay
  Checkout.js on `pay.html`, verify the payment signature on a webhook,
  then run the same `UPDATE transactions SET fine_paid = 1` and
  `INSERT INTO payments` logic on success.
- **Stripe**: create a PaymentIntent, use Stripe Elements on `pay.html`,
  confirm on the client, verify via webhook, then update the database
  the same way.

## Folder Structure

```
library_management/
├── app.py                    # all routes, database setup, business logic
├── library.db                 # auto-created on first run (SQLite database)
├── templates/
│   ├── base.html               # shared layout (navbar, footer, flash messages)
│   ├── login.html               # login page (realistic background)
│   ├── register.html            # registration page
│   ├── index.html                # dashboard with stats
│   ├── books.html                 # list/search books
│   ├── add_book.html               # add book form
│   ├── edit_book.html               # edit book form
│   ├── members.html                  # list members + pending dues
│   ├── add_member.html                # add member form
│   ├── issue.html                      # issue a book form
│   ├── transactions.html                # issue/return history + fines
│   ├── payments.html                     # pending dues + payment history
│   └── pay.html                           # checkout / payment page
└── static/
    ├── style.css                # all styling
    └── images/
        └── library-bg.jpg        # generated background image for login/register
```

## Setup & Run

1. Install Python 3.8+ if you don't have it already.
2. Install Flask:
   ```
   pip install flask
   ```
3. Run the app:
   ```
   python app.py
   ```
4. Open your browser at:
   ```
   http://127.0.0.1:5000
   ```
   You'll be sent straight to the login page. Click **"Create one"**
   to register your first account, then log in.

## Adjusting Library Policy

Near the top of `app.py`:

```python
LOAN_PERIOD_DAYS = 14      # how many days a member can keep a book
FINE_PER_DAY = 5           # fine charged per day overdue
```

Change these two numbers to match your library's actual policy.

## Notes

- `library.db` is created automatically the first time you run the app —
  don't worry if it's not there before that.
- To use a real photograph as the login/register background instead of
  the generated illustration, replace
  `static/images/library-bg.jpg` with your own image (same filename).
