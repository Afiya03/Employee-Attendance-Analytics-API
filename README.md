# Employee Attendance & Analytics API

A REST API built with Python, FastAPI, and MongoDB to manage employee attendance, punch-in/punch-out records, attendance regularization, and workforce analytics.

## Features

- Employee management
- Attendance punch-in and punch-out
- Attendance corrections with audit history
- Late arrival, overtime, work-hour, and half-day calculations
- Paginated attendance records
- Monthly employee attendance analytics
- Department summaries and late-arrival leaderboard
- Department daily trends and moving averages
- MongoDB indexes and query execution-plan analysis

## Technology Stack

- Python 3.11+
- FastAPI and Uvicorn
- MongoDB 6.0+
- PyMongo and Pydantic

## Prerequisites

- Python 3.11 or newer
- MongoDB 6.0 or newer, locally or through MongoDB Atlas
- Git

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/Afiya03/Employee-Attendance-Analytics-API.git
cd Employee-Attendance-Analytics-API
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure MongoDB

Create a local `.env` file containing your MongoDB configuration:

```env
MONGO_URI=your_mongodb_connection_string
MONGO_DB=your_database_name
```

Keep `.env` private. Never commit database credentials or connection strings.

### 5. Start the API

```bash
uvicorn app.main:app --reload --port 8000
```

## API Documentation

When the server is running:

- Swagger UI: http://127.0.0.1:8000/docs
- OpenAPI contract: `openapi.yaml`

## Sample Data

Sample employee and attendance documents are available in `sample_data/`. The `sample_seed.py` script loads the sample data. Configure MongoDB before running the seed script.

## Project Structure

- `app/main.py` — API implementation
- `requirements.txt` — Python dependencies
- `openapi.yaml` — API contract
- `DATA_MODEL.md` — MongoDB data model
- `DECISIONS.md` — Design decisions
- `REVIEW.md` — Code review and remaining verification items
- `sample_data/` — Sample documents
- `sample_seed.py` — Sample-data loader

## Security

Keep `.env`, database credentials, API keys, and other secrets out of version control. Do not include a Dockerfile in the submission.

## Assignment

Prepared for the HROne Software Engineer Trainee technical assignment.secrets, no Dockerfile.
