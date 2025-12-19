# Bark and Ambrosia Beetle Gallery

A dedicated web platform for storing, browsing, and managing large datasets of annotated images for **Bark and Ambrosia Beetles**.

# Development Workflow

This guide covers how to set up the project, run it locally, make changes (Python, HTML, CSS), and share your work with the team.

## **1. Prerequisites**

Before starting, ensure you have the following installed:

* **Docker Desktop** (must be running)
* **Git**
* **(Optional)** [Pixi](https://prefix.dev/) (installed locally helps with managing `pixi.lock`, though Docker handles the runtime).

---

## **2. Initial Setup (First Time Only)**

If you are cloning this repository for the first time (or setting up a new machine), follow these steps to initialize the environment.

1. **Clone the Repository**
```bash
git clone https://github.com/your-org/beetlesgallery.git
cd beetlesgallery

```


2. **Build the Environment**
This builds the Docker container and installs all Python (Pixi) and JavaScript (npm) dependencies.
```bash
docker compose build

```


3. **Initialize the Database**
Run the migrations to create the database schema.
```bash
docker compose run --rm web pixi run migrate

```


4. **Create an Admin User**
You need this to access the upload tools and admin panel.
```bash
docker compose run --rm web pixi run python manage.py createsuperuser

```



---

## **3. Daily Development Cycle**

### **Step A: Start the Server**

To view the website, start the containers. This runs the database and the Django web server.

```bash
docker compose up

```

* **View the site:** [http://localhost:8000](https://www.google.com/search?q=http://localhost:8000)
* **Stop the site:** Press `Ctrl+C` in the terminal.

### **Step B: Editing Code (Python & HTML)**

* **Hot Reloading:** The project is configured to "watch" your folders. If you edit any `.py` file (views, models) or `.html` template, the server will automatically reload. You just need to refresh your browser.

### **Step C: Editing Styles (Tailwind CSS)**

Because we use Tailwind, changing classes in HTML (e.g., `text-red-500` to `text-blue-500`) requires recompiling the CSS file.

1. Open a **new terminal** window (keep `docker compose up` running in the first one).
2. Run the CSS watcher:
```bash
docker compose run --rm web pixi run build-css

```


* *Note: This command runs in "watch mode" (it will stay open).*
* As you save HTML or JS files, you will see it regenerate `style.css` instantly.



### **Step D: Modifying the Database (Models)**

If you edit `models.py` (e.g., adding a new field to `Beetles`), you must update the database schema.

1. **Create Migration File:**
```bash
docker compose run --rm web pixi run python manage.py makemigrations

```


2. **Apply Migration:**
```bash
docker compose run --rm web pixi run migrate

```



### **Step E: Adding New Dependencies**

* **Python:** Add the package to `pixi.toml` under `[dependencies]`.
* Run `pixi install` locally (if you have Pixi) to update `pixi.lock`.
* Run `docker compose build` to rebuild the container with the new library.


* **JavaScript:** Edit `package.json`.
* Run `docker compose build` to update.



---

## **4. Sharing Your Changes (Git Workflow)**

Once you are happy with your changes and have tested them at `localhost:8000`:

1. **Check Status:**
See which files you changed.
```bash
git status

```


2. **Add & Commit:**
```bash
git add .
git commit -m "Description of what I changed (e.g., Fixed sidebar layout)"

```


3. **Pull Updates (Important):**
Before pushing, always pull the latest code from your collaborators to avoid conflicts.
```bash
git pull origin main

```


* *If there are new dependencies in the update, run `docker compose build` again.*
* *If there are database changes, run `docker compose run --rm web pixi run migrate`.*


4. **Push:**
```bash
git push origin main

```



---

## **5. Cheat Sheet (Commands)**

| Goal | Command |
| --- | --- |
| **Start Site** | `docker compose up` |
| **Watch CSS** | `docker compose run --rm web pixi run build-css` |
| **Apply DB Changes** | `docker compose run --rm web pixi run migrate` |
| **Create Migration** | `docker compose run --rm web pixi run python manage.py makemigrations` |
| **Create Admin** | `docker compose run --rm web pixi run python manage.py createsuperuser` |
| **Rebuild Container** | `docker compose build` |
| **Run Arbitrary Command** | `docker compose run --rm web pixi run python manage.py <command>` |