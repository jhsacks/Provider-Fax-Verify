# Provider Fax Verify - Google Drive edition

This edition has no Render disk and uses an existing Google Drive JSON file for persistence.

## Replace the GitHub repository
Upload these files to the repository root, replacing the earlier versions:
- app.py
- drive_store.py
- requirements.txt
- render.yaml
- provider_fax_data.json
- provider_import_template.csv

Delete the stray `download` file if present.

## Google Drive file
1. Upload `provider_fax_data.json` into the Google Drive folder already shared with the service account.
2. Open the file in Google Drive and copy its file ID from the URL. The ID is the text between `/d/` and the next `/`.
3. Do not delete this file. The app updates it in place.

## Render environment variables
Set these in Render Environment:
- ADMIN_PASSWORD: a new administrator password
- GOOGLE_SERVICE_ACCOUNT_JSON: the complete service-account JSON, as one environment-variable value
- GOOGLE_DRIVE_FILE_ID: the ID of the uploaded provider_fax_data.json file
- GOOGLE_DRIVE_FILE_NAME: provider_fax_data.json

The repository's render.yaml uses Render's free plan and contains no persistent disk.

## Security
Never commit the service-account JSON to GitHub. It belongs only in Render Environment. Do not store patient information in this application.
