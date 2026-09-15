# Public Policy Tracker

Short project description

## Overview
What the platform does

## Key Features
- Public policy discovery
- Policy database
- Search and filtering
- Deterministic policy retrieval
- AI-assisted responses
- User registration
- Email verification
- Secure login/logout
- Password reset
- Database-backed sessions
- References and official sources
- Dark/light UI [after UI phase]

## Architecture

Frontend
    ↓ HTTPS
Flask Backend
    ↓
PostgreSQL
    ↓
Policy Retrieval
    ↓
Gemini AI fallback/response layer

## Project Structure

frontend/
backend/
data/
migrations/
tests/

## Tech Stack

Frontend
- HTML
- CSS
- JavaScript

Backend
- Python
- Flask
- Flask-SQLAlchemy
- Flask-Migrate

Database
- PostgreSQL

Authentication
- Argon2id
- HttpOnly session cookies
- Email verification
- Password reset

AI
- Google Gemini API

Testing
- pytest

## Policy Dataset

151 policies
15 sectors

Explain current JSON → PostgreSQL migration.

## API Overview

GET /health
POST /chat
GET /policies
GET /api/policies
GET /api/policies/<id>
GET /api/policies/sectors
GET /api/policies/categories

Authentication endpoints

## Authentication Architecture

Explain:
registration
verification
login
sessions
logout
forgot password
reset password

## Retrieval System

Explain Retrieval V2 at a high level.

## Configuration

.env
DATABASE_URL
GEMINI_API_KEY
etc.

## Local Development

Backend setup
Database setup
Migrations
Import policies
Run application
Run tests

## Testing

pytest -q

## Deployment

Frontend
Backend
Database

## Security

Password hashing
session security
token hashing
single-use tokens
CORS
etc.

## Limitations

Policy data freshness
AI limitations
academic project

## Roadmap

Completed
Current
Planned

## Disclaimer

Academic/educational project
Verify important policy information with official sources.

## License