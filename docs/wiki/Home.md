# Bark and Ambrosia Beetle Gallery: Developer Wiki

[barkandambrosiagallery.org](https://barkandambrosiagallery.org) is a web platform for storing, browsing,
annotating and identifying images of bark and ambrosia beetles. It also has a pathogen-interactions database and the
game, Ambrosia Archive. It is a Django site with Postgres, Redis and Celery, run with Docker Compose.

## Pages

| Page | Read it when |
|---|---|
| **[Contributing](Contributing)** | Before your first change: what a good contribution looks like, from issue to merged PR. |
| **[Getting Started](Getting-Started)** | Setting up your computer and running the site locally. |
| **[Code Map](Code-Map)** | Looking for where something lives. |
| **[Testing](Testing)** | Running and writing tests, and what CI checks. |
| **[Git Workflow](Git-Workflow)** | Branching, commits, pull requests, migrations. |
| **[Staging](Staging)** | Trying `main` on the server before a release. |
| **[Deployment](Deployment)** | How releases reach production, and server maintenance (owner only). |

## The short version

1. Pick or open an issue.
2. Branch off `main`, make **one** focused change, and add tests for it.
3. Run the tests and open a pull request.
4. The owner reviews and merges.
5. Merged work shows up on the staging site; production only changes when the owner publishes a release.
