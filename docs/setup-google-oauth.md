# Setting up the shared "devrel-origin" Google OAuth project

`devrel seo connect-gsc` runs a standard OAuth 2.0 installed-app flow against a
GCP project owned by the maintainer. Users never set their own
client_id/secret: they consent against the shared "devrel-origin" app the first
time they connect, and refresh tokens are stored locally at
`.devrel/credentials/gsc.json`.

This doc is the one-time setup the maintainer runs to provision the shared
project and submit it for Google verification (so end users do not see the
"unverified app" warning indefinitely).

## 1. Create the GCP project

1. Sign into https://console.cloud.google.com with the account that should
   own the OAuth client (recommend a dedicated `devrel-origin@` Google
   Workspace account separate from personal Gmail).
2. Click the project selector (top bar), then "New Project".
3. Project name: `devrel-origin`. No organisation. Click Create.
4. Wait ~30 seconds for provisioning, then select the new project.

## 2. Enable the Search Console API

1. Navigation menu, APIs & Services, Library.
2. Search "Search Console API", click Enable.

## 3. Configure the OAuth consent screen

1. Navigation menu, APIs & Services, OAuth consent screen.
2. User type: **External**. Click Create.
3. App information:
   - App name: `devrel-origin`
   - User support email: `dovzhikova@gmail.com` (or the Workspace email)
   - App logo: 120x120 png hosted somewhere stable (e.g. the project's
     marketing site)
   - App domain: the project's marketing site URL
   - Authorized domains: the marketing site root domain
   - Developer contact: `dovzhikova@gmail.com`
4. Scopes: add `https://www.googleapis.com/auth/webmasters.readonly`
   (read-only Search Console).
5. Test users: leave blank for now (the project will switch from Testing to
   In production after verification).
6. Save.

## 4. Create the OAuth client

1. Navigation menu, APIs & Services, Credentials, "+ CREATE CREDENTIALS",
   OAuth client ID.
2. Application type: **Desktop app**.
3. Name: `devrel-origin CLI`.
4. Save. Click the download icon next to the new credential to grab the JSON.
   The relevant fields are `client_id` and `client_secret`.

## 5. Embed the client_id/secret in the package

The OAuth installed-app flow safely embeds `client_id` and `client_secret`:
they are not "secrets" in the cryptographic sense, they identify the app to
Google. Anyone could intercept them by inspecting the request, but the actual
auth happens against the user's Google account, not the client_secret. (See
Google's docs at https://developers.google.com/identity/protocols/oauth2/native-app
for the full rationale.)

Edit `src/devrel_origin/core/oauth_constants.py` (created in Wave 3, Task 1):

```python
GSC_OAUTH_CLIENT_ID = "<paste here>.apps.googleusercontent.com"
GSC_OAUTH_CLIENT_SECRET = "<paste here>"
```

Self-hosting maintainers who want to run their own GCP project can override
the values via env vars `GSC_OAUTH_CLIENT_ID` and `GSC_OAUTH_CLIENT_SECRET`
(see `tools/gsc_client.py` in Wave 3).

## 6. Submit for verification

1. Navigation menu, APIs & Services, OAuth consent screen.
2. "Publishing status" section, click "PUBLISH APP".
3. Confirm.
4. Click "Prepare for verification".
5. Fill in:
   - Justification for the `webmasters.readonly` scope: "Read-only access to
     Search Console data is required to surface keyword performance and crawl
     issues to the user. The user explicitly opts in via the
     `devrel seo connect-gsc` command. Data is not shared, sold, or
     transmitted off the user's device."
   - Demo video URL: link to a 1-minute screencast of
     `devrel seo connect-gsc` running. Record this when Wave 3 lands.
6. Submit. Google's review queue is typically 4 to 6 weeks.

## 7. While verification is pending

The OAuth flow still works with the consent screen showing "Google hasn't
verified this app". Users can proceed via "Advanced, Continue to
devrel-origin". This is acceptable for the first 100 users (Google's "Testing"
mode quota). Document this in `docs/seo-setup.md` (Wave 4) so users are not
surprised.

## 8. After verification

Google will email approval. The unverified-app warning disappears for all
users automatically; no code change required.
