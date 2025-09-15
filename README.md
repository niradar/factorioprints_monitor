# Factorioprints.com Monitor

Monitor user blueprints on Factorioprints.com and receive notifications when new comments are posted on your blueprints.


## 1. Prerequisites

### Docker

Make sure you have Docker installed and running on your machine.
You can download it from [Docker's official site](https://www.docker.com/get-started).

### uv

If needed, install [uv](https://github.com/astral-sh/uv), a Python package manager that simplifies virtual environments and dependency management.


### RabbitMQ

Run a RabbitMQ docker container:

    docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:management


## 2. Installation

To install the dependencies using uv:

```bash
uv sync
playwright install
```

Optional (for development):
```bash
uv sync --extra dev
```

## 3. Usage: Django Interface

On Windows, run:
```bash
run_server.bat
```

Open your browser and navigate to `http://localhost:8129/` to access the web interface.


## 4. Usage: Django Management Commands

After setting up your Django project and running migrations, you can collect and view blueprint and comment snapshots using the following management commands:

### 1. **Take a Snapshot**

Scrapes all blueprints and comments for a FactorioPrints user and stores them in the database.

```bash
python manage.py take_snapshot --user-url "https://factorioprints.com/user/<USER_ID>"
```

* **Arguments:**

  * `--user-url`: (required) The full URL of the FactorioPrints user to monitor.

---

### 2. **List Snapshots**

Lists all available snapshot timestamps (optionally filter by user).

```bash
python manage.py list_snapshots [--user-url "https://factorioprints.com/user/<USER_ID>"]
```

* **Arguments:**

  * `--user-url`: (optional) Filter snapshots for a specific user.

---

### 3. **Get Latest Blueprints**

Displays all blueprints for the most recent snapshot of a user.

```bash
python manage.py latest_blueprints --user-url "https://factorioprints.com/user/<USER_ID>"
```

* **Arguments:**

  * `--user-url`: (required) The full URL of the FactorioPrints user.

---

### 4. **Delete a Snapshot**

Delete a snapshot and all its data by timestamp:

```bash
python manage.py delete_snapshot --timestamp 2025-06-05T08:00:00+00:00
```

* The timestamp must match exactly the one listed in `list_snapshots`.


### Notes

* These commands are **run from your Django project root directory**.
* You can add these commands to a cron job or call them from a task queue (e.g., Celery) for automation.
* Snapshots can take up to several minutes to complete, depending on the number of blueprints and comments.
* All scraping logic is handled server-side; no browser or manual interaction is required.

---

### Example Workflow

```bash
# Take a snapshot for a user
python manage.py take_snapshot --user-url https://factorioprints.com/user/I6YX1Ar1cWUwhbQgMcW4nyZkDs52

# List all snapshots for that user
python manage.py list_snapshots --user-url https://factorioprints.com/user/I6YX1Ar1cWUwhbQgMcW4nyZkDs52

# See latest blueprints in the most recent snapshot
python manage.py latest_blueprints --user-url https://factorioprints.com/user/I6YX1Ar1cWUwhbQgMcW4nyZkDs52
```
