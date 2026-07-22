# ACC Governance Audit & Notification Dashboard

This project is an automated governance and compliance auditor for Autodesk Construction Cloud (ACC) and BIM 360 projects. It crawls project directories to enforce naming standards, missing metadata, and correct folder structures, and provides a lightweight, interactive dashboard to review the findings.

## Features

- **Automated Auditing**: Connects to the Autodesk Construction Cloud API to scan project folders (like `02_Shared/BIM`).
- **Rules Engine**: Verifies that files match strict naming conventions and checks for required metadata/custom attributes.
- **Interactive Dashboard**: Generates a fast, client-side HTML dashboard and runs a local web server (`localhost:8080`) to browse compliance reports across all your projects.
- **Automated Notifications**: Identifies who uploaded non-compliant files and uses your local Windows Outlook client to automatically draft and send email notifications requesting corrections (automatically CC'ing project admins).

## Prerequisites

- Python 3.10+
- Autodesk Forge / APS Application Credentials (Client ID and Secret)
- Windows OS with Microsoft Outlook installed (required for the automated email drafting feature).

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/mdshahnawaz123/Automation-KPI.git
   cd Automation-KPI
   ```

2. Install the required Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   *(Note: `pywin32` is required for the Outlook email integration).*

3. Configure your environment variables:
   Copy `.env.example` to `.env` and fill in your APS credentials and Account ID.

## Usage

Start the local dashboard server:
```bash
python server.py
```

- Open `http://localhost:8080` in your web browser.
- Browse through your projects to see compliance flags.
- Navigate to a specific project's **Shared/BIM check**.
- Click **"Send Notification Emails"** to automatically draft Outlook emails to the users who uploaded the non-compliant files!

## Architecture

- `server.py`: Flask web server that serves the interactive dashboard.
- `src/audit.py`: Core logic for crawling ACC folders and comparing against the rules engine.
- `src/acc_client.py`: Handles authentication and API requests to Autodesk Construction Cloud.
- `src/email_sender.py`: Uses `win32com` to interface with the local Windows Outlook application.
- `src/report.py`: Generates the HTML/CSS for the interactive reporting interface.
- `config/naming_rules.json`: Defines the acceptable file naming patterns and required metadata.

## License
Internal Use Only.
