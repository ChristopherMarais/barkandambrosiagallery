# Security Policy

## Supported Versions

This project is currently in active development. We provide security updates for the current main branch and stable releases used in production.

| Version | Supported |
| --- | --- |
| Main (Development) | :white_check_mark: |
| Production Deployment | :white_check_mark: |
| Legacy/Archived Versions | :x: |

## Reporting a Vulnerability

We take the security of the **Bark and Ambrosia Beetle Gallery** seriously. If you find a security vulnerability, please report it to us immediately using the following process:

### How to Report

Please **do not** report security vulnerabilities via public GitHub issues. Instead, send a detailed report to the project maintainers. Your report should include:

* **Description**: A summary of the vulnerability.
* **Steps to Reproduce**: Detailed steps or a proof-of-concept (PoC).
* **Impact**: How this vulnerability could affect the server, database, or user data.

### What to Expect

* **Acknowledgement**: You will receive an initial response within 72 hours of your report.
* **Update Frequency**: We will provide weekly updates on the status of the investigation and any planned fixes.
* **Resolution**: If the vulnerability is accepted, we will work on a patch to be deployed via our GitHub Actions pipeline.

## Security Best Practices for Contributors

To maintain the security of the Webapp, all contributors must follow these guidelines:

* **Secrets Management**: Never commit sensitive information like passwords, API keys, or `.env` files to the repository. The production environment uses a separate `.env.prod` file that is not tracked by Git.
* **Production Access**: SSH access to the Contabo server is restricted to authorized users. Ensure your SSH keys are kept secure.
* **Database Security**: In production, the database port `5432` is closed to external traffic, and access is only permitted within the internal Docker network.
* **Dependencies**: Regularly check `pixi.toml` and `package.json` for outdated or vulnerable packages and update them using the `docker compose build` workflow.
