# SmartResume

Resume builder with strength scoring and verified-company matching.
Python (Flask) + SQLite + HTML/CSS.

## Run
    pip install -r requirements.txt
    python app.py
Open http://127.0.0.1:5000 , sign up, then create a resume.

## Files
- app.py            backend, routes, strength scoring, company matching
- smartresume.db    SQLite database (users, resumes, analyses, companies)
- templates/*.html  pages
- static/style.css  styling

## Companies
The `companies` table is sample data. Add or edit rows
(name, industry, location, website, roles, comma-separated skills, verified=1).
Only verified = 1 companies are shown.
