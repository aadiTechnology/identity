# New developer: first-time setup of the Aadi products

You need **Python 3.12+**, **Node.js 20+** and **Docker Desktop** (running). Then, from each project folder, double-click or run `setup.bat` once. It creates the Python environment, starts PostgreSQL in Docker, creates the tables and **runs the seed**, which loads the **Aadi-Test** company (see "Seed data" below to run or reset it by hand). Run it again any time; it is safe to repeat. `run.bat` starts the project.

| Order | Folder | Setup | Start | Opens at |
|---|---|---|---|---|
| 1 | `identity` | `setup.bat` | `run.bat` | http://localhost:8010 |
| 2 | `ProjectTracking` | `setup.bat` | `run.bat` | http://localhost:8000 |
| 3 | `Media\Academics` | `setup.bat` | `run.bat` | http://localhost:5174 (API on 8100) |

Each project has its own database in its own container (ports 15440 Identity, 15434 Project Tracker, 15441 Academic), so they never get in each other's way. Videos are made by the Video Engine (`Media\ScriptToVideo`, see its README); everything except "Make the video" works without it.

## The Aadi-Test company: logins you do not have to remember

One fictional company, **Aadi-Test**, with 15 people. **Every person signs in with the password `AadiTest@2026`.** The Identity sign-in page and the Project Tracker sign-in page list them under the form (click a name). Academic in development mode needs no sign-in (you act as the Aadi-Test administrator).

The people, departments, salaries, projects, tags, costs, revenue and allocations of **Project Tracker** come from `ProjectTracking\docs\Aadi Technology Matrix_V4.xlsx` (sheet BusinessView). To change them, edit the workbook and run `python backend\tools\build_matrix_seed.py` in `ProjectTracking`, then `setup.bat` again (or `python -m app.seed_aadi_test --reset`).

| Email (`@aadi-test.example.com`) | Name | Title | Project Tracker role | Academic role | Department |
|---|---|---|---|---|---|
| prajakta | Prajakta Kulkarni | CEO | Owner | Administrator | Management |
| akanksha | Akanksha Joshi | Project Manager | Project Manager | Reviewer | Software Development |
| aarti | Aarti Deshmukh | Product Owner | Product Owner | Content operator | Software Development |
| kalyani | Kalyani Patil | Team Lead | Team Lead | Content operator | Software Development |
| namrata | Namrata More | Software Engineer | Employee | Production operator | Software Development |
| aman | Aman Shah | Software Engineer | Employee | Viewer | Software Development |
| anjali | Anjali Rao | Software Engineer | Employee | Production operator | Software Development |
| prital | Prital Naik | Software Engineer | Employee | Viewer | Software Development |
| anushka | Anushka Iyer | QA Engineer | Employee (bench) | Viewer | Software Development |
| ankita | Ankita Sawant | Software Engineer | Employee (bench) | Viewer | Software Development |
| manasi | Manasi Gokhale | Marketing Lead | Team Lead | Viewer | Marketing & Sales |
| gayatri | Gayatri Pawar | Marketing Executive | Employee | Viewer | Marketing & Sales |
| jyoti | Jyoti Menon | Finance Manager | Finance | Viewer | Marketing & Sales |
| dhande | Dhande Kamat | Delivery Manager | Ops | Viewer | Marketing & Sales |
| pravin | Pravin Dixit | Sales Lead | Sales | Viewer | Marketing & Sales |

Titles, roles and managers are not in the workbook; they were filled in so that every role of the products has a test person. Nobody in Aadi-Test is a platform administrator.

## Seed data: where it is and how to run it

`setup.bat` runs the seed for you (last step, "Loading the Aadi-Test company"). To run, repeat or reset it by hand, use the commands below from the project folder. Repeating is safe: the seed adds only what is missing. **`--reset` erases that project's whole local database first** (it asks you to type YES; add `--yes` to skip the question).

| Project | Seed code | Run it (Windows) | Start over (erases the local data) |
|---|---|---|---|
| Identity (`identity`) | `app	est_tenant.py` (people, company, subscriptions); `dev-setup` in `app\cli.py` (registers the local products and sign-in clients) | `.venv\Scripts\python.exe -m app.cli dev-setup` | `.venv\Scripts\python.exe -m app.cli reset-and-seed-test` |
| Project Tracker (`ProjectTrackingackend`) | `app\seed_aadi_test.py`; data in `app\seed_dataadi_matrix.json` (built from `docs\Aadi Technology Matrix_V4.xlsx`) | `set APP_ENV=demo` and `set DATABASE_URL=postgresql+psycopg://tracker:tracker_dev@localhost:15434/tracker_aadi_test`, then `.venv\Scripts\python.exe -m app.seed_aadi_test` | the same command with `--reset` |
| Academic (`Media\Academicsackend`) | `app\seed_aadi_test.py`; sample lesson in `app\seed_dataefraction\` | `set DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:15441/academic_aadi_test`, `set AUTH_MODE=dev`, `set APP_ENV=development`, then `.venv\Scripts\python.exe -m app.seed_aadi_test` | the same command with `--reset` |

What each seed loads:
- **Identity:** the company Aadi-Test, its 15 people (shared password `AadiTest@2026`), the Project Tracker, Academic and Video Engine entries with their local sign-in clients. Nobody is a platform administrator. For a local platform administrator: `set IDENTITY_ADMIN_PASSWORD=<your choice>` then `.venv\Scripts\python.exe -m app.cli create-admin --email you@example.com --name "Your Name"`.
- **Project Tracker:** the 15 people with department, title, role, manager and monthly salary; 3 departments with heads; 9 tags; 12 projects with planned cost and revenue; each person's monthly allocation to each project; the 160-hour month and Rs 250 hourly rate; and the organisation revenue target. All from the workbook.
- **Academic:** the same 15 people with roles and departments (linked to their Aadi sign-in on first login), one class with a chapter, and one approved lesson, ready for "Make the video".

Order matters only once: run `identity\setup.bat` first if you want real Aadi sign-in between products (it creates the products and sign-in clients the other two use). The three seeds use the same company id, `5ca1ab1e-aad1-4e57-8000-000000000001`, so a person who signs in through Identity lands in the right company in each product.

**Changing the data:** edit `Aadi Technology Matrix_V4.xlsx`, run `python backend	oolsuild_matrix_seed.py` in `ProjectTracking` (needs `pip install openpyxl`), then run the Tracker seed with `--reset`. To change the people, edit the same list in all three seeds (`PEOPLE` in each file) and keep them identical.

## Signing in through Aadi Identity locally (optional, the real flow)

`identity\setup.bat` registers Project Tracker and Academic with their local addresses and the Aadi-Test people. To use it from Project Tracker, start Identity (`run.bat`), then in `ProjectTracking\backend` set `AUTH_MODE=both`, `IDENTITY_ISSUER=http://localhost:8010`, `IDENTITY_CLIENT_ID=tracker-web` before starting (see its README). Academic: `AUTH_MODE=oidc`, `IDENTITY_ISSUER_URL=http://localhost:8010`, `IDENTITY_CLIENT_ID=academics-web`, `IDENTITY_CLIENT_SECRET=dev-academics-secret`, `SESSION_SECRET=<any long text>`, `APP_BASE_URL=http://localhost:5174`.

## Tests

`identity`: `.venv\Scripts\python.exe -m pytest -q` (no database needed). `ProjectTracking\backend` and `Media\Academics\backend`: start the project's PostgreSQL (setup does), create the test database as the README says, then `.venv\Scripts\python.exe -m pytest -q`.

## Production

The same Aadi-Test company can be loaded on the live system for demonstrations (see each project's Coolify notes: the one-time `APP_MAINTENANCE` settings). It erases all data first, so it is only done on purpose and with the owner's decision.
