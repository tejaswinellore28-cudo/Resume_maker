"""
SmartResume - Resume builder with strength analysis and verified-company matching.
Stack: Python (Flask) + SQLite + HTML/CSS.

Run:   pip install flask
       python app.py
Open:  http://127.0.0.1:5000
"""
import io
import os
import re
import sqlite3
import zipfile
from datetime import datetime
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "smartresume.db")

app = Flask(__name__)
app.secret_key = "change-this-secret-key-before-deploying"
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB uploads


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    phone       TEXT DEFAULT '',
    location    TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    last_login  TEXT
);

CREATE TABLE IF NOT EXISTS resumes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    full_name   TEXT NOT NULL,
    email       TEXT NOT NULL,
    phone       TEXT NOT NULL,
    location    TEXT NOT NULL,
    job_title   TEXT NOT NULL,
    summary     TEXT DEFAULT '',
    education   TEXT DEFAULT '',
    skills      TEXT DEFAULT '',
    experience  TEXT DEFAULT '',
    strength    INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source      TEXT NOT NULL,
    strength    INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    industry    TEXT NOT NULL,
    location    TEXT NOT NULL,
    website     TEXT NOT NULL,
    roles       TEXT NOT NULL,
    skills      TEXT NOT NULL,
    verified    INTEGER NOT NULL DEFAULT 1
);
"""

# Sample data: edit / extend this list (or the `companies` table) with your own.
# Only rows with verified = 1 are shown to users.
SEED_COMPANIES = [
    ("TCS", "IT Services", "India (Pan-India)", "https://www.tcs.com",
     "Software Developer, Systems Engineer",
     "python,java,sql,html,css,javascript,testing,git"),
    ("Infosys", "IT Services", "India (Pan-India)", "https://www.infosys.com",
     "Systems Engineer, Full Stack Developer",
     "java,python,sql,html,css,javascript,spring,git"),
    ("Wipro", "IT Services", "India (Pan-India)", "https://www.wipro.com",
     "Project Engineer, Cloud Engineer",
     "java,python,sql,aws,linux,testing,git"),
    ("HCLTech", "IT Services", "India (Pan-India)", "https://www.hcltech.com",
     "Graduate Engineer Trainee, DevOps Engineer",
     "java,python,sql,linux,docker,jenkins,git"),
    ("Tech Mahindra", "IT Services", "India (Pan-India)", "https://www.techmahindra.com",
     "Software Engineer, Network Engineer",
     "java,python,sql,html,css,javascript,testing"),
    ("Cognizant", "IT Services", "India (Pan-India)", "https://www.cognizant.com",
     "Programmer Analyst, Data Engineer",
     "java,python,sql,html,css,javascript,react,aws"),
    ("Capgemini", "IT Services", "India (Pan-India)", "https://www.capgemini.com",
     "Analyst, Software Engineer",
     "java,python,sql,javascript,angular,git,agile"),
    ("Accenture", "Consulting & Technology", "India (Pan-India)", "https://www.accenture.com",
     "Associate Software Engineer, Data Analyst",
     "python,java,sql,excel,power bi,aws,agile"),
    ("Zoho", "Product / SaaS", "Chennai, India", "https://www.zoho.com",
     "Member Technical Staff, Web Developer",
     "java,javascript,html,css,sql,python,git"),
    ("Freshworks", "Product / SaaS", "Chennai, India", "https://www.freshworks.com",
     "Software Engineer, Frontend Engineer",
     "javascript,react,html,css,ruby,sql,git"),
    ("Amazon", "E-commerce & Cloud", "Hyderabad / Bengaluru, India", "https://www.amazon.jobs",
     "SDE, Data Engineer, Cloud Support Associate",
     "python,java,sql,aws,data structures,algorithms,linux"),
    ("Microsoft", "Software & Cloud", "Hyderabad / Bengaluru, India", "https://careers.microsoft.com",
     "Software Engineer, Data Scientist",
     "c#,python,sql,azure,javascript,data structures,algorithms"),
    ("Google", "Software & Cloud", "Hyderabad / Bengaluru, India", "https://careers.google.com",
     "Software Engineer, Site Reliability Engineer",
     "python,java,c++,algorithms,data structures,linux,sql"),
    ("Deloitte", "Consulting", "India (Pan-India)", "https://www.deloitte.com",
     "Analyst, Data Analyst, Business Technology Analyst",
     "sql,excel,power bi,tableau,python,communication"),
    ("Oracle", "Software & Cloud", "Hyderabad / Bengaluru, India", "https://www.oracle.com",
     "Application Developer, Database Engineer",
     "java,sql,python,linux,javascript,cloud"),
    ("Flipkart", "E-commerce", "Bengaluru, India", "https://www.flipkartcareers.com",
     "SDE, Data Analyst, Machine Learning Engineer",
     "java,python,sql,machine learning,data structures,algorithms,spark"),
]

# Extra skills the analyzer should recognise inside uploaded resume text.
EXTRA_SKILLS = [
    "c", "r", "php", "mysql", "mongodb", "flask", "django", "node.js", "bootstrap",
    "typescript", "kubernetes", "communication", "teamwork", "leadership",
    "problem solving", "deep learning", "nlp", "pandas", "numpy", "figma", "oop",
    "rest api", "postgresql", "firebase", "android", "kotlin", "swift", "go",
]


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    if db.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO companies (name, industry, location, website, roles, skills, verified)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)",
            SEED_COMPANIES,
        )
    db.commit()
    db.close()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------
# Auth helpers
# --------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    if "user_id" not in session:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()


@app.context_processor
def inject_user():
    return {"user": current_user()}


@app.template_filter("day")
def day_filter(value):
    """'2026-10-29 14:27' -> '29 Oct 2026'"""
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d %b %Y")
    except (TypeError, ValueError):
        return value or "-"


@app.template_filter("tone")
def tone_filter(score):
    if score >= 80:
        return "good"
    if score >= 60:
        return "mid"
    return "low"


# --------------------------------------------------------------------------
# Skills, strength scoring and company matching
# --------------------------------------------------------------------------
def parse_skills(raw):
    """'Python, SQL; html' -> ['Python', 'SQL', 'html'] (unique, order kept)."""
    seen, out = set(), []
    for part in re.split(r"[,;\n|]+", raw or ""):
        s = part.strip(" .-•*\t")
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def skill_vocabulary():
    vocab = set(EXTRA_SKILLS)
    for row in get_db().execute("SELECT skills FROM companies"):
        vocab.update(s.strip().lower() for s in row["skills"].split(",") if s.strip())
    return vocab


ACTION_VERBS = (
    "developed built designed implemented created led managed improved increased reduced "
    "delivered launched optimized automated analyzed engineered deployed collaborated "
    "maintained tested migrated integrated achieved"
).split()


def label_for(score):
    if score >= 85:
        return "Excellent"
    if score >= 70:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Needs work"


def score_resume(d):
    """
    d: dict with full_name, email, phone, location, summary, skills(list),
       education(str), experience(str).
    Returns dict(score, label, insights[(kind, text)], suggestions[(title, text)]).
    kind is 'ok', 'warn' or 'bad'.
    """
    score = 0
    insights, suggestions = [], []

    # Contact details (15)
    contact = 0
    contact += 4 if d.get("full_name") else 0
    contact += 4 if d.get("email") else 0
    contact += 4 if d.get("phone") else 0
    contact += 3 if d.get("location") else 0
    score += contact
    if contact == 15:
        insights.append(("ok", "Complete contact information"))
    else:
        insights.append(("warn", "Some contact details are missing"))
        suggestions.append(("Complete your contact details",
                            "Add name, email, phone and location so recruiters can reach you."))

    # Summary (15)
    words = len((d.get("summary") or "").split())
    if words >= 25:
        score += 15
        insights.append(("ok", "Professional summary present"))
    elif words >= 8:
        score += 8
        insights.append(("warn", "Summary is short"))
        suggestions.append(("Expand your summary",
                            "Aim for 25-60 words covering your role, strengths and goals."))
    else:
        insights.append(("bad", "No professional summary"))
        suggestions.append(("Add a professional summary",
                            "A brief summary helps recruiters understand your goals and value."))

    # Skills (25)
    n_skills = len(d.get("skills") or [])
    score += min(25, round(n_skills / 8 * 25))
    if n_skills >= 8:
        insights.append(("ok", f"Strong skills section ({n_skills} skills)"))
    elif n_skills >= 4:
        insights.append(("warn", f"Skills section could be stronger ({n_skills} skills)"))
        suggestions.append(("Add more relevant skills",
                            "List at least 8 skills - tools, languages and soft skills - to improve company matching."))
    else:
        insights.append(("bad", "Skills section is thin"))
        suggestions.append(("Add relevant keywords",
                            "For better ATS compatibility, include industry-specific skills and keywords."))

    # Experience (20)
    exp = (d.get("experience") or "").strip()
    exp_words = len(exp.split())
    exp_lines = [l for l in exp.splitlines() if l.strip()]
    if exp_words >= 1:
        score += 12
        if len(exp_lines) >= 2 or exp_words >= 40:
            score += 8
            insights.append(("ok", "Good experience / project details"))
        else:
            score += 3
            insights.append(("warn", "Add more experience details"))
            suggestions.append(("Add more experience details",
                                "Describe 2-3 roles, internships or projects with what you did."))
    else:
        insights.append(("bad", "No experience or projects listed"))
        suggestions.append(("Add experience or projects",
                            "Include internships, academic projects or freelance work if you are a fresher."))

    # Education (10)
    if (d.get("education") or "").strip():
        score += 10
        insights.append(("ok", "Education section present"))
    else:
        insights.append(("bad", "Education section missing"))
        suggestions.append(("Add your education", "List your degree, institution and year."))

    # Quantified achievements (10)
    body = f"{d.get('summary', '')} {exp}"
    nums = re.findall(r"\d+\s?%|\d+\+|[$₹]\s?\d|\b\d{2,}\b", body)
    if len(nums) >= 2:
        score += 10
        insights.append(("ok", "Measurable achievements included"))
    elif len(nums) == 1:
        score += 5
        insights.append(("warn", "Add more quantifiable achievements"))
        suggestions.append(("Include more achievements",
                            "Use numbers and results (e.g., \"Increased performance by 20%\")."))
    else:
        insights.append(("bad", "Include measurable achievements"))
        suggestions.append(("Include more achievements",
                            "Use numbers and results (e.g., \"Increased performance by 20%\")."))

    # Action verbs (5)
    verbs = sum(1 for v in ACTION_VERBS if re.search(rf"\b{v}\b", body.lower()))
    if verbs >= 3:
        score += 5
        insights.append(("ok", "Good use of action verbs"))
    elif verbs >= 1:
        score += 2
        suggestions.append(("Use stronger action verbs",
                            "Start bullets with verbs like Developed, Built, Led, Optimized."))
    else:
        suggestions.append(("Use stronger action verbs",
                            "Start bullets with verbs like Developed, Built, Led, Optimized."))

    score = max(0, min(100, score))
    return {"score": score, "label": label_for(score),
            "insights": insights, "suggestions": suggestions}


HEADERS = {
    "summary": ("summary", "professional summary", "objective", "career objective", "profile", "about me"),
    "skills": ("skills", "technical skills", "key skills", "core skills"),
    "experience": ("experience", "work experience", "professional experience", "projects",
                   "internships", "employment"),
    "education": ("education", "academics", "academic background", "qualifications"),
}
OTHER_HEADERS = ("certifications", "achievements", "languages", "hobbies", "interests", "references")


def extract_from_text(text):
    """Very simple heuristic parser for pasted / uploaded resume text."""
    lines = [l.rstrip() for l in text.splitlines()]
    sections, current = {"top": []}, "top"
    for line in lines:
        key = line.strip().strip(":").lower()
        found = None
        for name, aliases in HEADERS.items():
            if key in aliases:
                found = name
        if found is None and key in OTHER_HEADERS:
            found = "other"
        if found:
            current = found
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(line)

    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phone = re.search(r"(\+?\d[\d\s\-()]{8,}\d)", text)
    first = next((l.strip() for l in lines if l.strip()), "")
    name = first if 0 < len(first.split()) <= 5 and "@" not in first else ""
    location = re.search(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)*,\s*[A-Z][A-Za-z ]+\b", sections["top"] and "\n".join(sections["top"]) or text)

    skills_text = "\n".join(sections.get("skills", []))
    skills = parse_skills(re.sub(r"^[A-Za-z ]+:", "", skills_text, flags=re.M))
    low = text.lower()
    for s in skill_vocabulary():
        if re.search(rf"(?<![A-Za-z0-9+#]){re.escape(s)}(?![A-Za-z0-9+#])", low):
            if s not in (x.lower() for x in skills):
                skills.append(s)
    skills = [s for s in skills if len(s) <= 40]

    return {
        "full_name": name,
        "email": email.group(0) if email else "",
        "phone": phone.group(0).strip() if phone else "",
        "location": location.group(0) if location else "",
        "summary": " ".join(sections.get("summary", [])).strip(),
        "skills": skills,
        "education": "\n".join(sections.get("education", [])).strip(),
        "experience": "\n".join(sections.get("experience", [])).strip(),
    }


def match_companies(skills):
    """Return verified companies that share at least one skill with the resume."""
    mine = {s.lower() for s in skills}
    results = []
    rows = get_db().execute("SELECT * FROM companies WHERE verified = 1").fetchall()
    for c in rows:
        need = [s.strip() for s in c["skills"].split(",") if s.strip()]
        matched = [s for s in need if s.lower() in mine]
        if not matched:
            continue
        missing = [s for s in need if s.lower() not in mine]
        results.append({
            "name": c["name"], "industry": c["industry"], "location": c["location"],
            "website": c["website"], "roles": c["roles"],
            "matched": matched, "missing": missing,
            "pct": round(len(matched) / len(need) * 100),
        })
    results.sort(key=lambda r: (-r["pct"], -len(r["matched"]), r["name"]))
    return results


# --------------------------------------------------------------------------
# File reading for the "Resume Strength" upload (.txt, .docx, optional .pdf)
# --------------------------------------------------------------------------
def read_upload(file_storage):
    name = (file_storage.filename or "").lower()
    data = file_storage.read()
    if name.endswith(".txt"):
        return data.decode("utf-8", errors="ignore")
    if name.endswith(".docx"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader  # optional: pip install pypdf
        except ImportError:
            raise ValueError("PDF reading needs the optional 'pypdf' package (pip install pypdf). "
                             "Or upload a .docx / .txt, or paste the text.")
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    raise ValueError("Unsupported file type. Please upload a .pdf, .docx or .txt file.")


# --------------------------------------------------------------------------
# Public pages and authentication
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or len(password) < 6:
            flash("Enter your name, a valid email and a password of at least 6 characters.", "error")
            return render_template("signup.html", form=request.form)
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            flash("An account with this email already exists. Please log in.", "error")
            return render_template("signup.html", form=request.form)
        cur = db.execute(
            "INSERT INTO users (name, email, password, created_at, last_login) VALUES (?,?,?,?,?)",
            (name, email, generate_password_hash(password), now(), now()),
        )
        db.commit()
        session.clear()
        session["user_id"] = cur.lastrowid
        flash("Account created. Welcome to SmartResume!", "success")
        return redirect(url_for("dashboard"))
    return render_template("signup.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row and check_password_hash(row["password"], password):
            session.clear()
            session["user_id"] = row["id"]
            db.execute("UPDATE users SET last_login = ? WHERE id = ?", (now(), row["id"]))
            db.commit()
            return redirect(url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


# --------------------------------------------------------------------------
# App pages
# --------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    uid = session["user_id"]
    resumes = db.execute("SELECT * FROM resumes WHERE user_id = ? ORDER BY updated_at DESC", (uid,)).fetchall()
    analysed = db.execute("SELECT COUNT(*) FROM analyses WHERE user_id = ?", (uid,)).fetchone()[0]
    u = current_user()
    filled = sum(1 for v in (u["name"], u["email"], u["phone"], u["location"]) if v) + (1 if resumes else 0)
    last = u["last_login"] or ""
    last_label = "Today" if last[:10] == datetime.now().strftime("%Y-%m-%d") else day_filter(last)
    return render_template(
        "dashboard.html", active="dashboard", resumes=resumes[:5], total=len(resumes),
        analysed=analysed + len(resumes), profile_pct=round(filled / 5 * 100), last_login=last_label,
    )


@app.route("/resume/new", methods=["GET", "POST"])
@login_required
def create_resume():
    if request.method == "POST":
        f = request.form
        required = ["full_name", "email", "phone", "location", "job_title"]
        if any(not f.get(k, "").strip() for k in required):
            flash("Please fill all required personal details.", "error")
            return render_template("create_resume.html", active="create", form=f)
        skills = parse_skills(f.get("skills", ""))
        data = {
            "full_name": f["full_name"].strip(), "email": f["email"].strip(),
            "phone": f["phone"].strip(), "location": f["location"].strip(),
            "summary": f.get("summary", "").strip(), "skills": skills,
            "education": f.get("education", "").strip(), "experience": f.get("experience", "").strip(),
        }
        result = score_resume(data)
        title = f.get("title", "").strip() or f"{f['job_title'].strip()} Resume"
        db = get_db()
        cur = db.execute(
            """INSERT INTO resumes (user_id, title, full_name, email, phone, location, job_title,
               summary, education, skills, experience, strength, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session["user_id"], title, data["full_name"], data["email"], data["phone"],
             data["location"], f["job_title"].strip(), data["summary"], data["education"],
             ", ".join(skills), data["experience"], result["score"], now(), now()),
        )
        db.commit()
        flash("Resume created! Here is your resume strength and matching companies.", "success")
        return redirect(url_for("view_resume", resume_id=cur.lastrowid))
    u = current_user()
    form = {"full_name": u["name"], "email": u["email"], "phone": u["phone"], "location": u["location"]}
    return render_template("create_resume.html", active="create", form=form)


@app.route("/resumes")
@login_required
def my_resumes():
    rows = get_db().execute("SELECT * FROM resumes WHERE user_id = ? ORDER BY updated_at DESC",
                            (session["user_id"],)).fetchall()
    return render_template("my_resumes.html", active="resumes", resumes=rows)


def load_resume(resume_id):
    row = get_db().execute("SELECT * FROM resumes WHERE id = ? AND user_id = ?",
                           (resume_id, session["user_id"])).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/resume/<int:resume_id>")
@login_required
def view_resume(resume_id):
    r = load_resume(resume_id)
    skills = parse_skills(r["skills"])
    analysis = score_resume({
        "full_name": r["full_name"], "email": r["email"], "phone": r["phone"],
        "location": r["location"], "summary": r["summary"], "skills": skills,
        "education": r["education"], "experience": r["experience"],
    })
    return render_template(
        "resume_view.html", active="resumes", r=r, skills=skills, analysis=analysis,
        companies=match_companies(skills),
    )


@app.route("/resume/<int:resume_id>/delete", methods=["POST"])
@login_required
def delete_resume(resume_id):
    load_resume(resume_id)
    db = get_db()
    db.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
    db.commit()
    flash("Resume deleted.", "info")
    return redirect(url_for("my_resumes"))


@app.route("/strength", methods=["GET", "POST"])
@login_required
def strength():
    analysis, companies, source = None, [], ""
    if request.method == "POST":
        text, source = "", ""
        upload = request.files.get("resume_file")
        try:
            if upload and upload.filename:
                text, source = read_upload(upload), upload.filename
            else:
                text, source = request.form.get("resume_text", ""), "Pasted text"
        except (ValueError, zipfile.BadZipFile, KeyError) as exc:
            flash(str(exc) if isinstance(exc, ValueError) else "Could not read that file.", "error")
        if text.strip():
            parsed = extract_from_text(text)
            analysis = score_resume(parsed)
            companies = match_companies(parsed["skills"])
            analysis["skills_found"] = parsed["skills"]
            db = get_db()
            db.execute("INSERT INTO analyses (user_id, source, strength, created_at) VALUES (?,?,?,?)",
                       (session["user_id"], source, analysis["score"], now()))
            db.commit()
        elif not get_flashed_has_error():
            flash("Upload a resume file or paste your resume text first.", "error")
    return render_template("strength.html", active="strength", analysis=analysis,
                           companies=companies, source=source)


def get_flashed_has_error():
    # Peek at pending flashes without consuming them.
    return any(cat == "error" for cat, _ in session.get("_flashes", []))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name cannot be empty.", "error")
        else:
            db.execute("UPDATE users SET name = ?, phone = ?, location = ? WHERE id = ?",
                       (name, request.form.get("phone", "").strip(),
                        request.form.get("location", "").strip(), session["user_id"]))
            db.commit()
            flash("Profile updated.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html", active="profile")


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


init_db()

if __name__ == "__main__":
    app.run(debug=True)
