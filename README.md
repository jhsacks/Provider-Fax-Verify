# Provider Fax Verify

A fast standalone Streamlit application for looking up the verified fax number for an external provider or practice.

## Core workflow

1. Search by provider, practice, fax, city, or NPI.
2. See the correct fax prominently.
3. Select **Copy fax**.
4. Add a missing provider from the same screen.

The public screen intentionally hides audit and correction details. Administrators can import, edit, merge and review records from the Admin screen.

## Local test

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Render deployment

1. Create a new GitHub repository.
2. Upload every file from this folder to the repository root.
3. In Render, choose **New +** then **Blueprint**.
4. Connect the repository and apply `render.yaml`.
5. After deployment, open the Render service and add or replace the `ADMIN_PASSWORD` environment variable with a password known to your administrators.
6. Keep the persistent disk. The SQLite database is stored in `/var/data` when deployed.

## Initial data load

Use `provider_import_template.csv` as the column format. In the app, open **Admin**, select **Import CSV/XLSX**, and upload the completed file.

Required columns:
- provider_name
- practice_name
- correct_fax

Recommended columns:
- credentials
- city
- state
- npi
- epic_status (`correct`, `incorrect`, `missing`, `unknown`)
- epic_fax
- phone
- source_url
- notes

## Weekly checking

The app runs a weekly comparison every Sunday at 3:00 AM in the server's configured timezone while the service is running. It uses:
- NPPES API when an NPI is stored
- The practice's stored official source URL when one is present

A discovered difference creates a review item. It does not silently overwrite the trusted fax.

For a production clinical deployment, the organization should approve hosting, authentication, access roles, and the permitted sources before broad use. Do not store patient information in this app.
