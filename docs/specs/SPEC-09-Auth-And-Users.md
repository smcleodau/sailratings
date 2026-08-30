# SPEC-09: Authentication & User Accounts

## 1. Overview
This specification details the integration of Clerk Authentication into the Next.js frontend, and the synchronization of user records into the FastAPI/Postgres backend using webhooks.

## 2. Frontend Integration (Clerk & Next.js)

### 2.1 Middleware & Routing
- The frontend will be **Soft-gated**.
- **Public Routes:** `/, /boats, /boats/[id]` (high-level stats only).
- **Protected Routes:** `/portfolio`, `/settings`, `/checkout`, and the detailed Premium Report views.
- Implement Clerk's `authMiddleware` in `web/src/middleware.ts` to enforce these routing rules.

### 2.2 UI Components
- Use Clerk's pre-built `<SignIn />`, `<SignUp />`, and `<UserButton />` components.
- Wrap these components in custom layouts that strictly adhere to the "Paper" design system defined in `globals.css` (e.g., using `--color-navy`, `--color-cream`). DO NOT use generic Tailwind classes for these wrappers.

## 3. Backend Integration (FastAPI & Postgres)

### 3.1 Database Schema
Create a new Alembic migration to create the `users` table with the following columns:
- `id`: UUID (Primary Key)
- `clerk_id`: String (Unique, Indexed) - The immutable `user_...` ID from Clerk.
- `email`: String (Unique, Indexed)
- `full_name`: String (Nullable)
- `subscription_status`: Enum (`none`, `premium`, `pro`) - Default to `none`.
- `stripe_customer_id`: String (Nullable)
- `created_at`: DateTime
- `updated_at`: DateTime

### 3.2 Webhook Endpoint
- Create a new router endpoint: `POST /v1/webhooks/clerk`.
- Validate the webhook payload using the Svix library (`svix.webhooks.Webhook`) and a secret stored in the environment (`CLERK_WEBHOOK_SECRET`).
- On `user.created` and `user.updated` events, upsert the corresponding record in the `users` table.
- On `user.deleted`, hard delete or soft delete the user record based on privacy requirements.

## 4. Acceptance Criteria
- [ ] Clerk `authMiddleware` correctly soft-gates the application.
- [ ] Clerk UI components are successfully integrated and styled to match the "Paper" design system.
- [ ] Alembic migration successfully creates the `users` table with all specified fields.
- [ ] `POST /v1/webhooks/clerk` successfully validates Svix signatures and upserts user records into Postgres upon Clerk signup.
