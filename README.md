# Bark and Ambrosia Beetle Gallery

A dedicated web platform for storing, browsing, and managing large datasets of annotated images for **Bark and Ambrosia Beetles**.

This project is designed to handle high-resolution image libraries (up to 500GB+) by separating the heavy image data (stored locally or on object storage) from the lightweight metadata (stored in PostgreSQL).

## 🏗 Architecture Overview

We use a **Hybrid Development Workflow** to ensure the setup is simple, fast, and scalable:

* **Django (via Pixi):** Runs directly on your machine for fast debugging and file watching.
* **PostgreSQL (via Docker):** Runs in a container to ensure a consistent, production-grade database environment without complex local installation.
* **Pixi:** Manages all Python dependencies (Django, Pillow, etc.) to guarantee every developer uses the exact same environment.

---

## 🛠 Prerequisites

Before starting, ensure you have the following installed:
1.  **[Pixi](https://pixi.sh/)** (for Python environment management)
2.  **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** (for the database)
3.  **Git**

---

## 🚀 Getting Started (Local Development)

Follow these steps to set up the project on your local machine.

### 1. Clone the Repository
```bash
git clone [https://github.com/your-username/barkandambrosiagallery.git](https://github.com/your-username/barkandambrosiagallery.git)
cd barkandambrosiagallery

```

### 2. Start the Database

We run the database in a Docker container to keep it isolated.

```bash
# Start the database in the background
docker compose up -d db

```

### 3. Install Dependencies

Use Pixi to install the Python environment and dependencies.

```bash
pixi install

```

### 4. Setup the Database (Migrations)

Create the database tables. We must explicitly tell the local Django app to look for the database on `localhost`.

**Windows (PowerShell):**

```powershell
$env:POSTGRES_HOST="localhost"; pixi run migrate

```

**Mac / Linux:**

```bash
POSTGRES_HOST=localhost pixi run migrate

```

### 5. Create an Admin User

To access the Django admin panel:

**Windows (PowerShell):**

```powershell
$env:POSTGRES_HOST="localhost"; pixi run python manage.py createsuperuser

```

**Mac / Linux:**

```bash
POSTGRES_HOST=localhost pixi run python manage.py createsuperuser

```

---

## 🏃‍♂️ Running the Website

To start the development server, ensure your Docker database is running, then run the start command.

**Windows (PowerShell):**

```powershell
$env:POSTGRES_HOST="localhost"; pixi run start

```

**Mac / Linux:**

```bash
POSTGRES_HOST=localhost pixi run start

```

### 🌐 Access the Site

* **Website:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
* **Admin Panel:** [http://127.0.0.1:8000/admin/](https://www.google.com/search?q=http://127.0.0.1:8000/admin/)

*(Press `Ctrl+C` in your terminal to stop the server)*

---

## 🤝 Contribution Guidelines

We follow a standard Git feature-branch workflow to maintain stability. **Please do not commit directly to the `main` branch.**

### How to Contribute:

1. **Create a Branch:**
Always create a new branch for your specific task or feature.
```bash
git checkout main
git pull origin main
git checkout -b feature/name-of-your-feature

```


*(Example branch names: `feature/add-image-upload`, `fix/login-bug`, `style/homepage-redesign`)*
2. **Develop & Test:**
Make your changes and verify they work locally using the instructions above.
3. **Commit & Push:**
```bash
git add .
git commit -m "Brief description of changes"
git push origin feature/name-of-your-feature

```


4. **Create a Pull Request (PR):**
* Go to the repository on GitHub.
* Click **"Compare & pull request"**.
* Describe your changes and submit.
* Your code will be reviewed by the team before being merged into the public version of the website.



---

## 🐳 Full Docker (Optional)

If you need to simulate the production server environment (running both the web server and database in containers):

```bash
docker compose up --build

```

```

### **How to Push this to GitHub**

Since you are setting this up for the first time, run these commands in your terminal to save the README and push everything to GitHub:

```powershell
# 1. Check status (you should see README.md as untracked or modified)
git status

# 2. Add all files
git add .

# 3. Commit
git commit -m "Initial commit: Basic Django setup with Pixi and Docker"

# 4. Push to your main branch
git push origin main

```