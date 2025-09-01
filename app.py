import os, io
from datetime import date, datetime
from dateutil.relativedelta import relativedelta

from flask import Flask, request, redirect, url_for, render_template_string, flash, Response, send_file
from sqlalchemy import create_engine, Column, Integer, String, Date, Float, ForeignKey, func
from sqlalchemy.orm import sessionmaker, declarative_base, relationship, scoped_session
from openpyxl import Workbook

APP_TITLE = "Budget Ομάδας Χάντμπολ"
app = Flask(__name__)
app.secret_key = "change-me"

# --- DB setup ---
Base = declarative_base()
DB_URL = "sqlite:///budget.db"
engine = create_engine(DB_URL, echo=False, future=True)
Session = scoped_session(sessionmaker(bind=engine))

# --- Models ---
class Category(Base):
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    kind = Column(String, nullable=False)  # 'income' ή 'expense'
    transactions = relationship("Transaction", back_populates="category")

class Section(Base):
    __tablename__ = "sections"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    transactions = relationship("Transaction", back_populates="section")

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    tx_date = Column(Date, nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(String, default="")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    section_id = Column(Integer, ForeignKey("sections.id"), nullable=False)
    category = relationship("Category", back_populates="transactions")
    section = relationship("Section", back_populates="transactions")

Base.metadata.create_all(engine)

# --- Defaults ---
def seed_defaults():
    session = Session()
    if session.query(Category).count() == 0:
        defaults = [
            ("Συνδρομές", "income"),
            ("Εισιτήρια Αγώνα", "income"),
            ("Χορηγίες", "income"),
            ("Άλλα Έσοδα", "income"),
            ("Προπονητές", "expense"),
            ("Έξοδα Αγώνα", "expense"),
            ("Μετακινήσεις", "expense"),
            ("Εξοπλισμός", "expense"),
            ("Άλλα Έξοδα", "expense"),
        ]
        for name, kind in defaults:
            session.add(Category(name=name, kind=kind))
    if session.query(Section).count() == 0:
        for name in ["Ανδρών", "Γυναικών", "Ακαδημίες"]:
            session.add(Section(name=name))
    session.commit()
    session.close()
seed_defaults()

# --- Helpers ---
def parse_date(s): return datetime.strptime(s, "%Y-%m-%d").date()
def month_range(year, month):
    start = date(year, month, 1)
    end = (start + relativedelta(months=1)) - relativedelta(days=1)
    return start, end

def summarize_month(session, year, month, section_id=None):
    start, end = month_range(year, month)
    q = session.query(Category.kind, func.sum(Transaction.amount)).join(Category)
    q = q.filter(Transaction.tx_date >= start, Transaction.tx_date <= end)
    if section_id: q = q.filter(Transaction.section_id == section_id)
    totals = q.group_by(Category.kind).all()
    incomes = sum(v for k, v in totals if k == "income") if totals else 0
    expenses = sum(v for k, v in totals if k == "expense") if totals else 0
    return {"incomes": incomes or 0, "expenses": expenses or 0, "net": (incomes or 0) - (expenses or 0)}

def summarize_year(session, year, section_id=None):
    start, end = date(year,1,1), date(year,12,31)
    q = session.query(Category.kind, func.sum(Transaction.amount)).join(Category)
    q = q.filter(Transaction.tx_date>=start, Transaction.tx_date<=end)
    if section_id: q = q.filter(Transaction.section_id==section_id)
    totals = q.group_by(Category.kind).all()
    incomes = sum(v for k,v in totals if k=="income") if totals else 0
    expenses = sum(v for k,v in totals if k=="expense") if totals else 0
    return {"year":year,"incomes":incomes,"expenses":expenses,"net":incomes-expenses}

# --- Routes ---
@app.route("/")
def dashboard():
    session = Session()
    today = date.today()
    year = int(request.args.get("year", today.year))
    month = int(request.args.get("month", today.month))
    section_id = request.args.get("section_id")
    section_id = int(section_id) if section_id else None

    msum = summarize_month(session, year, month, section_id)
    ysum = summarize_year(session, year, section_id)
    recents = session.query(Transaction).join(Category).join(Section).order_by(Transaction.tx_date.desc()).limit(15).all()
    categories = session.query(Category).all()
    sections = session.query(Section).all()
    session.close()
    return render_template_string(TEMPLATE_DASHBOARD, **locals())

@app.route("/transactions/new", methods=["POST"])
def add_transaction():
    session = Session()
    try:
        tx_date = parse_date(request.form["tx_date"])
        category_id = int(request.form["category_id"])
        section_id = int(request.form["section_id"])
        amount = float(request.form["amount"])
        description = request.form.get("description","")
        session.add(Transaction(tx_date=tx_date,category_id=category_id,section_id=section_id,amount=amount,description=description))
        session.commit()
        flash("Καταχωρήθηκε.", "success")
    except Exception as e:
        session.rollback()
        flash(f"Σφάλμα: {e}", "danger")
    finally: session.close()
    return redirect(url_for("dashboard"))

# --- Export CSV ---
@app.route("/export/csv")
def export_csv():
    year = int(request.args.get("year", date.today().year))
    section_id = request.args.get("section_id")
    section_id = int(section_id) if section_id else None
    session = Session()
    q = session.query(Transaction).join(Category).join(Section).filter(
        Transaction.tx_date >= date(year,1,1), Transaction.tx_date <= date(year,12,31))
    if section_id: q = q.filter(Transaction.section_id==section_id)
    txs = q.order_by(Transaction.tx_date).all()
    session.close()

    def generate():
        yield "Ημερομηνία,Κατηγορία,Τμήμα,Περιγραφή,Ποσό\n"
        for t in txs:
            yield f"{t.tx_date},{t.category.name},{t.section.name},{t.description},{t.amount:.2f}\n"
    return Response(generate(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment;filename=budget_{year}.csv"})

# --- Export Excel ---
@app.route("/export/excel")
def export_excel():
    year = int(request.args.get("year", date.today().year))
    section_id = request.args.get("section_id")
    section_id = int(section_id) if section_id else None
    session = Session()
    q = session.query(Transaction).join(Category).join(Section).filter(
        Transaction.tx_date >= date(year,1,1), Transaction.tx_date <= date(year,12,31))
    if section_id: q = q.filter(Transaction.section_id==section_id)
    txs = q.order_by(Transaction.tx_date).all()
    session.close()

    wb = Workbook(); ws = wb.active; ws.title = f"{year}"
    ws.append(["Ημερομηνία","Κατηγορία","Τμήμα","Περιγραφή","Ποσό (€)"])
    for t in txs:
        ws.append([t.tx_date.strftime("%d/%m/%Y"), t.category.name, t.section.name, t.description, t.amount])
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return send_file(bio, as_attachment=True, download_name=f"budget_{year}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# --- Report με γράφημα ανά τμήμα ---
@app.route("/reports/sections")
def report_sections():
    year = int(request.args.get("year", date.today().year))
    session = Session()
    sections = session.query(Section).all()
    data = []
    for s in sections:
        ysum = summarize_year(session, year, s.id)
        data.append({"name": s.name, "incomes": ysum["incomes"], "expenses": ysum["expenses"], "net": ysum["net"]})
    session.close()
    return render_template_string(TEMPLATE_SECTIONS, year=year, data=data)

# --- Templates ---
TEMPLATE_DASHBOARD = """
<!doctype html><html lang="el"><head>
<meta charset="utf-8"><title>{{APP_TITLE}}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
</head><body class="p-3">
<h1 class="h4">{{APP_TITLE}}</h1>
<form method="get">
  Έτος: <input type="number" name="year" value="{{year}}">
  Μήνας: <input type="number" name="month" value="{{month}}" min="1" max="12">
  Τμήμα:
  <select name="section_id">
    <option value="">Όλα</option>
    {% for s in sections %}
      <option value="{{s.id}}" {% if section_id==s.id %}selected{% endif %}>{{s.name}}</option>
    {% endfor %}
  </select>
  <button class="btn btn-sm btn-primary">Εμφάνιση</button>
</form>
<p>Μήνας: Έσοδα {{msum.incomes}} € · Έξοδα {{msum.expenses}} € · Καθαρό {{msum.net}} €</p>
<p>Έτος: Έσοδα {{ysum.incomes}} € · Έξοδα {{ysum.expenses}} € · Καθαρό {{ysum.net}} €</p>
<a href="{{url_for('export_csv',year=year,section_id=section_id)}}">Εξαγωγή CSV</a> |
<a href="{{url_for('export_excel',year=year,section_id=section_id)}}">Εξαγωγή Excel</a> |
<a href="{{url_for('report_sections',year=year)}}">Σύγκριση Τμημάτων</a>
<hr>
<h5>Καταχώρηση</h5>
<form method="post" action="{{url_for('add_transaction')}}">
  <input type="date" name="tx_date" required>
  <select name="category_id">{% for c in categories %}<option value="{{c.id}}">{{c.name}}</option>{% endfor %}</select>
  <select name="section_id">{% for s in sections %}<option value="{{s.id}}">{{s.name}}</option>{% endfor %}</select>
  <input type="number" step="0.01" name="amount" placeholder="Ποσό">
  <input type="text" name="description" placeholder="Περιγραφή">
  <button class="btn btn-success btn-sm">Καταχώρηση</button>
</form>
<hr>
<h5>Πρόσφατα</h5>
<table class="table table-sm"><tr><th>Ημ/νία</th><th>Κατηγορία</th><th>Τμήμα</th><th>Περιγραφή</th><th>Ποσό</th></tr>
{% for t in recents %}
<tr><td>{{t.tx_date}}</td><td>{{t.category.name}}</td><td>{{t.section.name}}</td><td>{{t.description}}</td><td>{{t.amount}}</td></tr>
{% endfor %}
</table>
</body></html>
"""

TEMPLATE_SECTIONS = """
<!doctype html><html lang="el"><head>
<meta charset="utf-8"><title>Σύγκριση Τμημάτων</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head><body class="p-3">
<h2>Σύγκριση Τμημάτων ({{year}})</h2>
<canvas id="bar" height="120"></canvas>
<script>
const ctx = document.getElementById('bar');
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: {{ data|map(attribute='name')|list }},
    datasets: [
      {label: 'Έσοδα', backgroundColor:'#198754aa', data: {{ data|map(attribute='incomes')|list }} },
      {label: 'Έξοδα', backgroundColor:'#dc3545aa', data: {{ data|map(attribute='expenses')|list }} },
      {label: 'Καθαρό', backgroundColor:'#0d6efdaa', data: {{ data|map(attribute='net')|list }} },
    ]
  },
  options: { plugins:{legend:{position:'bottom'}} }
});
</script>
<p><a href="{{url_for('dashboard')}}">← Πίσω</a></p>
</body></html>
"""

if __name__ == "__main__":
    app.run(debug=True)
