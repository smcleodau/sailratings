# Sail Ratings

This is the SailRatings platform monorepo. It contains the web frontend and the data API.

## Directory Structure

* `web/` - The Next.js 16 frontend for the website and admin tools.
* `api/` - The Python/FastAPI backend for data scraping, orchestration, and API services.
* `.claude/` - Project-level configuration for Claude Code / Antigravity.

## Development

Both the frontend and the backend have their own stack-specific commands. See the `CLAUDE.md` files in each subdirectory for detailed setup and run instructions:
* `web/CLAUDE.md`
* `api/CLAUDE.md`

### Environment Variables
Environment variables are managed centrally via 1Password Environments. The Dev environment ID is used during local development to inject secrets securely via `op run --environment <ID>`. Wait for your 1Password CLI to be authenticated before starting services.

### Services
* **Frontend:** Next.js server running on port `4200`
* **Backend:** FastAPI server running on port `4100`
* **Database:** PostgreSQL on `localhost:5433` (database `irc_data`)
