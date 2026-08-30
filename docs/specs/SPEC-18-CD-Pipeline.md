# SPEC-18: Automated Production Deployment (CD Pipeline)

## 1. Overview
The final step in the autonomous factory is deploying the merged code to production. We will use GitHub Actions to detect merges to the `main` branch, build the Docker containers, run database migrations, and deploy the application.

## 2. Architecture

### 2.1 GitHub Actions Workflow (`.github/workflows/deploy.yml`)
- Trigger: `push` to `branches: [ main ]`
- Steps:
  1. **Checkout Code:** Standard `actions/checkout`.
  2. **Set up Python/Node:** Install dependencies.
  3. **Lint & Test:** Run `flake8`, `pytest`, and Next.js build as a final safety check.
  4. **Build Docker Images:** Build `sailratings-api` and `sailratings-web`.
  5. **Push to Registry:** Push images to Amazon ECR or Docker Hub.
  6. **Deploy to Production:** 
     - SSH into the production server.
     - Pull the latest Docker images.
     - Execute `docker-compose -f docker-compose.prod.yml down` then `up -d`.
     - Execute `docker exec sailratings-api alembic upgrade head` to run any new database migrations.

### 2.2 Rollback Strategy
- If the deployment fails (e.g., containers crash or migrations fail), the GitHub Action must automatically revert the Docker tags to the previous stable version and restart the services.
- The Action must send an alert (via webhook to the Orchestrator) notifying the system of the deployment failure so an agent can be spawned to investigate.

## 3. Acceptance Criteria
- [ ] A `.github/workflows/deploy.yml` file is created and fully configured.
- [ ] Pushing to `main` successfully deploys the application to a staging/production server.
- [ ] Failed migrations safely rollback to the previous container versions.
