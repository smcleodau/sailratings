# SailRatings Frontend

## Environment
- This machine is DEV. Changes are visible at dev.sailratings.com
- PRODUCTION is on Railway at sailratings.com (deploys from `main` branch)
- The frontend calls the API at api.dev.sailratings.com (dev) or api.sailratings.com (prod)

## How changes work
1. You are on the `develop` branch. All changes happen here.
2. After making changes: `npm run build` then restart the Next.js server
3. ALWAYS commit and push: `git add -A && git commit && git push origin develop`
4. Changes are visible at dev.sailratings.com immediately after rebuild
5. To promote to production: merge develop → main and push
6. If prod breaks, rollback in Railway dashboard

## IMPORTANT: Always commit
Before ending any session, ALWAYS commit and push all changes.
Every commit is a rollback point. Never leave uncommitted work.
